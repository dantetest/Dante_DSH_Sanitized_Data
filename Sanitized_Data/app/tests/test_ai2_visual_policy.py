# -*- coding: utf-8 -*-
"""Batch2-B4：AI 模式 FACE 误检降级回归测试。

规则（只对 AI 换名版生效，human 涂黑版完全不变）：
- 该 FACE 所在页没有 ID_DOCUMENT / ID_PHOTO / EMBEDDED_IMAGE 实体；
- 且 FACE confidence < 0.95（当前 Haar 自动检测基准 0.92 / 小脸系数 0.874…）；
- 满足条件时 AI 导出跳过该 FACE（不画黑块），human 仍涂黑。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_ai2_visual_policy.py

数据安全：夹具全部虚构、运行期生成；写到临时目录（YINZHAN_DATA_DIR），
不写仓库 data/。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 必须在 import yinzhan 之前指定数据目录，避免写仓库 data/
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="yinzhan_ai2_visual_"))
os.environ["YINZHAN_DATA_DIR"] = str(_TMP_ROOT / "data")
(_TMP_ROOT / "data").mkdir(parents=True, exist_ok=True)

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import fitz  # PyMuPDF  # noqa: E402
import numpy as np  # noqa: E402

from yinzhan import jobs, pipeline  # noqa: E402
from yinzhan.parsers import pdf_parser as pp  # noqa: E402
from yinzhan.parsers.pdf_parser import (  # noqa: E402
    PdfParser, _ai_filter_face_entities,
)
from yinzhan.udm import (  # noqa: E402
    Block, Decision, Document, Entity, Page, ParseContext, Span,
)

PAGE_W, PAGE_H = 300.0, 300.0
FACE_AUTO_CONF = 0.92       # Haar frontal 基准分
FACE_SMALL_CONF = 0.874     # 小脸系数（0.92 * 0.95）
FACE_HIGH_CONF = 0.96       # 模拟高置信/人工框：AI 也应保留
FAST_BLACK_MAX = 45


def _make_pdf(path: Path, pages: int = 1) -> Path:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((40, 35), "标题文字（虚构）", fontname="china-s",
                         fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def _make_doc(path: Path, pages: int = 1, scanned: bool = True) -> Document:
    udm_pages = []
    for index in range(pages):
        udm_pages.append(Page(
            index=index, width=PAGE_W, height=PAGE_H,
            blocks=[Block(id=f"b{index}", kind="text", text=f"标题文字{index + 1}",
                          bbox=(40.0, 20.0, 260.0, 60.0), page=index)],
            ref={"rotation": 0, "scanned": bool(scanned)},
        ))
    return Document(doc_id="d_test", path=str(path), fmt="pdf", pages=udm_pages)


def _visual(eid: str, etype: str, rect, confidence: float,
            page: int = 0) -> Entity:
    return Entity(
        id=eid, etype=etype, label=etype, value="",
        spans=[Span(rect=tuple(float(v) for v in rect), page=page)],
        level="L3", confidence=float(confidence), origin="visual",
        status="accepted",
        decision=Decision(strategy="black", black=True, replacement=None),
    )


def _decisions(entities) -> dict:
    return {entity.id: entity.decision for entity in entities}


def _black_ratio(pdf_path: Path, rect, page: int = 0, dpi: int = 72) -> float:
    scale = dpi / 72.0
    with fitz.open(str(pdf_path)) as pdf:
        pixmap = pdf[page].get_pixmap(dpi=dpi, alpha=False)
        arr = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n)
        rgb = np.ascontiguousarray(arr[:, :, :3])
    x0 = max(0, int(round(rect[0] * scale)))
    y0 = max(0, int(round(rect[1] * scale)))
    x1 = min(rgb.shape[1], int(round(rect[2] * scale)))
    y1 = min(rgb.shape[0], int(round(rect[3] * scale)))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    sub = rgb[y0:y1, x0:x1]
    return float((np.max(sub, axis=2) <= FAST_BLACK_MAX).mean())


def _export(parser: PdfParser, doc: Document, entities, out: Path,
            mode: str):
    return parser.export(doc, entities, _decisions(entities), out,
                         {"mode": mode})


class FaceFilterPolicyTest(unittest.TestCase):
    """纯函数单测：_ai_filter_face_entities 的页面保护与置信度门槛。"""

    def setUp(self):
        self.pdf = _make_pdf(_TMP_ROOT / "unit.pdf", pages=2)
        self.doc = _make_doc(self.pdf, pages=2)

    def test_ai_skips_auto_faces_keeps_high_confidence(self):
        print("\n■ 过滤规则：0.92/0.874 跳过，0.96 保留")
        low = _visual("f_low", "FACE", (20, 20, 60, 60), FACE_AUTO_CONF)
        small = _visual("f_small", "FACE", (80, 20, 100, 40), FACE_SMALL_CONF)
        high = _visual("f_high", "FACE", (140, 20, 180, 60), FACE_HIGH_CONF)
        kept, skipped = _ai_filter_face_entities(
            self.doc, [low, small, high, _visual("seal", "SEAL", (20, 120, 60, 160), 0.9)])
        kept_ids = {e.id for e in kept}
        skipped_ids = {e.id for e in skipped}
        print(f"  kept={sorted(kept_ids)} skipped={sorted(skipped_ids)}")
        self.assertEqual(skipped_ids, {"f_low", "f_small"})
        self.assertIn("f_high", kept_ids)
        self.assertIn("seal", kept_ids)

    def test_ai_keeps_face_on_id_page(self):
        print("\n■ 过滤规则：ID_DOCUMENT 页上的 FACE 不跳")
        id_entity = _visual("id_doc", "ID_DOCUMENT", (20, 20, 120, 120), 0.9,
                            page=1)
        face_same_page = _visual("f_id_page", "FACE", (140, 20, 180, 60),
                                 FACE_AUTO_CONF, page=1)
        face_other_page = _visual("f_plain", "FACE", (20, 20, 60, 60),
                                  FACE_AUTO_CONF, page=0)
        kept, skipped = _ai_filter_face_entities(
            self.doc, [id_entity, face_same_page, face_other_page])
        kept_ids = {e.id for e in kept}
        skipped_ids = {e.id for e in skipped}
        print(f"  kept={sorted(kept_ids)} skipped={sorted(skipped_ids)}")
        self.assertIn("f_id_page", kept_ids, "有证件的页不能跳 FACE")
        self.assertEqual(skipped_ids, {"f_plain"}, "无证件页仍按规则跳")

    def test_face_without_page_kept(self):
        print("\n■ 过滤规则：定位不到页码的 FACE 保守保留")
        no_page = Entity(
            id="f_nopage", etype="FACE", label="FACE", value="",
            spans=[Span(rect=(20.0, 20.0, 60.0, 60.0))],
            level="L3", confidence=FACE_AUTO_CONF, origin="visual",
            status="accepted",
            decision=Decision(strategy="black", black=True, replacement=None),
        )
        kept, skipped = _ai_filter_face_entities(self.doc, [no_page])
        self.assertEqual([e.id for e in skipped], [])
        self.assertEqual([e.id for e in kept], ["f_nopage"])

    def test_text_layer_page_face_kept(self):
        print("\n■ 过滤规则：文本层页上的 FACE 不跳（本策略只降级扫描页）")
        text_doc = _make_doc(self.pdf, pages=1, scanned=False)
        face = _visual("f_text_page", "FACE", (20.0, 20.0, 60.0, 60.0),
                       FACE_AUTO_CONF)
        kept, skipped = _ai_filter_face_entities(text_doc, [face])
        self.assertEqual([e.id for e in skipped], [])
        self.assertEqual([e.id for e in kept], ["f_text_page"])


class FaceExportPolicyTest(unittest.TestCase):
    """导出级断言：AI 跳过自动 FACE；human 全部保留；verify 不受影响。"""

    def setUp(self):
        self.workdir = _TMP_ROOT / "export"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.parser = PdfParser()

    def test_ai_export_skips_auto_faces(self):
        print("\n■ AI 导出：自动 FACE 黑块消失、高置信 FACE 保留、warning 每页一条")
        pdf = _make_pdf(self.workdir / "ai_faces.pdf", pages=1)
        doc = _make_doc(pdf, pages=1)
        rects = {
            "low": (20.0, 20.0, 60.0, 60.0),
            "small": (80.0, 20.0, 100.0, 40.0),
            "high": (140.0, 20.0, 180.0, 60.0),
        }
        ents = [
            _visual("f_low", "FACE", rects["low"], FACE_AUTO_CONF),
            _visual("f_small", "FACE", rects["small"], FACE_SMALL_CONF),
            _visual("f_high", "FACE", rects["high"], FACE_HIGH_CONF),
        ]
        out = self.workdir / "ai_faces_out.pdf"
        result = _export(self.parser, doc, ents, out, "ai")
        low_ratio = _black_ratio(out, rects["low"])
        small_ratio = _black_ratio(out, rects["small"])
        high_ratio = _black_ratio(out, rects["high"])
        warnings = "\n".join(result.warnings or [])
        print(f"  黑像素占比 low={low_ratio:.3f} small={small_ratio:.3f} "
              f"high={high_ratio:.3f}")
        print(f"  warning: {[w for w in result.warnings if '人脸误检' in w]}")
        self.assertLess(low_ratio, 0.05, "0.92 FACE 在 AI 版不应再画黑块")
        self.assertLess(small_ratio, 0.05, "0.874 FACE 在 AI 版不应再画黑块")
        self.assertGreater(high_ratio, 0.9, "0.96 FACE 在 AI 版应保留黑块")
        self.assertIn("AI 视图跳过 2 个自动人脸误检", warnings)
        self.assertIn("仅换名版；涂黑版不变", warnings)

    def test_ai_export_keeps_face_on_id_page(self):
        print("\n■ AI 导出：有 ID_DOCUMENT 的页 FACE 仍涂黑")
        pdf = _make_pdf(self.workdir / "ai_id.pdf", pages=1)
        doc = _make_doc(pdf, pages=1)
        face_rect = (140.0, 20.0, 180.0, 60.0)
        ents = [
            _visual("id_doc", "ID_DOCUMENT", (20.0, 20.0, 120.0, 120.0), 0.9),
            _visual("f_id_page", "FACE", face_rect, FACE_AUTO_CONF),
        ]
        out = self.workdir / "ai_id_out.pdf"
        result = _export(self.parser, doc, ents, out, "ai")
        print(f"  FACE 黑像素占比={_black_ratio(out, face_rect):.3f}")
        self.assertGreater(_black_ratio(out, face_rect), 0.9,
                           "证件页不能跳 FACE")
        self.assertFalse(any("人脸误检" in w for w in (result.warnings or [])))

    def test_human_export_keeps_all_faces(self):
        print("\n■ human 导出：所有 FACE 照旧涂黑（含 AI 会跳过的低置信 FACE）")
        pdf = _make_pdf(self.workdir / "human_faces.pdf", pages=1)
        doc = _make_doc(pdf, pages=1)
        rects = {"low": (20.0, 20.0, 60.0, 60.0),
                 "high": (140.0, 20.0, 180.0, 60.0)}
        ents = [
            _visual("f_low", "FACE", rects["low"], FACE_AUTO_CONF),
            _visual("f_high", "FACE", rects["high"], FACE_HIGH_CONF),
        ]
        out = self.workdir / "human_faces_out.pdf"
        _export(self.parser, doc, ents, out, "human")
        low_ratio = _black_ratio(out, rects["low"])
        high_ratio = _black_ratio(out, rects["high"])
        print(f"  human 黑像素占比 low={low_ratio:.3f} high={high_ratio:.3f}")
        self.assertGreater(low_ratio, 0.9, "human 低置信 FACE 也必须涂黑")
        self.assertGreater(high_ratio, 0.9)
        self.assertNotIn("ai_skipped_faces", doc.extra,
                         "human 模式不应产生 AI 跳过标记")

    def test_ai_export_fast_verify_ignores_skipped_face(self):
        print("\n■ AI 导出后 verify：跳过的 FACE 不被要求黑块，快速复检仍通过")
        pdf = _make_pdf(self.workdir / "verify_faces.pdf", pages=1)
        doc = _make_doc(pdf, pages=1)
        ents = [
            _visual("f_low", "FACE", (20.0, 20.0, 60.0, 60.0), FACE_AUTO_CONF),
            _visual("f_high", "FACE", (140.0, 20.0, 180.0, 60.0),
                    FACE_HIGH_CONF),
        ]
        out = self.workdir / "verify_faces_out.pdf"
        _export(self.parser, doc, ents, out, "ai")
        info = pipeline.verify(out, ents, self.parser, ParseContext(),
                               doc=doc, options={})
        fast = info.get("fast_verify") or {}
        print(f"  verify={info.get('verified')} residual={info.get('residual')} "
              f"fast={fast.get('used')} black_checked={fast.get('black_checked')}")
        self.assertTrue(info.get("verified"))
        self.assertEqual(info.get("residual"), 0)
        self.assertTrue(fast.get("used"), "扫描件 AI 导出应走快速复检")


    def test_human_export_never_calls_face_filter(self):
        print("\n■ human 导出不经过 FACE 过滤（human 零变化）")
        pdf = _make_pdf(self.workdir / "human_never.pdf", pages=1)
        doc = _make_doc(pdf, pages=1)
        ents = [_visual("f_low", "FACE", (20.0, 20.0, 60.0, 60.0),
                        FACE_AUTO_CONF)]
        out = self.workdir / "human_never_out.pdf"
        with mock.patch.object(
                pp, "_ai_filter_face_entities",
                side_effect=AssertionError("human 模式不应调用 FACE 过滤")):
            _export(self.parser, doc, ents, out, "human")
        self.assertTrue(out.exists())
        self.assertGreater(_black_ratio(out, (20.0, 20.0, 60.0, 60.0)), 0.9)


def _pending_handwriting(block_id: str, text: str) -> Entity:
    """模拟「低置信 OCR 行」生成的待确认手写实体（decision 可能缺失）。"""
    return Entity(
        id="hw_1", etype="HANDWRITING", label="疑似手写", value="",
        spans=[Span(block_id=block_id, start=0, end=len(text))],
        level="L3", confidence=0.55, origin="visual", status="pending",
        decision=None,
    )


def _make_job(pdf: Path, doc: Document, entities) -> "jobs.Job":
    return jobs.Job(id="j_test", name="test.pdf", path=str(pdf), fmt="pdf",
                    doc=doc, parser=PdfParser(), entities=list(entities))


class PendingHandwritingPolicyTest(unittest.TestCase):
    """第二步：AI 模式 pending HANDWRITING → black 纳入导出；human 保持现状。"""

    def setUp(self):
        self.workdir = _TMP_ROOT / "pending"
        self.workdir.mkdir(parents=True, exist_ok=True)

    def test_ai_turns_pending_handwriting_black(self):
        print("\n■ AI 导出：pending HANDWRITING → black（含 warning）")
        pdf = _make_pdf(self.workdir / "ai_hand.pdf", pages=1)
        doc = _make_doc(pdf, pages=1)
        block = doc.pages[0].blocks[0]
        handwriting = _pending_handwriting(block.id, block.text)
        job = _make_job(pdf, doc, [handwriting])
        out = self.workdir / "ai_hand_out.pdf"
        info = jobs.JobManager()._export_to(job, "ai", out, do_verify=False)
        ratio = _black_ratio(out, block.bbox)
        warnings = "\n".join(info.get("warnings") or [])
        print(f"  手写区域黑像素占比={ratio:.3f}；"
              f"warning={[w for w in info.get('warnings') or [] if '手写' in w]}")
        self.assertEqual(handwriting.decision.strategy, "black")
        self.assertTrue(handwriting.decision.black)
        self.assertGreater(ratio, 0.9, "AI 版待确认手写应按涂黑导出")
        self.assertIn("AI 视图将 1 个疑似手写签名按涂黑处理", warnings)
        self.assertIn("仅换名版；涂黑版保持人工确认", warnings)

    def test_ai_pending_handwriting_verify_passes(self):
        print("\n■ AI 导出后 verify：pending 手写转 black 不影响自检")
        pdf = _make_pdf(self.workdir / "ai_hand_verify.pdf", pages=1)
        doc = _make_doc(pdf, pages=1)
        block = doc.pages[0].blocks[0]
        handwriting = _pending_handwriting(block.id, block.text)
        job = _make_job(pdf, doc, [handwriting])
        out = self.workdir / "ai_hand_verify_out.pdf"
        jobs.JobManager()._export_to(job, "ai", out, do_verify=False)
        info = pipeline.verify(out, job.entities, job.parser, ParseContext(),
                               doc=doc, options={})
        print(f"  verify={info.get('verified')} residual={info.get('residual')}")
        self.assertTrue(info.get("verified"))
        self.assertEqual(info.get("residual"), 0)

    def test_human_keeps_pending_handwriting_unprocessed(self):
        print("\n■ human 导出：pending HANDWRITING 保持人工确认（不自动涂黑）")
        pdf = _make_pdf(self.workdir / "human_hand.pdf", pages=1)
        doc = _make_doc(pdf, pages=1)
        block = doc.pages[0].blocks[0]
        handwriting = _pending_handwriting(block.id, block.text)
        job = _make_job(pdf, doc, [handwriting])
        out = self.workdir / "human_hand_out.pdf"
        info = jobs.JobManager()._export_to(job, "human", out, do_verify=False)
        ratio = _black_ratio(out, block.bbox)
        print(f"  human 手写区域黑像素占比={ratio:.3f} "
              f"decision={handwriting.decision}")
        self.assertIsNone(handwriting.decision,
                          "human 版不改 pending 手写决策")
        self.assertLess(ratio, 0.5, "human 版 pending 手写不应自动涂黑")
        self.assertFalse(any("疑似手写签名按涂黑" in w
                             for w in (info.get("warnings") or [])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
