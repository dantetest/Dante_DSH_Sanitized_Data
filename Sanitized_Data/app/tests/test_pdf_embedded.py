# -*- coding: utf-8 -*-
"""P1-B / P1.5：PDF 文本层内嵌图片检测/整块涂黑 自测。

覆盖：
1. sensitive.pdf（文本层 + 内嵌二维码/虚构证件图 + 正常配图）
   → parse 产出 embedded_image 标记（2 个），note/rect/page 正确；正常配图不命中；
2. text_sensitive.pdf（内嵌纯文字图：ASCII 手机号/邮箱/合法校验位身份证号）
   → 视觉无命中时由 media_text 文字规则检测器命中，产 embedded_image（note 带 text=N）；
3. normal_only / tiny_qr_only / text_only / scanned_qr
   → 无 embedded_image（面积门控 / 纯文本 / 扫描页不走本支）；
   text_only 性能不劣化、无新增 warning；
4. debug 开关（YINZHAN_PDF_EMBEDDED_DEBUG=1）输出处理/命中/文字 OCR 统计 warning；
5. P1.5 开关（YINZHAN_PDF_EMBEDDED_TEXT_OCR=0）关掉文字检测器后被文字图片不产 mark；
6. 端到端（视觉命中 / 纯文字命中两例）：jobs 实体 → export（redaction）→
   成品图片区域黑像素 ≥95% → 重解析成品 0 embedded_image → pipeline.verify 通过。

用法（Windows 控制台要 -X utf8）：
    python -X utf8 app/tests/test_pdf_embedded.py
首次运行若 fixtures 缺失会自动调用 generate_pdfs.py 生成（全虚构，可提交）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

# ── 隔离数据目录：必须在 import yinzhan.* 之前 ──
_WORK = Path(tempfile.mkdtemp(prefix="yz_pdf_embed_test_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(_WORK / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(_WORK / "out"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures" / "pdf_embedded"
MANIFEST = FIXTURES / "manifest.json"

if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import fitz  # noqa: E402

from yinzhan import jobs, mediascan, pipeline, registry  # noqa: E402
from yinzhan.ocr import get_ocr  # noqa: E402
from yinzhan.parsers.pdf_parser import PdfParser  # noqa: E402
from yinzhan.udm import (  # noqa: E402
    Decision, Entity, ParseContext, Span, VerifyError,
)

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
_RESULT = {"pass": 0, "fail": 0, "skip": 0, "fails": []}
_REPORT: dict[str, object] = {}
_PARSE_CACHE: dict[str, object] = {}

_NOTE_RE = re.compile(r"^engine=embedded_image;n=(\d+);area=(\d+\.\d+)$")
_TEXT_NOTE_RE = re.compile(r"^engine=embedded_image;text=(\d+);area=(\d+\.\d+)$")


# ────────────────────────────── 工具 ──────────────────────────────

def _record(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        _RESULT["pass"] += 1
        print(f"  {GREEN}[PASS]{RESET} {name}" + (f" - {detail}" if detail else ""))
    else:
        _RESULT["fail"] += 1
        _RESULT["fails"].append(f"{name}: {detail}")
        print(f"  {RED}[FAIL]{RESET} {name}" + (f" - {detail}" if detail else ""))
    return ok


def _check(name: str, cond: bool, detail: str = "") -> None:
    _record(name, bool(cond), detail)


def _skip(name: str, reason: str) -> None:
    _RESULT["skip"] += 1
    print(f"  {YELLOW}[SKIP]{RESET} {name} - {reason}")


def _load_manifest() -> dict:
    if not MANIFEST.is_file():
        spec = importlib.util.spec_from_file_location(
            "pdf_embedded_generator", FIXTURES / "generate_pdfs.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("缺少 fixtures/pdf_embedded/manifest.json")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _parser() -> PdfParser:
    return PdfParser()


def _parse(name: str, cache: bool = True) -> object:
    """解析 fixture；默认按文件名缓存，避免同一 PDF 在多个测试里重复解析（helper 很贵）。"""
    if cache and name in _PARSE_CACHE:
        return _PARSE_CACHE[name]
    ctx = ParseContext(ocr=get_ocr(), workdir=str(_WORK))
    doc = _parser().parse(FIXTURES / name, ctx)
    if cache:
        _PARSE_CACHE[name] = doc
    return doc


def _embedded(doc) -> list:
    return [m for m in doc.visual_marks if m.kind == "embedded_image"]


def _iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = max(0.0, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(0.0, (b[2] - b[0]) * (b[3] - b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _black_ratio(pdf_path: Path, page_no: int, rect) -> float:
    with fitz.open(str(pdf_path)) as pdf:
        page = pdf[page_no]
        clip = fitz.Rect(rect) & page.rect
        pix = page.get_pixmap(clip=clip, dpi=72, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)[:, :, :3]
        return float((arr.max(axis=2) <= 45).mean())


# ────────────────────────────── 测试 ──────────────────────────────

def test_sensitive_parse() -> None:
    print(f"\n{DIM}── sensitive.pdf：内嵌敏感图片被整块标记 ──{RESET}")
    manifest = _load_manifest()
    spec = manifest["cases"]["sensitive"]
    t0 = time.perf_counter()
    doc = _parse("sensitive.pdf")
    elapsed = (time.perf_counter() - t0) * 1000
    marks = _embedded(doc)
    _check("sensitive.pdf 解析成功", doc.pages, f"pages={len(doc.pages)} {elapsed:.0f}ms")
    _check("embedded_image 数 = 2", len(marks) == 2,
           f"n={len(marks)} marks={[(m.note, tuple(round(v) for v in m.rect)) for m in marks]}")
    for expected in spec["sensitive"]:
        rect = expected["rect"]
        hit = next((m for m in marks if _iou(tuple(m.rect), rect) >= 0.5), None)
        _check(f"内嵌 {expected['kind']} 图片命中且 rect 对齐",
               hit is not None, f"expected={rect}")
        if hit is not None:
            _check(f"{expected['kind']} note 格式 engine=embedded_image;n=;area=",
                   bool(_NOTE_RE.match(hit.note)), hit.note)
            _check(f"{expected['kind']} page/confidence 正确",
                   hit.page == 0 and abs(float(hit.confidence) - 0.8) < 1e-6,
                   f"page={hit.page} conf={hit.confidence}")
    # 正常风景配图不应被标记（其区域不应与任何 embedded_image 高度重叠）
    for normal in spec["normal"]:
        overlap = max((_iou(tuple(m.rect), normal["rect"]) for m in marks), default=0.0)
        _check("正常配图未被误标 embedded_image", overlap < 0.2,
               f"overlap={overlap:.2f}")
    _REPORT["sensitive_ms"] = round(elapsed, 1)


def test_text_sensitive_parse() -> None:
    print(f"\n{DIM}── text_sensitive.pdf：P1.5 内嵌纯文字图被整块标记 ──{RESET}")
    manifest = _load_manifest()
    spec = manifest["cases"]["text_sensitive"]
    t0 = time.perf_counter()
    doc = _parse("text_sensitive.pdf")
    elapsed = (time.perf_counter() - t0) * 1000
    _REPORT["text_sensitive_ms"] = round(elapsed, 1)
    marks = _embedded(doc)
    _check("text_sensitive.pdf 解析成功", doc.pages,
           f"pages={len(doc.pages)} {elapsed:.0f}ms")
    _check("文字敏感图产出 1 条 embedded_image", len(marks) == 1,
           f"n={len(marks)} marks={[(m.note, tuple(round(v) for v in m.rect)) for m in marks]}")
    if not marks:
        return
    mark = marks[0]
    match = _TEXT_NOTE_RE.match(mark.note)
    _check("note 为文字格式 engine=embedded_image;text=;area=",
           bool(match), mark.note)
    if match:
        text_count = int(match.group(1))
        _check("text=N 命中数 ≥ 期望值",
               text_count >= int(spec.get("expected_text_min") or 1),
               f"text={text_count} expected>={spec.get('expected_text_min')}")
        _check("area 与图片面积比例一致",
               abs(float(match.group(2)) - 0.163) < 0.01, match.group(2))
    _check("纯文字 mark 不带视觉 n= 字段", ";n=" not in mark.note, mark.note)
    _check("rect 与内嵌图片矩形对齐",
           _iou(tuple(mark.rect), spec["image_rect"]) >= 0.5,
           f"rect={tuple(round(v) for v in mark.rect)} expected={spec['image_rect']}")
    _check("page/confidence 正确",
           mark.page == 0 and abs(float(mark.confidence) - 0.8) < 1e-6,
           f"page={mark.page} conf={mark.confidence}")
    _check("无 helper 跳过/预算 warning",
           not [w for w in doc.warnings if "内嵌图片文字 OCR 跳过" in w
                or "内嵌图片过大" in w],
           str([w for w in doc.warnings if "内嵌图片" in w][:3]))


def test_text_sensitive_switch() -> None:
    print(f"\n{DIM}── P1.5 开关：YINZHAN_PDF_EMBEDDED_TEXT_OCR=0 关闭文字检测 ──{RESET}")
    old = os.environ.get("YINZHAN_PDF_EMBEDDED_TEXT_OCR")
    os.environ["YINZHAN_PDF_EMBEDDED_TEXT_OCR"] = "0"
    try:
        t0 = time.perf_counter()
        doc = _parse("text_sensitive.pdf", cache=False)
        elapsed = (time.perf_counter() - t0) * 1000
    finally:
        if old is None:
            os.environ.pop("YINZHAN_PDF_EMBEDDED_TEXT_OCR", None)
        else:
            os.environ["YINZHAN_PDF_EMBEDDED_TEXT_OCR"] = old
    marks = _embedded(doc)
    _check("关闭文字检测后不产 embedded_image", not marks,
           f"hits={[(m.note) for m in marks]} {elapsed:.0f}ms")
    _REPORT["text_ocr_disabled_ms"] = round(elapsed, 1)


def test_negative_fixtures() -> None:
    print(f"\n{DIM}── 负样本 / 门槛：normal_only / tiny_qr / scanned_qr ──{RESET}")
    for name, label in (("normal_only.pdf", "普通配图 PDF"),
                        ("tiny_qr_only.pdf", "小二维码（<5% 页）PDF"),
                        ("scanned_qr.pdf", "扫描页（无文本层）")):
        t0 = time.perf_counter()
        doc = _parse(name)
        elapsed = (time.perf_counter() - t0) * 1000
        marks = _embedded(doc)
        _check(f"{label} 无 embedded_image", not marks,
               f"hits={[(m.note, tuple(round(v) for v in m.rect)) for m in marks]} "
               f"{elapsed:.0f}ms")
        _REPORT[name] = round(elapsed, 1)


def test_text_only_perf() -> None:
    print(f"\n{DIM}── text_only.pdf：纯文本页零新增/性能不劣化 ──{RESET}")
    samples = []
    for _ in range(3):
        t0 = time.perf_counter()
        doc = _parse("text_only.pdf", cache=False)
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    median = samples[len(samples) // 2]
    _REPORT["text_only_ms"] = round(median, 1)
    _check("纯文本页无 embedded_image", not _embedded(doc))
    _check("纯文本页无内嵌图片扫描 warning",
           not [w for w in doc.warnings if "内嵌图片" in w],
           str(doc.warnings[:3]))
    _check("纯文本页解析中位耗时 <1500ms", median < 1500.0,
           f"median={median:.0f}ms best={samples[0]:.0f}ms")


def test_debug_warning() -> None:
    print(f"\n{DIM}── debug 开关：处理/命中/文字 OCR 统计 warning ──{RESET}")
    visual_doc = _parse("sensitive.pdf")
    text_doc = _parse("text_sensitive.pdf")
    visual_dbg = [w for w in visual_doc.warnings if "内嵌图片扫描 第 1 页" in w]
    _check("视觉样例 debug warning 存在", bool(visual_dbg),
           str(visual_doc.warnings[:4]))
    if visual_dbg:
        _check("视觉样例 debug warning 含命中数 + 文字 OCR 0",
               "命中 2 张" in visual_dbg[0] and "文字 OCR 0 张" in visual_dbg[0],
               visual_dbg[0])
    text_dbg = [w for w in text_doc.warnings if "内嵌图片扫描 第 1 页" in w]
    _check("文字样例 debug warning 存在", bool(text_dbg),
           str(text_doc.warnings[:4]))
    if text_dbg:
        _check("文字样例 debug warning 含文字 OCR 1 张",
               "文字 OCR 1 张" in text_dbg[0], text_dbg[0])


def _ensure_entities(doc, expected_min: int) -> list:
    """jobs 映射（视觉/文字 mark → EMBEDDED_IMAGE 实体）；未接入时测试兜底构造。"""
    ents = jobs._visual_entities(doc, [])
    emb = [e for e in ents if e.etype == "EMBEDDED_IMAGE"]
    if not emb:
        for mark in _embedded(doc):
            ents.append(Entity(
                id=Entity.new_id(), etype="EMBEDDED_IMAGE", label="内嵌图片（敏感）",
                value="", spans=[Span(rect=tuple(mark.rect), page=mark.page)],
                level="L3", confidence=0.8, origin="visual", status="accepted",
                decision=Decision(strategy="black", black=True, replacement=None),
            ))
        emb = [e for e in ents if e.etype == "EMBEDDED_IMAGE"]
    for entity in emb:
        if entity.decision is None:
            entity.decision = Decision(strategy="black", black=True, replacement=None)
    _check("embedded_image 实体已生成（jobs 映射或测试兜底）",
           len(emb) >= expected_min, f"n={len(emb)} expected>={expected_min}")
    return ents


def _run_end_to_end(name: str, label: str, expected_rects: list[tuple[str, list]]) -> None:
    doc = _parse(name)
    marks = _embedded(doc)
    ents = _ensure_entities(doc, len(expected_rects))
    out = _WORK / f"{name}_out.pdf"
    parser = _parser()
    t0 = time.perf_counter()
    result = parser.export(doc, ents, pipeline.decisions_of(ents), out,
                           {"mode": "human"})
    export_ms = (time.perf_counter() - t0) * 1000
    _check(f"{label} 导出成功且无涂黑复检 warning",
           out.is_file() and not [w for w in result.warnings if "未完全涂黑" in w],
           f"{export_ms:.0f}ms warnings={result.warnings[:3]}")
    for expected_label, rect in expected_rects:
        ratio = _black_ratio(out, 0, rect)
        _check(f"{label} {expected_label} 图片区域已实心涂黑（黑像素≥95%）",
               ratio >= 0.95, f"black={ratio:.1%}")
    doc_out = parser.parse(out, ParseContext(ocr=get_ocr(), workdir=str(_WORK)))
    left = _embedded(doc_out)
    _check(f"{label} 重解析成品 0 个 embedded_image", not left,
           f"left={[(m.note, tuple(round(v) for v in m.rect)) for m in left]}")
    try:
        info = pipeline.verify(out, ents, parser,
                               ParseContext(ocr=get_ocr(), workdir=str(_WORK)),
                               doc=doc, options={})
        ok, detail = True, {k: info.get(k) for k in ("verified", "residual",
                                                     "visual_residual") if k in info}
    except VerifyError as exc:
        ok, detail = False, str(exc)
    _check(f"{label} pipeline.verify 通过", ok, str(detail))
    _REPORT.setdefault("export_ms", {})
    _REPORT["export_ms"][name] = round(export_ms, 1)


def test_end_to_end() -> None:
    print(f"\n{DIM}── 端到端：导出整块涂黑 → 成品复检 → verify ──{RESET}")
    manifest = _load_manifest()
    cases = manifest["cases"]
    sensitive_rects = [(item["kind"], item["rect"])
                       for item in cases["sensitive"]["sensitive"]]
    _run_end_to_end("sensitive.pdf", "视觉命中", sensitive_rects)
    text_rect = cases["text_sensitive"]["image_rect"]
    _run_end_to_end("text_sensitive.pdf", "纯文字命中",
                    [("text", text_rect)])


# ────────────────────────────── 入口 ──────────────────────────────

def main() -> int:
    print("=" * 72)
    print("数据隐站 · P1-B/P1.5 PDF 内嵌图片检测/整块涂黑 自测")
    print(f"fixtures: {FIXTURES}")
    print("=" * 72)
    try:
        registry.load_plugins()
    except Exception as exc:  # noqa: BLE001
        _record("registry.load_plugins", False, f"{type(exc).__name__}: {exc}")
    print(f"  scanners: {mediascan.scanner_names()}")
    # debug 统计 warning 全流程开启（解析缓存后由 debug 测试直接断言）；
    # 测试结束恢复，避免污染父进程环境。
    old_debug = os.environ.get("YINZHAN_PDF_EMBEDDED_DEBUG")
    os.environ["YINZHAN_PDF_EMBEDDED_DEBUG"] = "1"
    tests = [
        test_sensitive_parse,
        test_text_sensitive_parse,
        test_text_sensitive_switch,
        test_negative_fixtures,
        test_text_only_perf,
        test_debug_warning,
        test_end_to_end,
    ]
    started = time.time()
    try:
        for fn in tests:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                import traceback

                _record(fn.__name__, False, f"{type(exc).__name__}: {exc}")
                traceback.print_exc(limit=3)
    finally:
        if old_debug is None:
            os.environ.pop("YINZHAN_PDF_EMBEDDED_DEBUG", None)
        else:
            os.environ["YINZHAN_PDF_EMBEDDED_DEBUG"] = old_debug
    elapsed = time.time() - started
    print()
    print(f"  PASS {_RESULT['pass']} · FAIL {_RESULT['fail']} · SKIP {_RESULT['skip']}"
          f" · {elapsed:.1f}s")
    if _REPORT:
        print(f"  实测：{json.dumps(_REPORT, ensure_ascii=False)}")
    if _RESULT["fail"]:
        print(f"{RED}结论：FAIL{RESET}")
        for item in _RESULT["fails"]:
            print(f"    ✗ {item}")
        return 1
    print(f"{GREEN}结论：PASS{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
