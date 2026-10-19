"""任务管理与状态机。

一个任务 = 一个文件从进来到出去的全过程。
解析/检测在后台线程跑，HTTP 线程只读快照，避免阻塞界面。
"""
from __future__ import annotations

import os
import random
import shutil
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import audit, pipeline, registry, vault
from .config import OUTPUT_DIR, STRICTNESS, WORK_DIR
from .ocr import get_ocr
from .udm import (
    Decision, Document, Entity, ParseContext, Rule, Span, YinzhanError,
)
from .utils import iso_now, safe_name, sha256_file, unique_path


def _load_rules(fmt: str, pack_ids: list[str] | None = None) -> tuple[list[Rule], str, list[dict]]:
    """加载规则包。对 rules 模块的返回形态做兼容，避免联调期互相卡住。"""
    from . import rules as rules_mod

    packs = {}
    try:
        packs = rules_mod.load_packs()
    except Exception:
        packs = {}
    if isinstance(packs, list):  # 兼容返回 list 的实现
        grouped: dict[str, list[Rule]] = {}
        for r in packs:
            grouped.setdefault(r.pack or "default", []).append(r)
        packs = grouped

    index = []
    for pid, rs in packs.items():
        index.append({"id": pid, "count": len(rs)})

    compiled: list[Rule] = []
    try:
        compiled = rules_mod.compile_rules(packs, fmt, pack_ids)
    except TypeError:
        try:
            compiled = rules_mod.compile_rules(packs, fmt)
        except Exception:
            compiled = []
    except Exception:
        compiled = []
    if not compiled:
        for pid, rs in packs.items():
            if pack_ids and pid not in pack_ids:
                continue
            for r in rs:
                if fmt in r.scope or "*" in r.scope:
                    compiled.append(r)
        compiled.sort(key=lambda r: r.priority, reverse=True)
    label = ",".join(sorted({r.pack for r in compiled if r.pack})) or "default"
    return compiled, label, index


