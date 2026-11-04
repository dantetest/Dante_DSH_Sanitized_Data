"""支付宝适配器单测（RSA2 签名/验签、precreate/page 下单、异步通知、金额、边界）。

特点：
- 不联网：所有 HTTP 调用 monkeypatch ``urllib.request.urlopen``。
- 不用真实密钥：测试内用 ``cryptography`` 现生成两对 RSA 2048 密钥
  （应用密钥对 / 支付宝密钥对），私钥只存在于内存与临时目录。
- 直接按文件路径加载 ``commercial/server/payments/alipay.py``，避免依赖 T1 的
  ``payments/__init__.py`` 是否已就绪。

运行（在 Sanitized_Data 目录下）：
    <python> -X utf8 -m pytest commercial/tests/test_alipay.py -q
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

COMMERCIAL_DIR = Path(__file__).resolve().parents[1]
SERVER_DIR = COMMERCIAL_DIR / "server"
ALIPAY_PATH = SERVER_DIR / "payments" / "alipay.py"

# 契约要求测试内自行插入 commercial/server，便于 import payments.* / yinzhan.*
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


def _load_alipay_module():
    if not ALIPAY_PATH.is_file():
        raise RuntimeError(f"找不到被测模块：{ALIPAY_PATH}")
    spec = importlib.util.spec_from_file_location("alipay_under_test", ALIPAY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


alipay = _load_alipay_module()
PaymentError = alipay.PaymentError

# 环境变量归一化：避免开发机上真实 ALIPAY_* 环境影响断言。
_EMPTY_ENV = {
    "ALIPAY_APP_ID": "",
    "ALIPAY_APP_PRIVATE_KEY_PATH": "",
    "ALIPAY_APP_PRIVATE_KEY": "",
    "ALIPAY_PUBLIC_KEY_PATH": "",
    "ALIPAY_PUBLIC_KEY": "",
    "ALIPAY_GATEWAY": "",
    "ALIPAY_PAY_MODE": "",
    "ALIPAY_NOTIFY_URL": "",
    "ALIPAY_SELLER_ID": "",
    "ALIPAY_APP_ID_CHECK": "",
    "ALIPAY_RETURN_URL": "",
    "ALIPAY_HTTP_TIMEOUT": "",
    "ALIPAY_APP_PRIVATE_KEY_PASSWORD": "",
    "COMMERCIAL_BASE_URL": "",
}

_KEYS: dict[str, object] = {}


def _keys() -> dict[str, object]:
    """进程内只生成一次测试密钥（两对 RSA 2048）。"""
    if not _KEYS:
        app_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        alipay_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _KEYS.update(
            {
                "app_key": app_key,
                "alipay_key": alipay_key,
                "app_private_pem": _pem_private(app_key),
                "app_private_pkcs1_pem": _pem_private(app_key, pkcs1=True),
                "app_public_pem": _pem_public(app_key),
                "alipay_private_pem": _pem_private(alipay_key),
                "alipay_public_pem": _pem_public(alipay_key),
            }
        )
    return _KEYS


def _pem_private(key: rsa.RSAPrivateKey, *, pkcs1: bool = False) -> str:
    private_format = (
        serialization.PrivateFormat.TraditionalOpenSSL
        if pkcs1
        else serialization.PrivateFormat.PKCS8
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=private_format,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _pem_public(key: rsa.RSAPrivateKey) -> str:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _pem_body(pem_text: str) -> str:
    """PEM 文本的裸 base64 主体（模拟开放平台「密钥工具」复制的无头私钥）。"""
    lines = [line.strip() for line in pem_text.strip().splitlines() if "-----" not in line]
    return "".join(lines)


def _rsa_public_key(pem_text: str):
    return alipay.load_public_key(pem_text)


class _FakeHTTPResponse:
    """模拟 urlopen 返回值（context manager + read）。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, *args, **kwargs) -> bytes:  # noqa: ANN002, ANN003 - 兼容 file-like
        return self._payload

    def getcode(self) -> int:
        return 200

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *exc_info) -> bool:  # noqa: ANN002 - context manager 协议
        return False


def _fake_urlopen(payload, captured: list | None = None):
    """构造伪 urlopen：payload 为 bytes/str（响应体）或 Exception（直接抛出）。"""

    def fake(request, timeout=None, **kwargs):  # noqa: ANN001, ANN003
        if captured is not None:
            captured.append({"request": request, "timeout": timeout})
        if isinstance(payload, Exception):
            raise payload
        body = payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode()
        return _FakeHTTPResponse(bytes(body))

    return fake


