# -*- coding: utf-8 -*-
"""license 公钥注册表。

生产公钥通过同目录 ``public_keys.json`` 分发（只含公钥，可进安装包）；
私钥只在签发服务，绝不进仓库/安装包/CI。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

PUBLIC_KEYS: dict[str, bytes] = {}


def _load_default() -> None:
    path = Path(__file__).with_name("public_keys.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    for key_id, value in raw.items():
        try:
            PUBLIC_KEYS[str(key_id)] = base64.b64decode(str(value), validate=True)
        except Exception:
            continue


_load_default()


def get_public_key(key_id: str) -> bytes | None:
    return PUBLIC_KEYS.get(str(key_id))


def add_public_key(key_id: str, raw: bytes) -> None:
    """测试/部署辅助；不要用它把私钥或不受信公钥写进生产包。"""
    PUBLIC_KEYS[str(key_id)] = bytes(raw)