@dataclass
class Job:
    id: str
    name: str
    path: str
    fmt: str
    strictness: str = "standard"
    pack_ids: list[str] | None = None
    stage: str = "queued"          # queued|parsing|detecting|ready|exporting|done|error
    progress: float = 0.0
    target: float = 0.1
    note: str = ""                 # 给人看的一句话进度，如「正在识别第 3/17 页」
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=iso_now)
    cancel_flag: bool = False

    doc: Document | None = None
    parser: registry.Parser | None = None
    entities: list[Entity] = field(default_factory=list)
    rules: dict[str, Rule] = field(default_factory=dict)
    session: pipeline.Session = field(default_factory=pipeline.Session)
    pack_label: str = ""

    exports: dict[str, dict] = field(default_factory=dict)
    preview_doc: Document | None = None
    preview_out: str | None = None
    _preview_dirty: bool = True

    lock: threading.RLock = field(default_factory=threading.RLock)
    preview_lock: threading.Lock = field(default_factory=threading.Lock)

    # ── 快照 ──
    def counts(self) -> dict:
        total = len(self.entities)
        pending = sum(1 for e in self.entities if e.status == "pending")
        done = total - pending
        by_type: dict[str, int] = {}
        for e in self.entities:
            by_type[e.label or e.etype] = by_type.get(e.label or e.etype, 0) + 1
        return {"total": total, "pending": pending, "done": done,
                "rejected": sum(1 for e in self.entities if e.status == "rejected"),
                "by_type": by_type}

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "name": self.name,
                "fmt": self.fmt,
                "stage": self.stage,
                "progress": round(self.progress, 3),
                "note": self.note,
                "cancelled": self.cancel_flag,
                "error": self.error,
                "warnings": self.warnings[:20],
                "pages": len(self.doc.pages) if self.doc else 0,
                "counts": self.counts(),
                "created_at": self.created_at,
                "pack": self.pack_label,
                "strictness": self.strictness,
                "exports": {k: {kk: vv for kk, vv in v.items() if kk != "bytes"}
                            for k, v in self.exports.items()},
            }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    # ── 创建 ──
    def create(self, path: Path, strictness: str = "standard",
               pack_ids: list[str] | None = None) -> Job:
        parser = registry.find_parser(path)
        if parser is None:
            raise YinzhanError(f"暂不支持这种文件：{path.suffix or '无扩展名'}")
        job_id = "j_" + os.urandom(4).hex()
        job = Job(
            id=job_id, name=_display_name(path.name), path=str(path),
            fmt=parser.format_id, strictness=strictness, pack_ids=pack_ids,
            parser=parser,
        )
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
        self._evict()
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise YinzhanError(f"任务不存在：{job_id}")
        return job

    def list(self, limit: int = 30) -> list[dict]:
        with self._lock:
            ids = self._order[-limit:][::-1]
        return [self._jobs[i].snapshot() for i in ids if i in self._jobs]

    # ── 主流程 ──
    def _run(self, job: Job) -> None:
        stop = threading.Event()
        threading.Thread(target=self._tick, args=(job, stop), daemon=True).start()
        parse_t0 = time.time()

        def on_progress(done: int, total: int, note: str = "") -> None:
            # 解析阶段占 2% ~ 55%，剩下的留给检测
            frac = (done / total) if total else 0.0
            with job.lock:
                job.progress = max(job.progress, 0.02 + 0.53 * min(1.0, max(0.0, frac)))
                job.target = job.progress
                if note:
                    job.note = note + _eta(parse_t0, done, total)

        try:
            job.stage, job.target = "parsing", 0.55
            job.note = "正在读取文件…"
            ctx = ParseContext(ocr=get_ocr(), workdir=str(WORK_DIR),
                               progress=on_progress,
                               should_stop=lambda: job.cancel_flag)
            doc = job.parser.parse(Path(job.path), ctx)
            if job.cancel_flag:
                raise YinzhanError("已取消")
            if not doc.pages:
                raise YinzhanError("这个文件里没有可识别的内容")
            job.doc = doc
            job.warnings.extend(doc.warnings or [])

            job.stage, job.target = "detecting", 0.95
            job.note = "正在比对敏感信息…"
            rules, pack_label, _ = _load_rules(job.fmt, job.pack_ids)
            job.pack_label = pack_label
            if not rules:
                job.warnings.append("没有匹配到任何规则包，只能靠人工框选")
            det = pipeline.detect(doc, rules, job.strictness)
            job.entities = det.entities
            job.rules = det.rules
            job.warnings.extend(det.warnings)

            # 表单填空里的人名：标签和名字被 OCR 切成两块，NER 看不见邻居
            try:
                form_ents = pipeline.find_form_names(doc, job.entities)
                form_ents += pipeline.find_party_names(doc, job.entities + form_ents)
                for e in form_ents:
                    job.entities.append(e)
                    job.rules[e.id] = pipeline._FORM_RULE
                if form_ents:
                    job.warnings.append(f"表单/并列人名补充 {len(form_ents)} 个")
            except Exception as exc:
                job.warnings.append(f"表单人名补充跳过：{exc}")

            pipeline.decide(job.entities, job.rules, job.session)

            # OCR 会把同一行切碎（公章压字、扫描歪斜），这里把已识别的值在
            # 全文其它位置补齐，否则签字页上的公司名会漏。
            try:
                propagated = pipeline.propagate_values(doc, job.entities)
                if propagated:
                    job.warnings.append(f"同值扩散补了 {propagated} 处")
            except Exception as exc:
                job.warnings.append(f"同值扩散跳过：{exc}")

            # 视觉标记（红章 / 手写签名）：OCR 读不出字，规则也匹配不到，
            # 但它们恰恰是合同里最能定位主体和签署人的东西。
            try:
                sig_blocks = pipeline.find_signature_blocks(doc)
            except Exception:
                sig_blocks = []
            for page_index, rect in sig_blocks:
                job.entities.append(Entity(
                    id=Entity.new_id(), etype="SIG_BLOCK", label="签署区", value="",
                    spans=[Span(rect=rect, page=page_index)],
                    level="L3", confidence=0.95, origin="visual", status="accepted",
                    decision=Decision(strategy="black", black=True, replacement=None),
                ))
            visual_ents = _visual_entities(doc, sig_blocks)
            if visual_ents:
                job.entities.extend(visual_ents)
            low_conf = _low_confidence_entities(doc, job.entities)
            if low_conf:
                job.entities.extend(low_conf)

            job.progress = 1.0
            job.note = ""
            job.stage = "ready"
        except Exception as exc:
            job.error = "已取消" if job.cancel_flag else f"{exc}"
            job.note = ""
            job.stage = "error"
            if not job.cancel_flag:
                job.warnings.append(traceback.format_exc(limit=3))
        finally:
            stop.set()

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        with job.lock:
            job.cancel_flag = True
            job.note = "正在停止…"
        return {"job_id": job.id, "cancelling": True}

    @staticmethod
    def _tick(job: Job, stop: threading.Event) -> None:
        while not stop.is_set():
            with job.lock:
                if job.progress < job.target:
                    job.progress = min(job.target, job.progress + (job.target - job.progress) * 0.18 + 0.004)
            stop.wait(0.18)

    # ── 编辑 ──
    def invalidate_preview(self, job: Job) -> None:
        job._preview_dirty = True
        job.preview_out = None
        job.preview_doc = None

    def mark(self, job_id: str, payload: dict) -> dict:
        job = self.get(job_id)
        with job.lock:
            if job.doc is None:
                raise YinzhanError("文件还没解析完")
            etype = (payload.get("etype") or "OTHER").upper()
            label = payload.get("label") or _TYPE_LABEL.get(etype, "其他敏感")
            page = int(payload.get("page") or 0)
            spans: list[Span] = []
            value = ""

            if payload.get("block_id"):
                bid = payload["block_id"]
                start = int(payload.get("start") or 0)
                end = int(payload.get("end") or 0)
                block = pipeline._find_block(job.doc, bid)
                if block is None:
                    raise YinzhanError("找不到这一段文字")
                if end <= start:
                    end = len(block.text)
                value = block.text[start:end]
                spans.append(Span(block_id=bid, start=start, end=end))

            if payload.get("rect"):
                r = [float(v) for v in payload["rect"]]
                pg = job.doc.pages[page] if page < len(job.doc.pages) else None
                w = pg.width if pg else 1.0
                h = pg.height if pg else 1.0
                # 前端传归一化 0~1，这里换成页面坐标
                if max(r) <= 1.0001:
                    rect = (r[0] * w, r[1] * h, r[2] * w, r[3] * h)
                else:
                    rect = (r[0], r[1], r[2], r[3])
                spans.append(Span(rect=rect, page=page))

            if not spans:
                raise YinzhanError("没有可标记的位置")

            ent = Entity(
                id=Entity.new_id(), etype=etype, label=label, value=value,
                spans=spans, level="L3" if etype in ("SECRET",) else "L2",
                confidence=1.0, origin="manual", status="accepted",
            )
            propagated = 0
            if payload.get("propagate") and value and len(value) >= 2:
                propagated = self._propagate(job, ent, value, page)

            ent.decision = Decision(strategy="black", black=True, replacement=None)
            job.entities.append(ent)
            self.invalidate_preview(job)
            return {"added": 1, "propagated": propagated, "entity": ent.to_dict()}

    def _propagate(self, job: Job, ent: Entity, value: str, page: int) -> int:
        seen = {(s.block_id, s.start, s.end) for s in ent.spans}
        n = 0
        for p, block in registry.iter_text_blocks(job.doc):
            idx = block.text.find(value)
            while idx >= 0:
                key = (block.id, idx, idx + len(value))
                if key not in seen and not _occupied(job.entities, block.id, idx, idx + len(value)):
                    ent.spans.append(Span(block_id=block.id, start=idx, end=idx + len(value)))
                    seen.add(key)
                    n += 1
                idx = block.text.find(value, idx + len(value))
        return n

    def update_entity(self, job_id: str, eid: str, payload: dict) -> dict:
        job = self.get(job_id)
        with job.lock:
            ent = next((e for e in job.entities if e.id == eid), None)
            if ent is None:
                raise YinzhanError("实体不存在")
            action = payload.get("action")
            if action == "accept":
                ent.status = "accepted"
            elif action == "reject":
                ent.status = "rejected"
                ent.decision = Decision(strategy="keep", replacement=ent.value)
            elif action == "replace":
                repl = payload.get("replacement") or ""
                ent.status = "accepted"
                ent.decision = Decision(strategy="surrogate", replacement=repl)
            self.invalidate_preview(job)
            return ent.to_dict()

    def accept_all(self, job_id: str) -> dict:
        job = self.get(job_id)
        with job.lock:
            n = 0
            for e in job.entities:
                if e.status == "pending":
                    e.status = "accepted"
                    n += 1
            self.invalidate_preview(job)
            return {"accepted": n, "total": len(job.entities)}

    # ── 预览 ──
    def preview(self, job_id: str, page: int, mode: str) -> dict:
        job = self.get(job_id)
        with job.lock:
            if job.doc is None:
                raise YinzhanError("还没解析完")
            doc = job.doc
            if mode == "out" and job.preview_doc is not None:
                doc = job.preview_doc
            return pipeline.build_preview(doc, job.parser, job.entities, page, mode)

    def render(self, job_id: str, page: int, mode: str) -> bytes:
        job = self.get(job_id)
        if job.doc is None:
            raise YinzhanError("还没解析完")
        parser = job.parser
        if mode == "out":
            # 生成成品预览可能要整篇导出（扫描件更慢），绝不能持着 job.lock 做，
            # 否则轮询会被卡住，界面看着就像死了。
            doc = self._ensure_preview(job)
        else:
            with job.lock:
                doc = job.doc
        data = parser.render(doc, page, mode)
        if not data:
            raise YinzhanError("这种格式不支持预览渲染")
        return data

    def _ensure_preview(self, job: Job) -> Document:
        with job.preview_lock:
            with job.lock:
                if not job._preview_dirty and job.preview_doc is not None:
                    return job.preview_doc
                if job.parser is None or job.doc is None:
                    raise YinzhanError("任务未就绪")
                parser = job.parser
                path = job.path
            out = WORK_DIR / f"{job.id}_preview{_ext(path)}"
            self._export_to(job, "ai", out, do_verify=False)
            ctx = ParseContext(ocr=get_ocr(), workdir=str(WORK_DIR))
            new_doc = parser.parse(out, ctx)
            with job.lock:
                job.preview_doc = new_doc
                job.preview_out = str(out)
                job._preview_dirty = False
            return new_doc

    # ── 导出 ──
    def export(self, job_id: str, mode: str) -> dict:
        job = self.get(job_id)
        if mode not in ("ai", "human"):
            raise YinzhanError("导出模式只能是 ai 或 human")
        with job.lock:
            if job.stage != "ready" or job.doc is None:
                raise YinzhanError("任务还没准备好")
            if not [e for e in job.entities if e.status != "rejected"]:
                raise YinzhanError("没有需要隐去的内容")
            job.stage = "exporting"
        try:
            seal = _seal_no()
            suffix = "换名" if mode == "ai" else "已隐去"
            stem = Path(job.name).stem
            out = unique_path(OUTPUT_DIR / f"{safe_name(stem, 60)}_{suffix}_{seal}{_ext(job.path)}")
            result = self._export_to(job, mode, out, do_verify=True)

            counts = job.counts()
            out_hash = sha256_file(out)
            audit.append(
                job_id=job.id, doc_name=job.name, doc_hash=sha256_file(job.path),
                fmt=job.fmt, counts={"total": counts["total"], "by_type": counts["by_type"]},
                rule_pack=job.pack_label, mode=mode,
                out_hash=out_hash, out_name=out.name,
            )
            if mode == "ai":
                vault.save_mapping(job.id, job.session.to_dict(),
                                   {"name": job.name, "seal": seal})
            record = {
                "out": str(out), "name": out.name, "seal_no": seal,
                "sha256": out_hash, "mode": mode,
                "verify": result.get("verify", {}),
                "warnings": result.get("warnings", []),
                "bytes": out.stat().st_size,
            }
            with job.lock:
                job.exports[mode] = record
                job.stage = "ready"
            return record
        except Exception:
            with job.lock:
                job.stage = "ready"
            raise

    def _export_to(self, job: Job, mode: str, out: Path, do_verify: bool) -> dict:
        ents = [e for e in job.entities if e.status != "rejected"]
        if mode == "human":
            for e in ents:
                e.decision = Decision(strategy="black", black=True, replacement=None)
        else:
            for e in ents:
                if e.decision is None:
                    e.decision = Decision(strategy="black", black=True)

        res = pipeline.export(job.doc, job.parser, ents, out, {"mode": mode})
        info = {"path": res.path, "warnings": list(res.warnings)}
        if do_verify:
            ctx = ParseContext(ocr=get_ocr(), workdir=str(WORK_DIR))
            try:
                info["verify"] = pipeline.verify(out, ents, job.parser, ctx)
            except Exception:
                out.unlink(missing_ok=True)
                raise
        else:
            info["verify"] = {}
        return info


