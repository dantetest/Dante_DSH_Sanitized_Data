#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成离线 license 的 Ed25519 密钥对（W1 运维工具）。

私钥只写到你指定的安全目录，绝不写进仓库/安装包/聊天记录。
生成后把 public_keys.json 里的公钥内容合并到
``app/server/yinzhan/licensing/public_keys.json``，再重新打包。

用法：
  set LICENSE_KEY_PASSWORD=...       # 私钥加密口令，不要写进仓库
  python deploy/license_keygen.py --key-id prod-2026 \
      --out-dir %USERPROFILE%\\.sanitized-data-license-keys
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-id", default="prod-2026")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--password-env", default="LICENSE_KEY_PASSWORD")
    args = ap.parse_args()
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else (
        Path.home() / ".sanitized-data-license-keys")
    out_dir.mkdir(parents=True, exist_ok=True)
    password = os.environ.get(args.password_env, "").encode("utf-8")
    if len(password) < 8:
        raise SystemExit(f"缺少 {args.password_env}（至少 8 个字符），拒绝生成未加密私钥")
    key = Ed25519PrivateKey.generate()
    private_path = out_dir / "license_signing_ed25519.pem"
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
    public_path = out_dir / "public_keys.json"
    public_path.write_text(json.dumps({args.key_id: public_b64}, indent=2) + "\n",
                           encoding="utf-8")
    print("private:", private_path)
    print("public :", public_path)
    print("key_id :", args.key_id)
    print("把下面内容合并到 app/server/yinzhan/licensing/public_keys.json：")
    print(json.dumps({args.key_id: public_b64}, indent=2))
    print("\n注意：私钥文件请离线备份；丢失/泄露需要重新签发所有 license。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
