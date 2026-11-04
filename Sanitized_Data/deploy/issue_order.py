#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一单发码：签发 license.json + 生成本机可直接粘贴的授权码 + 交付话术 + 台账登记。

与 deploy/license_issue.py 的区别：面向「已经收款的订单」，一条命令产出交付所需全部文件，
并强制登记订单台账（防重复签发）。私钥只从本地文件读取，不生成、不上传、不打印。

用法（Windows PowerShell，私钥口令交互输入）：
  python -X utf8 deploy\\issue_order.py `
    --private-key "$env:USERPROFILE\\.sanitized-data-license-keys\\license_signing_ed25519.pem" `
    --key-id prod-2026 --sku pro `
    --machine-code ZH39-7N5C-98CD-A8GB `
    --order-id ord_20261102_0001 `
    --customer-name "张三" --contact "微信 zhangsan" `
    --amount 299 --payment-method "微信支付" --paid-at 2026-11-02

产物（默认 out/licenses/）：
  ord_20261102_0001.json          发给客户的 license.json（附件）
  ord_20261102_0001.code.txt      纯文本授权码（一整行 JSON，可直接粘到授权页）
  ord_20261102_0001.delivery.txt  交付话术（复制到微信发送）
  orders.csv                      订单台账（追加一行；文件不存在时自动建表头）

安全：
  - 私钥口令默认从环境变量 LICENSE_KEY_PASSWORD 读取；未设置则交互输入（不回显）。
  - 同一 order_id 已存在于台账/已有授权文件时默认拒绝，防止重复签发；确需重签加 --force。
"""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from cryptography.hazmat.primitives import serialization  # noqa: E402

from yinzhan.licensing import tiers  # noqa: E402
from yinzhan.licensing.issue import (  # noqa: E402
    build_payload, normalize_machine_code, sign_license, write_license,
)

LEDGER_COLUMNS = [
    "order_id", "customer_name", "customer_org", "contact", "sku", "amount_cny",
    "paid_at", "payment_method", "machine_codes", "license_id", "issued_at",
    "expires_at", "invoice_status", "notes",
]

_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _load_key(path: Path, password_env: str):
    if not path.is_file():
        raise SystemExit(f"私钥不存在：{path}")
    password = os.environ.get(password_env, "").encode("utf-8") or None
    try:
        return serialization.load_pem_private_key(path.read_bytes(), password=password)
    except TypeError:
        try:
            password = getpass.getpass("私钥口令（不会回显）: ").encode("utf-8")
            return serialization.load_pem_private_key(path.read_bytes(), password=password)
        except Exception as exc:
            raise SystemExit(f"私钥读取失败：{exc}")
    except Exception as exc:
        raise SystemExit(f"私钥读取失败：{exc}")


def _existing_order_ids(ledger: Path) -> set[str]:
    if not ledger.is_file():
        return set()
    try:
        with ledger.open("r", encoding="utf-8-sig", newline="") as fp:
            return {str(row.get("order_id") or "").strip() for row in csv.DictReader(fp)}
    except Exception:
        return set()


def _append_ledger(ledger: Path, row: dict) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    new_file = not ledger.is_file() or ledger.stat().st_size == 0
    with ledger.open("a", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=LEDGER_COLUMNS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def _expires_text(payload: dict) -> str:
    if payload.get("term") == "perpetual":
        return "永久"
    return str(payload.get("expires_at") or "")[:10]


def _term_text(payload: dict) -> str:
    return "买断" if payload.get("term") == "perpetual" else "年费"


def build_delivery_text(payload: dict, container: dict, *, amount: str,
                        payment_method: str, contact: str) -> str:
    """生成给客户的交付话术（含备用纯文本授权码）。"""
    code = json.dumps(container, ensure_ascii=False, separators=(",", ":"))
    edition = payload.get("edition") or payload.get("sku")
    machines = "、".join(payload.get("machine_codes") or [])
    customer = payload.get("customer") or {}
    name = (customer.get("name") or "").strip()
    hello = f"{name}，" if name else ""
    pay_line = ""
    if amount:
        pay_line = f"已确认收款 {amount} 元"
        if payment_method:
            pay_line += f"（{payment_method}）"
        pay_line += "。"
    if payload.get("term") == "perpetual":
        tail = ("- 本授权为买断授权，长期有效；后续大版本升级/更新服务按购买时约定执行。\n"
                "- 数据与映射永久保留在本机，重装/换机后凭订单号可重新导入（换机需重签）。")
    else:
        tail = ("- 到期前我们会提醒续费；到期后有 14 天宽限期，之后自动回免费版（3 份/天），"
                "数据与映射不删。")
    return f"""【数据隐站 · {edition}授权交付】订单号：{payload.get('order_id')}

{hello}您好，{pay_line}

附件是本机专属授权文件（license.json），按 3 步导入即可解锁：
1）打开数据隐站 → 点右上角「授权」进入授权页
2）把附件 license.json 拖进授权页（或点「选择文件」；也可以把下方【备用授权码】整段复制，
   粘贴到授权页文本框后点「导入授权」）
3）看到「已授权 · {edition}」（到期：{_expires_text(payload)}）即成功；
   建议顺手点「备份当前授权」保存一份，换电脑/重装后可直接导入。

授权信息
- license_id：{payload.get('license_id')}
- 版本：{edition}（{_term_text(payload)}）
- 到期：{_expires_text(payload)}
- 绑定本机识别码：{machines}

说明
- 授权离线校验，不联网、不上传文件；导入成功后立即可用，一般无需重启（个别页面提示时重启一次即可）。
- 本授权绑定上述本机识别码；换电脑/重装系统请凭订单号联系我们重签（12 个月内首次免费）。
{tail}
- 需要发票请回复：抬头、税号、邮箱（普票/专票均可）。

