"""全局路径与常量。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import paths

APP_NAME = "数据隐站"
APP_NAME_EN = paths.APP_NAME_EN
VERSION = "1.5.4"
# W3 购买入口：静态页只跳公网，不 fetch。未配置时页面显示“入口待接入”，不伪造链接。
PURCHASE_URL = (os.environ.get("YINZHAN_PURCHASE_URL") or "").strip()
# L3 扫码升级：商业服务基址（例如 http://127.0.0.1:8787 或 https://buy.example.com）。
# 只有购买页（浏览器 JS，用户主动点击）会跨域访问它；本机引擎仍然零外呼。
# 优先级：环境变量 YINZHAN_COMMERCIAL_URL > commercial.json（安装目录/数据目录）。
# 留空时 /purchase 显示「购买入口尚未配置」，回落到 PURCHASE_URL / 人工开通。
COMMERCIAL_URL = (os.environ.get("YINZHAN_COMMERCIAL_URL") or "").strip()
# 收费门禁总开关：默认开；测试构建/内部试用可关闭（不阻塞朋友测试）。
# 正式包保持 True；测试构建可写 _build_flags.py 覆盖。
PAYWALL_ENABLED = os.environ.get("YINZHAN_PAYWALL", "1") != "0"
BUILD_CHANNEL = os.environ.get("YINZHAN_BUILD_CHANNEL", "release")
try:
    from ._build_flags import BUILD_CHANNEL as _build_channel
    from ._build_flags import PAYWALL_ENABLED as _build_paywall
    BUILD_CHANNEL = str(_build_channel)
    PAYWALL_ENABLED = bool(_build_paywall)
except Exception:
    pass
# 仅用于「部署后确认是不是新包」：/api/health 原样返回；不参与业务逻辑。
BUILD_ID = "20261113-payment-fix"

# 更新检查：只在用户点“检查更新”或启动后显式检查时访问；文件处理引擎不联网。
# 留空则禁用更新检查；默认用 CKA 静态清单。
UPDATE_MANIFEST_URL = (os.environ.get("YINZHAN_UPDATE_URL")
                       or "https://cka.blctek.com:8443/updates/latest.json").strip()
UPDATE_TIMEOUT = 8.0

# 资源（只读）：源码运行时是 app/，打包后是 sys._MEIPASS
APP_DIR = paths.resource_root()
PRODUCT_DIR = APP_DIR.parent
WEB_DIR = paths.web_dir()

# 数据（可写）：源码运行在产品目录，桌面版在用户目录
DATA_DIR = paths.data_root()
UPLOAD_DIR = DATA_DIR / "uploads"
WORK_DIR = DATA_DIR / "work"
OUTPUT_DIR = paths.output_root()
KEYS_DIR = DATA_DIR / "keys"
DB_PATH = DATA_DIR / "yinzhan.db"
RUNTIME_PATH = DATA_DIR / "runtime.json"

# L3：环境变量没配时，允许用 commercial.json 注入商业服务地址，
# 这样已安装的客户端不必重新打包也能指向正式购买服务。
# 文件格式：{"commercial_url": "https://buy.example.com"}
# 查找顺序（先到先得，仅接受 http/https）：
#   1) 桌面版 exe 同目录 commercial.json（安装器/运维可随包放置）
#   2) 数据目录 commercial.json（%LOCALAPPDATA%\Sanitized_Data 或 YINZHAN_DATA_DIR）
#   3) 源码/绿色版根目录 commercial.json（PRODUCT_DIR）
def _resolve_commercial_url() -> str:
    current = (COMMERCIAL_URL or "").strip()
    if current:
        return current
    candidates: list[Path] = []
    try:
        if paths.FROZEN:
            candidates.append(Path(sys.executable).resolve().parent / "commercial.json")
    except Exception:
        pass
    candidates.append(DATA_DIR / "commercial.json")
    candidates.append(PRODUCT_DIR / "commercial.json")
    for candidate in candidates:
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        value = str((raw or {}).get("commercial_url") or "").strip()
        if value.startswith("http://") or value.startswith("https://"):
            return value
    return ""


COMMERCIAL_URL = _resolve_commercial_url()

for _d in (DATA_DIR, UPLOAD_DIR, WORK_DIR, OUTPUT_DIR, KEYS_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError as _e:            # 受限机器：能读到就行，写失败留给上层报错
        print(f"  ! 无法创建目录 {_d}：{_e}", file=os.sys.stderr)

# 允许的处理大小上限（字节），超过要求用户走「选择文件」按路径处理
MAX_UPLOAD = 512 * 1024 * 1024

STRICTNESS = {
    "loose":    {"min_conf": 0.80, "pending_below": 0.55, "levels": ("L1", "L2", "L3")},
    "standard": {"min_conf": 0.60, "pending_below": 0.80, "levels": ("L1", "L2", "L3")},
    "strict":   {"min_conf": 0.35, "pending_below": 0.95, "levels": ("L1", "L2", "L3")},
}

LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}

HOST = "127.0.0.1"
PORT_RANGE = (8760, 8840)

# 元数据黑名单（导出时必须清理）
PDF_META_KEYS = ("author", "creator", "producer", "subject", "keywords", "title", "creationdate", "moddate")
IMAGE_EXIF_TAGS = ("exif", "gps", "icc_profile", "xml", "xmp")

_env_port = os.environ.get("YINZHAN_PORT")
PORT = int(_env_port) if _env_port and _env_port.isdigit() else None
