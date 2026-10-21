# -*- coding: utf-8 -*-
"""预览矩形一致性测试（pipeline.boxes_for_span）。

背景（v1.1.2 修复）：扫描件/图片解析出来的 OCR 行块只有整行 bbox，没有字符级
坐标。导出器（pdf_parser / image_parser / tiff_parser）会按字符比例在行框内插值，
但预览侧 `pipeline.boxes_for_span` 曾经直接回退成整块 bbox，导致 WebUI「涂黑」
形态把整行（甚至连续几行法律条款）画成黑条。本测试锁住两边口径：

- OCR 行块：实体 span 的预览矩形必须按字符比例插值（窄条、位置正确）；
- 有字符级坐标的块：仍然用精确字符框；
- 无 OCR 标记、无字符坐标的未知块：保留整块回退（不要误伤 flow 格式）；
- 矩形 span：原样返回。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_preview_boxes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import pipeline                                      # noqa: E402
from yinzhan.parsers.pdf_parser import (_merge_black_rects,        # noqa: E402
                                        _merge_ops)
from yinzhan.udm import Block, Document, Page, Span               # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f" - {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def _doc_with(block: Block) -> Document:
    return Document(doc_id="d", path="synthetic", fmt="pdf",
                    pages=[Page(index=block.page, width=595.0, height=842.0,
                                blocks=[block])])


def test_ocr_block_interpolates() -> None:
    print("\n■ OCR 行块：按字符比例插值（不再整行涂黑）")
    text = "平等自愿、协商一致的原则,就甲方委托乙方 胡连林、蒋港 律师及其团队代理"
    line = (55.0, 260.0, 540.0, 285.0)
    block = Block(id="b2_o8", kind="text", text=text, bbox=line,
                  ref={"page": 2, "ocr": True}, page=2)
    doc = _doc_with(block)
    start = text.index("胡连林")
    span = Span(block_id="b2_o8", start=start, end=start + 3)
    boxes = pipeline.boxes_for_span(doc, span)
    check("返回 1 个矩形", len(boxes) == 1, str(len(boxes)))
    if not boxes:
        return
    x0, y0, x1, y1 = boxes[0]
    line_w = line[2] - line[0]
    check("宽度 < 行宽 25%（只盖住人名）", (x1 - x0) < line_w * 0.25,
          f"w={x1 - x0:.1f} / line={line_w:.1f}")
    ratio = (x0 - line[0]) / line_w
    check("起点按字符比例定位", abs(ratio - start / len(text)) < 0.02,
          f"ratio={ratio:.3f} want={start / len(text):.3f}")
    check("纵向沿用行框", abs(y0 - line[1]) < 0.01 and abs(y1 - line[3]) < 0.01)

    # 同一行两个实体不能都膨胀成整行
    span2 = Span(block_id="b2_o8", start=text.index("蒋港"),
                 end=text.index("蒋港") + 2)
    boxes2 = pipeline.boxes_for_span(doc, span2)
    check("第二个人名同样窄条",
          len(boxes2) == 1 and (boxes2[0][2] - boxes2[0][0]) < line_w * 0.25,
          str(boxes2))


def test_char_level_block_precise() -> None:
    print("\n■ 有字符级坐标的块：仍用字符框")
    chars = [(10, 10, 20, 20), (20, 10, 30, 20), (30, 10, 40, 20), (40, 10, 50, 20)]
    block = Block(id="b1_t", kind="text", text="甲乙丙丁",
                  bbox=(10.0, 10.0, 50.0, 20.0),
                  ref={"page": 1, "chars": chars}, page=1)
    doc = _doc_with(block)
    boxes = pipeline.boxes_for_span(doc, Span(block_id="b1_t", start=1, end=3))
    check("字符框精确匹配", boxes == [(20.0, 10.0, 40.0, 20.0)], str(boxes))


def test_unknown_block_fallback() -> None:
    print("\n■ 无 OCR 标记/无字符坐标：保留整块回退")
    block = Block(id="b9", kind="text", text="某某", bbox=(0.0, 0.0, 100.0, 10.0),
                  ref={}, page=0)
    doc = _doc_with(block)
    boxes = pipeline.boxes_for_span(doc, Span(block_id="b9", start=0, end=2))
    check("回退整块", boxes == [(0.0, 0.0, 100.0, 10.0)], str(boxes))


def test_rect_span() -> None:
    print("\n■ 矩形 span：原样返回")
    block = Block(id="b0", kind="text", text="x", bbox=(0.0, 0.0, 10.0, 10.0),
                  ref={"page": 0, "ocr": True}, page=0)
    doc = _doc_with(block)
    boxes = pipeline.boxes_for_span(doc, Span(rect=(5.0, 5.0, 6.0, 6.0)))
    check("rect 优先", boxes == [(5.0, 5.0, 6.0, 6.0)], str(boxes))


def test_black_merge_no_growth() -> None:
    print("\n■ 导出黑框合并：不得把外接矩形并大（预览/成品一致）")
    sig = (0.0, 700.0, 539.0, 800.0)
    face = (300.0, 760.0, 420.0, 880.0)
    merged = _merge_black_rects([sig, face])

    def area(b):
        return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    check("大签名框+角落误检块不合并", len(merged) == 2, str(merged))
    check("黑区总面积不因合并增大",
          abs(sum(area(b) for b in merged) - (area(sig) + area(face))) < 1e-6,
          f"merged={sum(area(b) for b in merged):.0f}")
    # 近乎重合/包含的框仍然合并，避免重复绘制
    check("近似重合框合并", len(_merge_black_rects([(10, 10, 100, 20),
                                                  (12, 10, 98, 20)])) == 1)
    check("包含框合并", len(_merge_black_rects([(0, 0, 100, 100),
                                             (10, 10, 20, 20)])) == 1)
    ops = [{"rect": sig, "black": True, "text": ""},
           {"rect": face, "black": True, "text": ""}]
    black = [o for o in _merge_ops(ops, []) if o["black"]]
    check("_merge_ops 保留两个独立黑框", len(black) == 2, str(black))


def main() -> int:
    print("数据隐站 · 预览矩形一致性测试（pipeline.boxes_for_span）")
    print("-" * 64)
    test_ocr_block_interpolates()
    test_char_level_block_precise()
    test_unknown_block_fallback()
    test_rect_span()
    test_black_merge_no_growth()
    print("-" * 64)
    if FAILED:
        print(f"失败：{len(FAILED)} 项 - {FAILED}")
        return 1
    print("通过：5 组断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
