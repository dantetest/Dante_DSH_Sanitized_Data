# -*- coding: utf-8 -*-
"""客户端更新：检查 manifest → 下载绿色版更新包 → 校验 → 重启替换 → 再启动。

设计约束：
- 只在用户点击/启动检查时联网；文件处理引擎不调用本模块。
- 不静默安装：必须由用户在界面上点「立即更新」「安装并重启」。
- 更新包必须 HTTPS/HTTP + SHA-256 校验通过，才允许执行。
- 当前优先使用 windows_portable（绿色版 zip）自替换，绕开安装器问题；
  windows_installer 仅保留兼容，暂不自动执行。
"""
from __future__ import annotations

import hashlib
import json
import os
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
        if not url:
            self._set(phase="error", available=False, error="未配置更新源")
            return self.status()
        self._set(phase="checking", error="")
        try:
            manifest = _http_get_json(url, config.UPDATE_TIMEOUT)
            remote = str(manifest.get("version") or "").strip()
            if not remote:
                raise ValueError("manifest 缺少 version")
            assets = manifest.get("assets") or {}
            if not isinstance(assets, dict):
                raise ValueError("manifest assets 格式错误")
            if not _is_newer(remote, config.VERSION):
                self._set(phase="no_update", available=False, version=remote,
                          notes=str(manifest.get("notes") or ""), asset=None,
                          progress=0, total=0, error="",
                          checked_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                return self.status()
            # 当前优先绿色版自替换；manifest 里 installer 只做展示/备用
            asset = assets.get("windows_portable")
            if not _asset_valid(asset or {}):
                # 兼容未来只有 installer 的清单：仍提示，但明确不能自更新
                inst = assets.get("windows_installer")
                if _is_newer(remote, config.VERSION) and _asset_valid(inst or {}):
                    self._set(phase="available", available=True, version=remote,
                              notes=str(manifest.get("notes") or ""),
                              asset={"name": inst.get("name"), "size": int(inst.get("size") or 0),
                                     "sha256": str(inst.get("sha256") or "").upper(),
                                     "url": str(inst.get("url") or ""),
                                     "kind": "installer"},
                              progress=0, total=int(inst.get("size") or 0),
                              error="当前清单没有绿色版更新包，暂不能一键更新；请下载安装包手动升级。",
                              checked_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                    return self.status()
                raise ValueError("manifest 缺少可用的 windows_portable 资产")
            self._set(phase="available", available=True, version=remote,
                      notes=str(manifest.get("notes") or ""),
                      asset={"name": asset.get("name"),
                             "size": int(asset.get("size") or 0),
                             "sha256": str(asset.get("sha256") or "").upper(),
                             "url": str(asset.get("url") or ""),
                             "kind": "portable"},
                      progress=0, total=int(asset.get("size") or 0), error="",
                      checked_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            return self.status()
        except Exception as exc:
            self._set(phase="error", available=False, error=f"检查更新失败：{exc}")
            return self.status()

    # ── 下载 ──
    def start_download(self) -> dict:
        with self._lock:
            if self._state.get("phase") not in ("available", "error", "ready"):
                return self.status()
            asset = self._state.get("asset")
            if not asset or not asset.get("kind") == "portable":
                self._set(phase="error", error="没有可下载的绿色版更新包")
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
