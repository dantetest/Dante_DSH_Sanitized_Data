#!/usr/bin/env python3
"""冒烟测试：光能启动不算过，插件必须齐全。

打包最容易出的事故是「PyInstaller 没把插件模块打进包」——
程序照样起得来、页面照样能打开，但解析器是 0 个，一传文件就报错。
所以这里必须断言解析器数量和 OCR 状态。
"""
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8760/api/health"


def fetch(retries=20, delay=2):
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(URL, timeout=5) as r:
                return json.loads(r.read())
        except Exception as exc:      # noqa: BLE001
            last = exc
            time.sleep(delay)
    raise SystemExit(f"健康检查一直失败：{last}")


def main() -> int:
    h = fetch()
    eng = h.get("engine") or {}
    parsers = eng.get("parsers") or []
    detectors = eng.get("detectors") or []
    packs = eng.get("packs") or []
    print("version  =", h.get("version"))
    print("parsers  =", parsers)
    print("detectors=", detectors)
    print("packs    =", [p.get("id") for p in packs])
    print("ocr      =", eng.get("ocr"))

    ok = True
    if len(parsers) < 5:
        print("✗ 解析器没打进包（应 >= 5）")
        ok = False
    if len(detectors) < 3:
        print("✗ 检测器没打进包（应 >= 3）")
        ok = False
    if not eng.get("ocr"):
        print("✗ OCR 不可用")
        ok = False
    if ok:
        print("✓ 冒烟测试通过")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
