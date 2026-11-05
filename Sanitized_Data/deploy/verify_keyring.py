#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建/部署门禁：验证 public_keys.json 是由 core 公钥签名的合法 keyring。

不读取 core 私钥；只使用 keys.py 中硬编码的 core 公钥。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_SERVER = ROOT / "app" / "server"
if str(APP_SERVER) not in sys.path:
    sys.path.insert(0, str(APP_SERVER))

from yinzhan.licensing import keys  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="验证签名 public_keys.json")
    ap.add_argument("--manifest", default=str(
        ROOT / "app" / "server" / "yinzhan" / "licensing" / "public_keys.json"))
    ap.add_argument("--require-key", action="append", default=[])
    ap.add_argument("--allow-test", action="store_true")
    args = ap.parse_args()

    path = Path(args.manifest)
    ok, error = keys.verify_keyring_file(path)
    if not ok:
        print(f"FAIL keyring: {path} ({error})", file=sys.stderr)
        return 1
    # verify_keyring_file 只恢复全局状态，不返回 keys；这里重新读一次做内容检查。
    raw = json.loads(path.read_text(encoding="utf-8"))
    key_ids = sorted((raw.get("keys") or {}).keys())
    if not args.allow_test:
        bad = [k for k in key_ids if str(k).startswith("test")]
        if bad:
            print(f"FAIL keyring 含测试公钥：{bad}", file=sys.stderr)
            return 1
    required = args.require_key or ["prod-2026", "prod-2026b"]
    missing = [k for k in required if k not in key_ids]
    if missing:
        print(f"FAIL keyring 缺少必需公钥：{missing}", file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True,
        "manifest": str(path),
        "root_key_id": raw.get("root_key_id"),
        "keyring_revision": raw.get("keyring_revision"),
        "keys": key_ids,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
