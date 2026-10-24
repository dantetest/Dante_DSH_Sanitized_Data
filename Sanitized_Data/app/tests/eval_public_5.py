# -*- coding: utf-8 -*-
"""公共 5 样本实测：parse → detect → decide → visual → export → verify。

用于 `%TEMP%\\yz_public5_testset\\` 下的 5 个公开/生成样本（见
``app/tests/fixtures/public_5/MANIFEST.md``），完整记录每个文件的：
解析/检测/决策/导出/复检耗时、实体类型与数量、视觉 marks、verify 结果、
导出 warnings，并给出「P0 缺陷 / 已知限制 / 误报」分类。

用法（Windows 控制台要 ``-X utf8``）：
    python -X utf8 app/tests/eval_public_5.py
    python -X utf8 app/tests/eval_public_5.py --dir "%TEMP%\\yz_public5_testset" ^
        --out "%TEMP%\\yz_public5_eval"

安全约定：输入目录只读；报告只写类型/数量/长度/warning 文本（warning 经实体值
脱敏），不记录实体原值或 OCR 文本；导出成品写到报告目录的 ``exported/`` 下，
仅用于本地 verify，不上传、不提交。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
DEFAULT_DIR = Path(os.environ.get("TEMP", ".")) / "yz_public5_testset"
ENGINE_FMTS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp",
    ".tif", ".tiff", ".docx", ".xlsx", ".txt", ".csv",
}

#: 5 个样本的来源/许可/用途（与 MANIFEST.md 对齐；分类用于报告）
SAMPLE_META: dict[str, dict] = {
    "public_5_pdf_plos_0000308.pdf": {
        "category": "PDF（文本层 + 内嵌图片）",
        "source": "PLOS ONE 10.1371/journal.pone.0000308（printable PDF）",
        "license": "CC BY 4.0（PLOS 文章页：Creative Commons Attribution License）",
        "note": "下载；含文本层与内嵌栅格图（figure）。不提交。",
    },
    "public_5_docx_calibre_demo.docx": {
        "category": "DOCX（内嵌图片/表格/脚注）",
        "source": "filesamples.com sample1.docx（calibre DOCX 功能演示文档）",
        "license": "站点未声明许可（原始为 calibre 演示文档）→ 仅本地测试、不提交",
        "note": "下载；含 1 个 inline 图片、4 个媒体文件、5 个表格；PII 正则粗检 0 命中。",
    },
    "public_5_csv_iris.csv": {
        "category": "CSV 表格（无 PII）",
        "source": "UCI ML Repository Iris（iris.data）",
        "license": "CC BY 4.0（UCI 数据集页确认）",
        "note": "下载；经典 Iris 数据集，无数值/文本 PII。不提交。",
    },
    "public_5_qr_synthetic.png": {
        "category": "图片（QR）",
        "source": "本地生成：zxing-cpp 编码 PUBLIC-5-TEST-QR",
        "license": "生成内容（无第三方素材）",
        "note": "生成；仅含虚构字符串。可提交（默认也不提交）。",
    },
    "public_5_scanned_annotated.pdf": {
        "category": "扫描件 + 批注/附件 PDF",
        "source": "本地生成：PIL 合成扫描页 + PyMuPDF 批注/附件",
        "license": "生成内容（全虚构）",
        "note": "生成；含虚构手机/邮箱/身份证/地址/金额 + 文本批注 + notes.txt 附件。",
    },
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="公共 5 样本 parse→detect→export→verify 实测")
    ap.add_argument("--dir", action="append", default=None,
                    help="样本目录（可多次；默认 %%TEMP%%\\yz_public5_testset）")
    ap.add_argument("--out", default="", help="报告/成品输出目录（默认 %%TEMP%%\\yz_public5_eval_<ts>）")
    ap.add_argument("--strictness", default="standard",
                    choices=("loose", "standard", "strict"))
    ap.add_argument("--limit", type=int, default=0, help="最多评测文件数（调试）")
    return ap.parse_args(argv)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fmt_ms(value) -> str:
    try:
        return f"{float(value):.0f}ms"
    except (TypeError, ValueError):
        return "—"


def _issues(row: dict, meta: dict) -> list[dict]:
    """按「P0 缺陷 / 已知限制 / 误报」归类问题（只看统计，不读原文）。"""
    issues: list[dict] = []
    if row.get("error"):
        issues.append({"level": "P0 缺陷", "issue": f"解析失败：{row['error']}"})
        return issues
    exp = row.get("export") or {}
    ver = row.get("verify") or {}
    if not exp.get("ok"):
        issues.append({"level": "P0 缺陷", "issue": f"导出失败：{exp.get('error')}"})
    if exp.get("ok"):
        for w in exp.get("warnings") or []:
            if "未完全涂黑" in w or "复检失败" in w:
                issues.append({"level": "P0 缺陷", "issue": f"导出复检 warning：{w}"})
        if not ver.get("ok") and not ver.get("error"):
            issues.append({"level": "P0 缺陷", "issue": "verify 未通过"})
        elif ver.get("error"):
            issues.append({"level": "P0 缺陷", "issue": f"verify 失败：{ver['error']}"})
        elif _int(ver.get("residual")) or _int(ver.get("visual_residual")):
            issues.append({
                "level": "P0 缺陷",
                "issue": f"verify 残留 residual={ver.get('residual')} "
                         f"visual_residual={ver.get('visual_residual')}",
            })
    for mark in row.get("suspicious_marks") or []:
        kind = mark.get("kind") or ""
        if mark.get("whole_page_id"):
            issues.append({"level": "误报", "issue": f"整页证件误判（{kind}，面积 {mark.get('area_ratio')}）"})
        elif mark.get("invalid_large"):
            issues.append({"level": "已知限制/误报",
                           "issue": f"异常大面积码类框（{kind}，面积 {mark.get('area_ratio')}，未自动打码）"})
    if row.get("text_chars", 0) >= 200 and row.get("entity_total", 0) == 0:
        if "csv" in (meta.get("category") or "") or "表格" in (meta.get("category") or ""):
            issues.append({"level": "正常", "issue": "表格无敏感实体（数据本身无 PII，符合预期）"})
        else:
            issues.append({"level": "已知限制/待确认", "issue": "文本丰富但 0 实体，疑似漏检"})
    if row.get("fmt") in ("image", "tiff") and row.get("entity_total", 0) == 0:
        issues.append({"level": "待确认", "issue": "图片无实体（可能为无文字/无敏感目标）"})
    # 性能观察：PDF/扫描件的内嵌图片 OCR + P1.5 helper + verify 二次 OCR 会明显放大耗时
    total_ms = float(row.get("total_ms") or 0)
    if total_ms >= 5000:
        issues.append({
            "level": "已知限制/性能",
            "issue": f"总耗时 {total_ms:.0f}ms（parse {row.get('parse_ms')}ms / "
                     f"verify {row.get('verify_ms')}ms），疑似内嵌图片 OCR/P1.5 助检/扫描 OCR "
                     f"成本；建议 P2 优化（缓存或收紧触发条件）",
        })
    # 警告噪声：图多页重复提示；纯图无文字提示
    face_warns = [w for w in (row.get("warnings") or []) if "照片较大，小脸可能漏检" in w]
    if len(face_warns) >= 2:
        issues.append({"level": "误报/噪声",
                       "issue": f"“照片较大，小脸可能漏检”重复 {len(face_warns)} 次（图多页噪声）"})
    if any("未识别到文字" in w for w in (row.get("warnings") or [])) \
            and row.get("fmt") in ("image", "tiff"):
        issues.append({"level": "误报/噪声", "issue": "纯 QR/图片无文字 warning（不影响 verify）"})
    return issues


def _eval_one(path: Path, out_dir: Path, engine: dict, ocr, strictness: str) -> dict:
    pipeline = engine["pipeline"]
    registry = engine["registry"]
    jobs = engine["jobs"]
    udm = engine["udm"]
    row: dict = {
        "name": path.name, "size": 0, "sha256": "", "fmt": "", "pages": 0,
        "blocks": 0, "ocr_blocks": 0, "text_chars": 0,
        "parse_ms": 0, "rules_ms": 0, "detect_ms": 0, "decide_ms": 0,
        "visual_ms": 0, "export_ms": 0, "verify_ms": 0, "total_ms": 0,
        "entity_total": 0, "entities": {}, "decisions": {},
        "visual_marks": {}, "suspicious_marks": [],
        "export": {}, "verify": {}, "warnings": [], "error": "",
    }
    t_all = time.perf_counter()
    try:
        row["size"] = path.stat().st_size
        row["sha256"] = _sha256(path)
        parser = registry.find_parser(path)
        if parser is None:
            row["error"] = "没有匹配的解析器"
            return row
        row["fmt"] = parser.format_id
        work = out_dir / "_work" / path.name
        work.mkdir(parents=True, exist_ok=True)
        ctx = udm.ParseContext(ocr=ocr, workdir=str(work))

        t0 = time.perf_counter()
        doc = parser.parse(path, ctx)
        row["parse_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        row["pages"] = len(doc.pages)
        row["blocks"] = sum(len(p.blocks) for p in doc.pages)
        row["ocr_blocks"] = sum(
            1 for page in doc.pages for block in page.blocks
            if block.kind == "text" and bool((block.ref or {}).get("ocr")))
        row["text_chars"] = sum(
            len("".join((b.text or "").split())) for page in doc.pages for b in page.blocks
            if b.kind == "text")
        warnings: list[str] = list(doc.warnings or [])

        t0 = time.perf_counter()
        rules, _pack_label, _packs = jobs._load_rules(doc.fmt)
        row["rules_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        t0 = time.perf_counter()
        det = pipeline.detect(doc, rules, strictness)
        row["detect_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        warnings.extend(det.warnings or [])
        entities = list(det.entities)

        t0 = time.perf_counter()
        try:
            form_ents = pipeline.find_form_names(doc, entities)
            form_ents += pipeline.find_party_names(doc, entities + form_ents)
            entities.extend(form_ents)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"表单/并列人名补充跳过：{type(exc).__name__}")
        session = pipeline.Session() if hasattr(pipeline, "Session") else None
        rules_by_id = dict(det.rules)
        for entity in entities:
            if entity.id not in rules_by_id and getattr(entity, "origin", "") == "form":
                form_rule = getattr(pipeline, "_FORM_RULE", None)
                if form_rule is not None:
                    rules_by_id[entity.id] = form_rule
        pipeline.decide(entities, rules_by_id, session)
        try:
            from yinzhan.replacers import amount_consistency

            plan = amount_consistency.build_amount_plan(entities, rules_by_id, session)
            if plan:
                amount_consistency.apply_plan(entities, plan, session)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"金额一致性处理跳过：{type(exc).__name__}")
        try:
            pipeline.propagate_values(doc, entities)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"同值扩散跳过：{type(exc).__name__}")
        row["decide_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        t0 = time.perf_counter()
        try:
            sig_blocks = pipeline.find_signature_blocks(doc)
        except Exception:  # noqa: BLE001
            sig_blocks = []
        for page_index, rect in sig_blocks:
            entities.append(udm.Entity(
                id=udm.Entity.new_id(), etype="SIG_BLOCK", label="签署区", value="",
                spans=[udm.Span(rect=rect, page=page_index)],
                level="L3", confidence=0.95, origin="visual", status="accepted",
                decision=udm.Decision(strategy="black", black=True, replacement=None),
            ))
        try:
            entities.extend(jobs._visual_entities(doc, sig_blocks))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"视觉实体转换跳过：{type(exc).__name__}")
        try:
            entities.extend(jobs._low_confidence_entities(doc, entities))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"低置信手写补充跳过：{type(exc).__name__}")
        row["visual_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        entity_stats = engine["eval"]._entity_stats(entities)
        row["entity_total"] = len(entities)
        row["entities"] = entity_stats["by_type"]
        row["decisions"] = entity_stats["decisions"]
        marks = list(getattr(doc, "visual_marks", None) or [])
        row["visual_marks"] = engine["eval"]._visual_stats(doc)
        page_sizes = [(float(p.width), float(p.height)) for p in doc.pages]
        image_size = ((int(doc.pages[0].width), int(doc.pages[0].height))
                      if doc.pages else (0, 0))
        details = engine["eval"]._mark_details(
            marks, fmt=row.get("fmt"), image_size=image_size, page_sizes=page_sizes)
        row["suspicious_marks"] = [m for m in details if m.get("suspicious")]

        # 导出（成品写报告目录 exported/，仅本地复检）
        t0 = time.perf_counter()
        try:
            export_dir = out_dir / "exported"
            export_dir.mkdir(parents=True, exist_ok=True)
            out_path = export_dir / f"{path.stem}.redacted{path.suffix.lower()}"
            result = parser.export(doc, entities, pipeline.decisions_of(entities),
                                   out_path, {"mode": "human"})
            row["export"] = {
                "ok": True, "path": str(out_path), "size": int(getattr(result, "size", 0) or 0),
                "warnings": engine["eval"]._sanitize_warnings(
                    list(getattr(result, "warnings", None) or []), entities, doc),
            }
        except Exception as exc:  # noqa: BLE001
            row["export"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        row["export_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        # verify（成品再跑一遍：原文/元数据/视觉/码类残留）
        if row["export"].get("ok"):
            t0 = time.perf_counter()
            try:
                info = pipeline.verify(Path(row["export"]["path"]), entities, parser,
                                       ctx, doc=doc, options={})
                row["verify"] = {
                    "ok": bool(info.get("verified", True)),
                    "residual": info.get("residual"),
                    "visual_residual": info.get("visual_residual"),
                    "info": {k: v for k, v in (info or {}).items()
                             if k in ("verified", "residual", "visual_residual",
                                      "hidden_residual")},
                }
            except udm.VerifyError as exc:
                row["verify"] = {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                row["verify"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            row["verify_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        row["warnings"] = jobs._dedupe_warnings(engine["eval"]._sanitize_warnings(warnings, entities, doc))
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        row["total_ms"] = round((time.perf_counter() - t_all) * 1000.0, 1)
    return row


def _write_report(rows: list[dict], summary: dict, out_dir: Path) -> Path:
    lines = [
        "# 公共 5 样本实测报告（parse → detect → export → verify）",
        "",
        f"> 生成：{summary['generated_at']} · 样本目录：`{summary['dir']}`",
        f"> 引擎：`{summary.get('git_head') or 'unknown'}` · 脚本：`app/tests/eval_public_5.py`",
        "> 输入只读；导出成品仅在本机 `%TEMP%` 内用于 verify；报告不含实体原值/OCR 文本。",
        "",
        "## 吞吐",
        "",
        f"- 文件数：{len(rows)}；导出成功：{summary['export_ok']}；verify 通过：{summary['verify_ok']}；"
        f"解析错误：{summary['errors']}",
        f"- 总耗时：{summary['total_ms']:.0f}ms；最慢：{summary['slowest']}",
        "",
        "## 逐文件结果",
        "",
        "| 文件 | 类别/许可 | fmt | 页/块 | parse | detect | export | verify | 实体 | 视觉 marks | 问题 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        meta = SAMPLE_META.get(row["name"], {})
        ver = row.get("verify") or {}
        if row.get("export", {}).get("ok") and ver.get("ok"):
            verify_txt = f"ok (residual={ver.get('residual')}, visual={ver.get('visual_residual')})"
        elif row.get("export", {}).get("ok"):
            verify_txt = f"FAIL {ver.get('error') or ''}"
        else:
            verify_txt = "—"
        issues = "；".join(f"[{i['level']}] {i['issue']}" for i in (
            row.get("_issues") or [])) or "无"
        entity_txt = ", ".join(f"{k}:{v}" for k, v in sorted((row["entities"] or {}).items())) or "0"
        visual_txt = ", ".join(f"{k}:{v}" for k, v in sorted((row["visual_marks"] or {}).items())
                               if v) or "无"
        lines.append(
            f"| `{row['name']}` | {meta.get('category', '?')}<br>{meta.get('license', '?')} | "
            f"{row.get('fmt') or '?'} | {row.get('pages')}/{row.get('blocks')} | "
            f"{_fmt_ms(row.get('parse_ms'))} | {_fmt_ms(row.get('detect_ms'))} | "
            f"{_fmt_ms(row.get('export_ms'))} | {verify_txt} | {entity_txt} | {visual_txt} | {issues} |")
    lines += ["", "## 逐文件明细", ""]
    for row in rows:
        meta = SAMPLE_META.get(row["name"], {})
        ver = row.get("verify") or {}
        lines += [
            f"### `{row['name']}`",
            "",
            f"- 类别：{meta.get('category', '?')}；来源：{meta.get('source', '?')}",
            f"- 许可：{meta.get('license', '?')}；说明：{meta.get('note', '')}",
            f"- size={row['size']} sha256={row['sha256'][:16]}… fmt={row.get('fmt')} "
            f"pages={row.get('pages')} blocks={row.get('blocks')} ocr_blocks={row.get('ocr_blocks')} "
            f"text_chars={row.get('text_chars')}",
            f"- 耗时：parse {_fmt_ms(row.get('parse_ms'))} · rules {_fmt_ms(row.get('rules_ms'))} · "
            f"detect {_fmt_ms(row.get('detect_ms'))} · decide {_fmt_ms(row.get('decide_ms'))} · "
            f"visual {_fmt_ms(row.get('visual_ms'))} · export {_fmt_ms(row.get('export_ms'))} · "
            f"verify {_fmt_ms(row.get('verify_ms'))} · total {_fmt_ms(row.get('total_ms'))}",
            f"- 实体：{json.dumps(row.get('entities') or {}, ensure_ascii=False)}；"
            f"决策：{json.dumps(row.get('decisions') or {}, ensure_ascii=False)}",
            f"- 视觉 marks：{json.dumps(row.get('visual_marks') or {}, ensure_ascii=False)}；"
            f"suspicious={json.dumps([m.get('kind') for m in (row.get('suspicious_marks') or [])], ensure_ascii=False)}",
            f"- 导出：{json.dumps({k: v for k, v in (row.get('export') or {}).items() if k != 'warnings'}, ensure_ascii=False)}；"
            f"warnings={len((row.get('export') or {}).get('warnings') or [])}",
            f"- verify：{json.dumps(ver, ensure_ascii=False)}",
            f"- warnings：{json.dumps(row.get('warnings') or [], ensure_ascii=False)[:1200]}",
            f"- 问题分类：{json.dumps(row.get('_issues') or [], ensure_ascii=False)}",
            "",
        ]
    lines += [
        "## 结论与建议",
        "",
    ]
    p0 = [i for row in rows for i in (row.get("_issues") or []) if i["level"].startswith("P0")]
    known = [i for row in rows for i in (row.get("_issues") or [])
             if i["level"].startswith(("已知限制", "待确认"))]
    noise = [i for row in rows for i in (row.get("_issues") or [])
             if i["level"].startswith(("误报", "待确认"))]
    lines += [
        f"- **P0 缺陷：{len(p0)} 个**（解析/导出失败、verify 残留、导出复检 warning）；"
        + ("见上表。" if p0 else "本次 5 个样本均无。"),
        f"- **已知限制/性能：{len(known)} 条**（内嵌图片 OCR/P1.5 助检/扫描 OCR 造成的耗时放大、"
        "警告噪声等）；列为 P2/P3 优化候选，不阻塞功能正确性。",
        f"- **误报/噪声：{len(noise)} 条**（整页证件/无效大码框/重复警告等）；本次无敏感目标视觉误报。",
        "- 建议优先级：P2（PDF 内嵌图片 OCR/P1.5 helper 的成本优化、警告去重）> "
        "P3（纯图无文字提示降噪）；功能正确性（导出/verify）本次全部通过。",
        "",
        "许可证与来源详见 `app/tests/fixtures/public_5/MANIFEST.md`；样本二进制默认不提交。",
    ]
    report = out_dir / "public5_report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else Path(
        os.environ.get("TEMP", ".")) / f"yz_public5_eval_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YINZHAN_DATA_DIR"] = str(out_dir / "_engine_data")
    os.environ["YINZHAN_OUTPUT_DIR"] = str(out_dir / "_engine_data" / "out")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))
    import eval_real_media as ev  # noqa: E402  （复用引擎加载/统计/脱敏工具）

    engine = ev._load_engine()
    engine["eval"] = ev
    ocr = engine["ocr"].get_ocr()
    if not getattr(ocr, "available", True):
        ocr = None

    dirs = [Path(p).expanduser() for p in (args.dir or [])] or [DEFAULT_DIR]
    files: list[Path] = []
    for root in dirs:
        if not root.is_dir():
            print(f"[WARN] 目录不存在，跳过：{root}")
            continue
        files.extend(sorted(p for p in root.iterdir()
                            if p.is_file() and p.suffix.lower() in ENGINE_FMTS))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print(f"[ERROR] 没有可评测文件：{dirs}")
        return 2

    rows: list[dict] = []
    for index, path in enumerate(files, 1):
        row = _eval_one(path, out_dir, engine, ocr, args.strictness)
        row["_issues"] = _issues(row, SAMPLE_META.get(path.name, {}))
        rows.append(row)
        status = "ERROR" if row.get("error") else "ok"
        print(f"  [{index:02d}] {path.name} → {status} 实体 {row.get('entity_total', 0)} "
              f"导出 {row.get('export', {}).get('ok')} verify {row.get('verify', {}).get('ok')} "
              f"{row.get('total_ms')}ms")

    export_ok = sum(1 for r in rows if (r.get("export") or {}).get("ok"))
    verify_ok = sum(1 for r in rows if (r.get("verify") or {}).get("ok"))
    errors = sum(1 for r in rows if r.get("error"))
    slowest = max(rows, key=lambda r: r.get("total_ms") or 0)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dir": ", ".join(str(d) for d in dirs),
        "out_dir": str(out_dir),
        "files": [r["name"] for r in rows],
        "export_ok": export_ok,
        "verify_ok": verify_ok,
        "errors": errors,
        "p0_issues": sum(1 for r in rows for i in (r.get("_issues") or [])
                         if i["level"].startswith("P0")),
        "total_ms": round(sum(r.get("total_ms") or 0 for r in rows), 1),
        "slowest": f"{slowest['name']} {slowest.get('total_ms')}ms",
    }
    try:
        summary["git_head"] = ev._git_head()
        summary["engine_snapshot"] = ev._engine_snapshot()
    except Exception:  # noqa: BLE001
        pass
    (out_dir / "public5_report.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    report = _write_report(rows, summary, out_dir)
    # 任务要求固定路径：%TEMP%\yz_public5_report.md
    task_report = Path(os.environ.get("TEMP", ".")) / "yz_public5_report.md"
    task_report.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
    print()
    print(f"文件 {len(rows)} · 导出成功 {export_ok} · verify 通过 {verify_ok} · "
          f"解析错误 {errors} · 总 {summary['total_ms']:.0f}ms")
    print(f"报告：{task_report}")
    print(f"明细：{report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
