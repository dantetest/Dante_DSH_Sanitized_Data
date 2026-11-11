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


class TestPlatformIsolation(unittest.TestCase):
    """v1.5.3：Windows 只更新 Windows 包，macOS 只更新 macOS 包（含架构区分）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="yz_update_plat_")
        self._old_data = updater.config.DATA_DIR
        updater.config.DATA_DIR = Path(self._tmp.name)
        self.mgr = updater.UpdateManager()

    def tearDown(self):
        updater.config.DATA_DIR = self._old_data
        self._tmp.cleanup()

    @staticmethod
    def _asset(name, size=1024):
        return {"name": name, "url": "https://example.com/" + name,
                "size": size, "sha256": "A" * 64}

    def _manifest(self, **assets):
        return {"version": "9.9.9", "notes": "test", "release_url": "https://example.com/download",
                "assets": assets}

    def _check(self, manifest, platform_id, arch):
        with mock.patch.object(updater, "_platform_id", return_value=platform_id), \
                mock.patch.object(updater, "_arch_id", return_value=arch), \
                mock.patch.object(updater, "_http_get_json", return_value=manifest):
            return self.mgr.check()

    # ── Windows ──
    def test_windows_prefers_portable_auto(self):
        m = self._manifest(windows_portable=self._asset("Sanitized_Data-9.9.9-win64-portable.zip"),
                           windows_installer=self._asset("Sanitized_Data_Setup_9.9.9_x64.exe"))
        data = self._check(m, "windows", "x64")
        self.assertTrue(data["available"])
        self.assertEqual(data["asset"]["asset_key"], "windows_portable")
        self.assertEqual(data["asset"]["kind"], "portable")
        self.assertEqual(data["mode"], "auto")
        self.assertFalse(data["manual"])
        self.assertEqual(data["platform"], "windows")

    def test_windows_installer_only_is_manual(self):
        m = self._manifest(windows_installer=self._asset("Sanitized_Data_Setup_9.9.9_x64.exe"))
        data = self._check(m, "windows", "x64")
        self.assertTrue(data["available"])
        self.assertEqual(data["asset"]["kind"], "installer")
        self.assertEqual(data["mode"], "manual")
        self.assertTrue(data["manual"])
        self.assertIn("手动", data["error"])
        # 手动模式不允许应用内自动下载
        after = self.mgr.start_download()
        self.assertEqual(after["phase"], "available")
        self.assertNotEqual(after["phase"], "downloading")

    def test_windows_never_takes_macos_asset(self):
        m = self._manifest(macos_arm64=self._asset("Sanitized_Data-9.9.9-arm64.dmg"),
                           macos_x64=self._asset("Sanitized_Data-9.9.9-x64.dmg"))
        data = self._check(m, "windows", "x64")
        self.assertFalse(data["available"])
        self.assertEqual(data["phase"], "error")
        self.assertIn("Windows x64", data["error"])

    def test_windows_rejects_dmg_named_portable(self):
        # 清单把 .dmg 错填到 windows_portable 键：扩展名交叉校验必须拒绝
        m = self._manifest(windows_portable=self._asset("Sanitized_Data-9.9.9-arm64.dmg"))
        data = self._check(m, "windows", "x64")
        self.assertFalse(data["available"])
        self.assertEqual(data["phase"], "error")

    # ── macOS ──
    def test_macos_arm64_picks_arm64_dmg(self):
        m = self._manifest(macos_arm64=self._asset("Sanitized_Data-9.9.9-arm64.dmg"),
                           macos_x64=self._asset("Sanitized_Data-9.9.9-x64.dmg"),
                           windows_portable=self._asset("Sanitized_Data-9.9.9-win64-portable.zip"),
                           windows_installer=self._asset("Sanitized_Data_Setup_9.9.9_x64.exe"))
        data = self._check(m, "macos", "arm64")
        self.assertTrue(data["available"])
        self.assertEqual(data["asset"]["asset_key"], "macos_arm64")
        self.assertEqual(data["asset"]["kind"], "dmg")
        self.assertEqual(data["mode"], "manual")
        self.assertTrue(data["manual"])
        self.assertIn("DMG", data["error"])
        self.assertEqual(data["platform_label"], "macOS Apple Silicon（arm64）")

    def test_macos_x64_picks_x64_dmg(self):
        m = self._manifest(macos_arm64=self._asset("Sanitized_Data-9.9.9-arm64.dmg"),
                           macos_x64=self._asset("Sanitized_Data-9.9.9-x64.dmg"))
        data = self._check(m, "macos", "x64")
        self.assertTrue(data["available"])
        self.assertEqual(data["asset"]["asset_key"], "macos_x64")

    def test_macos_never_takes_windows_asset(self):
        m = self._manifest(windows_portable=self._asset("Sanitized_Data-9.9.9-win64-portable.zip"),
                           windows_installer=self._asset("Sanitized_Data_Setup_9.9.9_x64.exe"))
        data = self._check(m, "macos", "arm64")
        self.assertFalse(data["available"])
        self.assertEqual(data["phase"], "error")
        self.assertIn("macOS", data["error"])

    def test_macos_missing_arch_asset_errors(self):
        # 只有 arm64 包时，Intel Mac 不能退而求其次拿 arm64 包
        m = self._manifest(macos_arm64=self._asset("Sanitized_Data-9.9.9-arm64.dmg"))
        data = self._check(m, "macos", "x64")
        self.assertFalse(data["available"])
        self.assertEqual(data["phase"], "error")

    def test_macos_rejects_unexpected_extension(self):
        m = self._manifest(macos_arm64=self._asset("Sanitized_Data-9.9.9-arm64.exe"))
        data = self._check(m, "macos", "arm64")
        self.assertFalse(data["available"])

    def test_macos_install_refuses(self):
        # 防御：即使状态被手工置为 ready，macOS 也不得执行 Windows 替换脚本
        with mock.patch.object(updater, "_platform_id", return_value="macos"):
            self.mgr._set(phase="ready", asset={"path": "x.zip", "kind": "portable"})
            data = self.mgr.install()
        self.assertEqual(data["phase"], "error")
        self.assertIn("不支持自动替换", data["error"])

    # ── 其它平台 / 无更新 ──
    def test_unknown_platform_errors(self):
        m = self._manifest(windows_portable=self._asset("Sanitized_Data-9.9.9-win64-portable.zip"))
        data = self._check(m, "other", "x64")
        self.assertFalse(data["available"])
        self.assertEqual(data["phase"], "error")
        self.assertIn("不在自动更新支持范围", data["error"])

    def test_same_version_reports_platform_fields(self):
        m = {"version": updater.config.VERSION, "assets": {}}
        data = self._check(m, "macos", "arm64")
        self.assertEqual(data["phase"], "no_update")
        self.assertEqual(data["platform"], "macos")
        self.assertEqual(data["arch"], "arm64")


if __name__ == "__main__":
    unittest.main(verbosity=2)
