# -*- coding: utf-8 -*-
"""L3 商业服务地址注入：环境变量优先，其次 commercial.json（数据目录等）。

只测 config 的解析顺序，不启动服务；子进程隔离避免污染已导入的 config 模块。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]       # Sanitized_Data/
APP_SERVER = PROJECT_ROOT / "app" / "server"
BASE = PROJECT_ROOT / "commercial" / "var" / "_test_commercial_cfg"
URL = "https://buy.example.test"
PY = sys.executable


def _run(env_extra: dict[str, str]) -> str:
    env = os.environ.copy()
    env.pop("YINZHAN_COMMERCIAL_URL", None)
    env["PYTHONPATH"] = str(APP_SERVER)
    env.update(env_extra)
    code = (
        "import json;"
        "from yinzhan import config;"
        "print(json.dumps({'url': config.COMMERCIAL_URL}, ensure_ascii=False))"
    )
    out = subprocess.check_output(
        [PY, "-X", "utf8", "-c", code], cwd=str(PROJECT_ROOT), env=env,
        stderr=subprocess.STDOUT,
    )
    return str(json.loads(out.decode("utf-8").strip().splitlines()[-1])["url"])


def _setup() -> Path:
    shutil.rmtree(BASE, ignore_errors=True)
    data_dir = BASE / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def test_env_has_priority_over_file():
    data_dir = _setup()
    (data_dir / "commercial.json").write_text(
        json.dumps({"commercial_url": "https://file.example.test"}), encoding="utf-8")
    assert _run({"YINZHAN_DATA_DIR": str(data_dir),
                 "YINZHAN_COMMERCIAL_URL": URL}) == URL


def test_commercial_json_in_data_dir():
    data_dir = _setup()
    (data_dir / "commercial.json").write_text(
        json.dumps({"commercial_url": URL}), encoding="utf-8")
    assert _run({"YINZHAN_DATA_DIR": str(data_dir)}) == URL


def test_commercial_json_with_utf8_bom():
    """Windows PowerShell 写出的 UTF-8 BOM 不能让配置被静默忽略。"""
    data_dir = _setup()
    payload = json.dumps({"commercial_url": URL}).encode("utf-8")
    (data_dir / "commercial.json").write_bytes(b"\xef\xbb\xbf" + payload)
    assert _run({"YINZHAN_DATA_DIR": str(data_dir)}) == URL


def test_invalid_scheme_ignored():
    data_dir = _setup()
    (data_dir / "commercial.json").write_text(
        json.dumps({"commercial_url": "ftp://bad.example.test"}), encoding="utf-8")
    assert _run({"YINZHAN_DATA_DIR": str(data_dir)}) == ""


def test_missing_file_is_empty():
    data_dir = _setup()
    assert _run({"YINZHAN_DATA_DIR": str(data_dir)}) == ""
