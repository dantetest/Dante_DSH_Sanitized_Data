# -*- coding: utf-8 -*-
"""支付宝「主动查单兜底」测试（ALIPAY_QUERY_FALLBACK / alipay.trade.query）。

覆盖两层：
1) ``AlipayProvider.query_trade`` 单元：RSA2 请求签名、支付宝公钥验响应签名、
   ``total_amount`` 元→分、``ACQ.TRADE_NOT_EXIST`` 不算错误、其他业务错误/验签失败抛错；
2) ``GET /api/orders/{no}`` 路由：查单成功 → paid + license（DB payments/licenses 各 1）、
   重复 GET 不重复签发、按订单节流、金额不符/查询异常保持 pending 且稳定 200、
   默认关闭时不发起任何 HTTP。

特点：
- **不联网**：monkeypatch ``server.payments.alipay._post_form``（即替换 urllib 调用）；
- **不用真实密钥**：用 ``cryptography`` 现生成两对测试 RSA 2048 密钥；响应签名规则
  在测试里独立实现，不复用被测模块的签名函数，避免「自签自验」掩盖错误；
- 临时文件放仓库内 ``commercial/var/_test_tmp``（gitignore；系统 temp 在受限沙箱可能不可写）。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from server import app as app_module
from server.payments import alipay as alipay_module

COMMERCIAL_DIR = Path(__file__).resolve().parents[1]
#: 仓库内临时目录（不用 pytest tmp_path / 系统 temp：受限沙箱下可能不可写）
TEST_TMP_DIR = COMMERCIAL_DIR / "var" / "_test_tmp"

TEST_APP_ID = "2021000000000001"
VALID_MACHINE_CODE = "ZH39-7N5C-98CD-A8GB"
QUERY_TRADE_NO = "2026110222001400000000000001"


# ---------------------------------------------------------------------------
# 测试密钥 / 报文签名（独立实现，便于独立校验被测模块）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def query_keys():
    """两对测试 RSA 2048 密钥：应用密钥对（请求签名）+ 支付宝密钥对（响应签名）。"""
    work_dir = TEST_TMP_DIR / f"alipay-query-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    app_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    alipay_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    app_private_path = work_dir / "app_private.pem"
    app_private_path.write_bytes(app_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    alipay_public_path = work_dir / "alipay_public.pem"
    alipay_public_path.write_bytes(alipay_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    try:
        yield {
            "app_key": app_key,
            "app_public_key": app_key.public_key(),
            "alipay_key": alipay_key,
            "app_private_path": str(app_private_path),
            "alipay_public_path": str(alipay_public_path),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _sign_content(params: dict) -> str:
    """支付宝待签串规则（独立实现）：剔除 sign、字典序、k=v 用 & 连接、空值不参与。"""
    return "&".join(
        f"{key}={params[key]}"
        for key in sorted(params)
        if key != "sign" and params[key] not in (None, "")
    )


def _rsa2_sign(content: str, private_key) -> str:
    signature = private_key.sign(content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


def _rsa2_verify(content: str, signature: str, public_key) -> bool:
    try:
        public_key.verify(base64.b64decode(signature), content.encode("utf-8"),
                          padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:  # noqa: BLE001 - 测试辅助，任何异常都视为验签失败
        return False


def _signed_query_response(response_obj: dict, signing_key) -> bytes:
    """伪造 ``alipay.trade.query`` 同步响应：响应对象原始 JSON 用支付宝私钥签名。"""
    raw_obj = json.dumps(response_obj, ensure_ascii=False, separators=(",", ":"))
    signature = _rsa2_sign(raw_obj, signing_key)
    return ('{"alipay_trade_query_response":%s,"sign":"%s"}' % (raw_obj, signature)).encode("utf-8")


# ---------------------------------------------------------------------------
# 响应报文工厂 / 伪 HTTP 传输
# ---------------------------------------------------------------------------


def _query_paid(order_no: str, amount_cents: int = 49900,
                trade_status: str = "TRADE_SUCCESS") -> dict:
    return {
        "code": "10000",
        "msg": "Success",
        "buyer_logon_id": "buyer***@example.com",
        "out_trade_no": order_no,
        "trade_no": QUERY_TRADE_NO,
        "trade_status": trade_status,
        "total_amount": f"{amount_cents // 100}.{amount_cents % 100:02d}",
    }


def _query_wait_buyer_pay(order_no: str, amount_cents: int = 49900) -> dict:
    return _query_paid(order_no, amount_cents, trade_status="WAIT_BUYER_PAY")


def _query_trade_not_exist() -> dict:
    return {
        "code": "40004",
        "msg": "Business Failed",
        "sub_code": "ACQ.TRADE_NOT_EXIST",
        "sub_msg": "交易不存在",
    }


class FakeTransport:
    """替代 ``alipay._post_form``：记录调用参数并返回预置响应（或抛预置异常）。"""

    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, gateway, params, timeout):
        self.calls.append({"gateway": gateway, "params": dict(params), "timeout": timeout})
        if isinstance(self.response, BaseException):
            raise self.response
        if callable(self.response):
            return self.response(len(self.calls))
        return self.response


def _unit_config(query_keys, **overrides) -> dict:
    """``query_trade`` 单元测试用的最小配置（dict 形式，适配器兼容）。"""
    config = {
        "ALIPAY_APP_ID": TEST_APP_ID,
        "ALIPAY_APP_PRIVATE_KEY_PATH": query_keys["app_private_path"],
        "ALIPAY_PUBLIC_KEY_PATH": query_keys["alipay_public_path"],
        "ALIPAY_GATEWAY": "https://openapi.alipay.com/gateway.do",
        "ALIPAY_QUERY_TIMEOUT": "10",
        "COMMERCIAL_BASE_URL": "http://127.0.0.1:8787",
    }
    config.update(overrides)
    return config


def _alipay_app_overrides(query_keys, **overrides) -> dict[str, str]:
    """商业服务测试 Config 的支付宝覆盖项（page 模式下单不调网关，GET 时才查单）。"""
    values: dict[str, str] = {
        "PAYMENT_PROVIDER": "alipay",
        "PAYMENT_MOCK_ENABLED": "false",
        "ALIPAY_APP_ID": TEST_APP_ID,
        "ALIPAY_APP_PRIVATE_KEY_PATH": query_keys["app_private_path"],
        "ALIPAY_PUBLIC_KEY_PATH": query_keys["alipay_public_path"],
        "ALIPAY_GATEWAY": "https://openapi.alipay.com/gateway.do",
        "ALIPAY_PAY_MODE": "page",
        "ALIPAY_NOTIFY_URL": "http://127.0.0.1:8787/notify/alipay",
        "ALIPAY_APP_ID_CHECK": "1",
        "ALIPAY_QUERY_FALLBACK": "1",
        "ALIPAY_QUERY_MIN_INTERVAL_SECONDS": "5",
        "ALIPAY_QUERY_TIMEOUT": "10",
    }
    values.update({str(key).upper(): str(value) for key, value in overrides.items()})
    return values


# ---------------------------------------------------------------------------
# 夹具：启用查单兜底的 app/client + DB 读取辅助
# ---------------------------------------------------------------------------


@pytest.fixture()
def fallback_app(make_config, query_keys):
    from server.app import create_app

    return create_app(make_config(**_alipay_app_overrides(query_keys)))


@pytest.fixture()
def fallback_client(fallback_app):
    from fastapi.testclient import TestClient

    with TestClient(fallback_app) as client:
        yield client


@pytest.fixture()
def order_store(fallback_app):
    """直接读该 app 的 SQLite（计数/事件），不依赖 conftest 里 mock 配置的 db 夹具。"""
    database = fallback_app.state.db

    def count(table: str) -> int:
        with database.connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    def events(order_no: str, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM events WHERE order_no=?"
        params: list = [order_no]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        with database.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    return {"database": database, "count": count, "events": events}


# ---------------------------------------------------------------------------
# 请求辅助
# ---------------------------------------------------------------------------


def _create_order(client) -> dict:
    response = client.post("/api/orders", json={
        "sku": "pro",
        "machine_code": VALID_MACHINE_CODE,
        "channel": "alipay",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["order_no"] and body["order_token"]
    return body


def _get_order(client, order: dict) -> tuple[int, dict]:
    response = client.get(f"/api/orders/{order['order_no']}",
                          params={"token": order["order_token"]})
    return response.status_code, response.json()


# ===========================================================================
# AlipayProvider.query_trade 单元测试
# ===========================================================================


@pytest.mark.parametrize("trade_status", ["TRADE_SUCCESS", "TRADE_FINISHED"])
def test_query_trade_success_signature_and_fields(query_keys, monkeypatch, trade_status):
    order_no = "ord_20261102180000_ab12cd34"
    transport = FakeTransport(_signed_query_response(
        _query_paid(order_no, 19900, trade_status=trade_status), query_keys["alipay_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    result = alipay_module.AlipayProvider().query_trade(
        order_no, _unit_config(query_keys, ALIPAY_QUERY_TIMEOUT="3"))

    assert result["out_trade_no"] == order_no
    assert result["trade_no"] == QUERY_TRADE_NO
    assert result["trade_status"] == trade_status
    assert result["total_amount_cents"] == 19900
    assert isinstance(result["raw"], dict)
    assert result["raw"]["code"] == "10000"

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["gateway"] == "https://openapi.alipay.com/gateway.do"
    assert call["timeout"] == 3.0
    params = call["params"]
    assert params["method"] == "alipay.trade.query"
    assert params["app_id"] == TEST_APP_ID
    assert json.loads(params["biz_content"]) == {"out_trade_no": order_no}
    # 请求签名可被应用公钥独立验证（测试自己实现待签串/验签）
    assert _rsa2_verify(_sign_content(params), params["sign"], query_keys["app_public_key"])


def test_query_trade_not_exist_is_not_an_error(query_keys, monkeypatch):
    transport = FakeTransport(_signed_query_response(
        _query_trade_not_exist(), query_keys["alipay_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    result = alipay_module.AlipayProvider().query_trade(
        "ord_20261102180000_ab12cd34", _unit_config(query_keys))

    assert result["trade_status"] == "NOT_EXIST"
    assert result["total_amount_cents"] == 0
    assert result["trade_no"] == ""
    assert len(transport.calls) == 1


def test_query_trade_business_error_raises(query_keys, monkeypatch):
    body = {"code": "40004", "msg": "Business Failed",
            "sub_code": "ACQ.SYSTEM_ERROR", "sub_msg": "系统错误"}
    transport = FakeTransport(_signed_query_response(body, query_keys["alipay_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    with pytest.raises(alipay_module.PaymentError) as ctx:
        alipay_module.AlipayProvider().query_trade(
            "ord_20261102180000_ab12cd34", _unit_config(query_keys))
    assert ctx.value.code == "payment_query_failed"
    assert "ACQ.SYSTEM_ERROR" in str(ctx.value)


def test_query_trade_bad_response_signature_raises_invalid_signature(query_keys, monkeypatch):
    # 用「应用私钥」冒充支付宝私钥签响应 → 验签必须失败，且错误码是 invalid_signature
    transport = FakeTransport(_signed_query_response(
        _query_paid("ord_20261102180000_ab12cd34"), query_keys["app_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    with pytest.raises(alipay_module.PaymentError) as ctx:
        alipay_module.AlipayProvider().query_trade(
            "ord_20261102180000_ab12cd34", _unit_config(query_keys))
    assert ctx.value.code == "invalid_signature"


def test_query_trade_gateway_failure_raises(query_keys, monkeypatch):
    transport = FakeTransport(
        alipay_module.PaymentError("payment_create_failed", "无法连接支付宝网关：timed out"))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    with pytest.raises(alipay_module.PaymentError) as ctx:
        alipay_module.AlipayProvider().query_trade(
            "ord_20261102180000_ab12cd34", _unit_config(query_keys))
    assert ctx.value.code == "payment_query_failed"


# ===========================================================================
# GET /api/orders/{no} 主动查单兜底
# ===========================================================================


def test_query_fallback_marks_paid_and_issues_license(
        fallback_client, query_keys, order_store, monkeypatch,
        test_license_key, license_service):
    order = _create_order(fallback_client)
    transport = FakeTransport(_signed_query_response(
        _query_paid(order["order_no"], order["amount_cents"]), query_keys["alipay_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    status_code, body = _get_order(fallback_client, order)
    assert status_code == 200, body
    assert body["ok"] is True
    assert body["status"] == "paid"
    assert body["paid_at"]

    license_obj = body["license"]
    assert license_obj is not None
    assert license_obj["license_id"]
    assert license_obj["sku"] == "pro"
    assert license_obj["machine_code"] == VALID_MACHINE_CODE
    assert "\n" not in license_obj["text"]  # 紧凑单行授权码
    container = json.loads(license_obj["text"])
    assert container["key_id"] == test_license_key["key_id"]
    assert container["payload"]["order_id"] == order["order_no"]

    # 与 notify 通道签发的授权完全兼容：产品 LicenseService 可直接导入为 paid
    imported = license_service.import_license(license_obj["text"])
    assert imported["state"] == "paid"

    # 幂等入账：payments/licenses 各 1 条；只发起 1 次查单
    assert order_store["count"]("payments") == 1
    assert order_store["count"]("licenses") == 1
    assert len(transport.calls) == 1


def test_repeated_get_does_not_duplicate_license(
        fallback_client, query_keys, order_store, monkeypatch):
    order = _create_order(fallback_client)
    transport = FakeTransport(_signed_query_response(
        _query_paid(order["order_no"], order["amount_cents"]), query_keys["alipay_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    first_status, first = _get_order(fallback_client, order)
    assert first_status == 200 and first["status"] == "paid"
    license_id = first["license"]["license_id"]
    calls_after_first = len(transport.calls)

    second_status, second = _get_order(fallback_client, order)
    assert second_status == 200
    assert second["status"] == "paid"
    assert second["license"]["license_id"] == license_id
    # 已 paid：不再查单，也不重复签发
    assert len(transport.calls) == calls_after_first
    assert order_store["count"]("payments") == 1
    assert order_store["count"]("licenses") == 1


class _Clock:
    """可控 monotonic 时钟（替换 app 模块的 time 引用，仅影响轮询兜底/限流）。"""

    def __init__(self, now: float) -> None:
        self.now = float(now)

    def monotonic(self) -> float:
        return self.now


def test_throttle_skips_http_within_interval(fallback_client, query_keys, monkeypatch):
    order = _create_order(fallback_client)
    transport = FakeTransport(_signed_query_response(
        _query_wait_buyer_pay(order["order_no"], order["amount_cents"]),
        query_keys["alipay_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    status1, body1 = _get_order(fallback_client, order)
    assert status1 == 200 and body1["status"] == "pending"
    assert len(transport.calls) == 1

    # 立即第二次轮询（前端每 2s 一次）：默认间隔 5s，节流窗口内不再发起 HTTP
    status2, body2 = _get_order(fallback_client, order)
    assert status2 == 200 and body2["status"] == "pending"
    assert len(transport.calls) == 1


def test_throttle_allows_query_after_interval(fallback_client, query_keys, monkeypatch):
    order = _create_order(fallback_client)
    transport = FakeTransport(_signed_query_response(
        _query_wait_buyer_pay(order["order_no"], order["amount_cents"]),
        query_keys["alipay_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)
    clock = _Clock(1000.0)
    monkeypatch.setattr(app_module, "time", clock)

    _get_order(fallback_client, order)                 # t=1000 → 查单 #1
    assert len(transport.calls) == 1
    clock.now += 1.0
    _get_order(fallback_client, order)                 # t=1001 → 节流，不查单
    assert len(transport.calls) == 1
    clock.now += 4.0
    _get_order(fallback_client, order)                 # t=1005 → 间隔 5s，查单 #2
    assert len(transport.calls) == 2


def test_amount_mismatch_keeps_pending_and_records_event(
        fallback_client, query_keys, order_store, monkeypatch):
    order = _create_order(fallback_client)
    assert order["amount_cents"] == 49900
    # 查单返回金额与订单金额不一致 → 不 finalize（防止改价/串单）
    transport = FakeTransport(_signed_query_response(
        _query_paid(order["order_no"], 100), query_keys["alipay_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    status_code, body = _get_order(fallback_client, order)
    assert status_code == 200
    assert body["status"] == "pending"
    assert body["license"] is None
    assert order_store["count"]("payments") == 0
    assert order_store["count"]("licenses") == 0
    mismatch = order_store["events"](order["order_no"], "query_amount_mismatch")
    assert len(mismatch) == 1
    detail = json.loads(mismatch[0]["detail"])
    assert detail["expected_cents"] == 49900
    assert detail["received_cents"] == 100
    assert len(transport.calls) == 1


@pytest.mark.parametrize("failure", [
    RuntimeError("boom"),
    alipay_module.PaymentError("payment_query_failed", "无法连接支付宝网关：timed out"),
])
def test_query_failure_keeps_pending_and_returns_200(
        fallback_client, order_store, monkeypatch, failure):
    order = _create_order(fallback_client)
    transport = FakeTransport(failure)
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    status_code, body = _get_order(fallback_client, order)
    assert status_code == 200  # 轮询接口必须稳定 200，绝不 500
    assert body["ok"] is True
    assert body["status"] == "pending"
    assert body["license"] is None
    assert order_store["count"]("licenses") == 0
    assert len(transport.calls) == 1


def test_bad_response_signature_keeps_pending(fallback_client, query_keys, monkeypatch):
    order = _create_order(fallback_client)
    # 用应用私钥冒充支付宝私钥签响应 → 验签失败 → 保持 pending
    transport = FakeTransport(_signed_query_response(
        _query_paid(order["order_no"], order["amount_cents"]), query_keys["app_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    status_code, body = _get_order(fallback_client, order)
    assert status_code == 200
    assert body["status"] == "pending"
    assert body["license"] is None


def test_bad_token_does_not_trigger_query(fallback_client, query_keys, monkeypatch):
    order = _create_order(fallback_client)
    transport = FakeTransport(_signed_query_response(
        _query_paid(order["order_no"], order["amount_cents"]), query_keys["alipay_key"]))
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    response = fallback_client.get(f"/api/orders/{order['order_no']}",
                                   params={"token": "wrong-token"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "order_not_found"
    assert transport.calls == []  # token 未通过不查单（防枚举/防探测）


def test_fallback_disabled_by_default_never_queries(make_config, query_keys, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app import create_app

    app = create_app(make_config(**_alipay_app_overrides(
        query_keys, ALIPAY_QUERY_FALLBACK="0")))
    transport = FakeTransport(b"should-not-be-used")
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    with TestClient(app) as client:
        order = _create_order(client)
        status_code, body = _get_order(client, order)
    assert status_code == 200
    assert body["status"] == "pending"
    assert transport.calls == []


def test_mock_order_never_queries_even_if_enabled(make_config, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app import create_app

    app = create_app(make_config(PAYMENT_PROVIDER="mock", ALIPAY_QUERY_FALLBACK="1"))
    transport = FakeTransport(b"should-not-be-used")
    monkeypatch.setattr(alipay_module, "_post_form", transport)

    with TestClient(app) as client:
        order = _create_order(client)
        assert order["provider"] == "mock"
        status_code, body = _get_order(client, order)
    assert status_code == 200
    assert body["status"] == "pending"
    assert transport.calls == []


# ===========================================================================
# Config 解析/校验
# ===========================================================================


def test_config_defaults_and_env_parsing(make_config):
    from server.config import Config

    cfg = make_config()
    assert cfg.ALIPAY_QUERY_FALLBACK is False
    assert cfg.ALIPAY_QUERY_MIN_INTERVAL_SECONDS == 5
    assert cfg.ALIPAY_QUERY_TIMEOUT == 10

    enabled = make_config(ALIPAY_QUERY_FALLBACK="true",
                          ALIPAY_QUERY_MIN_INTERVAL_SECONDS="3",
                          ALIPAY_QUERY_TIMEOUT="7")
    assert enabled.ALIPAY_QUERY_FALLBACK is True
    assert enabled.ALIPAY_QUERY_MIN_INTERVAL_SECONDS == 3
    assert enabled.ALIPAY_QUERY_TIMEOUT == 7
    assert enabled.to_env_dict()["ALIPAY_QUERY_FALLBACK"] == "true"
    assert enabled.to_env_dict()["ALIPAY_QUERY_MIN_INTERVAL_SECONDS"] == "3"
    assert enabled.to_env_dict()["ALIPAY_QUERY_TIMEOUT"] == "7"


def test_config_rejects_too_small_interval(make_config):
    from server.config import ConfigError

    with pytest.raises(ConfigError):
        make_config(ALIPAY_QUERY_MIN_INTERVAL_SECONDS="1")


def test_config_rejects_invalid_query_timeout(make_config):
    from server.config import ConfigError

    with pytest.raises(ConfigError):
        make_config(ALIPAY_QUERY_TIMEOUT="0")
