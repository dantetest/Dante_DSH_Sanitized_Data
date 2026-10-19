"""服务入口。

启动顺序：加载插件 → 选端口 → 起 HTTP → 写 runtime.json → 打开浏览器。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import registry
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


def pick_port(preferred: int | None = None) -> int:
    if preferred:
        return preferred
    lo, hi = PORT_RANGE
    for p in range(lo, hi):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, p))
                return p
            except OSError:
                continue
    return lo


def write_runtime(port: int) -> None:
    payload = {
        "app": APP_NAME, "version": VERSION,
        "host": HOST, "port": port,
        "url": f"http://{HOST}:{port}",
        "pid": os.getpid(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="数据隐站本地引擎")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-ocr", action="store_true")
    args = ap.parse_args(argv)

    print(BANNER)
    boot = preload()
    formats = sorted({p.format_id for p in boot["plugins"].values()
                      if isinstance(p, registry.Parser)})
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

    port = pick_port(args.port or PORT)
    httpd = serve(HOST, port)
    write_runtime(port)
    url = f"http://{HOST}:{port}"
    print(f"\n  控制台    {url}")
    print("  按 Ctrl+C 退出\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  正在退出…")
    finally:
        httpd.shutdown()
        purge_runtime_dirs()
        try:
            RUNTIME_PATH.unlink(missing_ok=True)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
