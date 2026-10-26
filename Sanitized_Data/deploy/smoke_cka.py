# -*- coding: utf-8 -*-
"""CKA 测试服验收探针（公网 / 内网均可）。

用法：
  python deploy/smoke_cka.py --base https://cka.blctek.com:8443 --expect internal
  python deploy/smoke_cka.py --base https://cka.blctek.com:8443 --expect release

  # 额外做一次真实上传/解析/导出（仅内测全功能建议）：
  python deploy/smoke_cka.py --base https://cka.blctek.com:8443 --expect internal \
      --deep --tif "C:\\path\\ortho.tif" --jpeg "C:\\path\\photo.jpg"

只读检查 + 可选的 internal 深度上传；release 模式不消耗免费额度。
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def request(base: str, method: str, path: str, data: bytes | None = None,
            headers: dict | None = None, timeout: float = 120) -> tuple[int, dict | bytes]:
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read()
            if "json" in (resp.headers.get("Content-Type") or ""):
                return resp.status, json.loads(body.decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, body


def check(condition: bool, label: str, detail: str = "") -> bool:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}" + (f"  ({detail})" if detail else ""))
    return condition


def upload(base: str, path: Path) -> str:
    status, resp = request(base, "POST", "/api/upload", path.read_bytes(),
                           {"X-Filename": path.name,
                            "Content-Type": "application/octet-stream"})
    if status != 200 or not isinstance(resp, dict):
        raise RuntimeError(f"上传失败 {path.name}: {status} {resp}")
    return str(resp["upload_id"])


def wait_job(base: str, jid: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, snap = request(base, "GET", f"/api/jobs/{jid}")
        if status == 200 and isinstance(snap, dict):
            if snap.get("stage") in ("ready", "error", "failed"):
                return snap
        time.sleep(1)
    raise TimeoutError(f"任务超时：{jid}")


def deep_internal(base: str, tif: Path | None, jpeg: Path | None) -> bool:
    ok = True
    if tif and tif.is_file():
        uid = upload(base, tif)
        status, created = request(base, "POST", "/api/jobs",
                                  json.dumps({"upload_id": uid, "strictness": "standard"}).encode(),
                                  {"Content-Type": "application/json"})
        if status != 200 or not isinstance(created, dict) or not created.get("job_id"):
            return check(False, "正射 TIFF 建任务", f"{status} {str(created)[:200]}")
        jid = created["job_id"]
        snap = wait_job(base, jid)
        ok &= check(snap.get("stage") == "ready",
                    "正射 TIFF 解析", f"stage={snap.get('stage')} err={snap.get('error')}")
        status, exp = request(base, "POST", f"/api/jobs/{jid}/export",
                              json.dumps({"mode": "ai"}).encode(),
                              {"Content-Type": "application/json"}, timeout=600)
        verified = isinstance(exp, dict) and (exp.get("verify") or {}).get("verified")
        ok &= check(status == 200 and bool(verified), "正射 TIFF 导出+自检",
                    f"status={status} verify={verified if isinstance(exp, dict) else exp}")
        if isinstance(exp, dict):
            out = str(exp.get("out") or "")
            ok &= check(bool(out), "导出返回路径", out[:120])
    if jpeg and jpeg.is_file():
        uid = upload(base, jpeg)
        status, created = request(base, "POST", "/api/jobs",
                                  json.dumps({"upload_id": uid, "strictness": "standard"}).encode(),
                                  {"Content-Type": "application/json"})
        ok &= check(status == 200 and isinstance(created, dict)
                    and bool(created.get("job_id")), "航拍照片建任务", f"{status}")
        if status == 200 and isinstance(created, dict) and created.get("job_id"):
            jid = created["job_id"]
            wait_job(base, jid)
            status, ents = request(base, "GET", f"/api/jobs/{jid}/entities")
            metas = [e for e in (ents.get("entities") or [])
                     if e.get("origin") == "metadata"] if isinstance(ents, dict) else []
            masked = all("*" in str(e.get("value") or "") for e in metas)
            ok &= check(bool(metas) and masked, "照片 EXIF/GPS 进入隐去清单（已打码）",
                        f"{len(metas)} 项")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://cka.blctek.com:8443")
    ap.add_argument("--expect", choices=["internal", "release"], default="internal")
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--tif", default="")
    ap.add_argument("--jpeg", default="")
    args = ap.parse_args()
    base = args.base
    print(f"== 目标 {base}（期望：{args.expect}） ==")

    ok = True
    status, health = request(base, "GET", "/api/health")
    ok &= check(status == 200 and isinstance(health, dict) and health.get("ok") is True,
                "服务健康", f"status={status}")
    version = str((health or {}).get("version") or "")
    build = str((health or {}).get("build") or "")
    ok &= check(version == "1.3.0", "版本 1.3.0", version)
    ok &= check("20261026" in build, "新构建 BUILD_ID", build)

    status, lic = request(base, "GET", "/api/license/status")
    lic = lic if isinstance(lic, dict) else {}
    quota = lic.get("quota") or {}
    enforced = quota.get("enforced")
    paywall = lic.get("paywall_enabled")
    ok &= check(isinstance(lic, dict) and bool(lic.get("state")), "授权状态可读",
                f"state={lic.get('state')} paywall={paywall} enforced={enforced}")
    if args.expect == "internal":
        ok &= check(enforced is False, "内测：收费门禁未启用", f"enforced={enforced}")
        ok &= check(paywall is False or enforced is False, "内测：全功能不限量",
                    f"paywall={paywall}")
    else:
        ok &= check(paywall is True and enforced is True, "正式：免费门禁启用",
                    f"paywall={paywall} enforced={enforced}")
        ok &= check((quota.get("limit_today") or 0) == 3, "免费额度 3 份/天",
                    str(quota.get("limit_today")))

    status, appjs = request(base, "GET", "/app.js")
    text = appjs.decode("utf-8", "replace") if isinstance(appjs, bytes) else ""
    ok &= check("测试版 · 全功能" in text, "前端含内测全功能标签")
    ok &= check("非画面数据" in text, "前端含非画面敏感数据清单")

    if args.deep:
        if args.expect != "internal":
            print("[SKIP] release 模式不做深度上传（会消耗免费额度）")
        else:
            tif = Path(args.tif) if args.tif else None
            jpeg = Path(args.jpeg) if args.jpeg else None
            if not tif and not jpeg:
                print("[SKIP] --deep 需要 --tif/--jpeg 至少一个")
            else:
                ok &= deep_internal(base, tif, jpeg)

    print("== 结论：", "PASS" if ok else "FAIL", "==")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
