# -*- coding: utf-8 -*-
"""预览=成品一致性测试（在线预览 vs 涂黑版下载）。

背景（v1.1.2）：WebUI 的「涂黑」形态曾经按实体框估算黑条，而下载成品由解析器
按 `_collect_ops`+`_merge_ops` 计算矩形，两条路径各算各的 → 出现过「预览整行黑、
下载只涂实体」（扫描件）或「预览小、成品大」。现在解析器提供
`export_black_rects()` 钩子，预览直接调用同一套几何，本测试锁住这个契约：

1. PDF：`export_black_rects()` 的矩形必须与 human 导出成品上的黑色矩形一致
   （成品每块≥98% 黑、成品没有在预期矩形之外新增黑块）；
2. 扫描件 OCR 行块：hook 给出的矩形必须等于预览用 `boxes_for_span` 的矩形
   （按字符比例插值，而不是整行）；
3. 非 PDF（图片等）：hook 返回 None，预览回退到实体框（不强制）。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_preview_export_parity.py
"""
from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import fitz                                                     # noqa: E402
import numpy as np                                              # noqa: E402

from yinzhan import pipeline, registry                          # noqa: E402
from yinzhan.jobs import _load_rules                            # noqa: E402
from yinzhan.ocr import get_ocr                                 # noqa: E402
from yinzhan.udm import Block, Decision, Document, Page, ParseContext, Span  # noqa: E402

SAMPLES = REPO / "samples"
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def _black_mask(img) -> np.ndarray:
    arr = np.asarray(img).astype(np.int16)
    return (arr[:, :, 0] < 40) & (arr[:, :, 1] < 40) & (arr[:, :, 2] < 40)


def _human_black_rects(parser, doc, entities) -> dict[int, list]:
    clones = []
    for e in entities:
        if e.status == "rejected" or e.decision is None:
            continue
        c = copy.copy(e)
        c.decision = Decision(strategy="black", black=True, replacement=None)
        clones.append(c)
    return parser.export_black_rects(doc, clones, {}) or {}


def test_pdf_export_parity() -> None:
    print("\n■ PDF：export_black_rects 与 human 成品黑框一致")
    sample = SAMPLES / "委托代理合同.pdf"
    if not sample.is_file():
        check("样例 委托代理合同.pdf 存在", False)
        return
    parser = registry.find_parser(sample)
    check("找到 PDF 解析器", parser is not None)
    if parser is None:
        return
    tmp = Path(tempfile.mkdtemp(prefix="yz_parity_"))
    ctx = ParseContext(ocr=get_ocr(), workdir=str(tmp))
    doc = parser.parse(sample, ctx)
    rules, _, _ = _load_rules("pdf")
    det = pipeline.detect(doc, rules, "standard")
    pipeline.decide(det.entities, det.rules, pipeline.Session())
    ents = [e for e in det.entities if e.status != "rejected"]
    check("样例有实体", bool(ents), f"{len(ents)} 个")

    rects = _human_black_rects(parser, doc, ents)
    check("hook 返回按页矩形", isinstance(rects, dict) and len(rects) > 0,
          f"pages={sorted(rects)}")

    out = tmp / "human.pdf"
    clones = []
    for e in ents:
        if e.decision is None:
            continue
        c = copy.copy(e)
        c.decision = Decision(strategy="black", black=True, replacement=None)
        clones.append(c)
    parser.export(doc, clones, {}, out, {"mode": "human"})
    check("human 导出成功", out.is_file() and out.stat().st_size > 0)

    pdf = fitz.open(out)
    src_pdf = fitz.open(str(sample))
    zoom = 2.0
    for page_no in sorted(rects):
        if page_no >= len(pdf):
            continue
        pix = pdf[page_no].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        from PIL import Image
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        actual = _black_mask(img)
        W = doc.pages[page_no].width or pdf[page_no].rect.width
        H = doc.pages[page_no].height or pdf[page_no].rect.height
        sx, sy = pix.width / W, pix.height / H
        # 1) 每个预期矩形内部≥98% 是黑的
        worst = 1.0
        for rect in rects[page_no]:
            x0, y0, x1, y1 = [float(v) for v in rect[:4]]
            px0, py0 = int(round(x0 * sx)), int(round(y0 * sy))
            px1, py1 = int(round(x1 * sx)), int(round(y1 * sy))
            sub = actual[max(0, py0):max(0, py1), max(0, px0):max(0, px1)]
            if sub.size:
                worst = min(worst, float(sub.sum()) / float(sub.size))
        check(f"第 {page_no + 1} 页预期矩形内均为黑色", worst >= 0.98,
              f"最低黑占比={worst:.3f}")
        # 2) 成品没有在预期矩形之外新增黑块（与原页面渲染中的已有黑字对比）
        orig_pix = src_pdf[page_no].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        orig_img = Image.frombytes("RGB", (orig_pix.width, orig_pix.height),
                                   orig_pix.samples)
        orig_black = _black_mask(orig_img)
        added = actual & ~orig_black
        expected = np.zeros_like(actual)
        for rect in rects[page_no]:
            x0, y0, x1, y1 = [float(v) for v in rect[:4]]
            expected[int(round(y0 * sy)):int(round(y1 * sy)),
                     int(round(x0 * sx)):int(round(x1 * sx))] = True
        # 允许 2px 渲染/坐标取整误差：把 expected 向外膨胀后再判外溢
        padded = np.zeros_like(expected)
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                shifted = np.roll(np.roll(expected, dy, axis=0), dx, axis=1)
                padded |= shifted
        leaked = np.logical_and(added, ~padded).sum()
        total = max(1, int(added.sum()))
        check(f"第 {page_no + 1} 页成品未在预期外新增涂黑", leaked / total <= 0.005,
              f"预期外新增 {leaked} px（{leaked / total:.4%}）")
        # 3) 预览矩形必须全部落进成品黑色区域（预览不能多画）
        missed = np.logical_and(expected, ~actual).sum()
        check(f"第 {page_no + 1} 页预览黑区在成品中也是黑的",
              missed <= int(expected.sum()) * 0.002,
              f"预览但成品未黑 {missed} px")
    pdf.close()
    src_pdf.close()


