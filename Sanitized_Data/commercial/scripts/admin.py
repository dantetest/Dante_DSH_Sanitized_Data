#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商业服务管理 CLI（本地运维/对账用）。

安全模型：能读本地 SQLite 文件即视为管理员（与商业服务同机/同权限）；
``COMMERCIAL_ADMIN_TOKEN`` 预留给未来远程管理接口，本 CLI 不需要它。

用法（在 Sanitized_Data 目录）：
    python commercial\\scripts\\admin.py stats
    python commercial\\scripts\\admin.py orders --status paid --limit 20
    python commercial\\scripts\\admin.py order ord_20261102180000_ab12cd34
    python commercial\\scripts\\admin.py order ord_... --show-license
    python commercial\\scripts\\admin.py events --limit 30
    python commercial\\scripts\\admin.py expire
    python commercial\\scripts\\admin.py check

约定：
- 默认只列必要字段，不打印订单 token 哈希、私钥、口令；
- ``--show-license`` 会输出授权全文（等同交付给客户的授权码），谨慎使用。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COMMERCIAL_DIR = Path(__file__).resolve().parents[1]
if str(COMMERCIAL_DIR) not in sys.path:
    sys.path.insert(0, str(COMMERCIAL_DIR))


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _order_brief(row: dict) -> dict:
    return {
        "order_no": row.get("order_no"),
        "created_at": row.get("created_at"),
        "status": row.get("status"),
        "sku": row.get("sku"),
        "amount_cents": row.get("amount_cents"),
        "channel": row.get("channel"),
        "provider": row.get("provider"),
        "paid_at": row.get("paid_at"),
        "expires_at": row.get("expires_at"),
        "license_id": row.get("license_id"),
    }


def _load(args):
    from server.config import Config
    from server.db import Database

    overrides = {}
    if args.data_dir:
        overrides["COMMERCIAL_DATA_DIR"] = args.data_dir
    if args.db:
        overrides["COMMERCIAL_DB"] = args.db
    env_path = Path(args.env_file) if args.env_file else None
    config = Config.load(env_path=env_path, overrides=overrides)
    database = Database(config.db_path)
    database.init_schema()
    return config, database


def cmd_stats(config, database, args) -> int:
    print(_dumps({"db": str(config.db_path), **database.stats()}))
    return 0


def cmd_orders(config, database, args) -> int:
    rows = [_order_brief(row) for row in database.list_orders(args.status, args.limit)]
    if args.json:
        print(_dumps(rows))
        return 0
    if not rows:
        print("（无订单）")
        return 0
    header = f"{'订单号':32s} {'状态':8s} {'套餐':8s} {'金额(分)':>8s} {'通道':8s} {'创建时间':20s}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{str(row['order_no'])[:32]:32s} {str(row['status']):8s} "
              f"{str(row['sku']):8s} {int(row['amount_cents'] or 0):8d} "
              f"{str(row['channel']):8s} {str(row['created_at'])[:19]:20s}")
    return 0


def cmd_order(config, database, args) -> int:
    order = database.get_order(args.order_no)
    if order is None:
        print(f"订单不存在：{args.order_no}", file=sys.stderr)
        return 2
    license_row = database.get_license(args.order_no)
    payload = {"order": _order_brief(order)}
    if license_row:
        license_view = {
            "license_id": license_row.get("license_id"),
            "sku": license_row.get("sku"),
            "machine_code": license_row.get("machine_code"),
            "issued_at": license_row.get("issued_at"),
            "expires_at": license_row.get("expires_at"),
        }
        if args.show_license:
            license_view["text"] = license_row.get("text")
        payload["license"] = license_view
    payload["events"] = database.list_events(args.order_no, limit=args.events)
    print(_dumps(payload))
    return 0


def cmd_events(config, database, args) -> int:
    print(_dumps(database.list_events(args.order_no, limit=args.limit)))
    return 0


def cmd_expire(config, database, args) -> int:
    count = database.expire_stale_orders()
    print(f"已将 {count} 个过期未支付订单置为 expired。")
    return 0


def cmd_check(config, database, args) -> int:
    from server import catalog
    from server.payments import available_channels
    from server.signer import LicenseSigner

    signer = LicenseSigner(config.LICENSE_PRIVATE_KEY_PATH, key_id=config.LICENSE_KEY_ID,
                           password_env=config.LICENSE_KEY_PASSWORD_ENV)
    report = {
        "version": config.VERSION,
        "provider": config.PAYMENT_PROVIDER,
        "mock_enabled": bool(config.PAYMENT_MOCK_ENABLED),
        "base_url": config.COMMERCIAL_BASE_URL,
        "db": {"path": str(config.db_path), "ok": database.ping()},
        "signer": signer.describe(),
        "channels": available_channels(config),
        "catalog_skus": [sku["id"] for sku in catalog.catalog_payload(config)["skus"]],
        "stats": database.stats(),
    }
    print(_dumps(report))
    return 0 if report["db"]["ok"] else 1


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env-file", default=None, help="env 文件（默认 commercial/.env）")
    common.add_argument("--data-dir", default=None, help="覆盖 COMMERCIAL_DATA_DIR")
    common.add_argument("--db", default=None, help="覆盖 COMMERCIAL_DB")

    parser = argparse.ArgumentParser(
        description="数据隐站商业服务管理 CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_stats = sub.add_parser("stats", parents=[common], help="订单/收入/授权统计")
    p_stats.set_defaults(func=cmd_stats)

    p_orders = sub.add_parser("orders", parents=[common], help="列出订单")
    p_orders.add_argument("--status", choices=("pending", "paid", "expired", "closed"),
                          default=None)
    p_orders.add_argument("--limit", type=int, default=50)
    p_orders.add_argument("--json", action="store_true", help="输出 JSON")
    p_orders.set_defaults(func=cmd_orders)

    p_order = sub.add_parser("order", parents=[common], help="查看单个订单/授权/事件")
    p_order.add_argument("order_no")
    p_order.add_argument("--show-license", action="store_true",
                         help="输出授权全文（敏感，等同交付给客户的凭证）")
    p_order.add_argument("--events", type=int, default=10, help="附带最近事件条数")
    p_order.set_defaults(func=cmd_order)

    p_events = sub.add_parser("events", parents=[common], help="查看审计事件")
    p_events.add_argument("--order-no", default=None)
    p_events.add_argument("--limit", type=int, default=30)
    p_events.set_defaults(func=cmd_events)

    p_expire = sub.add_parser("expire", parents=[common], help="把过期未支付订单置为 expired")
    p_expire.set_defaults(func=cmd_expire)

    p_check = sub.add_parser("check", parents=[common], help="配置/DB/签名密钥自检")
    p_check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    _reconfigure_stdio()
    args = _build_parser().parse_args(argv)
    config, database = _load(args)
    return int(args.func(config, database, args))


if __name__ == "__main__":
    raise SystemExit(main())
