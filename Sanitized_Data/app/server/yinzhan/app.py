"""服务入口。

启动顺序：加载插件 → 选端口 → 起 HTTP → 写 runtime.json → 打开浏览器。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from . import logging_setup, registry
from .api import serve
from .config import APP_NAME, DATA_DIR, HOST, PORT, PORT_RANGE, RUNTIME_PATH, VERSION
from .jobs import MANAGER, purge_runtime_dirs
from .ocr import get_ocr

BANNER = r"""
  ┌──────────────────────────────────────────────┐
  │   数 据 隐 站   Sanitized_Data               │
  │   文件交给 AI 之前，最后一道本地的门          │
  └──────────────────────────────────────────────┘
"""


def _port_bindable(host: str, port: int) -> bool:
    """用「不带 SO_REUSEADDR 的 bind」探测端口是否空闲。

    不能用 SO_REUSEADDR：Windows 语义允许绑定**已被监听**的端口，两个进程会
    「都绑定成功」，新实例的页面/健康检查实际命中旧实例（v1.1.0 构建自检时
    踩到过，表现为 runtime.json 是 1.1.0 而 /api/health 返回 1.0.0）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, preferred: int | None = None) -> int:
    """挑一个真正空闲的端口（只做本地 bind 探测，不发起任何外呼）。"""
    if preferred and _port_bindable(host, preferred):
        return preferred
    lo, hi = PORT_RANGE
    for p in range(lo, hi):
        if _port_bindable(host, p):
            return p
    return lo


