# -*- coding: utf-8 -*-
"""SKU 目录/价格（服务端唯一事实来源；金额只从这里取，绝不信任 HTTP 入参）。

契约 §3：
- 只卖 ``personal`` / ``pro`` 自助单；``team`` / ``org`` 返回 ``contact_sales``。
- 价格单位：分（int）。
- 功能位与产品 ``yinzhan.licensing.tiers`` 保持一致。
"""
from __future__ import annotations

from dataclasses import dataclass

from .signer import ensure_app_server_path

ensure_app_server_path()

from yinzhan.licensing import tiers  # noqa: E402

CURRENCY = "CNY"
TERM = "annual"
TERM_LABEL = "1年"

#: 商品名（订单 subject 前缀）
PRODUCT_NAME = "数据隐站"


@dataclass(frozen=True)
class Sku:
    """自助售卖 SKU。"""

    id: str
    name: str
    edition: str
    devices: int
    features: tuple[str, ...]
    #: Config 上对应的价格属性名
    price_attr: str
    #: annual / perpetual
    term: str = TERM
    term_label: str = TERM_LABEL
    #: 年费续费价（仅首年优惠的 SKU）
    renewal_price_attr: str | None = None


@dataclass(frozen=True)
class ContactSku:
    """非自助 SKU（联系商务，不建线上单）。"""

    id: str
    name: str
    edition: str
    devices: int
    features: tuple[str, ...]
    price_attr: str | None


SELF_SERVE_SKUS: tuple[Sku, ...] = (
    Sku("personal", "个人版·年付", "个人版", 1,
        tuple(tiers.PERSONAL_FEATURES), "COMMERCIAL_PRICE_PERSONAL_CENTS",
        term="annual", term_label="首年", renewal_price_attr="COMMERCIAL_PRICE_PERSONAL_RENEWAL_CENTS"),
    Sku("personal_perpetual", "个人版·买断", "个人版", 1,
        tuple(tiers.PERSONAL_FEATURES), "COMMERCIAL_PRICE_PERSONAL_PERPETUAL_CENTS",
        term="perpetual", term_label="买断 · 含 1 年更新"),
    Sku("pro", "专业版", "专业版", 2,
        tuple(tiers.PRO_FEATURES), "COMMERCIAL_PRICE_PRO_CENTS"),
)

CONTACT_SKUS: tuple[ContactSku, ...] = (
    ContactSku("team", "团队版", "团队版", 5,
               tuple(tiers.TEAM_FEATURES), "COMMERCIAL_PRICE_TEAM_CENTS"),
    ContactSku("org", "机构版", "机构版", 50,
               tuple(tiers.ORG_FEATURES), None),
)

_SKU_BY_ID: dict[str, Sku] = {sku.id: sku for sku in SELF_SERVE_SKUS}
_CONTACT_BY_ID: dict[str, ContactSku] = {sku.id: sku for sku in CONTACT_SKUS}

#: 团队/机构版联系商务提示（前端给联系方式，不建线上单）
CONTACT_SALES_MESSAGE = "团队版与机构版按团队规模报价，请联系商务开通（不提供线上自助下单）。"


def get_sku(sku_id: str | None) -> Sku | None:
    return _SKU_BY_ID.get(str(sku_id or "").strip().lower())


def get_contact_sku(sku_id: str | None) -> ContactSku | None:
    return _CONTACT_BY_ID.get(str(sku_id or "").strip().lower())


def is_contact_sales_sku(sku_id: str | None) -> bool:
    return get_contact_sku(sku_id) is not None


def price_cents(sku: Sku, config, *, renewal: bool = False) -> int:
    """按服务端 SKU 表 + 配置取价（int 分）。"""
    attr = sku.price_attr
    if renewal and sku.renewal_price_attr:
        attr = sku.renewal_price_attr
    return int(getattr(config, attr))


def sku_payload(sku: Sku, config) -> dict:
    payload = {
        "id": sku.id,
        "name": sku.name,
        "edition": sku.edition,
        "price_cents": price_cents(sku, config),
        "currency": CURRENCY,
        "term": sku.term,
        "term_label": sku.term_label,
        "devices": sku.devices,
        "features": list(sku.features),
    }
    if sku.renewal_price_attr:
        payload["renewal_price_cents"] = price_cents(sku, config, renewal=True)
    return payload


def subject_for(sku: Sku) -> str:
    """订单标题，例：``数据隐站 个人版 · 首年`` / ``数据隐站 个人版 · 买断 · 含 1 年更新``。"""
    return f"{PRODUCT_NAME} {sku.edition} · {sku.term_label}"


def contact_sales_payload(config) -> dict:
    reference: dict[str, int | None] = {}
    for sku in CONTACT_SKUS:
        reference[sku.id] = (int(getattr(config, sku.price_attr))
                             if sku.price_attr else None)
    if not getattr(config, "COMMERCIAL_SELF_SERVE_PRO_ENABLED", True):
        # 价格页/授权页已只展示 199；团队/机构保留联系入口，但不再露出参考价。
        reference = {}
    return {
        "skus": [sku.id for sku in CONTACT_SKUS],
        "label": "团队版 / 机构版",
        "message": CONTACT_SALES_MESSAGE,
        "reference_prices_cents": reference,
        "url": None,
    }


def catalog_payload(config) -> dict:
    """GET /api/catalog 的完整响应体（契约 §3）。"""
    from .payments import available_channels  # 延迟导入，避免循环依赖

    skus = [sku for sku in SELF_SERVE_SKUS
            if getattr(config, "COMMERCIAL_SELF_SERVE_PRO_ENABLED", True)
            or sku.id != "pro"]
    return {
        "ok": True,
        "provider": config.PAYMENT_PROVIDER,
        "channels": available_channels(config),
        "skus": [sku_payload(sku, config) for sku in skus],
        "contact_sales": contact_sales_payload(config),
        "currency": CURRENCY,
        "mock_available": bool(config.PAYMENT_MOCK_ENABLED),
        "version": config.VERSION,
    }


__all__ = [
    "Sku", "ContactSku", "SELF_SERVE_SKUS", "CONTACT_SKUS",
    "CURRENCY", "TERM", "TERM_LABEL", "PRODUCT_NAME", "CONTACT_SALES_MESSAGE",
    "get_sku", "get_contact_sku", "is_contact_sales_sku", "price_cents",
    "sku_payload", "subject_for", "contact_sales_payload", "catalog_payload",
]
