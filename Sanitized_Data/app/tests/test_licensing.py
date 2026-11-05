# -*- coding: utf-8 -*-
"""W1 离线授权单元测试：machine code / Ed25519 / trial / 状态。"""
from __future__ import annotations

import base64
import json
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

from yinzhan.licensing import keys, machine, tiers  # noqa: E402
from yinzhan.licensing.service import (  # noqa: E402
    LicenseError, LicenseService, TRIAL_DAYS, canonical_payload, sign_payload,
)


class Clock:
    def __init__(self, dt: datetime):
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt

    def advance(self, days: float = 0, hours: float = 0) -> None:
        self.dt = self.dt + timedelta(days=days, hours=hours)


class TestMachineCode(unittest.TestCase):
    def test_format_and_stability(self):
        with mock.patch.object(machine, "_components_for_platform",
                               return_value=("AAA", "BBB", "test")):
            one = machine.machine_info()
            two = machine.machine_info()
        self.assertEqual(one["code"], two["code"])
        self.assertRegex(one["display"], r"^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")
        self.assertEqual(one["display"].replace("-", ""), one["code"])

    def test_different_components_differ(self):
        with mock.patch.object(machine, "_components_for_platform",
                               return_value=("AAA", "BBB", "test")):
            a = machine.machine_code()
        with mock.patch.object(machine, "_components_for_platform",
                               return_value=("AAA", "CCC", "test")):
            b = machine.machine_code()
        self.assertNotEqual(a, b)


