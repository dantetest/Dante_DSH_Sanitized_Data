# -*- coding: utf-8 -*-
"""客户端更新：按运行平台选择自己的更新包 → 校验 → 安装/引导手动安装。

设计约束：
- 只在用户点击/启动检查时联网；文件处理引擎不调用本模块。
- 不静默安装：必须由用户在界面上点「立即更新」「安装并重启」。
- 更新包必须 HTTPS/HTTP + SHA-256 校验通过，才允许执行。
- **平台隔离（v1.5.3）**：清单是「按平台分键」的资产表
  （windows_installer / windows_portable / macos_arm64 / macos_x64），
  客户端只读自己平台、自己架构的键，且做扩展名/文件名交叉校验，
  绝不允许 Windows 端拿到 .dmg、macOS 端拿到 .exe/.zip。
- Windows：优先 windows_portable（绿色版 zip）自替换，绕开安装器问题；
  windows_installer 仅保留兼容，作为「手动下载安装」入口，不自动执行。
- macOS：只接受 macos_arm64 / macos_x64 的 .dmg；.app 自替换需要应用外 helper，
  v1.5.3 不做自动替换，只给出 DMG 下载入口并把「拖入应用程序覆盖」写进提示。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

_USER_AGENT = "sanitized-data-updater/1"
_UA_COUNT = 0

# 清单资产键 → (kind, 允许的扩展名, 所属平台)
_ASSET_KINDS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "windows_portable": ("portable", (".zip",), "windows"),
    "windows_installer": ("installer", (".exe",), "windows"),
    "macos_arm64": ("dmg", (".dmg",), "macos"),
    "macos_x64": ("dmg", (".dmg",), "macos"),
}

# 反向黑名单：Windows 端拒绝 macOS/磁盘映像标记；macOS 端拒绝 Windows 安装包标记。
_FORBIDDEN_TOKENS: dict[str, tuple[str, ...]] = {
    "windows": ("macos", "darwin", ".dmg", "apple"),
    "macos": ("windows", "win32", "win64", ".exe", ".zip", "setup"),
}


def _version_tuple(value: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", str(value or ""))
    return tuple(int(x) for x in nums[:4]) or (0,)


def _is_newer(remote: str, current: str) -> bool:
    return _version_tuple(remote) > _version_tuple(current)


def _asset_valid(asset: dict) -> bool:
    try:
        name = str(asset.get("name") or "")
        url = str(asset.get("url") or "")
        size = int(asset.get("size") or 0)
        digest = str(asset.get("sha256") or "").strip().upper()
        return (bool(name) and bool(url) and size > 0
                and re.fullmatch(r"[0-9A-F]{64}", digest) is not None)
    except Exception:
        return False


# ── 平台识别与资产选择（平台隔离的核心） ──
def _platform_id() -> str:
    """当前运行平台：windows / macos / other。"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "other"


def _arch_id() -> str:
    """当前运行架构：arm64 / x64 / 其它 machine 值。"""
    machine = (platform.machine() or "").strip().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64", "x64"):
        return "x64"
    return machine or "unknown"


def _platform_label(platform_id: str, arch: str) -> str:
    if platform_id == "windows":
        return "Windows x64"
    if platform_id == "macos":
        if arch == "arm64":
            return "macOS Apple Silicon（arm64）"
        if arch == "x64":
            return "macOS Intel（x64）"
        return f"macOS（{arch}）"
    return platform_id


def _platform_asset_keys(platform_id: str, arch: str) -> list[tuple[str, str]]:
    """当前平台允许的资产键顺序：(asset_key, mode)。auto=应用内自动替换，manual=手动下载安装。"""
    if platform_id == "windows":
        return [("windows_portable", "auto"), ("windows_installer", "manual")]
    if platform_id == "macos":
        if arch == "arm64":
            return [("macos_arm64", "manual")]
        if arch == "x64":
            return [("macos_x64", "manual")]
        return []
    return []


