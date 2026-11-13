#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线授权转移/重签工具。

产品端生成转移请求（无需私钥）：
  python deploy/license_transfer.py --request-only \
    --old-license old-license.json --new-machine-code XXXX-XXXX-XXXX-XXXX \
    --out out/transfer_request.json

销售端重签（私钥只在本地读取）：
  python deploy/license_transfer.py \
    --old-license old-license.json --new-machine-code XXXX-XXXX-XXXX-XXXX \
    --private-key %USERPROFILE%\\.sanitized-data-license-keys\\license_signing_ed25519.pem \
    --key-id prod-2026 --out out/licenses/reissued.json
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from cryptography.hazmat.primitives import serialization  # noqa: E402

from yinzhan.licensing.issue import write_license  # noqa: E402
from yinzhan.licensing.transfer import (  # noqa: E402
    create_transfer_request, reissue_from_license,
)


def _load_key(path: Path, password_env: str):
    if not path.is_file():
        raise SystemExit(f"私钥不存在：{path}")
    pw = os.environ.get(password_env, "").encode("utf-8") or None
    try:
        return serialization.load_pem_private_key(path.read_bytes(), password=pw)
    except TypeError:
        try:
            pw = getpass.getpass("私钥口令（不会回显）: ").encode("utf-8")
            return serialization.load_pem_private_key(path.read_bytes(), password=pw)
        except Exception as exc:
            raise SystemExit(f"私钥读取失败：{exc}")
    except Exception as exc:
        raise SystemExit(f"私钥读取失败：{exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="离线授权转移/重签")
    ap.add_argument("--old-license", required=True)
    ap.add_argument("--new-machine-code", required=True)
    ap.add_argument("--request-only", action="store_true",
                    help="只生成转移请求，不签名")
    ap.add_argument("--private-key", default="")
    ap.add_argument("--key-id", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--notes", default="设备转移/重装系统重绑")
    ap.add_argument("--max-devices", type=int, default=None)
    ap.add_argument("--password-env", default="LICENSE_KEY_PASSWORD")
    args = ap.parse_args()

    old_path = Path(args.old_license).expanduser()
    if not old_path.is_file():
        raise SystemExit(f"旧授权不存在：{old_path}")
    out = Path(args.out).expanduser()

    if args.request_only:
        request = create_transfer_request(old_path.read_text(encoding="utf-8"),
                                          args.new_machine_code)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print("request:", out)
        print("old_license_id:", request["old_license_id"])
        print("new_machine_code:", request["new_machine_code"])
        return 0

    if not args.private_key or not args.key_id:
        raise SystemExit("重签模式需要 --private-key 和 --key-id")
    key = _load_key(Path(args.private_key).expanduser(), args.password_env)
    container = reissue_from_license(
        old_path.read_text(encoding="utf-8"), args.new_machine_code, key,
        args.key_id, max_devices=args.max_devices, note=args.notes)
    write_license(container, out)
    payload = container["payload"]
    print("license_id:", payload["license_id"])
    print("order_id  :", payload["order_id"])
    print("machines  :", ", ".join(payload["machine_codes"]))
    print("transfer  :", payload.get("transfer_count"))
    print("expires_at:", payload.get("expires_at"))
    print("out       :", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