_TYPE_LABEL = {
    "PERSON": "人名", "PHONE": "电话", "ADDRESS": "地址", "ID_CARD": "身份证",
    "BANK": "账号", "ORG": "机构", "AMOUNT": "金额", "CASE_NO": "案号",
    "EMAIL": "邮箱", "SECRET": "密钥", "OTHER": "其他敏感",
    "SEAL": "公章/印章", "SIGNATURE": "手写签名", "SIG_BLOCK": "签署区",
}

# 印章默认直接处理（它一定是敏感信息）；手写签名先标成待确认，
# 因为墨迹检测可能误报，让用户扫一眼再定。
_VISUAL_SPEC = {
    "seal":      ("SEAL", "公章/印章", "accepted"),
    "signature": ("SIGNATURE", "手写签名", "pending"),
}


def _inside(inner, outer) -> bool:
    return (inner[0] >= outer[0] - 4 and inner[1] >= outer[1] - 4 and
            inner[2] <= outer[2] + 4 and inner[3] <= outer[3] + 4)


# OCR 置信度低于这个值、又短又孤立的一行，多半是手写批注（编号、签字、日期），
# 规则匹配不到，但也不能放着不管 —— 标成待确认让用户扫一眼。
_OCR_LOW_CONF = 0.85
_OCR_LOW_MAX_LEN = 24