def _asset_matches(key: str, asset: dict, platform_id: str, arch: str) -> bool:
    """交叉校验：键的平台归属、扩展名、反平台标记、macOS 架构标记。"""
    meta = _ASSET_KINDS.get(key)
    if not meta:
        return False
    _kind, suffixes, expected_platform = meta
    if expected_platform != platform_id:
        return False
    name = str(asset.get("name") or "")
    url = str(asset.get("url") or "")
    if not name.lower().endswith(suffixes):
        return False
    haystack = (name + " " + url).lower()
    for token in _FORBIDDEN_TOKENS[platform_id]:
        if token in haystack:
            return False
    if platform_id == "macos":
        opposite = "x64" if arch == "arm64" else ("arm64" if arch == "x64" else "")
        if opposite and opposite in name.lower():
            return False
    return True


def _select_update_asset(assets: dict, platform_id: str, arch: str):
    """返回 (asset_key, kind, mode)；没有本平台可用资产时返回 None。"""
    for key, mode in _platform_asset_keys(platform_id, arch):
        asset = assets.get(key)
        if not isinstance(asset, dict) or not _asset_valid(asset):
            continue
        if not _asset_matches(key, asset, platform_id, arch):
            continue
        return key, _ASSET_KINDS[key][0], mode
    return None


def _manual_hint(platform_id: str) -> str:
    if platform_id == "macos":
        return ("macOS 版请下载 DMG，打开后把 Sanitized_Data.app 拖入「应用程序」覆盖安装；"
                "本程序不会自动替换 macOS 应用。")
    if platform_id == "windows":
        return "当前清单只有 Windows 安装包，请下载后手动安装（程序不会静默执行安装器）。"
    return "当前平台请手动下载安装包。"


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _http_get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    })
    kwargs = {"timeout": timeout}
    if url.lower().startswith("https://"):
        kwargs["context"] = _ssl_context()
    with urllib.request.urlopen(req, **kwargs) as resp:
        raw = resp.read(2 * 1024 * 1024)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest 不是对象")
    return data


class UpdateManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: dict = {
            "phase": "idle",       # idle|checking|available|downloading|ready|installing|error|no_update
            "available": False,
            "version": "",
            "current_version": config.VERSION,
            "notes": "",
            "asset": None,          # 安全的公开元数据
            "progress": 0,
            "downloaded": 0,
            "total": 0,
            "error": "",
            "checked_at": "",
            "later_until": 0,
            # 平台隔离相关（前端据此渲染文案/按钮）
            "platform": "",         # windows / macos / other
            "arch": "",             # arm64 / x64 / ...
            "platform_label": "",   # 展示用
            "mode": "none",         # auto（应用内替换）/ manual（手动下载安装）/ none
            "manual": False,
            "release_url": "",
        }
        self._download_thread: threading.Thread | None = None

    # ── 状态 ──
    def _set(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)

    def status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._state, ensure_ascii=False))

    # ── 检查 ──
    def check(self) -> dict:
        url = config.UPDATE_MANIFEST_URL
        platform_id = _platform_id()
        arch = _arch_id()
        label = _platform_label(platform_id, arch)
        base = {"platform": platform_id, "arch": arch, "platform_label": label}
        if not url:
            self._set(phase="error", available=False, error="未配置更新源", **base)
            return self.status()
        self._set(phase="checking", error="", **base)
        try:
            manifest = _http_get_json(url, config.UPDATE_TIMEOUT)
            remote = str(manifest.get("version") or "").strip()
            if not remote:
                raise ValueError("manifest 缺少 version")
            assets = manifest.get("assets") or {}
            if not isinstance(assets, dict):
                raise ValueError("manifest assets 格式错误")
            release_url = str(manifest.get("release_url") or "")
            notes = str(manifest.get("notes") or "")
            if not _is_newer(remote, config.VERSION):
                self._set(phase="no_update", available=False, version=remote,
                          notes=notes, asset=None,
                          progress=0, total=0, error="", release_url=release_url,
                          mode="none", manual=False,
                          checked_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), **base)
                return self.status()

            if platform_id == "other":
                raise ValueError(
                    f"当前平台（{sys.platform}）不在自动更新支持范围，请到下载页手动下载。")

            selected = _select_update_asset(assets, platform_id, arch)
            if selected is None:
                # 关键：绝不回退到其它平台的资产（Windows 不拿 dmg，macOS 不拿 exe/zip）。
                raise ValueError(
                    f"更新清单暂未提供适用于 {label} 的安装包，请稍后重试或到下载页手动下载。")

            asset_key, kind, mode = selected
            raw = assets[asset_key]
            asset_state = {
                "name": str(raw.get("name")),
                "size": int(raw.get("size") or 0),
                "sha256": str(raw.get("sha256") or "").upper(),
                "url": str(raw.get("url")),
                "kind": kind,               # portable | installer | dmg
                "asset_key": asset_key,
                "manual": mode == "manual",
            }
            self._set(
                phase="available", available=True, version=remote, notes=notes,
                asset=asset_state,
                progress=0, total=int(raw.get("size") or 0),
                error=(_manual_hint(platform_id) if mode == "manual" else ""),
                release_url=release_url,
                mode=mode, manual=(mode == "manual"),
                checked_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), **base,
            )
            return self.status()
        except Exception as exc:
            self._set(phase="error", available=False, error=f"检查更新失败：{exc}", **base)
            return self.status()

    # ── 下载 ──
    def start_download(self) -> dict:
        with self._lock:
            if self._state.get("phase") not in ("available", "error", "ready"):
                return self.status()
            asset = self._state.get("asset")
            if not asset or asset.get("kind") != "portable":
                self._set(phase="available",
                          error="当前平台/该更新包不支持应用内自动下载，请按提示手动下载安装包。")
                return self.status()
            if self._download_thread and self._download_thread.is_alive():
                return self.status()
            self._set(phase="downloading", progress=0, downloaded=0,
                      total=int(asset.get("size") or 0), error="")
            self._download_thread = threading.Thread(target=self._download, daemon=True)
            self._download_thread.start()
            return self.status()

    def _download_path(self) -> Path:
        version = re.sub(r"[^0-9A-Za-z._-]", "_", str(self._state.get("version") or "update"))
        name = re.sub(r"[^0-9A-Za-z._-]", "_", str((self._state.get("asset") or {}).get("name") or "update.zip"))
        return config.DATA_DIR / "updates" / version / name

    def _download(self) -> None:
        try:
            asset = self._state.get("asset") or {}
            dest = self._download_path()
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_file():
                if dest.stat().st_size == int(asset.get("size") or 0) and \
                        self._sha256(dest) == str(asset.get("sha256") or "").upper():
                    self._set(phase="ready", progress=100,
                              downloaded=dest.stat().st_size, total=dest.stat().st_size)
                    return
            tmp = dest.with_suffix(dest.suffix + ".part")
            req = urllib.request.Request(str(asset.get("url") or ""),
                                         headers={"User-Agent": _USER_AGENT})
            kwargs = {"timeout": 300}
            if str(asset.get("url") or "").lower().startswith("https://"):
                kwargs["context"] = _ssl_context()
            total = 0
            with urllib.request.urlopen(req, **kwargs) as resp, tmp.open("wb") as fh:
                while True:
                    chunk = resp.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
                    total_hint = int(asset.get("size") or 0) or total
                    self._set(downloaded=total, total=total_hint,
                              progress=min(99, int(total * 100 / total_hint)) if total_hint else 0)
            if tmp.stat().st_size != int(asset.get("size") or 0):
                raise ValueError(f"更新包大小不符：{tmp.stat().st_size} != {asset.get('size')}")
            digest = self._sha256(tmp)
            if digest != str(asset.get("sha256") or "").upper():
                raise ValueError("更新包 SHA-256 校验失败")
            tmp.replace(dest)
            self._set(phase="ready", progress=100, downloaded=dest.stat().st_size,
                      total=dest.stat().st_size, asset={**asset, "path": str(dest)})
        except Exception as exc:
            try:
                if 'tmp' in locals() and Path(tmp).exists():
                    Path(tmp).unlink()
            except Exception:
                pass
            self._set(phase="error", error=f"下载更新包失败：{exc}")

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest().upper()

    # ── 安装 ──
    def install(self) -> dict:
        with self._lock:
            if self._state.get("phase") != "ready":
                self._set(error="更新包尚未下载完成")
                return self.status()
            if _platform_id() != "windows":
                # macOS / Linux：不做应用内替换，避免误执行 Windows helper 或破坏 .app。
                self._set(phase="error",
                          error="当前平台不支持自动替换安装，请手动下载并安装更新包。")
                return self.status()
            asset = self._state.get("asset") or {}
            zip_path = Path(str(asset.get("path") or self._download_path()))
            if not zip_path.is_file():
                self._set(phase="error", error="更新包文件不存在，请重新下载")
                return self.status()
            if not getattr(sys, "frozen", False):
                self._set(phase="error", error="源码模式不支持自动替换，请使用打包版")
                return self.status()
            app_dir = Path(sys.executable).resolve().parent
            exe = Path(sys.executable).resolve()
            helper = config.DATA_DIR / "updates" / "apply_update.ps1"
            helper.parent.mkdir(parents=True, exist_ok=True)
            helper.write_text(_HELPER, encoding="utf-8-sig")
            log = config.DATA_DIR / "updates" / "update.log"
            flags = 0
            if os.name == "nt":
                flags = 0x00000200 | 0x08000000  # NEW_GROUP | NO_WINDOW (DETACHED_PROCESS breaks PowerShell helper)
            try:
                subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-WindowStyle", "Hidden", "-File", str(helper),
                     str(os.getpid()), str(zip_path), str(app_dir), str(exe), str(log)],
                    close_fds=True, creationflags=flags,
                )
            except Exception as exc:
                self._set(phase="error", error=f"无法启动更新进程：{exc}")
                return self.status()
            self._set(phase="installing", error="")
            return self.status()


