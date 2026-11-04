# -*- coding: utf-8 -*-
"""支付提供者（Provider）注册表。

契约 §4：
- Provider 是鸭子类型：``name`` / ``label`` / ``create(order, config)`` /
  ``verify_notify(form, config)``；实例由 ``get_provider(name, config)`` 无参构造。
- ``alipay`` 适配器由 T2 并行实现，因此这里**延迟导入**：缺失时抛
  :class:`PaymentError`（code=channel_unavailable），不阻塞 mock 全链路。
- mock 通道仅用于本地演示/测试，不联网。
"""
from __future__ import annotations

from typing import Any

#: 所有支付错误统一用这个异常（T2 alipay.py 也复用）
class PaymentError(Exception):
    """支付通道错误；``code``/``message`` 映射 API 错误体，``status`` 为建议 HTTP 码。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)


# 注意：PaymentError 必须先定义，再导入 mock（mock.py 会 `from . import PaymentError`）
from .mock import MockProvider  # noqa: E402

#: 通道元数据（展示顺序 == 用户可选项顺序）
CHANNEL_LABELS: dict[str, str] = {
    "alipay": "支付宝扫码",
    "mock": "模拟支付（演示）",
}
CHANNEL_ORDER: tuple[str, ...] = ("alipay", "mock")

_PROVIDERS: dict[str, type] = {
    "mock": MockProvider,
    # "alipay" 延迟导入，见 _load_alipay_provider()
}


def _load_alipay_provider() -> type:
    """延迟导入支付宝适配器（T2 文件：payments/alipay.py）。"""
    try:
        from .alipay import AlipayProvider  # type: ignore[import-not-found]
    except ImportError as exc:  # T2 尚未交付 / 部署时漏带文件
        raise PaymentError(
            "channel_unavailable",
            "支付宝通道暂不可用（服务端缺少 payments/alipay.py 适配器）",
            503,
        ) from exc
    return AlipayProvider


def get_provider(name: str, config: Any = None):
    """按名称返回 Provider 实例；未知通道抛 :class:`PaymentError`。"""
    key = str(name or "").strip().lower()
    if key == "alipay":
        provider_cls = _load_alipay_provider()
        try:
            return provider_cls()  # 契约：无参构造
        except TypeError as exc:
            raise PaymentError(
                "channel_unavailable", "支付宝通道初始化失败（适配器接口不匹配）", 503) from exc
    provider_cls = _PROVIDERS.get(key)
    if provider_cls is None:
        raise PaymentError("channel_unavailable", f"未知支付通道：{name!r}", 400)
    return provider_cls()


def provider_label(name: str) -> str:
    key = str(name or "").strip().lower()
    if key in CHANNEL_LABELS:
        return CHANNEL_LABELS[key]
    return key


def available_channels(config: Any) -> list[dict]:
    """GET /api/catalog 的 channels 列表。

    - ``alipay``：真实支付宝通道存在时可用；``PAYMENT_PROVIDER=mock`` 的演示模式下
      也允许选「支付宝扫码」——此时实际由 mock provider 出演示二维码，方便本地演示
      与真实部署共用同一套前端。
    - ``mock``：仅 ``PAYMENT_MOCK_ENABLED``（provider=mock 时自动 true）时可用。
    """
    provider = str(getattr(config, "PAYMENT_PROVIDER", "mock") or "mock").lower()
    mock_enabled = bool(getattr(config, "PAYMENT_MOCK_ENABLED", provider == "mock"))
    alipay_enabled = provider in ("alipay", "mock")
    return [
        {"id": "alipay", "label": CHANNEL_LABELS["alipay"], "enabled": alipay_enabled},
        {"id": "mock", "label": CHANNEL_LABELS["mock"], "enabled": mock_enabled},
    ]


def payment_error_details(exc: BaseException) -> tuple[str, str]:
    """从任意支付异常里提取 (code, message)，兼容 T2 自带的 PaymentError。"""
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if not isinstance(code, str) or not code:
        code = "payment_create_failed"
    if not isinstance(message, str) or not message:
        message = "支付下单失败，请稍后重试或更换支付方式"
    return code, message


__all__ = [
    "PaymentError", "MockProvider", "get_provider", "available_channels",
    "provider_label", "payment_error_details", "CHANNEL_LABELS", "CHANNEL_ORDER",
]