def _parse_form_request(request) -> dict[str, str]:
    """解析 POST 请求体（application/x-www-form-urlencoded）为扁平 dict。"""
    raw = request.data
    assert raw is not None, "请求应携带 form body"
    parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


def _parse_query(url: str) -> dict[str, str]:
    parsed = parse_qs(urlparse(url).query, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


def _signed_response_body(response_key: str, response_obj: dict, signing_key) -> str:
    """伪造支付宝同步响应：响应对应的原始 JSON 文本用「支付宝私钥」签名。"""
    raw_obj = json.dumps(response_obj, ensure_ascii=False, separators=(",", ":"))
    signature = alipay.sign_content(raw_obj, signing_key)
    return '{"%s":%s,"sign":"%s"}' % (response_key, raw_obj, signature)


class AlipayTestBase(unittest.TestCase):
    """Provider 相关测试基类：测试密钥、临时 PEM 文件、配置/订单工厂。"""

    @classmethod
    def setUpClass(cls) -> None:
        keys = _keys()
        cls.app_key = keys["app_key"]
        cls.alipay_key = keys["alipay_key"]
        cls.app_private_pem = keys["app_private_pem"]
        cls.app_public_pem = keys["app_public_pem"]
        cls.alipay_private_pem = keys["alipay_private_pem"]
        cls.alipay_public_pem = keys["alipay_public_pem"]
        cls.app_public_key = _rsa_public_key(cls.app_public_pem)
        cls.alipay_public_key = _rsa_public_key(cls.alipay_public_pem)

        # 注意：这里不用 tempfile.TemporaryDirectory/mkdtemp —— Windows 下它们用 0o700
        # 建目录，在受限沙箱里会因目录 ACL 收紧而无法写入/清理；改为普通 mkdir 的
        # 唯一临时目录（权限继承默认 ACL），测试结束尽力清理。
        base = Path(tempfile.gettempdir())
        cls._keys_dir = base / f"alipay-test-keys-{uuid.uuid4().hex[:8]}"
        cls._keys_dir.mkdir()
        cls.app_private_path = cls._keys_dir / "app_private.pem"
        cls.app_private_path.write_text(cls.app_private_pem, encoding="ascii")
        cls.alipay_public_path = cls._keys_dir / "alipay_public.pem"
        cls.alipay_public_path.write_text(cls.alipay_public_pem, encoding="ascii")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._keys_dir, ignore_errors=True)

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, _EMPTY_ENV, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- 工厂 -------------------------------------------------------------
    def make_config(self, **overrides):
        config = {
            "ALIPAY_APP_ID": "2021000000000001",
            "ALIPAY_APP_PRIVATE_KEY_PATH": str(self.app_private_path),
            "ALIPAY_PUBLIC_KEY_PATH": str(self.alipay_public_path),
            "ALIPAY_GATEWAY": alipay.GATEWAY_SANDBOX,
            "ALIPAY_PAY_MODE": "precreate",
            "ALIPAY_NOTIFY_URL": "https://buy.example.com/notify/alipay",
            "ALIPAY_SELLER_ID": "",
            "ALIPAY_APP_ID_CHECK": "1",
            "COMMERCIAL_BASE_URL": "https://buy.example.com",
        }
        config.update(overrides)
        return config

    def make_order(self, **overrides):
        order = {
            "order_no": "ord_20261102180000_ab12cd34",
            "sku": "pro",
            "amount_cents": 49900,
            "subject": "数据隐站 专业版 · 1年",
            "machine_code": "ZH39-7N5C-98CD-A8GB",
            "token": "token_abc123",
            "return_url": "https://buy.example.com/order.html?order_no=ord_20261102180000_ab12cd34",
        }
        order.update(overrides)
        return order

    def make_notify_form(self, **overrides):
        form = {
            "notify_time": "2026-11-02 18:00:00",
            "notify_type": "trade_status_sync",
            "notify_id": "91722adff935e8cfa58b3aabf4dead6i",
            "app_id": "2021000000000001",
            "charset": "utf-8",
            "version": "1.0",
            "sign_type": "RSA2",
            "trade_no": "2026110222001400000000000001",
            "out_trade_no": "ord_20261102180000_ab12cd34",
            "total_amount": "499.00",
            "trade_status": "TRADE_SUCCESS",
            "seller_id": "2088000000000001",
            "buyer_logon_id": "buyer***@example.com",
        }
        form.update(overrides)
        return form

    def sign_notify(self, form, key=None):
        """按支付宝规则用「支付宝私钥」给异步通知表单签名。"""
        key = key or self.alipay_key
        unsigned = {
            name: value for name, value in form.items() if name not in ("sign", "sign_type")
        }
        content = alipay.build_sign_content(unsigned)
        signed = dict(form)
        signed["sign"] = alipay.sign_content(content, key)
        signed.setdefault("sign_type", "RSA2")
        return signed

    def assertPaymentError(self, code: str | None, func, *args, **kwargs):
        with self.assertRaises(PaymentError) as ctx:
            func(*args, **kwargs)
        if code is not None:
            self.assertEqual(code, ctx.exception.code, msg=str(ctx.exception))
        return ctx.exception