_HELPER = r"""
param(
  [int]$TargetPid,
  [string]$ZipPath,
  [string]$AppDir,
  [string]$ExePath,
  [string]$LogPath
)
$ErrorActionPreference = "Stop"
function Log([string]$Text) {
  try { Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f (Get-Date -Format o), $Text) -Encoding UTF8 } catch {}
}
try {
  Log "wait for app to exit pid=$TargetPid"
  Start-Sleep -Seconds 2
  for ($i = 0; $i -lt 4; $i++) {
    if (-not (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
  }
  try { Stop-Process -Id $TargetPid -Force -ErrorAction SilentlyContinue } catch {}
  Start-Sleep -Seconds 2
  $tmp = Join-Path (Split-Path -Parent $ZipPath) ("extract_" + [guid]::NewGuid().ToString("N"))
  Expand-Archive -LiteralPath $ZipPath -DestinationPath $tmp -Force
  $src = Join-Path $tmp "Sanitized_Data"
  if (-not (Test-Path (Join-Path $src "Sanitized_Data.exe"))) {
    throw "update zip missing Sanitized_Data\Sanitized_Data.exe"
  }
  Log "copy to $AppDir"
  New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
  Copy-Item (Join-Path $src "*") $AppDir -Recurse -Force
  Log "start $ExePath"
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $ExePath
  $psi.WorkingDirectory = $AppDir
  $psi.UseShellExecute = $false
  [System.Diagnostics.Process]::Start($psi) | Out-Null
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
  Log "done"
} catch {
  Log ("ERROR: " + $_.Exception.Message)
  # fallback: restart old app if copy failed
  try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $ExePath
    $psi.WorkingDirectory = $AppDir
    $psi.UseShellExecute = $false
    [System.Diagnostics.Process]::Start($psi) | Out-Null
  } catch {}
}
"""


UPDATE = UpdateManager()
