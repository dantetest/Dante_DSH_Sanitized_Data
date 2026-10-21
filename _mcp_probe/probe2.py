# -*- coding: utf-8 -*-
"""Second MCP probe: real sample files -> what the agent gets."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data")
PROBE = Path(r"D:\DSH_DESKTOP_WORKSPACE\_mcp_probe")
DATA = PROBE / "data2"
CWD = PROBE / "cwd2"
for p in (DATA, CWD):
    shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)

env = dict(os.environ)
env["YINZHAN_DATA_DIR"] = str(DATA)
env["PYTHONIOENCODING"] = "utf-8"

server = subprocess.Popen(
    [sys.executable, "-X", "utf8", str(ROOT / "app" / "server" / "mcp_server.py")],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", errors="replace", cwd=str(CWD), env=env,
)


def call(i, method, params=None):
    server.stdin.write(json.dumps(
        {"jsonrpc": "2.0", "id": i, "method": method,
         "params": params or {}}, ensure_ascii=False) + "\n")
    server.stdin.flush()
    return json.loads(server.stdout.readline())


out = {}
try:
    call(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "probe2", "version": "0"}})
    server.stdin.write(json.dumps({"jsonrpc": "2.0",
        "method": "notifications/initialized"}) + "\n")
    server.stdin.flush()

    cases = [
        ("namecard.png", ROOT / "samples" / "名片.png", "ai"),
        ("medical.docx", ROOT / "samples" / "住院病历.docx", "ai"),
        ("contract.pdf", ROOT / "samples" / "委托代理合同.pdf", "ai"),
    ]
    i = 10
    for label, path, mode in cases:
        t0 = time.time()
        resp = call(i, "tools/call", {"name": "sanitize_document",
                                      "arguments": {"path": str(path), "mode": mode}})
        i += 1
        res = resp.get("result") or {}
        txt = (res.get("content") or [{}])[0].get("text", "")
        out[label] = {
            "seconds": round(time.time() - t0, 2),
            "isError": res.get("isError"),
            "raw": txt,
        }
finally:
    try:
        server.stdin.close()
    except Exception:
        pass
    err = server.stderr.read()
    server.wait(timeout=30)

(PROBE / "transcript2.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
(PROBE / "server_stderr2.log").write_text(err, encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
