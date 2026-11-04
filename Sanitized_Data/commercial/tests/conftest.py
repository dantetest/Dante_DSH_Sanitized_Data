# -*- coding: utf-8 -*-
"""T1 商业服务测试夹具。

- 把 ``commercial/`` 与 ``app/server`` 加入 sys.path（也支持 `PYTHONPATH=commercial;app/server`）；
- 会话级生成 Ed25519 **测试**密钥（key_id=test-2026）并注册到产品公钥表，
  因此测试可以验证「签发 → 产品 LicenseService 导入为 paid」而不碰生产私钥；
- 每个用例独立临时数据目录（SQLite）+ 独立 FastAPI app/TestClient，限流互不影响；
- 临时目录放在仓库内 ``commercial/var/_pytest_tmp``（gitignore），不使用 pytest tmp_path：
  部分 Windows 沙箱环境下系统临时目录不可写。
"""
from __future__ import annotations

import base64
import itertools
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

COMMERCIAL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = COMMERCIAL_DIR.parent
APP_SERVER_DIR = PROJECT_ROOT / "app" / "server"
for _candidate in (str(COMMERCIAL_DIR), str(APP_SERVER_DIR)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

# 说明：本测试不使用 pytest 的 tmp_path（在部分 Windows 沙箱里系统临时目录/沙箱
# 创建的临时目录会不可写）。统一使用仓库内 gitignore 的 commercial/var/_pytest_tmp。
_TMP_BASE = COMMERCIAL_DIR / "var" / "_pytest_tmp"
_CASE_COUNTER = itertools.count(1)


def _new_session_tmp() -> Path:
    _TMP_BASE.mkdir(parents=True, exist_ok=True)
    root = _TMP_BASE / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    root.mkdir(parents=True, exist_ok=True)
    return root


#: 合法机器码（Crockford Base32 格式）
VALID_MACHINE_CODE = "ZH39-7N5C-98CD-A8GB"
#: 测试 key_id（测试私钥/公钥只存在于临时目录与内存）
TEST_KEY_ID = "test-2026"


@pytest.fixture(scope="session")
def session_tmp() -> Path:
    """会话级临时根目录（仓库内；结束后尽力清理）。"""
    root = _new_session_tmp()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def tmp_dir(session_tmp) -> Path:
    """用例级临时目录（替代 tmp_path）。"""
    path = session_tmp / f"case-{next(_CASE_COUNTER):03d}"
    path.mkdir(parents=True, exist_ok=True)
    return path



@pytest.fixture(scope="session", autouse=True)
def _isolated_process_env():
    """隔离进程环境：测试私钥未加密，不能继承生产口令。"""
    patch = pytest.MonkeyPatch()
    patch.delenv("LICENSE_KEY_PASSWORD", raising=False)
    yield
    patch.undo()


@pytest.fixture(scope="session")
def test_license_key(session_tmp) -> dict:
    """生成会话级 Ed25519 测试密钥（私钥写临时目录，公钥注册给产品校验）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from yinzhan.licensing import keys

    key = Ed25519PrivateKey.generate()
    key_dir = session_tmp / "license_keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    pem_path = key_dir / "license_signing_ed25519.pem"
    pem_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    keys.add_public_key(TEST_KEY_ID, public_raw)
    return {
        "path": str(pem_path),
        "key_id": TEST_KEY_ID,
        "public_raw_b64": base64.b64encode(public_raw).decode("ascii"),
    }


@pytest.fixture()
def valid_machine_code() -> str:
    return VALID_MACHINE_CODE


@pytest.fixture(scope="session")
def real_machine_code() -> str:
    """本机真实机器码（无连字符）——用于验证授权可被产品 LicenseService 导入。"""
    from yinzhan.licensing.machine import machine_info

    return machine_info()["code"]


@pytest.fixture()
def make_config(tmp_dir, test_license_key):
    """构造测试 Config；可用 ``make_config(PAYMENT_PROVIDER="alipay")`` 覆盖任意项。"""
    from server.config import Config

    def _make(**overrides):
        values: dict[str, str] = {
            "COMMERCIAL_HOST": "127.0.0.1",
            "COMMERCIAL_PORT": "8787",
            "COMMERCIAL_BASE_URL": "http://127.0.0.1:8787",
            "COMMERCIAL_DATA_DIR": str(tmp_dir / "data"),
            "COMMERCIAL_DB": str(tmp_dir / "data" / "commercial.db"),
            "COMMERCIAL_CORS_EXTRA_ORIGINS": "https://buy.example.com",
            "PAYMENT_PROVIDER": "mock",
            "LICENSE_PRIVATE_KEY_PATH": test_license_key["path"],
            "LICENSE_KEY_ID": test_license_key["key_id"],
            "COMMERCIAL_LOG_LEVEL": "WARNING",
        }
        for key, value in overrides.items():
            values[str(key).upper()] = "" if value is None else str(value)
        return Config(env={}, overrides=values)

    return _make


@pytest.fixture()
def app(make_config):
    from server.app import create_app

    return create_app(make_config())


@pytest.fixture()
def config(app):
    return app.state.config


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def create_order(client):
    """下单辅助：默认 pro / alipay / 合法机器码；返回响应 JSON。"""

    def _create(**overrides):
        payload = {
            "sku": "pro",
            "machine_code": VALID_MACHINE_CODE,
            "channel": "alipay",
        }
        payload.update(overrides)
        response = client.post("/api/orders", json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    return _create


@pytest.fixture()
def license_service(tmp_dir):
    """产品 LicenseService（临时数据目录；测试公钥已注册）。"""
    from yinzhan.licensing.service import LicenseService

    return LicenseService(data_dir=tmp_dir / "yz-license-data")


@pytest.fixture()
def db_query(config):
    import sqlite3

    def _query(sql: str, params: tuple = ()) -> list[dict]:
        conn = sqlite3.connect(str(config.db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    return _query


@pytest.fixture()
def db_count(config, db_query):
    def _count(table: str) -> int:
        return int(db_query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])

    return _count


@pytest.fixture()
def db_exec(config):
    import sqlite3

    def _exec(sql: str, params: tuple = ()) -> None:
        conn = sqlite3.connect(str(config.db_path))
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    return _exec