class TestLicenseService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="yz_license_")
        self.data_dir = Path(self._tmp.name)
        self.now = Clock(datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc))
        self.private = Ed25519PrivateKey.generate()
        self._keys_old = dict(keys.PUBLIC_KEYS)
        keys.PUBLIC_KEYS.clear()
        keys.PUBLIC_KEYS["test-2026"] = self.private.public_key().public_bytes_raw()
        self.svc = LicenseService(self.data_dir, now=self.now)
        # 固定机器码，避免测试受真实硬件影响
        self.svc._machine_cache = {"code": "K7Q29WXM4TRBEFGH",
                                   "display": "K7Q2-9WXM-4TRB-EFGH",
                                   "source": "test"}

    def tearDown(self):
        keys.PUBLIC_KEYS.clear()
        keys.PUBLIC_KEYS.update(self._keys_old)
        self._tmp.cleanup()

    def _payload(self, **overrides):
        now = self.now.dt
        payload = {
            "license_id": "lic_test_001",
            "order_id": "ord_test_001",
            "sku": "personal",
            "edition": tiers.TIERS["personal"]["edition"],
            "term": "annual",
            "seats": 1,
            "max_devices": 1,
            "machine_codes": [self.svc.machine_code()],
            "customer": {"name": "测试用户", "org": None, "note": ""},
            "issued_at": now.isoformat(),
            "not_before": now.isoformat(),
            "expires_at": (now + timedelta(days=365)).isoformat(),
            "updates_until": (now + timedelta(days=365)).isoformat(),
            "grace_days": 14,
            "features": list(tiers.PERSONAL_FEATURES),
            "team_id": None,
            "issuer": "BLCTEK",
            "notes": "",
        }
        payload.update(overrides)
        return payload

    def _license(self, **overrides):
        payload = self._payload(**overrides)
        return {
            "schema": 1, "alg": "Ed25519", "key_id": "test-2026",
            "payload": payload, "sig": sign_payload(payload, self.private),
        }

    def test_trial_once_and_expiry(self):
        info = self.svc.status()
        self.assertEqual(info["state"], "free")
        self.assertFalse(info["trial"]["used"])
        self.assertEqual(TRIAL_DAYS, 3)
        trial = self.svc.start_trial()
        self.assertEqual(trial["state"], "trial")
        self.assertGreaterEqual(trial["trial"]["days_left"], TRIAL_DAYS - 1)
        self.assertIn("trial_start", (self.data_dir / "license" / "events.jsonl").read_text(encoding="utf-8"))
        # 幂等
        again = self.svc.start_trial()
        self.assertEqual(again["state"], "trial")
        self.now.advance(days=TRIAL_DAYS + 1)
        expired = self.svc.status()
        self.assertEqual(expired["state"], "free")
        with self.assertRaises(LicenseError):
            self.svc.start_trial()

    def test_import_annual_and_status(self):
        result = self.svc.import_license(self._license())
        self.assertEqual(result["state"], "paid")
        self.assertEqual(result["sku"], "personal")
        self.assertIn("all_packs", result["features"])
        self.assertIn("mcp", result["features"])
        self.assertTrue((self.data_dir / "license" / "license.json").is_file())
        self.assertIn("license_import", (self.data_dir / "license" / "events.jsonl").read_text(encoding="utf-8"))
        # 重新构造 service 也能读
        svc2 = LicenseService(self.data_dir, now=self.now)
        svc2._machine_cache = dict(self.svc._machine_cache)
        self.assertEqual(svc2.status()["state"], "paid")

    def test_legacy_personal_license_still_gets_mcp(self):
        """2026-11-05 MCP 归入个人版：旧个人版签名缺 mcp 时本地兼容补上。"""
        features = [f for f in tiers.PERSONAL_FEATURES if f != "mcp"]
        legacy = self._license(
            license_id="lic_legacy_001", order_id="ord_legacy_001", features=features)
        result = self.svc.import_license(legacy)
        self.assertEqual(result["sku"], "personal")
        self.assertIn("mcp", result["features"])

    def test_import_rejects_wrong_machine(self):
        lic = self._license(machine_codes=["AAAA-BBBB-CCCC-DDDD"])
        with self.assertRaises(LicenseError) as ctx:
            self.svc.import_license(lic)
        self.assertEqual(ctx.exception.code, "license_machine_mismatch")
        self.assertFalse((self.data_dir / "license" / "license.json").exists())

    def test_tampered_payload_rejected(self):
        lic = self._license()
        lic["payload"]["seats"] = 99
        with self.assertRaises(LicenseError) as ctx:
            self.svc.import_license(lic)
        self.assertEqual(ctx.exception.code, "license_invalid")

    def test_unknown_key_rejected(self):
        lic = self._license()
        lic["key_id"] = "unknown"
        with self.assertRaises(LicenseError):
            self.svc.import_license(lic)

    def test_expired_import_rejected(self):
        old = self.now.dt - timedelta(days=400)
        lic = self._license(
            issued_at=old.isoformat(), not_before=old.isoformat(),
            expires_at=(old + timedelta(days=365)).isoformat(),
            updates_until=(old + timedelta(days=365)).isoformat(),
        )
        with self.assertRaises(LicenseError) as ctx:
            self.svc.import_license(lic)
        self.assertEqual(ctx.exception.code, "license_expired")

    def test_grace_and_perpetual(self):
        expired = self.now.dt - timedelta(days=2)
        lic = self._license(
            issued_at=(expired - timedelta(days=365)).isoformat(),
            not_before=(expired - timedelta(days=365)).isoformat(),
            expires_at=expired.isoformat(),
            updates_until=expired.isoformat(),
        )
        result = self.svc.import_license(lic)
        self.assertEqual(result["state"], "grace")
        self.assertGreaterEqual(result["license"]["grace_days_left"], 1)

    def test_perpetual(self):
        lic = self._license(term="perpetual", expires_at=None, updates_until=None)
        result = self.svc.import_license(lic)
        self.assertEqual(result["state"], "paid")
        self.assertEqual(result["term"], "perpetual")

    def test_clock_rollback_does_not_extend_trial(self):
        self.svc.start_trial()
        before = self.svc.status()["trial"]["days_left"]
        self.now.advance(hours=-72)  # 回拨 3 天
        after = self.svc.status()
        self.assertTrue(after["clock_suspect"])
        self.assertLessEqual(after["trial"]["days_left"], before)

    def test_state_corruption_does_not_grant_trial(self):
        self.svc.start_trial()
        state_path = self.data_dir / "license" / "state.json"
        state_path.write_text('{"schema":1,"trial":null}', encoding="utf-8")
        status = self.svc.status()
        self.assertEqual(status["state"], "free")
        self.assertTrue(status["trial"]["used"] is False)  # 状态损坏不恢复试用资格
        self.assertNotEqual(status["state"], "trial")


if __name__ == "__main__":
    unittest.main(verbosity=2)
