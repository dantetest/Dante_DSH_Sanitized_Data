#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 core 私钥给 public_keys.json 签名。

输入是扁平 license 公钥 JSON：
    {"prod-2026": "<base64 raw Ed25519 pub>", ...}
输出是带 schema/root/sig 的 keyring envelope。

core 私钥只从离线文件读取；不打印私钥/口令。
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
APP_SERVER = ROOT / "app" / "server"
if str(APP_SERVER) not in sys.path:
    sys.path.insert(0, str(APP_SERVER))

from yinzhan.licensing.keys import canonical_keyring_payload  # noqa: E402


def _is_inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except Exception:
        return False


def _load_keys(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("输入 JSON 必须是对象")
    source = raw.get("keys") if isinstance(raw.get("keys"), dict) else raw
    result: dict[str, str] = {}
    for key_id, value in source.items():
        if not isinstance(value, str):
            raise SystemExit(f"key {key_id} 不是字符串")
        try:
            decoded = base64.b64decode(value, validate=True)
        except Exception as exc:
            raise SystemExit(f"key {key_id} 不是合法 base64：{exc}") from exc
        if len(decoded) != 32:
            raise SystemExit(f"key {key_id} 不是 32 字节 Ed25519 公钥")
        result[str(key_id)] = value
    if not result:
        raise SystemExit("输入没有任何公钥")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="给 license public_keys.json 签名")
    ap.add_argument("--core-private-key", required=True)
    ap.add_argument("--root-key-id", default="core-2026")
    ap.add_argument("--in", dest="input", required=True,
                    help="扁平公钥 JSON（可直接用当前 public_keys.json）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--password-env", default="CORE_KEY_PASSWORD")
    ap.add_argument("--revision", type=int, default=1)
    ap.add_argument("--issued-at", default=None)
    ap.add_argument("--allow-test", action="store_true")
    args = ap.parse_args()

    private_path = Path(args.core_private_key).expanduser()
    out_path = Path(args.out)
    if _is_inside_repo(private_path):
        raise SystemExit("拒绝从仓库目录读取 core 私钥；请用仓库外离线路径")
    if _is_inside_repo(out_path) and out_path.name != "public_keys.json":
        raise SystemExit("core keyring 输出只应为 public_keys.json")

    keys = _load_keys(Path(args.input))
    test_keys = [k for k in keys if k.startswith("test")]
    if test_keys and not args.allow_test:
        raise SystemExit(f"拒绝把测试公钥写入生产 keyring：{test_keys}")

    password = os.environ.get(args.password_env, "").encode("utf-8") or None
    try:
        private = serialization.load_pem_private_key(private_path.read_bytes(),
                                                     password=password)
    except TypeError:
        try:
            password = getpass.getpass("core 私钥口令（不会回显）: ").encode("utf-8")
            private = serialization.load_pem_private_key(private_path.read_bytes(),
                                                         password=password)
        except Exception as exc:
            raise SystemExit(f"core 私钥读取失败：{exc}") from exc
    except Exception as exc:
        raise SystemExit(f"core 私钥读取失败：{exc}") from exc

    payload = {
        "schema": 2,
        "root_key_id": str(args.root_key_id),
        "keyring_revision": int(args.revision),
        "issued_at": args.issued_at or datetime.now(timezone.utc).isoformat(),
        "keys": keys,
    }
    signature = private.sign(canonical_keyring_payload(payload))
    result = dict(payload)
    result["sig"] = base64.b64encode(signature).decode("ascii")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({
        "out": str(out_path),
        "root_key_id": args.root_key_id,
        "keyring_revision": args.revision,
        "keys": sorted(keys),
        "keys_sha256": hashlib.sha256(
            json.dumps(keys, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest().upper(),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
