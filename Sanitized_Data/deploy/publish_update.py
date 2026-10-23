#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在数据隐站部署机（如 CKA）发布更新清单与安装包。

运行位置：CKA 服务器 /opt/sanitized-data 目录（运维 git pull 之后）。
产物：<dest>/latest.json + 安装包/绿色版；由产品自身的 /updates/ 静态路由对外提供。

用法：
  export GITEA_TOKEN=...   # 只读 release 权限即可，不要写进仓库
  python3 deploy/publish_update.py --tag v1.3.0 \
      --url-prefix https://cka.blctek.com:8443/updates

  # 资产已在服务器上时（不连 Gitea 下载）
  python3 deploy/publish_update.py --tag v1.3.0 \
      --installer /path/Setup.exe --portable /path/portable.zip

  # 只看 Gitea 元数据，不下载
  python3 deploy/publish_update.py --tag v1.3.0 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

GITEA_DEFAULT = "https://git.blctek.com:8443"
REPO_DEFAULT = "baoluo/DSH_DESKTOP_WORKSPACE"
PAGE_DEFAULT = "https://cka.blctek.com:8443/download"


def die(message: str) -> "NoReturn":
    print("ERROR:", message, file=sys.stderr)
    raise SystemExit(2)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)
    return ssl.create_default_context()


def request_json(url: str, token: str, context: ssl.SSLContext) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Authorization": "token " + token,
        "User-Agent": "sanitized-data-updater/1",
    })
    with urllib.request.urlopen(req, timeout=60, context=context) as r:
        return json.loads(r.read().decode("utf-8"))


def download(url: str, dest: Path, token: str, context: ssl.SSLContext) -> None:
    req = urllib.request.Request(url, headers={
        "Authorization": "token " + token,
        "User-Agent": "sanitized-data-updater/1",
    })
    tmp = dest.with_suffix(dest.suffix + ".part")
    total = 0
    with urllib.request.urlopen(req, timeout=600, context=context) as r, tmp.open("wb") as f:
        while True:
            chunk = r.read(8 * 1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
            print(f"\r  {dest.name}: {total/1048576:.1f} MiB", end="", flush=True)
    print()
    tmp.replace(dest)


def pick(assets: list[dict], predicate) -> dict | None:
    for asset in assets:
        if predicate(str(asset.get("name") or "")):
            return asset
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="如 v1.3.0")
    ap.add_argument("--gitea", default=GITEA_DEFAULT)
    ap.add_argument("--repo", default=REPO_DEFAULT)
    ap.add_argument("--dest", default="data/updates", help="服务器上 latest.json/安装包目录")
    ap.add_argument("--url-prefix", default="https://cka.blctek.com:8443/updates")
    ap.add_argument("--release-page", default=PAGE_DEFAULT)
    ap.add_argument("--token-env", default="GITEA_TOKEN")
    ap.add_argument("--ca-bundle", default=os.environ.get("GITEA_CA_BUNDLE"))
    ap.add_argument("--installer", default="", help="本地安装包路径（给定时不下载）")
    ap.add_argument("--portable", default="", help="本地绿色版路径（给定时不下载）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tag = args.tag.strip()
    if not re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
        die(f"tag 格式应是 vX.Y.Z: {tag}")
    version = tag[1:] if tag.startswith("v") else tag
    token = os.environ.get(args.token_env, "").strip()
    context = ssl_context(args.ca_bundle)

    release = None
    if not (args.installer and args.portable):
        if not token:
            die(f"缺少环境变量 {args.token_env}（只读 release token）")
        api = args.gitea.rstrip("/") + f"/api/v1/repos/{args.repo}/releases/tags/{tag}"
        try:
            release = request_json(api, token, context)
        except Exception as e:
            die(f"读取 Gitea Release 失败: {type(e).__name__}: {e}")

    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    url_prefix = args.url_prefix.rstrip("/")

    selected = {}
    if args.installer:
        selected["windows_installer"] = Path(args.installer)
    if args.portable:
        selected["windows_portable"] = Path(args.portable)

    remote_assets = (release or {}).get("assets") or []
    if not selected:
        installer = pick(remote_assets, lambda n: n.startswith("Sanitized_Data_Setup_") and n.endswith(".exe"))
        portable = pick(remote_assets, lambda n: n.startswith("Sanitized_Data-") and n.endswith("-win64-portable.zip"))
        if not installer or not portable:
            die("Release 缺少安装包或绿色版资产（需要 Setup*.exe 与 *win64-portable.zip）")
        selected["windows_installer"] = installer
        selected["windows_portable"] = portable

    if args.dry_run:
        print("DRY-RUN", version, "release assets:")
        for key, item in selected.items():
            if isinstance(item, Path):
                print(" ", key, item)
            else:
                print(" ", key, item.get("name"), item.get("size"), item.get("browser_download_url"))
        return 0

    manifest_assets: dict[str, dict] = {}
    for key, item in selected.items():
        if isinstance(item, Path):
            src = item.resolve()
            if not src.is_file():
                die(f"本地资产不存在: {src}")
            name = src.name
            target = dest / name
            if src != target:
                target.write_bytes(src.read_bytes())
            browser_url = f"{url_prefix}/{urllib.parse.quote(name)}"
        else:
            name = str(item["name"])
            target = dest / name
            browser_url = f"{url_prefix}/{urllib.parse.quote(name)}"
            if not target.is_file() or target.stat().st_size != int(item.get("size") or 0):
                print(f"下载 {name} ...")
                download(str(item["browser_download_url"]), target, token, context)
        actual_size = target.stat().st_size
        expected_size = None if isinstance(item, Path) else int(item.get("size") or 0)
        if expected_size and actual_size != expected_size:
            die(f"size 校验失败 {name}: {actual_size} != {expected_size}")
        digest = sha256_file(target)
        manifest_assets[key] = {
            "name": name,
            "url": browser_url,
            "size": actual_size,
            "sha256": digest,
        }
        print(f"OK {name} {actual_size} B sha256={digest}")

    manifest = {
        "version": version,
        "tag": tag,
        "published_at": (release or {}).get("published_at") or "",
        "release_url": args.release_page,
        "notes": ((release or {}).get("body") or "")[:4000],
        "assets": manifest_assets,
    }
    tmp = dest / "latest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest / "latest.json")
    print(f"已写入 {dest / 'latest.json'}")
    print(f"公网检查: {url_prefix}/latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