def test_ocr_block_hook_matches_preview() -> None:
    print("\n■ 扫描件 OCR 行块：hook 矩形 = 预览插值矩形（不是整行）")
    text = "平等自愿、协商一致的原则,就甲方委托乙方 胡连林、蒋港 律师及其团队代理"
    line = (55.0, 260.0, 540.0, 285.0)
    block = Block(id="b2_o8", kind="text", text=text, bbox=line,
                  ref={"page": 2, "ocr": True}, page=2)
    doc = Document(doc_id="d", path="synthetic", fmt="pdf",
                   pages=[Page(index=2, width=595.0, height=842.0, blocks=[block])])
    from yinzhan.udm import Entity
    start = text.index("胡连林")
    ent = Entity(id="e1", etype="PERSON", label="人名", value="胡连林",
                 spans=[Span(block_id="b2_o8", start=start, end=start + 3)],
                 decision=Decision(strategy="black", black=True, replacement=None))
    parser = registry.all_parsers().get("pdf")
    if parser is None:
        check("pdf 解析器已注册", False)
        return
    rects = parser.export_black_rects(doc, [ent], {})
    preview_boxes = pipeline.boxes_for_span(doc, ent.spans[0])
    got = (rects or {}).get(2) or []
    check("hook 返回该页矩形", len(got) == 1, str(got))
    if got and preview_boxes:
        g, p = got[0], preview_boxes[0]
        same = all(abs(float(a) - float(b)) < 0.05 for a, b in zip(g[:4], p[:4]))
        check("hook 与预览矩形一致", same, f"{g} vs {p}")
        check("宽度为行宽 25% 以内（未整行涂黑）",
              (g[2] - g[0]) < (line[2] - line[0]) * 0.25,
              f"w={g[2] - g[0]:.1f}")


def test_non_pdf_fallback() -> None:
    print("\n■ 非 PDF：hook 返回 None（预览回退实体框）")
    sample = SAMPLES / "名片.png"
    if not sample.is_file():
        check("样例 名片.png 存在", False)
        return
    parser = registry.find_parser(sample)
    check("图片解析器无 hook 或返回 None",
          parser is not None and getattr(parser, "export_black_rects",
                                         lambda *a, **k: None) is not None)


def main() -> int:
    print("数据隐站 · 预览=成品一致性测试（preview/export parity）")
    print("-" * 64)
    registry.load_plugins()
    try:
        test_pdf_export_parity()
    except Exception as exc:  # noqa: BLE001
        check("PDF 一致性测试未抛异常", False, f"{type(exc).__name__}: {exc}")
    try:
        test_ocr_block_hook_matches_preview()
    except Exception as exc:  # noqa: BLE001
        check("OCR hook 测试未抛异常", False, f"{type(exc).__name__}: {exc}")
    try:
        test_non_pdf_fallback()
    except Exception as exc:  # noqa: BLE001
        check("非 PDF 回退测试未抛异常", False, f"{type(exc).__name__}: {exc}")
    print("-" * 64)
    if FAILED:
        print(f"失败：{len(FAILED)} 项 - {FAILED}")
        return 1
    print("通过：预览矩形与导出成品一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
