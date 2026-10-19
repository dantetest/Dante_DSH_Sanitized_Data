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


def data_root() -> Path:
    """可写数据根目录。

    优先级：YINZHAN_DATA_DIR > 桌面版用户目录 > 源码运行时的产品目录/data
    """
    env = os.environ.get("YINZHAN_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if is_desktop():
        return _user_data_base() / APP_NAME_EN
    return Path(__file__).resolve().parents[3] / "data"     # Sanitized_Data/data


def output_root() -> Path:
    env = os.environ.get("YINZHAN_OUTPUT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if is_desktop():
        return _user_data_base() / APP_NAME_EN / "out"
    return Path(__file__).resolve().parents[3] / "out"      # Sanitized_Data/out
