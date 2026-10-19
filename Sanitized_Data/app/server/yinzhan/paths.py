"""路径解析：同时支持「源码运行」「PyInstaller 冻结」「桌面版」三种形态。

规则：
  资源（web/、规则包、OCR 模型）—— 只读，跟着程序走
  数据（上传副本、映射库、审计库、导出成品）—— 可写，放用户目录
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME_EN = "Sanitized_Data"

# PyInstaller 打包后 sys.frozen 为 True，资源在 sys._MEIPASS
FROZEN = bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根目录（app/ 这一层）。"""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]        # app/


def web_dir() -> Path:
    return resource_root() / "web"


def _user_data_base() -> Path:
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(root)


def is_desktop() -> bool:
    """桌面版：打包运行，或显式设了 YINZHAN_DESKTOP=1。"""
    return FROZEN or os.environ.get("YINZHAN_DESKTOP") == "1"


def _writable(path: Path) -> bool:
    """能不能真的在里头建目录和写文件。有些受限机器会拒 AppData 写入。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / "._write_test"
        probe.write_text("x", "utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


_DATA_CACHE: Path | None = None


def data_root() -> Path:
    """可写数据根目录，自动挑第一个真的能写的位置。

    优先级：YINZHAN_DATA_DIR > 用户目录 > 程序目录 > 临时目录
    （后两者是兜底：企业受限机器上 AppData 可能不可写）
    """
    global _DATA_CACHE
    if _DATA_CACHE is not None:
        return _DATA_CACHE

    env = os.environ.get("YINZHAN_DATA_DIR")
    if env:
        _DATA_CACHE = Path(env).expanduser().resolve()
        return _DATA_CACHE

    if is_desktop():
        candidates = [
            _user_data_base() / APP_NAME_EN,
            resource_root().parent / "data",
            Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / APP_NAME_EN,
        ]
    else:
        candidates = [Path(__file__).resolve().parents[3] / "data"]

    for c in candidates:
        if _writable(c):
            _DATA_CACHE = c
            return c
    _DATA_CACHE = candidates[-1]
    return _DATA_CACHE


def output_root() -> Path:
    env = os.environ.get("YINZHAN_OUTPUT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # 跟随数据目录：数据放哪，成品就放哪
    root = data_root() / "out"
    if _writable(root):
        return root
    return Path(os.environ.get("TEMP") or ".") / APP_NAME_EN / "out"
