# -*- coding: utf-8 -*-
"""license 信任根（core key + 签名 keyring）。

信任链：
    .exe 内硬编码 CORE_PUBLIC_KEYS
        ↓ 验证
    public_keys.json（core 私钥签名的 envelope）
        ↓ 取出
    license 公钥（prod-2026 / prod-2026b ...）
        ↓ 验证
    license.json

只有 envelope 验签通过后，PUBLIC_KEYS 才会被填充；任何失败都 fail closed。
生产包（PyInstaller/PyWebView 冻结态）不接受任何运行时公钥覆盖；
源码模式仅允许显式测试覆盖，便于 e2e 与单测。
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

try:  # 源码/冻结态判断；单独加载本模块的测试也能工作
    from .. import paths as _paths
except Exception:  # pragma: no cover - 防御性
    _paths = None

KEYRING_SCHEMA = 2

#: 核心公钥（只有公钥；core 私钥只离线保存，绝不进仓库/安装包/CI）
#: 轮换时在这里追加新的 core key，旧 key 保留到旧 keyring 不再需要。
CORE_PUBLIC_KEYS: dict[str, str] = {
    "core-2026": "dhfTSmtT73EZ2pKTlEY3Qko1hStmRXlBu10j6n7zjWM=",
}

#: 运行时测试注入（仅源码模式；冻结包忽略）
TEST_KEYRING_PATH_ENV = "YINZHAN_TEST_KEYRING_PATH"
TEST_CORE_PUBLIC_KEY_ENV = "YINZHAN_TEST_CORE_PUBLIC_KEY"

PUBLIC_KEYS: dict[str, bytes] = {}
KEYRING_OK = False
KEYRING_ERROR: str | None = None
KEYRING_PATH = Path(__file__).with_name("public_keys.json")


def _is_frozen() -> bool:
    if _paths is not None:
        return bool(_paths.FROZEN)
    return bool(getattr(sys, "frozen", False))


def canonical_keyring_payload(payload: dict[str, Any]) -> bytes:
    """keyring 签名/验签共用的规范 JSON（不包含 sig）。"""
    body = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _b64_key(value: Any) -> bytes:
    raw = base64.b64decode(str(value), validate=True)
    if len(raw) != 32:
        raise ValueError("public key must be 32 bytes")
    return raw


def _fail(reason: str) -> None:
    global KEYRING_OK, KEYRING_ERROR
    PUBLIC_KEYS.clear()
    KEYRING_OK = False
    KEYRING_ERROR = reason


def load_keyring(path: Path | str | None = None,
                 core_public_keys: dict[str, str] | None = None) -> bool:
    """加载并验证签名 keyring；成功才填充 PUBLIC_KEYS。

    失败不会抛到调用方（避免启动崩溃），而是清空公钥并记录 KEYRING_ERROR。
    """
    global KEYRING_OK, KEYRING_ERROR
    keyring_path = Path(path) if path is not None else KEYRING_PATH
    roots = core_public_keys if core_public_keys is not None else CORE_PUBLIC_KEYS
    try:
        raw_text = keyring_path.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
        if not isinstance(raw, dict) or raw.get("schema") != KEYRING_SCHEMA:
            raise ValueError("unsupported keyring schema")
        root_key_id = str(raw.get("root_key_id") or "")
        root_b64 = roots.get(root_key_id)
        if not root_b64:
            raise ValueError("unknown core key")
        signature = str(raw.get("sig") or "")
        if not signature:
            raise ValueError("missing keyring signature")
        core = Ed25519PublicKey.from_public_bytes(_b64_key(root_b64))
        core.verify(base64.b64decode(signature, validate=True),
                    canonical_keyring_payload(raw))
        raw_keys = raw.get("keys")
        if not isinstance(raw_keys, dict) or not raw_keys:
            raise ValueError("empty keyring")
        decoded: dict[str, bytes] = {}
        for key_id, value in raw_keys.items():
            decoded[str(key_id)] = _b64_key(value)
    except Exception as exc:  # noqa: BLE001 - fail closed，具体原因不展示给用户
        _fail(f"{type(exc).__name__}: {exc}")
        return False
    PUBLIC_KEYS.clear()
    PUBLIC_KEYS.update(decoded)
    KEYRING_OK = True
    KEYRING_ERROR = None
    return True


def verify_keyring_file(path: Path | str,
                        core_public_keys: dict[str, str] | None = None) -> tuple[bool, str | None]:
    """只校验文件，不修改全局 PUBLIC_KEYS；供构建门禁使用。"""
    roots = core_public_keys if core_public_keys is not None else CORE_PUBLIC_KEYS
    old_keys = dict(PUBLIC_KEYS)
    old_ok = KEYRING_OK
    old_error = KEYRING_ERROR
    try:
        ok = load_keyring(path, roots)
        return ok, KEYRING_ERROR
    finally:
        PUBLIC_KEYS.clear()
        PUBLIC_KEYS.update(old_keys)
        globals()["KEYRING_OK"] = old_ok
        globals()["KEYRING_ERROR"] = old_error


def _load_test_override() -> None:
    """仅源码模式：e2e 可用签名 test keyring + test core public key。"""
    if _is_frozen():
        return
    raw_path = (os.environ.get(TEST_KEYRING_PATH_ENV) or "").strip()
    raw_core = (os.environ.get(TEST_CORE_PUBLIC_KEY_ENV) or "").strip()
    if not raw_path or not raw_core:
        return
    load_keyring(Path(raw_path).expanduser(), {"test-core": raw_core})


def get_public_key(key_id: str) -> bytes | None:
    return PUBLIC_KEYS.get(str(key_id))


def add_public_key(key_id: str, raw: bytes) -> None:
    """仅源码测试使用；生产冻结包禁止运行时追加公钥。"""
    if _is_frozen():
        raise RuntimeError("运行时公钥注入在正式包中禁用")
    PUBLIC_KEYS[str(key_id)] = bytes(raw)


def install_test_keyring(path: Path | str,
                         core_public_keys: dict[str, str]) -> bool:
    """仅源码测试使用：加载测试签名 keyring。"""
    if _is_frozen():
        raise RuntimeError("测试 keyring 注入在正式包中禁用")
    return load_keyring(path, core_public_keys)


# 先加载生产签名 keyring；源码测试模式可再覆盖。
load_keyring(KEYRING_PATH, CORE_PUBLIC_KEYS)
_load_test_override()
