#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商业服务部署前检查：只检查配置/密钥文件，不发支付、不打印密钥内容。

用法（在 Sanitized_Data 目录）：
    python commercial/scripts/check_deploy.py
    python commercial/scripts/check_deploy.py --env-file commercial/.env
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

COMMERCIAL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = COMMERCIAL_DIR.parent
for path in (str(COMMERCIAL_DIR), str(PROJECT_ROOT / "app" / "server")):
    if path not in sys.path:
        sys.path.insert(0, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查商业服务密钥与价格配置")
    parser.add_argument("--env-file", default=None, help="默认 commercial/.env")
    args = parser.parse_args(argv)

    from server.config import Config

    cfg = Config.load(env_path=args.env_file)
    errors: list[str] = []
    checks: list[dict] = []

    def check(name: str, path_value: str, loader=None) -> None:
        path = Path(str(path_value or "")).expanduser()
        item = {"name": name, "path": str(path), "exists": path.is_file()}
        checks.append(item)
        if not path.is_file():
            errors.append(f"{name}: 文件不存在：{path}")
            return
        if loader is not None:
            try:
                loader(path)
                item["parse"] = "ok"
            except Exception as exc:  # noqa: BLE001
                item["parse"] = "failed"
                errors.append(f"{name}: 解析失败：{type(exc).__name__}: {exc}")

    if str(cfg.PAYMENT_PROVIDER).lower() == "alipay":
        from server.payments.alipay import load_private_key, load_public_key

        check("支付宝应用私钥", getattr(cfg, "ALIPAY_APP_PRIVATE_KEY_PATH", ""), load_private_key)
        check("支付宝公钥", getattr(cfg, "ALIPAY_PUBLIC_KEY_PATH", ""), load_public_key)
    else:
        checks.append({"name": "支付通道", "path": str(cfg.PAYMENT_PROVIDER),
                       "exists": True, "parse": "not-alipay"})

    from server.signer import LicenseSigner

    signer = LicenseSigner(
        getattr(cfg, "LICENSE_PRIVATE_KEY_PATH", ""),
        key_id=getattr(cfg, "LICENSE_KEY_ID", "prod-2026"),
        password_env=getattr(cfg, "LICENSE_KEY_PASSWORD_ENV", "LICENSE_KEY_PASSWORD"),
    )
    info = signer.describe()
    checks.append({"name": "授权签名私钥", "path": str(signer.private_key_path),
                   "exists": bool(info.get("private_key_configured")),
                   "parse": "ok" if info.get("loaded") else "failed",
                   "error": (info.get("error") or {}).get("message")})
    if not info.get("private_key_configured"):
        errors.append(f"授权签名私钥: 文件不存在：{signer.private_key_path}")
    elif not info.get("loaded"):
        errors.append("授权签名私钥: 无法加载（通常是 LICENSE_KEY_PASSWORD 未注入或口令错误）")

    print(json.dumps({
        "provider": cfg.PAYMENT_PROVIDER,
        "base_url": cfg.COMMERCIAL_BASE_URL,
        "prices_cents": {
            "personal": cfg.COMMERCIAL_PRICE_PERSONAL_CENTS,
            "personal_renewal": cfg.COMMERCIAL_PRICE_PERSONAL_RENEWAL_CENTS,
            "personal_perpetual": cfg.COMMERCIAL_PRICE_PERSONAL_PERPETUAL_CENTS,
        },
        "checks": checks,
        "errors": errors,
        "ok": not errors,
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