# ===========================================================================
# 金额换算
# ===========================================================================


class AmountTests(unittest.TestCase):
    def test_cents_to_yuan_format(self):
        cases = {1: "0.01", 10: "0.10", 100: "1.00", 19900: "199.00", 49900: "499.00"}
        for cents, expected in cases.items():
            with self.subTest(cents=cents):
                self.assertEqual(expected, alipay.cents_to_yuan(cents))

    def test_cents_to_yuan_rejects_invalid(self):
        for bad in (0, -1, 10_000_000_001, True, "100", 1.5, None):
            with self.subTest(bad=bad):
                self.assertRaises(PaymentError, alipay.cents_to_yuan, bad)

    def test_yuan_to_cents(self):
        cases = {
            "0.01": 1,
            "0.1": 10,
            "1": 100,
            "199": 19900,
            "199.00": 19900,
            "499.00": 49900,
            "100000000.00": 10_000_000_000,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, alipay.yuan_to_cents(text))

    def test_yuan_to_cents_rejects_non_cent_values(self):
        for bad in ("1.234", "0.001", "abc", "", None, "0", "-1", "100000000.01", "NaN", "Infinity"):
            with self.subTest(bad=bad):
                self.assertRaises(PaymentError, alipay.yuan_to_cents, bad)

    def test_amount_roundtrip(self):
        for cents in (1, 5, 99, 100, 101, 19900, 49900, 199900, 9999999999):
            with self.subTest(cents=cents):
                self.assertEqual(cents, alipay.yuan_to_cents(alipay.cents_to_yuan(cents)))


# ===========================================================================
# 签名 / 验签 / 报文拼接
# ===========================================================================


