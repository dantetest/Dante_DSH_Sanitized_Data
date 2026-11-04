#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成支付宝「电脑网站支付」接入用的 RSA2 2048 应用密钥对。

用法（在 Sanitized_Data 目录）：
    python commercial\\scripts\\gen_alipay_keys.py
    python commercial\\scripts\\gen_alipay_keys.py --out-dir commercial/var/keys --force

产物：
    alipay_app_private.pem   应用私钥（只有本机/商业服务器保存，绝不外发/上传）
    alipay_app_public.pem    应用公钥（复制到开放平台「开发设置 → 接口加签方式 → 公钥模式」）
    alipay_public_key.txt    说明与路径（不是支付宝公钥！）

重要：
- 开放平台上传的是「应用公钥」，上传后会得到「支付宝公钥」；
  商业服务配置的 ALIPAY_PUBLIC_KEY_PATH 要放**支付宝公钥**，两把公钥别搞混。
- 私钥不要发聊天、不要提交 git、不要放进安装包；文件权限尽量只给运行服务的账号。
- 默认生成无口令 PKCS8 PEM；如需口令加密，设置环境变量后加 --password-env 指定变量名：
      set ALIPAY_KEY_PASSWORD=你的口令
      python commercial\\scripts\\gen_alipay_keys.py --password-env ALIPAY_KEY_PASSWORD
  此时商业服务还要配置 ALIPAY_APP_PRIVATE_KEY_PASSWORD=你的口令（或用环境变量同名）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"需要 cryptography：{exc}")


def _reconfigure() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _reconfigure()
    ap = argparse.ArgumentParser(description="生成支付宝 RSA2(2048) 应用密钥对")
    ap.add_argument("--out-dir", default=str(Path("commercial") / "var" / "keys"),
                    help="输出目录（默认 commercial/var/keys，已 gitignore）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的密钥文件")
    ap.add_argument("--password-env", default="",
                    help="可选：用该环境变量的值给应用私钥加口令（不推荐把口令写进命令行）")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()
    private_path = out_dir / "alipay_app_private.pem"
    public_path = out_dir / "alipay_app_public.pem"
    note_path = out_dir / "README-密钥说明.txt"

    if not args.force and (private_path.exists() or public_path.exists()):
        raise SystemExit(f"密钥文件已存在：{private_path} / {public_path}；确需重新生成请加 --force "
                         f"（旧密钥上传的支付宝公钥会失效）")

    password = b""
    if args.password_env:
        raw = os.environ.get(args.password_env, "")
        if not raw:
            raise SystemExit(f"环境变量 {args.password_env} 为空；未生成，避免误创建无口令私钥")
        password = raw.encode("utf-8")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if password:
        enc = serialization.BestAvailableEncryption(password)
    else:
        enc = serialization.NoEncryption()
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=enc,
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    try:
        os.chmod(private_path, 0o600)
    except Exception:
        pass
    note_path.write_text(
        "支付宝接入密钥说明\n"
        "==================\n"
        f"应用私钥（保密，只放商业服务机器）：{private_path}\n"
        f"应用公钥（上传开放平台）：{public_path}\n\n"
        "1) 打开 https://open.alipay.com → 控制台 → 应用 → 你的网页应用\n"
        "2) 开发设置 → 接口加签方式 → 公钥模式 → 粘贴下面『应用公钥』→ 保存\n"
        "3) 保存后页面展示『支付宝公钥』——复制它，另存为 alipay_public.pem，\n"
        "   配置到商业服务 ALIPAY_PUBLIC_KEY_PATH（不是本文件里的应用公钥！）\n"
        "4) APPID 在应用详情页顶部；配置 ALIPAY_APP_ID\n"
        "5) 千万不要把应用私钥发给任何人/提交 git/放进安装包\n",
        encoding="utf-8",
    )

    print("=" * 72)
    print("支付宝 RSA2 应用密钥已生成")
    print(f"  应用私钥（保密）: {private_path}")
    print(f"  应用公钥（上传）: {public_path}")
    print(f"  说明文件        : {note_path}")
    print()
    print("把下面【应用公钥】粘贴到开放平台：")
    print("  应用详情 → 开发设置 → 接口加签方式 → 公钥模式")
    print("-" * 72)
    print(public_pem.decode("ascii").strip())
    print("-" * 72)
    print("保存后，页面展示的『支付宝公钥』才是商业服务要配置的 ALIPAY_PUBLIC_KEY_PATH。")
    print("应用私钥请勿外发；生产环境建议只允许运行商业服务的账号读取。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
