# -*- coding: utf-8 -*-
"""SQLite 访问层（WAL；每次操作独立连接，事务用 BEGIN IMMEDIATE）。

表结构严格按 ``commercial/CONTRACT.md`` §5：
orders / payments / licenses / events。

关键不变量（幂等/防重复签发）：
- ``orders.status`` 只前进：pending -> paid / expired / closed；
- ``payments.transaction_id`` UNIQUE（同一支付流水只入账一次）；
- ``licenses.order_no`` UNIQUE（同一订单只签发一次）。
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# ── 状态 ──
STATUS_PENDING = "pending"
STATUS_PAID = "paid"
STATUS_EXPIRED = "expired"
STATUS_CLOSED = "closed"
ORDER_STATUSES = (STATUS_PENDING, STATUS_PAID, STATUS_EXPIRED, STATUS_CLOSED)

#: 允许的状态迁移（只前进）
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_PENDING: (STATUS_PAID, STATUS_EXPIRED, STATUS_CLOSED),
    STATUS_EXPIRED: (STATUS_PAID,),   # 仅在「钱已到账」的回调场景放行
    STATUS_PAID: (),
    STATUS_CLOSED: (),
}

_BUSY_TIMEOUT_MS = 10_000
_EVENT_DETAIL_MAX = 4000


# ── 时间 ──
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    """UTC ISO8601（秒精度，带 +00:00）；用于 DB 存储与 API 返回。"""
    value = dt or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(text: Any) -> datetime | None:
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def make_order_no(now: datetime | None = None) -> str:
    """订单号：``ord_<UTC YYYYMMDDHHMMSS>_<8 hex>``（契约示例格式）。"""
    value = (now or utcnow()).astimezone(timezone.utc)
    return "ord_" + value.strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(4)


# ── 异常 ──
class OrderError(Exception):
    """订单/入账业务错误；``code``/``message``/``status`` 映射 API 错误体。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class OrderNotFound(OrderError):
    def __init__(self, message: str = "订单不存在"):
        super().__init__("order_not_found", message, 404)


class OrderExpiredError(OrderError):
    def __init__(self, message: str = "订单已过期，请重新下单"):
        super().__init__("order_expired", message, 400)


class OrderNotPayable(OrderError):
    def __init__(self, message: str = "订单当前状态不可支付"):
        super().__init__("order_not_payable", message, 400)


class PaymentConflict(OrderError):
    def __init__(self, message: str = "支付流水已用于其他订单"):
        super().__init__("payment_conflict", message, 409)


