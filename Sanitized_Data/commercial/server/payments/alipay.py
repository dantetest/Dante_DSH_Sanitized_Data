"""支付宝支付适配器（RSA2）：当面付扫码预下单 + 电脑网站支付 + 异步通知验签 + 主动查单。

契约见 ``commercial/CONTRACT.md`` §4（Provider 鸭子类型接口）：

- ``AlipayProvider`` 支持无参构造：``AlipayProvider()``，之后
  ``create(order, config)`` / ``verify_notify(form, config)`` /
  ``query_trade(out_trade_no, config)``。
- ``create`` 返回 ``{"qr_content": str|None, "pay_url": str|None, "provider_ref": str|None}``；
  失败统一抛 ``PaymentError("payment_create_failed", 消息)``。
- ``verify_notify`` 验签（RSA2）+ 可选校验 ``app_id`` / ``seller_id`` +
  ``total_amount`` 元字符串 → int 分；返回
  ``{"order_no", "transaction_id", "amount_cents", "raw", "trade_status"}``。
- ``query_trade`` 主动查询交易（``alipay.trade.query``），供异步通知不可达/延迟时
  由 ``GET /api/orders/{no}`` 轮询兜底；验响应签名后返回
  ``{"out_trade_no", "trade_no", "trade_status", "total_amount_cents", "raw"}``。
- 金额、订单号、交易号等语义校验全部在验签通过之后进行。

实现约束：

- 只依赖标准库 + ``cryptography``；HTTP 走 ``urllib.request``（测试 monkeypatch
  ``urllib.request.urlopen``），不引入 requests/httpx 运行依赖。
- 配置对象为 T1 的 ``Config``（属性名与 env 同名、全大写），同时兼容
  ``dict``、普通对象和 ``os.environ``，便于单测与独立脚本使用。
- 应用私钥 / 支付宝公钥只在内存中使用，绝不打印、不写日志、不放进异常消息。
- 签名原文按支付宝规则：参数按 key 字典序、``k=v`` 用 ``&`` 连接、原始值不 URL 编码；
  异步通知剔除 ``sign`` / ``sign_type``；同步响应验签用响应对象在原始 JSON 文本中的原文。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

__all__ = [
    "GATEWAY_PROD",
    "GATEWAY_SANDBOX",
    "METHOD_PAGE_PAY",
    "METHOD_PRECREATE",
    "METHOD_QUERY",
    "NOTIFY_PAID_STATUSES",
    "RESPONSE_KEY_QUERY",
    "TRADE_NOT_EXIST_CODE",
    "TRADE_NOT_EXIST_STATUS",
    "PaymentError",
    "AlipayProvider",
    "build_page_url",
    "build_request_params",
    "build_sign_content",
    "cents_to_yuan",
    "extract_json_object_text",
    "load_private_key",
    "load_public_key",
    "parse_signed_response",
    "sign_content",
    "sign_params",
    "verify_content",
    "verify_params",
    "yuan_to_cents",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

GATEWAY_PROD = "https://openapi.alipay.com/gateway.do"
# 当前官方沙箱网关（2023 年后新沙箱地址）。旧文档里的 https://openapi.alipaydev.com/gateway.do
# 仍可能出现在老账号/老文档中，如遇沙箱域名不通可直接用 ALIPAY_GATEWAY 覆盖。
GATEWAY_SANDBOX = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"

METHOD_PRECREATE = "alipay.trade.precreate"
METHOD_PAGE_PAY = "alipay.trade.page.pay"
METHOD_QUERY = "alipay.trade.query"
RESPONSE_KEY_QUERY = "alipay_trade_query_response"
#: 主动查单时支付宝返回的业务码：交易不存在（下单后用户还没扫码/还没创建交易）
TRADE_NOT_EXIST_CODE = "ACQ.TRADE_NOT_EXIST"
#: 查单兜底返回给调用方的「未支付」状态（交易尚未创建）
TRADE_NOT_EXIST_STATUS = "NOT_EXIST"
SIGN_TYPE_RSA2 = "RSA2"
CHARSET = "utf-8"
FORMAT = "JSON"
API_VERSION = "1.0"
PAGE_PRODUCT_CODE = "FAST_INSTANT_TRADE_PAY"

NOTIFY_PAID_STATUSES = ("TRADE_SUCCESS", "TRADE_FINISHED")

MAX_AMOUNT_CENTS = 10_000_000_000  # 支付宝 total_amount 上限 100000000.00 元
MAX_SUBJECT_LEN = 256
MAX_OUT_TRADE_NO_LEN = 64
DEFAULT_HTTP_TIMEOUT = 10.0

_BEIJING_TZ = timezone(timedelta(hours=8))
_ORDER_NO_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


# ---------------------------------------------------------------------------
# 错误模型
# ---------------------------------------------------------------------------

try:  # 与 T1 ``payments/__init__.py`` 的 PaymentError 统一（app.py 精确捕获并记录 code）。
    # 以单文件方式加载（例如单测直接按路径 import）时相对导入会失败，降级为普通异常。
    from . import PaymentError as _BasePaymentError  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001 - 单文件加载 / 循环导入场景的降级
    _BasePaymentError = Exception


class PaymentError(_BasePaymentError):  # type: ignore[misc,valid-type]
    """支付相关错误：``code`` 供调用方分支处理，``message`` 面向日志/返回体。

    约定 code（create 路径统一为 ``payment_create_failed``，与 CONTRACT §3 一致）：
    - ``payment_create_failed``：下单/网关/响应签名等创建环节失败
    - ``payment_query_failed``：主动查单（alipay.trade.query）网关/业务失败
    - ``invalid_signature``：异步通知/主动查单响应验签失败
    - ``app_id_mismatch`` / ``seller_id_mismatch``：商户身份校验失败
    - ``invalid_amount``：金额不是合法的人民币元字符串（不支持非整分）
    - ``trade_status_not_paid``：通知状态不是 TRADE_SUCCESS/TRADE_FINISHED
    - ``notify_malformed``：通知缺少必要字段
    - ``alipay_key_invalid``：密钥读取/解析失败
    - ``alipay_config_invalid``：配置缺失（APP_ID 等）
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        self.message = str(message)
        super().__init__(code, message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict[str, str]:
        """返回 ``{"code": ..., "message": ...}``（供 app.py 拼错误响应）。"""
        return {"code": self.code, "message": self.message}


# ---------------------------------------------------------------------------
# 配置读取（兼容 Config 属性 / dict / 环境变量）
# ---------------------------------------------------------------------------


def _get_config(config: Any, name: str, default: Any = None) -> Any:
    """读取配置项。

    T1 的 ``Config`` 属性名与 env 同名（全大写）。这里额外兼容 dict、
    类 dict 对象以及直接读 ``os.environ``，且空字符串视为「未配置」。
    """
    candidates = (name, name.lower())
    if config is not None:
        if isinstance(config, Mapping):
            for key in candidates:
                if key in config:
                    value = config[key]
                    if value is not None and value != "":
                        return value
        else:
            getter = getattr(config, "get", None)
            if callable(getter):
                for key in candidates:
                    try:
                        value = getter(key)
                    except Exception:  # pragma: no cover - 防御性
                        value = None
                    if value is not None and value != "":
                        return value
            for attr in candidates:
                try:
                    value = getattr(config, attr)
                except Exception:  # pragma: no cover - 防御性
                    continue
                if value is not None and value != "":
                    return value
    value = os.environ.get(name)
    if value is not None and value != "":
        return value
    return default


def _config_bool(config: Any, name: str, default: bool = False) -> bool:
    value = _get_config(config, name, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _http_timeout(config: Any) -> float:
    raw = _get_config(config, "ALIPAY_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_HTTP_TIMEOUT
    if timeout <= 0:
        return DEFAULT_HTTP_TIMEOUT
    return min(timeout, 60.0)


def _query_timeout(config: Any) -> float:
    """主动查单超时：优先 ``ALIPAY_QUERY_TIMEOUT``，未配置时回落到通用 HTTP 超时。"""
    raw = _get_config(config, "ALIPAY_QUERY_TIMEOUT")
    if raw is None or raw == "":
        return _http_timeout(config)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_HTTP_TIMEOUT
    if timeout <= 0:
        return DEFAULT_HTTP_TIMEOUT
    return min(timeout, 60.0)


def _beijing_timestamp(now: datetime | None = None) -> str:
    """支付宝要求 ``yyyy-MM-dd HH:mm:ss`` 北京时间；这里固定 +08:00，不依赖服务器时区。"""
    if now is None:
        now = datetime.now(_BEIJING_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_BEIJING_TZ)
    return now.astimezone(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _pay_mode(config: Any) -> str:
    """返回 ``precreate`` 或 ``page``；也接受常见别名。"""
    raw = _get_config(config, "ALIPAY_PAY_MODE", "precreate")
    mode = str(raw or "precreate").strip().lower()
    aliases = {
        "precreate": "precreate",
        "qr": "precreate",
        "qrcode": "precreate",
        "scan": "precreate",
        "native": "precreate",
        "page": "page",
        "web": "page",
        "pc": "page",
        "wap": "page",
    }
    if mode not in aliases:
        raise PaymentError(
            "payment_create_failed",
            f"ALIPAY_PAY_MODE 非法：{mode!r}（只支持 precreate 或 page）",
        )
    return aliases[mode]


def _gateway_url(config: Any) -> str:
    raw = _get_config(config, "ALIPAY_GATEWAY")
    gateway = str(raw).strip() if raw else GATEWAY_PROD
    alias = gateway.lower()
    if alias in ("sandbox", "test", "dev", "sandbox_env"):
        return GATEWAY_SANDBOX
    if alias in ("prod", "production", "live", "online"):
        return GATEWAY_PROD
    if not (gateway.startswith("https://") or gateway.startswith("http://")):
        raise PaymentError(
            "payment_create_failed",
            f"ALIPAY_GATEWAY 不是合法的 http(s) 地址：{gateway!r}",
        )
    return gateway


def _app_id(config: Any) -> str:
    app_id = str(_get_config(config, "ALIPAY_APP_ID") or "").strip()
    if not app_id:
        raise PaymentError(
            "alipay_config_invalid",
            "未配置 ALIPAY_APP_ID（支付宝开放平台应用 APPID）",
        )
    return app_id


def _private_key_spec(config: Any) -> Any:
    for name in ("ALIPAY_APP_PRIVATE_KEY_PATH", "ALIPAY_APP_PRIVATE_KEY"):
        value = _get_config(config, name)
        if value:
            return value
    raise PaymentError(
        "alipay_key_invalid",
        "未配置支付宝应用私钥（ALIPAY_APP_PRIVATE_KEY_PATH，支持 PEM 或裸 base64）",
    )


def _public_key_spec(config: Any) -> Any:
    for name in ("ALIPAY_PUBLIC_KEY_PATH", "ALIPAY_PUBLIC_KEY"):
        value = _get_config(config, name)
        if value:
            return value
    raise PaymentError(
        "alipay_key_invalid",
        "未配置支付宝公钥（ALIPAY_PUBLIC_KEY_PATH，用于异步通知与响应验签）",
    )


# ---------------------------------------------------------------------------
# 密钥读取（PEM / PKCS8 / PKCS1 / 裸 base64）
# ---------------------------------------------------------------------------


def _looks_like_key_path(text: str) -> bool:
    """启发式判断配置值是「文件路径」而不是裸 base64（用于给出更清晰的报错）。"""
    lowered = text.strip().lower()
    if any(lowered.endswith(suffix) for suffix in (".pem", ".key", ".p8", ".crt", ".cer", ".txt")):
        return True
    if "\\" in text:
        return True
    if len(text) > 1 and text[1] == ":":  # Windows 盘符，如 C:\keys\app.pem
        return True
    return False


def _read_key_material(spec: Any) -> bytes:
    """把配置值解析成密钥字节：PEM 文本、文件路径或裸 base64 字符串。"""
    if isinstance(spec, (bytes, bytearray)):
        raw = bytes(spec)
        if not raw.strip():
            raise PaymentError("alipay_key_invalid", "密钥内容为空")
        return raw
    if isinstance(spec, os.PathLike):
        return _read_key_file(Path(spec))
    if isinstance(spec, str):
        text = spec.strip()
        if not text:
            raise PaymentError("alipay_key_invalid", "密钥内容为空")
        if "BEGIN" in text.upper():
            return text.encode("utf-8")
        path = Path(text)
        try:
            if path.is_file():
                return _read_key_file(path)
        except OSError:
            pass
        if _looks_like_key_path(text):
            raise PaymentError(
                "alipay_key_invalid",
                f"无法读取密钥文件：{path.name}（文件不存在或路径错误；"
                "相对路径按 Sanitized_Data 根目录解析）",
            )
        return text.encode("utf-8")
    raise PaymentError(
        "alipay_key_invalid",
        f"密钥配置类型不支持：{type(spec).__name__}（应为路径或 PEM/base64 字符串）",
    )


def _read_key_file(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PaymentError(
            "alipay_key_invalid",
            f"无法读取密钥文件：{path.name}（{type(exc).__name__}）",
        ) from exc


def _pem_wrap(b64_text: str, label: str) -> bytes:
    body = "\n".join(b64_text[index : index + 64] for index in range(0, len(b64_text), 64))
    return f"-----BEGIN {label}-----\n{body}\n-----END {label}-----\n".encode("ascii")


def _b64decode_loose(text: bytes) -> bytes:
    compact = b"".join(text.split()).decode("ascii", "ignore")
    if not compact:
        raise PaymentError("alipay_key_invalid", "密钥 base64 内容为空")
    try:
        return base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PaymentError(
            "alipay_key_invalid",
            "密钥不是有效 PEM，也不是合法的 base64（请重新从开放平台复制）",
        ) from exc


def load_private_key(spec: Any, password: str | bytes | None = None) -> rsa.RSAPrivateKey:
    """读取应用私钥：PEM(PKCS8/PKCS1，可带口令) 或裸 base64（自动补 PKCS8 头）。"""
    raw = _read_key_material(spec)
    password_bytes: bytes | None = None
    if password not in (None, ""):
        password_bytes = password.encode("utf-8") if isinstance(password, str) else password

    key: Any = None
    if b"BEGIN" in raw.upper():
        try:
            key = serialization.load_pem_private_key(raw, password=password_bytes)
        except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
            raise PaymentError(
                "alipay_key_invalid",
                f"应用私钥 PEM 解析失败（{type(exc).__name__}），请确认是 RSA2 应用私钥且口令正确",
            ) from exc
    else:
        der = _b64decode_loose(raw)
        b64_text = base64.b64encode(der).decode("ascii")
        try:
            key = serialization.load_pem_private_key(
                _pem_wrap(b64_text, "PRIVATE KEY"), password=password_bytes
            )
        except (ValueError, TypeError, UnsupportedAlgorithm):
            try:
                key = serialization.load_der_private_key(der, password=password_bytes)
            except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
                raise PaymentError(
                    "alipay_key_invalid",
                    "应用私钥 base64 解析失败（应为 PKCS8/PKCS1 的 base64，或带 PEM 头的 PEM 文件）",
                ) from exc

    if not isinstance(key, rsa.RSAPrivateKey):
        raise PaymentError(
            "alipay_key_invalid",
            "应用私钥不是 RSA 私钥（请确认使用 RSA2/2048 位应用私钥，而不是 Ed25519 等其他密钥）",
        )
    return key


def load_public_key(spec: Any) -> rsa.RSAPublicKey:
    """读取支付宝公钥：PEM(SPKI/PKCS1)、X.509 证书 PEM 或裸 base64。"""
    raw = _read_key_material(spec)
    key: Any = None

    if b"BEGIN CERTIFICATE" in raw.upper():
        try:
            from cryptography import x509

            certificate = x509.load_pem_x509_certificate(raw)
            key = certificate.public_key()
        except Exception as exc:  # noqa: BLE001 - 证书解析异常类型较多
            raise PaymentError(
                "alipay_key_invalid",
                f"支付宝公钥证书解析失败（{type(exc).__name__}）",
            ) from exc
    elif b"BEGIN" in raw.upper():
        if b"PRIVATE KEY" in raw.upper():
            raise PaymentError(
                "alipay_key_invalid",
                "ALIPAY_PUBLIC_KEY_PATH 配置的是私钥，应填「支付宝公钥」（不是应用私钥）",
            )
        try:
            key = serialization.load_pem_public_key(raw)
        except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
            raise PaymentError(
                "alipay_key_invalid",
                f"支付宝公钥 PEM 解析失败（{type(exc).__name__}）",
            ) from exc
    else:
        der = _b64decode_loose(raw)
        try:
            key = serialization.load_der_public_key(der)
        except (ValueError, TypeError, UnsupportedAlgorithm):
            b64_text = base64.b64encode(der).decode("ascii")
            try:
                key = serialization.load_pem_public_key(_pem_wrap(b64_text, "PUBLIC KEY"))
            except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
                raise PaymentError(
                    "alipay_key_invalid",
                    "支付宝公钥 base64 解析失败（应为 SPKI 公钥的 base64 或 PEM 文件）",
                ) from exc

    if not isinstance(key, rsa.RSAPublicKey):
        raise PaymentError(
            "alipay_key_invalid", "支付宝公钥不是 RSA 公钥（请确认 RSA2 对应的支付宝公钥）"
        )
    return key


# ---------------------------------------------------------------------------
# 金额换算
# ---------------------------------------------------------------------------


def cents_to_yuan(cents: Any) -> str:
    """int 分 → ``"199.00"`` 形式的元字符串（支付宝 total_amount）。"""
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise PaymentError("invalid_amount", f"金额（分）必须是整数，收到 {type(cents).__name__}")
    if cents < 1 or cents > MAX_AMOUNT_CENTS:
        raise PaymentError(
            "invalid_amount",
            f"金额超出支付宝允许范围（0.01~100000000.00 元），收到 {cents} 分",
        )
    return f"{Decimal(cents) / Decimal(100):.2f}"


def yuan_to_cents(value: Any) -> int:
    """``"199.00"`` 元字符串 → int 分；拒绝非整分/非法值（异步通知金额核对用）。"""
    if value is None:
        raise PaymentError("invalid_amount", "缺少 total_amount（交易金额）")
    text = str(value).strip()
    if not text:
        raise PaymentError("invalid_amount", "total_amount 为空")
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise PaymentError("invalid_amount", f"total_amount 不是合法数字：{text!r}") from exc
    if not amount.is_finite():
        raise PaymentError("invalid_amount", f"total_amount 不是有限数字：{text!r}")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise PaymentError("invalid_amount", f"total_amount 精度超过 1 分：{text!r}")
    cents_int = int(cents)
    if cents_int < 1 or cents_int > MAX_AMOUNT_CENTS:
        raise PaymentError(
            "invalid_amount",
            f"total_amount 超出支付宝允许范围（0.01~100000000.00 元）：{text!r}",
        )
    return cents_int


# ---------------------------------------------------------------------------
# RSA2 签名 / 验签
# ---------------------------------------------------------------------------


def build_sign_content(params: Mapping[str, Any]) -> str:
    """按支付宝规则拼待签名字符串：剔除 ``sign``，key 字典序，``k=v`` 用 ``&`` 连接。

    - 值使用原始字符串，**不做 URL 编码**（URL 编码只在生成请求 URL/表单时做）。
    - 空值（``None``/``""``）不参与签名，与官方 SDK 一致。
    """
    parts: list[str] = []
    for key in sorted(params):
        if key == "sign":
            continue
        value = params[key]
        if value is None:
            continue
        if isinstance(value, bytes):
            text = value.decode("utf-8")
        else:
            text = str(value)
        if text == "":
            continue
        parts.append(f"{key}={text}")
    return "&".join(parts)


def sign_content(content: str, private_key: rsa.RSAPrivateKey) -> str:
    """PKCS1v15 + SHA256 签名，返回 base64（支付宝 RSA2）。"""
    signature = private_key.sign(
        content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
    )
    return base64.b64encode(signature).decode("ascii")


def verify_content(content: str, signature: str, public_key: rsa.RSAPublicKey) -> bool:
    """RSA2 验签；任何格式问题都返回 ``False``（由调用方决定错误码）。"""
    if not signature:
        return False
    try:
        raw_signature = base64.b64decode(str(signature).strip(), validate=False)
    except (ValueError, binascii.Error):
        return False
    try:
        public_key.verify(
            raw_signature,
            content.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def sign_params(
    params: Mapping[str, Any], private_key: rsa.RSAPrivateKey
) -> dict[str, str]:
    """对待请求参数签名，返回新 dict（追加 ``sign``，不修改入参）。"""
    data = {str(key): ("" if value is None else str(value)) for key, value in params.items()}
    data.pop("sign", None)
    data["sign"] = sign_content(build_sign_content(data), private_key)
    return data


def verify_params(params: Mapping[str, Any], public_key: rsa.RSAPublicKey) -> bool:
    """校验请求参数签名（``sign`` 之外的参数按字典序拼原文）。"""
    data = {str(key): ("" if value is None else str(value)) for key, value in params.items()}
    signature = data.pop("sign", "")
    return verify_content(build_sign_content(data), signature, public_key)


# ---------------------------------------------------------------------------
# 请求构造
# ---------------------------------------------------------------------------


def build_request_params(
    method: str,
    biz: Mapping[str, Any],
    config: Any,
    *,
    notify_url: str | None = None,
    return_url: str | None = None,
    timestamp: str | None = None,
) -> dict[str, str]:
    """构造支付宝公共请求参数（未签名）。

    ``notify_url`` / ``return_url`` 是**公共参数**（官方文档：notify_url 属于公共参数，
    不能放进 biz_content），这里放在顶层。
    """
    params: dict[str, str] = {
        "app_id": _app_id(config),
        "method": str(method),
        "format": FORMAT,
        "charset": CHARSET,
        "sign_type": SIGN_TYPE_RSA2,
        "timestamp": timestamp or _beijing_timestamp(),
        "version": API_VERSION,
        "biz_content": json.dumps(dict(biz), ensure_ascii=False, separators=(",", ":")),
    }
    if notify_url:
        params["notify_url"] = str(notify_url)
    if return_url:
        params["return_url"] = str(return_url)
    return params


def build_page_url(gateway: str, params: Mapping[str, str]) -> str:
    """把已签名参数拼成 GET/POST 跳转 URL（电脑网站支付用）。"""
    separator = "&" if "?" in gateway else "?"
    return f"{gateway}{separator}{urllib.parse.urlencode(list(params.items()))}"


# ---------------------------------------------------------------------------
# 响应解析 / 验签
# ---------------------------------------------------------------------------


def extract_json_object_text(raw_text: str, response_key: str) -> str | None:
    """从原始 JSON 文本中截取指定 key 对应对象的**原文**（保持字节级一致）。

    支付宝同步响应验签的对象是响应对象在响应报文中的原始 JSON 文本，
    不能 ``json.dumps`` 重新序列化，否则可能因空白/转义差异导致验签失败。
    """
    if not raw_text or not response_key:
        return None
    marker = f'"{response_key}"'
    index = raw_text.find(marker)
    if index < 0:
        return None
    colon = raw_text.find(":", index + len(marker))
    if colon < 0:
        return None
    start = raw_text.find("{", colon)
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(raw_text)):
        char = raw_text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return raw_text[start : position + 1]
    return None


def parse_signed_response(
    raw: bytes | str, response_key: str, public_key: rsa.RSAPublicKey
) -> dict[str, Any]:
    """解析同步响应并用支付宝公钥验签，返回响应对象 dict。

    响应形如 ``{"<response_key>": {...}, "sign": "..."}``，``sign`` 是响应对象
    原始 JSON 文本的 RSA2 签名。验签失败抛 ``PaymentError``。
    """
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if not text.strip():
        raise PaymentError("payment_create_failed", "支付宝网关返回空响应")
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PaymentError(
            "payment_create_failed", "支付宝网关响应不是合法 JSON（可能是网关/网络错误页）"
        ) from exc
    if not isinstance(envelope, dict):
        raise PaymentError("payment_create_failed", "支付宝网关响应格式非法（不是 JSON 对象）")

    content = extract_json_object_text(text, response_key)
    if content is None:
        raise PaymentError(
            "payment_create_failed", f"支付宝响应缺少 {response_key} 对象"
        )
    signature = envelope.get("sign")
    if not signature:
        raise PaymentError(
            "payment_create_failed", f"支付宝响应缺少签名（sign），已拒绝：{response_key}"
        )
    if not verify_content(content, str(signature), public_key):
        raise PaymentError(
            "alipay_response_sign_invalid",
            "支付宝响应验签失败：请确认 ALIPAY_PUBLIC_KEY_PATH 填的是「支付宝公钥」而非应用公钥，且未被换行/空格破坏",
        )
    body = envelope.get(response_key)
    if not isinstance(body, dict):
        raise PaymentError("payment_create_failed", f"支付宝响应 {response_key} 不是 JSON 对象")
    return body


# ---------------------------------------------------------------------------
# HTTP（urllib.request；测试 monkeypatch urlopen）
# ---------------------------------------------------------------------------


def _post_form(gateway: str, params: Mapping[str, str], timeout: float) -> bytes:
    """以 ``application/x-www-form-urlencoded`` POST 到支付宝网关。"""
    body = urllib.parse.urlencode(list(params.items())).encode("utf-8")
    request = urllib.request.Request(
        gateway,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "SanitizedData-Commercial/1.0 (python-urllib)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read()[:500].decode("utf-8", "replace")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - 防御性
            detail = ""
        raise PaymentError(
            "payment_create_failed",
            f"支付宝网关返回 HTTP {exc.code}" + (f"：{detail}" if detail else ""),
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise PaymentError(
            "payment_create_failed", f"无法连接支付宝网关：{reason}"
        ) from exc


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


def _flat_str(value: Any) -> str:
    """把 form 值统一成字符串（兼容 parse_qs 的 list 值和 None）。"""
    if isinstance(value, (list, tuple)):
        return "" if not value else _flat_str(value[0])
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if value is None:
        return ""
    return str(value)


def _validate_order(order: Any) -> tuple[str, int, str]:
    if not isinstance(order, Mapping):
        raise PaymentError("payment_create_failed", "订单数据非法（应为 dict）")
    order_no = _flat_str(order.get("order_no")).strip()
    if not order_no:
        raise PaymentError("payment_create_failed", "订单缺少 order_no")
    if len(order_no) > MAX_OUT_TRADE_NO_LEN or not _ORDER_NO_RE.fullmatch(order_no):
        raise PaymentError(
            "payment_create_failed",
            f"order_no 不合法（支付宝要求 1~64 位字母/数字/下划线）：{order_no!r}",
        )
    try:
        raw_amount = order.get("amount_cents")
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, int):
            raise TypeError(f"amount_cents 类型：{type(raw_amount).__name__}")
        amount_cents = raw_amount
    except TypeError as exc:
        raise PaymentError(
            "payment_create_failed", "订单缺少合法的 amount_cents（整数分）"
        ) from exc
    subject = _flat_str(order.get("subject")).strip() or "数据隐站授权"
    return order_no, amount_cents, subject[:MAX_SUBJECT_LEN]


def _resolve_notify_url(config: Any, order: Mapping[str, Any]) -> str:
    value = _get_config(config, "ALIPAY_NOTIFY_URL")
    if not value:
        value = order.get("notify_url")
    if not value:
        base = _get_config(config, "COMMERCIAL_BASE_URL")
        if base:
            value = str(base).strip().rstrip("/") + "/notify/alipay"
    if not value:
        raise PaymentError(
            "payment_create_failed",
            "未配置 ALIPAY_NOTIFY_URL（或 COMMERCIAL_BASE_URL），无法接收支付宝异步通知",
        )
    url = str(value).strip()
    if not (url.startswith("https://") or url.startswith("http://")):
        raise PaymentError(
            "payment_create_failed",
            f"notify_url 必须是 http(s) 地址（生产必须 HTTPS）：{url!r}",
        )
    return url


class AlipayProvider:
    """支付宝支付 Provider（鸭子类型，CONTRACT §4）。

    - ``create(order, config)``：按 ``ALIPAY_PAY_MODE`` 走当面付预下单（precreate）
      或电脑网站支付（page）。
    - ``verify_notify(form, config)``：异步通知验签 + 身份/金额/状态校验。

    支持 ``AlipayProvider()`` 无参构造（``get_provider`` 会这么用），也可以
    ``AlipayProvider(config)`` 预先绑定配置。
    """

    name = "alipay"
    label = "支付宝扫码"

    def __init__(self, config: Any = None) -> None:
        self._config = config

    # -- 内部工具 ---------------------------------------------------------
    def _resolve_config(self, config: Any) -> Any:
        return config if config is not None else self._config

    def _load_keys(self, config: Any) -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
        password = _get_config(config, "ALIPAY_APP_PRIVATE_KEY_PASSWORD")
        private_key = load_private_key(_private_key_spec(config), password=password)
        public_key = load_public_key(_public_key_spec(config))
        return private_key, public_key

    # -- create -----------------------------------------------------------
    def create(self, order: Mapping[str, Any], config: Any = None) -> dict[str, Any]:
        """创建支付：返回 ``{"qr_content", "pay_url", "provider_ref"}``。"""
        cfg = self._resolve_config(config)
        try:
            order_no, amount_cents, subject = _validate_order(order)
            self._validate_order_amount(amount_cents)
            mode = _pay_mode(cfg)
            if mode == "precreate":
                return self._create_precreate(order, order_no, amount_cents, subject, cfg)
            return self._create_page(order, order_no, amount_cents, subject, cfg)
        except PaymentError as exc:
            if exc.code == "payment_create_failed":
                raise
            raise PaymentError("payment_create_failed", exc.message) from exc
        except Exception as exc:  # noqa: BLE001 - 兜底，避免把未知异常漏给 app
            raise PaymentError(
                "payment_create_failed", f"支付宝下单异常（{type(exc).__name__}）"
            ) from exc

    @staticmethod
    def _validate_order_amount(amount_cents: int) -> None:
        # 复用金额换算做范围校验，保证报错信息与支付宝限制一致
        cents_to_yuan(amount_cents)

    def _create_precreate(
        self,
        order: Mapping[str, Any],
        order_no: str,
        amount_cents: int,
        subject: str,
        config: Any,
    ) -> dict[str, Any]:
        private_key, public_key = self._load_keys(config)
        gateway = _gateway_url(config)
        notify_url = _resolve_notify_url(config, order)
        biz = {
            "out_trade_no": order_no,
            "total_amount": cents_to_yuan(amount_cents),
            "subject": subject,
        }
        # notify_url 是支付宝公共参数（官方文档），随请求参数一起签名。
        params = build_request_params(
            METHOD_PRECREATE, biz, config, notify_url=notify_url
        )
        signed = sign_params(params, private_key)
        raw_response = _post_form(gateway, signed, _http_timeout(config))
        body = parse_signed_response(raw_response, "alipay_trade_precreate_response", public_key)

        code = _flat_str(body.get("code") or "").strip()
        if code != "10000":
            sub_code = _flat_str(body.get("sub_code") or code).strip() or "UNKNOWN"
            sub_msg = _flat_str(body.get("sub_msg") or body.get("msg") or "未知错误").strip()
            raise PaymentError(
                "payment_create_failed", f"支付宝预下单失败：{sub_code} {sub_msg}".strip()
            )
        qr_code = _flat_str(body.get("qr_code")).strip()
        if not qr_code:
            raise PaymentError(
                "payment_create_failed",
                "支付宝预下单响应缺少 qr_code（请确认应用已签约「当面付」）",
            )
        provider_ref = _flat_str(body.get("trade_no")).strip() or None
        return {"qr_content": qr_code, "pay_url": None, "provider_ref": provider_ref}

    def _create_page(
        self,
        order: Mapping[str, Any],
        order_no: str,
        amount_cents: int,
        subject: str,
        config: Any,
    ) -> dict[str, Any]:
        private_key = load_private_key(
            _private_key_spec(config),
            password=_get_config(config, "ALIPAY_APP_PRIVATE_KEY_PASSWORD"),
        )
        gateway = _gateway_url(config)
        notify_url = _resolve_notify_url(config, order)
        return_url = _flat_str(
            order.get("return_url") or _get_config(config, "ALIPAY_RETURN_URL")
        ).strip()
        biz = {
            "out_trade_no": order_no,
            "total_amount": cents_to_yuan(amount_cents),
            "subject": subject,
            "product_code": PAGE_PRODUCT_CODE,
        }
        qr_pay_mode = str(_get_config(config, "ALIPAY_QR_PAY_MODE", "") or "").strip()
        if qr_pay_mode == "2":
            # 支付宝页面扫码：跳转模式下二维码由支付宝收银台生成，商户页面不画二维码。
            biz["qr_pay_mode"] = "2"
        params = build_request_params(
            METHOD_PAGE_PAY,
            biz,
            config,
            notify_url=notify_url,
            return_url=return_url or None,
        )
        signed = sign_params(params, private_key)
        pay_url = build_page_url(gateway, signed)
        return {"qr_content": pay_url, "pay_url": pay_url, "provider_ref": None,
                "qr_pay_mode": qr_pay_mode or None}

    # -- verify_notify ----------------------------------------------------
    def verify_notify(self, form: Any, config: Any = None) -> dict[str, Any]:
        """校验支付宝异步通知，返回交易字段；不通过抛 ``PaymentError``。

        注意：金额与订单的核对由调用方（app.py）用 ``amount_cents`` 与 DB 比对；
        这里只负责签名、商户身份、金额格式、交易状态。
        """
        cfg = self._resolve_config(config)
        if not isinstance(form, Mapping) or not form:
            raise PaymentError("notify_malformed", "支付宝异步通知为空或格式非法")

        raw_form = {str(key): _flat_str(value) for key, value in form.items()}
        signature = raw_form.get("sign", "").strip()
        sign_type = (raw_form.get("sign_type") or SIGN_TYPE_RSA2).strip().upper()
        if not signature:
            raise PaymentError("notify_malformed", "支付宝异步通知缺少 sign 参数")
        if sign_type != SIGN_TYPE_RSA2:
            raise PaymentError("invalid_signature", f"仅支持 RSA2 签名（收到 {sign_type}）")

        public_key = load_public_key(_public_key_spec(cfg))
        signed_params = {
            key: value for key, value in raw_form.items() if key not in ("sign", "sign_type")
        }
        if not verify_content(build_sign_content(signed_params), signature, public_key):
            raise PaymentError(
                "invalid_signature",
                "支付宝异步通知验签失败（请确认 ALIPAY_PUBLIC_KEY_PATH 是支付宝公钥）",
            )

        expected_app_id = str(_get_config(cfg, "ALIPAY_APP_ID") or "").strip()
        if _config_bool(cfg, "ALIPAY_APP_ID_CHECK", True):
            if not expected_app_id:
                raise PaymentError(
                    "alipay_config_invalid",
                    "已开启 app_id 校验（ALIPAY_APP_ID_CHECK=1）但未配置 ALIPAY_APP_ID",
                )
            if raw_form.get("app_id", "") != expected_app_id:
                raise PaymentError(
                    "app_id_mismatch",
                    f"异步通知 app_id 与配置不一致：{raw_form.get('app_id', '')!r} != {expected_app_id!r}",
                )

        expected_seller_id = str(_get_config(cfg, "ALIPAY_SELLER_ID") or "").strip()
        if expected_seller_id and raw_form.get("seller_id", "") != expected_seller_id:
            raise PaymentError(
                "seller_id_mismatch",
                f"异步通知 seller_id 与配置不一致：{raw_form.get('seller_id', '')!r} != {expected_seller_id!r}",
            )

        order_no = raw_form.get("out_trade_no", "").strip()
        if not order_no:
            raise PaymentError("notify_malformed", "支付宝异步通知缺少 out_trade_no")
        amount_cents = yuan_to_cents(raw_form.get("total_amount"))
        trade_status = raw_form.get("trade_status", "").strip().upper()
        if trade_status not in NOTIFY_PAID_STATUSES:
            raise PaymentError(
                "trade_status_not_paid",
                f"交易状态 {trade_status or '空'} 不是支付成功（仅接受 TRADE_SUCCESS/TRADE_FINISHED）",
            )
        transaction_id = raw_form.get("trade_no", "").strip()
        if not transaction_id:
            raise PaymentError("notify_malformed", "支付宝异步通知缺少 trade_no")

        return {
            "order_no": order_no,
            "transaction_id": transaction_id,
            "amount_cents": amount_cents,
            "raw": raw_form,
            "trade_status": trade_status,
        }

    # -- query_trade ------------------------------------------------------
    def query_trade(self, out_trade_no: Any, config: Any = None) -> dict[str, Any]:
        """主动查询交易（``alipay.trade.query``），供通知不可达时的轮询兜底。

        流程与 ``create`` 一致：构造公共参数 + ``biz_content={"out_trade_no": ...}`` →
        RSA2 签名 → POST 网关 → 用**支付宝公钥**验响应签名 → 解析交易字段。

        返回 ``{"out_trade_no", "trade_no", "trade_status", "total_amount_cents", "raw"}``：

        - 交易不存在（业务码 ``ACQ.TRADE_NOT_EXIST``）**不抛异常**，返回
          ``trade_status="NOT_EXIST"``（便于前端每 2s 轮询）；``total_amount_cents`` 为 0；
        - 响应验签失败抛 ``PaymentError("invalid_signature", ...)``；
        - 网关/网络/其他业务错误抛 ``PaymentError("payment_query_failed", ...)``（或
          ``alipay_key_invalid`` / ``alipay_config_invalid`` 等配置错误），由调用方决定
          是否保持 pending。
        """
        cfg = self._resolve_config(config)
        order_no = _flat_str(out_trade_no).strip()
        if not order_no:
            raise PaymentError("payment_query_failed", "查单缺少 out_trade_no")
        if len(order_no) > MAX_OUT_TRADE_NO_LEN or not _ORDER_NO_RE.fullmatch(order_no):
            raise PaymentError(
                "payment_query_failed",
                f"out_trade_no 不合法（支付宝要求 1~64 位字母/数字/下划线）：{order_no!r}",
            )

        private_key, public_key = self._load_keys(cfg)
        params = build_request_params(METHOD_QUERY, {"out_trade_no": order_no}, cfg)
        signed = sign_params(params, private_key)
        try:
            raw_response = _post_form(_gateway_url(cfg), signed, _query_timeout(cfg))
        except PaymentError as exc:
            raise PaymentError("payment_query_failed", exc.message) from exc
        try:
            body = parse_signed_response(raw_response, RESPONSE_KEY_QUERY, public_key)
        except PaymentError as exc:
            if getattr(exc, "code", "") == "alipay_response_sign_invalid":
                raise PaymentError("invalid_signature", exc.message) from exc
            raise PaymentError("payment_query_failed", exc.message) from exc

        code = _flat_str(body.get("code") or "").strip()
        sub_code = _flat_str(body.get("sub_code") or "").strip()
        sub_msg = _flat_str(body.get("sub_msg") or body.get("msg") or "").strip()
        trade_status = _flat_str(body.get("trade_status") or "").strip().upper()
        trade_no = _flat_str(body.get("trade_no") or "").strip()
        returned_order_no = _flat_str(body.get("out_trade_no") or order_no).strip() or order_no

        if code != "10000":
            if sub_code == TRADE_NOT_EXIST_CODE:
                # 下单后用户还没扫码/交易尚未创建：不是错误，按「未支付」返回给轮询方
                return {
                    "out_trade_no": returned_order_no,
                    "trade_no": trade_no,
                    "trade_status": TRADE_NOT_EXIST_STATUS,
                    "total_amount_cents": 0,
                    "raw": dict(body),
                }
            detail = " ".join(part for part in (sub_code or code, sub_msg) if part).strip()
            raise PaymentError(
                "payment_query_failed", f"支付宝查单失败：{detail or '未知错误'}"
            )

        if not trade_status:
            raise PaymentError("payment_query_failed", "支付宝查单响应缺少 trade_status")
        total_amount_cents = 0
        total_amount = _flat_str(body.get("total_amount") or "").strip()
        if total_amount:
            total_amount_cents = yuan_to_cents(total_amount)
        return {
            "out_trade_no": returned_order_no,
            "trade_no": trade_no,
            "trade_status": trade_status,
            "total_amount_cents": total_amount_cents,
            "raw": dict(body),
        }
