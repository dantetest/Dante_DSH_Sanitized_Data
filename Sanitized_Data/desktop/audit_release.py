# -*- coding: utf-8 -*-
"""发布包审计（给陌生客户前跑一遍）：
1) 源码/测试数据/私钥/映射密钥是否混进 dist；
2) 必备文件（EXE、MCP EXE、web、规则、公钥）是否齐全，restore 控制台是否含左右对照；
3) 冒烟：启动 EXE 查 /api/license/status（paywall_enabled / quota.enforced / 3份每天）；
4) 冒烟：启动 MCP EXE 走一次 initialize + tools/list，确认工具已注册。

用法（在 Sanitized_Data 目录下）：
    python -X utf8 desktop/audit_release.py
    python -X utf8 desktop/audit_release.py --no-smoke
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIST = ROOT / "dist" / "Sanitized_Data"

FORBIDDEN = [
    (r"(^|/)_build_flags\.py$", "TEST_MODE 标记文件（测试构建残留）"),
    (r"(^|/)yinzhan/[^/]+\.py$", "产品源码 .py（yinzhan 包）"),
    (r"(^|/)mcp_server\.py$", "MCP 源码 .py"),
    (r"(^|/)app/server/", "源码目录 app/server"),
    (r"(^|/)app/tests/|/test_[^/]+\.py$", "测试源码"),
    (r"(^|/)docs?/.*\.md$|(^|/)README\.md$", "内部文档/说明"),
    (r"master\.key$|(^|/)vault/.*\.bin$", "映射主密钥/映射库"),
    (r"(^|/)data/(yinzhan\.db|runtime\.json|license/|vault/)", "运行时数据"),
    (r"(?i)(private|secret)[^/]*\.(pem|key|json)$", "私钥/密钥文件"),
    (r"(?i)(license_issue|license_keygen)\.py$", "签发工具"),
    (r"(^|/)\.git/", "git 仓库"),
]

REQUIRED = [
    "Sanitized_Data.exe",
    "Sanitized_Data_MCP.exe",
    "_internal/yinzhan/licensing/public_keys.json",
    "_internal/web/restore.html",
    "_internal/web/restore.js",
    "_internal/web/restore.css",
    "_internal/yinzhan/rules/common.yaml",
]


def rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def scan_forbidden(dist: Path) -> list[dict]:
    hits = []
    for p in dist.rglob("*"):
        if not p.is_file():
            continue
        r = rel(p, dist)
        if "certifi" in r and r.endswith(".pem"):
            continue  # 第三方 CA 证书，正常
        for pat, why in FORBIDDEN:
            if re.search(pat, r, re.I):
                hits.append({"file": r, "reason": why})
                break
    return hits


def check_required(dist: Path) -> tuple[list[str], list[str]]:
    missing = [r for r in REQUIRED if not (dist / r).is_file()]
    notes = []
    restore_html = dist / "_internal/web/restore.html"
    if restore_html.is_file():
        text = restore_html.read_text("utf-8", "ignore")
        if "compareBox" not in text or "左右对照" not in text:
            notes.append("restore.html 缺少左右对照预览标记（是否为旧包？）")
    restore_js = dist / "_internal/web/restore.js"
    if restore_js.is_file() and "renderCompare" not in restore_js.read_text("utf-8", "ignore"):
        notes.append("restore.js 缺少 renderCompare（是否为旧包？）")
    return missing, notes


def _get_json(url: str, timeout: float = 4.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _smoke_dir(prefix: str) -> Path:
    """冒烟用临时目录放在项目 build 下（沙箱/权限环境下系统 temp 可能不可写）。"""
    base = ROOT / "build"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(base)))


def smoke_app(dist: Path) -> dict:
    """用临时数据目录/端口启动打包 EXE，检查收费门禁与免费额度。"""
    exe = dist / "Sanitized_Data.exe"
    if not exe.is_file():
        return {"ok": False, "error": "缺少 Sanitized_Data.exe"}
    tmp = _smoke_dir("yz_release_smoke_")
    env = dict(os.environ)
    env["YINZHAN_PORT"] = "18761"
    env["YINZHAN_DATA_DIR"] = str(tmp)
    env["YINZHAN_NO_BROWSER"] = "1"
    proc = subprocess.Popen([str(exe)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    result = {"ok": False, "data_dir": str(tmp)}
    try:
        runtime = None
        for _ in range(40):  # 最多 20s
            time.sleep(0.5)
            rt = tmp / "runtime.json"
            if rt.is_file():
                try:
                    runtime = json.loads(rt.read_text("utf-8"))
                    break
                except Exception:
                    pass
        if not runtime:
            result["error"] = "服务未启动（runtime.json 未生成）"
            return result
        port = int(runtime.get("port") or 18761)
        base = f"http://127.0.0.1:{port}"
        health = _get_json(base + "/api/health")
        lic = _get_json(base + "/api/license/status")
        quota = lic.get("quota") or {}
        limits = lic.get("limits") or {}
        result.update({
            "ok": True,
            "version": health.get("version"),
            "build": health.get("build"),
            "paywall_enabled": lic.get("paywall_enabled"),
            "quota_enforced": quota.get("enforced"),
            "jobs_per_day": limits.get("jobs_per_day"),
            "state": lic.get("state"),
            "port": port,
        })
        problems = []
        if not lic.get("paywall_enabled"):
            problems.append("paywall_enabled=false（这是测试构建，不能给陌生客户）")
        if not quota.get("enforced"):
            problems.append("quota.enforced=false（免费额度未生效）")
        if int(limits.get("jobs_per_day") or 0) != 3:
            problems.append(f"jobs_per_day={limits.get('jobs_per_day')}（期望 3）")
        result["problems"] = problems
        result["ok"] = not problems
        return result
    finally:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def smoke_mcp(dist: Path) -> dict:
    """启动 MCP EXE，发 initialize + tools/list，确认工具注册。"""
    exe = dist / "Sanitized_Data_MCP.exe"
    if not exe.is_file():
        return {"ok": False, "error": "缺少 Sanitized_Data_MCP.exe"}
    tmp = _smoke_dir("yz_mcp_smoke_")
    env = dict(os.environ)
    env["YINZHAN_DATA_DIR"] = str(tmp)
    payload = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18",
                               "capabilities": {},
                               "clientInfo": {"name": "audit", "version": "0"}}},
                   ensure_ascii=False) + "\n" +
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                    "params": {}}, ensure_ascii=False) + "\n"
    )
    result = {"ok": False}
    proc = subprocess.Popen([str(exe)], env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out, err = b"", b""
    try:
        try:
            out, err = proc.communicate(payload.encode("utf-8"), timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        text = out.decode("utf-8", "ignore")
        tools = re.findall(r'"name"\s*:\s*"([^"]+)"', text)
        result.update({"ok": bool(text), "stdout_head": text[:500],
                       "tool_count": len(set(tools)),
                       "has_restore_tool": "restore_sanitize_file" in text,
                       "stderr_tail": err.decode("utf-8", "ignore")[-300:]})
        if not result["has_restore_tool"]:
            result["ok"] = False
        return result
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=str(DEFAULT_DIST))
    ap.add_argument("--no-smoke", action="store_true")
    args = ap.parse_args()
    dist = Path(args.dist)
    report = {"dist": str(dist), "exists": dist.is_dir()}
    if not dist.is_dir():
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    report["forbidden_hits"] = scan_forbidden(dist)
    missing, notes = check_required(dist)
    report["missing_required"] = missing
    report["notes"] = notes
    if not args.no_smoke:
        report["app_smoke"] = smoke_app(dist)
        report["mcp_smoke"] = smoke_mcp(dist)
    failures = []
    if report["forbidden_hits"]:
        failures.append(f"发现 {len(report['forbidden_hits'])} 个禁止文件")
    if missing:
        failures.append(f"缺少 {len(missing)} 个必备文件")
    if notes:
        failures.extend(notes)
    if not args.no_smoke:
        for key in ("app_smoke", "mcp_smoke"):
            if not report[key].get("ok"):
                failures.append(f"{key} 未通过：{report[key].get('error') or report[key].get('problems')}")
    report["pass"] = not failures
    report["failures"] = failures
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