# ── schema（契约 §5 逐列对应） ──
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    order_no         TEXT PRIMARY KEY,
    sku              TEXT NOT NULL,
    amount_cents     INTEGER NOT NULL,
    machine_code     TEXT NOT NULL,
    email            TEXT NULL,
    channel          TEXT NOT NULL,
    provider         TEXT NOT NULL,
    status           TEXT NOT NULL,
    order_token_hash TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    paid_at          TEXT NULL,
    expires_at       TEXT NOT NULL,
    license_id       TEXT NULL,
    provider_ref     TEXT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no       TEXT NOT NULL,
    provider       TEXT NOT NULL,
    transaction_id TEXT NOT NULL UNIQUE,
    amount_cents   INTEGER NOT NULL,
    raw            TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS licenses (
    license_id   TEXT PRIMARY KEY,
    order_no     TEXT NOT NULL UNIQUE,
    machine_code TEXT NOT NULL,
    sku          TEXT NOT NULL,
    text         TEXT NOT NULL,
    issued_at    TEXT NOT NULL,
    expires_at   TEXT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no   TEXT,
    kind       TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_payments_order_no ON payments(order_no);
CREATE INDEX IF NOT EXISTS idx_events_order_no ON events(order_no);
"""


class Database:
    """商业服务 SQLite 封装。一个实例对应一个 DB 文件；线程安全靠「每次操作新连接」。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    # ── 连接 ──
    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=_BUSY_TIMEOUT_MS / 1000.0,
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = %d" % _BUSY_TIMEOUT_MS)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """只读/短操作连接（自动关闭，不做事务包装）。"""
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """写事务：BEGIN IMMEDIATE，成功 COMMIT，失败 ROLLBACK。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    # ── 内部工具 ──
    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _event(conn: sqlite3.Connection, order_no: str | None, kind: str,
               detail: dict[str, Any] | None = None) -> None:
        """事务内写审计事件（不记录 token/私钥/口令；详情截断）。"""
        payload = ""
        if detail:
            try:
                payload = json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str)
            except Exception:
                payload = "{}"
        payload = payload[:_EVENT_DETAIL_MAX]
        conn.execute(
            "INSERT INTO events(order_no, kind, detail, created_at) VALUES(?,?,?,?)",
            (order_no, str(kind), payload, iso()),
        )

    # ── orders ──
    def create_order(self, order: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO orders(order_no, sku, amount_cents, machine_code, email, channel,"
                " provider, status, order_token_hash, created_at, paid_at, expires_at,"
                " license_id, provider_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    order["order_no"], order["sku"], int(order["amount_cents"]),
                    order["machine_code"], order.get("email"), order["channel"],
                    order["provider"], STATUS_PENDING, order["order_token_hash"],
                    order["created_at"], None, order["expires_at"], None,
                    order.get("provider_ref"),
                ),
            )
            self._event(conn, order["order_no"], "order_created", {
                "sku": order["sku"], "channel": order["channel"],
                "provider": order["provider"], "amount_cents": int(order["amount_cents"]),
            })
        return self.get_order(order["order_no"]) or dict(order)

    def get_order(self, order_no: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._row(conn.execute(
                "SELECT * FROM orders WHERE order_no=?", (str(order_no),)).fetchone())

    def load_order(self, order_no: str, *, expire: bool = True) -> dict[str, Any] | None:
        """读取订单；pending 且已过期时惰性标记为 expired（写事件）。"""
        order = self.get_order(order_no)
        if order is None:
            return None
        if expire and order["status"] == STATUS_PENDING and order["expires_at"] <= iso():
            with self.transaction() as conn:
                conn.execute(
                    "UPDATE orders SET status=? WHERE order_no=? AND status=?",
                    (STATUS_EXPIRED, order_no, STATUS_PENDING),
                )
                if conn.execute("SELECT changes() AS n").fetchone()["n"]:
                    self._event(conn, order_no, "order_expired")
            order = self.get_order(order_no) or order
        return order

    def expire_stale_orders(self) -> int:
        """把所有到期的 pending 订单置为 expired；返回变更条数（admin/定时任务用）。"""
        now = iso()
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT order_no FROM orders WHERE status=? AND expires_at<=?",
                (STATUS_PENDING, now)).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE orders SET status=? WHERE order_no=? AND status=?",
                    (STATUS_EXPIRED, row["order_no"], STATUS_PENDING))
                self._event(conn, row["order_no"], "order_expired")
            return len(rows)

    def set_order_provider_ref(self, order_no: str, provider_ref: str | None) -> None:
        if not provider_ref:
            return
        with self.transaction() as conn:
            conn.execute("UPDATE orders SET provider_ref=? WHERE order_no=?",
                         (str(provider_ref), str(order_no)))

    def close_order(self, order_no: str, reason: str = "") -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE orders SET status=? WHERE order_no=? AND status=?",
                (STATUS_CLOSED, order_no, STATUS_PENDING))
            self._event(conn, order_no, "order_closed", {"reason": reason[:200]})

    # ── payments / licenses / 入账 ──
    def get_payment_by_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._row(conn.execute(
                "SELECT * FROM payments WHERE transaction_id=?",
                (str(transaction_id),)).fetchone())

    def get_license(self, order_no: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._row(conn.execute(
                "SELECT * FROM licenses WHERE order_no=?", (str(order_no),)).fetchone())

    def get_license_by_id(self, license_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._row(conn.execute(
                "SELECT * FROM licenses WHERE license_id=?", (str(license_id),)).fetchone())

    def has_paid_order_for_machine(self, machine_code: str,
                                   sku_ids: tuple[str, ...]) -> bool:
        """该机器码是否已有指定 SKU 的已支付订单（用于首年/续费价判断）。"""
        if not machine_code or not sku_ids:
            return False
        placeholders = ",".join("?" for _ in sku_ids)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM orders WHERE machine_code=? AND status=? "
                f"AND sku IN ({placeholders}) LIMIT 1",
                (str(machine_code), STATUS_PAID, *sku_ids),
            ).fetchone()
        return row is not None

    def finalize_payment(self, *, order_no: str, provider: str, transaction_id: str,
                         amount_cents: int, raw: dict[str, Any] | str | None,
                         license: dict[str, Any], trade_status: str = "TRADE_SUCCESS",
                         allow_expired: bool = False) -> dict[str, Any]:
        """原子入账：写 payments + 写 licenses + 订单置 paid。

        幂等：订单已 paid 时直接返回既有 license（不重复签发/入账）；
        重复 transaction_id 只允许同一订单（否则 PaymentConflict）。
        返回 ``{"order", "license", "paid_now", "duplicate"}``。
        """
        if isinstance(raw, dict):
            raw_text = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)[:_EVENT_DETAIL_MAX]
        else:
            raw_text = str(raw or "")
        with self.transaction() as conn:
            order = self._row(conn.execute(
                "SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone())
            if order is None:
                raise OrderNotFound()
            if order["status"] == STATUS_PAID:
                license_row = self._row(conn.execute(
                    "SELECT * FROM licenses WHERE order_no=?", (order_no,)).fetchone())
                if license_row is None:
                    raise OrderNotPayable("订单已支付但缺少授权记录，请联系客服")
                return {"order": order, "license": license_row,
                        "paid_now": False, "duplicate": True}
            if order["status"] == STATUS_CLOSED:
                raise OrderNotPayable("订单已关闭，无法支付")
            if not allow_expired and (
                    order["status"] == STATUS_EXPIRED
                    or (order["status"] == STATUS_PENDING and order["expires_at"] <= iso())):
                # 过期不可支付（pending 但已到期的订单在 GET/load_order 时才会被惰性标记，
                # 这里再兜底一次，保证「直接入账」路径也遵守 15 分钟有效期）
                raise OrderExpiredError()

            previous = conn.execute(
                "SELECT * FROM payments WHERE transaction_id=?",
                (str(transaction_id),)).fetchone()
            if previous is not None and previous["order_no"] != order_no:
                raise PaymentConflict()

            now = iso()
            license_row = self._row(conn.execute(
                "SELECT * FROM licenses WHERE order_no=?", (order_no,)).fetchone())
            if license_row is None:
                try:
                    conn.execute(
                        "INSERT INTO licenses(license_id, order_no, machine_code, sku,"
                        " text, issued_at, expires_at) VALUES(?,?,?,?,?,?,?)",
                        (
                            license["license_id"], order_no, license.get("machine_code", ""),
                            license.get("sku", order["sku"]), license["text"],
                            license.get("issued_at") or now, license.get("expires_at"),
                        ),
                    )
                except sqlite3.IntegrityError:
                    # license_id 撞车（概率极低）：重新查 order_no 是否已签
                    license_row = self._row(conn.execute(
                        "SELECT * FROM licenses WHERE order_no=?", (order_no,)).fetchone())
                    if license_row is None:
                        raise OrderNotPayable("授权写入冲突，请稍后重试") from None
                else:
                    license_row = self._row(conn.execute(
                        "SELECT * FROM licenses WHERE order_no=?", (order_no,)).fetchone())

            conn.execute(
                "INSERT OR IGNORE INTO payments(order_no, provider, transaction_id,"
                " amount_cents, raw, created_at) VALUES(?,?,?,?,?,?)",
                (order_no, str(provider), str(transaction_id), int(amount_cents),
                 raw_text, now),
            )
            conn.execute(
                "UPDATE orders SET status=?, paid_at=?, license_id=?, provider=?"
                " WHERE order_no=?",
                (STATUS_PAID, order["paid_at"] or now,
                 license_row["license_id"] if license_row else license["license_id"],
                 str(provider), order_no),
            )
            self._event(conn, order_no, "order_paid", {
                "provider": str(provider),
                "transaction_id": str(transaction_id),
                "amount_cents": int(amount_cents),
                "trade_status": str(trade_status),
                "license_id": license_row["license_id"] if license_row else license["license_id"],
            })
            updated = self._row(conn.execute(
                "SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone())
            if updated is None or license_row is None:
                raise OrderNotPayable("入账事务异常，请稍后重试")
            return {"order": updated, "license": license_row,
                    "paid_now": True, "duplicate": False}

    # ── events ──
    def log_event(self, order_no: str | None, kind: str,
                  detail: dict[str, Any] | None = None) -> None:
        with self.transaction() as conn:
            self._event(conn, order_no, kind, detail)

    def list_events(self, order_no: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.connect() as conn:
            if order_no:
                rows = conn.execute(
                    "SELECT * FROM events WHERE order_no=? ORDER BY id DESC LIMIT ?",
                    (str(order_no), limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    # ── 管理/统计 ──
    def list_orders(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM orders WHERE status=? ORDER BY rowid DESC LIMIT ?",
                    (str(status), limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM orders ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            by_status = {row["status"]: int(row["n"]) for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM orders GROUP BY status").fetchall()}
            orders_total = int(conn.execute(
                "SELECT COUNT(*) AS n FROM orders").fetchone()["n"])
            revenue = int(conn.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) AS n FROM orders WHERE status=?",
                (STATUS_PAID,)).fetchone()["n"])
            licenses = int(conn.execute(
                "SELECT COUNT(*) AS n FROM licenses").fetchone()["n"])
            payments = int(conn.execute(
                "SELECT COUNT(*) AS n FROM payments").fetchone()["n"])
        return {
            "orders": {
                "total": orders_total,
                "pending": by_status.get(STATUS_PENDING, 0),
                "paid": by_status.get(STATUS_PAID, 0),
                "expired": by_status.get(STATUS_EXPIRED, 0),
                "closed": by_status.get(STATUS_CLOSED, 0),
            },
            "revenue_cents": revenue,
            "licenses": licenses,
            "payments": payments,
        }

    def ping(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """测试/启动辅助：等待 DB 可写（WAL 初始化）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ping():
                return True
            time.sleep(0.05)
        return False


__all__ = [
    "Database", "OrderError", "OrderNotFound", "OrderExpiredError",
    "OrderNotPayable", "PaymentConflict",
    "STATUS_PENDING", "STATUS_PAID", "STATUS_EXPIRED", "STATUS_CLOSED",
    "ORDER_STATUSES", "ALLOWED_TRANSITIONS",
    "utcnow", "iso", "parse_iso", "make_order_no",
]
