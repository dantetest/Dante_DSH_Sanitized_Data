#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线生成 core 密钥对（最高信任根）。

- 输出加密 Ed25519 私钥、core 公钥基线、key_info；
- 不打印私钥/口令；
- core 私钥不得进入仓库、CI、安装包或聊天记录。
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]


def _is_inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 core 签名密钥对（离线）")
    ap.add_argument("--key-id", default="core-2026")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--password-env", default="CORE_KEY_PASSWORD")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_dir = (Path(args.out_dir).expanduser() if args.out_dir
               else Path.home() / ".sanitized-data-core-keys" / args.key_id)
    if _is_inside_repo(out_dir):
        raise SystemExit("拒绝把 core 私钥写进仓库目录；请用 --out-dir 指定仓库外目录")
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path = out_dir / "core_signing_ed25519.pem"
    public_path = out_dir / "core_public.b64"
    info_path = out_dir / "core_key_info.txt"
    if (private_path.exists() or public_path.exists()) and not args.force:
        raise SystemExit(f"已存在：{out_dir}\n确需重建请加 --force（会导致已发 keyring 无法再用新 key 重签）")

    password = os.environ.get(args.password_env, "").encode("utf-8")
    if not password:
        p1 = getpass.getpass("设置 core 私钥口令（至少 12 位，不会回显）: ")
        p2 = getpass.getpass("再输入一次: ")
        if p1 != p2:
            raise SystemExit("两次口令不一致")
        password = p1.encode("utf-8")
    if len(password) < 12:
        raise SystemExit("口令太短（至少 12 个字符）")

    key = Ed25519PrivateKey.generate()
    private_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    ))
    try:
        os.chmod(private_path, 0o600)
    except Exception:
        pass
    public_b64 = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
    public_path.write_text(public_b64 + "\n", encoding="ascii")
    fingerprint = hashlib.sha256(base64.b64decode(public_b64)).hexdigest().upper()
    info_path.write_text(
        f"key_id={args.key_id}\n"
        f"created_utc={datetime.now(timezone.utc).isoformat()}\n"
        f"public_b64={public_b64}\n"
        f"public_sha256={fingerprint}\n"
        f"private_file={private_path.name}\n"
        "要求：core 私钥离线保存，和口令分开；至少两份异地备份。\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "key_id": args.key_id,
        "out_dir": str(out_dir),
        "private_file": str(private_path),
        "public_b64": public_b64,
        "public_sha256": fingerprint,
    }, ensure_ascii=False, indent=2))
    print("把 public_b64 写入 app/server/yinzhan/licensing/keys.py 的 CORE_PUBLIC_KEYS。")
    print("不要提交/发送 core 私钥或口令。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
