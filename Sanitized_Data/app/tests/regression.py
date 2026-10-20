# -*- coding: utf-8 -*-
"""数据隐站 · 回归门禁（qa 所有）。

一条命令给结论：

    python -X utf8 app/tests/regression.py

退出码：0 = 全绿；1 = 有硬项失败（回归）；2 = 基线/环境异常。
``--update-baseline`` 在**确认是改进**（不是回归）后重录基线：
    python -X utf8 app/tests/regression.py --update-baseline

设计原则（见 docs/red-lines.md）：
  * 硬项 = 用户已有体验 + 安全边界：原有必须命中的变少 / 导出失败 / 残留>0 /
    元数据回来了 / 服务外呼 → FAIL，必须修或回滚；
  * 新增识别、新增规则、新增实体 → 只提示（软），不算回归；
  * 已知边界（Office 内嵌图仅告警、PDF 文本层不 OCR 等）只做软快照。

注意：Windows 控制台默认 GBK，必须 ``python -X utf8`` 运行。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import traceback
import zipfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import qa_common as qa  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = qa.GREEN, qa.RED, qa.YELLOW, qa.DIM, qa.RESET

# ── 已批准的"规则迁移/去重"白名单 ──────────────────────────────────────
# 规则从一个包搬到另一个包、或与别的规则合并去重时，在此登记：id -> 理由/替代规则。
# 未登记的规则消失 = 硬失败（防止静默减少覆盖）；登记时必须同时验证替代规则仍存在。
# 新增条目需要在交接/评审里说明（这等于承认"该规则 id 不再存在，但能力有替代"）。
RULE_REMOVAL_ALLOWLIST: dict[str, dict] = {
    "trade.swift_bic": {
        "reason": "去重：SWIFT/BIC 由 finance-cn 的 cn.swift_bic（含 ISO 9362 校验位）"
                  "统一覆盖，trade-cn 不再重复定义（2026-10-19，Lead/rule-dev 批准）",
        "replaced_by": "cn.swift_bic",
    },
}

# ── P0 二波（PIPL 收口，2026-10-20）的明确契约 ─────────────────────────
# 10 条原先 scope 缺 image/tiff 的规则必须补上（图片/TIF 也能识别）。
SCOPE_EXPANDED_RULE_IDS = {
    "cn.amount_cn", "cn.credit_line", "cn.iban", "cn.reg_capital",
    "cn.bank_routing_no", "cn.pay_platform_id", "cn.lawyer_license",
    "cn.legal_party", "cn.department", "cn.sensitive_diagnosis",
}
# 新增规则包的最小自有规则数（低于此视为删规则=回归）
PACK_MIN_OWN_RULES = {"pipl-cn": 21, "table-cn": 11}
# 新增规则包要求 id 命名空间（防止串包）
PACK_ID_PREFIX = {"pipl-cn": "pipl.", "table-cn": "table."}


# ══════════════════════════ 结果收集 ══════════════════════════
class Gate:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.fails = 0
        self.passes = 0
        self.warns = 0

    def group(self, title: str) -> None:
        self.rows.setdefault(title, [])
        print(f"\n{DIM}── {title} ──{RESET}")

    def _row(self, title: str, status: str, name: str, detail: str) -> None:
        self.rows.setdefault(title, []).append(
            {"name": name, "status": status, "detail": detail})
        if status == "PASS":
            self.passes += 1
            head = f"{GREEN}[PASS]{RESET}"
        elif status == "FAIL":
            self.fails += 1
            head = f"{RED}[FAIL]{RESET}"
        else:
            self.warns += 1
            head = f"{YELLOW}[WARN]{RESET}"
        print(f"  {head} {name}" + (f" - {detail}" if detail else ""))

    def hard(self, name: str, ok: bool, detail: str = "") -> bool:
        self._row(self._current, "PASS" if ok else "FAIL", name, detail)
        return ok

    def soft(self, name: str, detail: str = "", changed: bool = False) -> None:
        self._row(self._current, "WARN" if changed else "PASS",
                  name, detail if detail else "（软记录）")

    def set_current(self, title: str) -> None:
        self._current = title


def _fmt_val(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)[:220]
    return str(value)[:220]


# ══════════════════════════ 工具 ══════════════════════════
def zip_xml_texts(path: Path, prefix: str) -> list[str]:
    """读 zip 中 prefix 下的 xml 部件文本（用于成品原文残留复检）。"""
    out = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not (name.startswith(prefix) and name.endswith(".xml")):
                    continue
                raw = z.read(name)
                try:
                    from lxml import etree
                    root = etree.fromstring(raw)
                    out.append("\n".join(t for t in root.itertext() if t))
                except Exception:
                    out.append(raw.decode("utf-8", "replace"))
    except Exception:
        pass
    return out


def _py_compiles(rel: str) -> tuple[bool, str]:
    p = qa.REPO / rel
    if not p.is_file():
        return False, "文件不存在"
    try:
        src = p.read_text("utf-8")
        compile(src, str(p), "exec")
        return True, "语法 OK"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _entity_values(entities) -> list[str]:
    return [e.value for e in entities
            if getattr(e, "value", "") and len(e.value.strip()) >= 2
            and getattr(e, "status", "") != "rejected"]


# ══════════════════════════ 0. 基线自检 ══════════════════════════
def check_baseline(gate: Gate, base: dict) -> None:
    gate.set_current("基线自检")
    gate.group("基线自检")
    gate.hard("基线 schema", base.get("schema") == "sanitized-data-baseline/v1",
              str(base.get("schema")))
    meta = base.get("meta") or {}
    gate.hard("基线 git HEAD 可用", bool(meta.get("git_head")),
              f"{meta.get('git_head_short')} @ {meta.get('captured_at')}")
    req = ((base.get("required") or {}).get("samples") or {})
    gate.hard("基线包含 5 个样例要求", len(req) >= 5, f"{len(req)} 个")
    missing = [n for n in req if not (qa.SAMPLES / n).is_file()]
    gate.hard("基线样例文件都在", not missing, f"缺少 {missing}" if missing else "5/5")
    head_now = qa.run_cmd(["git", "-C", str(qa.REPO), "rev-parse", "HEAD"])["out"]
    if head_now and head_now != meta.get("git_head"):
        gate.soft("git HEAD 已变化", f"{meta.get('git_head_short')} → {head_now[:7]}"
                  "（提交属正常，不判回归）", changed=True)
    else:
        gate.soft("git HEAD 与基线一致", meta.get("git_head_short", ""))
    dirty = qa.run_cmd(["git", "-C", str(qa.REPO), "status", "--porcelain", "--",
                        "app/server", "samples"])["out"]
    if dirty:
        dirty_files = [ln[3:] for ln in dirty.splitlines()][:6]
        gate.soft("引擎/样例有未提交改动",
                  f"{dirty_files}（开发进行中；此时失败可能是暂态，不要当最终结论）",
                  changed=True)
    else:
        gate.soft("引擎/样例工作树状态", "与基线一致（干净）")
    # 样例哈希漂移：样例被改过时必须重录基线并记录理由
    req = ((base.get("required") or {}).get("samples") or {})
    drifted = []
    for name, r in req.items():
        want = r.get("sha256") or ((base.get("samples") or {}).get("files", {})
                                   .get(name) or {}).get("sha256")
        p = qa.SAMPLES / name
        if want and p.is_file() and qa.sha256_file(p) != want:
            drifted.append(name)
    if drifted:
        gate.soft("样例文件已变化（需 --update-baseline 并记录理由）",
                  _fmt_val(drifted), changed=True)
    else:
        gate.soft("样例文件哈希", "与基线一致" if any(r.get("sha256") for r in req.values())
                  else "旧基线未存哈希（下次重录后生效）")


# ══════════════════════════ 1. e2e ══════════════════════════
def check_e2e(gate: Gate, base: dict) -> dict:
    gate.set_current("功能红线 · e2e")
    gate.group("功能红线 · e2e")
    e2e = qa.capture_e2e()
    base_e2e = base.get("e2e") or {}
    ok = gate.hard("e2e 退出码 0", e2e["exit_code"] == 0,
                   f"exit={e2e['exit_code']} {e2e['elapsed_s']}s "
                   f"{e2e.get('summary_lines')}")
    gate.hard("e2e 汇总 5/5 全部通过",
              any("5/5" in s for s in e2e.get("summary_lines", [])),
              _fmt_val(e2e.get("summary_lines")))
    gate.hard("e2e 无失败导出行",
              not any("未通过" in s for s in e2e.get("summary_lines", [])),
              "汇总未见未通过")
    b_elapsed = float(base_e2e.get("elapsed_s") or 0)
    if b_elapsed and e2e["elapsed_s"] > b_elapsed * 1.5:
        gate.soft("e2e 耗时报警", f"{b_elapsed}s → {e2e['elapsed_s']}s（>50%，"
                  "red-lines F12 软记录）", changed=True)
    else:
        gate.soft("e2e 耗时", f"{e2e['elapsed_s']}s（基线 {b_elapsed}s）")
    if not ok:
        print(f"    {DIM}e2e stdout tail: {_fmt_val(e2e.get('stdout_tail'))}{RESET}")
    return e2e


# ══════════════════════════ 2. 样例功能红线 ══════════════════════════
def check_samples(gate: Gate, base: dict) -> dict:
    gate.set_current("功能红线 · 样例解析/导出/复检")
    gate.group("功能红线 · 样例解析/导出/复检")
    samples, runtime = qa.capture_samples()
    base_samples = base.get("samples") or {}
    reqs = ((base.get("required") or {}).get("samples") or {})
    base_files = base_samples.get("files") or {}
    cur_files = samples.get("files") or {}
    base_names = list(reqs) or list(base_files)
    missing = [n for n in base_names if n not in cur_files]
    bad_parse = [n for n in base_names if not (cur_files.get(n) or {}).get("ok")]
    gate.hard("基线样例全部解析/导出成功", not missing and not bad_parse,
              f"缺失 {missing} 失败 {bad_parse}" if (missing or bad_parse)
              else f"{len(base_names)}/{len(base_names)}")
    gate.soft("样例目录文件数", f"{len(cur_files)} 个（基线 {len(base_names)} 个）",
              changed=len(cur_files) != len(base_names))
    gate.hard("插件加载无错误", not samples.get("plugin_load_errors"),
              _fmt_val(samples.get("plugin_load_errors")))
    gate.hard("OCR 可用（基线要求）",
              bool((samples.get("ocr") or {}).get("available"))
              or bool((base_samples.get("ocr") or {}).get("available")) is False,
              _fmt_val(samples.get("ocr")))

    for name in base_names:
        req = reqs.get(name) or {}
        rec = cur_files.get(name) or {}
        brec = base_files.get(name) or {}
        fmt = req.get("fmt") or brec.get("fmt") or "?"
        # 解析
        if not rec.get("ok"):
            gate.hard(f"{name} 解析/导出成功", False,
                      rec.get("err") or "解析失败")
            continue
        # 页数（文件固有，回退算破坏）
        base_pages = int(req.get("baseline_pages") or brec.get("pages") or 0)
        gate.hard(f"{name} 页数不减少", int(rec.get("pages") or 0) >= base_pages,
                  f"{rec.get('pages')}p（基线 {base_pages}p）")
        # 关键实体"至少"仍在
        tol = int(req.get("tolerance") or 0)
        required = req.get("required_types") or {}
        got = rec.get("types") or {}
        missing = []
        for label, need in required.items():
            have = int(got.get(label) or 0)
            floor = max(1, int(need))
            if have < floor:
                missing.append(f"{label} {have}<{floor}")
        gate.hard(f"{name} 关键实体不减少", not missing,
                  "、".join(missing) if missing else
                  f"{len(required)} 类达标（容差±{tol}）")
        # 双导出
        exps = rec.get("exports") or {}
        for mode, label in (("ai", "换名"), ("human", "涂黑")):
            ex = exps.get(mode) or {}
            gate.hard(f"{name} {label}导出成功", bool(ex.get("ok")),
                      ex.get("err") or f"{ex.get('size')}B residual={ex.get('residual')}")
            gate.hard(f"{name} {label}残留=0", ex.get("residual") == 0,
                      f"residual={ex.get('residual')}")
            p = runtime["exports"].get((name, mode))
            exists = bool(p and Path(p).is_file() and Path(p).stat().st_size > 0)
            gate.hard(f"{name} {label}成品存在非空", exists,
                      f"{Path(p).name if p else '?'} "
                      f"{(Path(p).stat().st_size if exists else 0)}B")
            if exists:
                _artifact_residual(gate, name, fmt, mode, Path(p), runtime)
        # 元数据清理（硬）
        r_source_frames = int((rec.get("source_meta") or {}).get("frames")
                              or rec.get("pages") or 1)
        for mode in ("ai", "human"):
            mc = ((rec.get("exports") or {}).get(mode) or {}).get("meta_check") or {}
            ident = mc.get("identity_cleared")
            gate.hard(f"{name} {mode} 元数据已清理", ident is True,
                      _meta_detail(fmt, mc))
            if fmt == "docx":
                gate.hard(f"{name} {mode} 文档缩略图移除",
                          mc.get("thumbnail_removed") is True,
                          _fmt_val(mc.get("thumbnail_removed")))
            if fmt == "tif" or fmt == "tiff":
                out_geo = (mc.get("out") or {})
                real = out_geo.get("real_geo_residual") or []
                local = bool(out_geo.get("local_crs"))
                gate.hard(f"{name} {mode} 无真实地理参考残留（LOCAL_CS 允许）",
                          real == [],
                          f"real={real} local_crs={local} "
                          f"raw={out_geo.get('raw_geo_tags')}")
                gate.soft(f"{name} {mode} TIF 中性化方式",
                          "LOCAL_CS 本地任意 CRS（D3 批准，可 GIS 打开）"
                          if local else "物理 0 Geo tag（strip 模式）")
                src_frames = int((r_source_frames or 1))
                out_frames = int(out_geo.get("frames") or 0)
                if src_frames > 1:
                    gate.hard(f"{name} {mode} 多帧不丢失",
                              out_frames >= src_frames,
                              f"frames {out_frames}（源 {src_frames}）")
        # 额外命中（软）
        extra = {k: v for k, v in got.items()
                 if k not in (brec.get("types") or {})
                 or v > int((brec.get("types") or {}).get(k, 0))}
        if extra:
            gate.soft(f"{name} 新增/多识别实体", f"{extra}（允许，不算回归）")

    base_exports_need = len(base_names) * 2
    ok_exports = sum(1 for n in base_names for e in
                     (cur_files.get(n) or {}).get("exports", {}).values()
                     if e.get("ok"))
    gate.hard(f"基线样例导出全部通过（{base_exports_need}/{base_exports_need}）",
              ok_exports == base_exports_need,
              f"{ok_exports}/{base_exports_need}")
    base_residual = sum(int(e.get("residual") or 0) for n in base_names
                        for e in (cur_files.get(n) or {}).get("exports", {}).values())
    gate.hard("样例残留合计=0", base_residual == 0,
              f"residual_total={base_residual}")
    # 样例目录新增文件也过一遍（只提示，不影响基线比对）
    for name in cur_files:
        if name not in base_names:
            gate.soft(f"新增样例 {name}",
                      "解析/导出 "
                      + ("正常" if (cur_files[name] or {}).get("ok") else
                         f"失败：{(cur_files[name] or {}).get('err')}"),
                      changed=True)
    return {"samples": samples, "runtime": runtime}


def _meta_detail(fmt: str, mc: dict) -> str:
    if not isinstance(mc, dict) or "error" in mc:
        return _fmt_val(mc)
    if fmt == "pdf":
        return (f"metadata={list((mc.get('out') or {}).get('nonempty') or {})} "
                f"xmp={ (mc.get('out') or {}).get('xmp_len')}")
    if fmt == "docx":
        out = mc.get("out") or {}
        return (f"fields={ {k: v for k, v in (out.get('fields') or {}).items() if v} } "
                f"源值残留={out.get('forbidden_hits')}")
    if fmt == "xlsx":
        out = mc.get("out") or {}
        return (f"props={list((out.get('nonempty_fields') or {}).keys())} "
                f"源值残留={out.get('forbidden_hits')}")
    if fmt == "image":
        return _fmt_val(mc.get("out"))
    if fmt == "tiff":
        out = mc.get("out") or {}
        return (f"真实地理参考残留={out.get('real_geo_residual')}，"
                f"local_crs={out.get('local_crs')}，"
                f"raw={out.get('raw_geo_tags')}，sidecars={mc.get('sidecars')}")
    return _fmt_val(mc)


def _artifact_residual(gate: Gate, name: str, fmt: str, mode: str,
                       path: Path, runtime: dict) -> None:
    """成品原文残留的独立复核（不只依赖 pipeline.verify）。"""
    entities = runtime["entities"].get(name) or []
    values = _entity_values(entities)
    if not values:
        return
    texts: list[str] = []
    if fmt == "pdf":
        texts = [qa.pdf_meta(path).get("text") or ""]
    elif fmt == "docx":
        texts = zip_xml_texts(path, "word/")
    elif fmt == "xlsx":
        texts = zip_xml_texts(path, "xl/")
    if not texts:
        return                      # 图片/TIFF 由 verify 的 OCR 复检负责
    blob = "\n".join(texts)
    left = [v for v in values if v in blob]
    label = "换名" if mode == "ai" else "涂黑"
    gate.hard(f"{name} {label}成品无原文残留（独立复核）", not left,
              f"残留 {left[:3]}" if left else f"checked {len(values)} 个实体值")


# ══════════════════════════ 3. 安全红线 ══════════════════════════
def check_security(gate: Gate, base: dict) -> dict:
    gate.set_current("安全红线 · 静态")
    gate.group("安全红线 · 静态")
    sec = qa.capture_security_scan()
    gate.hard("引擎无出站网络（requests/urllib.request/http.client/socket 外呼）",
              sec["outbound_ok"], _fmt_val(sec["outbound_hits"][:3]))
    gate.hard("无调用上下文的外部 URL", sec["external_urls_ok"],
              _fmt_val(sec["external_urls_called"][:3]))
    gate.soft("外部 URL 字面量（仅说明）",
              _fmt_val([(u["url"], u["context"]) for u in sec["external_url_literals"]]))
    gate.hard("审计源码只记类型数量", sec["audit_ok"],
              f"列={sec['audit_insert_columns']} 疑似原值={sec['audit_value_like_columns']}")
    gate.hard("默认监听地址=127.0.0.1",
              sec["listen_host_config"] == "127.0.0.1",
              f"HOST={sec['listen_host_config']!r}")
    gate.hard("requirements 无网络客户端依赖（离线红线）",
              sec.get("requirements_offline_ok", True),
              f"可疑依赖={sec.get('network_deps')}" if not sec.get(
                  "requirements_offline_ok", True) else
              _fmt_val(sec.get("requirements_packages")))
    gate.soft("依赖清单（人工复核用）",
              _fmt_val(sec.get("requirements_packages")))
    gate.soft("可选重依赖（TIF P0：rasterio/pyproj/tifffile 等，只记录）",
              _fmt_val(sec.get("optional_deps")))
    # 启动参数默认值
    app_src = (qa.SERVER / "yinzhan" / "app.py").read_text("utf-8")
    gate.hard("--host 默认取 HOST（不默认 0.0.0.0）",
              'ap.add_argument("--host", default=HOST' in app_src,
              "app.py 中 --host default=HOST")
    # 破坏性重写：静态证据
    pdf_src = (qa.SERVER / "yinzhan" / "parsers" / "pdf_parser.py").read_text("utf-8")
    gate.hard("PDF 导出使用 apply_redactions（真删除）",
              "apply_redactions" in pdf_src, "pdf_parser.py")
    img_src = (qa.SERVER / "yinzhan" / "parsers" / "image_parser.py").read_text("utf-8")
    m = re.search(r"def _save_image\(.*?\n(?=\ndef |\nclass )", img_src, re.S)
    save_body = m.group(0) if m else ""
    save_code = re.sub(r'""".*?"""', "", save_body, flags=re.S)   # 去掉 docstring
    gate.hard("图片导出重编码且不携带 EXIF/ICC",
              bool(save_code) and ".save(" in save_code
              and not re.search(r"\b(?:exif|icc_profile|xmp)\s*=", save_code),
              "image_parser._save_image 保存时未传 exif/icc" if save_code
              else "未找到 _save_image（解析器被改？）")
    tif_src = (qa.SERVER / "yinzhan" / "parsers" / "tiff_parser.py").read_text("utf-8")
    gate.hard("TIFF 导出剥离地理标签并复检",
              "GEO_TAG_NAMES" in tif_src and "_verify_geo_stripped" in tif_src,
              "tiff_parser.py")
    docx_src = (qa.SERVER / "yinzhan" / "parsers" / "docx_parser.py").read_text("utf-8")
    gate.hard("DOCX 导出清空 core properties",
              "core_properties" in docx_src and "setattr(properties" in docx_src,
              "docx_parser.py")
    xlsx_src = (qa.SERVER / "yinzhan" / "parsers" / "xlsx_parser.py").read_text("utf-8")
    gate.hard("XLSX 导出清空文档属性",
              "properties" in xlsx_src and "_PROP_FIELDS_CLEAR" in xlsx_src,
              "xlsx_parser.py")
    # 图片 EXIF 剥离行为测试（样例无 EXIF，用临时夹具真正跑一遍）
    exif_ok, exif_detail = _exif_fixture_test()
    gate.hard("图片导出剥离 EXIF/文本元数据（夹具实测）", exif_ok, exif_detail)
    # 审计源码无 value 列
    audit_src = (qa.SERVER / "yinzhan" / "audit.py").read_text("utf-8")
    gate.hard("审计写库无原值字段",
              not re.search(r"INSERT INTO audit\([^)]*\bvalue\b", audit_src),
              "audit.py INSERT 列检查")
    return sec


def _exif_fixture_test() -> tuple[bool, str]:
    """临时造一张带 EXIF/文本元数据的图片，走 image 解析→导出，验证元数据被剥离。"""
    try:
        from PIL import Image, PngImagePlugin
        from yinzhan import pipeline, registry
        from yinzhan.udm import Decision, Entity, ParseContext, Span

        work = qa.WORK / "fixtures"
        work.mkdir(parents=True, exist_ok=True)
        png = work / "meta_fixture.png"
        info = PngImagePlugin.PngInfo()
        info.add_text("Author", "QA-Meta-Author")
        info.add_text("Comment", "QA-Meta-Comment")
        Image.new("RGB", (320, 200), (240, 240, 240)).save(
            str(png), pnginfo=info)
        jpg = work / "exif_fixture.jpg"
        exif = Image.Exif()
        exif[271] = "QA-Camera-Make"
        exif[272] = "QA-Camera-Model"
        exif[306] = "2025:01:02 03:04:05"
        img = Image.new("RGB", (320, 200), (240, 240, 240))
        img.save(str(jpg), exif=exif.tobytes(), quality=90)

        parser = registry.find_parser(png)
        ctx = ParseContext(ocr=qa_engine_ocr(), workdir=str(qa.WORK / "work"))
        problems = []
        for src in (png, jpg):
            doc = parser.parse(src, ctx)
            ent = Entity(id=Entity.new_id(), etype="OTHER", label="测试遮盖",
                         value="", spans=[Span(rect=(0.2, 0.2, 0.8, 0.8), page=0)],
                         level="L2", confidence=1.0, status="accepted",
                         decision=Decision(strategy="black", black=True))
            out = work / (src.stem + "_out" + src.suffix)
            res = pipeline.export(doc, parser, [ent], out, {"mode": "ai"})
            meta = qa.image_meta(out)
            if meta["info_keys"]:
                problems.append(f"{src.name} info={meta['info_keys']}")
            if meta["exif_entries"]:
                problems.append(f"{src.name} exif={meta['exif_entries']}")
            if not (out.is_file() and out.stat().st_size > 0):
                problems.append(f"{src.name} 成品为空")
        return (not problems), ("；".join(problems) if problems
                                else "EXIF/PNG 文本元数据均被剥离")
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def qa_engine_ocr():
    from yinzhan.ocr import get_ocr
    return get_ocr()


# ══════════════════════════ 4. 契约完整性 ══════════════════════════
def check_contract(gate: Gate, base: dict) -> dict:
    gate.set_current("契约完整性 · 规则/注册表")
    gate.group("契约完整性 · 规则/注册表")
    cur = qa.capture_rules()
    brules = base.get("rules") or {}
    base_packs = brules.get("packs") or {}
    # 包完整性
    missing_packs = [pid for pid in base_packs if pid not in cur["packs"]]
    gate.hard("5 个规则包仍在", not missing_packs,
              f"缺少 {missing_packs}" if missing_packs else
              f"{sorted(cur['packs'])}")
    gate.hard("规则包加载无错误", not cur["load_errors"],
              _fmt_val(cur["load_errors"][:3]))
    gate.hard("规则定义合法（id 唯一/必填/scope/策略）", not cur["violations"],
              _fmt_val(cur["violations"][:3]))
    reduced = []
    allowed_moves = []
    all_cur_ids = {rid for cp in cur["packs"].values()
                   for rid in (cp.get("rule_ids") or [])}
    for pid, bp in base_packs.items():
        cp = cur["packs"].get(pid)
        if not cp:
            continue
        lost_ids = [rid for rid in (bp.get("rule_ids") or [])
                    if rid not in set(cp.get("rule_ids") or [])]
        allowed_here = [rid for rid in lost_ids if rid in RULE_REMOVAL_ALLOWLIST]
        allowed_moves.extend(allowed_here)
        left = [rid for rid in lost_ids if rid not in RULE_REMOVAL_ALLOWLIST]
        if left:
            reduced.append(f"{pid} 少规则 {left[:3]}")
        expect = int(bp.get("own_count", 0)) - len(allowed_here)
        if cp.get("own_count", 0) < expect:
            reduced.append(f"{pid} {cp.get('own_count')}<{expect}")
    # 白名单的替代规则必须真实存在（防止"去重"后能力凭空消失）
    missing_repl = []
    for rid in allowed_moves:
        repl = (RULE_REMOVAL_ALLOWLIST.get(rid) or {}).get("replaced_by")
        if repl and repl not in all_cur_ids:
            missing_repl.append(f"{rid}→{repl}")
    gate.hard("规则迁移/去重的替代规则存在", not missing_repl,
              f"缺少替代规则 {missing_repl}" if missing_repl else
              "、".join(f"{rid}→{RULE_REMOVAL_ALLOWLIST[rid].get('replaced_by')}"
                       for rid in allowed_moves) or "无迁移")
    if allowed_moves:
        gate.soft("已批准的规则迁移/去重（白名单）",
                  "；".join(f"{rid}（{RULE_REMOVAL_ALLOWLIST[rid]['reason']}）"
                           for rid in allowed_moves))
    gate.hard("已有规则不减少（新增不算回归；迁移需登记）", not reduced,
              "、".join(reduced) if reduced else
              f"自有 {cur['defined_rules_total']} 条（基线 "
              f"{brules.get('defined_rules_total')}）")
    reduced_fmt = []
    for fmt, bcount in (brules.get("compiled_by_fmt") or {}).items():
        try:
            bcount = int(bcount)
        except (TypeError, ValueError):
            continue
        ccount = int(cur["compiled_by_fmt"].get(fmt) or 0)
        if ccount < bcount:
            reduced_fmt.append(f"{fmt} {ccount}<{bcount}")
    gate.hard("各格式编译规则数不减少", not reduced_fmt,
              "、".join(reduced_fmt) if reduced_fmt else
              _fmt_val(cur["compiled_by_fmt"]))
    extra_rules = cur["defined_rules_total"] - int(brules.get("defined_rules_total") or 0)
    if extra_rules > 0:
        gate.soft("新增规则", f"+{extra_rules} 条（允许，不算回归）", changed=False)

    # ── P0 二波规则契约（PIPL 收口，2026-10-20）──
    from yinzhan import rules as _rules_mod
    packs_cur = _rules_mod.load_packs()
    id_scope: dict[str, list[str]] = {}
    for pid, rs in packs_cur.items():
        for r in rs:
            if r.pack == pid:
                id_scope[r.id] = list(r.scope)
    missing_scope = [rid for rid in SCOPE_EXPANDED_RULE_IDS
                     if rid not in id_scope
                     or "image" not in id_scope[rid] or "tiff" not in id_scope[rid]]
    gate.hard("10 条规则 scope 已扩到 image+tiff", not missing_scope,
              f"缺 {missing_scope}" if missing_scope
              else f"{len(SCOPE_EXPANDED_RULE_IDS)} 条全部含 image,tiff")
    bad_packs = []
    for pid, min_own in PACK_MIN_OWN_RULES.items():
        cp = cur["packs"].get(pid)
        if not cp:
            bad_packs.append(f"{pid} 缺失")
            continue
        if int(cp.get("own_count") or 0) < min_own:
            bad_packs.append(f"{pid} 自有 {cp.get('own_count')}<{min_own}")
        prefix = PACK_ID_PREFIX.get(pid, "")
        own_ids = cp.get("rule_ids") or []
        bad_ids = [rid for rid in own_ids if prefix and not rid.startswith(prefix)]
        if bad_ids:
            bad_packs.append(f"{pid} 命名空间不符 {bad_ids[:3]}")
        if len(own_ids) != len(set(own_ids)):
            bad_packs.append(f"{pid} 内部 id 重复")
    gate.hard("pipl-cn / table-cn 规则包完整且 id 唯一", not bad_packs,
              "；".join(bad_packs) if bad_packs else
              "、".join(f"{k}={cur['packs'][k]['own_count']} 条"
                       for k in PACK_MIN_OWN_RULES))
    base_compiled = brules.get("compiled_by_fmt") or {}
    # scope 必须真的生效：10 条规则要出现在 image/tiff 的编译结果里
    # （不依赖"比旧基线增加"，重录基线后依然有效）
    compiled_image = {r.id for r in _rules_mod.compile_rules(packs_cur, "image")}
    compiled_tiff = {r.id for r in _rules_mod.compile_rules(packs_cur, "tiff")}
    not_compiled = [rid for rid in SCOPE_EXPANDED_RULE_IDS
                    if rid not in compiled_image or rid not in compiled_tiff]
    gate.hard("10 条规则已编译进 image/tiff 规则集", not not_compiled,
              f"未生效 {not_compiled}" if not_compiled else
              f"image/tiff 编译数 "
              f"{len(compiled_image)}/{len(compiled_tiff)}"
              f"（基线 {base_compiled.get('image')}/"
              f"{base_compiled.get('tiff')}）")

    # 注册表
    reg = qa.capture_registry()
    breg = base.get("registry") or {}
    gate.hard("插件加载无错误", not reg["plugin_load_errors"],
              _fmt_val(reg["plugin_load_errors"][:3]))
    missed_p = [p for p in (breg.get("parsers") or []) if p not in reg["parsers"]]
    missed_d = [d for d in (breg.get("detectors") or []) if d not in reg["detectors"]]
    gate.hard("解析器不回退", not missed_p, f"缺少 {missed_p}" if missed_p
              else _fmt_val(reg["parsers"]))
    gate.hard("检测器不回退", not missed_d, f"缺少 {missed_d}" if missed_d
              else _fmt_val(reg["detectors"]))
    gate.hard("替换器已注册", all(reg["replacers"].values()),
              _fmt_val(reg["replacers"]))
    base_ocr = (breg.get("ocr") or {}).get("available")
    gate.hard("OCR 可用性与基线一致", bool(reg["ocr"]["available"]) == bool(base_ocr),
              _fmt_val(reg["ocr"]))
    return {"rules": cur, "registry": reg}


# ══════════════════════════ 5. 服务与主路径 ══════════════════════════
def check_http(gate: Gate, base: dict) -> dict:
    gate.set_current("契约完整性 · 服务/静态站点/主路径")
    gate.group("契约完整性 · 服务/静态站点/主路径")
    http = qa.capture_http()
    bhttp = base.get("http") or {}
    bhealth = base.get("health") or {}
    if not http.get("started"):
        gate.hard("服务启动", False,
                  _fmt_val(http.get("error") or "90s 未就绪")
                  + ("；runtime=" + _fmt_val(http.get("runtime"))
                     if http.get("runtime") else ""))
        return {"http": http, "ok": False}
    gate.hard("服务启动并 /api/health 200", True,
              f"port={http.get('port')} startup={http.get('startup_s')}s")
    health = http.get("health") or {}
    gate.hard("health.ok=true", health.get("ok") is True, _fmt_val(health.get("ok")))
    gate.hard("health.net=blocked", health.get("net") == "blocked",
              _fmt_val(health.get("net")))
    gate.hard("health 报告解析器不回退",
              set(bhealth.get("engine", {}).get("parsers") or [])
              <= set(health.get("parsers") or []),
              _fmt_val(health.get("parsers")))
    base_packs = {p.get("id") for p in
                  (bhealth.get("engine", {}).get("packs") or []) if p.get("id")}
    cur_packs = {p.get("id") for p in (health.get("packs") or []) if p.get("id")}
    gate.hard("health 报告规则包不缺失", base_packs <= cur_packs,
              f"缺少 {sorted(base_packs - cur_packs)}" if base_packs - cur_packs
              else f"{sorted(cur_packs)}")
    runtime = http.get("runtime") or {}
    gate.hard("runtime.json host=127.0.0.1", runtime.get("host") == "127.0.0.1",
              _fmt_val(runtime))
    ns = http.get("netstat") or []
    loop_lines = [ln for ln in ns if "127.0.0.1" in ln]
    gate.hard("监听地址只有回环（netstat 证据）",
              bool(loop_lines) and all("0.0.0.0:0" in ln for ln in ns),
              _fmt_val(ns[:3]) if ns else "netstat 无输出（软化为 runtime 证据）")
    # 规则包 API
    packs_api = http.get("packs_api") or {}
    gate.hard("/api/packs 正常", packs_api.get("status") == 200
              and len(packs_api.get("packs") or []) >= len(base_packs),
              f"status={packs_api.get('status')} "
              f"packs={len(packs_api.get('packs') or [])}")
    # 静态站点
    static = http.get("static") or {}
    bad = [p for p, v in static.items()
           if v.get("status") != 200 or not v.get("mime_ok")]
    gate.hard("4 页面 + 3 图标 200 且 MIME 正确", not bad and len(static) == 7,
              f"异常 {bad}" if bad else "/".join(static))
    thin = [p for p, v in static.items()
            if v.get("bytes", 0) < (200 if p.startswith("/apple") or
                                    p.startswith("/favicon") else 500)]
    gate.hard("页面/图标内容非空", not thin,
              f"内容过短 {thin}" if thin else
              "/".join(f"{p}:{v.get('bytes')}B" for p, v in static.items()))
    # CSRF
    csrf = http.get("csrf") or {}
    gate.hard("跨站写请求被拒（403）", csrf.get("blocked") is True,
              _fmt_val(csrf))
    # 主路径
    mp = http.get("main_path") or {}
    gate.hard("主路径 stage=ready",
              (mp.get("job_snapshot") or {}).get("stage") == "ready",
              _fmt_val((mp.get("job_snapshot") or {}).get("stage")))
    gate.hard("主路径流程通过", mp.get("ok") is True, _fmt_val(mp.get("ok")))
    for mode, label in (("ai", "换名"), ("human", "涂黑")):
        ex = (mp.get("exports") or {}).get(mode) or {}
        gate.hard(f"主路径 {label}导出 residual=0",
                  (ex.get("verify") or {}).get("residual") == 0,
                  _fmt_val(ex.get("verify")))
        dl = ex.get("download") or {}
        gate.hard(f"主路径 {label}下载非空且 %PDF",
                  dl.get("pdf_magic_ok") is True and dl.get("nonempty") is True,
                  f"{dl.get('bytes')}B magic={dl.get('magic')}")
        gate.hard(f"主路径 {label}下载与导出校验一致",
                  dl.get("match_export_sha") is True
                  and dl.get("match_export_bytes") is True,
                  f"sha一致={dl.get('match_export_sha')} "
                  f"size一致={dl.get('match_export_bytes')}")
    audit = mp.get("audit") or {}
    gate.hard("审计记录存在且计数含类型", (audit.get("records") or 0) >= 2
              and audit.get("counts_has_by_type") is True,
              f"records={audit.get('records')} keys={audit.get('record_keys')}")
    gate.hard("审计记录无原值字段", not (audit.get("value_fields_present") or []),
              _fmt_val(audit.get("value_fields_present")))
    gate.hard("审计链校验通过", (mp.get("audit_verify") or {}).get("ok") is True,
              _fmt_val(mp.get("audit_verify")))
    # 预览/渲染/框选（F8）
    pv = mp.get("preview") or {}
    gate.hard("预览接口可用", pv.get("status") == 200 and (pv.get("blocks") or 0) > 0
              and (pv.get("entities") or 0) > 0,
              f"kind={pv.get('kind')} blocks={pv.get('blocks')} "
              f"entities={pv.get('entities')}")
    rd = mp.get("render") or {}
    gate.hard("渲染接口返回 PNG", rd.get("status") == 200
              and rd.get("png_magic") is True,
              f"status={rd.get('status')} ctype={rd.get('ctype')}")
    mk = mp.get("mark") or {}
    gate.hard("框选补漏（rect 归一化）可用", mk.get("added") == 1
              and mk.get("accept_status") == 200 and mk.get("status_after") == "accepted",
              _fmt_val(mk))
    gate.hard("accept-all 可用", (mp.get("accept_all") or {}).get("status") == 200,
              _fmt_val(mp.get("accept_all")))
    # 整图涂黑（前端「整图涂黑」按钮的后端契约）：rect=[0,0,1,1] → black 实体铺满页面
    mf = mp.get("mark_full") or {}
    gate.hard("整图涂黑（rect=[0,0,1,1] 生成 black 实体）",
              mf.get("added") == 1 and mf.get("has_entity") is True
              and mf.get("black") is True and float(mf.get("coverage") or 0) >= 0.95,
              _fmt_val(mf))
    # PDF 隐藏数据剥离：主路径成品 verify 必须报告 pdf_hidden 为空
    hidden_bad = []
    for mode in ("ai", "human"):
        ex = (mp.get("exports") or {}).get(mode) or {}
        ph = (ex.get("verify") or {}).get("pdf_hidden")
        if ph:
            hidden_bad.append(f"{mode}:{ph}")
    gate.hard("主路径 PDF 隐藏数据已剥离（verify pdf_hidden 为空）",
              not hidden_bad, _fmt_val(hidden_bad) if hidden_bad else "pdf_hidden={}")
    gate.hard("主路径有实体命中",
              (mp.get("job_entities") or {}).get("total", 0) > 0,
              _fmt_val((mp.get("job_entities") or {}).get("types")))
    # 主路径（jobs 级：含表单人名/扩散/视觉补充）关键类型不回退
    b_types = ((((bhttp.get("main_path") or {}).get("job_entities") or {}).get("types"))
               or {})
    c_types = ((mp.get("job_entities") or {}).get("types") or {})
    lost = [k for k, v in b_types.items() if int(c_types.get(k) or 0) < int(v)]
    gate.hard("主路径关键实体类型不回退", not lost,
              f"少了 {[(k, c_types.get(k), b_types[k]) for k in lost]}" if lost
              else f"{len(c_types)} 类（基线 {len(b_types)} 类）")
    # 映射库/还原（F5）
    jid = ((mp.get("job_create") or {}).get("job_id")) or ""
    reh = qa.check_rehydrate(jid) if jid else {"ok": False, "err": "无 job_id"}
    gate.hard("AI 映射库可解密并还原", reh.get("ok") is True, _fmt_val(reh))
    cli = qa.rehydrate_cli_test(jid) if jid else {"ok": False, "err": "无 job_id"}
    gate.hard("rehydrate.py 命令行还原链路", cli.get("ok") is True, _fmt_val(cli))
    return {"http": http, "rehydrate": reh, "rehydrate_cli": cli, "ok": True}


# ══════════════════════════ 5.5 P0 二波新能力（PDF 隐藏数据 / EXIF 朝向 / 表格数值） ══════════════════════════
def check_new_capabilities(gate: Gate) -> None:
    """P0 二波（PIPL 收口）新增能力的硬断言：用虚构夹具真实跑通导出/复检链路。"""
    gate.set_current("功能红线 · P0 二波新能力")
    gate.group("功能红线 · P0 二波新能力")
    # 1) PDF 隐藏数据：附件/批注/表单/JS 默认剥离 + verify 拦截未清理
    pdf_hidden = qa.pdf_hidden_fixture_test()
    gate.hard("PDF 隐藏数据默认剥离（附件/批注/表单/JS）",
              pdf_hidden.get("ok") is True, _fmt_val(pdf_hidden))
    # 2) 图片 EXIF Orientation=6：解析/渲染/导出转正且导出无 EXIF
    orient = qa.exif_orientation_fixture_test()
    gate.hard("图片 EXIF Orientation 转正（解析/渲染/导出）",
              orient.get("ok") is True, _fmt_val(orient))
    # 3) 表格：表头列级覆盖 + 纯数值金额导出写回数字
    table = qa.table_numeric_fixture_test()
    gate.hard("表格列级覆盖 + 数值金额写回数字（openpyxl 断言）",
              table.get("ok") is True, _fmt_val(table))
    # 已知不修的边界只做软记录（不判回归，也不为它们改引擎）
    limits = qa.capture_known_limits()
    gaps = limits.get("known_gaps_not_fixed") or {}
    for key, text in gaps.items():
        gate.soft(f"已知不修：{key}", text)


# ══════════════════════════ 6. MCP 工具（F9） ══════════════════════════
def check_mcp(gate: Gate) -> None:
    gate.set_current("契约完整性 · MCP 工具")
    gate.group("契约完整性 · MCP 工具")
    try:
        import mcp_server  # noqa: F401
        gate.hard("MCP 模块可导入", True, "mcp_server.py")
    except Exception as exc:  # noqa: BLE001
        gate.hard("MCP 模块可导入", False, f"{type(exc).__name__}: {exc}")
        return
    try:
        tool_names = [t.get("name") for t in getattr(mcp_server, "TOOLS", [])]
        need = {"sanitize_document", "redact_text", "check_support", "list_rules"}
        gate.hard("MCP 四个工具齐全", need <= set(tool_names), _fmt_val(tool_names))
    except Exception as exc:  # noqa: BLE001
        gate.hard("MCP 四个工具齐全", False, f"{type(exc).__name__}: {exc}")

    # stdio 协议端到端：initialize → tools/list → 3×tools/call，stdout 只能有 JSON-RPC
    stdio = qa.mcp_stdio_test()
    gate.hard("MCP stdio 协议端到端（含 redact_text 不泄露原值）",
              stdio.get("ok") is True, _fmt_val(stdio))

    def result_json(resp: dict) -> dict:
        try:
            return json.loads(resp["content"][0]["text"])
        except Exception:
            return {}

    # list_rules
    try:
        r = result_json(mcp_server.tool_list_rules({}))
        gate.hard("MCP list_rules 可用",
                  bool(r.get("packs")) and len(r.get("packs") or []) >= 1,
                  f"{len(r.get('packs') or [])} 个包")
    except Exception as exc:  # noqa: BLE001
        gate.hard("MCP list_rules 可用", False, f"{type(exc).__name__}: {exc}")
    # check_support
    try:
        r = result_json(mcp_server.tool_check_support(
            {"path": str(qa.SAMPLES / "委托代理合同.pdf")}))
        gate.hard("MCP check_support 可用",
                  r.get("supported") is True and r.get("format") == "pdf",
                  _fmt_val(r))
    except Exception as exc:  # noqa: BLE001
        gate.hard("MCP check_support 可用", False, f"{type(exc).__name__}: {exc}")
    # redact_text（合成值，不含真实数据）
    try:
        secret_id = "310104198703124510"
        secret_phone = "13817264451"
        text = f"联系人：张伟，身份证号码：{secret_id}，联系电话：{secret_phone}。"
        r = result_json(mcp_server.tool_redact_text({"text": text}))
        red = r.get("redacted") or ""
        ok = (r.get("entities", 0) >= 2 and red and red != text
              and secret_id not in red and secret_phone not in red)
        gate.hard("MCP redact_text 可用且不泄露原值", ok,
                  f"entities={r.get('entities')} by_type={r.get('by_type')}")
    except Exception as exc:  # noqa: BLE001
        gate.hard("MCP redact_text 可用且不泄露原值", False,
                  f"{type(exc).__name__}: {exc}")
    # sanitize_document（在临时 cwd 里跑，避免污染仓库）
    old_cwd = os.getcwd()
    try:
        cwd = qa.WORK / "mcp_cwd"
        cwd.mkdir(parents=True, exist_ok=True)
        os.chdir(cwd)
        try:
            r = result_json(mcp_server.tool_sanitize_document(
                {"path": str(qa.SAMPLES / "委托代理合同.pdf"), "mode": "ai"}))
            out = Path(r.get("output") or "")
            ok = (r.get("ok") is True and out.is_file()
                  and out.stat().st_size > 0
                  and (r.get("verify") or {}).get("residual") == 0
                  and (r.get("entities") or 0) > 0)
            gate.hard("MCP sanitize_document 可用且 residual=0", ok,
                      f"entities={r.get('entities')} verify={r.get('verify')}")
            gate.soft("MCP 路径保存映射（S6 现状）",
                      "已保存" if "save_mapping" in (
                          qa.SERVER / "mcp_server.py").read_text("utf-8")
                      else "现状未保存（已知缺口，后续修复不算回归）")
        finally:
            os.chdir(old_cwd)
    except Exception as exc:  # noqa: BLE001
        os.chdir(old_cwd)
        gate.hard("MCP sanitize_document 可用且 residual=0", False,
                  f"{type(exc).__name__}: {exc}")


# ══════════════════════════ 7. 桌面/构建入口（F11） ══════════════════════════
def check_desktop(gate: Gate) -> None:
    gate.set_current("功能红线 · 桌面/构建入口")
    gate.group("功能红线 · 桌面/构建入口")
    for rel in ("desktop/main.py", "app/server/run.py",
                "app/server/mcp_server.py", "app/server/rehydrate.py"):
        ok, detail = _py_compiles(rel)
        gate.hard(f"{rel} 语法 OK", ok, detail)
    for rel in ("desktop/app.spec", "desktop/installer.iss",
                "app/web/home.html", "app/web/index.html", "app/web/app.js",
                "app/web/style.css", "app/server/requirements.txt"):
        gate.hard(f"{rel} 存在", (qa.REPO / rel).is_file(), "")
    # 打包入口引用的资源目录仍在（防新功能改路径把桌面版弄坏）
    app_spec = (qa.REPO / "desktop" / "app.spec")
    if app_spec.is_file():
        txt = app_spec.read_text("utf-8", "replace")
        gate.hard("app.spec 仍收集 web 资源", "web" in txt, "")
    # 启动入口 import 链（不真的起服务）
    try:
        from yinzhan import app as engine_app
        gate.hard("服务入口模块可导入", callable(getattr(engine_app, "main", None)),
                  "yinzhan.app.main")
    except Exception as exc:  # noqa: BLE001
        gate.hard("服务入口模块可导入", False, f"{type(exc).__name__}: {exc}")


# ══════════════════════════ 8. 软记录 ══════════════════════════
def check_soft(gate: Gate, base: dict, e2e: dict, samples_bundle: dict) -> None:
    gate.set_current("软参考 · 性能/已知边界")
    gate.group("软参考 · 性能/已知边界（只警告，不判回归）")
    # 性能
    perf = base.get("perf_soft") or {}
    b_elapsed = float(perf.get("e2e_elapsed_s") or 0)
    if b_elapsed and e2e.get("elapsed_s", 0) > b_elapsed * 1.5:
        gate.soft("e2e 总耗时", f"{e2e['elapsed_s']}s > 基线 {b_elapsed}s ×1.5",
                  changed=True)
    else:
        gate.soft("e2e 总耗时", f"{e2e.get('elapsed_s')}s（基线 {b_elapsed}s）")
    samples = (samples_bundle or {}).get("samples") or {}
    b_parse = perf.get("sample_parse_ms") or {}
    slow = []
    for name, rec in (samples.get("files") or {}).items():
        base_ms = b_parse.get(name)
        cur_ms = rec.get("parse_ms")
        if base_ms and cur_ms and cur_ms > float(base_ms) * 1.5 + 500:
            slow.append(f"{name} {cur_ms}ms>{base_ms}ms")
    gate.soft("逐样例解析耗时", "、".join(slow) if slow else "基线 ±50% 内",
              changed=bool(slow))
    # 已知边界：只报告现状变化，不判回归
    cur = qa.capture_known_limits()
    bnd = base.get("known_limits") or {}
    for key, bval in bnd.items():
        if key.startswith("_"):
            continue
        cval = cur.get(key)
        if cval != bval:
            gate.soft(f"边界现状变化：{key}",
                      f"基线 {_fmt_val(bval)} → 现在 {_fmt_val(cval)}"
                      "（P0 修复属预期，软记录）", changed=True)
        else:
            gate.soft(f"边界现状未变：{key}", _fmt_val(cval))


# ══════════════════════════ 模式：更新基线 ══════════════════════════
def update_baseline(reason: str = "") -> int:
    print(f"{DIM}数据隐站 · 重录基线（--update-baseline）{RESET}")
    t0 = time.time()
    old = None
    if qa.BASELINE_JSON.is_file():
        try:
            old = json.loads(qa.BASELINE_JSON.read_text("utf-8"))
        except Exception:
            old = None
    print("  [1/4] 采集 e2e + 样例 + 规则 + 注册表 + 安全 + HTTP 主路径 …")
    base = qa.capture_all(with_http=True)
    meta = base["meta"]
    if not reason:
        reason = "(未填写理由)"
    meta["update_reason"] = reason
    history = list(((old or {}).get("meta") or {}).get("update_history") or [])
    entry = {
        "at": meta["captured_at"],
        "head": meta["git_head_short"],
        "engine_clean": meta["engine_clean"],
        "reason": reason,
        "samples": base["samples"]["summary"],
        "rules": base["rules"]["defined_rules_total"],
    }
    # 同一理由 + 同一 HEAD 的连续重录（例如修门禁后重跑）只保留最新一条，避免刷历史
    if history and history[-1].get("reason") == reason \
            and history[-1].get("head") == entry["head"]:
        history[-1] = entry
    else:
        history.append(entry)
    meta["update_history"] = history[-10:]
    print("  [2/4] 写入 baseline.json / SNAPSHOT.md …")
    js, md = qa.write_baseline(base)
    elapsed = round(time.time() - t0, 2)
    print(f"  [3/4] HEAD={meta['git_head_short']} "
          f"engine_clean={meta['engine_clean']}")
    print(f"        e2e={'OK' if base['e2e']['ok'] else 'FAIL'} "
          f"samples={base['samples']['summary']} "
          f"packs={len(base['rules']['packs'])}/"
          f"{base['rules']['defined_rules_total']} 条 "
          f"main_path={(base.get('http') or {}).get('main_path', {}).get('ok')}")
    if meta["engine_clean"] is False:
        print(f"  {YELLOW}注意：引擎/样例有未提交改动，本次基线记录的是当前工作树状态。{RESET}")
    if old:
        o_s = (old.get("samples") or {}).get("summary") or {}
        n_s = base["samples"]["summary"]
        o_p = (old.get("rules") or {}).get("defined_rules_total")
        n_p = base["rules"]["defined_rules_total"]
        print(f"       差异：实体总数 "
              f"{sum(r.get('entities', 0) for r in (old.get('samples') or {}).get('files', {}).values())}"
              f" → {sum(r.get('entities', 0) for r in base['samples']['files'].values())}；"
              f"规则 {o_p} → {n_p}；残留 "
              f"{o_s.get('residual_total')} → {n_s.get('residual_total')}")
        drift = []
        old_req = ((old.get("required") or {}).get("samples") or {})
        for name, r in (base.get("required", {}).get("samples") or {}).items():
            want = (old_req.get(name) or {}).get("sha256")
            if want and r.get("sha256") and want != r["sha256"]:
                drift.append(name)
        if drift:
            print(f"       样例内容变化：{drift}（理由：{reason}）")
    print(f"  [4/4] 已写入：{js}\n                {md}")
    ok = _baseline_sane(base)
    print(f"  {'基线记录完成' if ok else '基线记录异常（请检查上方采集结果）'}，"
          f"耗时 {elapsed}s")
    return 0 if ok else 2


def _baseline_sane(base: dict) -> bool:
    return bool(
        base.get("e2e", {}).get("ok")
        and base["samples"]["summary"]["files_ok"] == 5
        and base["samples"]["summary"]["exports_ok"] == 10
        and base["samples"]["summary"]["residual_total"] == 0
        and not base["rules"].get("load_errors")
        and not base["rules"].get("violations")
        and base["security"].get("outbound_ok")
        and base["security"].get("audit_ok")
        and ((base.get("http") or {}).get("main_path") or {}).get("ok")
    )


# ══════════════════════════ 主流程 ══════════════════════════
def run_checks() -> int:
    t0 = time.time()
    base = qa.load_baseline()
    gate = Gate()
    print(f"\n{'=' * 68}\n  数据隐站 · 回归门禁（red-lines）\n"
          f"  基线：{qa.BASELINE_JSON}\n"
          f"  基线 HEAD：{(base.get('meta') or {}).get('git_head_short')}  "
          f"冻结于 {(base.get('meta') or {}).get('captured_at')}\n{'=' * 68}")

    # 0 基线自检
    try:
        check_baseline(gate, base)
    except Exception:
        gate.hard("基线自检异常", False, traceback.format_exc(limit=2))

    # 1 e2e
    try:
        e2e = check_e2e(gate, base)
    except Exception:
        gate.hard("e2e 检查异常", False, traceback.format_exc(limit=2))
        e2e = {"ok": False, "elapsed_s": 0}

    # 2 样例功能红线
    bundle: dict = {}
    try:
        bundle = check_samples(gate, base)
    except Exception:
        gate.hard("样例功能检查异常", False, traceback.format_exc(limit=2))

    # 3 安全静态
    try:
        check_security(gate, base)
    except Exception:
        gate.hard("安全静态检查异常", False, traceback.format_exc(limit=2))

    # 4 契约（规则/注册表）
    contract: dict = {}
    try:
        contract = check_contract(gate, base)
    except Exception:
        gate.hard("规则/注册表检查异常", False, traceback.format_exc(limit=2))

    # 4.5 P0 二波新能力（PDF 隐藏数据 / EXIF 朝向 / 表格数值）
    try:
        check_new_capabilities(gate)
    except Exception:
        gate.hard("P0 二波新能力检查异常", False, traceback.format_exc(limit=2))

    # 5 HTTP 服务 + 主路径
    http_bundle: dict = {}
    try:
        http_bundle = check_http(gate, base)
    except Exception:
        gate.hard("HTTP 主路径检查异常", False, traceback.format_exc(limit=2))

    # 6 MCP
    try:
        check_mcp(gate)
    except Exception:
        gate.hard("MCP 检查异常", False, traceback.format_exc(limit=2))

    # 7 桌面/构建入口
    try:
        check_desktop(gate)
    except Exception:
        gate.hard("桌面/构建入口检查异常", False, traceback.format_exc(limit=2))

    # 8 软记录
    try:
        check_soft(gate, base, e2e, bundle)
    except Exception:
        gate.hard("软记录检查异常", False, traceback.format_exc(limit=2))

    elapsed = round(time.time() - t0, 2)
    total = gate.passes + gate.fails
    print(f"\n{'=' * 68}")
    if gate.fails == 0:
        print(f"  {GREEN}回归通过{RESET}：{total} 项硬检查全绿"
              f"（含 {gate.warns} 条软提示），耗时 {elapsed}s")
        print(f"  结论：红线未破，可以继续开发/提交。")
    else:
        print(f"  {RED}发现有 {gate.fails} 项硬检查失败{RESET}"
              f"（共 {total} 项，软提示 {gate.warns} 条），耗时 {elapsed}s")
        print(f"  {RED}结论：红线被破坏，禁止提交/交付；修好或回滚后重跑。{RESET}")
        print(f"\n  失败清单：")
        for title, rows in gate.rows.items():
            for row in rows:
                if row["status"] == "FAIL":
                    print(f"    {RED}✗{RESET} [{title}] {row['name']} - {row['detail']}")
    print(f"{'=' * 68}\n")
    return 0 if gate.fails == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="数据隐站回归门禁：一条命令判定红线是否被破坏")
    ap.add_argument("--update-baseline", action="store_true",
                    help="重录基线（仅在确认是改进后使用）")
    ap.add_argument("--reason", default="",
                    help="重录基线的原因（会写入 baseline.json/SNAPSHOT.md 的更新记录）")
    ap.add_argument("--keep", action="store_true",
                    help="保留临时数据目录（排查用）")
    ap.add_argument("--workdir", default="",
                    help="用固定目录做临时区（默认系统临时目录）")
    args = ap.parse_args(argv)

    try:
        work = qa.setup(Path(args.workdir) if args.workdir else None, clean=True)
    except Exception as exc:  # noqa: BLE001
        print(f"初始化失败：{type(exc).__name__}: {exc}")
        return 2
    try:
        if args.update_baseline:
            return update_baseline(args.reason)
        return run_checks()
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"{DIM}临时目录保留：{work}{RESET}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(2)
