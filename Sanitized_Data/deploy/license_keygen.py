#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成离线 license 的 Ed25519 密钥对（W1 运维工具）。

私钥只写到你指定的安全目录，绝不写进仓库/安装包/聊天记录。

新流程（v1.5.0+）：
1. 本工具生成 license 密钥对，并把公钥写到一个**未签名**的
   ``public_keys.json``（格式仍是扁平 key_id → base64 公钥）；
2. 用 core 私钥运行 ``deploy/sign_keyring.py`` 生成带 ``sig`` 的最终
   ``app/server/yinzhan/licensing/public_keys.json``；
3. 构建脚本会拒绝未签名的 keyring。

用法：
  # 推荐：交互输入口令（不出现在命令历史）
  python deploy/license_keygen.py --key-id prod-2026 \
      --out-dir %USERPROFILE%\\.sanitized-data-license-keys

  # 自动签发机：用环境变量传口令
  set LICENSE_KEY_PASSWORD=...
  python deploy/license_keygen.py --key-id prod-2026

已有私钥时默认拒绝覆盖；确需重建才加 --force（会作废旧授权）。
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-id", default="prod-2026")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--password-env", default="LICENSE_KEY_PASSWORD")
    ap.add_argument("--force", action="store_true",
                    help="覆盖已有私钥；会作废旧私钥签发的后续签发能力，慎用")
    args = ap.parse_args()
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else (
        Path.home() / ".sanitized-data-license-keys")
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path = out_dir / "license_signing_ed25519.pem"
    public_path = out_dir / "public_keys.json"
    if (private_path.exists() or public_path.exists()) and not args.force:
        raise SystemExit(
            f"私钥/公钥已存在：{out_dir}\n"
            "为避免覆盖导致无法续签，默认拒绝。确需重建请加 --force（旧授权不会失效，但新授权只能用新 key_id）。")
    password = os.environ.get(args.password_env, "").encode("utf-8")
    if not password:
        p1 = getpass.getpass("设置私钥口令（至少 8 位，建议 24 位以上；不会回显）: ")
        p2 = getpass.getpass("再输入一次: ")
        if p1 != p2:
            raise SystemExit("两次口令不一致")
        password = p1.encode("utf-8")
    if len(password) < 8:
        raise SystemExit(f"口令太短（至少 8 个字符，建议 24 位以上）")
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
    public_path.write_text(json.dumps({args.key_id: public_b64}, indent=2) + "\n",
                           encoding="utf-8")
    fingerprint = hashlib.sha256(base64.b64decode(public_b64)).hexdigest().upper()
    key_info = out_dir / "key_info.txt"
    key_info.write_text(
        f"key_id={args.key_id}\n"
        f"created_utc={datetime.now(timezone.utc).isoformat()}\n"
        f"public_sha256={fingerprint}\n"
        f"private_file={private_path.name}\n"
        "备份要求：私钥与口令分开保存；至少两份离线副本；每 6 个月做一次恢复演练。\n",
        encoding="utf-8")
    print("private:", private_path)
    print("public :", public_path)
    print("info   :", key_info)
    print("key_id :", args.key_id)
    print("pub fp :", fingerprint)
    print("把下面内容合并到未签名 keyring，再用 core 私钥签名：")
    print(json.dumps({args.key_id: public_b64}, indent=2))
    print("  python deploy/sign_keyring.py --core-private-key <离线 core 私钥> \\")
    print("      --root-key-id core-2026 --in <未签名 public_keys.json> \\")
    print("      --out app/server/yinzhan/licensing/public_keys.json")
    print("\n注意：私钥文件请离线备份；丢失/泄露需要重新签发所有 license。")
    print("备份：把加密私钥 + public_keys.json + key_info.txt 复制到离线介质；")
    print("      口令只记在密码管理器/纸质密封，不要和私钥放同一处。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
