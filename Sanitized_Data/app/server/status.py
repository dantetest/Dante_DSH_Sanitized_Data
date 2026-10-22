#!/usr/bin/env python3
"""数据隐站 · 服务器状态查看（本地、只读）。

用途：部署在服务器上后，不用登录页面/翻 journald，一条命令确认
「进程还在不在、健康检查通不通、日志最后写到哪、今天有没有报错」。

用法（在项目根目录）::

    python app/server/status.py                 # 人读摘要
    python app/server/status.py --json          # 机器可读（监控脚本/cron）
    python app/server/status.py --tail 30       # 附带最近 30 行日志
    python app/server/status.py --no-http       # 不探测 /api/health

只读：直接按 ``YINZHAN_DATA_DIR`` / 脚本位置计算数据目录，**不 import config**
（避免 config 初始化时建目录/写探测文件）。读取 ``runtime.json`` 和日志文件，
最多向 **127.0.0.1 / ::1 / localhost** 的 ``/api/health`` 发一次 GET；
禁用代理与重定向，不会访问外部网络，也不写任何文件。

退出码：0 = 健康；1 = 不健康（进程不在 / 健康检查失败）；2 = 参数/环境错误。
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]      # Sanitized_Data/
_LOG_BASENAME = "server.log"
_ARCHIVE_RE = re.compile(r"^server-(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.log$")
_LINE_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] "
    r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]")
_HEARTBEAT_MARK = " heartbeat pid="
_LOOPBACK = ("127.0.0.1", "::1")


# ══════════════════════════ 路径与纯工具 ══════════════════════════

def _data_dir() -> Path:
    env = os.environ.get("YINZHAN_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return _PROJECT_ROOT / "data"


def _default_log_dir(data_dir: Path) -> Path:
    env = os.environ.get("YINZHAN_LOG_DIR")
    if env:
        return Path(env).expanduser()
    return Path(data_dir) / "logs"


def _source_meta() -> tuple[str, str]:
    """runtime.json 缺失时，直接从 config.py 源码读版本/构建号（只读，不 import）。"""
    try:
        text = (Path(__file__).resolve().parent / "yinzhan" / "config.py").read_text("utf-8")
        ver = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
        build = re.search(r'^BUILD_ID\s*=\s*"([^"]+)"', text, re.M)
        return (ver.group(1) if ver else "-", build.group(1) if build else "")
    except Exception:
        return "-", ""


def _pid_alive(pid: int) -> bool:
    """跨平台判断 PID 是否还在（Windows 不能用 os.kill(pid, 0)，会误杀）。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            try:
                code = wintypes.DWORD(0)
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == 259        # STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_tail(path: Path, max_bytes: int = 512 * 1024) -> list[str]:
    """读文件尾部若干 KB（避免日志很大时整文件读入）。"""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - max_bytes)
            fh.seek(start)
            data = fh.read()
        text = data.decode("utf-8", "replace")
        if start > 0:
            text = text.split("\n", 1)[-1]      # 丢掉半行
        return [ln for ln in text.splitlines() if ln.strip()]
    except OSError:
        return []


def _safe_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _safe_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _line_time(line: str) -> datetime | None:
    m = _LINE_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _last_timed(lines: list[str]) -> tuple[str, datetime] | None:
    """反向找最后一条带时间戳的日志行（堆栈续行不算“最后活动”）。"""
    for line in reversed(lines):
        stamp = _line_time(line)
        if stamp is not None:
            return line, stamp
    return None


