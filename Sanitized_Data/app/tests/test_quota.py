# -*- coding: utf-8 -*-
"""W2 免费额度测试：格式/大小/页数/规则包/每日 3 份/退款/试用不限。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import quota  # noqa: E402
from yinzhan.licensing.service import LicenseError, LicenseService  # noqa: E402


def _blank_pdf(path: Path, pages: int) -> None:
    import fitz
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


class TestFreeQuota(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="yz_quota_")
        self.data_dir = Path(self._tmp.name)
        self.now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
        self.svc = LicenseService(self.data_dir, now=lambda: self.now)
        self.svc._machine_cache = {"code": "K7Q29WXM4TRBEFGH",
                                   "display": "K7Q2-9WXM-4TRB-EFGH",
                                   "source": "test"}
        self._old = os.environ.get("YINZHAN_ENFORCE_FREE_TIER")
        os.environ["YINZHAN_ENFORCE_FREE_TIER"] = "1"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("YINZHAN_ENFORCE_FREE_TIER", None)
        else:
            os.environ["YINZHAN_ENFORCE_FREE_TIER"] = self._old
        self._tmp.cleanup()

    def _job(self):
        return SimpleNamespace(quota_charged=False, quota_refunded=False,
                               quota_free=False, quota_detect_started=False)

    def test_precheck_free_normal_pdf(self):
        path = self.data_dir / "a.pdf"
        _blank_pdf(path, 2)
        packs = quota.precheck(path, "pdf", None, service=self.svc)
        self.assertEqual(packs, ["common"])

    def test_precheck_paid_pack_rejected(self):
        path = self.data_dir / "a.pdf"
        _blank_pdf(path, 1)
        with self.assertRaises(LicenseError) as ctx:
            quota.precheck(path, "pdf", ["legal-cn"], service=self.svc)
        self.assertEqual(ctx.exception.code, "pack_requires_paid")

    def test_precheck_tif_rejected(self):
        path = self.data_dir / "a.tif"
        path.write_bytes(b"II*\x00")
        with self.assertRaises(LicenseError) as ctx:
            quota.precheck(path, "tiff", None, service=self.svc)
        self.assertEqual(ctx.exception.code, "free_format")

    def test_precheck_page_limit_pre_parse(self):
        path = self.data_dir / "long.pdf"
        _blank_pdf(path, 21)
        with self.assertRaises(LicenseError) as ctx:
            quota.precheck(path, "pdf", None, service=self.svc)
        self.assertEqual(ctx.exception.code, "free_page_limit")

    def test_daily_three_then_quota_exceeded_and_refund(self):
        path = self.data_dir / "a.pdf"
        _blank_pdf(path, 1)
        for _ in range(3):
            job = self._job()
            quota.consume_job(job, service=self.svc)
            self.assertTrue(job.quota_charged)
        q = self.svc.quota_status()
        self.assertEqual(q["used_today"], 3)
        self.assertEqual(q["remaining"], 0)
        with self.assertRaises(LicenseError) as ctx:
            quota.consume_job(self._job(), service=self.svc)
        self.assertEqual(ctx.exception.code, "quota_exceeded")
        # 退款只退一次，且不能变成负数
        job = self._job()
        job.quota_charged = True
        quota.refund_job(job, service=self.svc)
        quota.refund_job(job, service=self.svc)
        self.assertEqual(self.svc.quota_status()["used_today"], 2)

    def test_trial_unlimited_and_pack_allowed(self):
        self.svc.start_trial()
        q = self.svc.quota_status()
        self.assertTrue(q["unlimited"])
        path = self.data_dir / "a.pdf"
        _blank_pdf(path, 3)
        packs = quota.precheck(path, "pdf", ["legal-cn"], service=self.svc)
        self.assertEqual(packs, ["legal-cn"])

    def test_after_parse_page_limit_refunds(self):
        job = self._job()
        quota.consume_job(job, service=self.svc)
        self.assertEqual(self.svc.quota_status()["used_today"], 1)
        with self.assertRaises(LicenseError) as ctx:
            quota.after_parse(job, 21, service=self.svc)
        self.assertEqual(ctx.exception.code, "free_page_limit")
        self.assertTrue(job.quota_refunded)
        self.assertEqual(self.svc.quota_status()["used_today"], 0)

    def test_daily_reset_on_next_local_day(self):
        for _ in range(3):
            quota.consume_job(self._job(), service=self.svc)
        self.assertEqual(self.svc.quota_status()["remaining"], 0)
        self.now = self.now + timedelta(days=1)
        q = self.svc.quota_status()
        self.assertEqual(q["used_today"], 0)
        self.assertEqual(q["remaining"], 3)

    def test_after_parse_ocr_limit_refunds(self):
        job = self._job()
        quota.consume_job(job, service=self.svc)
        with self.assertRaises(LicenseError) as ctx:
            quota.after_parse(job, 6, ocr_pages=11, service=self.svc)
        self.assertEqual(ctx.exception.code, "free_ocr_limit")
        self.assertTrue(job.quota_refunded)
        self.assertEqual(self.svc.quota_status()["used_today"], 0)

    def test_visible_packs_free_only_common(self):
        index = [{"id": "common", "count": 18}, {"id": "legal-cn", "count": 29}]
        visible = quota.visible_packs(index, service=self.svc)
        self.assertEqual([p["id"] for p in visible], ["common"])

    # ── 2026-10-25 QA 回归：H-01/H-02/H-03 ──
    def test_state_tamper_recovers_as_one_used(self):
        """H-02：state.json 损坏/被删不能刷满额度，按「今日已用 1 份」恢复。"""
        for _ in range(3):
            quota.consume_job(self._job(), service=self.svc)
        self.assertEqual(self.svc.quota_status()["remaining"], 0)
        # 损坏：保留野内容（验签失败）
        self.svc.state_path.write_text('{"schema": 1, "usage": {"jobs": 0}}',
                                       encoding="utf-8")
        q = self.svc.quota_status()
        self.assertEqual(q["used_today"], 1, "损坏 state 应按已用 1 份恢复")
        self.assertEqual(q["remaining"], 2)
        self.assertTrue(q["state_recovered"])
        status = self.svc.status()
        self.assertTrue(status["state_recovered"], "授权状态需暴露 state_recovered 供 UI 提示")
        # 删除：同样按已用 1 份恢复（不能清零）
        self.svc.state_path.unlink()
        q2 = self.svc.quota_status()
        self.assertEqual(q2["used_today"], 1)
        self.assertTrue(q2["state_recovered"])

    def test_docx_page_estimate_applies_free_page_limit(self):
        """H-01：DOCX 只有 1 个逻辑 Page，免费 20 页限额需按显式分页符估算。"""
        import docx
        from yinzhan.parsers.docx_parser import DocxParser
        from yinzhan.udm import ParseContext

        path = self.data_dir / "long.docx"
        document = docx.Document()
        for index in range(24):
            document.add_paragraph(f"第 {index + 1} 页 联系人 张伟 电话 13800138000")
            document.add_page_break()
        document.add_paragraph("最后一页")
        document.save(str(path))

        doc = DocxParser().parse(path, ParseContext())
        self.assertEqual(len(doc.pages), 1, "docx 解析器仍是 1 个逻辑 Page")
        self.assertEqual(doc.meta.get("page_estimate"), 25, "24 个分页符 → 估算 25 页")
        job = self._job()
        quota.consume_job(job, service=self.svc)
        effective_pages = max(len(doc.pages), int(doc.meta["page_estimate"]))
        with self.assertRaises(LicenseError) as ctx:
            quota.after_parse(job, effective_pages, service=self.svc)
        self.assertEqual(ctx.exception.code, "free_page_limit")
        self.assertTrue(job.quota_refunded, "超页拒绝要退还额度")

    def test_mcp_resources_gated_for_free(self):
        """H-03：免费态 resources/list|read 必须返回 JSON-RPC 错误，不泄露任务数据。"""
        import mcp_server
        with mock.patch.object(quota, "is_enforced", return_value=True), \
                mock.patch.object(quota.SERVICE, "has_feature", return_value=False):
            resp = mcp_server.handle({"jsonrpc": "2.0", "id": 1,
                                      "method": "resources/list", "params": {}})
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32002)
            self.assertIn("license_required", resp["error"]["message"])
            resp_read = mcp_server.handle({"jsonrpc": "2.0", "id": 2,
                                           "method": "resources/read",
                                           "params": {"uri": "sanitize://jobs"}})
            self.assertIn("error", resp_read)
            self.assertEqual(resp_read["error"]["code"], -32002)
        # 试用/付费有 mcp 功能位时门禁放行（只测门禁函数本身，不依赖后端）
        with mock.patch.object(quota, "is_enforced", return_value=True), \
                mock.patch.object(quota.SERVICE, "has_feature", return_value=True):
            mcp_server._require_resource_entitlement()      # 不应抛错


if __name__ == "__main__":
    unittest.main(verbosity=2)
