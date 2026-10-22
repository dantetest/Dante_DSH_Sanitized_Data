"""数据隐站 · 桌面版入口。

双击后：单实例检查 → 启动本地服务 → 打开原生窗口 → 退出时清理临时数据。
没有 pywebview 或 WebView 不可用时，自动退回系统浏览器。

引擎代码一行不改，这里只做「启动器 + 窗口 + 收尾」。
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

# 必须在导入 yinzhan 之前设置：让 config 走「桌面版」数据目录
os.environ.setdefault("YINZHAN_DESKTOP", "1")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if not getattr(sys, "frozen", False) else Path(sys.executable).parent
SERVER_DIR = ROOT / "app" / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from yinzhan import api, config, logging_setup          # noqa: E402
from yinzhan.app import pick_port, preload, purge_runtime_dirs, write_runtime  # noqa: E402

WINDOW_TITLE = "数据隐站 · Sanitized_Data"
MUTEX_NAME = "Global\\SanitizedData_SingleInstance_v1"

_mutex_handle = None
_STOP_REASON = "正常退出"


def _install_signal_handlers() -> None:
    """SIGTERM / Ctrl+C 时先记 stop 日志，再走 finally 清理。"""
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
            pass


# ───────────────────────── 工具 ─────────────────────────

def _msgbox(text: str, title: str = WINDOW_TITLE) -> None:
    """没有控制台时的兜底提示。"""
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
            return
        if sys.platform == "darwin":
            import subprocess
            subprocess.run(["osascript", "-e",
                            f'display dialog "{text}" with title "{title}" buttons {{"好"}}'],
                           check=False)
            return
    except Exception:
        pass
    print(text)


def _alive(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _single_instance() -> bool:
    """True = 我是唯一实例；False = 已有实例在跑。"""
    global _mutex_handle
    if sys.platform == "win32":
        try:
            import ctypes
            _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
            if ctypes.windll.kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
                return False
            return True
        except Exception:
            return True
    # macOS / Linux：用 pid 锁文件
    lock = config.DATA_DIR / "desktop.lock"
    try:
        if lock.exists():
            old = int(lock.read_text().strip() or 0)
            if old and _pid_alive(old):
                return False
        lock.write_text(str(os.getpid()))
        return True
    except Exception:
        return True


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _activate_existing() -> None:
    """已有实例：把它的界面调出来。"""
    url = "http://127.0.0.1:8760/"
    try:
        rt = json.loads((config.DATA_DIR / "runtime.json").read_text("utf-8"))
        url = rt.get("url") or url
    except Exception:
        pass
    if _alive(url):
        webbrowser.open(url)
    else:
        _msgbox("数据隐站已经在运行，但没有响应。请在任务管理器里结束它后重试。")


# ───────────────────────── 主流程 ─────────────────────────

def _start_server():
    port = pick_port(config.HOST, config.PORT or 8760)
    boot = preload()                       # 加载插件 + 预热 OCR（约 2 秒）
    httpd = api.serve(config.HOST, port)
    write_runtime(config.HOST, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port, boot


def _run_window(url: str) -> str:
    """打开窗口，返回实际使用的模式。"""
    try:
        import webview
    except Exception:
        webbrowser.open(url)
        return "browser"

    try:
        webview.create_window(
            WINDOW_TITLE, url,
            width=1360, height=900, min_size=(1024, 700),
            background_color="#F4EEE1",
        )
        webview.start()
        return "window"
    except Exception as exc:
        print("窗口启动失败，退回浏览器：", exc)
        webbrowser.open(url)
        return "browser"


def _diagnose() -> int:
    """--diagnose：把启动环境写成文件，便于排查打包问题（无控制台时尤其有用）。"""
    from yinzhan import registry
    lines = []
    lines.append(f"frozen      = {getattr(sys, 'frozen', False)}")
    lines.append(f"_MEIPASS    = {getattr(sys, '_MEIPASS', '-')}")
    lines.append(f"executable  = {sys.executable}")
    lines.append(f"data_dir    = {config.DATA_DIR}")
    lines.append(f"output_dir  = {config.OUTPUT_DIR}")
    lines.append(f"web_dir     = {config.WEB_DIR}  exists={config.WEB_DIR.is_dir()}")
    lines.append(f"root        = {registry.__file__}")
    try:
        errs = registry.load_plugins()
        lines.append(f"plugin_errors = {len(errs)}")
        for e in errs:
            lines.append("  ! " + e.replace("\n", " | ")[:300])
        lines.append(f"parsers     = {sorted({p.format_id for p in registry.all_parsers().values()})}")
        lines.append(f"detectors   = {sorted(registry.all_detectors().keys())}")
    except Exception:
        lines.append("load_plugins 崩溃:\n" + traceback.format_exc())
    out = config.DATA_DIR / "diagnose.txt"
    out.write_text("\n".join(lines), "utf-8")
    print(out)
    return 0


def main() -> int:
    if "--diagnose" in sys.argv:
        return _diagnose()
    if not _single_instance():
        _activate_existing()
        return 0

    # 桌面版/绿色版没有控制台：启动、失败、退出都要落到本地日志文件
    logging_setup.setup_logging()
    logging_setup.install_exception_hooks()
    logging_setup.log_startup({"entry": "desktop",
                               "frozen": bool(getattr(sys, "frozen", False))})
    logging_setup.start_heartbeat()
    _install_signal_handlers()
    global _STOP_REASON
    _STOP_REASON = "正常退出"

    httpd = None
    reason = "正常退出"
    try:
        logging_setup.get_logger(__name__).info("加载解析插件与 OCR…")
        httpd, port, boot = _start_server()
        url = f"http://{config.HOST}:{port}/"
        print(f"  数据目录  {config.DATA_DIR}")
        print(f"  服务地址  {url}")
        if not boot.get("ocr"):
            print("  ! OCR 不可用：", boot.get("ocr_error"))
        logging_setup.log_ready({
            "entry": "desktop", "url": url, "port": port,
            "ocr": bool(boot.get("ocr")),
            "plugin_errors": len(boot.get("errors") or []),
        })
        logging_setup.get_logger(__name__).info("桌面窗口准备就绪")

        mode = _run_window(url)
        logging_setup.get_logger(__name__).info("窗口模式结束 mode=%s", mode)

        # 窗口模式：窗口关闭即退出；浏览器模式：等用户按 Ctrl+C
        if mode == "browser":
            print("\n  已在浏览器打开，关闭本程序请按 Ctrl+C 或结束进程。")
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                reason = _STOP_REASON
        return 0
    except KeyboardInterrupt:
        reason = _STOP_REASON
        return 0
    except Exception as exc:                      # noqa: BLE001
        traceback.print_exc()
        try:
            logging_setup.get_logger(__name__).critical(
                "桌面版启动失败\n%s", traceback.format_exc())
        except Exception:
            pass
        log_ref = (logging_setup.current_log_path()
                   or logging_setup.log_dir_path()
                   or (config.DATA_DIR / "logs"))
        _msgbox(f"启动失败：\n\n{exc}\n\n日志：{log_ref}")
        reason = "启动失败"
        return 1
    finally:
        # 先停心跳再写 stop：保证 stop 是最后一条记录
        logging_setup.stop_heartbeat()
        logging_setup.log_shutdown(reason)
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        purge_runtime_dirs()
        for leftover in (config.DATA_DIR / "desktop.lock",
                         config.RUNTIME_PATH):
            try:
                leftover.unlink(missing_ok=True)
            except Exception:
                pass
        logging_setup.shutdown_logging()


if __name__ == "__main__":
    sys.exit(main())