def _low_confidence_entities(doc: Document, entities: list[Entity]) -> list[Entity]:
    out: list[Entity] = []
    taken = [s for e in entities if e.status != "rejected" for s in e.spans if s.block_id]
    for page in doc.pages:
        for b in page.blocks:
            if b.kind != "text" or not b.text or not b.bbox:
                continue
            if not (b.ref or {}).get("ocr"):
                continue
            score = float((b.attrs or {}).get("score") or 1.0)
            text = b.text.strip()
            if score >= _OCR_LOW_CONF or not text or len(text) > _OCR_LOW_MAX_LEN:
                continue
            if any(s.block_id == b.id for s in taken):
                continue
            out.append(Entity(
                id=Entity.new_id(), etype="HANDWRITING", label="疑似手写", value="",
                spans=[Span(block_id=b.id, start=0, end=len(b.text))],
                level="L3", confidence=round(score, 3), origin="visual",
                status="pending",
                decision=Decision(strategy="black", black=True, replacement=None),
            ))
    return out


def _visual_entities(doc: Document, sig_blocks: list | None = None) -> list[Entity]:
    sig_by_page: dict[int, list] = {}
    for pg, rect in (sig_blocks or []):
        sig_by_page.setdefault(pg, []).append(rect)

    out: list[Entity] = []
    for vm in (getattr(doc, "visual_marks", None) or []):
        spec = _VISUAL_SPEC.get(vm.kind)
        if spec is None:
            continue
        # 已经落在「签署区」黑框里的印章，不用再单独列一次
        if vm.kind == "seal" and any(_inside(vm.rect, r) for r in sig_by_page.get(vm.page, [])):
            continue
        etype, label, status = spec
        out.append(Entity(
            id=Entity.new_id(), etype=etype, label=label, value="",
            spans=[Span(rect=vm.rect, page=vm.page)],
            level="L3", confidence=vm.confidence, origin="visual",
            status=status,
            decision=Decision(strategy="black", black=True, replacement=None),
        ))
    return out


