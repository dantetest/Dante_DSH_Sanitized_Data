# -*- coding: utf-8 -*-
"""Live MCP stdio probe: what does an MCP client (agent) actually see?

Runs the shipped stdio server from source, with an isolated data dir and cwd,
captures every JSON-RPC response, then inspects side effects (audit DB, vault,
output dir).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data")
PROBE = Path(r"D:\DSH_DESKTOP_WORKSPACE\_mcp_probe")
DATA = PROBE / "data"
CWD = PROBE / "cwd"

for p in (DATA, CWD):
    shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)

sample = CWD / "probe_sample.txt"
sample.write_text(
    "委托人张伟，男，身份证号310104198703124510，电话13812345678，"
    "邮箱zhangwei@example.com。\n"
    "对方律师：李娜，手机13987654321。\n"
    "收款账号：6222021001112223334。\n"
    "案号：（2024）沪0104民初1234号。\n",
    encoding="utf-8",
)

env = dict(os.environ)
env["YINZHAN_DATA_DIR"] = str(DATA)
env["PYTHONIOENCODING"] = "utf-8"

server = subprocess.Popen(
    [sys.executable, "-X", "utf8", str(ROOT / "app" / "server" / "mcp_server.py")],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", errors="replace", cwd=str(CWD), env=env,
)

transcript: list[dict] = []
stderr_text = ""


def send(obj: dict) -> dict | None:
    """Send one JSON-RPC message, read one response line."""
    server.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
    server.stdin.flush()
    line = server.stdout.readline()
    if not line:
        return None
    return json.loads(line)


def record(label: str, payload, note: str = "") -> None:
    transcript.append({"label": label, "note": note, "payload": payload})


try:
    t0 = time.time()
    record("initialize", send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "probe", "version": "0"}},
    }))
    record("initialized_notification", None, "no response expected")
    server.stdin.write(json.dumps(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, ensure_ascii=False) + "\n")
    server.stdin.flush()

    tools = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    record("tools/list", tools)

    record("tools/call redact_text", send({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "redact_text",
                   "arguments": {"text": sample.read_text(encoding="utf-8")}},
    }))

    record("tools/call check_support", send({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "check_support", "arguments": {"path": str(sample)}},
    }))

    record("tools/call sanitize_document(ai)", send({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "sanitize_document",
                   "arguments": {"path": str(sample), "mode": "ai"}},
    }))

    record("tools/call sanitize_document(human)", send({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "sanitize_document",
                   "arguments": {"path": str(sample), "mode": "human"}},
    }))

    rules = send({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                  "params": {"name": "list_rules", "arguments": {}}})
    if rules and rules.get("result", {}).get("content"):
        txt = rules["result"]["content"][0]["text"]
        try:
            parsed = json.loads(txt)
            rules["result"]["content"][0]["text"] = (
                f"<{len(parsed.get('packs', []))} packs, {len(txt)} chars total; "
                f"default_pack={parsed.get('default_pack')}>")
        except Exception:
            pass
    record("tools/call list_rules", rules)
    elapsed = round(time.time() - t0, 2)
finally:
    try:
        server.stdin.close()
    except Exception:
        pass
    try:
        stderr_text = server.stderr.read()
    except Exception:
        pass
    try:
        server.wait(timeout=30)
    except Exception:
        server.kill()

# ── side effects ─────────────────────────────────────────────
side: dict = {"elapsed_s": elapsed, "cwd": str(CWD), "data_dir": str(DATA)}

db = DATA / "yinzhan.db"
if db.exists():
    try:
        conn = sqlite3.connect(str(db))
        side["audit_rows"] = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        side["audit_sample"] = [
            dict(zip([c[1] for c in conn.execute("PRAGMA table_info(audit)")], row))
            for row in conn.execute(
                "SELECT * FROM audit ORDER BY seq DESC LIMIT 3").fetchall()
        ]
        conn.close()
    except Exception as exc:
        side["audit_error"] = f"{type(exc).__name__}: {exc}"
else:
    side["audit_rows"] = None
    side["audit_note"] = "yinzhan.db not created"

vault_dir = DATA / "vault"
side["vault_files"] = sorted(p.name for p in vault_dir.glob("*")) if vault_dir.is_dir() else []
side["data_tree"] = sorted(str(p.relative_to(DATA)) for p in DATA.rglob("*") if p.is_file())[:40]

out_dir = CWD / "sanitized_output"
side["out_dir"] = str(out_dir)
side["out_files"] = sorted(p.name for p in out_dir.glob("*")) if out_dir.is_dir() else []

# rehydrate probe: list vault via rehydrate.py
try:
    r = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "app" / "server" / "rehydrate.py"), "--list"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(CWD), env=env, timeout=60,
    )
    side["rehydrate_list"] = (r.stdout + r.stderr).strip()[:1200]
except Exception as exc:
    side["rehydrate_error"] = f"{type(exc).__name__}: {exc}"

(PROBE / "transcript.json").write_text(
    json.dumps({"transcript": transcript, "side_effects": side}, ensure_ascii=False, indent=2),
    encoding="utf-8")
(PROBE / "server_stderr.log").write_text(stderr_text, encoding="utf-8")

print(json.dumps({"transcript": transcript, "side_effects": side},
                 ensure_ascii=False, indent=2))
