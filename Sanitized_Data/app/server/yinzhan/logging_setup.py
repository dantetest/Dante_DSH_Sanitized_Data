"""本地文件日志：按日期翻转 + 保留清理，供服务器端监控服务状态。

为什么需要这个模块
------------------
桌面版双击启动、服务器 systemd 启动时都没有控制台；出问题时用户只能看到
「页面打不开」，不知道服务启动到哪一步、有没有请求进来、进程是否还活着。
本模块把 **启动、就绪、请求、错误、心跳、退出** 统一写成一份本地文件日志。

文件布局（默认 ``<数据目录>/logs/``）
------------------------------------
``server.log``
    当前正在写的日志。
``server-YYYY-MM-DD.log``
    每天 0 点后由下一条日志触发的翻转（rename）历史文件；默认保留最近 30 个
    按日归档（约 30 天）。可用 ``YINZHAN_LOG_KEEP_DAYS`` 调整，0 表示不清理。

只记「运维事实」，不记用户内容（产品红线：日志不能变成新的泄漏源）
--------------------------------------------------------------------
* 请求行只有 method / path / status / 耗时 / 字节数；
  **query string 整段丢弃**，请求体、上传文件名、文档内容、实体原值都不落日志。
* 错误日志沿用原 ``data/server.log`` 的口径（错误类型、接口路径、堆栈），
  不额外打印请求体。
* 客户端 IP 默认不记；``YINZHAN_LOG_CLIENT_IP=1`` 时才写（反代场景取
  X-Real-IP / X-Forwarded-For 的第一跳）。

环境变量
--------
====================================  ==========================================
``YINZHAN_LOG_DIR``                   日志目录（默认 ``<数据目录>/logs``）
``YINZHAN_LOG_LEVEL``                 DEBUG / INFO / WARNING / ERROR（默认 INFO）
``YINZHAN_LOG_KEEP_DAYS``             历史日志保留个数（默认 30；0 = 不清理）
``YINZHAN_LOG_REQUESTS``              0 = 关闭请求日志（默认 1）
``YINZHAN_LOG_HEARTBEAT_SEC``         心跳间隔秒数（默认 300；0 = 关闭）
``YINZHAN_LOG_CLIENT_IP``             1 = 请求日志记客户端 IP（默认 0）
``YINZHAN_LOG_STDERR``                0 = 关闭控制台输出（默认有 stderr 就开）
====================================  ==========================================

降级策略：首选日志目录不可写（只读挂载、磁盘满、权限不足）时，先尝试
``<数据目录>/logs``；仍不可写才只写 stderr（服务继续跑），绝不让
「日志写不进去」把服务本身拖死。
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# ── 常量 ──
LOGGER_NAME = "yinzhan"
LOG_BASENAME = "server.log"
DEFAULT_LEVEL = "INFO"
DEFAULT_KEEP_DAYS = 30
DEFAULT_HEARTBEAT_SEC = 300

_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# server-2026-10-22.log；极端情况下同日多次 rollover 会变成 server-2026-10-22-1.log
_ARCHIVE_RE = re.compile(r"^server-(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.log$")

_PROCESS_STARTED = time.time()
_PROCESS_STARTED_MONO = time.monotonic()
_LOCK = threading.RLock()
_STATE: "LogState | None" = None
_HEARTBEAT: "Heartbeat | None" = None
_HOOKS_INSTALLED = False


# ══════════════════════════ 配置读取 ══════════════════════════

def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def default_log_dir() -> Path:
    """日志目录：YINZHAN_LOG_DIR > <数据目录>/logs。"""
    env = os.environ.get("YINZHAN_LOG_DIR")
    if env:
        return Path(env).expanduser().resolve()
    from .config import DATA_DIR          # 延迟导入：单测可以不碰 config
    return (DATA_DIR / "logs").resolve()


def _legacy_log_path() -> Path | None:
    """旧版单文件日志 <数据目录>/server.log（用于一次性迁移）。"""
    try:
        from .config import DATA_DIR
        legacy = Path(DATA_DIR) / LOG_BASENAME
        if legacy.resolve() == (default_log_dir() / LOG_BASENAME):
            return None
        return legacy
    except Exception:
        return None


def _log_level_name(level: str | int | None) -> str:
    if isinstance(level, int):
        return logging.getLevelName(level) if logging.getLevelName(level) in _LEVELS else DEFAULT_LEVEL
    name = (level or os.environ.get("YINZHAN_LOG_LEVEL") or DEFAULT_LEVEL).strip().upper()
    return name if name in _LEVELS else DEFAULT_LEVEL


# ══════════════════════════ 按日期翻转的 handler ══════════════════════════

class DailyRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """把 ``server.log`` 按天翻转为 ``server-YYYY-MM-DD.log``。

    继承标准库的 TimedRotatingFileHandler（它处理跨天判断、DST、线程锁），
    只做两处定制：
      1. ``rotation_filename``：归档名改成 ``server-YYYY-MM-DD.log``，
         不用默认的 ``server.log.2026-10-22``；
      2. ``getFilesToDelete``：按上面的命名规则找旧归档，保留最近 N 天。

    另外多一个 ``cleanup()``，供启动时先清一次过期归档。
    """

    def __init__(self, log_path: Path, keep_days: int = DEFAULT_KEEP_DAYS):
        self.log_path = Path(log_path)
        self.log_dir = self.log_path.parent
        self.stem = self.log_path.stem
        super().__init__(
            str(self.log_path),
            when="midnight",
            interval=1,
            backupCount=max(0, int(keep_days)),
            encoding="utf-8",
            delay=False,
            utc=False,
        )

    def rotation_filename(self, default_name: str) -> str:
        """default_name 形如 ``.../server.log.2026-10-22``。"""
        base = str(self.baseFilename)
        name = str(default_name)
        suffix = name[len(base) + 1:] if name.startswith(base + ".") else "unknown"
        target = self.log_dir / f"{self.stem}-{suffix}{self.log_path.suffix}"
        if target.exists():
            # 时钟回拨 / 同日二次 rollover：加序号，绝不覆盖旧日志
            n = 1
            while True:
                alt = self.log_dir / f"{self.stem}-{suffix}-{n}{self.log_path.suffix}"
                if not alt.exists():
                    return str(alt)
                n += 1
        return str(target)

    def getFilesToDelete(self) -> list[str]:
        """只保留最近 backupCount 个按日期命名的归档。"""
        if self.backupCount <= 0:
            return []
        found: list[tuple[str, int, str, str]] = []
        try:
            names = os.listdir(self.log_dir)
        except OSError:
            return []
        for name in names:
            m = _ARCHIVE_RE.match(name)
            if not m:
                continue
            found.append((m.group(1), int(m.group(2) or 0), name,
                          str(self.log_dir / name)))
        found.sort(key=lambda item: (item[0], item[1], item[2]))
        excess = len(found) - self.backupCount
        return [item[3] for item in found[:max(0, excess)]]

    def cleanup(self) -> int:
        """删除超过保留个数的归档，返回删除个数。"""
        removed = 0
        for path in self.getFilesToDelete():
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        return removed


# ══════════════════════════ 启动前的归档处理 ══════════════════════════

def _archive_stale_current(log_dir: Path, *, now: datetime | None = None) -> Path | None:
    """进程跨天停机后重启：先把昨天留下的大 ``server.log`` 归档。

    TimedRotatingFileHandler 只在写入时检查跨天；若进程昨天停机、今天才启动，
    旧内容会和新一天混在同一个文件里。启动时按文件 mtime 判断日期，旧文件
    直接改名成 ``server-<mtime 日期>.log``，再开新的 ``server.log``。
    """
    current = Path(log_dir) / LOG_BASENAME
    try:
        if not current.is_file() or current.stat().st_size <= 0:
            return None
        mtime = datetime.fromtimestamp(current.stat().st_mtime)
    except OSError:
        return None
    now = now or datetime.now()
    if mtime.strftime("%Y-%m-%d") == now.strftime("%Y-%m-%d"):
        return None
    date = mtime.strftime("%Y-%m-%d")
    target = Path(log_dir) / f"server-{date}.log"
    n = 1
    while target.exists():
        target = Path(log_dir) / f"server-{date}-{n}.log"
        n += 1
    try:
        current.replace(target)       # os.replace，跨平台
        return target
    except OSError:
        return None


def _migrate_legacy_log(log_dir: Path) -> Path | None:
    """旧版把日志写在 ``<数据目录>/server.log``，新目录还是空的话迁一次。"""
    legacy = _legacy_log_path()
    if legacy is None:
        return None
    target = Path(log_dir) / LOG_BASENAME
    try:
        if not legacy.is_file() or legacy.stat().st_size <= 0 or target.exists():
            return None
        if any(Path(log_dir).glob("server-*.log")):
            return None
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        legacy.replace(target)
        return target
    except OSError:
        return None


def _candidate_dirs(log_dir: Path) -> list[Path]:
    """首选日志目录；不可写时退到数据目录下的 logs（如果不同）。"""
    dirs = [Path(log_dir)]
    try:
        from .config import DATA_DIR
        fallback = (Path(DATA_DIR) / "logs").resolve()
        if fallback != Path(log_dir).resolve():
            dirs.append(fallback)
    except Exception:
        pass
    return dirs


# ══════════════════════════ 运行时状态 ══════════════════════════

class LogState:
    """一次日志配置的只读快照，供 status.py / runtime.json 使用。"""

    def __init__(self, *, log_dir: Path | None, log_path: Path | None,
                 level: str, keep_days: int, requests: bool, client_ip: bool,
                 heartbeat_sec: int, console: bool, handler: logging.Handler | None,
                 file_error: str = ""):
        self.log_dir = log_dir
        self.log_path = log_path
        self.level = level
        self.keep_days = keep_days
        self.requests = requests
        self.client_ip = client_ip
        self.heartbeat_sec = heartbeat_sec
        self.console = console
        self.handler = handler
        self.file_error = file_error


def _make_formatter() -> logging.Formatter:
    return logging.Formatter(_LOG_FORMAT, _LOG_DATEFMT)


def _build_handler(log_dir: Path, keep_days: int) -> DailyRotatingFileHandler:
    log_dir.mkdir(parents=True, exist_ok=True)
    _archive_stale_current(log_dir)
    handler = DailyRotatingFileHandler(log_dir / LOG_BASENAME, keep_days)
    handler.setFormatter(_make_formatter())
    handler.cleanup()
    return handler


def setup_logging(log_dir: str | Path | None = None,
                  level: str | int | None = None,
                  keep_days: int | None = None,
                  console: bool | None = None, *,
                  requests: bool | None = None,
                  client_ip: bool | None = None,
                  heartbeat_sec: int | None = None,
                  force: bool = False) -> LogState:
    """配置 ``yinzhan`` 文件日志（幂等；force=True 时重建）。

    首选目录不可写时先回退数据目录下的 logs；仍不可写才只写 stderr，
    并仍返回可用状态；业务服务不受影响。
    """
    global _STATE
    with _LOCK:
        if _STATE is not None and not force:
            return _STATE
        stop_heartbeat()
        if _STATE is not None:
            _close_handlers()

        target_dir = Path(log_dir).expanduser().resolve() if log_dir else default_log_dir()
        level_name = _log_level_name(level)
        keep = (_env_int("YINZHAN_LOG_KEEP_DAYS", DEFAULT_KEEP_DAYS, 0)
                if keep_days is None else max(0, int(keep_days)))
        want_requests = (_env_bool("YINZHAN_LOG_REQUESTS", True)
                         if requests is None else bool(requests))
        want_ip = (_env_bool("YINZHAN_LOG_CLIENT_IP", False)
                   if client_ip is None else bool(client_ip))
        heartbeat = (_env_int("YINZHAN_LOG_HEARTBEAT_SEC", DEFAULT_HEARTBEAT_SEC, 0)
                     if heartbeat_sec is None else max(0, int(heartbeat_sec)))
        if console is None:
            console = _env_bool("YINZHAN_LOG_STDERR", sys.stderr is not None)
            console = bool(console and sys.stderr is not None)

        try:
            _migrate_legacy_log(target_dir)
        except Exception:
            pass

        handler: DailyRotatingFileHandler | None = None
        actual_dir: Path | None = None
        file_error = ""
        for candidate in _candidate_dirs(target_dir):
            try:
                handler = _build_handler(candidate, keep)
                actual_dir = candidate
                break
            except Exception as exc:            # 权限/磁盘/路径异常：尝试下一个
                file_error = f"{candidate}: {type(exc).__name__}: {exc}"

        logger = logging.getLogger(LOGGER_NAME)
        logger.disabled = False
        logger.setLevel(level_name)
        logger.propagate = False
        _close_handlers(logger)
        if handler is not None:
            logger.addHandler(handler)
        if console:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(_make_formatter())
            logger.addHandler(stream)
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())

        _STATE = LogState(
            log_dir=actual_dir,
            log_path=(actual_dir / LOG_BASENAME) if actual_dir else None,
            level=level_name,
            keep_days=keep,
            requests=want_requests,
            client_ip=want_ip,
            heartbeat_sec=heartbeat,
            console=console,
            handler=handler,
            file_error=file_error,
        )
        if file_error:
            if actual_dir is not None:
                logger.warning("首选日志目录不可写，已写入 %s；原因：%s",
                               actual_dir, file_error)
            else:
                logger.warning("日志目录不可写，已降级为控制台输出；原因：%s", file_error)
        return _STATE


def _close_handlers(logger: logging.Logger | None = None) -> None:
    log = logger or logging.getLogger(LOGGER_NAME)
    for h in list(log.handlers):
        try:
            h.flush()
            h.close()
        except Exception:
            pass
        log.removeHandler(h)


def shutdown_logging() -> None:
    """停止心跳、关闭文件（进程退出前调用；之后不再写日志）。"""
    global _STATE
    stop_heartbeat()
    with _LOCK:
        _close_handlers()
        _STATE = None


def flush() -> None:
    """把缓冲刷到磁盘（测试/退出前用；FileHandler 平时每行自动 flush）。"""
    logger = logging.getLogger(LOGGER_NAME)
    for h in logger.handlers:
        try:
            h.flush()
        except Exception:
            pass


# ══════════════════════════ 对外只读信息 ══════════════════════════

def get_logger(name: str = "") -> logging.Logger:
    """取 ``yinzhan`` 命名空间下的 logger（确保挂在文件 handler 上）。"""
    if name and not name.startswith(LOGGER_NAME + "."):
        name = f"{LOGGER_NAME}.{name}"
    return logging.getLogger(name or LOGGER_NAME)


def log_dir_path() -> Path | None:
    return _STATE.log_dir if _STATE is not None else None


def current_log_path() -> Path | None:
    return _STATE.log_path if _STATE is not None else None


def log_settings() -> dict:
    state = _STATE
    if state is None:
        return {"configured": False}
    return {
        "configured": True,
        "dir": str(state.log_dir) if state.log_dir else "",
        "file": str(state.log_path) if state.log_path else "",
        "level": state.level,
        "keep_days": state.keep_days,
        "requests": state.requests,
        "client_ip": state.client_ip,
        "heartbeat_sec": state.heartbeat_sec,
        "file_error": state.file_error,
    }


def requests_enabled() -> bool:
    return _STATE is not None and _STATE.requests


# ══════════════════════════ 格式化与日志写入 ══════════════════════════

def _kv(fields: dict[str, Any]) -> str:
    """key=value 列表；含空格的值用 JSON 引号包起来，方便 grep/awk。"""
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        text = sanitize_field(str(value), limit=1000)
        if text == "" or re.search(r"\s", text):
            text = json.dumps(text, ensure_ascii=False)
        parts.append(f"{key}={text}")
    return " ".join(parts)


def sanitize_field(value: Any, limit: int = 300, *, strip_query: bool = False) -> str:
    """单行字段清洗：去 CR/LF/控制字符，可选去掉 query，限长。

    HTTP 头（Origin / X-Forwarded-Host 等）和 http.server 的错误消息都可能带
    换行或 URL query；直接落盘会造成日志注入/参数泄漏，所有外部输入先过这里。
    """
    text = str(value)
    if strip_query:
        text = re.sub(r"\?[^\s'\"<>]*", "", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "?", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:max(0, limit)]


def redact_text(text: Any, limit: int = 4000) -> str:
    """错误/堆栈落盘前脱敏：数据目录路径、URL query、控制字符。

    上传临时名里带原文件名（``u_xxx_张三.pdf``），异常堆栈若原样落盘会把
    文件名带进日志；这里把数据目录相关路径统一替换成占位符。
    """
    value = str(text)
    try:
        from . import config
        pairs = [
            (str(config.UPLOAD_DIR), "<data>/uploads"),
            (str(config.WORK_DIR), "<data>/work"),
            (str(config.OUTPUT_DIR), "<data>/out"),
            (str(config.KEYS_DIR), "<data>/keys"),
            (str(config.DATA_DIR), "<data>"),
        ]
        for prefix, placeholder in pairs:
            if prefix:
                value = value.replace(prefix, placeholder)
    except Exception:
        pass
    value = re.sub(r"\?[^\s'\"<>]*", "", value)
    # 上传临时名 u_<hex>_<原文件名>：不把原文件名带进日志
    value = re.sub(r"(?i)(?:u_[0-9a-f]{6,32}_)[^\\/\r\n\"'<>]+", "<upload>", value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "?", value)
    return value[:max(0, limit)]


def _clean_path(path: str, limit: int = 300) -> str:
    """请求路径清洗：去 query/fragment、控制字符，限长（query 可能含路径/参数）。"""
    text = str(path)
    text = text.split("?", 1)[0].split("#", 1)[0]
    text = re.sub(r"[\x00-\x1f\x7f]", "?", text)
    text = text.replace(" ", "%20")
    return text[:limit]


def format_request(method: str, path: str, status: int,
                   bytes_sent: int = 0, elapsed_ms: float = 0.0,
                   client_ip: str = "") -> str:
    """一行请求日志；刻意不包含 query、请求体、文件名。"""
    fields: dict[str, Any] = {
        "method": (method or "?").upper(),
        "path": _clean_path(path),
        "status": int(status or 0),
        "bytes": int(bytes_sent or 0),
        "ms": f"{max(0.0, float(elapsed_ms or 0.0)):.1f}",
    }
    if client_ip:
        fields["ip"] = client_ip
    return "req " + _kv(fields)


def pick_client_ip(peer: str, real_ip: str = "", forwarded_for: str = "") -> str:
    """客户端 IP：默认不记；开启后反代场景优先 X-Real-IP / X-Forwarded-For。"""
    if _STATE is None or not _STATE.client_ip:
        return ""
    peer = sanitize_field(peer, 64)
    if peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        candidate = sanitize_field(real_ip, 64)
        if not candidate and forwarded_for:
            candidate = sanitize_field(forwarded_for.split(",", 1)[0], 64)
        if candidate:
            return candidate
    return peer


def log_request(method: str, path: str, status: int,
                bytes_sent: int = 0, elapsed_ms: float = 0.0,
                client_ip: str = "") -> None:
    if not requests_enabled():
        return
    try:
        get_logger("api").info("%s", format_request(method, path, status,
                                                    bytes_sent, elapsed_ms,
                                                    client_ip))
    except Exception:
        pass


def log_error(text: str) -> None:
    """错误：写 ERROR 行；堆栈里的数据目录/上传原文件名先脱敏。"""
    try:
        get_logger("api").error("%s", redact_text(text).rstrip())
    except Exception:
        pass


def log_warning(text: str) -> None:
    """单行警告：先清洗换行/query，防止外部 Header 注入伪造日志行。"""
    try:
        get_logger("api").warning("%s", sanitize_field(text, 1000,
                                                       strip_query=True))
    except Exception:
        pass


# ══════════════════════════ 生命周期日志 ══════════════════════════

def _runtime_facts() -> dict[str, Any]:
    facts: dict[str, Any] = {}
    try:
        from . import config
        facts.update({
            "app": config.APP_NAME,
            "version": config.VERSION,
            "build": config.BUILD_ID,
            "data_dir": str(config.DATA_DIR),
        })
    except Exception:
        pass
    return facts


def log_startup(extra: dict[str, Any] | None = None) -> None:
    """进程启动第一行：版本/构建/PID/路径/日志配置，之后任何日志都能定位到实例。"""
    fields: dict[str, Any] = {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "cwd": os.getcwd(),
        "heartbeat_sec": _STATE.heartbeat_sec if _STATE else 0,
        "keep_days": _STATE.keep_days if _STATE else 0,
        "log_level": _STATE.level if _STATE else "",
    }
    fields.update(_runtime_facts())
    if _STATE is not None and _STATE.log_path is not None:
        fields["log_file"] = str(_STATE.log_path)
    if extra:
        fields.update(extra)
    try:
        get_logger("server").info("start %s", _kv(fields))
    except Exception:
        pass


def log_ready(extra: dict[str, Any] | None = None) -> None:
    """服务真正开始接收请求：端口/URL/引擎状态/心跳间隔。"""
    fields: dict[str, Any] = {
        "pid": os.getpid(),
        "uptime": round(time.monotonic() - _PROCESS_STARTED_MONO, 3),
        "threads": threading.active_count(),
    }
    if extra:
        fields.update(extra)
    try:
        get_logger("server").info("ready %s", _kv(fields))
    except Exception:
        pass


def log_shutdown(reason: str, extra: dict[str, Any] | None = None) -> None:
    fields: dict[str, Any] = {
        "pid": os.getpid(),
        "uptime": round(time.monotonic() - _PROCESS_STARTED_MONO, 3),
        "reason": reason or "normal",
    }
    if extra:
        fields.update(extra)
    try:
        get_logger("server").info("stop %s", _kv(fields))
        flush()
    except Exception:
        pass


# ══════════════════════════ 心跳 ══════════════════════════

def _rss_mb() -> float | None:
    """当前进程常驻内存 MB（Linux/macOS 用 resource；Windows 用 psapi）。"""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            psapi = ctypes.WinDLL("psapi")
            kernel32 = ctypes.WinDLL("kernel32")
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            handle = kernel32.GetCurrentProcess()
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / (1024 * 1024), 1)
            return None
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux 的 ru_maxrss 单位是 KB，macOS 是字节
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        return round(usage / divisor, 1)
    except Exception:
        return None


def _disk_free_mb() -> int | None:
    try:
        base = _STATE.log_dir if _STATE and _STATE.log_dir else Path.cwd()
        return int(shutil.disk_usage(str(base)).free // (1024 * 1024))
    except Exception:
        return None


def heartbeat_fields(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """心跳的公共字段（pid/uptime/rss/磁盘/线程），业务可再补 active_jobs。"""
    fields: dict[str, Any] = {
        "pid": os.getpid(),
        "uptime": round(time.monotonic() - _PROCESS_STARTED_MONO, 1),
        "threads": threading.active_count(),
    }
    rss = _rss_mb()
    if rss is not None:
        fields["rss_mb"] = rss
    free = _disk_free_mb()
    if free is not None:
        fields["disk_free_mb"] = free
    if extra:
        fields.update(extra)
    return fields


def log_heartbeat(extra: dict[str, Any] | None = None) -> None:
    """写一行心跳。空闲时也能据此判断进程是否还活着。"""
    try:
        get_logger("heartbeat").info("heartbeat %s", _kv(heartbeat_fields(extra)))
    except Exception:
        pass


class Heartbeat:
    """后台守护线程：首次立即打一行，之后每 interval 秒一行。"""

    def __init__(self, interval: int,
                 provider: Callable[[], dict[str, Any]] | None = None):
        self.interval = max(1, int(interval))
        self.provider = provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="yinzhan-heartbeat",
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._emit()
        while not self._stop.wait(self.interval):
            self._emit()

    def _emit(self) -> None:
        extra: dict[str, Any] = {}
        if self.provider is not None:
            try:
                extra = self.provider() or {}
            except Exception:
                extra = {"provider_error": 1}
        log_heartbeat(extra)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None


def start_heartbeat(provider: Callable[[], dict[str, Any]] | None = None,
                    interval: int | None = None) -> Heartbeat | None:
    """启动心跳；YINZHAN_LOG_HEARTBEAT_SEC=0（或 interval=0）时关闭。"""
    global _HEARTBEAT
    with _LOCK:
        stop_heartbeat()
        seconds = (_STATE.heartbeat_sec if interval is None and _STATE is not None
                   else (int(interval) if interval is not None else 0))
        if seconds <= 0:
            return None
        hb = Heartbeat(seconds, provider)
        _HEARTBEAT = hb
        hb.start()
        return hb


def stop_heartbeat() -> None:
    global _HEARTBEAT
    hb = _HEARTBEAT
    _HEARTBEAT = None
    if hb is not None:
        try:
            hb.stop()
        except Exception:
            pass


# ══════════════════════════ 未捕获异常钩子 ══════════════════════════

def install_exception_hooks() -> None:
    """主线程/子线程未捕获异常都写日志，避免进程静默退出。"""
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    _HOOKS_INSTALLED = True
    old_excepthook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        try:
            get_logger("crash").critical(
                "未捕获异常，进程退出\n%s",
                "".join(traceback.format_exception(exc_type, exc, tb)))
            flush()
        except Exception:
            pass
        try:
            old_excepthook(exc_type, exc, tb)
        except Exception:
            pass

    sys.excepthook = _excepthook

    if hasattr(threading, "excepthook"):
        old_thread_hook = threading.excepthook

        def _thread_excepthook(args):
            try:
                thread_name = args.thread.name if args.thread is not None else "?"
                get_logger("crash").error(
                    "线程 %s 未捕获异常\n%s", thread_name,
                    "".join(traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback)))
            except Exception:
                pass
            try:
                old_thread_hook(args)
            except Exception:
                pass

        threading.excepthook = _thread_excepthook
