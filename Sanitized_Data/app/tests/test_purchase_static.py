# -*- coding: utf-8 -*-
"""L3 扫码升级 · 本机客户端静态/契约测试（不启动浏览器，不启动 app 进程）。

对应 ``commercial/CONTRACT.md`` §3（HTTP API）、§7（本机客户端改动）、§8（测试与验收）：
  * ``purchase.html``：必要结构/ID、纯本地资源（无 CDN/外部脚本/字体）、未配置兜底文案；
  * ``purchase.js``：调商业服务 /api/catalog、/api/orders、/api/mock/pay，
    调同源 /api/license/import；轮询 2 秒/10 分钟超时；卸载停止轮询；esc 转义；
  * ``license.html/js``：新增「扫码升级」入口 → /purchase，原导入/价格/备份保留；
  * ``pricing.js``：有 commercial_url 时购买按钮指向 /purchase?sku=，否则回退 purchase_url；
  * ``api.py``：PAGE_ALIASES 含 purchase、status 含 commercial_url；
  * ``config.py``：读 YINZHAN_COMMERCIAL_URL；
  * ``licensing/keys.py``：YINZHAN_PUBLIC_KEYS_PATH 覆盖/合并公钥，未设置时行为不变。
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]                  # Sanitized_Data
APP = REPO / "app"
WEB = APP / "web"
SERVER = APP / "server"
LICENSING = SERVER / "yinzhan" / "licensing"
KEYS_PY = LICENSING / "keys.py"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class PurchaseStaticTest(unittest.TestCase):
    """静态检查（只读文件，不依赖运行中的 app / 商业服务）。"""

    # ── purchase.html ──────────────────────────────────────────
    def test_purchase_html_has_required_structure(self):
        html = read_text(WEB / "purchase.html")
        for element_id in (
            "stateBadge", "stateText", "edition", "expiresText", "machineCode",
            "statusMsg", "noCommercial", "fallbackBuy", "fallbackHint",
            "shopPanel", "skuList", "catalogMsg",
            "orderPanel", "qrBox", "orderSku", "orderAmount", "orderNo",
            "orderCountdown", "orderStatus", "btnMockPay", "btnCancelOrder",
            "btnRetryOrder", "btnResumePoll", "orderMsg",
            "successPanel", "successLine", "successEdition", "successExpires",
            "manualPanel", "manualText", "btnManualImport", "btnCopyLicense",
            "manualMsg",
        ):
            self.assertIn('id="%s"' % element_id, html, "purchase.html 缺少 #%s" % element_id)
        self.assertIn('src="purchase.js"', html)
        self.assertIn('href="purchase.css"', html)
        # commercial_url 为空时必须显示兜底文案（不伪造链接）
        self.assertIn("购买入口尚未配置", html)
        self.assertIn('href="/pricing"', html)
        self.assertIn('href="/license"', html)

    def test_purchase_html_is_fully_local(self):
        html = read_text(WEB / "purchase.html")
        # 不加载任何 http(s) 外部脚本/样式/字体/图片/CDN
        self.assertNotRegex(html, r'(?i)(?:src|href)\s*=\s*["\']\s*https?://')
        self.assertNotRegex(html, r'(?i)(?:cdn\.|fonts\.googleapis|unpkg\.|jsdelivr)')
        # 只有一个本地脚本
        self.assertEqual(html.lower().count("<script"), 1)
        self.assertIn('<script src="purchase.js"></script>', html)

    def test_purchase_css_is_local(self):
        css = read_text(WEB / "purchase.css")
        self.assertNotRegex(css, r"(?i)@import")
        self.assertNotRegex(css, r"(?i)url\(\s*['\"]?\s*https?://")

    def test_purchase_js_element_ids_exist_in_html(self):
        """purchase.js 里 $('...') 引用的元素必须都在 purchase.html 中，防止改页面漏 id。"""
        html = read_text(WEB / "purchase.html")
        js = read_text(WEB / "purchase.js")
        all_ids = re.findall(r'id="([^"]+)"', html)
        self.assertEqual(len(all_ids), len(set(all_ids)), "purchase.html 存在重复 id")
        html_ids = set(all_ids)
        js_ids = set(re.findall(r"\$\('([^']+)'\)", js))
        missing = sorted(js_ids - html_ids)
        self.assertEqual(missing, [], "purchase.js 引用了 HTML 中不存在的 id: %s" % missing)

    # ── purchase.js ────────────────────────────────────────────
    def test_purchase_js_calls_contract_endpoints(self):
        js = read_text(WEB / "purchase.js")
        # 契约 §3 的四个商业服务接口 + 同源导入接口
        for endpoint in ("/api/catalog", "/api/orders", "/api/mock/pay",
                         "/api/license/import", "/api/license/status"):
            self.assertIn(endpoint, js, "purchase.js 未调用 %s" % endpoint)
        # 下单请求体关键字段（契约 §3 POST /api/orders）
        self.assertIn("machine_code", js)
        self.assertIn("channel", js)
        self.assertIn("qr_svg", js)
        self.assertIn("license.text", js)
        # paid 后才导入：状态分支存在
        self.assertIn("'paid'", js)
        self.assertIn("expired", js)

    def test_purchase_js_polling_and_cleanup(self):
        js = read_text(WEB / "purchase.js")
        self.assertIn("2000", js)                        # 每 2 秒轮询
        self.assertIn("10 * 60 * 1000", js)              # 10 分钟超时
        self.assertIn("/api/orders/", js)                # GET /api/orders/{order_no}?token=
        self.assertIn("?token=", js)
        self.assertIn("beforeunload", js)                # 卸载停止轮询
        self.assertIn("pagehide", js)
        self.assertIn("clearTimeout", js)
        self.assertIn("clearInterval", js)

    def test_purchase_js_escapes_and_no_external_assets(self):
        js = read_text(WEB / "purchase.js")
        self.assertIn("function esc(", js)
        self.assertIn("textContent", js)
        # 除注释外不出现外部 URL 常量 / 第三方脚本加载
        self.assertNotIn("https://", js)
        self.assertNotRegex(js, r"(?i)(?:document\.write|createElement\(['\"]script|\.src\s*=)")
        self.assertNotRegex(js, r"(?i)(?:cdn\.|fonts\.googleapis|unpkg\.|jsdelivr)")

    # ── license 页：新增扫码入口，保留原功能 ────────────────────
    def test_license_page_adds_scan_upgrade_entry(self):
        html = read_text(WEB / "license.html")
        self.assertIn('id="scanBuyLink"', html)
        self.assertIn('href="/purchase"', html)
        self.assertIn("扫码升级（自动解锁）", html)
        # 原导入/价格/备份功能仍在
        for keep in ('id="buyLink"', 'id="licenseFile"', 'id="btnImport"',
                     'id="btnBackup"', 'id="licenseText"', 'href="/pricing"'):
            self.assertIn(keep, html)
        js = read_text(WEB / "license.js")
        self.assertIn("/purchase", js)
        self.assertIn("commercial_url", js)
        self.assertIn("scanBuyLink", js)

    # ── pricing 页：有 commercial_url 时指向 /purchase?sku= ─────
    def test_pricing_prefers_local_purchase_when_commercial_configured(self):
        js = read_text(WEB / "pricing.js")
        self.assertIn("/purchase?sku=", js)
        self.assertIn("commercial_url", js)
        # 未配置商业服务时保持原有 purchase_url 逻辑
        self.assertIn("purchase_url", js)
        self.assertIn("dataset.sku", js)
        html = read_text(WEB / "pricing.html")
        self.assertIn('data-sku="personal"', html)
        # 2026-11-04 业务调整：价格页只保留 199 个人版，其他价位已移除
        self.assertNotIn('data-sku="pro"', html)
        self.assertIn("¥199", html)
        self.assertNotIn("¥499", html)
        # 2026-11-05：MCP 并入个人版；不再写“本地页为参考价/以官网为准”这类
        # 自相矛盾的描述；专业版/团队版/机构版只留商务联系。
        self.assertIn("MCP 接入", html)
        self.assertIn("专业版 / 团队版 / 机构版", html)
        self.assertNotIn("本地页为参考价", html)
        self.assertNotIn("参考价", html)
        self.assertNotIn("价格与活动以官网为准", html)
        self.assertIn("免费版：每天 3 份", html)

    # ── Python 侧注入 ─────────────────────────────────────────
    def test_api_exposes_purchase_route_and_commercial_url(self):
        source = read_text(SERVER / "yinzhan" / "api.py")
        self.assertIn('"purchase": "purchase.html"', source)
        self.assertIn('"purchase/": "purchase.html"', source)
        self.assertIn('"commercial_url"', source)
        self.assertIn("COMMERCIAL_URL", source)

    def test_config_reads_commercial_url_env(self):
        source = read_text(SERVER / "yinzhan" / "config.py")
        self.assertIn("YINZHAN_COMMERCIAL_URL", source)
        self.assertIn("COMMERCIAL_URL", source)

    def test_keys_supports_public_keys_path_override(self):
        source = read_text(KEYS_PY)
        self.assertIn("YINZHAN_PUBLIC_KEYS_PATH", source)
        self.assertIn("PUBLIC_KEYS_PATH_ENV", source)
        # 注释必须写明仅供测试/私有化，避免误用到生产
        self.assertRegex(source, r"仅测试|私有化")

    # ── keys.py 行为：env 覆盖/合并；未设置不变（不依赖 app 进程） ──
    def _load_keys_module(self):
        spec = importlib.util.spec_from_file_location("yinzhan_keys_under_test", KEYS_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)          # noqa: SLF001  (测试内加载独立模块)
        return module

    def test_public_keys_env_override_merges(self):
        override_id = "t3-override-2026"
        raw = base64.b64encode(b"t" * 32).decode("ascii")
        # 不落盘：用 Path.read_text 的 side_effect 伪造环境变量指向的 JSON 文件，
        # 避免测试往沙箱外/仓库里写临时文件；同时验证 env → 读取 → 合并的完整路径。
        override_path = KEYS_PY.parent / "nonexistent_public_keys_override.json"
        real_read_text = Path.read_text

        def fake_read_text(self, *args, **kwargs):
            if self == override_path:
                return json.dumps({override_id: raw})
            return real_read_text(self, *args, **kwargs)

        with mock.patch.object(Path, "read_text", fake_read_text):
            with mock.patch.dict(os.environ,
                                 {"YINZHAN_PUBLIC_KEYS_PATH": str(override_path)},
                                 clear=False):
                module = self._load_keys_module()
        self.assertEqual(module.PUBLIC_KEYS.get(override_id), b"t" * 32)
        # 合并而不是整体替换：默认公钥仍然保留（若默认文件存在）
        default_path = LICENSING / "public_keys.json"
        if default_path.is_file():
            default = json.loads(default_path.read_text(encoding="utf-8"))
            for key_id, value in default.items():
                self.assertEqual(module.PUBLIC_KEYS.get(str(key_id)),
                                 base64.b64decode(str(value)))

    def test_public_keys_without_env_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("YINZHAN_PUBLIC_KEYS_PATH", None)
            module = self._load_keys_module()
        self.assertNotIn("t3-override-2026", module.PUBLIC_KEYS)
        default_path = LICENSING / "public_keys.json"
        if default_path.is_file():
            default = json.loads(default_path.read_text(encoding="utf-8"))
            self.assertEqual(set(module.PUBLIC_KEYS), {str(k) for k in default})
            for key_id, value in default.items():
                self.assertEqual(module.PUBLIC_KEYS[str(key_id)],
                                 base64.b64decode(str(value)))

    def test_purchase_page_documented_in_contract_aliases(self):
        """防止有人把 /purchase 别名改成别的文件名（契约要求 purchase.html）。"""
        source = read_text(SERVER / "yinzhan" / "api.py")
        self.assertIn("purchase.html", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
