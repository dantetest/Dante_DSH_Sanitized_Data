# -*- coding: utf-8 -*-
"""离线机器码（machine code）。

只读取本机已有的稳定标识，不联网、不上传原始硬件序列号。
输出 Crockford Base32 的 16 字符短码，例如 ``K7Q2-9WXM-4TRB-EFGH``。
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import re
import subprocess
import uuid
from pathlib import Path

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""


def _run(cmd: list[str], timeout: float = 3.0) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def _windows_components() -> tuple[str, str]:
    guid = ""
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as key:
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
    except Exception:
        guid = ""

    serial = ""
    try:
        drive = (os.environ.get("SystemDrive") or "C:") + "\\"
        volume = ctypes.c_buffer(261)
        fs_name = ctypes.c_buffer(261)
        serial_num = ctypes.c_ulong()
        max_len = ctypes.c_ulong(0)
        flags = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive), volume, len(volume), ctypes.byref(serial_num),
            ctypes.byref(max_len), ctypes.byref(flags), fs_name, len(fs_name))
        if ok:
            serial = f"{int(serial_num.value):08X}"
    except Exception:
        serial = ""

    if not guid or not serial:
        # 兜底：主机名 + 网卡 MAC（不单独使用主机名）
        fallback = f"{platform.node()}|{uuid.getnode():012X}"
        guid = guid or fallback
        serial = serial or fallback
    return str(guid), str(serial)


def _mac_components() -> tuple[str, str]:
    out = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
    m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
    platform_uuid = m.group(1) if m else ""
    disk_uuid = ""
    info = _run(["diskutil", "info", "/"])
    m = re.search(r"Volume UUID:\s*([0-9A-Fa-f-]+)", info)
    if m:
        disk_uuid = m.group(1)
    if not platform_uuid or not disk_uuid:
        fallback = f"{platform.node()}|{uuid.getnode():012X}"
        platform_uuid = platform_uuid or fallback
        disk_uuid = disk_uuid or fallback
    return platform_uuid, disk_uuid


def _linux_components() -> tuple[str, str]:
    machine_id = _read_text("/etc/machine-id") or _read_text("/var/lib/dbus/machine-id")
    product_uuid = _read_text("/sys/class/dmi/id/product_uuid")
    if not machine_id or not product_uuid:
        fallback = f"{platform.node()}|{uuid.getnode():012X}"
        machine_id = machine_id or fallback
        product_uuid = product_uuid or fallback
    return machine_id, product_uuid


def _components_for_platform(system: str | None = None) -> tuple[str, str, str]:
    """返回 (component_a, component_b, source)；测试可 monkeypatch。"""
    system = (system or platform.system()).lower()
    if system == "windows":
        a, b = _windows_components()
        return a, b, "windows-machineguid+volume-serial"
    if system == "darwin":
        a, b = _mac_components()
        return a, b, "macos-platformuuid+volume-uuid"
    a, b = _linux_components()
    return a, b, "linux-machine-id+product-uuid"


def _crockford_base32(data: bytes, length: int = 16) -> str:
    value = int.from_bytes(data, "big")
    bits = len(data) * 8
    chars: list[str] = []
    for i in range(length):
        shift = bits - 5 * (i + 1)
        idx = (value >> shift) & 0x1F if shift >= 0 else 0
        chars.append(_ALPHABET[idx])
    return "".join(chars)


def _display(code: str) -> str:
    code = code.upper()
    return "-".join(code[i:i + 4] for i in range(0, 16, 4))


def machine_info(system: str | None = None) -> dict:
    """返回 ``{code, display, source}``；code 为无连字符 16 字符。"""
    try:
        a, b, source = _components_for_platform(system)
    except Exception:
        a = b = f"{platform.node()}|{uuid.getnode():012X}"
        source = "fallback-hostname+mac"
    raw = "machine-v1|" + str(a) + "|" + str(b)
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    code = _crockford_base32(digest[:10], 16)
    return {"code": code, "display": _display(code), "source": source}


def machine_code(system: str | None = None) -> str:
    return machine_info(system)["code"]


def machine_display(system: str | None = None) -> str:
    return machine_info(system)["display"]