【备用授权码：如果附件不方便，可整段复制下面这一行粘贴到授权页】
{code}

—— 交付联系方式：{contact or '（请补充客服微信/邮箱）'}
"""


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser(description="一单发码：签发 license + 授权码 + 交付话术 + 台账")
    ap.add_argument("--private-key", required=True)
    ap.add_argument("--key-id", required=True)
    ap.add_argument("--sku", required=True, choices=["personal", "pro", "team", "org"])
    ap.add_argument("--machine-code", action="append", required=True,
                    help="可重复；XXXX-XXXX-XXXX-XXXX")
    ap.add_argument("--order-id", required=True)
    ap.add_argument("--license-id", default=None)
    ap.add_argument("--term", default="annual", choices=["annual", "perpetual"])
    ap.add_argument("--seats", type=int, default=None)
    ap.add_argument("--max-devices", type=int, default=None)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--expires-at", default=None)
    ap.add_argument("--customer-name", default="")
    ap.add_argument("--customer-org", default="")
    ap.add_argument("--contact", default="", help="交付联系方式（微信/邮箱），写进话术")
    ap.add_argument("--team-id", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--amount", default="", help="实收金额（元），可留空")
    ap.add_argument("--payment-method", default="", help="微信支付/支付宝/对公转账/平台")
    ap.add_argument("--paid-at", default="", help="到账日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--invoice-status", default="待确认")
    ap.add_argument("--out-dir", default=str(ROOT / "out" / "licenses"))
    ap.add_argument("--ledger", default="", help="台账 CSV 路径，默认 <out-dir>/orders.csv")
    ap.add_argument("--force", action="store_true", help="允许重复订单号/覆盖已有文件（慎用）")
    ap.add_argument("--password-env", default="LICENSE_KEY_PASSWORD")
    args = ap.parse_args()

    order_id = args.order_id.strip()
    if not _ORDER_ID_RE.match(order_id):
        raise SystemExit("order_id 只允许字母/数字/._-，2–64 位：既做台账主键也做文件名")

    out_dir = Path(args.out_dir).expanduser()
    ledger = Path(args.ledger).expanduser() if args.ledger else out_dir / "orders.csv"
    license_path = out_dir / f"{order_id}.json"
    code_path = out_dir / f"{order_id}.code.txt"
    delivery_path = out_dir / f"{order_id}.delivery.txt"

    if not args.force:
        if order_id in _existing_order_ids(ledger):
            raise SystemExit(f"台账里已有订单 {order_id}，默认拒绝重复签发；确需重签加 --force")
        if license_path.exists():
            raise SystemExit(f"授权文件已存在：{license_path}；确需覆盖加 --force")

    key = _load_key(Path(args.private_key).expanduser(), args.password_env)
    try:
        payload = build_payload(
            sku=args.sku,
            machine_codes=[normalize_machine_code(c) for c in args.machine_code],
            order_id=order_id,
            license_id=args.license_id,
            term=args.term,
            seats=args.seats,
            max_devices=args.max_devices,
            days=args.days,
            expires_at=args.expires_at,
            customer_name=args.customer_name,
            customer_org=args.customer_org,
            team_id=args.team_id,
            notes=args.notes,
        )
    except Exception as exc:
        raise SystemExit(f"参数/授权数据无效：{exc}")

    container = sign_license(payload, key, args.key_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = write_license(container, license_path)
    code_path.write_text(json.dumps(container, ensure_ascii=False, separators=(",", ":")) + "\n",
                         encoding="utf-8")
    delivery = build_delivery_text(payload, container, amount=args.amount.strip(),
                                   payment_method=args.payment_method.strip(),
                                   contact=args.contact.strip())
    delivery_path.write_text(delivery, encoding="utf-8")

    paid_at = args.paid_at.strip() or datetime.now().astimezone().date().isoformat()
    row = {
        "order_id": payload["order_id"],
        "customer_name": args.customer_name,
        "customer_org": args.customer_org,
        "contact": args.contact,
        "sku": payload["sku"],
        "amount_cny": args.amount.strip(),
        "paid_at": paid_at,
        "payment_method": args.payment_method.strip(),
        "machine_codes": ";".join(payload["machine_codes"]),
        "license_id": payload["license_id"],
        "issued_at": payload["issued_at"],
        "expires_at": payload.get("expires_at") or "",
        "invoice_status": args.invoice_status,
        "notes": " | ".join(x for x in (args.notes, f"term={payload['term']}") if x),
    }
    ledger_error = ""
    try:
        _append_ledger(ledger, row)
    except Exception as exc:  # 授权已签发，台账失败必须让人看见，避免漏账
        ledger_error = str(exc)

    print("=" * 66)
    print("已签发：", payload["license_id"])
    print("订单号：", payload["order_id"], "｜ 版本：", payload["edition"],
          "｜ 期限：", payload["term"])
    print("机器码：", "、".join(payload["machine_codes"]))
    print("到期  ：", _expires_text(payload))
    print("授权文件：", written)
    print("文本授权码：", code_path)
    print("交付话术：", delivery_path)
    print("订单台账：", ledger, ("（写入失败：" + ledger_error + "）") if ledger_error else "（已追加）")
    print("-" * 66)
    print("下一步：把 .json 附件发给客户（或复制 .delivery.txt 整段发微信），")
    print("        客户在「授权」页导入后，确认状态为『已授权』即完成交付。")
    print("=" * 66)
    if ledger_error:
        print("警告：台账写入失败，请手工补登：" + str(ledger), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