def _occupied(entities: list[Entity], block_id: str, start: int, end: int) -> bool:
    for e in entities:
        if e.status == "rejected":
            continue
        for s in e.spans:
            if s.block_id == block_id and s.start >= 0 and s.start < end and start < s.end:
                return True
    return False


def _ext(path: str) -> str:
    return Path(path).suffix or ".bin"


def _eta(t0: float, done: int, total: int) -> str:
    """粗略估算剩余时间。扫描件一页好几秒，有句话用户才不慌。"""
    try:
        if not done or not total or done >= total:
            return ""
        elapsed = time.time() - t0
        if elapsed < 1.5:
            return ""
        remain = elapsed / float(done) * float(total - done)
        if remain < 5:
            return ""
        if remain < 60:
            return f" · 约剩 {int(round(remain))} 秒"
        return f" · 约剩 {int(round(remain / 60))} 分钟"
    except Exception:
        return ""


_UID_PREFIX = __import__("re").compile(r"^u_[0-9a-f]{12}_")


def _display_name(filename: str) -> str:
    """去掉上传时加的内部前缀，还原用户看到的文件名。"""
    return _UID_PREFIX.sub("", filename) or filename


_SEAL_COUNTER = 0
_SEAL_LOCK = threading.Lock()


def _seal_no() -> str:
    global _SEAL_COUNTER
    with _SEAL_LOCK:
        _SEAL_COUNTER += 1
        seq = _SEAL_COUNTER
    from .utils import now
    return f"{now().strftime('%Y%m%d')}-{seq:04d}"


