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


if __name__ == "__main__":
    unittest.main(verbosity=2)