def _archive_key(path: Path) -> tuple[str, int, str]:
    m = _ARCHIVE_RE.match(path.name)
    if not m:
        return ("0000-00-00", 0, path.name)
    return (m.group(1), int(m.group(2) or 0), path.name)


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = int(max(0, seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _fmt_bytes(num: int | None) -> str:
    if num is None:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _uptime_seconds(runtime: dict) -> float | None:
    started = str(runtime.get("started") or "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return max(0.0, (datetime.now() - datetime.strptime(started, fmt)).total_seconds())
        except ValueError:
            continue
    return None


# ══════════════════════════ 日志扫描 ══════════════════════════

def _scan_logs(log_dir: Path, tail: int) -> dict:
    """统计日志：当前位置、大小、最后活动/心跳、今日错误数、最近 N 行。"""
    current = Path(log_dir) / _LOG_BASENAME
    current_size = _safe_size(current)
    current_mtime = _safe_mtime(current)
    archives = sorted(
        (p for p in Path(log_dir).glob("server-*.log") if _ARCHIVE_RE.match(p.name)),
        key=_archive_key,
    )
    archives_bytes = sum(_safe_size(p) or 0 for p in archives)
    total_bytes = (current_size or 0) + archives_bytes

    lines = _read_tail(current) if current_size is not None else []
    last = _last_timed(lines)
    last_line = last[0] if last else (lines[-1] if lines else "")
    last_time = last[1] if last else None

    today = datetime.now().date()
    # 当前文件最后一条时间戳不是今天的：可能刚跨天/新进程，用最近归档补充最后活动
    source_lines = lines
    if archives and (last_time is None or last_time.date() != today):
        for path in reversed(archives):
            alt = _read_tail(path)
            alt_last = _last_timed(alt)
            if alt_last is not None:
                source_lines = alt
                last_line, last_time = alt_last
                break

    today_text = today.strftime("%Y-%m-%d")
    errors_today = warnings_today = 0
    for line in lines:
        m = _LINE_RE.match(line)
        if not m or m.group(1) != today_text:
            continue
        if m.group(3) in ("ERROR", "CRITICAL"):
            errors_today += 1
        elif m.group(3) == "WARNING":
            warnings_today += 1

    heartbeat_line = ""
    heartbeat_time = None
    for line in reversed(source_lines):
        if _HEARTBEAT_MARK in line:
            heartbeat_line = line
            heartbeat_time = _line_time(line)
            break

    now = time.time()
    return {
        "exists": current_size is not None,
        "dir": str(log_dir),
        "current": str(current),
        "current_bytes": current_size or 0,
        "archives": len(archives),
        "archives_bytes": archives_bytes,
        "total_bytes": total_bytes,
        "oldest_archive": archives[0].name if archives else "",
        "newest_archive": archives[-1].name if archives else "",
        "errors_today": errors_today,
        "warnings_today": warnings_today,
        "last_line": last_line[-400:],
        "last_line_time": last_time.strftime("%Y-%m-%d %H:%M:%S") if last_time else "",
        "last_activity_age_s": (round(max(0.0, now - last_time.timestamp()), 1)
                                if last_time else None),
        "heartbeat_line": heartbeat_line[-400:],
        "heartbeat_time": (heartbeat_time.strftime("%Y-%m-%d %H:%M:%S")
                           if heartbeat_time else ""),
        "heartbeat_age_s": (round(max(0.0, now - heartbeat_time.timestamp()), 1)
                            if heartbeat_time else None),
        "file_age_s": (round(max(0.0, now - current_mtime), 1)
                       if current_mtime else None),
        "tail": source_lines[-tail:] if tail and tail > 0 else [],
    }


# ══════════════════════════ 健康检查（仅回环、禁代理/重定向） ══════════════════════════

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                      # 302 一律不跟，避免被跳到外部地址


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _is_loopback_host(host: str, port: int | None) -> bool:
    if not host:
        return False
    if host in _LOOPBACK:
        return True
    if host != "localhost":
        return False
    try:
        infos = socket.getaddrinfo("localhost", port or 80, type=socket.SOCK_STREAM)
        return bool(infos) and all(
            ipaddress.ip_address(info[4][0]).is_loopback for info in infos)
    except Exception:
        return False


def _check_health(runtime: dict, timeout: float = 2.0, enabled: bool = True) -> dict:
    """只探测本机 /api/health；禁用代理与重定向，显式拒绝非回环地址（离线红线）。"""
    if not enabled:
        return {"ok": None, "skipped": True}
    url = str(runtime.get("url") or "")
    if not url:
        return {"ok": False, "error": "runtime.json 里没有 url"}
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not _is_loopback_host(
            parsed.hostname or "", parsed.port):
        return {"ok": False,
                "error": f"拒绝探测非回环地址：{parsed.hostname or url}"}
    target = url.rstrip("/") + "/api/health"
    start = time.perf_counter()
    try:
        with _OPENER.open(target, timeout=timeout) as resp:
            final = urllib.parse.urlparse(resp.geturl())
            if final.scheme not in ("http", "https") or not _is_loopback_host(
                    final.hostname or "", final.port):
                raise ValueError(f"健康检查被重定向到非回环地址：{resp.geturl()}")
            body = resp.read(256 * 1024)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            data = json.loads(body.decode("utf-8", "replace"))
            return {
                "ok": bool(resp.status == 200 and data.get("ok")),
                "status": resp.status,
                "ms": elapsed_ms,
                "version": data.get("version"),
                "build": data.get("build"),
                "net": data.get("net"),
                "ocr": (data.get("engine") or {}).get("ocr"),
            }
    except Exception as exc:                    # noqa: BLE001
        return {"ok": False,
                "ms": round((time.perf_counter() - start) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}"}


def _heartbeat_interval(runtime: dict) -> int:
    """心跳间隔：优先 runtime.json 里的实际配置，其次环境变量默认值。"""
    cfg = runtime.get("log")
    if isinstance(cfg, dict) and "heartbeat_sec" in cfg:
        try:
            return max(0, int(cfg.get("heartbeat_sec") or 0))
        except (TypeError, ValueError):
            pass
    try:
        return max(0, int(os.environ.get("YINZHAN_LOG_HEARTBEAT_SEC", "300") or 300))
    except ValueError:
        return 300


# ══════════════════════════ 汇总 ══════════════════════════

def collect(data_dir: Path, log_dir: Path, tail: int = 10, http: bool = True) -> dict:
    data_dir = Path(data_dir)
    runtime_path = data_dir / "runtime.json"
    runtime = _read_json(runtime_path)
    pid = 0
    try:
        pid = int(runtime.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    alive = _pid_alive(pid)
    health = _check_health(runtime, enabled=http)
    logs = _scan_logs(Path(log_dir), tail)
    uptime = _uptime_seconds(runtime) if alive else None
    src_version, src_build = _source_meta()

    warnings: list[str] = []
    if not runtime:
        warnings.append("runtime.json 不存在或不可读：服务可能未启动，或数据目录不对")
    elif pid and not alive:
        warnings.append(f"runtime.json 里的进程 pid={pid} 已经不在运行（残留文件）")
    if health.get("ok") is False:
        warnings.append("本机 /api/health 不通：服务可能已崩溃、卡死或端口被改")
    hb_sec = _heartbeat_interval(runtime)
    hb_age = logs.get("heartbeat_age_s")
    if hb_sec > 0 and hb_age is not None and hb_age > hb_sec * 3:
        warnings.append(f"心跳已 {_fmt_duration(hb_age)} 没更新（间隔 {hb_sec}s）：进程可能卡住")
    if not logs.get("exists"):
        warnings.append("日志文件不存在：服务还没写过日志，或日志目录不可写")

    if health.get("ok") is True:
        ok = bool(runtime and alive)
    elif http:
        ok = False
    else:
        ok = bool(runtime and alive)

    version = runtime.get("version") or health.get("version") or src_version
    build = runtime.get("build") or health.get("build") or src_build
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ok": ok,
        "version": version,
        "build": build,
        "data_dir": str(data_dir),
        "runtime_file": str(runtime_path),
        "process": {
            "pid": pid,
            "alive": alive,
            "uptime_s": round(uptime, 1) if uptime is not None else None,
            "uptime": _fmt_duration(uptime),
        },
        "service": {
            "url": runtime.get("url") or "",
            "started": runtime.get("started") or "",
            "health": health,
        },
        "logs": logs,
        "heartbeat_interval_s": hb_sec,
        "warnings": warnings,
    }


# ══════════════════════════ 输出 ══════════════════════════

def _human(report: dict) -> str:
    proc = report["process"]
    svc = report["service"]
    health = svc["health"]
    logs = report["logs"]
    lines = [
        "数据隐站 · 服务器状态",
        "─" * 52,
        f"  结论        {'[OK] 健康' if report['ok'] else '[!!] 异常'}",
        f"  版本        {report['version']}（构建 {report['build'] or '-'}）",
    ]
    if proc["alive"]:
        lines.append(f"  进程        pid={proc['pid']} · 运行中 · 已运行 {proc['uptime']}")
    elif proc["pid"]:
        lines.append(f"  进程        pid={proc['pid']} · 已退出（runtime.json 残留）")
    else:
        lines.append("  进程        未找到运行信息（runtime.json 缺失）")

    if health.get("skipped"):
        lines.append(f"  服务        {svc['url'] or '-'} · 健康检查已跳过")
    elif health.get("ok"):
        lines.append(f"  服务        {svc['url']} · /api/health {health.get('status')} · "
                     f"{health.get('ms')}ms · OCR={health.get('ocr')}")
    else:
        lines.append(f"  服务        {svc['url'] or '-'} · 健康检查失败："
                     f"{health.get('error') or 'status=' + str(health.get('status'))}")

    log_size = _fmt_bytes(logs["current_bytes"]) if logs["exists"] else "不存在"
    lines.append(f"  日志        {logs['current']}（{log_size}）")
    if logs["archives"]:
        lines.append(f"  历史日志    {logs['archives']} 个按日期归档 · 共 "
                     f"{_fmt_bytes(logs['archives_bytes'])}"
                     f" · {logs['oldest_archive']} → {logs['newest_archive']}")
    if logs["heartbeat_time"]:
        lines.append(f"  最近心跳    {logs['heartbeat_time']} · "
                     f"{_fmt_duration(logs['heartbeat_age_s'])}前")
    elif logs["last_line_time"]:
        lines.append(f"  最后活动    {logs['last_line_time']} · "
                     f"{_fmt_duration(logs['last_activity_age_s'])}前")
    elif logs["last_line"]:
        lines.append("  最后活动    （有日志但无法解析时间戳）")
    else:
        lines.append("  最后活动    （还没有日志）")
    lines.append(f"  今日        {logs['errors_today']} 个 ERROR/CRITICAL · "
                 f"{logs['warnings_today']} 个 WARNING")

    if logs["tail"]:
        lines.append("  最近日志")
        for line in logs["tail"]:
            lines.append("    " + line[:220])
    if report["warnings"]:
        lines.append("  提示")
        for item in report["warnings"]:
            lines.append("    ! " + item)
    lines.append("─" * 52)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="数据隐站服务器状态查看（只读；健康=退出码 0）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--tail", type=int, default=None,
                    help="附带最近 N 行日志（默认 10；--json 默认 0）")
    ap.add_argument("--data-dir", default="",
                    help="数据目录（默认 YINZHAN_DATA_DIR 或项目 data/）")
    ap.add_argument("--log-dir", default="",
                    help="临时指定日志目录（默认读 runtime.json / 数据目录）")
    ap.add_argument("--no-http", action="store_true",
                    help="不探测本机 /api/health")
    args = ap.parse_args(argv)

    data_dir = (Path(args.data_dir).expanduser()
                if args.data_dir else _data_dir())
    runtime = _read_json(data_dir / "runtime.json")
    if args.log_dir:
        log_dir = Path(args.log_dir).expanduser()
    elif runtime.get("log_dir"):
        log_dir = Path(str(runtime["log_dir"])).expanduser()
    else:
        log_dir = _default_log_dir(data_dir)

    tail = args.tail if args.tail is not None else (0 if args.json else 10)
    try:
        report = collect(data_dir, log_dir, tail=max(0, tail),
                         http=not args.no_http)
    except Exception as exc:                    # noqa: BLE001
        print(f"status 采集失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_human(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
