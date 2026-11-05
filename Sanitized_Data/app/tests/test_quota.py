# -*- coding: utf-8 -*-
"""授权门禁：2026-11-05 起取消免费版，只有 3 天全功能试用和付费授权。"""
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


class TestTrialOnlyGate(unittest.TestCase):
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

    def test_free_without_trial_is_blocked(self):
        path = self.data_dir / "a.pdf"
        _blank_pdf(path, 1)
        with self.assertRaises(LicenseError) as ctx:
            quota.precheck(path, "pdf", None, service=self.svc)
        self.assertEqual(ctx.exception.code, "license_required")
        self.assertIn("不提供免费使用", ctx.exception.message)
        with self.assertRaises(LicenseError) as ctx2:
            quota.consume_job(self._job(), service=self.svc)
        self.assertEqual(ctx2.exception.code, "license_required")

    def test_free_quota_status_has_no_daily_quota(self):
        q = self.svc.quota_status()
        self.assertFalse(q["unlimited"])
        self.assertEqual(q["limit_today"], 0)
        self.assertEqual(q["remaining"], 0)

    def test_trial_is_unlimited_and_allows_packs(self):
        self.svc.start_trial()
        q = self.svc.quota_status()
        self.assertTrue(q["unlimited"])
        self.assertIsNone(q["remaining"])
        path = self.data_dir / "a.pdf"
        _blank_pdf(path, 3)
        packs = quota.precheck(path, "pdf", ["legal-cn"], service=self.svc)
        self.assertEqual(packs, ["legal-cn"])

    def test_after_trial_is_blocked_until_purchase(self):
        self.svc.start_trial()
        self.now = self.now + timedelta(days=4)
        status = self.svc.status()
        self.assertEqual(status["state"], "free")
        self.assertTrue(status["trial"]["used"])
        path = self.data_dir / "a.pdf"
        _blank_pdf(path, 1)
        with self.assertRaises(LicenseError) as ctx:
            quota.precheck(path, "pdf", None, service=self.svc)
        self.assertEqual(ctx.exception.code, "license_required")
        self.assertIn("试用已结束", ctx.exception.message)

    def test_visible_packs_empty_when_unlicensed(self):
        index = [{"id": "common", "count": 18}, {"id": "legal-cn", "count": 29}]
        self.assertEqual(quota.visible_packs(index, service=self.svc), [])

    def test_mcp_resources_gated_for_free(self):
        """免费态 resources/list|read 必须返回 JSON-RPC 错误，不泄露任务数据。"""
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
