#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W4a 人工签发 license.json。

私钥只从本地文件读取；不生成、不上传、不打印。
用法示例：
  set LICENSE_KEY_PASSWORD=...
  python deploy/license_issue.py ^
    --private-key %USERPROFILE%\\.sanitized-data-license-keys\\license_signing_ed25519.pem ^
    --key-id prod-2026 --sku personal ^
    --machine-code K7Q2-9WXM-4TRB-EFGH ^
    --order-id ord_20261025_0001 ^
    --out out/licenses/lic_personal_001.json
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from cryptography.hazmat.primitives import serialization  # noqa: E402

from yinzhan.licensing.issue import (  # noqa: E402
    build_payload, normalize_machine_code, sign_license, write_license,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="签发离线 license.json")
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
    ap.add_argument("--expires-at", default=None,
                    help="ISO8601；不填则 now + --days")
    ap.add_argument("--customer-name", default="")
    ap.add_argument("--customer-org", default="")
    ap.add_argument("--team-id", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--password-env", default="LICENSE_KEY_PASSWORD")
    args = ap.parse_args()

    key_path = Path(args.private_key).expanduser()
    if not key_path.is_file():
        raise SystemExit(f"私钥不存在：{key_path}")
    password = os.environ.get(args.password_env, "").encode("utf-8") or None
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=password)
    except TypeError:
        # 加密私钥：交互输入，避免口令出现在命令历史/进程环境里
        try:
            password = getpass.getpass("私钥口令（不会回显）: ").encode("utf-8")
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=password)
        except Exception as exc:
            raise SystemExit(f"私钥读取失败：{exc}")
    except Exception as exc:
        raise SystemExit(f"私钥读取失败：{exc}")

    try:
        payload = build_payload(
            sku=args.sku,
            machine_codes=[normalize_machine_code(c) for c in args.machine_code],
            order_id=args.order_id,
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
    out = Path(args.out).expanduser() if args.out else (
        ROOT / "out" / "licenses" / f"{payload['license_id']}.json")
    path = write_license(container, out)
    print("license_id:", payload["license_id"])
    print("order_id  :", payload["order_id"])
    print("sku       :", payload["sku"], "term:", payload["term"])
    print("machines  :", ", ".join(payload["machine_codes"]))
    print("expires_at:", payload["expires_at"])
    print("out       :", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
