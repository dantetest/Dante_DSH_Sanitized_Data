"""加密映射库。

可逆导出（给 AI 读）必须留下「假值 -> 原值」的映射，否则模型回话里的假名字没法还原。
映射只在用户主动选择可逆导出时写入，用 AES-GCM 加密落盘；
涂黑（给人看）不产生任何映射。
"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import DATA_DIR, KEYS_DIR
from .utils import iso_now

VAULT_DIR = DATA_DIR / "vault"
VAULT_DIR.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()
_KEY: bytes | None = None


def _key() -> bytes:
    global _KEY
    if _KEY is not None:
        return _KEY
    path = KEYS_DIR / "master.key"
    with _LOCK:
        if _KEY is not None:
            return _KEY
        if path.exists():
            _KEY = path.read_bytes()
        else:
            _KEY = os.urandom(32)
            path.write_bytes(_KEY)
            try:  # 尽量收紧权限（Windows 上依赖 ACL，失败不致命）
                os.chmod(path, 0o600)
            except Exception:
                pass
        return _KEY


def _derive(purpose: bytes) -> bytes:
    import hashlib
    return hashlib.sha256(_key() + purpose).digest()


def save_mapping(job_id: str, session_map: dict, meta: dict | None = None) -> str:
    """session_map: {etype: {原值: 假值}}"""
    payload = {
        "job_id": job_id,
        "saved_at": iso_now(),
        "meta": meta or {},
        "map": session_map,
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    nonce = os.urandom(12)
    ct = AESGCM(_derive(b"vault")).encrypt(nonce, raw, job_id.encode("utf-8"))
    blob = b"YZ1" + nonce + ct
    path = VAULT_DIR / f"{job_id}.bin"
    path.write_bytes(blob)
    return str(path)


def load_mapping(job_id: str) -> dict | None:
    path = VAULT_DIR / f"{job_id}.bin"
    if not path.exists():
        return None
    blob = path.read_bytes()
    if not blob.startswith(b"YZ1"):
        return None
    nonce, ct = blob[3:15], blob[15:]
    raw = AESGCM(_derive(b"vault")).decrypt(nonce, ct, job_id.encode("utf-8"))
    return json.loads(raw.decode("utf-8"))


def rehydrate(text: str, mapping: dict) -> str:
    """把假值换回原值。长字符串优先，避免子串误伤。"""
    pairs: list[tuple[str, str]] = []
    for etype, table in (mapping.get("map") or {}).items():
        for original, fake in table.items():
            if fake:
                pairs.append((fake, original))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    out = text
    for fake, original in pairs:
        out = out.replace(fake, original)
    return out


def wipe(job_id: str) -> bool:
    path = VAULT_DIR / f"{job_id}.bin"
    if path.exists():
        try:
            size = path.stat().st_size
            with open(path, "r+b") as f:
                f.write(os.urandom(size))
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            pass
        path.unlink(missing_ok=True)
        return True
    return False


def list_vault() -> list[dict]:
    out = []
    for p in sorted(VAULT_DIR.glob("*.bin")):
        out.append({"job_id": p.stem, "size": p.stat().st_size, "mtime": p.stat().st_mtime})
    return out


def key_fingerprint() -> str:
    return base64.b16encode(_derive(b"vault")[:6]).decode()
