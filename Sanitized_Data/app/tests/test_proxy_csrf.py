# -*- coding: utf-8 -*-
"""跨站/反代 Host 回归（2026-10-26 CKA 事故）。

背景：服务只监听 127.0.0.1，CKA 由 nginx 反代且 Host=cka.blctek.com。
旧 `_same_origin()` 先做 DNS rebinding 检查，直接拒绝所有非回环 Host，
导致升级 1.3.0 后整个站点 403「跨站请求被拒绝」。

修复原则：
1. 正常反代：nginx 会写 X-Forwarded-* / X-Real-IP；据此信任公网 Host；
2. 浏览器直连 127.0.0.1 的恶意域名：Host 是 evil.com 但无法伪造反代头 → 拒绝；
3. 跨站 Origin 仍拒绝（只有同源或白名单可写）；
4. CSRF 只约束 /api/*，静态首页/JS 不应整站 403。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import api  # noqa: E402


def _handler(*, host: str = "", origin: str | None = None,
             xfh: str = "", xff: str = "", xfp: str = "", xreal: str = "",
             bound: str = "127.0.0.1"):
    headers: dict[str, str] = {}
    if host:
        headers["Host"] = host
    if origin:
        headers["Origin"] = origin
    if xfh:
        headers["X-Forwarded-Host"] = xfh
    if xff:
        headers["X-Forwarded-For"] = xff
    if xfp:
        headers["X-Forwarded-Proto"] = xfp
    if xreal:
        headers["X-Real-IP"] = xreal
    fake = SimpleNamespace(server=SimpleNamespace(server_address=(bound, 8760)),
                           headers=headers)
    return api.Handler._same_origin(fake)


class ProxyHostTest(unittest.TestCase):
    def test_nginx_proxy_request_is_allowed(self):
        self.assertTrue(_handler(
            host="cka.blctek.com:8443",
            xfh="cka.blctek.com", xff="1.2.3.4", xfp="https",
            origin="https://cka.blctek.com:8443"))

    def test_proxy_with_rewritten_loopback_host(self):
        self.assertTrue(_handler(
            host="127.0.0.1:8760", xfh="cka.blctek.com", xff="1.2.3.4",
            origin="https://cka.blctek.com:8443"))

    def test_direct_non_loopback_host_without_proxy_headers_is_rejected(self):
        # DNS rebinding：Host=evil.com 直连 127.0.0.1，无 nginx 反代头
        self.assertFalse(_handler(host="evil.example", origin=None))
        self.assertFalse(_handler(host="evil.example", origin="http://evil.example"))

    def test_local_browser_is_allowed(self):
        self.assertTrue(_handler(host="127.0.0.1:8760", origin="http://127.0.0.1:8760"))

    def test_cross_site_origin_is_rejected_even_behind_proxy(self):
        self.assertFalse(_handler(
            host="cka.blctek.com:8443", xfh="cka.blctek.com", xff="1.2.3.4",
            origin="https://evil.example"))

    def test_allowed_hosts_env_fallback(self):
        with patch.dict(os.environ, {"YINZHAN_ALLOWED_HOSTS": "cka.blctek.com"}, clear=False):
            self.assertTrue(_handler(host="cka.blctek.com", origin=None))

    def test_allowed_origins_env(self):
        with patch.dict(os.environ,
                        {"YINZHAN_ALLOWED_ORIGINS": "https://cka.blctek.com:8443"},
                        clear=False):
            self.assertTrue(_handler(host="127.0.0.1:8760", origin="https://cka.blctek.com:8443"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
