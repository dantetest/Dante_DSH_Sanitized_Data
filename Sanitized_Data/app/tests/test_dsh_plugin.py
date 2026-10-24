# -*- coding: utf-8 -*-
"""DSH 检测插件静态契约：包结构、slot key、host 代理回环边界。"""
from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "integrations" / "dsh-plugin-sanitize-detect"


class TestDshPluginPackage(unittest.TestCase):
    def test_package_manifest(self):
        data = json.loads((PLUGIN / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "@blctek/dsh-plugin-sanitize-detect")
        self.assertEqual(data["type"], "module")
        self.assertEqual(data["dsh"]["bundle"]["patch"], "./cordis.patch.yml")
        self.assertEqual(data["dsh"]["client"]["platform"], "web")
        self.assertIn("lib", data["files"])
        self.assertIn("cordis.patch.yml", data["files"])

    def test_bundle_patch_is_insert(self):
        text = (PLUGIN / "cordis.patch.yml").read_text(encoding="utf-8")
        self.assertIn("- insert:", text)
        self.assertIn("@blctek/dsh-plugin-sanitize-detect", text)

    def test_client_registers_tool_cards_and_real_workbench_tab(self):
        text = (PLUGIN / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn("tool.call.toolview", text)
        self.assertIn("sidebar.footer.action", text)
        self.assertIn("mcp__sanitized-data__", text)
        self.assertIn("inject: ['slots', 'sidebarRightTabs', 'sidebarRight']", text)
        self.assertIn("sanitize_file", text)
        self.assertIn("sanitize_document", text)
        # v1.2.0 起 T2 默认用 DSH 原生 browser tab 打开真实工作台；不再注册仿制面板
        self.assertIn("openWorkbenchTab", text)
        self.assertIn("openTab('browser'", text)
        self.assertNotIn("sanitize-detect: panel body", text)
        self.assertNotIn("sidebar.right.pane.tab", text)
        self.assertIn("数据隐站工作台", text)
        self.assertIn("latestWorkbenchUrl", text)
        self.assertNotIn("jobs.find", text)
        self.assertIn("return base + '/console'", text)
        self.assertIn("PREFIX + '/start'", text)

    def test_host_proxy_is_loopback_only(self):
        text = (PLUGIN / "lib" / "index.js").read_text(encoding="utf-8")
        self.assertIn("/sanitized-data/api", text)
        self.assertIn("127.0.0.1", text)
        self.assertIn("kind: 'prefix'", text)
        self.assertIn("/start", text)
        self.assertIn("ensureWorkbench", text)
        self.assertIn("warmupWorkbench", text)
        # 不允许出现公网 URL 字面量
        urls = re.findall(r"https?://[^\s'\"<>]+", text)
        for url in urls:
            self.assertTrue(
                re.match(r"https?://127\.0\.0\.1(?::\d+)?", url) or "dsh.local" in url,
                url,
            )

    def test_install_script_present(self):
        script = (PLUGIN / "install.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("mcp-sanitized-data", script)
        self.assertIn("SkipMcp", script)
        self.assertIn("Sanitized_Data_MCP.exe", script)

    def test_bundles_syntax_if_node_available(self):
        node = subprocess.run(["node", "--version"], capture_output=True, text=True)
        if node.returncode != 0:
            self.skipTest("node not installed")
        for name in ("lib/index.js", "lib/client.js"):
            result = subprocess.run(
                ["node", "--check", str(PLUGIN / name)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-800:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
