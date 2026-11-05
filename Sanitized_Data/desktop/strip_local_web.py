#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面版本地化：从 PyInstaller dist 中移除下载页和下载入口。

本地安装/绿色版里“下载桌面版”没有意义，且容易让用户点到无效页面。
只处理 dist/_internal/web，不改源码网站；CKA 公网仍保留下载页。
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

LINK_RE = re.compile(
    r'<a\b[^>]*href="(?P<href>/download(?:/)?)"[^>]*>.*?</a>',
    re.S | re.I,
)
DOWNLOAD_RE = re.compile(r'href="[^"]*download[^"]*"', re.I)


def strip_web(web_dir: Path) -> dict:
    web_dir = Path(web_dir)
    removed: list[str] = []
    download = web_dir / "download.html"
    if download.is_file():
        download.unlink()
        removed.append(download.name)
    for path in sorted(web_dir.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        new = LINK_RE.sub("", text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            removed.append(path.name)
    return {"web_dir": str(web_dir), "changed": removed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-dir", required=True)
    args = ap.parse_args()
    print(strip_web(Path(args.web_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
