# -*- coding: utf-8 -*-
"""T1 商业服务 API 测试：目录/下单/查询/mock 支付/幂等/过期/CORS/静态页/回调路由。"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from datetime import datetime, timezone

import pytest

from conftest import VALID_MACHINE_CODE

ORDER_NO_RE = re.compile(r"^ord_\d{14}_[0-9a-f]{8}$")


def _client_for(cfg):
    from fastapi.testclient import TestClient
    from server.app import create_app

    return TestClient(create_app(cfg))


# ── 基础 ──
def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["provider"] == "mock"
    assert data["version"]


def test_config_defaults_contract():
    from server.config import Config

    cfg = Config(env={})
    assert cfg.PAYMENT_PROVIDER == "mock"
    assert cfg.PAYMENT_MOCK_ENABLED is True
    assert cfg.LICENSE_KEY_ID == "prod-2026"
    assert cfg.COMMERCIAL_ORDER_TTL_SECONDS == 15 * 60
    assert cfg.COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE == 10
    assert cfg.prices == {"personal": 19900, "pro": 49900, "team": 199900}
    # 属性名与 env 同名（T2 依赖）
    for name in ("ALIPAY_APP_ID", "ALIPAY_APP_PRIVATE_KEY_PATH", "ALIPAY_PUBLIC_KEY_PATH",
                 "ALIPAY_GATEWAY", "ALIPAY_PAY_MODE", "ALIPAY_NOTIFY_URL",
                 "ALIPAY_SELLER_ID", "ALIPAY_APP_ID_CHECK", "COMMERCIAL_BASE_URL",
                 "COMMERCIAL_DB", "COMMERCIAL_DATA_DIR", "LICENSE_PRIVATE_KEY_PATH",
                 "LICENSE_KEY_PASSWORD_ENV", "COMMERCIAL_PRICE_PERSONAL_CENTS",
                 "COMMERCIAL_PRICE_PRO_CENTS", "COMMERCIAL_PRICE_TEAM_CENTS",
                 "COMMERCIAL_CORS_EXTRA_ORIGINS"):
        assert hasattr(cfg, name), name


def test_catalog_contract(client, config):
    response = client.get("/api/catalog")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["provider"] == "mock"
    assert [channel["id"] for channel in data["channels"]] == ["alipay", "mock"]
    assert all(channel["enabled"] for channel in data["channels"])
    skus = {sku["id"]: sku for sku in data["skus"]}
    assert set(skus) == {"personal", "pro"}
    assert skus["personal"]["price_cents"] == config.COMMERCIAL_PRICE_PERSONAL_CENTS
    assert skus["pro"]["price_cents"] == config.COMMERCIAL_PRICE_PRO_CENTS
    assert skus["pro"]["currency"] == "CNY"
    assert skus["pro"]["term"] == "annual"
    assert skus["pro"]["devices"] == 2
    assert skus["personal"]["devices"] == 1
    assert "custom_packs" in skus["pro"]["features"]
    assert "mcp" in skus["pro"]["features"]
    assert "custom_packs" not in skus["personal"]["features"]
    assert "all_packs" in skus["personal"]["features"]
    # team/org 只联系商务，不建线上单
    assert data["contact_sales"]["skus"] == ["team", "org"]
    assert data["contact_sales"]["reference_prices_cents"]["team"] == \
        config.COMMERCIAL_PRICE_TEAM_CENTS
    assert data["mock_available"] is True


# ── 下单 ──
def test_create_order_success(client, config, valid_machine_code):
    response = client.post("/api/orders", json={
        "sku": "pro", "machine_code": valid_machine_code, "channel": "alipay"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert ORDER_NO_RE.match(data["order_no"]), data["order_no"]
    assert data["sku"] == "pro"
    assert data["amount_cents"] == config.COMMERCIAL_PRICE_PRO_CENTS
    assert data["subject"] == "数据隐站 专业版 · 1年"
    assert data["channel"] == "alipay"
    assert data["provider"] == "mock"
    assert data["qr_svg"].startswith("<svg")
    assert "xmlns" in data["qr_svg"]
    assert "<path" in data["qr_svg"]
    assert "/mock/pay?order_no=" in data["qr_content"]
    assert data["order_token"] in data["qr_content"]
    assert data["pay_url"] == data["qr_content"]
    assert data["mock_available"] is True
    assert len(data["order_token"]) >= 40
    expires = datetime.fromisoformat(data["expires_at"])
    delta = (expires - datetime.now(timezone.utc)).total_seconds()
    assert 800 <= delta <= 910, delta


def test_create_order_ignores_client_supplied_amount(client, config, valid_machine_code):
    response = client.post("/api/orders", json={
        "sku": "personal",
        "machine_code": valid_machine_code,
        "channel": "alipay",
        "amount_cents": 1,
        "price_cents": 1,
        "total": 0.01,
    })
    assert response.status_code == 200
    assert response.json()["amount_cents"] == config.COMMERCIAL_PRICE_PERSONAL_CENTS


def test_create_order_accepts_compact_machine_code(client):
    response = client.post("/api/orders", json={
        "sku": "pro", "machine_code": "zh397n5c98cda8gb", "channel": "alipay"})
    assert response.status_code == 200


@pytest.mark.parametrize("bad_code", ["", "abc", "IIII-IIII-IIII-IIII", "1234-5678-90AB-CDE"])
def test_create_order_machine_code_invalid(client, bad_code):
    response = client.post("/api/orders", json={
        "sku": "pro", "machine_code": bad_code, "channel": "alipay"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "machine_code_invalid"


@pytest.mark.parametrize("bad_sku", ["", "free", "trial", "gold", "PRO!"])
def test_create_order_sku_invalid(client, valid_machine_code, bad_sku):
    response = client.post("/api/orders", json={
        "sku": bad_sku, "machine_code": valid_machine_code, "channel": "alipay"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "sku_invalid"


def test_create_order_team_requires_contact_sales(client, valid_machine_code):
    response = client.post("/api/orders", json={
        "sku": "team", "machine_code": valid_machine_code, "channel": "alipay"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "sku_invalid"
    assert "商务" in error["message"]


def test_create_order_channel_unavailable(client, valid_machine_code):
    response = client.post("/api/orders", json={
        "sku": "pro", "machine_code": valid_machine_code, "channel": "wechat"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "channel_unavailable"


def test_create_order_email_invalid(client, valid_machine_code):
    for bad_email in ("abc", "a@", "@x.com", "a b@c.com"):
        response = client.post("/api/orders", json={
            "sku": "pro", "machine_code": valid_machine_code,
            "channel": "alipay", "email": bad_email})
        assert response.status_code == 400, bad_email
        assert response.json()["error"]["code"] == "invalid_request"


def test_create_order_malformed_json(client):
    response = client.post("/api/orders", content=b"{not json",
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_create_order_email_accepted(client, valid_machine_code, db_query):
    response = client.post("/api/orders", json={
        "sku": "pro", "machine_code": valid_machine_code,
        "channel": "alipay", "email": "buyer@example.com"})
    assert response.status_code == 200
    rows = db_query("SELECT email FROM orders WHERE order_no=?",
                    (response.json()["order_no"],))
    assert rows[0]["email"] == "buyer@example.com"


# ── 查询/token ──
def test_get_order_pending(client, create_order):
    data = create_order()
    response = client.get(f"/api/orders/{data['order_no']}",
                          params={"token": data["order_token"]})
    assert response.status_code == 200
    view = response.json()
    assert view["ok"] is True
    assert view["status"] == "pending"
    assert view["sku"] == "pro"
    assert view["amount_cents"] == data["amount_cents"]
    assert view["paid_at"] is None
    assert view["license"] is None
    assert view["mock_available"] is True


def test_get_order_token_errors_are_uniform_404(client, create_order):
    data = create_order()
    order_no = data["order_no"]
    token = data["order_token"]
    wrong = client.get(f"/api/orders/{order_no}", params={"token": "wrong-token"})
    missing = client.get(f"/api/orders/{order_no}")
    unknown = client.get("/api/orders/ord_20260101000000_deadbeef",
                         params={"token": token})
    for response in (wrong, missing, unknown):
        assert response.status_code == 404
        body = response.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "order_not_found"
    # 三种失败响应体一致（防枚举）
    assert wrong.json() == missing.json() == unknown.json()


def test_order_token_stored_hashed(client, create_order, db_query):
    data = create_order()
    rows = db_query("SELECT order_token_hash FROM orders WHERE order_no=?",
                    (data["order_no"],))
    stored = rows[0]["order_token_hash"]
    assert stored != data["order_token"]
    assert len(stored) == 64
    assert stored == __import__("hashlib").sha256(
        data["order_token"].encode("utf-8")).hexdigest()


# ── mock 支付 / 授权 ──
def test_mock_pay_issues_license_importable_by_product(client, create_order,
                                                       license_service, real_machine_code,
                                                       db_count, test_license_key):
    from server.signer import normalize_machine_code

    data = create_order(machine_code=real_machine_code)
    order_no, token = data["order_no"], data["order_token"]

    response = client.post("/api/mock/pay", json={"order_no": order_no, "token": token})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["ok"] is True
    assert view["status"] == "paid"
    assert view["paid_at"]
    license_info = view["license"]
    assert license_info["license_id"].startswith("lic_")
    assert license_info["sku"] == "pro"
    assert license_info["edition"] == "专业版"
    assert license_info["machine_code"] == normalize_machine_code(real_machine_code)
    assert license_info["expires_at"]

    # 授权文本：紧凑单行 JSON + 容器字段正确
    text = license_info["text"]
    assert "\n" not in text
    container = json.loads(text)
    assert container["schema"] == 1
    assert container["alg"] == "Ed25519"
    assert container["key_id"] == test_license_key["key_id"]
    payload = container["payload"]
    assert payload["order_id"] == order_no
    assert payload["sku"] == "pro"
    assert payload["term"] == "annual"
    assert payload["issuer"] == "BLCTEK"

    # 独立验签（使用测试公钥）
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from yinzhan.licensing.service import canonical_payload

    public_key = Ed25519PublicKey.from_public_bytes(
        base64.b64decode(test_license_key["public_raw_b64"]))
    public_key.verify(base64.b64decode(container["sig"]), canonical_payload(payload))

    # 产品 LicenseService 导入 → state=paid（含 pro 功能位）
    status = license_service.import_license(license_info["text"])
    assert status["state"] == "paid"
    assert status["sku"] == "pro"
    assert status["license"]["license_id"] == license_info["license_id"]
    assert "mcp" in status["features"]

    # GET 订单同样返回授权
    view2 = client.get(f"/api/orders/{order_no}", params={"token": token}).json()
    assert view2["status"] == "paid"
    assert view2["license"]["license_id"] == license_info["license_id"]

    assert db_count("payments") == 1
    assert db_count("licenses") == 1


def test_mock_pay_is_idempotent(client, create_order, db_count, db_query):
    data = create_order()
    order_no, token = data["order_no"], data["order_token"]
    first = client.post("/api/mock/pay", json={"order_no": order_no, "token": token})
    second = client.post("/api/mock/pay", json={"order_no": order_no, "token": token})
    assert first.status_code == 200 and second.status_code == 200
    license_id = first.json()["license"]["license_id"]
    assert second.json()["license"]["license_id"] == license_id
    assert db_count("payments") == 1
    assert db_count("licenses") == 1
    paid_rows = db_query("SELECT paid_at, status FROM orders WHERE order_no=?", (order_no,))
    assert paid_rows[0]["status"] == "paid"
    assert first.json()["paid_at"] == paid_rows[0]["paid_at"]


def test_mock_pay_token_error_404(client, create_order):
    data = create_order()
    response = client.post("/api/mock/pay", json={
        "order_no": data["order_no"], "token": "bad-token"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "order_not_found"


def test_mock_pay_unknown_order_404(client):
    response = client.post("/api/mock/pay", json={
        "order_no": "ord_20260101000000_deadbeef", "token": "x"})
    assert response.status_code == 404


def test_expired_order_cannot_be_paid(client, create_order, db_exec, db_query, db_count):
    data = create_order()
    order_no, token = data["order_no"], data["order_token"]
    db_exec("UPDATE orders SET expires_at=? WHERE order_no=?",
            ("2000-01-01T00:00:00+00:00", order_no))

    view = client.get(f"/api/orders/{order_no}", params={"token": token}).json()
    assert view["status"] == "expired"
    assert view["license"] is None

    response = client.post("/api/mock/pay", json={"order_no": order_no, "token": token})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "order_expired"
    assert db_count("licenses") == 0
    rows = db_query("SELECT status FROM orders WHERE order_no=?", (order_no,))
    assert rows[0]["status"] == "expired"


def test_mock_disabled_when_provider_is_alipay(make_config):
    cfg = make_config(PAYMENT_PROVIDER="alipay")
    with _client_for(cfg) as test_client:
        response = test_client.post("/api/mock/pay", json={
            "order_no": "ord_20260101000000_deadbeef", "token": "x"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "mock_disabled"
        catalog = test_client.get("/api/catalog").json()
        mock_channel = [c for c in catalog["channels"] if c["id"] == "mock"][0]
        assert mock_channel["enabled"] is False


def test_channel_mock_disabled_rejects_order(make_config, valid_machine_code):
    cfg = make_config(PAYMENT_PROVIDER="alipay")
    with _client_for(cfg) as test_client:
        response = test_client.post("/api/orders", json={
            "sku": "pro", "machine_code": valid_machine_code, "channel": "mock"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "channel_unavailable"


# ── 限流 ──
def test_rate_limit_orders_per_minute(make_config, valid_machine_code):
    cfg = make_config(COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE="3")
    with _client_for(cfg) as test_client:
        statuses = []
        for _ in range(4):
            response = test_client.post("/api/orders", json={
                "sku": "personal", "machine_code": valid_machine_code, "channel": "mock"})
            statuses.append(response.status_code)
        assert statuses[:3] == [200, 200, 200]
        assert statuses[3] == 429
        assert response.json()["error"]["code"] == "rate_limited"
        assert "Retry-After" in response.headers


# ── CORS ──
def test_cors_preflight_and_origin_whitelist(client, valid_machine_code):
    allowed_origins = [
        "http://127.0.0.1:8760",
        "http://localhost:5000",
        "https://buy.example.com",  # COMMERCIAL_CORS_EXTRA_ORIGINS
    ]
    for origin in allowed_origins:
        response = client.options("/api/orders", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        assert response.status_code in (200, 204), origin
        assert response.headers.get("access-control-allow-origin") == origin
        assert "POST" in response.headers.get("access-control-allow-methods", "")
        assert "content-type" in response.headers.get(
            "access-control-allow-headers", "").lower()
        # 不带 credentials
        assert response.headers.get("access-control-allow-credentials") is None

    # 无关来源：不返回 CORS 头
    blocked = client.options("/api/orders", headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "POST",
    })
    assert blocked.headers.get("access-control-allow-origin") is None

    # 简单请求（POST）从允许来源也带 CORS 头
    post = client.post("/api/orders", json={
        "sku": "personal", "machine_code": valid_machine_code, "channel": "alipay"},
        headers={"Origin": "http://127.0.0.1:9999"})
    assert post.status_code == 200
    assert post.headers.get("access-control-allow-origin") == "http://127.0.0.1:9999"

    post_blocked = client.post("/api/orders", json={
        "sku": "personal", "machine_code": valid_machine_code, "channel": "alipay"},
        headers={"Origin": "https://evil.example.com"})
    assert post_blocked.status_code == 200
    assert post_blocked.headers.get("access-control-allow-origin") is None


# ── 静态页面 ──
def test_static_pages_are_local_only(client):
    for path in ("/buy.html", "/order.html", "/mock/pay", "/"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "text/html" in response.headers.get("content-type", "")
        html = response.text
        assert "/static/style.css" in html
        # ICP/许可证允许站外 <a href>；但不得加载外部脚本/样式/图片
        assert not re.search(
            r"<(?:script|img|link)[^>]+(?:src|href)\s*=\s*[\"']https?://",
            html, re.I), path
        assert "default-src 'self'" in response.headers.get("content-security-policy", "")
    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert "text/css" in css.headers.get("content-type", "")
    for name in ("common.js", "buy.js", "order.js", "mock_pay.js"):
        script = client.get(f"/static/{name}")
        assert script.status_code == 200, name
        assert "javascript" in script.headers.get("content-type", "")
    assert client.get("/static/no-such-file.js").status_code == 404
    # 页面 XSS 转义基础：服务端不把用户输入回填进 HTML（页面为纯静态）
    page = client.get("/buy.html")
    assert "noindex" in page.text


def test_public_checkout_pages_are_production_copy(client):
    """购买/订单页不得出现测试、演示、模拟支付等上线前文案。"""
    for path in ("/buy.html", "/order.html"):
        html = client.get(path).text
        assert not re.search(r"测试|演示|模拟支付|mock|测试用例", html, re.I), path
        assert "static/logo.svg" in html
        assert "static/favicon.svg" in html


# ── 支付宝回调路由（适配器由 T2 提供；此用例不依赖其配置） ──
def test_notify_alipay_route_rejects_invalid_form(client):
    response = client.post("/notify/alipay", data={"out_trade_no": "x"})
    assert response.status_code in (200, 503)
    assert response.text.strip() == "failure"


def test_notify_alipay_without_adapter_returns_503(client, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "server.payments.alipay", None)
    response = client.post("/notify/alipay", data={})
    assert response.status_code == 503
    assert response.text.strip() == "failure"


# ── DB 幂等/唯一约束（重复回调不重复签发） ──
def test_finalize_payment_idempotent_and_transaction_unique(config, create_order, db_count):
    from server import db as db_module
    from server.signer import LicenseSigner

    database = db_module.Database(config.db_path)
    signer = LicenseSigner(config.LICENSE_PRIVATE_KEY_PATH, key_id=config.LICENSE_KEY_ID)

    first = create_order()
    license_one = signer.issue(sku="pro", machine_code=VALID_MACHINE_CODE,
                               order_no=first["order_no"])
    result = database.finalize_payment(
        order_no=first["order_no"], provider="alipay", transaction_id="trade_0001",
        amount_cents=first["amount_cents"], raw={"trade_status": "TRADE_SUCCESS"},
        license=license_one, trade_status="TRADE_SUCCESS")
    assert result["paid_now"] is True

    # 重复回调：换一个「新签发」的 license 也不应覆盖/重复入账
    license_two = signer.issue(sku="pro", machine_code=VALID_MACHINE_CODE,
                               order_no=first["order_no"])
    duplicate = database.finalize_payment(
        order_no=first["order_no"], provider="alipay", transaction_id="trade_0001",
        amount_cents=first["amount_cents"], raw={"trade_status": "TRADE_SUCCESS"},
        license=license_two, trade_status="TRADE_SUCCESS")
    assert duplicate["paid_now"] is False
    assert duplicate["duplicate"] is True
    assert duplicate["license"]["license_id"] == license_one["license_id"]
    assert db_count("payments") == 1
    assert db_count("licenses") == 1

    # 同一 transaction_id 用到另一个订单 → PaymentConflict
    second = create_order()
    license_three = signer.issue(sku="pro", machine_code=VALID_MACHINE_CODE,
                                 order_no=second["order_no"])
    with pytest.raises(db_module.PaymentConflict):
        database.finalize_payment(
            order_no=second["order_no"], provider="alipay", transaction_id="trade_0001",
            amount_cents=second["amount_cents"], raw={}, license=license_three)
    assert db_count("licenses") == 1


def test_finalize_payment_expired_policy(config, create_order, db_exec):
    from server import db as db_module
    from server.signer import LicenseSigner

    database = db_module.Database(config.db_path)
    signer = LicenseSigner(config.LICENSE_PRIVATE_KEY_PATH, key_id=config.LICENSE_KEY_ID)

    order = create_order()
    db_exec("UPDATE orders SET expires_at=? WHERE order_no=?",
            ("2000-01-01T00:00:00+00:00", order["order_no"]))
    license_data = signer.issue(sku="pro", machine_code=VALID_MACHINE_CODE,
                                order_no=order["order_no"])

    # mock/主动支付：过期不可付
    with pytest.raises(db_module.OrderExpiredError):
        database.finalize_payment(
            order_no=order["order_no"], provider="mock", transaction_id="mock_x",
            amount_cents=order["amount_cents"], raw={}, license=license_data)
    # 支付宝回调（钱已到账）：允许过期补签发
    result = database.finalize_payment(
        order_no=order["order_no"], provider="alipay", transaction_id="trade_expired",
        amount_cents=order["amount_cents"], raw={}, license=license_data,
        allow_expired=True)
    assert result["order"]["status"] == "paid"
    assert result["license"]["license_id"] == license_data["license_id"]


def test_db_unique_constraints(config, create_order, db_count):
    from server.signer import LicenseSigner

    data = create_order()
    license_data = LicenseSigner(
        config.LICENSE_PRIVATE_KEY_PATH, key_id=config.LICENSE_KEY_ID
    ).issue(sku="pro", machine_code=VALID_MACHINE_CODE, order_no=data["order_no"])

    conn = sqlite3.connect(str(config.db_path))
    try:
        conn.execute(
            "INSERT INTO payments(order_no, provider, transaction_id, amount_cents,"
            " raw, created_at) VALUES(?,?,?,?,?,?)",
            (data["order_no"], "mock", "txn_dup", 1, "{}", "2026-01-01T00:00:00+00:00"))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO payments(order_no, provider, transaction_id, amount_cents,"
                " raw, created_at) VALUES(?,?,?,?,?,?)",
                (data["order_no"], "mock", "txn_dup", 1, "{}",
                 "2026-01-01T00:00:00+00:00"))
        conn.rollback()

        conn.execute(
            "INSERT INTO licenses(license_id, order_no, machine_code, sku, text,"
            " issued_at, expires_at) VALUES(?,?,?,?,?,?,?)",
            (license_data["license_id"], data["order_no"], VALID_MACHINE_CODE,
             "pro", license_data["text"], license_data["issued_at"],
             license_data["expires_at"]))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO licenses(license_id, order_no, machine_code, sku, text,"
                " issued_at, expires_at) VALUES(?,?,?,?,?,?,?)",
                ("lic_other", data["order_no"], VALID_MACHINE_CODE, "pro",
                 license_data["text"], license_data["issued_at"],
                 license_data["expires_at"]))
        conn.rollback()
    finally:
        conn.close()
    assert db_count("payments") == 1
    assert db_count("licenses") == 1