class SigningTests(AlipayTestBase):
    def test_build_sign_content_sorted_and_raw_values(self):
        params = {
            "b": "2",
            "a": "数据 隐站",
            "sign": "IGNORED",
            "empty": "",
            "skip": None,
            "Method": "alipay.trade.precreate",
        }
        content = alipay.build_sign_content(params)
        # 字典序：Method < a < b（大写字母 ASCII 更小）；空值/None/sign 不参与
        self.assertEqual("Method=alipay.trade.precreate&a=数据 隐站&b=2", content)
        # 原始值不 URL 编码
        self.assertNotIn("%", content)

    def test_request_signature_can_be_verified_by_app_public_key(self):
        config = self.make_config()
        biz = {"out_trade_no": "ord_1", "total_amount": "0.01", "subject": "测试"}
        params = alipay.build_request_params(
            "alipay.trade.precreate",
            biz,
            config,
            notify_url="https://buy.example.com/notify/alipay",
        )
        signed = alipay.sign_params(params, self.app_key)
        self.assertIn("sign", signed)
        self.assertEqual(params, {k: v for k, v in signed.items() if k != "sign"})

        self.assertTrue(alipay.verify_content(alipay.build_sign_content(params), signed["sign"], self.app_public_key))
        self.assertTrue(alipay.verify_params(signed, self.app_public_key))

        # 篡改任一参数 → 验签失败
        tampered = dict(signed)
        tampered["biz_content"] = tampered["biz_content"].replace("0.01", "0.02")
        self.assertFalse(alipay.verify_params(tampered, self.app_public_key))

        bad_sign = dict(signed)
        bad_sign["sign"] = "AAAA" + signed["sign"][4:]
        self.assertFalse(alipay.verify_params(bad_sign, self.app_public_key))

    def test_request_params_fields(self):
        config = self.make_config()
        params = alipay.build_request_params(
            alipay.METHOD_PRECREATE,
            {"out_trade_no": "ord_1", "total_amount": "0.01", "subject": "测试"},
            config,
            notify_url="https://buy.example.com/notify/alipay",
        )
        self.assertEqual("2021000000000001", params["app_id"])
        self.assertEqual("alipay.trade.precreate", params["method"])
        self.assertEqual("JSON", params["format"])
        self.assertEqual("utf-8", params["charset"])
        self.assertEqual("RSA2", params["sign_type"])
        self.assertEqual("1.0", params["version"])
        self.assertRegex(params["timestamp"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        biz = json.loads(params["biz_content"])
        self.assertEqual("ord_1", biz["out_trade_no"])
        self.assertEqual("0.01", biz["total_amount"])
        self.assertEqual("测试", biz["subject"])

    def test_build_page_url_separator(self):
        params = {"a": "1 2", "sign": "abc+/="}
        self.assertTrue(
            alipay.build_page_url("https://openapi.alipay.com/gateway.do", params).startswith(
                "https://openapi.alipay.com/gateway.do?"
            )
        )
        with_query = alipay.build_page_url("https://example.com/gateway.do?x=1", params)
        self.assertTrue(with_query.startswith("https://example.com/gateway.do?x=1&"))
        self.assertEqual("1 2", _parse_query(with_query)["a"])

    def test_extract_json_object_text_keeps_exact_raw_text(self):
        raw = '{"alipay_trade_precreate_response": {"a": "x}y", "b": {"c": 1}},"sign": "zzz"}'
        extracted = alipay.extract_json_object_text(raw, "alipay_trade_precreate_response")
        self.assertEqual('{"a": "x}y", "b": {"c": 1}}', extracted)
        self.assertIsNone(alipay.extract_json_object_text(raw, "not_exists"))
        self.assertIsNone(alipay.extract_json_object_text("{}", "alipay_trade_precreate_response"))

    def test_sign_params_does_not_mutate_input(self):
        params = {"app_id": "1", "method": "x"}
        snapshot = dict(params)
        alipay.sign_params(params, self.app_key)
        self.assertEqual(snapshot, params)


# ===========================================================================
# 密钥读取
# ===========================================================================


class KeyLoadingTests(AlipayTestBase):
    def test_private_key_forms(self):
        key = _keys()["app_key"]
        expected_public = key.public_key()
        candidates = {
            "pkcs8_pem_path": str(self.app_private_path),
            "pkcs8_pem_inline": self.app_private_pem,
            "pkcs1_pem_inline": _keys()["app_private_pkcs1_pem"],
            "bare_base64_pkcs8": _pem_body(self.app_private_pem),
            "pathlib_path": self.app_private_path,
        }
        for name, spec in candidates.items():
            with self.subTest(name=name):
                loaded = alipay.load_private_key(spec)
                self.assertEqual(expected_public.public_numbers(), loaded.public_key().public_numbers())

    def test_public_key_forms(self):
        public_pem = _keys()["alipay_public_pem"]
        expected = _rsa_public_key(public_pem).public_numbers()
        for name, spec in {
            "pem_path": str(self.alipay_public_path),
            "pem_inline": public_pem,
            "bare_base64": _pem_body(public_pem),
        }.items():
            with self.subTest(name=name):
                loaded = alipay.load_public_key(spec)
                self.assertEqual(expected, loaded.public_numbers())

    def test_public_key_rejects_private_key_material(self):
        error = self.assertPaymentError(
            "alipay_key_invalid", alipay.load_public_key, self.app_private_pem
        )
        self.assertIn("私钥", str(error))

    def test_invalid_key_material(self):
        self.assertPaymentError("alipay_key_invalid", alipay.load_private_key, "not-a-key!!!")
        self.assertPaymentError("alipay_key_invalid", alipay.load_public_key, "not-a-key!!!")
        self.assertPaymentError("alipay_key_invalid", alipay.load_private_key, "")
        # 错误消息不得包含密钥正文
        with self.assertRaises(PaymentError) as ctx:
            alipay.load_private_key("-----BEGIN PRIVATE KEY-----\nZZZZ\n-----END PRIVATE KEY-----")
        self.assertNotIn("ZZZZ", str(ctx.exception))

    def test_missing_key_file(self):
        error = self.assertPaymentError(
            "alipay_key_invalid",
            alipay.load_private_key,
            str(self._keys_dir / "no-such-key.pem"),
        )
        self.assertIn("不存在", str(error))


# ===========================================================================
# precreate（当面付扫码）
# ===========================================================================


class PrecreateTests(AlipayTestBase):
    def _precreate_body(self, **overrides):
        response_obj = {
            "code": "10000",
            "msg": "Success",
            "out_trade_no": "ord_20261102180000_ab12cd34",
            "qr_code": "https://qr.alipay.com/bavh4wjlxf12tper3a",
            "trade_no": "2026110222001400000000000001",
        }
        response_obj.update(overrides)
        return _signed_response_body("alipay_trade_precreate_response", response_obj, self.alipay_key)

    def test_precreate_success_returns_qr_code(self):
        captured: list = []
        body = self._precreate_body()
        with mock.patch.object(alipay.urllib.request, "urlopen", _fake_urlopen(body, captured)):
            result = alipay.AlipayProvider().create(self.make_order(), self.make_config())

        self.assertEqual(
            {"qr_content": "https://qr.alipay.com/bavh4wjlxf12tper3a", "pay_url": None,
             "provider_ref": "2026110222001400000000000001"},
            result,
        )
        self.assertEqual(1, len(captured))

    def test_precreate_request_is_signed_and_contains_biz(self):
        captured: list = []
        body = self._precreate_body()
        with mock.patch.object(alipay.urllib.request, "urlopen", _fake_urlopen(body, captured)):
            alipay.AlipayProvider().create(self.make_order(), self.make_config())

        request = captured[0]["request"]
        self.assertEqual("POST", request.get_method())
        self.assertIn("openapi-sandbox.dl.alipaydev.com", request.full_url)
        params = _parse_form_request(request)
        self.assertTrue(alipay.verify_params(params, self.app_public_key), "请求签名应可被应用公钥验证")
        self.assertEqual("alipay.trade.precreate", params["method"])
        self.assertEqual("2021000000000001", params["app_id"])
        self.assertEqual("https://buy.example.com/notify/alipay", params["notify_url"])
        biz = json.loads(params["biz_content"])
        self.assertEqual("ord_20261102180000_ab12cd34", biz["out_trade_no"])
        self.assertEqual("499.00", biz["total_amount"])
        self.assertEqual("数据隐站 专业版 · 1年", biz["subject"])
        self.assertNotIn("sign", biz)
        self.assertEqual(10.0, captured[0]["timeout"])

    def test_precreate_with_attribute_style_config(self):
        """T1 的 Config 是属性对象（属性名与 env 同名），必须兼容。"""

        class AttrConfig:
            pass

        config = AttrConfig()
        for name, value in self.make_config().items():
            setattr(config, name, value)

        body = self._precreate_body(trade_no="")
        with mock.patch.object(alipay.urllib.request, "urlopen", _fake_urlopen(body)):
            result = alipay.AlipayProvider().create(self.make_order(), config)
        self.assertEqual("https://qr.alipay.com/bavh4wjlxf12tper3a", result["qr_content"])
        self.assertIsNone(result["provider_ref"])

    def test_precreate_response_bad_signature_rejected(self):
        # 用「应用私钥」冒充支付宝私钥签响应 → 验签必须失败
        raw_obj = json.dumps(
            {"code": "10000", "msg": "Success", "qr_code": "https://qr.alipay.com/fake"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        forged = '{"alipay_trade_precreate_response":%s,"sign":"%s"}' % (
            raw_obj,
            alipay.sign_content(raw_obj, self.app_key),
        )
        with mock.patch.object(alipay.urllib.request, "urlopen", _fake_urlopen(forged)):
            error = self.assertPaymentError(
                "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), self.make_config()
            )
        self.assertIn("验签", str(error))

    def test_precreate_business_error(self):
        body = self._precreate_body(
            code="40004",
            msg="Business Failed",
            sub_code="ACQ.ACCESS_FORBIDDEN",
            sub_msg="无权限使用接口",
            qr_code="",
        )
        with mock.patch.object(alipay.urllib.request, "urlopen", _fake_urlopen(body)):
            error = self.assertPaymentError(
                "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), self.make_config()
            )
        self.assertIn("ACQ.ACCESS_FORBIDDEN", str(error))

    def test_precreate_missing_qr_code(self):
        body = self._precreate_body(qr_code="")
        with mock.patch.object(alipay.urllib.request, "urlopen", _fake_urlopen(body)):
            self.assertPaymentError(
                "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), self.make_config()
            )

    def test_precreate_gateway_network_error(self):
        with mock.patch.object(
            alipay.urllib.request, "urlopen", _fake_urlopen(urllib.error.URLError("timed out"))
        ):
            error = self.assertPaymentError(
                "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), self.make_config()
            )
        self.assertIn("网关", str(error))

    def test_precreate_gateway_http_error(self):
        http_error = urllib.error.HTTPError(
            "https://openapi.alipay.com/gateway.do",
            503,
            "Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"code":"20000","msg":"busy"}'),
        )
        with mock.patch.object(alipay.urllib.request, "urlopen", _fake_urlopen(http_error)):
            error = self.assertPaymentError(
                "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), self.make_config()
            )
        self.assertIn("HTTP 503", str(error))

    def test_precreate_malformed_response(self):
        with mock.patch.object(alipay.urllib.request, "urlopen", _fake_urlopen("<html>gateway error</html>")):
            error = self.assertPaymentError(
                "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), self.make_config()
            )
        self.assertIn("JSON", str(error))


# ===========================================================================
# page（电脑网站支付）
# ===========================================================================


class PageModeTests(AlipayTestBase):
    def test_page_mode_returns_signed_pay_url_without_network(self):
        config = self.make_config(ALIPAY_PAY_MODE="page")
        order = self.make_order()

        def _no_network(*args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("page 模式不应发起网络请求")

        with mock.patch.object(alipay.urllib.request, "urlopen", _no_network):
            result = alipay.AlipayProvider().create(order, config)

        pay_url = result["pay_url"]
        self.assertIsNotNone(pay_url)
        self.assertEqual(pay_url, result["qr_content"])
        self.assertIsNone(result["provider_ref"])

        self.assertTrue(pay_url.startswith("https://openapi-sandbox.dl.alipaydev.com/gateway.do?"))
        params = _parse_query(pay_url)
        self.assertEqual("alipay.trade.page.pay", params["method"])
        self.assertEqual("https://buy.example.com/notify/alipay", params["notify_url"])
        self.assertEqual(order["return_url"], params["return_url"])
        self.assertTrue(alipay.verify_params(params, self.app_public_key))
        biz = json.loads(params["biz_content"])
        self.assertEqual("FAST_INSTANT_TRADE_PAY", biz["product_code"])
        self.assertEqual("499.00", biz["total_amount"])
        self.assertEqual(order["order_no"], biz["out_trade_no"])

    def test_page_mode_does_not_need_alipay_public_key(self):
        config = self.make_config(ALIPAY_PAY_MODE="page", ALIPAY_PUBLIC_KEY_PATH="")
        result = alipay.AlipayProvider().create(self.make_order(), config)
        self.assertIn("method=alipay.trade.page.pay", result["pay_url"])

    def test_page_mode_uses_commercial_base_url_for_notify_fallback(self):
        config = self.make_config(
            ALIPAY_PAY_MODE="page", ALIPAY_NOTIFY_URL="", COMMERCIAL_BASE_URL="https://buy.example.com/"
        )
        result = alipay.AlipayProvider().create(self.make_order(), config)
        params = _parse_query(result["pay_url"])
        self.assertEqual("https://buy.example.com/notify/alipay", params["notify_url"])

    def test_page_mode_qr_pay_mode_2_is_embedded_in_signed_biz_content(self):
        config = self.make_config(ALIPAY_PAY_MODE="page", ALIPAY_QR_PAY_MODE="2")
        result = alipay.AlipayProvider().create(self.make_order(), config)
        params = _parse_query(result["pay_url"])
        biz = json.loads(params["biz_content"])
        self.assertEqual("2", biz["qr_pay_mode"])
        self.assertEqual("2", result["qr_pay_mode"])
        self.assertTrue(alipay.verify_params(params, self.app_public_key))

    def test_page_mode_without_qr_pay_mode_keeps_old_behavior(self):
        config = self.make_config(ALIPAY_PAY_MODE="page")
        result = alipay.AlipayProvider().create(self.make_order(), config)
        params = _parse_query(result["pay_url"])
        biz = json.loads(params["biz_content"])
        self.assertNotIn("qr_pay_mode", biz)
        self.assertIsNone(result["qr_pay_mode"])


# ===========================================================================
# 异步通知 verify_notify
# ===========================================================================


class VerifyNotifyTests(AlipayTestBase):
    def test_valid_notify(self):
        form = self.sign_notify(self.make_notify_form())
        result = alipay.AlipayProvider().verify_notify(form, self.make_config())
        self.assertEqual("ord_20261102180000_ab12cd34", result["order_no"])
        self.assertEqual("2026110222001400000000000001", result["transaction_id"])
        self.assertEqual(49900, result["amount_cents"])
        self.assertEqual("TRADE_SUCCESS", result["trade_status"])
        self.assertIn("sign", result["raw"])
        self.assertEqual("499.00", result["raw"]["total_amount"])

    def test_notify_amount_yuan_to_cents(self):
        for total_amount, expected in {"0.01": 1, "199.00": 19900, "0.10": 10}.items():
            with self.subTest(total_amount=total_amount):
                config = self.make_config(ALIPAY_APP_ID_CHECK="0")
                form = self.sign_notify(self.make_notify_form(total_amount=total_amount))
                result = alipay.AlipayProvider().verify_notify(form, config)
                self.assertEqual(expected, result["amount_cents"])

    def test_trade_finished_accepted(self):
        form = self.sign_notify(self.make_notify_form(trade_status="TRADE_FINISHED"))
        result = alipay.AlipayProvider().verify_notify(form, self.make_config())
        self.assertEqual("TRADE_FINISHED", result["trade_status"])

    def test_tampered_amount_rejected(self):
        form = self.sign_notify(self.make_notify_form())
        form["total_amount"] = "0.01"
        self.assertPaymentError(
            "invalid_signature", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_tampered_sign_rejected(self):
        form = self.sign_notify(self.make_notify_form())
        form["sign"] = "A" + form["sign"][1:]
        self.assertPaymentError(
            "invalid_signature", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_tampered_trade_status_rejected(self):
        form = self.sign_notify(self.make_notify_form())
        form["trade_status"] = "TRADE_CLOSED"
        self.assertPaymentError(
            "invalid_signature", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_notify_signed_by_wrong_key_rejected(self):
        form = self.sign_notify(self.make_notify_form(), key=self.app_key)
        self.assertPaymentError(
            "invalid_signature", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_app_id_mismatch(self):
        form = self.sign_notify(self.make_notify_form(app_id="2021999999999999"))
        self.assertPaymentError(
            "app_id_mismatch", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_app_id_check_disabled(self):
        form = self.sign_notify(self.make_notify_form(app_id="2021999999999999"))
        config = self.make_config(ALIPAY_APP_ID_CHECK="0")
        result = alipay.AlipayProvider().verify_notify(form, config)
        self.assertEqual("2021999999999999", result["raw"]["app_id"])

    def test_seller_id_mismatch(self):
        form = self.sign_notify(self.make_notify_form(seller_id="2088000000000001"))
        config = self.make_config(ALIPAY_SELLER_ID="2088111111111111")
        self.assertPaymentError(
            "seller_id_mismatch", alipay.AlipayProvider().verify_notify, form, config
        )

    def test_seller_id_match(self):
        form = self.sign_notify(self.make_notify_form(seller_id="2088000000000001"))
        config = self.make_config(ALIPAY_SELLER_ID="2088000000000001")
        result = alipay.AlipayProvider().verify_notify(form, config)
        self.assertEqual("2088000000000001", result["raw"]["seller_id"])

    def test_trade_status_not_paid(self):
        form = self.sign_notify(self.make_notify_form(trade_status="WAIT_BUYER_PAY"))
        self.assertPaymentError(
            "trade_status_not_paid", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_missing_sign(self):
        form = self.make_notify_form()
        self.assertPaymentError(
            "notify_malformed", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_missing_total_amount(self):
        form = self.make_notify_form()
        form.pop("total_amount")
        form = self.sign_notify(form)
        self.assertPaymentError(
            "invalid_amount", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_non_cent_amount_rejected(self):
        form = self.sign_notify(self.make_notify_form(total_amount="499.001"))
        self.assertPaymentError(
            "invalid_amount", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_missing_trade_no(self):
        form = self.make_notify_form()
        form.pop("trade_no")
        form = self.sign_notify(form)
        self.assertPaymentError(
            "notify_malformed", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_non_rsa2_sign_type_rejected(self):
        form = self.sign_notify(self.make_notify_form())
        form["sign_type"] = "RSA"
        self.assertPaymentError(
            "invalid_signature", alipay.AlipayProvider().verify_notify, form, self.make_config()
        )

    def test_missing_public_key(self):
        form = self.sign_notify(self.make_notify_form())
        config = self.make_config(ALIPAY_PUBLIC_KEY_PATH="")
        self.assertPaymentError(
            "alipay_key_invalid", alipay.AlipayProvider().verify_notify, form, config
        )

    def test_empty_form(self):
        for form in (None, {}, "not-a-form"):
            with self.subTest(form=form):
                self.assertPaymentError(
                    "notify_malformed", alipay.AlipayProvider().verify_notify, form, self.make_config()
                )

    def test_notify_can_be_replayed(self):
        """同一通知重复验签结果稳定（幂等由调用方按 transaction_id 处理）。"""
        form = self.sign_notify(self.make_notify_form())
        provider = alipay.AlipayProvider()
        first = provider.verify_notify(form, self.make_config())
        second = provider.verify_notify(dict(form), self.make_config())
        self.assertEqual(first, second)


# ===========================================================================
# 配置 / 边界
# ===========================================================================


class ConfigEdgeTests(AlipayTestBase):
    def test_provider_duck_type_attributes(self):
        provider = alipay.AlipayProvider()
        self.assertEqual("alipay", provider.name)
        self.assertEqual("支付宝扫码", provider.label)

    def test_missing_app_id(self):
        config = self.make_config(ALIPAY_APP_ID="")
        error = self.assertPaymentError(
            "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), config
        )
        self.assertIn("ALIPAY_APP_ID", str(error))

    def test_missing_private_key(self):
        config = self.make_config(ALIPAY_APP_PRIVATE_KEY_PATH="")
        self.assertPaymentError(
            "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), config
        )

    def test_missing_public_key_precreate(self):
        config = self.make_config(ALIPAY_PUBLIC_KEY_PATH="")
        with mock.patch.object(
            alipay.urllib.request,
            "urlopen",
            _fake_urlopen('{"alipay_trade_precreate_response":{"code":"10000"},"sign":"x"}'),
        ):
            self.assertPaymentError(
                "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), config
            )

    def test_invalid_pay_mode(self):
        config = self.make_config(ALIPAY_PAY_MODE="h5")
        error = self.assertPaymentError(
            "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), config
        )
        self.assertIn("ALIPAY_PAY_MODE", str(error))

    def test_missing_notify_url(self):
        config = self.make_config(ALIPAY_NOTIFY_URL="", COMMERCIAL_BASE_URL="")
        self.assertPaymentError(
            "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), config
        )

    def test_invalid_order_amount(self):
        config = self.make_config(ALIPAY_PAY_MODE="page")
        for amount in (0, -100, 10_000_000_001, "49900", 49900.0, True, None):
            with self.subTest(amount=amount):
                self.assertPaymentError(
                    "payment_create_failed",
                    alipay.AlipayProvider().create,
                    self.make_order(amount_cents=amount),
                    config,
                )

    def test_invalid_order_no(self):
        config = self.make_config(ALIPAY_PAY_MODE="page")
        for order_no in ("", "bad order", "a" * 65):
            with self.subTest(order_no=order_no):
                self.assertPaymentError(
                    "payment_create_failed",
                    alipay.AlipayProvider().create,
                    self.make_order(order_no=order_no),
                    config,
                )

    def test_create_without_config(self):
        error = self.assertPaymentError(
            "payment_create_failed", alipay.AlipayProvider().create, self.make_order()
        )
        self.assertIn("ALIPAY_APP_PRIVATE_KEY", str(error))

    def test_bound_provider_config(self):
        config = self.make_config(ALIPAY_PAY_MODE="page")
        provider = alipay.AlipayProvider(config)
        result = provider.create(self.make_order())
        self.assertIn("alipay.trade.page.pay", result["pay_url"])

    def test_invalid_gateway(self):
        config = self.make_config(ALIPAY_GATEWAY="ftp://example.com/gateway.do", ALIPAY_PAY_MODE="page")
        self.assertPaymentError(
            "payment_create_failed", alipay.AlipayProvider().create, self.make_order(), config
        )

    def test_gateway_alias_sandbox(self):
        config = self.make_config(ALIPAY_GATEWAY="sandbox", ALIPAY_PAY_MODE="page")
        result = alipay.AlipayProvider().create(self.make_order(), config)
        self.assertTrue(result["pay_url"].startswith(alipay.GATEWAY_SANDBOX))

    def test_payment_error_to_dict(self):
        error = PaymentError("payment_create_failed", "boom")
        self.assertEqual("payment_create_failed", error.code)
        self.assertEqual("boom", error.message)
        self.assertEqual({"code": "payment_create_failed", "message": "boom"}, error.to_dict())
        self.assertIn("payment_create_failed", str(error))


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
