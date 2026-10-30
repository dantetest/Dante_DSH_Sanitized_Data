# -*- coding: utf-8 -*-
"""客户端更新检查/下载/安全校验单元测试（不启动安装）。"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import updater  # noqa: E402


class TestUpdater(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="yz_update_")
        self.data_dir = Path(self._tmp.name)
        self._old_data = updater.config.DATA_DIR
        updater.config.DATA_DIR = self.data_dir
        self.mgr = updater.UpdateManager()

    def tearDown(self):
        updater.config.DATA_DIR = self._old_data
        self._tmp.cleanup()

    def _make_zip(self) -> Path:
        path = self.data_dir / "update.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Sanitized_Data/Sanitized_Data.exe", b"fake exe")
            zf.writestr("Sanitized_Data/_internal/data.txt", b"data")
        return path

    def test_version_compare(self):
        self.assertTrue(updater._is_newer("1.3.4", "1.3.3"))
        self.assertFalse(updater._is_newer("1.3.3", "1.3.3"))
        self.assertTrue(updater._is_newer("v1.4.0", "1.3.9"))

    def test_check_available(self):
        z = self._make_zip()
        manifest = {
            "version": "9.9.9",
            "notes": "test release",
            "assets": {"windows_portable": {
                "name": z.name, "url": "https://example.com/update.zip",
                "size": z.stat().st_size,
                "sha256": hashlib.sha256(z.read_bytes()).hexdigest().upper(),
            }},
        }
        with mock.patch.object(updater, "_http_get_json", return_value=manifest):
            data = self.mgr.check()
        self.assertTrue(data["available"])
        self.assertEqual(data["version"], "9.9.9")
        self.assertEqual(data["asset"]["kind"], "portable")
        self.assertEqual(data["asset"]["url"], "https://example.com/update.zip")
        self.assertEqual(data["phase"], "available")

    def test_check_no_update(self):
        manifest = {"version": updater.config.VERSION, "assets": {}}
        with mock.patch.object(updater, "_http_get_json", return_value=manifest):
            data = self.mgr.check()
        self.assertFalse(data["available"])
        self.assertEqual(data["phase"], "no_update")

    def test_download_verifies_hash(self):
        z = self._make_zip()
        digest = hashlib.sha256(z.read_bytes()).hexdigest().upper()
        # 直接用 file:// 作为本地 HTTP 替身，测试下载/校验逻辑
        self.mgr._set(phase="available", available=True, version="9.9.9",
                      asset={"name": z.name, "url": z.as_uri(),
                             "size": z.stat().st_size, "sha256": digest,
                             "kind": "portable"},
                      total=z.stat().st_size)
        self.mgr.start_download()
        for _ in range(100):
            if self.mgr.status()["phase"] in ("ready", "error"):
                break
            time.sleep(0.02)
        data = self.mgr.status()
        self.assertEqual(data["phase"], "ready", data)
        self.assertEqual(data["progress"], 100)
        self.assertTrue((self.data_dir / "updates" / "9.9.9" / z.name).is_file())

    def test_source_mode_install_rejected(self):
        self.mgr._set(phase="ready", asset={"path": str(self._make_zip()), "kind": "portable"})
        data = self.mgr.install()
        self.assertEqual(data["phase"], "error")
        self.assertIn("源码模式", data["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
