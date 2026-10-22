"""任务管理与状态机。

一个任务 = 一个文件从进来到出去的全过程。
解析/检测在后台线程跑，HTTP 线程只读快照，避免阻塞界面。
"""
from __future__ import annotations

import copy
import os
import random
import re
import shutil
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import date
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


def _embedded_media_info(doc: Document) -> dict:
    """解析器写在 ``doc.meta["embedded_media"]`` 的隐藏对象信息。

    P1-B 语义：``count`` = **无法自动脱敏、需用户确认**的隐藏对象数（图表/
    文本框/OLE/activeX/不可解码图片等）；可自动脱敏的内嵌图片在 ``images``，
    不计入 count。成品里图片仍然存在是正常的（已被解析器自动涂黑）。
    """
    try:
        media = (getattr(doc, "meta", None) or {}).get("embedded_media") or {}
        return dict(media) if isinstance(media, dict) else {}
    except Exception:
        return {}


# 同一 warning 在多页扫描件里常按页重复一次（典型：人像检测的「照片较大…」），
# 逐页堆叠会把真正需要看的错误淹掉。这里只合并**完全相同的字符串**（不同错误
# 绝不合并），保留首次出现顺序并标注出现次数；已带计数的可重复调用（幂等）。
_WARNING_COUNT_RE = re.compile(r"（共 (\d+) 次）$")


def _dedupe_warnings(warnings: list[str]) -> list[str]:
    """相同 warning 只保留一次并标注次数（如「……（共 12 次）」）。"""
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in warnings or []:
        text = item if isinstance(item, str) else str(item)
        if not text:
            continue
        match = _WARNING_COUNT_RE.search(text)
        if match:
            # 已带计数的条目：拆出基数并保留原次数，重复调用保持幂等
            base = text[:match.start()]
            try:
                count = int(match.group(1))
            except (TypeError, ValueError):
                count = 1
        else:
            base, count = text, 1
        if base not in counts:
            counts[base] = 0
            order.append(base)
        counts[base] += count
    return [f"{base}（共 {counts[base]} 次）" if counts[base] > 1 else base
            for base in order]


def _ensure_rect_black_decision(entity: Entity) -> None:
    """用户确认后给「无决策的矩形实体」补 black；未确认时保持 decision=None，
    导出阶段会被过滤掉（见 ``_export_to``），因此不会自动涂黑。"""
    if entity.decision is not None:
        return
    if any(span.rect for span in (entity.spans or [])):
        entity.decision = Decision(strategy="black", black=True, replacement=None)


