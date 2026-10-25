# -*- coding: utf-8 -*-
"""W4a 人工签发工具测试：payload/sign/导入闭环。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from yinzhan.licensing import keys, tiers  # noqa: E402
from yinzhan.licensing.issue import (  # noqa: E402
    build_payload, normalize_machine_code, sign_license,
)
from yinzhan.licensing.service import LicenseError, LicenseService  # noqa: E402


class TestIssue(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="yz_issue_")
        self.data_dir = Path(self._tmp.name)
        self.now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
        self.private = Ed25519PrivateKey.generate()
        self._keys_old = dict(keys.PUBLIC_KEYS)
        keys.PUBLIC_KEYS.clear()
        keys.PUBLIC_KEYS["test-2026"] = self.private.public_key().public_bytes_raw()
        self.svc = LicenseService(self.data_dir, now=lambda: self.now)
        self.svc._machine_cache = {"code": "K7Q29WXM4TRBEFGH",
                                   "display": "K7Q2-9WXM-4TRB-EFGH",
                                   "source": "test"}

    def tearDown(self):
        keys.PUBLIC_KEYS.clear()
        keys.PUBLIC_KEYS.update(self._keys_old)
        self._tmp.cleanup()

    def _issue(self, **overrides):
        params = dict(
            sku="personal", machine_codes=["K7Q2-9WXM-4TRB-EFGH"],
            order_id="ord_test_001", now=self.now,
        )
        params.update(overrides)
        payload = build_payload(**params)
        return sign_license(payload, self.private, "test-2026")

    def test_personal_roundtrip(self):
        container = self._issue()
        result = self.svc.import_license(container)
        self.assertEqual(result["state"], "paid")
        self.assertEqual(result["sku"], "personal")
        self.assertIn("all_packs", result["features"])
        self.assertEqual(result["license"]["order_id"], "ord_test_001")

    def test_pro_has_mcp(self):
        container = self._issue(sku="pro")
        result = self.svc.import_license(container)
        self.assertEqual(result["state"], "paid")
        self.assertIn("mcp", result["features"])
        self.assertEqual(result["license"]["max_devices"], 2)

    def test_team_requires_team_id(self):
        with self.assertRaises(ValueError):
            build_payload(sku="team", machine_codes=["K7Q2-9WXM-4TRB-EFGH"],
                          order_id="ord_test_002", now=self.now)
        payload = build_payload(
            sku="team", machine_codes=["K7Q2-9WXM-4TRB-EFGH"],
            order_id="ord_test_003", team_id="team_test_01", seats=5,
            now=self.now,
        )
        container = sign_license(payload, self.private, "test-2026")
        self.assertEqual(self.svc.import_license(container)["state"], "paid")

    def test_machine_code_format(self):
        self.assertEqual(normalize_machine_code("k7q29wxm4trbefgh"),
                         "K7Q2-9WXM-4TRB-EFGH")
        with self.assertRaises(ValueError):
            normalize_machine_code("abc")

    def test_expired_or_invalid(self):
        with self.assertRaises(ValueError):
            build_payload(sku="personal", machine_codes=["K7Q2-9WXM-4TRB-EFGH"],
                          order_id="ord_test_004", now=self.now,
                          expires_at=(self.now - timedelta(days=1)).isoformat())
        with self.assertRaises(ValueError):
            build_payload(sku="free", machine_codes=["K7Q2-9WXM-4TRB-EFGH"],
                          order_id="ord_test_005", now=self.now)

    def test_wrong_machine_rejected_on_import(self):
        payload = build_payload(
            sku="personal", machine_codes=["AAAA-BBBB-CCCC-DDDD"],
            order_id="ord_test_006", now=self.now,
        )
        container = sign_license(payload, self.private, "test-2026")
        with self.assertRaises(LicenseError) as ctx:
            self.svc.import_license(container)
        self.assertEqual(ctx.exception.code, "license_machine_mismatch")

    def test_perpetual(self):
        container = self._issue(term="perpetual")
        result = self.svc.import_license(container)
        self.assertEqual(result["state"], "paid")
        self.assertEqual(result["term"], "perpetual")


if __name__ == "__main__":
    unittest.main(verbosity=2)
