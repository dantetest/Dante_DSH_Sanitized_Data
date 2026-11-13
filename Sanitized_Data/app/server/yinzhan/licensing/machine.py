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




# ── v2：稳定硬件指纹（不依赖 OS 安装态 / 卷序列号） ─────────────────

_PLACEHOLDERS = {
    "", "none", "unknown", "n/a", "na", "not applicable", "default string",
    "to be filled by o.e.m.", "to be filled by oem", "system serial number",
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
    "03000200-0400-0500-0006-000700080009",
}


def _clean_component(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    low = text.lower()
    if low in _PLACEHOLDERS:
        return ""
    if set(low) <= {"0", "-"}:
        return ""
    return text


def _powershell(script: str) -> str:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                timeout=8.0)


def _windows_stable_components() -> tuple[str, str]:
    uuid = ""
    board = ""
    cpu = ""
    try:
        out = _powershell(
            "$p=Get-CimInstance Win32_ComputerSystemProduct;"
            "$b=Get-CimInstance Win32_BaseBoard;"
            "$c=Get-CimInstance Win32_Processor | Select-Object -First 1;"
            "'{0}`n{1}`n{2}' -f $p.UUID,$b.SerialNumber,$c.ProcessorId")
        parts = [x.strip() for x in out.splitlines()]
        if len(parts) >= 3:
            uuid, board, cpu = parts[0], parts[1], parts[2]
    except Exception:
        pass
    if not uuid:
        try:
            out = _run(["wmic", "csproduct", "get", "uuid"], timeout=5.0)
            lines = [x.strip() for x in out.splitlines() if x.strip()]
            if len(lines) >= 2:
                uuid = lines[1]
        except Exception:
            pass
    uuid = _clean_component(uuid)
    board = _clean_component(board)
    cpu = _clean_component(cpu)
    parts = [x for x in (uuid, board, cpu) if x]
    return "|".join(parts), "windows-smbios+baseboard+cpu"


def _mac_stable_components() -> tuple[str, str]:
    out = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
    platform_uuid = ""
    serial = ""
    m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
    if m:
        platform_uuid = m.group(1)
    m = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', out)
    if m:
        serial = m.group(1)
    parts = [x for x in (_clean_component(platform_uuid), _clean_component(serial)) if x]
    return "|".join(parts), "macos-platform-uuid+hardware-serial"


def _linux_stable_components() -> tuple[str, str]:
    product_uuid = _read_text("/sys/class/dmi/id/product_uuid")
    product_serial = _read_text("/sys/class/dmi/id/product_serial")
    parts = [x for x in (_clean_component(product_uuid), _clean_component(product_serial)) if x]
    return "|".join(parts), "linux-dmi-product-uuid+serial"


def _stable_components_for_platform(system: str | None = None) -> tuple[str, str]:
    system = (system or platform.system()).lower()
    if system == "windows":
        a, source = _windows_stable_components()
    elif system == "darwin":
        a, source = _mac_stable_components()
    else:
        a, source = _linux_stable_components()
    if not a:
        # 硬件信息不可得：退回旧指纹，至少保证可用；source 标记便于诊断。
        la, lb, legacy_source = _components_for_platform(system)
        return f"{la}|{lb}", f"fallback-{legacy_source}"
    return a, source


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
    """返回 {code, display, source, legacy_code, legacy_display, codes}。

    code        = v2 稳定硬件指纹（新签发使用）
    legacy_code = v1 旧指纹（仅用于兼容旧 license）
    codes       = 当前可接受的机器码列表
    """
    try:
        stable, source = _stable_components_for_platform(system)
    except Exception:
        stable = f"{platform.node()}|{uuid.getnode():012X}"
        source = "fallback-hostname+mac"
    try:
        la, lb, legacy_source = _components_for_platform(system)
    except Exception:
        la = lb = f"{platform.node()}|{uuid.getnode():012X}"
        legacy_source = "fallback-hostname+mac"

    def _code(raw: str) -> str:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return _crockford_base32(digest[:10], 16)

    code = _code("machine-v2|" + str(stable))
    legacy_code = _code("machine-v1|" + str(la) + "|" + str(lb))
    codes = [code] + ([legacy_code] if legacy_code != code else [])
    return {"code": code, "display": _display(code), "source": source,
            "legacy_code": legacy_code, "legacy_display": _display(legacy_code),
            "legacy_source": legacy_source, "codes": codes}


def machine_code(system: str | None = None) -> str:
    return machine_info(system)["code"]


def machine_display(system: str | None = None) -> str:
    return machine_info(system)["display"]
