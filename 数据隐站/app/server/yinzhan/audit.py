"""审计链。

只记「类型 + 数量」，绝不记原值 —— 否则审计日志自己就成了泄露源。
每条记录带 HMAC，改任何一条，后续校验都会失败。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from pathlib import Path

from .config import DB_PATH
from .utils import iso_now
from .vault import _derive

_LOCK = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    doc_name   TEXT,
    doc_hash   TEXT,
    fmt        TEXT,
    counts     TEXT,
    rule_pack  TEXT,
    mode       TEXT,
    out_hash   TEXT,
    out_name   TEXT,
    prev_sig   TEXT,
    sig        TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _sign(fields: dict, prev_sig: str) -> str:
    key = _derive(b"audit")
    body = json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hmac.new(key, prev_sig.encode() + body, hashlib.sha256).hexdigest()


def append(*, job_id: str, doc_name: str, doc_hash: str, fmt: str,
           counts: dict, rule_pack: str, mode: str,
           out_hash: str = "", out_name: str = "") -> dict:
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute("SELECT sig FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row["sig"] if row else "GENESIS"
            ts = iso_now()
            fields = {
                "ts": ts, "job_id": job_id, "doc_name": doc_name,
                "doc_hash": doc_hash, "fmt": fmt, "counts": counts,
                "rule_pack": rule_pack, "mode": mode,
                "out_hash": out_hash, "out_name": out_name,
            }
            sig = _sign(fields, prev)
            cur = conn.execute(
                "INSERT INTO audit(ts,job_id,doc_name,doc_hash,fmt,counts,rule_pack,mode,out_hash,out_name,prev_sig,sig)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, job_id, doc_name, doc_hash, fmt, json.dumps(counts, ensure_ascii=False),
                 rule_pack, mode, out_hash, out_name, prev, sig),
            )
            conn.commit()
            return {"seq": cur.lastrowid, "ts": ts, "sig": sig[:16]}
        finally:
            conn.close()


def recent(limit: int = 50) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["counts"] = json.loads(d.get("counts") or "{}")
            except Exception:
                d["counts"] = {}
            d["sig"] = (d.get("sig") or "")[:16]
            d["prev_sig"] = (d.get("prev_sig") or "")[:16]
            out.append(d)
        return out
    finally:
        conn.close()


def verify_chain() -> dict:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM audit ORDER BY seq ASC").fetchall()
        prev = "GENESIS"
        bad = 0
        for r in rows:
            fields = {
                "ts": r["ts"], "job_id": r["job_id"], "doc_name": r["doc_name"],
                "doc_hash": r["doc_hash"], "fmt": r["fmt"],
                "counts": json.loads(r["counts"] or "{}"),
                "rule_pack": r["rule_pack"], "mode": r["mode"],
                "out_hash": r["out_hash"], "out_name": r["out_name"],
            }
            expect = _sign(fields, prev)
            if not hmac.compare_digest(expect, r["sig"] or ""):
                bad += 1
            prev = r["sig"] or ""
        return {"total": len(rows), "bad": bad, "ok": bad == 0}
    finally:
        conn.close()


def stats() -> dict:
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) c FROM audit").fetchone()["c"]
        last = conn.execute("SELECT ts FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        return {"records": total, "last": last["ts"] if last else None}
    finally:
        conn.close()
