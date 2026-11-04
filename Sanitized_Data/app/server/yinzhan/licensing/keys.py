# -*- coding: utf-8 -*-
"""license 公钥注册表。

生产公钥通过同目录 ``public_keys.json`` 分发（只含公钥，可进安装包）；
私钥只在签发服务，绝不进仓库/安装包/CI。

测试/私有化部署可用环境变量 ``YINZHAN_PUBLIC_KEYS_PATH`` 指定一个同格式
JSON（``{"key_id": "<base64 raw Ed25519 公钥>"}``），在默认公钥之后合并/覆盖。
不设置该变量时行为与旧版完全一致。
⚠ 仅测试/私有化使用：正式发布包不要设置，避免把不受信公钥带进生产。
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

PUBLIC_KEYS: dict[str, bytes] = {}

# 仅测试/私有化：覆盖/合并公钥的 JSON 路径（格式同 public_keys.json）。
PUBLIC_KEYS_PATH_ENV = "YINZHAN_PUBLIC_KEYS_PATH"


def _load_json_keys(path: Path) -> None:
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


def _load_default() -> None:
    _load_json_keys(Path(__file__).with_name("public_keys.json"))


def _load_override() -> None:
    """仅测试/私有化：环境变量指定 JSON 时合并/覆盖默认公钥。

    路径未设置、文件不存在或格式不对都静默跳过（默认公钥保持不变），
    避免因为一个可选环境变量让授权模块在启动时抛错。
    """
    raw_path = (os.environ.get(PUBLIC_KEYS_PATH_ENV) or "").strip()
    if not raw_path:
        return
    try:
        path = Path(raw_path).expanduser()
    except Exception:
        return
    _load_json_keys(path)


_load_default()
_load_override()


def get_public_key(key_id: str) -> bytes | None:
    return PUBLIC_KEYS.get(str(key_id))


def add_public_key(key_id: str, raw: bytes) -> None:
    """测试/部署辅助；不要用它把私钥或不受信公钥写进生产包。"""
    PUBLIC_KEYS[str(key_id)] = bytes(raw)
