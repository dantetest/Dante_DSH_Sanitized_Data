# -*- coding: utf-8 -*-
"""flow 格式手动补漏回归（2026-11-05 客户反馈修复）。

覆盖：
1. DOCX 手动选中文字 -> 生成 text span -> human 导出真的隐藏；
2. DOCX 只传矩形 -> 明确拒绝（不再静默生成空实体）；
3. 选中文字不存在 -> 明确报错；
4. PDF 矩形框选仍生效（不回归）；
5. 前端 flow 分支确实发送 value（静态断言）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="yz_manual_flow_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import registry  # noqa: E402
from yinzhan.jobs import JobManager  # noqa: E402
from yinzhan.udm import YinzhanError  # noqa: E402

SAMPLE_DOCX = REPO / "samples" / "住院病历.docx"


def _wait(job, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        if job.stage in ("ready", "failed") or job.error:
            break
        time.sleep(0.15)
    if job.stage != "ready":
        raise AssertionError(f"job not ready: {job.stage} {job.error}")


def _docx_text(path: Path) -> str:
    import docx
    return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)


def _make_pdf(path: Path) -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "手动框选回归标题", fontname="china-s", fontsize=14)
    page.insert_text((72, 130), "正文测试内容", fontname="china-s", fontsize=12)
    doc.save(str(path))
    doc.close()


class ManualFlowMarkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()

    def _new_docx_job(self):
        mgr = JobManager()
        job = mgr.create(SAMPLE_DOCX)
        _wait(job)
        return mgr, job

    def test_docx_manual_text_value_hides_text(self):
        mgr, job = self._new_docx_job()
        value = "住院病历首页"
        res = mgr.mark(job.id, {"page": 0, "value": value, "etype": "OTHER",
                                "label": "手动文字补漏", "propagate": True})
        self.assertEqual(res["added"], 1)
        manual = [e for e in job.entities if e.origin == "manual"]
        self.assertTrue(manual)
        entity = manual[-1]
        self.assertEqual(entity.value, value)
        self.assertTrue(entity.spans)
        self.assertTrue(all(s.is_text for s in entity.spans))

        out = Path(mgr.export(job.id, "human")["out"])
        self.assertTrue(out.is_file())
        self.assertNotIn(value, _docx_text(out))

    def test_docx_rect_only_is_rejected(self):
        mgr, job = self._new_docx_job()
        with self.assertRaises(YinzhanError) as ctx:
            mgr.mark(job.id, {"page": 0, "rect": [0.0, 0.0, 1.0, 1.0],
                              "etype": "OTHER"})
        self.assertIn("矩形", str(ctx.exception))
        self.assertFalse([e for e in job.entities
                          if e.origin == "manual" and not e.value])

    def test_docx_missing_value_is_rejected(self):
        mgr, job = self._new_docx_job()
        with self.assertRaises(YinzhanError) as ctx:
            mgr.mark(job.id, {"page": 0, "value": "不存在的文字XYZ",
                              "etype": "OTHER"})
        self.assertIn("未在正文定位", str(ctx.exception))

    def test_pdf_rect_mark_still_works(self):
        pdf = TMP / "manual_rect.pdf"
        _make_pdf(pdf)
        mgr = JobManager()
        job = mgr.create(pdf)
        _wait(job)
        res = mgr.mark(job.id, {"page": 0, "rect": [0.0, 0.0, 1.0, 1.0],
                                "etype": "OTHER", "label": "手动矩形"})
        self.assertEqual(res["added"], 1)
        out = Path(mgr.export(job.id, "human")["out"])
        import fitz
        with fitz.open(str(out)) as doc:
            text = "".join(page.get_text("text") for page in doc)
        self.assertNotIn("手动框选回归标题", text)

    def test_legacy_rect_manual_entity_warns_not_silent(self):
        """历史任务里已有的空 value 矩形实体：导出至少要有明确 warning。"""
        from yinzhan.udm import Decision, Entity, Span
        mgr, job = self._new_docx_job()
        job.entities.append(Entity(
            id=Entity.new_id(), etype="OTHER", label="旧矩形补漏", value="",
            spans=[Span(rect=(1.0, 1.0, 20.0, 20.0), page=0)],
            level="L2", confidence=1.0, origin="manual", status="accepted",
            decision=Decision(strategy="black", black=True),
        ))
        exp = mgr.export(job.id, "human")
        warnings = exp.get("warnings") or []
        self.assertTrue(
            any("手动框选无法映射" in str(w) for w in warnings),
            f"expected manual-unmapped warning, got {warnings}",
        )

    def test_frontend_flow_sends_selected_value(self):
        js = (REPO / "app" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("draft.selText = picked", js)
        self.assertIn("payload.value = picked", js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
