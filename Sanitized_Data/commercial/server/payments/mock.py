# -*- coding: utf-8 -*-
"""Mock 支付提供者（本地演示 / 单元测试用；绝不联网、不产生真实扣款）。

契约 §4：``create(order, config)`` 返回 ``qr_content = f"{base}/mock/pay?order_no=..&token=.."``；
``verify_notify`` 不使用（mock 没有异步通知）。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from . import PaymentError

__all__ = ["MockProvider"]


class MockProvider:
    """演示支付：二维码指向本服务的 ``/mock/pay`` 页面，由用户点「模拟支付成功」。"""

    name = "mock"
    label = "模拟支付（演示）"

    def create(self, order: dict, config: Any = None) -> dict:
        base = str(getattr(config, "COMMERCIAL_BASE_URL", "") or "").rstrip("/")
        if not base:
            raise PaymentError("payment_create_failed", "COMMERCIAL_BASE_URL 未配置，无法生成演示支付链接", 503)
        order_no = str(order.get("order_no") or "")
        token = str(order.get("token") or "")
        if not order_no or not token:
            raise PaymentError("payment_create_failed", "订单信息不完整，无法生成演示支付链接", 500)
        pay_url = (f"{base}/mock/pay?order_no={quote(order_no, safe='')}"
                   f"&token={quote(token, safe='')}")
        return {"qr_content": pay_url, "pay_url": pay_url,
                "provider_ref": f"mock_{order_no}"}

    def verify_notify(self, form: dict, config: Any = None) -> dict | None:
        raise PaymentError("not_supported", "mock 支付通道不支持异步通知", 400)