# ────────────────────── 临时文件清理 ──────────────────────
# 「不留存」不能只是文案：上传副本和预览中间件必须在退出时清干净。
# 成品（out/）是用户主动要的，保留；工作目录和上传目录一律清掉。

def purge_runtime_dirs() -> int:
    """清空 uploads/ 和 work/，返回删掉的条目数。"""
    from .config import UPLOAD_DIR, WORK_DIR
    removed = 0
    for d in (UPLOAD_DIR, WORK_DIR):
        if not d.exists():
            continue
        for p in d.iterdir():
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                removed += 1
            except Exception:
                pass
    return removed


def _remove_for_job(job: "Job") -> None:
    """任务被淘汰时，删掉它的源文件副本和预览件。"""
    try:
        p = Path(job.path)
        from .config import UPLOAD_DIR
        if p.parent.resolve() == UPLOAD_DIR.resolve():
            p.unlink(missing_ok=True)
    except Exception:
        pass
    for f in (job.preview_out,):
        if f:
            try:
                Path(f).unlink(missing_ok=True)
            except Exception:
                pass


MAX_JOBS_IN_MEMORY = 24


def _evict(self: "JobManager") -> None:
    """内存里只留最近若干个任务，老的连源文件一起清掉。"""
    with self._lock:
        while len(self._order) > MAX_JOBS_IN_MEMORY:
            old_id = self._order.pop(0)
            job = self._jobs.pop(old_id, None)
            if job is not None:
                _remove_for_job(job)


JobManager._evict = _evict  # type: ignore[attr-defined]


MANAGER = JobManager()