@dataclass
class Job:
    id: str
    name: str
    path: str
    fmt: str
    # 来源：api=工作台/网页；mcp=Agent MCP 调用（只读标记，用于列表徽标与淘汰豁免）
    origin: str = "api"
    strictness: str = "standard"
    # 证件脱敏档位：fields=只遮头像/姓名/身份证号/住址（默认）；whole=整卡涂黑
    id_card_mode: str = "fields"
    # 精确时间模糊化（P1-A，默认关）：只把「日期+时刻」里的时刻替换成时段，日期保留
    time_blur: bool = False
    # 未成年人（<14 由身份证生日推断）：只提示，人工确认后走 minor-strict
    minor_detected: bool = False
    minor_strict: bool = False
    combination_risk: dict = field(default_factory=dict)
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
    # 「预览=成品」：缓存按 human 导出同一套几何算出的每页涂黑矩形
    _black_rects_cache: dict[int, list] | None = None
    _black_rects_sig: tuple | None = None

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

    def dedupe_warnings(self) -> list[str]:
        """任务层统一去重：相同字符串只留一条并带出现次数。"""
        self.warnings = _dedupe_warnings(self.warnings)
        return self.warnings

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
                # 快照也做一次（只读副本），运行中/人工构造的 Job 也不会泄漏重复噪声
                "warnings": _dedupe_warnings(self.warnings)[:20],
                "pages": len(self.doc.pages) if self.doc else 0,
                "counts": self.counts(),
                "created_at": self.created_at,
                "pack": self.pack_label,
                "strictness": self.strictness,
                "origin": self.origin,
                "id_card_mode": self.id_card_mode,
                "time_blur": self.time_blur,
                "minor_detected": self.minor_detected,
                "minor_strict": self.minor_strict,
                "combination_risk": self.combination_risk,
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
               pack_ids: list[str] | None = None,
               id_card_mode: str = "fields",
               time_blur: bool = False, origin: str = "api") -> Job:
        parser = registry.find_parser(path)
        if parser is None:
            raise YinzhanError(f"暂不支持这种文件：{path.suffix or '无扩展名'}")
        mode = id_card_mode if id_card_mode in ("fields", "whole") else "fields"
        job_id = "j_" + os.urandom(4).hex()
        job = Job(
            id=job_id, name=_display_name(path.name), path=str(path),
            fmt=parser.format_id, strictness=strictness, pack_ids=pack_ids,
            id_card_mode=mode, time_blur=bool(time_blur), parser=parser,
            origin="mcp" if origin == "mcp" else "api",
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

            # 金额一致性（P0 财务）：同一勾稽组统一缩放因子、合计按分项重算，
            # 避免逐处 ±25% 随机导致「合计≠分项之和」。见 docs/red-lines.md E4。
            try:
                from .replacers import amount_consistency

                plan = amount_consistency.build_amount_plan(
                    job.entities, job.rules, job.session)
                if plan:
                    amount_consistency.apply_plan(job.entities, plan, job.session)
                    job.warnings.append(f"金额一致性缩放 {len(plan)} 处")
            except Exception as exc:
                job.warnings.append(f"金额一致性处理跳过：{exc}")

            # Phase A2：跨字段/跨页 OCR 变体归并 —— 同一实体的近似原值统一假值，
            # 否则 AI 读到的文档里同一甲方会出现多个假名、自相矛盾。human 导出全黑
            # 覆盖，效果上只影响换名版。
            try:
                merged = pipeline.consolidate_surrogates(
                    job.entities, job.rules, job.session)
                if merged.get("groups"):
                    job.warnings.append(
                        f"近似实体假值归并 {merged['groups']} 组"
                        f"（{merged['members']} 个实体，统一 {merged['changed']} 个假值）")
            except Exception as exc:
                job.warnings.append(f"近似实体假值归并跳过：{exc}")

            # OCR 会把同一行切碎（公章压字、扫描歪斜），这里把已识别的值在
            # 全文其它位置补齐，否则签字页上的公司名会漏。
            try:
                propagated = pipeline.propagate_values(doc, job.entities)
                if propagated:
                    job.warnings.append(f"同值扩散补了 {propagated} 处")
            except Exception as exc:
                job.warnings.append(f"同值扩散跳过：{exc}")

            # Batch2-B2：明细行金额同值扩散 —— 合计行与明细行的同一个数值共用
            # 同一实体决策，避免明细行裸数字漏脱敏（human 版黑块会相应增加）。
            try:
                amount_spans = pipeline.propagate_amount_numeric_values(
                    doc, job.entities)
                if amount_spans:
                    job.warnings.append(f"明细金额同值扩散 {amount_spans} 处")
            except Exception as exc:
                job.warnings.append(f"明细金额同值扩散跳过：{exc}")

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
            warn_before = len(doc.warnings or [])
            visual_ents = _visual_entities(doc, sig_blocks, job.id_card_mode)
            if visual_ents:
                job.entities.extend(visual_ents)
            # 证件页里的 OCR 文字实体（比如整块「住址+姓名+出生+性别」被 OCR 合并）
            # 在证件档位下会被字段框/整卡框覆盖；保留会重复涂黑、甚至把要保留的
            # 性别/民族/出生行一起涂掉。所以证件命中范围内的文字实体统一让位。
            _drop_text_entities_in_id_cards(job, doc)
            # 视觉层可能新增「低置信码类候选未自动处理」等提示，补进 job.warnings
            job.warnings.extend((doc.warnings or [])[warn_before:])
            # 证件页已经有整卡/字段脱敏兜底，人脸大图提示对证件页是噪音
            if any(getattr(m, "kind", "") == "id_document"
                   for m in (getattr(doc, "visual_marks", None) or [])):
                job.warnings = [w for w in job.warnings if "照片较大" not in w]
            low_conf = _low_confidence_entities(doc, job.entities)
            if low_conf:
                job.entities.extend(low_conf)

            # P1-A：精确时间模糊化（默认关）、未成年人推断、组合风险提示
            try:
                if job.time_blur:
                    _apply_time_blur(job)
            except Exception as exc:
                job.warnings.append(f"精确时间模糊化跳过：{exc}")
            try:
                minor_age = _find_minor_age(doc)
                if minor_age is not None:
                    job.minor_detected = True
                    job.warnings.append(
                        "检测到未满十四周岁信息（由身份证号生日推断），"
                        "建议启用「未成年人严格模式」（证件整卡+全部涂黑）")
            except Exception as exc:
                job.warnings.append(f"未成年人推断跳过：{exc}")
            try:
                job.combination_risk = _combination_risk(job)
                if job.combination_risk:
                    cats = "、".join(job.combination_risk["categories"])
                    job.warnings.append(
                        f"组合可识别风险：本媒体同时包含 {cats} 等 "
                        f"{len(job.combination_risk['categories'])} 类准标识符，"
                        "建议对场景照片整图涂黑或人工复核")
            except Exception as exc:
                job.warnings.append(f"组合风险统计跳过：{exc}")

            job.progress = 1.0
            job.note = ""
            job.dedupe_warnings()
            job.stage = "ready"
        except Exception as exc:
            job.error = "已取消" if job.cancel_flag else f"{exc}"
            job.note = ""
            job.stage = "error"
            if not job.cancel_flag:
                job.warnings.append(traceback.format_exc(limit=3))
        finally:
            job.dedupe_warnings()
            stop.set()

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        with job.lock:
            job.cancel_flag = True
            job.note = "正在停止…"
        return {"job_id": job.id, "cancelling": True}

    def minor_strict(self, job_id: str) -> dict:
        """P1-A：未成年人严格模式（人工确认后）：证件整卡 + 全部实体强制涂黑。"""
        job = self.get(job_id)
        with job.lock:
            if job.doc is None or job.stage not in ("ready", "exporting"):
                raise YinzhanError("任务还没准备好")
            if not job.minor_detected:
                raise YinzhanError("未检测到未满十四周岁信息，不能启用未成年人严格模式")
            job.minor_strict = True
            job.id_card_mode = "whole"
            # 清掉旧的视觉证件字段/整卡实体，统一改为整卡；文字 ID_CARD 保留并强制涂黑
            job.entities = [
                e for e in job.entities
                if not (e.origin == "visual" and e.etype in _ID_VISUAL_ETYPES)
            ]
            for vm in (getattr(job.doc, "visual_marks", None) or []):
                if getattr(vm, "kind", "") == "id_document":
                    job.entities.append(Entity(
                        id=Entity.new_id(), etype="ID_DOCUMENT", label="证件", value="",
                        spans=[Span(rect=vm.rect, page=getattr(vm, "page", 0))],
                        level="L3", confidence=getattr(vm, "confidence", 0.9),
                        origin="visual", status="accepted",
                        decision=Decision(strategy="black", black=True, replacement=None),
                    ))
            # 严格模式不可换名还原：全部实体强制涂黑
            for entity in job.entities:
                if entity.status == "rejected":
                    continue
                entity.status = "accepted"
                entity.decision = Decision(strategy="black", black=True, replacement=None)
            job._preview_dirty = True
            job.warnings.append(
                "已启用未成年人严格模式：证件整卡涂黑 + 全部实体强制涂黑（不可换名还原）")
            job.dedupe_warnings()
            return job.snapshot()

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
                _ensure_rect_black_decision(ent)
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
                    _ensure_rect_black_decision(e)
                    n += 1
            self.invalidate_preview(job)
            return {"accepted": n, "total": len(job.entities)}

    # ── 预览 ──
    def _human_black_rects(self, job: Job) -> dict[int, list] | None:
        """按 human 导出的同一套实体/决策/几何，计算每页涂黑矩形。

        预览端直接使用这些矩形（而不是按实体框估算），因此「在线预览的涂黑
        形态」与「下载的涂黑版成品」使用完全相同的几何，不会再出现预览整行黑、
        成品只涂实体（或反之）的不一致。返回 None 表示该格式不支持，预览回退。
        """
        if job.doc is None or job.parser is None:
            return None
        sig = tuple(
            (e.id, e.status,
             (e.decision.strategy, bool(e.decision.black)) if e.decision else None)
            for e in job.entities
        )
        if job._black_rects_sig == sig and job._black_rects_cache is not None:
            return job._black_rects_cache
        fn = getattr(job.parser, "export_black_rects", None)
        if not callable(fn):
            return None
        ents: list[Entity] = []
        try:
            for e in job.entities:
                # 与 _export_to(mode="human") 完全相同的筛选/决策：
                # rejected 与「尚未确认（decision=None）」不参与导出。
                if e.status == "rejected" or e.decision is None:
                    continue
                clone = copy.copy(e)
                clone.decision = Decision(strategy="black", black=True, replacement=None)
                ents.append(clone)
            rects = fn(job.doc, ents, {})
            rects = {int(k): list(v) for k, v in (rects or {}).items()}
        except Exception:
            return None
        job._black_rects_sig = sig
        job._black_rects_cache = rects
        return rects

    def preview(self, job_id: str, page: int, mode: str) -> dict:
        job = self.get(job_id)
        with job.lock:
            if job.doc is None:
                raise YinzhanError("还没解析完")
            doc = job.doc
            if mode == "out" and job.preview_doc is not None:
                doc = job.preview_doc
            black_rects = None
            if mode == "out" and getattr(job.parser, "can_render", False):
                all_rects = self._human_black_rects(job)
                if all_rects is not None:
                    black_rects = all_rects.get(int(page), [])
            return pipeline.build_preview(doc, job.parser, job.entities, page, mode,
                                          black_rects=black_rects)

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
    def export(self, job_id: str, mode: str, options: dict | None = None) -> dict:
        """导出成品。

        :param options: 导出选项。``allow_unprocessed_media=True`` 表示用户已确认
            「文件里有未脱敏的内嵌图片，仍要导出」（P0 安全默认：检测到即阻断，
            必须二次确认；见 docs/red-lines.md 扩展点 E3）。
        """
        job = self.get(job_id)
        if mode not in ("ai", "human"):
            raise YinzhanError("导出模式只能是 ai 或 human")
        options = dict(options or {})
        with job.lock:
            if job.stage != "ready" or job.doc is None:
                raise YinzhanError("任务还没准备好")
            active = [e for e in job.entities if e.status != "rejected"]
            # 「仅清理」场景：没有可涂的实体，但文件里有必须剥离的元数据/地理参考
            # （典型：只有 Geo 标签、没有可 OCR 文字的正射影像）。以前会直接报
            # 「没有需要隐去的内容」，导致这类文件出不去。
            if not active and not pipeline.has_cleanup_targets(job.doc):
                raise YinzhanError("没有需要隐去的内容")
            media = _embedded_media_info(job.doc)
            media_count = int(media.get("count") or 0)
            if media_count and not options.get("allow_unprocessed_media"):
                err = YinzhanError(
                    f"检测到 {media_count} 个无法自动脱敏的隐藏对象"
                    "（图表/文本框/OLE 等），继续导出会保留它们。要继续吗？")
                err.code = "media_confirmation_required"
                err.media_count = media_count
                raise err
            job.stage = "exporting"
        try:
            seal = _seal_no()
            # 源文件哈希只算一次：它既是 audit 的 doc_hash，也是 AI 中性文件名里
            # 的 sha8（导出名不能带源 stem，见 ai_export_name 注释）。
            src_hash = sha256_file(job.path)
            ext = _ext(job.path)
            if mode == "ai":
                out = unique_path(OUTPUT_DIR / ai_export_name(src_hash, ext, seal))
            else:
                # 给人看（human）版命名规则保持原样，逐字节不变。
                stem = Path(job.name).stem
                out = unique_path(
                    OUTPUT_DIR / f"{safe_name(stem, 60)}_已隐去_{seal}{ext}")
            result = self._export_to(job, mode, out, do_verify=True, options=options)

            counts = job.counts()
            out_hash = sha256_file(out)
            pdf_hidden = (job.doc.extra or {}).get("pdf_hidden") if job.doc else None
            office_media = (job.doc.extra or {}).get("office_media") if job.doc else None
            audit.append(
                job_id=job.id, doc_name=job.name, doc_hash=src_hash,
                fmt=job.fmt,
                counts={"total": counts["total"], "by_type": counts["by_type"],
                        # 未自动处理隐藏对象经用户二次确认放行时，审计留下痕迹
                        "media_count": media_count,
                        "media_confirmed": bool(options.get("allow_unprocessed_media")),
                        # Office 内嵌图片自动脱敏/隐藏对象统计
                        "office_media": office_media or {},
                        # PDF 隐藏数据（附件/批注/表单/JS）剥离/保留痕迹
                        "pdf_hidden": pdf_hidden or {},
                        "keep_pdf_hidden": bool(options.get("keep_pdf_hidden"))},
                rule_pack=job.pack_label, mode=mode,
                out_hash=out_hash, out_name=out.name,
            )
            if mode == "ai":
                # 映射库除文字实体图外，也保存解析器交出来的地理坐标变换参数
                # （正射 TIF 中性化需要可还原；见 docs/red-lines.md E5）。
                extra = {"name": job.name, "seal": seal}
                geo_params = (job.doc.extra or {}).get("geo_params") if job.doc else None
                if geo_params:
                    extra["geo"] = geo_params
                vault.save_mapping(job.id, job.session.to_dict(), extra)
            record = {
                "out": str(out), "name": out.name, "seal_no": seal,
                "sha256": out_hash, "mode": mode,
                "verify": result.get("verify", {}),
                # 导出结果层同样统一去重（保留首次顺序 + 次数）
                "warnings": _dedupe_warnings(result.get("warnings", [])),
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

    def _export_to(self, job: Job, mode: str, out: Path, do_verify: bool,
                   options: dict | None = None) -> dict:
        options = dict(options or {})
        # Batch2-B4（第二步）：AI 换名版把「待确认的手写视觉实体」按涂黑纳入
        # 导出（安全优先，免得 AI 读到手写签名/批注）；human 涂黑版保持现状
        # （pending 仍交给人工确认，不在这里自动处理）。
        ai_handwriting_black = 0
        if mode == "ai":
            for entity in job.entities:
                if (entity.status != "pending"
                        or (entity.etype or "").upper() != "HANDWRITING"):
                    continue
                entity.decision = Decision(
                    strategy="black", black=True, replacement=None)
                ai_handwriting_black += 1
        # 没有 decision 的实体 = 尚未确认（典型：异常大面积码类框）。它们不参与
        # 导出，也绝不能在导出阶段被兜底成 black；用户 accept 后才会补上决策。
        ents = [e for e in job.entities
                if e.status != "rejected" and e.decision is not None]
        if mode == "human":
            for e in ents:
                e.decision = Decision(strategy="black", black=True, replacement=None)

        res = pipeline.export(job.doc, job.parser, ents, out,
                              {"mode": mode, **options})
        if ai_handwriting_black:
            msg = (f"AI 视图将 {ai_handwriting_black} 个疑似手写签名按涂黑处理"
                   "（仅换名版；涂黑版保持人工确认）")
            res.warnings.append(msg)
            try:
                job.warnings.append(msg)
            except Exception:
                pass
        info = {"path": res.path, "warnings": _dedupe_warnings(list(res.warnings))}
        if do_verify:
            ctx = ParseContext(ocr=get_ocr(), workdir=str(WORK_DIR))
            try:
                info["verify"] = pipeline.verify(
                    out, ents, job.parser, ctx,
                    doc=job.doc, options=options)
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
    "HANDWRITING": "疑似手写",
    "FACE": "人脸", "ID_DOCUMENT": "证件", "PLATE": "车牌",
    "ID_PHOTO": "证件头像", "ID_NAME": "证件姓名",
    "ID_NUMBER": "证件号码", "ID_ADDRESS": "证件住址",
    "ID_AUTHORITY": "证件签发机关", "ID_VALIDITY": "证件有效期",
    "ROAD_SIGN": "路牌", "DOOR_ADDRESS": "门牌/门址",
    "SHOP_SIGN": "店招/门头", "ORG_PLAQUE": "单位铭牌",
    "CHAT_AVATAR": "聊天头像", "CHAT_NAME": "聊天昵称",
    "CHAT_GROUP": "群名称", "CHAT_TIME": "聊天时间",
    "CHAT_INPUT": "聊天输入框",
    "EMBEDDED_IMAGE": "内嵌图片（敏感）",
    "PASSPORT": "护照号",
    "QRCODE": "二维码", "BARCODE": "条形码",
}

# 视觉/码类标记的展示口径。前三元组向后兼容旧代码：
# (etype, label, status[, level])；视觉类默认都不可逆涂黑，低置信度的先进待确认。
# 人脸/证件/码类属于识别身份或承载数据的高危目标，默认直接处理；
# 车牌/路牌/门址可能误报，先标待确认，让用户在对照台一眼确认。
_VISUAL_SPEC = {
    "seal":        ("SEAL", "公章/印章", "accepted", "L3"),
    "signature":   ("SIGNATURE", "手写签名", "pending", "L3"),
    "face":        ("FACE", "人脸", "accepted", "L3"),
    "id_document": ("ID_DOCUMENT", "证件", "accepted", "L3"),
    # 身份证关键字段（A 方案默认档）：只遮头像/姓名/身份证号/住址
    "id_photo":    ("ID_PHOTO", "证件头像", "accepted", "L3"),
    "id_name":     ("ID_NAME", "证件姓名", "accepted", "L3"),
    "id_number":   ("ID_NUMBER", "证件号码", "accepted", "L3"),
    "id_address":  ("ID_ADDRESS", "证件住址", "accepted", "L3"),
    # 身份证反面：签发机关 + 有效期限（用户 2026-10-20 追加要求一并抹除）
    "id_authority": ("ID_AUTHORITY", "证件签发机关", "accepted", "L3"),
    "id_validity":  ("ID_VALIDITY", "证件有效期", "accepted", "L3"),
    "plate":       ("PLATE", "车牌", "pending", "L2"),
    "road_sign":   ("ROAD_SIGN", "路牌", "pending", "L2"),
    "door_address": ("DOOR_ADDRESS", "门牌/门址", "pending", "L2"),
    "shop_sign":   ("SHOP_SIGN", "店招/门头", "pending", "L2"),
    "org_plaque":  ("ORG_PLAQUE", "单位铭牌", "pending", "L2"),
    # 聊天截图逐项（P1-A）：头像/昵称/群名/时间/输入框
    "chat_avatar": ("CHAT_AVATAR", "聊天头像", "accepted", "L3"),
    "chat_name":   ("CHAT_NAME", "聊天昵称", "accepted", "L3"),
    "chat_group":  ("CHAT_GROUP", "群名称", "accepted", "L3"),
    "chat_time":   ("CHAT_TIME", "聊天时间", "accepted", "L2"),
    "chat_input":  ("CHAT_INPUT", "聊天输入框", "accepted", "L3"),
    # PDF 文本层页面里的内嵌图片命中敏感目标：整块图片区域不可逆涂黑
    "embedded_image": ("EMBEDDED_IMAGE", "内嵌图片（敏感）", "accepted", "L3"),
    "qrcode":      ("QRCODE", "二维码", "accepted", "L3"),
    "barcode":     ("BARCODE", "条形码", "accepted", "L2"),
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


_ID_FIELD_KINDS = frozenset({"id_photo", "id_name", "id_number", "id_address",
                             "id_authority", "id_validity"})

# 精确时间：只认「完整日期 + 时刻」，只模糊时刻；日期保留（P1-A，默认关）
_TIME_BLUR_RE = re.compile(
    r"(?<!\d)(?P<date>(?:19|20)\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?)"
    r"[ ,，]*(?P<time>(?:[上下]午\s*)?\d{1,2}\s*[:：]\s*\d{2}(?::\d{2})?)(?!\d)")
# 身份证号（18 位）用于未成年人推断；校验函数在解析时动态导入
_ID18_RE = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
    r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")

_ID_ETYPES = frozenset({
    "ID_CARD", "ID_DOCUMENT", "ID_PHOTO", "ID_NAME", "ID_NUMBER",
    "ID_ADDRESS", "ID_AUTHORITY", "ID_VALIDITY",
})
# 严格模式只替换「视觉证件框」，文字类 ID_CARD 实体必须保留（强制涂黑）
_ID_VISUAL_ETYPES = frozenset({
    "ID_DOCUMENT", "ID_PHOTO", "ID_NAME", "ID_NUMBER",
    "ID_ADDRESS", "ID_AUTHORITY", "ID_VALIDITY",
})
# 非身份证证件的展示名（扫描器 note 里的 doc= 稳定标识）
_ID_DOC_LABELS: dict[str, str] = {
    "passport": "护照",
    "driver_license": "驾驶证",
    "business_license": "营业执照",
    "social_security": "社保卡",
    "work_permit": "工作证",
    "residence_permit": "居住证",
    "hk_mo_permit": "港澳通行证",
    "tw_permit": "台胞证",
    "foreign_pr": "外国人永居证",
    "bank_card": "银行卡",
}


def _time_period(text: str) -> str:
    """把具体时刻替换成时段；保留上午/下午前缀语义。"""
    if "上午" in text:
        return "上午"
    if "下午" in text:
        return "下午"
    if "晚上" in text:
        return "晚上"
    match = re.search(r"\d{1,2}", text)
    try:
        hour = int(match.group(0)) if match else 12
    except ValueError:
        hour = 12
    if hour < 6:
        return "凌晨"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    return "晚上"


def _apply_time_blur(job: "Job") -> None:
    """P1-A：把「日期+时刻」里的时刻替换成时段，日期保留。"""
    doc = job.doc
    if doc is None or not job.time_blur:
        return
    added = 0
    seen: set[tuple[str, int, int]] = set()
    for page in doc.pages:
        for block in page.blocks:
            text = block.text or ""
            if not text:
                continue
            for match in _TIME_BLUR_RE.finditer(text):
                start, end = match.span("time")
                key = (block.id, start, end)
                if key in seen:
                    continue
                seen.add(key)
                raw = match.group("time").strip()
                replacement = _time_period(raw)
                job.entities.append(Entity(
                    id=Entity.new_id(), etype="TIME", label="精确时间",
                    value=raw, spans=[Span(block_id=block.id, start=start, end=end)],
                    level="L2", confidence=0.9, origin="rule", status="accepted",
                    decision=Decision(strategy="surrogate", replacement=replacement),
                ))
                added += 1
    if added:
        job.warnings.append(f"精确时间模糊化 {added} 处（日期保留，仅时刻→时段）")


def _find_minor_age(doc: Document) -> int | None:
    """P1-A：从文档里的身份证号推断年龄，只返回 <14 的年龄（不做人脸年龄估计）。"""
    try:
        from .detectors.regex_det import cn_id_checksum
    except Exception:
        return None
    today = date.today()
    for page in doc.pages:
        for block in page.blocks:
            for match in _ID18_RE.finditer(block.text or ""):
                value = match.group(0)
                try:
                    if not cn_id_checksum(value):
                        continue
                    birth = date(int(value[6:10]), int(value[10:12]), int(value[12:14]))
                except Exception:
                    continue
                age = today.year - birth.year - (
                    (today.month, today.day) < (birth.month, birth.day))
                if 0 <= age < 14:
                    return age
    return None


_COMBINATION_CATEGORIES: dict[str, str] = {
    "ADDRESS": "位置", "DOOR_ADDRESS": "位置",
    "ID_CARD": "证件", "ID_DOCUMENT": "证件", "ID_PHOTO": "证件",
    "ID_NAME": "证件", "ID_NUMBER": "证件", "ID_ADDRESS": "证件",
    "ID_AUTHORITY": "证件", "ID_VALIDITY": "证件",
    "FACE": "人脸",
    "PLATE": "车辆",
    "PERSON": "联系人", "PHONE": "联系人", "EMAIL": "联系人",
    "SOCIAL_ID": "联系人", "CHAT_NAME": "联系人", "CHAT_AVATAR": "联系人",
    "TIME": "时间", "CHAT_TIME": "时间",
}


def _combination_risk(job: "Job") -> dict:
    """P1-A：统计准标识符类别；≥2 类提示组合可识别风险（只提示，不自动改）。"""
    cats: set[str] = set()
    for entity in job.entities:
        if entity.status == "rejected":
            continue
        cat = _COMBINATION_CATEGORIES.get(entity.etype)
        if cat:
            cats.add(cat)
    if len(cats) >= 2:
        return {"level": "high" if len(cats) >= 3 else "medium",
                "categories": sorted(cats)}
    return {}


def _drop_text_entities_in_id_cards(job: "Job", doc: Document) -> None:
    """证件页内由 OCR 文本块产生的文字实体让位给证件字段/整卡框。

    实测身份证 OCR 会把「住址+姓名+出生+性别」合并成一个文本块，若保留该
    ADDRESS 实体，会把整块（含要保留的性别/民族/出生）一起涂掉；字段档下用
    id_* 框精确遮，文字实体应丢弃。只丢 spans 全部落在证件矩形内的文字实体。
    """
    card_rects: dict[int, list] = {}
    for mark in getattr(doc, "visual_marks", None) or []:
        if getattr(mark, "kind", "") == "id_document":
            card_rects.setdefault(getattr(mark, "page", 0), []).append(mark.rect)
    if not card_rects:
        return
    # block_id -> (page, bbox)
    blocks: dict[str, tuple[int, tuple | None]] = {}
    for page in doc.pages:
        for block in page.blocks:
            blocks[block.id] = (page.index, block.bbox)
    kept: list[Entity] = []
    for ent in job.entities:
        if ent.origin == "visual":
            kept.append(ent)
            continue
        rects = []
        for span in ent.spans or []:
            if span.rect:
                rects.append((span.page if span.page is not None else 0, span.rect))
            elif span.block_id and span.block_id in blocks:
                page_idx, bbox = blocks[span.block_id]
                if bbox:
                    rects.append((page_idx, bbox))
        if not rects:
            kept.append(ent)
            continue
        inside = all(
            any(_inside(rect, card) for card in card_rects.get(page, []))
            for page, rect in rects
        )
        if not inside:
            kept.append(ent)
    job.entities = kept


def _mark_note(note: str) -> dict[str, str]:
    """解析扫描器 note 里的 k=v 片段（side=front;fields=4 等）。"""
    data: dict[str, str] = {}
    for part in str(note or "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            data[key.strip()] = value.strip()
    return data


def _visual_entities(doc: Document, sig_blocks: list | None = None,
                     id_card_mode: str = "fields") -> list[Entity]:
    sig_by_page: dict[int, list] = {}
    for pg, rect in (sig_blocks or []):
        sig_by_page.setdefault(pg, []).append(rect)

    def page_area(page: int) -> float:
        try:
            pg = doc.pages[page]
            return max(1.0, float(pg.width) * float(pg.height))
        except Exception:
            return 1.0

    marks = list(getattr(doc, "visual_marks", None) or [])
    mode = id_card_mode if id_card_mode in ("fields", "whole") else "fields"
    id_docs = [m for m in marks if m.kind == "id_document"]
    field_marks = [m for m in marks if m.kind in _ID_FIELD_KINDS]
    others = [m for m in marks
              if m.kind not in _ID_FIELD_KINDS and m.kind != "id_document"]

    chosen = list(others)
    if mode == "whole":
        # 整卡档：只保留整卡/整页命中，字段命中不重复涂
        chosen.extend(id_docs)
    else:
        # 关键字段档（默认）：
        #   正面 -> 头像/姓名/身份证号/住址；
        #   反面 -> 签发机关/有效期限（用户 2026-10-20 追加）。
        chosen.extend(field_marks)
        for mark in id_docs:
            meta = _mark_note(mark.note)
            page = getattr(mark, "page", 0)
            doc_type = meta.get("doc", "")
            if doc_type and doc_type not in ("id_card",):
                # 非身份证证件没有字段模板（护照/驾驶证/工作证/居住证/港澳台/
                # 永居证/银行卡等）：整卡/整区域涂黑，不做字段定位。
                chosen.append(mark)
                continue
            side = meta.get("side", "") or "front"
            if meta.get("fallback") == "1":
                card_fields: list = []
            else:
                card_fields = []
                for fm in field_marks:
                    if getattr(fm, "page", 0) != page:
                        continue
                    f_side = _mark_note(getattr(fm, "note", "")).get("side", "front")
                    if not f_side:
                        f_side = "front"
                    if f_side == side:
                        card_fields.append(fm)
            if card_fields:
                # 该面字段已命中：整卡命中不加入，避免把卡面全部涂掉
                continue
            chosen.append(mark)
            if side == "back":
                doc.warnings.append(
                    "身份证反面字段定位失败，已按整卡涂黑（可改用整卡模式）")
            else:
                doc.warnings.append(
                    "证件字段定位失败，已按整卡涂黑（可改用整卡模式）")

    out: list[Entity] = []
    for vm in chosen:
        spec = _VISUAL_SPEC.get(vm.kind)
        if spec is None:
            continue
        # 码类「仅检测未解码（/detect）」的高置信度不可信：实测发票/合同页面上
        # 木纹、图标等会被误检成二维码，最大一处覆盖 35% 页面；自动涂黑会误伤整块
        # 正文。这里对面积 >5% 页面的 detect-only 候选**不自动打码**，而是生成
        # status=pending / decision=None 的待确认实体：用户在对照台能看到并 accept，
        # 确认后由 update_entity/accept_all 补 black 决策，导出时才涂黑。
        if vm.kind in ("qrcode", "barcode") and "/detect" in (getattr(vm, "note", "") or ""):
            rect = vm.rect
            area = max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])
            if area / page_area(vm.page) > 0.05:
                spec = _VISUAL_SPEC.get(vm.kind) or ("QRCODE", "二维码", "pending", "L3")
                out.append(Entity(
                    id=Entity.new_id(), etype=spec[0],
                    label=f"{spec[1]}（面积异常，待确认）", value="",
                    spans=[Span(rect=rect, page=vm.page)],
                    level=spec[3] if len(spec) > 3 else "L3",
                    confidence=vm.confidence, origin="visual",
                    status="pending", decision=None,
                ))
                doc.warnings.append(
                    "检测到疑似二维码/条码但面积异常，未自动打码，已列入待确认列表，请人工确认")
                continue
        # 已经落在「签署区」黑框里的印章，不用再单独列一次
        if vm.kind == "seal" and any(_inside(vm.rect, r) for r in sig_by_page.get(vm.page, [])):
            continue
        etype, label, status = spec[0], spec[1], spec[2]
        if vm.kind == "id_document":
            doc_type = _mark_note(getattr(vm, "note", "")).get("doc", "")
            label = _ID_DOC_LABELS.get(doc_type, label)
        level = spec[3] if len(spec) > 3 else "L3"
        out.append(Entity(
            id=Entity.new_id(), etype=etype, label=label, value="",
            spans=[Span(rect=vm.rect, page=vm.page)],
            level=level, confidence=vm.confidence, origin="visual",
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


def ai_export_name(sha256_hex: str, ext: str, seal: str = "") -> str:
    """「给 AI 看」成品的文件名（中性化）。

    文件名本身经常携带个人信息（"张三_身份证.jpg"、"某某公司_对账单.pdf"），
    导出给 AI 时不能沿用源 stem。这里只保留**内容 sha256 前 8 位**（同一文件
    稳定可对账，且无法反推原名）、模式 token ``_换名_``（供 UI/测试识别）和
    交付编号 ``seal``（MCP 路径没有编号时传空串）。human（给人看）版不用本
    函数，保持既有 ``{stem}_已隐去_{seal}`` 规则不变。
    """
    token = f"AI视图_{sha256_hex[:8]}_换名"
    return f"{token}_{seal}{ext}" if seal else f"{token}{ext}"


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
    """内存里只留最近若干任务，老的连源文件一起清掉。

    MCP 来源的最近 8 个任务豁免淘汰：Agent 会话可能持续数分钟才回来复核/导出，
    不能被工作台的新任务挤掉；超过 8 个后仍按最老优先淘汰。
    """
    with self._lock:
        mcp_ids = [jid for jid in self._order
                   if jid in self._jobs and self._jobs[jid].origin == "mcp"]
        protected = set(mcp_ids[-8:])
        while len(self._order) > MAX_JOBS_IN_MEMORY:
            old_id = next((jid for jid in self._order if jid not in protected), None)
            if old_id is None:
                break
            self._order.remove(old_id)
            job = self._jobs.pop(old_id, None)
            if job is not None:
                _remove_for_job(job)


JobManager._evict = _evict  # type: ignore[attr-defined]


MANAGER = JobManager()
