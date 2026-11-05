# -*- coding: utf-8 -*-
"""数据隐站 L3 商业服务 HTTP 入口（FastAPI）。

路由（契约 §3）：
- ``GET  /api/catalog``                    目录/价格/通道
- ``POST /api/orders``                     下单（CORS，服务端计价，限流）
- ``GET  /api/orders/{order_no}?token=``   轮询订单/取授权（token 错误统一 404；
  开启 ``ALIPAY_QUERY_FALLBACK`` 时对 pending 的支付宝订单按节流主动查单兜底）
- ``POST /api/mock/pay``                   模拟支付（仅 mock 启用时）
- ``POST /notify/alipay``                  支付宝异步通知（T2 适配器延迟导入）
- ``GET  /healthz``                        健康检查
- ``GET  /buy.html`` ``/order.html`` ``/mock/pay`` ``/static/**``  纯本地静态页

安全约定：
- 订单 token 只存 sha256；错误/不存在统一 404，防枚举；
- 金额、SKU、有效期全部服务端计算，忽略客户端金额字段；
- 回调幂等（payments.transaction_id UNIQUE / licenses.order_no UNIQUE）；
- 主动查单兜底只在 GET 订单轮询里触发（默认关闭），查询异常/金额不符保持 pending 不 500；
- 日志不打印 token、私钥、口令；不记录查询串。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from . import catalog
from . import db as db_module
from . import qr, signer
from .config import STATIC_DIR, Config
from .payments import PaymentError, get_provider

logger = logging.getLogger("commercial.app")

_ALLOWED_ORDER_CHANNELS = ("alipay", "mock")
_TOKEN_MAX_LEN = 256
_EMAIL_MAX_LEN = 200
_LOCAL_ORIGIN_RE = re.compile(r"^http://(?:127\.0\.0\.1|localhost)(?::\d{1,5})?$")
_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
        "object-src 'none'")
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


# ── 请求体模型 ──
class OrderCreateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sku: str = ""
    machine_code: str = ""
    channel: str = "alipay"
    email: str | None = None


class MockPayBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    order_no: str = ""
    token: str = ""


# ── 响应工具 ──
def _error(status: int, code: str, message: str,
           headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message}},
        status_code=int(status),
        headers=headers,
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _token_matches(order: dict[str, Any], token: str | None) -> bool:
    """常量时间比较订单 token（库内只有 sha256）。"""
    if not token or len(str(token)) > _TOKEN_MAX_LEN:
        return False
    expected = str(order.get("order_token_hash") or "")
    if not expected:
        return False
    return hmac.compare_digest(_token_hash(str(token)), expected)


def _license_public(license_row: dict[str, Any] | None) -> dict[str, Any] | None:
    """GET 订单时返回的 license 对象（契约 §3）。"""
    if not license_row:
        return None
    payload: dict[str, Any] = {}
    try:
        container = json.loads(license_row.get("text") or "{}")
        payload = container.get("payload") or {}
    except Exception:
        payload = {}
    return {
        "license_id": license_row.get("license_id"),
        "text": license_row.get("text"),
        "sku": license_row.get("sku"),
        "machine_code": license_row.get("machine_code"),
        "expires_at": license_row.get("expires_at") or payload.get("expires_at"),
        "updates_until": payload.get("updates_until"),
        "term": payload.get("term"),
        "edition": payload.get("edition") or "",
    }


def _issue_for_order(license_signer, order: dict[str, Any]):
    """按订单 SKU 签发：personal_perpetual → personal / perpetual term。"""
    order_sku = str(order.get("sku") or "")
    perpetual = order_sku.endswith("_perpetual")
    license_sku = "personal" if order_sku == "personal_perpetual" else order_sku
    return license_signer.issue(
        sku=license_sku,
        machine_code=str(order.get("machine_code") or ""),
        order_no=str(order.get("order_no") or ""),
        term="perpetual" if perpetual else "annual",
        updates_days=365 if perpetual else None,
    )


def _order_view(order: dict[str, Any], license_row: dict[str, Any] | None,
                config: Config) -> dict[str, Any]:
    """订单查询/模拟支付共用响应体（契约 §3）。"""
    paid = order.get("status") == db_module.STATUS_PAID
    return {
        "ok": True,
        "order_no": order.get("order_no"),
        "status": order.get("status"),
        "sku": order.get("sku"),
        "amount_cents": int(order.get("amount_cents") or 0),
        "paid_at": order.get("paid_at"),
        "license": _license_public(license_row) if paid else None,
        "mock_available": bool(config.PAYMENT_MOCK_ENABLED),
        # 扩展字段（前端倒计时/展示用；不影响契约必选字段）
        "expires_at": order.get("expires_at"),
    }


def _client_ip(request: Request, config: Config) -> str:
    """限流 key。默认用直连 IP；仅显式信任反代时才看 X-Forwarded-For。"""
    if config.COMMERCIAL_TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for") or ""
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = request.client
    return str(client.host) if client and client.host else "unknown"


# ── 限流 ──
class RateLimiter:
    """固定窗口（滑动）计数：默认 10 次/分钟/IP（契约 §8）。"""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = max(1, int(limit))
        self.window_seconds = float(window_seconds)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = deque()
                self._hits[key] = bucket
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            if len(self._hits) > 2048:  # 机会式清理，防内存膨胀
                for name in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                    self._hits.pop(name, None)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# ── 中间件 ──
class CorsMiddleware(BaseHTTPMiddleware):
    """只放行本机回环来源（任意端口）+ 配置的额外来源；不带 credentials。"""

    def __init__(self, app, extra_origins: tuple[str, ...] = ()):
        super().__init__(app)
        self.extra_origins = tuple(extra_origins or ())
        self.allow_methods = "GET, POST, OPTIONS"
        self.allow_headers = "Content-Type"
        self.max_age = "600"

    def _origin_allowed(self, origin: str | None) -> bool:
        if not origin:
            return False
        value = str(origin).strip().rstrip("/")
        if _LOCAL_ORIGIN_RE.match(value):
            return True
        return value in self.extra_origins

    def _apply(self, response: Response, origin: str) -> None:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = self.allow_methods
        response.headers["Access-Control-Allow-Headers"] = self.allow_headers
        response.headers["Access-Control-Max-Age"] = self.max_age

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = self._origin_allowed(origin)
        if request.method == "OPTIONS" and allowed and request.headers.get(
                "access-control-request-method"):
            response = Response(status_code=204)
            self._apply(response, str(origin))
            return response
        response = await call_next(request)
        if allowed:
            self._apply(response, str(origin))
        elif origin:
            existing = response.headers.get("Vary")
            response.headers["Vary"] = "Origin" if not existing else f"{existing}, Origin"
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, csp: str = _CSP):
        super().__init__(app)
        self.csp = csp

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", self.csp)
            response.headers.setdefault("Cache-Control", "no-store")
        return response


# ── 应用工厂 ──
def create_app(config: Config | None = None) -> FastAPI:
    """构造 FastAPI 应用（测试可注入临时数据目录/测试密钥的 Config）。"""
    cfg = config or Config.load()
    _configure_logging(cfg)

    cfg.ensure_dirs()
    static_dir = STATIC_DIR
    static_dir.mkdir(parents=True, exist_ok=True)

    database = db_module.Database(cfg.db_path)
    database.init_schema()
    license_signer = signer.LicenseSigner(
        cfg.LICENSE_PRIVATE_KEY_PATH,
        key_id=cfg.LICENSE_KEY_ID,
        password_env=cfg.LICENSE_KEY_PASSWORD_ENV,
    )
    limiter = RateLimiter(cfg.COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE,
                          window_seconds=60.0)

    # 支付宝主动查单兜底：进程内按 order_no 节流（monotonic；仅单进程有效，多 worker 各自独立）
    alipay_query_lock = threading.Lock()
    alipay_query_last: dict[str, float] = {}

    def _sync_alipay_order(
        order: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        """notify 不可达/延迟时的主动查单兜底；返回 ``(order, license)`` 或 ``None``。

        仅在 ``GET /api/orders/{no}`` 中、订单 ``pending`` + ``provider=alipay`` +
        ``ALIPAY_QUERY_FALLBACK`` 开启时调用：

        - 进程内按 order_no 节流（间隔 ≥ ``ALIPAY_QUERY_MIN_INTERVAL_SECONDS``）；
        - 调 ``alipay.trade.query``（RSA2 签名 + 支付宝公钥验响应签名）；
        - ``TRADE_SUCCESS`` / ``TRADE_FINISHED`` 且金额与 DB 一致 → ``finalize_payment``
          （与 notify 共用幂等入账/签发逻辑），随后重新加载 order + license；
        - 金额不符/未支付/查询异常/超时都只记日志（金额不符额外记事件）并保持 pending，
          绝不把异常抛给轮询接口（前端每 2s 轮询，必须稳定 200）。
        """
        order_no = str(order.get("order_no") or "")
        if not order_no:
            return None

        interval = float(cfg.ALIPAY_QUERY_MIN_INTERVAL_SECONDS)
        now = time.monotonic()
        with alipay_query_lock:
            last = alipay_query_last.get(order_no)
            if last is not None and (now - last) < interval:
                return None  # 节流窗口内：不查单，直接返回当前 pending 状态
            alipay_query_last[order_no] = now
            if len(alipay_query_last) > 4096:  # 机会式清理，防内存膨胀
                cutoff = now - 3600.0
                for key in [k for k, value in alipay_query_last.items() if value <= cutoff]:
                    alipay_query_last.pop(key, None)

        try:
            provider = get_provider("alipay", cfg)
            result = provider.query_trade(order_no, cfg)
        except Exception as exc:  # 含 PaymentError/网络超时/响应验签失败
            code = getattr(exc, "code", type(exc).__name__)
            logger.warning("alipay query fallback failed: order=%s code=%s", order_no, code)
            return None

        result = result or {}
        trade_status = str(result.get("trade_status") or "").upper()
        if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            logger.info("alipay query fallback: order=%s not paid (status=%s)",
                        order_no, trade_status or "unknown")
            return None

        try:
            amount_cents = int(result.get("total_amount_cents"))
        except (TypeError, ValueError):
            amount_cents = -1
        expected_cents = int(order.get("amount_cents") or 0)
        if amount_cents != expected_cents:
            logger.warning("alipay query fallback amount mismatch: order=%s expected=%s got=%s",
                           order_no, expected_cents, amount_cents)
            try:
                database.log_event(order_no, "query_amount_mismatch", {
                    "expected_cents": expected_cents,
                    "received_cents": amount_cents,
                    "trade_status": trade_status,
                })
            except Exception:
                logger.exception("log query_amount_mismatch failed: order=%s", order_no)
            return None

        transaction_id = str(result.get("trade_no") or "").strip()
        if not transaction_id:
            logger.warning("alipay query fallback missing trade_no: order=%s", order_no)
            return None

        try:
            license_data = _issue_for_order(license_signer, order)
        except signer.SignerError as exc:
            logger.error("license issue failed (query): order=%s code=%s", order_no, exc.code)
            try:
                database.log_event(order_no, "license_issue_failed", {"code": exc.code})
            except Exception:
                logger.exception("log license_issue_failed failed: order=%s", order_no)
            return None

        raw = result.get("raw")
        raw_text = (json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
                    if isinstance(raw, dict) else str(raw or ""))
        try:
            finalized = database.finalize_payment(
                order_no=order_no,
                provider="alipay",
                transaction_id=transaction_id,
                amount_cents=amount_cents,
                raw=raw_text,
                license=license_data,
                trade_status=trade_status,
            )
        except db_module.OrderError as exc:
            logger.warning("alipay query finalize rejected: order=%s code=%s", order_no, exc.code)
            try:
                database.log_event(order_no, "query_finalize_rejected", {"code": exc.code})
            except Exception:
                logger.exception("log query_finalize_rejected failed: order=%s", order_no)
            return None
        except Exception:
            logger.exception("alipay query finalize failed: order=%s", order_no)
            return None

        try:
            database.log_event(order_no, "query_paid", {
                "trade_status": trade_status,
                "transaction_id": transaction_id,
                "license_id": (finalized.get("license") or {}).get("license_id"),
                "paid_now": bool(finalized.get("paid_now")),
            })
        except Exception:
            logger.exception("log query_paid failed: order=%s", order_no)

        fresh_order = database.get_order(order_no) or finalized["order"]
        license_row = database.get_license(order_no) or finalized["license"]
        logger.info("alipay query fallback: order=%s paid (trade_status=%s)", order_no, trade_status)
        return fresh_order, license_row

    app = FastAPI(
        title="数据隐站商业服务",
        version=cfg.VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = cfg
    app.state.db = database
    app.state.signer = license_signer
    app.state.rate_limiter = limiter

    # 中间件（后加的更靠外层）
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CorsMiddleware, extra_origins=tuple(cfg.cors_extra_origins))

    _register_exception_handlers(app)

    # ── 健康检查 ──
    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "provider": cfg.PAYMENT_PROVIDER,
            "version": cfg.VERSION,
        }

    # ── 目录 ──
    @app.get("/api/catalog")
    def api_catalog() -> dict[str, Any]:
        return catalog.catalog_payload(cfg)

    # ── 下单 ──
    @app.post("/api/orders")
    def api_create_order(body: OrderCreateBody, request: Request):
        ip = _client_ip(request, cfg)
        if not limiter.allow(ip):
            return _error(429, "rate_limited",
                          "下单过于频繁，请稍后再试（每分钟最多 10 单）",
                          headers={"Retry-After": "60"})

        sku_id = (body.sku or "").strip().lower()
        sku = catalog.get_sku(sku_id)
        if sku is None:
            if catalog.is_contact_sales_sku(sku_id):
                return _error(400, "sku_invalid", catalog.CONTACT_SALES_MESSAGE)
            return _error(400, "sku_invalid", "套餐不存在，请刷新购买页后重试")
        if (sku.id == "pro"
                and not cfg.COMMERCIAL_SELF_SERVE_PRO_ENABLED):
            return _error(400, "sku_invalid",
                          "专业版暂未开放在线自助购买，请联系交付顾问开通")

        try:
            machine_code = signer.normalize_machine_code(body.machine_code)
        except signer.SignerError as exc:
            return _error(exc.status, exc.code, exc.message)

        email: str | None = None
        raw_email = (body.email or "").strip()
        if raw_email:
            if (len(raw_email) > _EMAIL_MAX_LEN or "@" not in raw_email
                    or raw_email.startswith("@") or raw_email.endswith("@")
                    or " " in raw_email):
                return _error(400, "invalid_request", "邮箱格式无效")
            email = raw_email

        channel = (body.channel or "").strip().lower()
        if channel not in _ALLOWED_ORDER_CHANNELS:
            return _error(400, "channel_unavailable", "不支持的支付通道")
        if channel == "mock" and not cfg.PAYMENT_MOCK_ENABLED:
            return _error(403, "channel_unavailable", "模拟支付未启用")

        # 金额/SKU/有效期全部服务端决定（忽略客户端传入的金额）
        # 个人版年付：首次购买 99，已有历史已支付订单则按续费 129。
        renewal = (sku.id == "personal"
                   and database.has_paid_order_for_machine(machine_code, ("personal",)))
        amount_cents = catalog.price_cents(sku, cfg, renewal=renewal)
        subject = catalog.subject_for(sku)
        if renewal:
            subject = f"{catalog.PRODUCT_NAME} {sku.edition} · 续费"
        token = secrets.token_urlsafe(32)
        expires_at = db_module.iso(
            db_module.utcnow() + timedelta(seconds=int(cfg.COMMERCIAL_ORDER_TTL_SECONDS)))

        order_row: dict[str, Any] | None = None
        order_no = db_module.make_order_no()
        for _attempt in range(3):
            candidate = {
                "order_no": order_no,
                "sku": sku.id,
                "amount_cents": amount_cents,
                "machine_code": machine_code,
                "email": email,
                "channel": channel,
                "provider": "mock" if channel == "mock" else cfg.PAYMENT_PROVIDER,
                "order_token_hash": _token_hash(token),
                "created_at": db_module.iso(),
                "expires_at": expires_at,
            }
            try:
                order_row = database.create_order(candidate)
                break
            except sqlite3.IntegrityError:
                order_no = db_module.make_order_no()
        if order_row is None:
            return _error(503, "order_create_failed", "创建订单失败，请稍后重试")

        provider_name = "mock" if channel == "mock" else cfg.PAYMENT_PROVIDER
        try:
            provider = get_provider(provider_name, cfg)
        except PaymentError:
            logger.warning("provider unavailable: channel=%s provider=%s order=%s",
                           channel, provider_name, order_no)
            database.close_order(order_no, "provider_unavailable")
            return _error(503, "payment_create_failed", "支付通道暂不可用，请稍后重试")

        provider_order = {
            "order_no": order_no,
            "sku": sku.id,
            "amount_cents": amount_cents,
            "subject": subject,
            "machine_code": machine_code,
            "token": token,
            "return_url": (f"{cfg.COMMERCIAL_BASE_URL}/order.html"
                           f"?order_no={order_no}&token={token}"),
        }
        try:
            payment = provider.create(provider_order, cfg)
        except Exception as exc:  # 含 T2 的 PaymentError / 网络错误
            code = getattr(exc, "code", "payment_create_failed")
            logger.warning("payment create failed: provider=%s order=%s code=%s",
                           provider_name, order_no, code)
            database.close_order(order_no, f"payment_create_failed:{code}")
            return _error(503, "payment_create_failed",
                          "支付下单失败，请稍后重试或更换支付方式")

        qr_content = str((payment or {}).get("qr_content")
                         or (payment or {}).get("pay_url") or "").strip()
        if not qr_content:
            logger.warning("payment create returned empty qr/pay url: order=%s", order_no)
            database.close_order(order_no, "payment_create_empty")
            return _error(503, "payment_create_failed", "支付下单失败，请稍后重试")
        try:
            qr_svg = qr.make_svg(qr_content, title=f"{subject} 扫码支付")
        except ValueError:
            logger.warning("qr encode failed: order=%s", order_no)
            database.close_order(order_no, "payment_qr_failed")
            return _error(503, "payment_create_failed", "支付二维码生成失败，请稍后重试")

        database.set_order_provider_ref(order_no, (payment or {}).get("provider_ref"))
        database.log_event(order_no, "payment_created", {
            "provider": provider_name, "channel": channel, "pay_url": bool((payment or {}).get("pay_url")),
        })
        return {
            "ok": True,
            "order_no": order_no,
            "sku": sku.id,
            "amount_cents": amount_cents,
            "subject": subject,
            "channel": channel,
            "provider": provider_name,
            "qr_svg": qr_svg,
            "qr_content": qr_content,
            "pay_url": (payment or {}).get("pay_url"),
            "qr_pay_mode": (payment or {}).get("qr_pay_mode") or "",
            "order_token": token,
            "expires_at": expires_at,
            "mock_available": bool(cfg.PAYMENT_MOCK_ENABLED),
        }

    # ── 订单查询/轮询 ──
    @app.get("/api/orders/{order_no}")
    def api_get_order(order_no: str, token: str | None = None):
        order = database.load_order(order_no)
        if order is None or not _token_matches(order, token):
            # token 错误与不存在统一 404，防订单枚举
            return _error(404, "order_not_found", "订单不存在或查询令牌无效")

        license_row: dict[str, Any] | None = None
        if order.get("status") == db_module.STATUS_PAID:
            license_row = database.get_license(order_no)
        elif (order.get("status") == db_module.STATUS_PENDING
              and str(order.get("provider") or "") == "alipay"
              and bool(cfg.ALIPAY_QUERY_FALLBACK)):
            # 主动查单兜底：notify 不可达（本地联调/内网/回调延迟）时也能自动发码解锁。
            # 只影响 GET 轮询；notify 仍保留且幂等，谁先确认支付谁签发。
            try:
                synced = _sync_alipay_order(order)
            except Exception:
                # 兜底链路任何异常都不能让轮询接口 500（前端每 2s 轮询，必须稳定）
                logger.exception("alipay query fallback unexpected error: order=%s", order_no)
                synced = None
            if synced is not None:
                order, license_row = synced
        return _order_view(order, license_row, cfg)

    # ── 模拟支付（本地演示） ──
    @app.post("/api/mock/pay")
    def api_mock_pay(body: MockPayBody):
        if not cfg.PAYMENT_MOCK_ENABLED:
            return _error(403, "mock_disabled", "模拟支付未启用（仅本地演示可用）")
        order_no = (body.order_no or "").strip()
        order = database.load_order(order_no) if order_no else None
        if order is None or not _token_matches(order, body.token):
            return _error(404, "order_not_found", "订单不存在或查询令牌无效")
        if order.get("status") == db_module.STATUS_PAID:
            # 幂等：重复点「模拟支付」直接返回既有授权，不重复入账/签发
            return _order_view(order, database.get_license(order_no), cfg)
        if order.get("status") == db_module.STATUS_EXPIRED:
            return _error(400, "order_expired", "订单已过期，请重新下单")
        if order.get("status") != db_module.STATUS_PENDING:
            return _error(400, "order_not_payable", "订单当前状态不可支付")

        try:
            license_data = _issue_for_order(license_signer, order)
        except signer.SignerError as exc:
            logger.error("license issue failed (mock): order=%s code=%s", order_no, exc.code)
            database.log_event(order_no, "license_issue_failed", {"code": exc.code})
            return _error(503, exc.code, "授权签发失败，请稍后重试或联系客服")

        try:
            result = database.finalize_payment(
                order_no=order_no,
                provider="mock",
                transaction_id=f"mock_{order_no}",
                amount_cents=int(order["amount_cents"]),
                raw={"kind": "mock", "order_no": order_no},
                license=license_data,
            )
        except db_module.OrderError as exc:
            logger.warning("mock pay rejected: order=%s code=%s", order_no, exc.code)
            database.log_event(order_no, "mock_pay_rejected", {"code": exc.code})
            return _error(exc.status, exc.code, exc.message)
        except Exception:
            logger.exception("mock pay failed: order=%s", order_no)
            return _error(500, "internal_error", "模拟支付处理失败，请稍后重试")
        return _order_view(result["order"], result["license"], cfg)

    # ── 支付宝异步通知（T2 适配器；缺失时 503 failure 让支付宝重试） ──
    @app.post("/notify/alipay")
    async def alipay_notify(request: Request):
        try:
            from .payments.alipay import AlipayProvider  # T2 并行实现
        except ImportError:
            # 适配器未部署：返回 503 + failure（支付宝会稍后重试），不静默当成功
            logger.warning("alipay adapter missing: /notify/alipay unavailable")
            return PlainTextResponse("failure", status_code=503)

        try:
            raw_body = await request.body()
            form: dict[str, str] = {
                key: (values[0] if values else "")
                for key, values in parse_qs(
                    raw_body.decode("utf-8", "replace"), keep_blank_values=True).items()
            }
        except Exception:
            logger.warning("alipay notify bad form body")
            return PlainTextResponse("failure", status_code=200)

        try:
            provider = AlipayProvider()
        except Exception:
            logger.error("alipay provider init failed")
            return PlainTextResponse("failure", status_code=503)

        try:
            payload = provider.verify_notify(form, cfg)
        except PaymentError as exc:
            if getattr(exc, "code", "") == "trade_status_not_paid":
                # 非支付成功状态（WAIT_BUYER_PAY / TRADE_CLOSED 等，T2 适配器会抛此 code）：
                # 验签已通过、只是没有付款，确认收到即可；返回 success 避免支付宝对无意义
                # 状态反复重试。最终 TRADE_SUCCESS 通知会正常签发。
                logger.info("alipay notify non-paid status acked")
                return PlainTextResponse("success", status_code=200)
            logger.warning("alipay notify verify failed: code=%s", exc.code)
            return PlainTextResponse("failure", status_code=200)
        except Exception:
            logger.warning("alipay notify verify error")
            return PlainTextResponse("failure", status_code=200)
        if not payload:
            return PlainTextResponse("failure", status_code=200)

        order_no = str(payload.get("order_no") or "").strip()
        transaction_id = str(payload.get("transaction_id") or "").strip()
        trade_status = str(payload.get("trade_status") or "")
        try:
            amount_cents = int(payload.get("amount_cents"))
        except (TypeError, ValueError):
            amount_cents = -1
        order = database.load_order(order_no) if order_no else None
        if order is None:
            database.log_event(order_no or None, "notify_unknown_order",
                               {"transaction_id": transaction_id})
            return PlainTextResponse("failure", status_code=200)

        if amount_cents != int(order["amount_cents"]):
            logger.warning("alipay notify amount mismatch: order=%s", order_no)
            database.log_event(order_no, "notify_amount_mismatch", {
                "expected_cents": int(order["amount_cents"]),
                "received_cents": amount_cents,
                "transaction_id": transaction_id,
            })
            return PlainTextResponse("failure", status_code=200)

        if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            # 非成功状态（如等待付款）确认收到但不签发
            database.log_event(order_no, "notify_ignored_status",
                               {"trade_status": trade_status,
                                "transaction_id": transaction_id})
            return PlainTextResponse("success", status_code=200)

        if not transaction_id:
            database.log_event(order_no, "notify_missing_transaction")
            return PlainTextResponse("failure", status_code=200)

        existing = database.get_license(order_no)
        if existing is not None:
            # 重复通知：已签发，直接 ack，不重复入账
            database.log_event(order_no, "notify_duplicate", {
                "transaction_id": transaction_id,
                "trade_status": trade_status,
            })
            return PlainTextResponse("success", status_code=200)

        try:
            license_data = _issue_for_order(license_signer, order)
        except signer.SignerError as exc:
            logger.error("license issue failed (notify): order=%s code=%s", order_no, exc.code)
            database.log_event(order_no, "license_issue_failed", {"code": exc.code})
            return PlainTextResponse("failure", status_code=200)

        try:
            database.finalize_payment(
                order_no=order_no,
                provider="alipay",
                transaction_id=transaction_id,
                amount_cents=amount_cents,
                raw=payload.get("raw") or form,
                license=license_data,
                trade_status=trade_status,
                allow_expired=True,  # 钱已到账：过期订单也补签发（避免客户付了钱拿不到码）
            )
        except db_module.OrderError as exc:
            logger.warning("alipay notify finalize rejected: order=%s code=%s",
                           order_no, exc.code)
            database.log_event(order_no, "notify_finalize_rejected", {"code": exc.code})
            return PlainTextResponse("failure", status_code=200)
        except Exception:
            logger.exception("alipay notify finalize error: order=%s", order_no)
            return PlainTextResponse("failure", status_code=200)
        return PlainTextResponse("success", status_code=200)

    # ── 静态页面（纯本地资源，无外链） ──
    def _page(name: str) -> Response:
        path = static_dir / name
        if not path.is_file():
            return PlainTextResponse("页面不存在", status_code=404)
        return FileResponse(str(path), media_type="text/html; charset=utf-8")

    @app.get("/")
    def index_page() -> Response:
        return _page("buy.html")

    @app.get("/buy.html")
    def buy_page() -> Response:
        return _page("buy.html")

    @app.get("/order.html")
    def order_page() -> Response:
        return _page("order.html")

    @app.get("/mock/pay")
    def mock_pay_page() -> Response:
        # 生产环境不暴露任何 mock 演示页（mock API 已由 PAYMENT_MOCK_ENABLED 关闭）。
        if not cfg.PAYMENT_MOCK_ENABLED:
            return _error(404, "not_found", "页面不存在")
        return _page("mock_pay.html")

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    logger.info("commercial app ready: provider=%s mock_enabled=%s base_url=%s",
                cfg.PAYMENT_PROVIDER, cfg.PAYMENT_MOCK_ENABLED, cfg.COMMERCIAL_BASE_URL)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """统一错误体形态（API 路径始终 ``{ok:false, error:{code,message}}``）。"""

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return _error(400, "invalid_request", "请求参数格式无效")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            code = "order_not_found" if exc.status_code == 404 else "http_error"
            detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
            return _error(exc.status_code, code, detail)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # 只记录方法+路径（不带查询串，避免 token 进日志）
        logger.exception("unhandled error: %s %s", request.method, request.url.path)
        if request.url.path.startswith("/api/"):
            return _error(500, "internal_error", "服务内部错误，请稍后重试")
        return PlainTextResponse("internal error", status_code=500)


def _configure_logging(config: Config) -> None:
    level = getattr(logging, str(config.COMMERCIAL_LOG_LEVEL or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    else:
        root.setLevel(level)
    logging.getLogger("commercial").setLevel(level)


__all__ = [
    "create_app", "RateLimiter", "CorsMiddleware", "SecurityHeadersMiddleware",
    "OrderCreateBody", "MockPayBody",
]
