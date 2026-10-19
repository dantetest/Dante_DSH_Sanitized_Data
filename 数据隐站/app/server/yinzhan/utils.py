"""零碎工具：哈希、文件名、时间。"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

CN_TZ = timezone(timedelta(hours=8))

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def now() -> datetime:
    return datetime.now(CN_TZ)


def iso_now() -> str:
    return now().isoformat(timespec="seconds")


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str, max_len: int = 80) -> str:
    """清洗成安全的文件名（保留中英文数字）。"""
    name = unicodedata.normalize("NFC", name or "file")
    name = _BAD.sub("_", name).strip(" .")
    if not name:
        name = "file"
    stem, ext = os.path.splitext(name)
    stem = stem[:max_len]
    return stem + ext


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 1000):
        cand = path.with_name(f"{stem}({i}){suffix}")
        if not cand.exists():
            return cand
    return path.with_name(f"{stem}_{os.getpid()}{suffix}")


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
