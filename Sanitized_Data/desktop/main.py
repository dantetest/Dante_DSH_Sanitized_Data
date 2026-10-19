"""数据隐站 · 桌面版入口。

双击后：单实例检查 → 启动本地服务 → 打开原生窗口 → 退出时清理临时数据。
没有 pywebview 或 WebView 不可用时，自动退回系统浏览器。

引擎代码一行不改，这里只做「启动器 + 窗口 + 收尾」。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
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

from yinzhan import api, config                      # noqa: E402
from yinzhan.app import pick_port, preload, purge_runtime_dirs, write_runtime  # noqa: E402

WINDOW_TITLE = "数据隐站 · Sanitized_Data"
MUTEX_NAME = "Global\\SanitizedData_SingleInstance_v1"

_mutex_handle = None


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


def main() -> int:
    if not _single_instance():
        _activate_existing()
        return 0

    httpd = None
    try:
        httpd, port, boot = _start_server()
        url = f"http://{config.HOST}:{port}/"
        print(f"  数据目录  {config.DATA_DIR}")
        print(f"  服务地址  {url}")
        if not boot.get("ocr"):
            print("  ! OCR 不可用：", boot.get("ocr_error"))

        mode = _run_window(url)

        # 窗口模式：窗口关闭即退出；浏览器模式：等用户按 Ctrl+C
        if mode == "browser":
            print("\n  已在浏览器打开，关闭本程序请按 Ctrl+C 或结束进程。")
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
        return 0
    except Exception as exc:                      # noqa: BLE001
        import traceback
        traceback.print_exc()
        _msgbox(f"启动失败：\n\n{exc}\n\n日志：{config.DATA_DIR / 'server.log'}")
        return 1
    finally:
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
        purge_runtime_dirs()
        try:
            (config.DATA_DIR / "desktop.lock").unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
