# -*- coding: utf-8 -*-
"""core 公钥 + 签名 keyring 测试。"""
from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan.licensing import keys  # noqa: E402
from yinzhan.licensing.service import LicenseError, LicenseService  # noqa: E402

MANIFEST = Path(keys.__file__).with_name("public_keys.json")


class TestSignedKeyring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="yz_keyring_")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_production_keyring_verifies(self):
        self.assertTrue(keys.KEYRING_OK)
        self.assertIn("prod-2026", keys.PUBLIC_KEYS)
        self.assertIn("prod-2026b", keys.PUBLIC_KEYS)
        self.assertEqual(len(keys.PUBLIC_KEYS["prod-2026b"]), 32)

    def test_tampered_public_key_rejected(self):
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        raw["keys"]["prod-2026b"] = base64.b64encode(b"x" * 32).decode("ascii")
        path = self.tmp / "bad.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        ok, error = keys.verify_keyring_file(path)
        self.assertFalse(ok)
        self.assertTrue(error)

    def test_tampered_signature_rejected(self):
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        raw["sig"] = base64.b64encode(b"x" * 64).decode("ascii")
        path = self.tmp / "bad.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        ok, error = keys.verify_keyring_file(path)
        self.assertFalse(ok)
        self.assertTrue(error)

    def test_old_flat_format_rejected(self):
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        path = self.tmp / "flat.json"
        path.write_text(json.dumps(raw["keys"], ensure_ascii=False), encoding="utf-8")
        ok, error = keys.verify_keyring_file(path)
        self.assertFalse(ok)
        self.assertTrue(error)

    def test_invalid_keyring_blocks_license_with_human_message(self):
        svc = LicenseService(self.tmp / "data")
        old_ok, old_keys = keys.KEYRING_OK, dict(keys.PUBLIC_KEYS)
        try:
            keys.KEYRING_OK = False
            keys.PUBLIC_KEYS.clear()
            with self.assertRaises(LicenseError) as ctx:
                svc._verify_container("{}")
            self.assertEqual(ctx.exception.code, "keyring_invalid")
            self.assertEqual(ctx.exception.message,
                             "软件组件可能损坏，请使用官方安装包重新安装")
            self.assertNotIn("公钥文件", ctx.exception.message)
        finally:
            keys.KEYRING_OK = old_ok
            keys.PUBLIC_KEYS.clear()
            keys.PUBLIC_KEYS.update(old_keys)

    def test_add_public_key_rejected_when_frozen(self):
        import unittest.mock as mock
        with mock.patch.object(keys, "_is_frozen", return_value=True):
            with self.assertRaises(RuntimeError):
                keys.add_public_key("attacker", b"x" * 32)

    def test_missing_keyring_rejected(self):
        ok, error = keys.verify_keyring_file(self.tmp / "missing.json")
        self.assertFalse(ok)
        self.assertTrue(error)

    def test_existing_prod_license_still_verifies(self):
        fixture = REPO / "out" / "audit" / "rotation_import_data" / "license" / "license.json"
        if not fixture.is_file():
            self.skipTest("生产测试 license 不存在")
        svc = LicenseService(self.tmp / "existing")
        svc._machine_cache = {"code": "ZH397N5C98CDA8GB",
                              "display": "ZH39-7N5C-98CD-A8GB",
                              "source": "test"}
        result = svc.import_license(fixture.read_text(encoding="utf-8"))
        self.assertEqual(result["state"], "paid")
        self.assertEqual(result["sku"], "personal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
