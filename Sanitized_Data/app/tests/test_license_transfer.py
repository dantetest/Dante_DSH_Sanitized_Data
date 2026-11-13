# -*- coding: utf-8 -*-
"""授权转移/重绑 + legacy 机器码兼容回归。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from yinzhan.licensing import keys, tiers  # noqa: E402
from yinzhan.licensing.issue import build_payload, sign_license  # noqa: E402
from yinzhan.licensing.service import LicenseService  # noqa: E402
from yinzhan.licensing.transfer import (  # noqa: E402
    create_transfer_request, reissue_from_license, reissue_from_request,
)

OLD = "K7Q2-9WXM-4TRB-EFGH"
NEW = "ABCD-EFGH-JKMN-PQRS"


class TestLicenseTransfer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="yz_transfer_test_")
        self.private = Ed25519PrivateKey.generate()
        self._keys = dict(keys.PUBLIC_KEYS)
        self._keyring_ok = keys.KEYRING_OK
        keys.PUBLIC_KEYS.clear()
        keys.PUBLIC_KEYS["test-transfer"] = self.private.public_key().public_bytes_raw()
        keys.KEYRING_OK = True
        self.now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        self.payload = build_payload(
            sku="personal", machine_codes=[OLD], order_id="ord_transfer_001",
            license_id="lic_transfer_001", now=self.now,
        )
        self.container = sign_license(self.payload, self.private, "test-transfer")
        self.svc = LicenseService(Path(self._tmp.name) / "old", now=lambda: self.now)
        self.svc._machine_cache = {
            "code": "ABCDEFGHJKMNPQRS", "display": "NEWC-ODE0-0000-0001",
            "legacy_code": "K7Q29WXM4TRBEFGH", "codes": ["ABCDEFGHJKMNPQRS", "K7Q29WXM4TRBEFGH"],
            "source": "test", "legacy_display": "K7Q2-9WXM-4TRB-EFGH",
        }

    def tearDown(self):
        keys.PUBLIC_KEYS.clear()
        keys.PUBLIC_KEYS.update(self._keys)
        keys.KEYRING_OK = self._keyring_ok
        self._tmp.cleanup()

    def test_legacy_code_is_accepted(self):
        result = self.svc.import_license(self.container)
        self.assertEqual(result["state"], "paid")

    def test_transfer_request_and_reissue(self):
        req = create_transfer_request(self.container, NEW)
        self.assertEqual(req["type"], "license_transfer_request")
        self.assertEqual(req["old_license_id"], "lic_transfer_001")
        self.assertEqual(req["new_machine_code"], NEW)
        json.dumps(req, ensure_ascii=False)

        new_container = reissue_from_request(req, self.private, "test-transfer")
        new_payload = new_container["payload"]
        self.assertEqual(new_payload["order_id"], "ord_transfer_001")
        self.assertEqual(new_payload["license_id"], "lic_transfer_001")
        self.assertEqual(new_payload["machine_codes"], [NEW])
        self.assertEqual(new_payload["transfer_count"], 1)
        self.assertEqual(new_payload["reissued_from"], "lic_transfer_001")

        new_svc = LicenseService(Path(self._tmp.name) / "new", now=lambda: self.now)
        new_svc._machine_cache = {"code": "ABCDEFGHJKMNPQRS",
                                  "display": "NEWC-ODE0-0000-0001",
                                  "codes": ["ABCDEFGHJKMNPQRS"], "source": "test"}
        self.assertEqual(new_svc.import_license(new_container)["state"], "paid")

    def test_reissue_from_license_direct(self):
        new_container = reissue_from_license(
            self.container, NEW, self.private, "test-transfer")
        self.assertEqual(new_container["payload"]["machine_codes"], [NEW])
        self.assertGreaterEqual(new_container["payload"]["transfer_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