def write_runtime(host: str, port: int) -> None:
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    payload = {
        "app": APP_NAME, "version": VERSION,
        "host": host, "port": port,
        "url": f"http://{shown}:{port}",
        "pid": os.getpid(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        # 本地文件日志位置/配置：status.py / 运维脚本不用再猜路径
        "log_dir": str(logging_setup.log_dir_path() or ""),
        "log_file": str(logging_setup.current_log_path() or ""),
        "log": logging_setup.log_settings(),
    }
    RUNTIME_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")


def preload() -> dict:
    """加载插件 + 预热 OCR，启动时间换首次响应速度。"""
    errors = registry.load_plugins()
    ocr = get_ocr()
    return {
        "plugins": registry.all_parsers(),
        "errors": errors,
        "ocr": ocr.available,
        "ocr_error": ocr.error,
    }


_STOP_REASON = "正常退出"


def _install_signal_handlers() -> None:
    """Ctrl+C / systemd stop(SIGTERM) 时先写一行 stop 日志再退出。

    Python 默认对 SIGTERM 是直接终止，不给 finally 机会；服务器上
    ``systemctl stop`` 后日志会突然断掉，无法区分「正常停」和「被杀」。
    """
    def _on_signal(signum, frame):
        global _STOP_REASON
        try:
            name = signal.Signals(signum).name
        except Exception:
            name = str(signum)
        _STOP_REASON = f"收到信号 {name}"
        raise KeyboardInterrupt

    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            # 非主线程 / Windows 某些环境不支持；不影响主流程
            pass


def _heartbeat_metrics() -> dict:
    """心跳里带当前活跃任务数，方便判断服务是否卡在处理流程里。"""
    active = 0
    try:
        for snap in MANAGER.list(limit=200):
            if snap.get("stage") in ("queued", "parsing", "detecting", "exporting"):
                active += 1
    except Exception:
        pass
    return {"active_jobs": active}


def main(argv: list[str] | None = None) -> int:
    global _STOP_REASON
    ap = argparse.ArgumentParser(description="数据隐站本地引擎")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default=HOST,
                    help="监听地址。默认 127.0.0.1（仅本机）；"
                         "要对外提供服务（如放在 nginx 反代后面）用 0.0.0.0")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-ocr", action="store_true")
    args = ap.parse_args(argv)

    # 先落日志再干活：这样卡在 OCR 加载 / 端口绑定阶段也能从日志看出来
    logging_setup.setup_logging()
    logging_setup.install_exception_hooks()
    logger = logging_setup.get_logger(__name__)
    _install_signal_handlers()
    _STOP_REASON = "正常退出"

    httpd = None
    serve_thread: threading.Thread | None = None
    serve_errors: list[str] = []
    reason = "正常退出"
    try:
        logging_setup.log_startup({
            "entry": "server",
            "host_arg": args.host,
            "port_arg": args.port,
            "no_browser": bool(args.no_browser),
            "no_ocr": bool(args.no_ocr),
        })
        print(BANNER)
        logger.info("正在加载解析插件与 OCR…")
        t0 = time.monotonic()
        boot = preload()
        load_s = round(time.monotonic() - t0, 2)
        formats = sorted({p.format_id for p in boot["plugins"].values()
                          if isinstance(p, registry.Parser)})
        logger.info("引擎加载完成 load_s=%s ocr=%s parsers=%s plugin_errors=%s",
                    load_s, bool(boot["ocr"]), ",".join(formats) or "-",
                    len(boot["errors"]))
        print(f"  版本      {VERSION}")
        print(f"  解析器    {', '.join(formats) or '（无）'}")
        print(f"  OCR       {'就绪' if boot['ocr'] else '不可用：' + str(boot['ocr_error'])}")
        if boot["errors"]:
            for e in boot["errors"]:
                print(f"  ! 插件加载失败  {e.splitlines()[0]}")
        print(f"  数据目录  {DATA_DIR}")

        # 「不留存」：上次异常退出留下的上传副本和预览件，开机先清掉
        purged = purge_runtime_dirs()
        if purged:
            print(f"  已清理    {purged} 个上次残留的临时文件")
            logger.info("已清理上次残留临时文件 count=%s", purged)

        bind_host = args.host or HOST
        port = pick_port(bind_host, args.port or PORT)
        logger.info("准备监听 host=%s port=%s", bind_host, port)
        httpd = serve(bind_host, port)
        write_runtime(bind_host, port)
        public = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host
        url = f"http://{public}:{port}"
        print(f"\n  首页      {url}/")
        print(f"  控制台    {url}/console")
        print(f"  下载页    {url}/download")
        print(f"  MCP 接入  {url}/mcp")
        if bind_host in ("0.0.0.0", "::"):
            print("  ⚠ 已监听所有网卡。对外提供服务请务必套 HTTPS 反向代理并加访问控制。")
        print("\n  按 Ctrl+C 退出\n")

        logging_setup.log_ready({
            "entry": "server",
            "url": url,
            "host": bind_host,
            "port": port,
            "bind_all": bind_host in ("0.0.0.0", "::"),
            "ocr": bool(boot["ocr"]),
            "parsers": ",".join(formats) or "-",
            "plugin_errors": len(boot["errors"]),
            "runtime": str(RUNTIME_PATH),
        })
        logging_setup.start_heartbeat(_heartbeat_metrics)
        logger.info("服务开始接收请求")

        if not args.no_browser:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()

        def _serve() -> None:
            """HTTP 循环放子线程：主线程只等信号，关停不会卡在 shutdown()。"""
            try:
                httpd.serve_forever()
            except BaseException:                  # noqa: BLE001
                serve_errors.append(traceback.format_exc())
                logger.critical("HTTP 服务异常退出\n%s", serve_errors[-1])

        serve_thread = threading.Thread(target=_serve, name="yinzhan-http",
                                        daemon=True)
        serve_thread.start()
        while serve_thread.is_alive():
            serve_thread.join(0.5)
        if serve_errors:
            reason = "服务异常退出"
            return 1
        return 0
    except KeyboardInterrupt:
        reason = _STOP_REASON
        print("\n  正在退出…")
        return 0
    except Exception:
        reason = "启动失败"
        try:
            logger.critical("服务启动失败\n%s", traceback.format_exc())
        except Exception:
            traceback.print_exc()
        log_ref = (logging_setup.current_log_path()
                   or logging_setup.log_dir_path()
                   or (DATA_DIR / "logs"))
        print(f"  ! 启动失败，详见日志：{log_ref}", file=sys.stderr)
        return 1
    finally:
        # 先停心跳再写 stop：保证 stop 是最后一条记录（监控据此判断已退出）
        logging_setup.stop_heartbeat()
        logging_setup.log_shutdown(reason)
        if httpd is not None:
            try:
                if serve_thread is not None and serve_thread.is_alive():
                    httpd.shutdown()
                    serve_thread.join(timeout=2.0)
                httpd.server_close()
            except Exception:
                logger.debug("关闭 HTTP 服务失败", exc_info=True)
        purge_runtime_dirs()
        try:
            RUNTIME_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        logging_setup.shutdown_logging()


if __name__ == "__main__":
    sys.exit(main())
