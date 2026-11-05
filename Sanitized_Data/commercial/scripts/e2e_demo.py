#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 付费解锁 · 真实 HTTP 端到端验收 / 演示脚本（T4）。

覆盖 ``commercial/CONTRACT.md`` §8 的端到端验收项：

1. **测试密钥**：调用 ``gen_test_keys.py``（幂等，缺则生成；不轮换已有密钥）；
2. **mock 全链路**（真实 HTTP，两个独立进程）：
   商业服务（``run_dev.py --provider mock``，端口 8788） + 本机 app
   （``app/server/run.py``，端口 8799，测试数据目录 + ``YINZHAN_PUBLIC_KEYS_PATH``
   测试公钥 + ``YINZHAN_COMMERCIAL_URL``）：
   下单 → ``qr_svg`` → 模拟支付 → 拿 ``license.text`` → 本机 ``/api/license/import``
   → ``state=paid`` / ``edition=专业版``；订单幂等（重复支付/查询同一 license）；
   外加 CORS 预检（允许本机 Origin / 拒绝无关 Origin）、token 错误 404、过期订单不可支付；
3. **支付宝异步通知**（真实 HTTP）：第二个商业服务
   ``--provider alipay``（端口 8789，page 模式，测试 RSA 密钥）：
   page 下单返回 ``pay_url`` → 用**测试「支付宝私钥」独立实现**的 RSA2 签名
   （不调用被测服务的签名函数，避免自签自验）→ ``POST /notify/alipay``：
   - 篡改金额/签名 → ``failure`` 且订单仍 ``pending``；
   - ``WAIT_BUYER_PAY`` → ``success`` 且不签发；
   - ``TRADE_SUCCESS`` → ``success`` + 订单 paid + license；
   - 重复通知 → ``success`` 且不重复签发（DB payments/licenses 各 1 条）；
4. 支付宝通道签发的 license 同样可被本机 app 导入解锁（跨通道验证）。

设计约定：
* 全部子进程数据/日志都放在仓库内 ``commercial/var/_e2e_tmp/``（gitignore），
  不使用系统临时目录（Windows 受限沙箱下可能不可写）；正常/异常都清理子进程与临时目录；
* 端口优先 8788/8789/8799，被占用时自动顺延（范围 8788-8839，避开 8787）；
* 不打印订单 token / license 全文 / 任何私钥；只打印订单号、license_id、指纹等 QA 证据；
* 退出码：全部检查点 PASS → 0；任一失败 → 1；环境/准备失败 → 1。

单独运行::

    python -X utf8 commercial/scripts/e2e_demo.py
    python -X utf8 commercial/scripts/e2e_demo.py --keep-tmp     # 保留临时目录排障
    python -X utf8 commercial/scripts/e2e_demo.py --json         # 机器可读摘要

pytest 复用：``commercial/tests/test_e2e_mock.py`` 会调用 :func:`run_e2e` 并逐项断言。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT_DIR = Path(__file__).resolve().parent
#: commercial/ 目录
COMMERCIAL_DIR = SCRIPT_DIR.parent
#: Sanitized_Data/ 项目根目录
PROJECT_ROOT = COMMERCIAL_DIR.parent
#: 商业服务开发启动脚本
RUN_DEV = COMMERCIAL_DIR / "scripts" / "run_dev.py"
#: 本机 app 启动入口
APP_SERVER_RUN = PROJECT_ROOT / "app" / "server" / "run.py"
#: 端到端临时目录根（仓库内，gitignore；不用系统 tmp）
TMP_ROOT = COMMERCIAL_DIR / "var" / "_e2e_tmp"
#: 本机 app 测试公钥覆盖文件（由 gen_test_keys 生成）
TEST_KEYS_DIR = COMMERCIAL_DIR / "var" / "test_keys"
#: 签名测试 keyring（e2e 专用；test core 公钥只通过源码模式注入）
TEST_KEYRING = TEST_KEYS_DIR / "public_keys_test.signed.json"
TEST_CORE_PRIVATE = TEST_KEYS_DIR / "test_core_signing_ed25519.pem"
TEST_CORE_PUBLIC = TEST_KEYS_DIR / "test_core_public.b64"


def _ensure_test_keyring() -> dict[str, str]:
    """生成/复用 test core 密钥并签一个测试 keyring。

    仅用于源码 e2e；生产包不识别该注入。
    """
    import datetime

    license_public = json.loads(
        (TEST_KEYS_DIR / "license_public_keys_test.json").read_text(encoding="utf-8")
    )["test-2026"]
    if TEST_KEYRING.is_file() and TEST_CORE_PUBLIC.is_file() and TEST_CORE_PRIVATE.is_file():
        return {
            "path": str(TEST_KEYRING),
            "core_public_b64": TEST_CORE_PUBLIC.read_text(encoding="ascii").strip(),
        }
    TEST_KEYS_DIR.mkdir(parents=True, exist_ok=True)
    core = Ed25519PrivateKey.generate()
    TEST_CORE_PRIVATE.write_bytes(core.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    try:
        os.chmod(TEST_CORE_PRIVATE, 0o600)
    except Exception:
        pass
    core_public_b64 = base64.b64encode(core.public_key().public_bytes_raw()).decode("ascii")
    TEST_CORE_PUBLIC.write_text(core_public_b64 + "\n", encoding="ascii")
    payload = {
        "schema": 2,
        "root_key_id": "test-core",
        "keyring_revision": 1,
        "issued_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "keys": {"test-2026": license_public},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    payload["sig"] = base64.b64encode(core.sign(raw)).decode("ascii")
    TEST_KEYRING.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return {"path": str(TEST_KEYRING), "core_public_b64": core_public_b64}

# 让 ``import gen_test_keys`` 在「直接运行」与「pytest 导入」两种方式下都可用
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gen_test_keys import ensure_test_keys  # noqa: E402

#: 允许的机器码格式（用于断言，不生成）
#: 本机 app 的 API 端口范围与商业服务默认端口（见 CONTRACT §2 / 产品 config.py）
DEFAULT_MOCK_PORT = 8788
DEFAULT_ALIPAY_PORT = 8789
DEFAULT_APP_PORT = 8799
PORT_SCAN_RANGE = (8788, 8840)
HEALTH_TIMEOUT_S = 90.0
HTTP_TIMEOUT_S = 15.0

#: 稳定的检查点 key（pytest 依赖这些 key）
CHECKPOINT_KEYS = (
    "keys",
    "mock_health",
    "app_health",
    "local_status",
    "purchase_page",
    "catalog",
    "order_create",
    "mock_pay",
    "order_idempotent",
    "local_import",
    "local_status_paid",
    "cors_allowed",
    "cors_denied",
    "token_404",
    "order_expired",
    "alipay_health",
    "alipay_order_page",
    "alipay_mock_disabled",
    "notify_amount_tampered",
    "notify_wait_status",
    "notify_success_paid",
    "notify_duplicate",
    "alipay_license_import",
)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _norm_code(value: Any) -> str:
    """机器码归一化（去连字符、大写），只用于比较展示形式。"""
    return str(value or "").replace("-", "").strip().upper()


def _yuan(amount_cents: int) -> str:
    """int 分 → 支付宝 ``total_amount`` 元字符串（如 49900 → "499.00"）。"""
    return f"{int(amount_cents) // 100}.{int(amount_cents) % 100:02d}"


def _port_bindable(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, int(port)))
            return True
        except OSError:
            return False


def _choose_port(preferred: list[int], used: set[int]) -> int:
    """优先用 preferred，其次在 PORT_SCAN_RANGE 里找一个当前空闲端口。"""
    candidates: list[int] = []
    for port in preferred:
        if port not in used and port not in candidates:
            candidates.append(int(port))
    for port in range(*PORT_SCAN_RANGE):
        if port not in used and port not in candidates:
            candidates.append(port)
    for port in candidates:
        if _port_bindable("127.0.0.1", port):
            return port
    raise RuntimeError("找不到空闲端口（8788-8839 都被占用？）")


def _header(headers: dict[str, str], name: str) -> str:
    target = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == target:
            return str(value)
    return ""


# ---------------------------------------------------------------------------
# HTTP 客户端（stdlib，跨平台；错误响应也返回而不抛）
# ---------------------------------------------------------------------------
def http_call(method: str, url: str, *, json_body: Any = None,
              form_body: dict[str, str] | None = None,
              headers: dict[str, str] | None = None,
              timeout: float = HTTP_TIMEOUT_S) -> tuple[int, dict[str, str], str]:
    """发请求，返回 ``(status, headers, text)``；HTTP 4xx/5xx 不抛异常。"""
    data: bytes | None = None
    request_headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    if form_body is not None:
        data = urllib.parse.urlencode(form_body, doseq=True).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return int(resp.status), dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也是合法响应（契约用错误体）
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return int(exc.code), dict(exc.headers or {}), body


def http_json(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
    status, headers, text = http_call(method, url, **kwargs)
    try:
        payload = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        payload = None
    return status, headers, payload


def _wait_health(child: "ChildProcess", url: str, deadline_s: float) -> dict[str, Any]:
    """轮询 <url> 直到 200 + JSON；子进程提前退出/超时则抛错（带日志尾部）。"""
    deadline = time.monotonic() + deadline_s
    last_error = "尚未响应"
    while time.monotonic() < deadline:
        if child.proc is not None and child.proc.poll() is not None:
            raise RuntimeError(
                f"{child.name} 进程提前退出（exit={child.proc.returncode}）。日志尾部：\n{child.tail()}")
        try:
            status, _headers, text = http_call("GET", url, timeout=3.0)
            if status == 200:
                payload = json.loads(text)
                if isinstance(payload, dict) and payload.get("ok"):
                    return payload
                last_error = f"响应缺少 ok=true：{text[:200]}"
            else:
                last_error = f"HTTP {status}"
        except Exception as exc:  # noqa: BLE001 - 服务尚未就绪属正常
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.2)
    raise RuntimeError(f"等待 {url} 超时（{deadline_s:.0f}s）：{last_error}\n{child.tail()}")


# ---------------------------------------------------------------------------
# 子进程管理
# ---------------------------------------------------------------------------
class ChildProcess:
    """后台子进程：stdout/stderr 落日志文件（不用管道，避免死锁/沙箱命名管道问题）。"""

    def __init__(self, name: str, args: list[str], cwd: Path, env: dict[str, str],
                 log_path: Path) -> None:
        self.name = name
        self.args = [str(item) for item in args]
        self.cwd = Path(cwd)
        self.env = env
        self.log_path = Path(log_path)
        self.proc: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None

    def start(self) -> "ChildProcess":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = open(self.log_path, "wb")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            self.args,
            cwd=str(self.cwd),
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        return self

    @property
    def pid(self) -> int:
        return int(self.proc.pid) if self.proc is not None else -1

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def tail(self, lines: int = 25) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"<无法读取日志 {self.log_path}：{exc}>"
        return "\n".join(text.splitlines()[-lines:])

    def stop(self, timeout: float = 8.0) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None


def _child_env(extra: dict[str, str]) -> dict[str, str]:
    """构造子进程环境：先剔除会串味的本机/密钥变量，再叠加显式配置。

    测试密钥无口令，绝不能继承生产 ``LICENSE_KEY_PASSWORD``。
    """
    env = dict(os.environ)
    for name in (
        "LICENSE_KEY_PASSWORD",
        "LICENSE_PRIVATE_KEY_PATH",
        "LICENSE_KEY_ID",
        "LICENSE_KEY_PASSWORD_ENV",
        "COMMERCIAL_DATA_DIR",
        "COMMERCIAL_DB",
        "COMMERCIAL_BASE_URL",
        "COMMERCIAL_CORS_EXTRA_ORIGINS",
        "COMMERCIAL_TRUST_PROXY",
        "COMMERCIAL_HOST",
        "COMMERCIAL_PORT",
        "PAYMENT_PROVIDER",
        "PAYMENT_MOCK_ENABLED",
        "COMMERCIAL_SELF_SERVE_PRO_ENABLED",
        "ALIPAY_APP_ID",
        "ALIPAY_APP_PRIVATE_KEY_PATH",
        "ALIPAY_PUBLIC_KEY_PATH",
        "ALIPAY_PAY_MODE",
        "ALIPAY_APP_ID_CHECK",
        "ALIPAY_NOTIFY_URL",
        "ALIPAY_SELLER_ID",
        "ALIPAY_GATEWAY",
        "YINZHAN_DATA_DIR",
        "YINZHAN_PUBLIC_KEYS_PATH",
        "YINZHAN_TEST_KEYRING_PATH",
        "YINZHAN_TEST_CORE_PUBLIC_KEY",
        "YINZHAN_COMMERCIAL_URL",
        "YINZHAN_PAYWALL",
        "PYTHONPATH",
    ):
        env.pop(name, None)
    env.update({str(k): str(v) for k, v in extra.items()})
    return env


# ---------------------------------------------------------------------------
# 支付宝异步通知签名（**独立实现**，不调用被测服务代码）
# ---------------------------------------------------------------------------
def _rsa2_sign_params(params: dict[str, str], private_key: object) -> str:
    """按支付宝 RSA2 规则：剔除 sign/sign_type、空值不参与、key 字典序、k=v&...。"""
    parts: list[str] = []
    for key in sorted(params):
        if key in ("sign", "sign_type"):
            continue
        value = params[key]
        if value is None or str(value) == "":
            continue
        parts.append(f"{key}={value}")
    content = "&".join(parts)
    signature = private_key.sign(  # type: ignore[attr-defined]
        content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


def _load_rsa_private(path: Path) -> Any:
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def _build_notify_form(*, order_no: str, amount_cents: int, trade_no: str,
                       app_id: str, trade_status: str = "TRADE_SUCCESS",
                       total_amount: str | None = None) -> dict[str, str]:
    """构造支付宝异步通知表单（未签名）。字段覆盖官方 notify 的常见公共字段。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "notify_time": now,
        "notify_type": "trade_status_sync",
        "notify_id": f"e2e_{int(time.time() * 1000)}_{trade_no[-6:]}",
        "app_id": app_id,
        "charset": "utf-8",
        "version": "1.0",
        "sign_type": "RSA2",
        "trade_no": trade_no,
        "out_trade_no": order_no,
        "total_amount": total_amount if total_amount is not None else _yuan(amount_cents),
        "trade_status": trade_status,
        "seller_id": "2088000000000001",
        "buyer_logon_id": "buyer***@example.com",
    }


def _sign_notify(form: dict[str, str], private_key: Any) -> dict[str, str]:
    signed = dict(form)
    signed["sign"] = _rsa2_sign_params(form, private_key)
    return signed


def _post_notify(base_url: str, form: dict[str, str]) -> tuple[int, str]:
    status, _headers, text = http_call("POST", f"{base_url}/notify/alipay", form_body=form)
    return status, (text or "").strip()


# ---------------------------------------------------------------------------
# 检查点 / 报告
# ---------------------------------------------------------------------------
@dataclass
class Checkpoint:
    key: str
    title: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


@dataclass
class E2EReport:
    results: list[Checkpoint] = field(default_factory=list)
    tmp_dir: Path | None = None
    ports: dict[str, int] = field(default_factory=dict)
    services: list[ChildProcess] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    tmp_removed: bool = False
    started_at: float = field(default_factory=time.time)

    def checkpoint(self, key: str) -> Checkpoint | None:
        for item in self.results:
            if item.key == key:
                return item
        return None

    def passed_all(self) -> bool:
        return bool(self.results) and all(item.ok for item in self.results) and not self.error

    def failed(self) -> list[Checkpoint]:
        return [item for item in self.results if not item.ok]

    @property
    def duration_s(self) -> float:
        return time.time() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.passed_all(),
            "error": self.error,
            "duration_s": round(self.duration_s, 2),
            "ports": dict(self.ports),
            "tmp_dir": str(self.tmp_dir) if self.tmp_dir else None,
            "tmp_removed": self.tmp_removed,
            "checkpoints": [
                {"key": item.key, "title": item.title, "ok": item.ok,
                 "detail": item.detail, "seconds": round(item.seconds, 3)}
                for item in self.results
            ],
            "artifacts": dict(self.artifacts),
        }


class CheckpointFailed(RuntimeError):
    """某个检查点断言失败（已记录到报告，抛出让主流程尽快停止）。"""


def _check(report: E2EReport, key: str, title: str,
           func: Callable[[], str | None]) -> Any:
    """执行一个检查点：PASS 打印日志；失败记录并抛 :class:`CheckpointFailed`。"""
    started = time.monotonic()
    try:
        detail = func() or ""
    except Exception as exc:  # noqa: BLE001 - 统一记录，避免半途静默
        seconds = time.monotonic() - started
        report.results.append(Checkpoint(key, title, False,
                                         f"{type(exc).__name__}: {exc}", seconds))
        print(f"[FAIL] {title}\n         {type(exc).__name__}: {exc}")
        raise CheckpointFailed(title) from exc
    seconds = time.monotonic() - started
    detail = str(detail)
    report.results.append(Checkpoint(key, title, True, detail, seconds))
    suffix = f"  — {detail}" if detail else ""
    print(f"[PASS] {title}{suffix}")
    return detail


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# 服务启动
# ---------------------------------------------------------------------------
def _start_mock_service(report: E2EReport, tmp_dir: Path, port: int,
                        keys: dict[str, Any]) -> str:
    data_dir = tmp_dir / "mock_data"
    db_path = data_dir / "commercial.db"
    env = _child_env({
        "LICENSE_PRIVATE_KEY_PATH": keys["paths"]["license_private"],
        "LICENSE_KEY_ID": keys["key_id"],
        "LICENSE_KEY_PASSWORD_ENV": "LICENSE_KEY_PASSWORD",
        "COMMERCIAL_CORS_EXTRA_ORIGINS": "",
        "COMMERCIAL_LOG_LEVEL": "WARNING",
        # e2e 覆盖专业版自助下单；生产成本配置仍只开个人版
        "COMMERCIAL_SELF_SERVE_PRO_ENABLED": "true",
    })
    child = ChildProcess(
        "commercial-mock",
        [sys.executable, "-X", "utf8", str(RUN_DEV),
         "--provider", "mock", "--host", "127.0.0.1", "--port", str(port),
         "--data-dir", str(data_dir), "--db", str(db_path),
         "--base-url", f"http://127.0.0.1:{port}", "--log-level", "warning"],
        PROJECT_ROOT, env, tmp_dir / "logs" / "commercial_mock.log",
    ).start()
    report.services.append(child)
    _wait_health(child, f"http://127.0.0.1:{port}/healthz", HEALTH_TIMEOUT_S)
    report.artifacts["mock_db"] = str(db_path)
    return f"http://127.0.0.1:{port}"


def _start_alipay_service(report: E2EReport, tmp_dir: Path, port: int,
                          keys: dict[str, Any]) -> str:
    data_dir = tmp_dir / "alipay_data"
    db_path = data_dir / "commercial.db"
    base = f"http://127.0.0.1:{port}"
    env = _child_env({
        "LICENSE_PRIVATE_KEY_PATH": keys["paths"]["license_private"],
        "LICENSE_KEY_ID": keys["key_id"],
        "LICENSE_KEY_PASSWORD_ENV": "LICENSE_KEY_PASSWORD",
        "COMMERCIAL_CORS_EXTRA_ORIGINS": "",
        "COMMERCIAL_LOG_LEVEL": "WARNING",
        "COMMERCIAL_SELF_SERVE_PRO_ENABLED": "true",
        # T2 支付宝适配器（测试密钥；page 模式只本地签名拼 URL，不访问网关）
        "PAYMENT_PROVIDER": "alipay",
        "PAYMENT_MOCK_ENABLED": "false",
        "ALIPAY_APP_ID": "test-app",
        "ALIPAY_APP_PRIVATE_KEY_PATH": keys["paths"]["app_private"],
        "ALIPAY_PUBLIC_KEY_PATH": keys["paths"]["alipay_public"],
        "ALIPAY_GATEWAY": "https://openapi-sandbox.dl.alipaydev.com/gateway.do",
        "ALIPAY_PAY_MODE": "page",
        "ALIPAY_NOTIFY_URL": f"{base}/notify/alipay",
        "ALIPAY_SELLER_ID": "",
        "ALIPAY_APP_ID_CHECK": "1",
    })
    child = ChildProcess(
        "commercial-alipay",
        [sys.executable, "-X", "utf8", str(RUN_DEV),
         "--provider", "alipay", "--host", "127.0.0.1", "--port", str(port),
         "--data-dir", str(data_dir), "--db", str(db_path),
         "--base-url", base, "--log-level", "warning"],
        PROJECT_ROOT, env, tmp_dir / "logs" / "commercial_alipay.log",
    ).start()
    report.services.append(child)
    health = _wait_health(child, f"{base}/healthz", HEALTH_TIMEOUT_S)
    _require(health.get("provider") == "alipay",
             f"/healthz provider 应为 alipay，实际 {health.get('provider')!r}")
    report.artifacts["alipay_db"] = str(db_path)
    return base


def _start_local_app(report: E2EReport, tmp_dir: Path, port: int,
                     keys: dict[str, Any], commercial_url: str) -> tuple[str, dict[str, Any]]:
    data_dir = tmp_dir / "app_data"
    test_keyring = _ensure_test_keyring()
    env = _child_env({
        "YINZHAN_DATA_DIR": str(data_dir),
        "YINZHAN_TEST_KEYRING_PATH": test_keyring["path"],
        "YINZHAN_TEST_CORE_PUBLIC_KEY": test_keyring["core_public_b64"],
        "YINZHAN_COMMERCIAL_URL": commercial_url,
        "YINZHAN_PAYWALL": "1",
    })
    child = ChildProcess(
        "local-app",
        [sys.executable, "-X", "utf8", str(APP_SERVER_RUN),
         "--port", str(port), "--host", "127.0.0.1", "--no-browser", "--no-ocr"],
        PROJECT_ROOT, env, tmp_dir / "logs" / "local_app.log",
    ).start()
    report.services.append(child)

    runtime_path = data_dir / "runtime.json"
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    last_error = "runtime.json 尚未生成"
    while time.monotonic() < deadline:
        if child.proc is not None and child.proc.poll() is not None:
            raise RuntimeError(
                f"本机 app 进程提前退出（exit={child.proc.returncode}）。日志尾部：\n{child.tail()}")
        if runtime_path.is_file():
            try:
                runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                last_error = f"runtime.json 解析失败：{exc}"
            else:
                actual_port = int(runtime.get("port") or 0)
                if actual_port:
                    base = f"http://127.0.0.1:{actual_port}"
                    try:
                        status, _headers, text = http_call("GET", f"{base}/api/health", timeout=3.0)
                        if status == 200:
                            health = json.loads(text)
                            if isinstance(health, dict) and health.get("ok"):
                                return base, health
                        last_error = f"/api/health HTTP {status}"
                    except Exception as exc:  # noqa: BLE001
                        last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.2)
    raise RuntimeError(f"等待本机 app 就绪超时：{last_error}\n{child.tail()}")


# ---------------------------------------------------------------------------
# SQLite 只读/测试辅助
# ---------------------------------------------------------------------------
def _db_rows(db_path: str | Path, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        return list(conn.execute(sql, params).fetchall())
    finally:
        conn.close()


def _db_exec(db_path: str | Path, sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_e2e(*, keep_tmp: bool = False, verbose: bool = True) -> E2EReport:
    """执行全部端到端检查点，返回 :class:`E2EReport`（不抛异常；失败看 report）。"""
    _reconfigure_stdio()
    report = E2EReport()
    tmp_dir = TMP_ROOT / f"e2e-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    report.tmp_dir = tmp_dir

    def log(text: str) -> None:
        if verbose:
            print(text)

    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # ── 0) 测试密钥 ────────────────────────────────────────────────
        def step_keys() -> str:
            keys = ensure_test_keys(quiet=True)
            report.artifacts["key_id"] = keys["key_id"]
            report.artifacts["fingerprints"] = dict(keys["fingerprints"])
            return (f"key_id={keys['key_id']} "
                    f"license公钥指纹={keys['fingerprints'].get('license_ed25519', '?')[:23]}…")
        _check(report, "keys", "生成/复用测试密钥（Ed25519 test-2026 + 两对 RSA）", step_keys)
        keys_report = ensure_test_keys(quiet=True)
        report.artifacts["test_keys_dir"] = keys_report["out_dir"]

        # ── 端口 ──────────────────────────────────────────────────────
        mock_port = _choose_port([DEFAULT_MOCK_PORT, 8790, 8792, 8794, 8796, 8800], set())
        alipay_port = _choose_port([DEFAULT_ALIPAY_PORT, 8791, 8793, 8795, 8797, 8802],
                                   {mock_port})
        app_port = _choose_port([DEFAULT_APP_PORT, 8801, 8803, 8805, 8807, 8809],
                                {mock_port, alipay_port})
        report.ports = {"mock": mock_port, "alipay": alipay_port, "app": app_port}
        log("=" * 78)
        log("L3 付费解锁 · 真实 HTTP 端到端验收")
        log(f"  临时目录 : {tmp_dir}")
        log(f"  端口     : 商业 mock={mock_port} / 商业 alipay={alipay_port} / 本机 app={app_port}")
        log("=" * 78)

        # ── 1) 商业服务（mock） ───────────────────────────────────────
        def step_mock_health() -> str:
            base = _start_mock_service(report, tmp_dir, mock_port, keys_report)
            report.artifacts["mock_base"] = base
            return f"{base}/healthz provider=mock"
        _check(report, "mock_health", "启动商业服务（mock）并等待 /healthz", step_mock_health)
        mock_base = report.artifacts["mock_base"]

        # ── 2) 本机 app ───────────────────────────────────────────────
        def step_app_health() -> str:
            base, health = _start_local_app(report, tmp_dir, app_port, keys_report, mock_base)
            report.artifacts["app_base"] = base
            report.artifacts["app_health"] = {
                "version": health.get("version"), "net": health.get("net"),
            }
            return f"{base}/api/health version={health.get('version')} net={health.get('net')}"
        _check(report, "app_health", "启动本机 app（--no-browser --no-ocr）并等待 /api/health",
               step_app_health)
        app_base = report.artifacts["app_base"]
        _require(report.artifacts["app_health"].get("net") == "blocked",
                 f"本机 app /api/health net 应为 blocked（引擎零外呼），"
                 f"实际 {report.artifacts['app_health'].get('net')!r}")

        # ── 3) 本机 status：机器码 + commercial_url ────────────────────
        def step_local_status() -> str:
            status, _headers, payload = http_json("GET", f"{app_base}/api/license/status")
            _require(status == 200 and isinstance(payload, dict), f"/api/license/status HTTP {status}")
            machine = str(payload.get("machine_display") or payload.get("machine_code") or "")
            _require(bool(machine), "status 缺少 machine_code/machine_display")
            _require(_norm_code(machine) != "", "机器码为空")
            _require(str(payload.get("commercial_url") or "") == mock_base,
                     f"status.commercial_url={payload.get('commercial_url')!r} 应等于 {mock_base!r}")
            _require("purchase_url" in payload, "status 缺少 purchase_url 兜底字段")
            _require(payload.get("state") == "free", f"全新数据目录初始 state 应为 free，实际 {payload.get('state')!r}")
            report.artifacts["machine_code"] = machine
            return f"machine_code={machine} state=free commercial_url={mock_base}"
        _check(report, "local_status", "本机 /api/license/status 提供机器码/commercial_url",
               step_local_status)
        machine_code = report.artifacts["machine_code"]

        # ── 4) 本机 /purchase 页面 ────────────────────────────────────
        def step_purchase_page() -> str:
            status, _headers, html = http_call("GET", f"{app_base}/purchase")
            _require(status == 200, f"GET /purchase HTTP {status}")
            for needle in ("purchase.js", "purchase.css", 'id="skuList"',
                           'id="qrBox"', "购买入口尚未配置"):
                _require(needle in html, f"/purchase 页面缺少 {needle!r}")
            status2, _headers2, _html2 = http_call("GET", f"{app_base}/purchase?sku=pro")
            _require(status2 == 200, f"GET /purchase?sku=pro HTTP {status2}")
            return "GET /purchase 200（含 purchase.js/css、SKU 容器、未配置兜底文案）"
        _check(report, "purchase_page", "本机购买页 /purchase 可访问（200，纯本地资源）",
               step_purchase_page)

        # ── 5) 商业服务 catalog ───────────────────────────────────────
        def step_catalog() -> str:
            status, _headers, catalog = http_json("GET", f"{mock_base}/api/catalog")
            _require(status == 200 and isinstance(catalog, dict), f"GET /api/catalog HTTP {status}")
            _require(catalog.get("ok") is True, "catalog.ok 应为 true")
            _require(catalog.get("provider") == "mock", f"catalog.provider 应为 mock，实际 {catalog.get('provider')!r}")
            skus = {item.get("id"): item for item in catalog.get("skus") or []}
            _require("personal" in skus and "pro" in skus, f"catalog.skus 缺少 personal/pro：{sorted(skus)}")
            _require(int(skus["pro"].get("price_cents") or 0) == 49900,
                     f"pro 价格应为 49900 分，实际 {skus['pro'].get('price_cents')!r}")
            channels = {item.get("id"): item for item in catalog.get("channels") or []}
            _require(bool(channels.get("alipay", {}).get("enabled")), "alipay 通道应 enabled")
            _require(catalog.get("mock_available") is True, "mock_available 应为 true")
            return "provider=mock，pro=49900 分，alipay/mock 通道可用"
        _check(report, "catalog", "商业服务 /api/catalog（SKU/价格/通道）", step_catalog)

        # ── 6) 下单（mock 通道） ──────────────────────────────────────
        def step_order_create() -> str:
            status, _headers, order = http_json("POST", f"{mock_base}/api/orders", json_body={
                "sku": "pro", "machine_code": machine_code, "channel": "alipay",
            })
            _require(status == 200 and isinstance(order, dict), f"下单 HTTP {status}: {order}")
            _require(order.get("ok") is True, "下单 ok 应为 true")
            _require(isinstance(order.get("order_no"), str) and order["order_no"].startswith("ord_"),
                     f"order_no 形态不对：{order.get('order_no')!r}")
            _require("<svg" in str(order.get("qr_svg") or ""), "qr_svg 应包含 <svg")
            _require(bool(order.get("order_token")), "缺少 order_token")
            _require(int(order.get("amount_cents") or 0) == 49900,
                     f"amount_cents 应为 49900，实际 {order.get('amount_cents')!r}")
            _require(order.get("mock_available") is True, "mock_available 应为 true")
            _require(bool(order.get("expires_at")), "缺少 expires_at")
            report.artifacts.update({
                "order_no": order["order_no"],
                "order_token": order["order_token"],
                "amount_cents": int(order["amount_cents"]),
            })
            return (f"order_no={order['order_no']} amount={order['amount_cents']}分 "
                    f"qr_svg={len(order['qr_svg'])}B")
        _check(report, "order_create", "POST /api/orders（pro，qr_svg/order_token）",
               step_order_create)
        order_no = report.artifacts["order_no"]
        order_token = report.artifacts["order_token"]
        amount_cents = report.artifacts["amount_cents"]

        # ── 7) mock 支付 → license ────────────────────────────────────
        def step_mock_pay() -> str:
            status, _headers, paid = http_json("POST", f"{mock_base}/api/mock/pay", json_body={
                "order_no": order_no, "token": order_token,
            })
            _require(status == 200 and isinstance(paid, dict), f"模拟支付 HTTP {status}: {paid}")
            _require(paid.get("status") == "paid", f"支付后 status 应为 paid，实际 {paid.get('status')!r}")
            license_data = paid.get("license")
            _require(isinstance(license_data, dict), "支付响应缺少 license")
            text = str(license_data.get("text") or "")
            _require(bool(text), "license.text 为空")
            _require("\n" not in text, "license.text 应为单行 JSON")
            container = json.loads(text)
            _require({"schema", "payload", "sig"} <= set(container), "license 容器缺少 schema/payload/sig")
            _require(container.get("alg") in ("ed25519", "Ed25519"), f"license.alg 异常：{container.get('alg')!r}")
            _require(container.get("key_id") == "test-2026",
                     f"license.key_id 应为 test-2026，实际 {container.get('key_id')!r}")
            report.artifacts.update({
                "license_id": str(license_data.get("license_id")),
                "license_text": text,
                "license_expires_at": license_data.get("expires_at"),
            })
            return (f"status=paid license_id={license_data.get('license_id')} "
                    f"edition={license_data.get('edition')} text={len(text)}B")
        _check(report, "mock_pay", "POST /api/mock/pay → paid + license.text（单行 JSON）",
               step_mock_pay)
        license_id = report.artifacts["license_id"]
        license_text = report.artifacts["license_text"]

        # ── 8) 订单查询/重复支付幂等 ──────────────────────────────────
        def step_order_idempotent() -> str:
            status, _headers, view = http_json(
                "GET", f"{mock_base}/api/orders/{order_no}?token={urllib.parse.quote(order_token)}")
            _require(status == 200 and isinstance(view, dict), f"GET 订单 HTTP {status}")
            _require(view.get("status") == "paid", f"查询 status 应为 paid，实际 {view.get('status')!r}")
            _require((view.get("license") or {}).get("license_id") == license_id,
                     "GET 订单返回的 license_id 与支付响应不一致")
            # 重复点击「模拟支付」：幂等，返回同一 license，不重复入账
            status2, _headers2, again = http_json("POST", f"{mock_base}/api/mock/pay", json_body={
                "order_no": order_no, "token": order_token,
            })
            _require(status2 == 200 and isinstance(again, dict), f"重复模拟支付 HTTP {status2}")
            _require((again.get("license") or {}).get("license_id") == license_id,
                     "重复模拟支付返回了不同的 license_id（幂等被破坏）")
            payments = _db_rows(report.artifacts["mock_db"],
                                "SELECT COUNT(*) FROM payments WHERE order_no=?", (order_no,))[0][0]
            licenses = _db_rows(report.artifacts["mock_db"],
                                "SELECT COUNT(*) FROM licenses WHERE order_no=?", (order_no,))[0][0]
            _require(int(payments) == 1, f"payments 表应有 1 条流水，实际 {payments}")
            _require(int(licenses) == 1, f"licenses 表应有 1 条授权，实际 {licenses}")
            return "GET/重复支付同一 license_id；DB payments=1 licenses=1"
        _check(report, "order_idempotent", "订单查询 + 重复模拟支付幂等（同一 license_id）",
               step_order_idempotent)

        # ── 9) 本机导入 license → 解锁 ────────────────────────────────
        def step_local_import() -> str:
            status, _headers, result = http_json(
                "POST", f"{app_base}/api/license/import", json_body={"content": license_text})
            _require(status == 200 and isinstance(result, dict), f"导入 HTTP {status}: {result}")
            _require(result.get("state") == "paid", f"导入后 state 应为 paid，实际 {result.get('state')!r}")
            _require(result.get("edition") == "专业版", f"导入后 edition 应为 专业版，实际 {result.get('edition')!r}")
            _require(_norm_code(result.get("machine_code")) == _norm_code(machine_code),
                     "导入后机器码与本机不一致")
            _require((result.get("license") or {}).get("license_id") == license_id,
                     "导入后 license_id 不一致")
            return f"state=paid edition=专业版 license_id={license_id}"
        _check(report, "local_import", "本机 POST /api/license/import（测试 license → paid）",
               step_local_import)

        # ── 10) 本机 status 复核 ──────────────────────────────────────
        def step_local_status_paid() -> str:
            status, _headers, payload = http_json("GET", f"{app_base}/api/license/status")
            _require(status == 200 and isinstance(payload, dict), f"status HTTP {status}")
            _require(payload.get("state") == "paid", f"state 应为 paid，实际 {payload.get('state')!r}")
            _require(payload.get("edition") == "专业版", f"edition 应为 专业版，实际 {payload.get('edition')!r}")
            _require(payload.get("sku") == "pro", f"sku 应为 pro，实际 {payload.get('sku')!r}")
            _require((payload.get("license") or {}).get("license_id") == license_id,
                     "status.license.license_id 不一致")
            _require(str(payload.get("commercial_url") or "") == mock_base, "commercial_url 丢失")
            return f"state=paid edition=专业版 sku=pro commercial_url={mock_base}"
        _check(report, "local_status_paid", "本机 /api/license/status 复核 paid/专业版",
               step_local_status_paid)

        # ── 11) CORS 预检（允许本机 Origin） ──────────────────────────
        def step_cors_allowed() -> str:
            origin = "http://127.0.0.1:8760"
            status, headers, _text = http_call(
                "OPTIONS", f"{mock_base}/api/orders",
                headers={"Origin": origin,
                         "Access-Control-Request-Method": "POST",
                         "Access-Control-Request-Headers": "content-type"},
            )
            _require(status == 204, f"CORS 预检 HTTP 应为 204，实际 {status}")
            _require(_header(headers, "access-control-allow-origin") == origin,
                     f"预检缺少/错误的 allow-origin：{_header(headers, 'access-control-allow-origin')!r}")
            _require("POST" in _header(headers, "access-control-allow-methods").upper(),
                     "预检 allow-methods 不含 POST")
            # 实际 GET 也带 CORS 头
            status2, headers2, _body = http_call(
                "GET", f"{mock_base}/api/catalog", headers={"Origin": origin})
            _require(status2 == 200, f"带 Origin 的 GET /api/catalog HTTP {status2}")
            _require(_header(headers2, "access-control-allow-origin") == origin,
                     "GET 响应缺少 allow-origin")
            return f"OPTIONS 204 + allow-origin={origin}；GET 同样带 CORS 头"
        _check(report, "cors_allowed", "CORS 预检：允许本机 Origin（204 + allow-origin）",
               step_cors_allowed)

        # ── 12) CORS 拒绝无关 Origin ─────────────────────────────────
        def step_cors_denied() -> str:
            status, headers, _text = http_call(
                "OPTIONS", f"{mock_base}/api/orders",
                headers={"Origin": "https://evil.example.com",
                         "Access-Control-Request-Method": "POST"},
            )
            _require(_header(headers, "access-control-allow-origin") == "",
                     "无关 Origin 不应返回 allow-origin")
            return f"evil.example.com 未获 allow-origin（HTTP {status}）"
        _check(report, "cors_denied", "CORS：无关 Origin 不返回 allow-origin", step_cors_denied)

        # ── 13) token 错误统一 404 ───────────────────────────────────
        def step_token_404() -> str:
            status, _headers, payload = http_json(
                "GET", f"{mock_base}/api/orders/{order_no}?token=wrong-token-32bytes")
            _require(status == 404, f"错误 token 应 404，实际 {status}")
            _require(isinstance(payload, dict), "错误 token 响应不是 JSON")
            _require((payload.get("error") or {}).get("code") == "order_not_found",
                     f"错误码应为 order_not_found，实际 {(payload.get('error') or {}).get('code')!r}")
            return "错误 token → 404 order_not_found（防枚举）"
        _check(report, "token_404", "token 错误统一 404 order_not_found", step_token_404)

        # ── 14) 过期订单不可支付 ─────────────────────────────────────
        def step_order_expired() -> str:
            status, _headers, expired_order = http_json(
                "POST", f"{mock_base}/api/orders", json_body={
                    "sku": "personal", "machine_code": machine_code, "channel": "alipay"})
            _require(status == 200 and isinstance(expired_order, dict), f"下单 HTTP {status}")
            exp_no = expired_order["order_no"]
            exp_token = expired_order["order_token"]
            # 把订单改成已过期（模拟 15 分钟 TTL 到期；相当于 T1 的惰性过期判定）
            _db_exec(report.artifacts["mock_db"],
                     "UPDATE orders SET expires_at=? WHERE order_no=?",
                     ("2000-01-01T00:00:00+00:00", exp_no))
            status2, _headers2, view = http_json(
                "GET", f"{mock_base}/api/orders/{exp_no}?token={urllib.parse.quote(exp_token)}")
            _require(status2 == 200 and isinstance(view, dict), f"GET 过期订单 HTTP {status2}")
            _require(view.get("status") == "expired",
                     f"过期订单 status 应为 expired，实际 {view.get('status')!r}")
            status3, _headers3, pay = http_json(
                "POST", f"{mock_base}/api/mock/pay", json_body={
                    "order_no": exp_no, "token": exp_token})
            _require(status3 == 400, f"过期订单支付应 400，实际 {status3}")
            _require((pay.get("error") or {}).get("code") == "order_expired",
                     f"过期订单错误码应为 order_expired，实际 {(pay.get('error') or {}).get('code')!r}")
            return f"{exp_no} → expired；支付被拒 order_expired"
        _check(report, "order_expired", "过期订单（expires_at 已过）不可支付 → 400 order_expired",
               step_order_expired)

        # ── 15) 商业服务（alipay/page 模式） ─────────────────────────
        def step_alipay_health() -> str:
            base = _start_alipay_service(report, tmp_dir, alipay_port, keys_report)
            report.artifacts["alipay_base"] = base
            return f"{base}/healthz provider=alipay pay_mode=page"
        _check(report, "alipay_health", "启动第二个商业服务（provider=alipay，page 模式）",
               step_alipay_health)
        alipay_base = report.artifacts["alipay_base"]

        # ── 16) page 模式下单返回 pay_url ────────────────────────────
        def step_alipay_order() -> str:
            status, _headers, order = http_json("POST", f"{alipay_base}/api/orders", json_body={
                "sku": "pro", "machine_code": machine_code, "channel": "alipay"})
            _require(status == 200 and isinstance(order, dict),
                     f"支付宝 page 下单 HTTP {status}: {order}")
            pay_url = str(order.get("pay_url") or "")
            _require(pay_url.startswith("http"), "page 模式应返回 http(s) pay_url")
            _require("gateway.do" in pay_url, f"pay_url 不含网关地址：{pay_url[:120]}…")
            _require("sign=" in pay_url, "pay_url 缺少 sign（应用私钥未参与签名？）")
            _require("app_id=test-app" in pay_url, "pay_url 缺少 app_id=test-app")
            _require(order.get("provider") == "alipay",
                     f"provider 应为 alipay，实际 {order.get('provider')!r}")
            _require(order.get("mock_available") is False, "alipay 服务不应暴露 mock 支付")
            _require("<svg" in str(order.get("qr_svg") or ""), "page 模式二维码 qr_svg 缺失")
            report.artifacts.update({
                "alipay_order_no": order["order_no"],
                "alipay_order_token": order["order_token"],
                "alipay_amount_cents": int(order["amount_cents"]),
            })
            return (f"order_no={order['order_no']} pay_url={len(pay_url)}B "
                    f"app_id=test-app 已签名")
        _check(report, "alipay_order_page", "支付宝 page 模式下单 → pay_url（含签名）",
               step_alipay_order)
        alipay_order_no = report.artifacts["alipay_order_no"]
        alipay_order_token = report.artifacts["alipay_order_token"]
        alipay_amount_cents = report.artifacts["alipay_amount_cents"]

        # ── 17) 非 mock 部署上 /api/mock/pay 应 403 ──────────────────
        def step_mock_disabled() -> str:
            status, _headers, payload = http_json("POST", f"{alipay_base}/api/mock/pay", json_body={
                "order_no": alipay_order_no, "token": alipay_order_token})
            _require(status == 403, f"非 mock 部署调用 /api/mock/pay 应 403，实际 {status}")
            _require((payload.get("error") or {}).get("code") == "mock_disabled",
                     f"错误码应为 mock_disabled，实际 {(payload.get('error') or {}).get('code')!r}")
            return "403 mock_disabled"
        _check(report, "alipay_mock_disabled", "支付宝服务上 /api/mock/pay → 403 mock_disabled",
               step_mock_disabled)

        # 供 notify 模拟使用的独立 RSA2 签名私钥（测试「支付宝私钥」）
        alipay_private_key = _load_rsa_private(Path(keys_report["paths"]["alipay_private"]))

        # ── 18) 篡改金额/签名 → failure，订单保持 pending ────────────
        def step_notify_tampered() -> str:
            # (a) 签名后篡改 total_amount → 验签失败
            form = _sign_notify(_build_notify_form(
                order_no=alipay_order_no, amount_cents=alipay_amount_cents,
                trade_no="2026110222001400000000000001", app_id="test-app",
            ), alipay_private_key)
            form["total_amount"] = "0.01"
            status, text = _post_notify(alipay_base, form)
            _require(status == 200 and text == "failure",
                     f"签名后篡改金额应 failure，实际 HTTP {status} {text!r}")
            # (b) 用正确签名但错误金额（签名者就是「支付宝」，属于金额核对分支）
            form2 = _sign_notify(_build_notify_form(
                order_no=alipay_order_no, amount_cents=alipay_amount_cents,
                trade_no="2026110222001400000000000002", app_id="test-app",
                total_amount="0.01",
            ), alipay_private_key)
            status2, text2 = _post_notify(alipay_base, form2)
            _require(status2 == 200 and text2 == "failure",
                     f"金额不符应 failure，实际 HTTP {status2} {text2!r}")
            # 订单仍 pending、没有 license
            status3, _headers, view = http_json(
                "GET", f"{alipay_base}/api/orders/{alipay_order_no}"
                       f"?token={urllib.parse.quote(alipay_order_token)}")
            _require(status3 == 200 and view.get("status") == "pending",
                     f"篡改通知后订单应仍 pending，实际 {view.get('status')!r}")
            _require(view.get("license") is None, "篡改通知不应签发 license")
            licenses = _db_rows(report.artifacts["alipay_db"],
                                "SELECT COUNT(*) FROM licenses WHERE order_no=?",
                                (alipay_order_no,))[0][0]
            _require(int(licenses) == 0, f"篡改通知后 licenses 应为 0，实际 {licenses}")
            return "签名后篡改→failure；签名正确但金额不符→failure；订单仍 pending、无 license"
        _check(report, "notify_amount_tampered", "支付宝 notify 篡改金额/签名 → failure 且订单 pending",
               step_notify_tampered)

        # ── 19) WAIT_BUYER_PAY → success 但不签发 ───────────────────
        def step_notify_wait() -> str:
            form = _sign_notify(_build_notify_form(
                order_no=alipay_order_no, amount_cents=alipay_amount_cents,
                trade_no="2026110222001400000000000003", app_id="test-app",
                trade_status="WAIT_BUYER_PAY",
            ), alipay_private_key)
            status, text = _post_notify(alipay_base, form)
            _require(status == 200 and text == "success",
                     f"WAIT_BUYER_PAY 应 ack success，实际 HTTP {status} {text!r}")
            status2, _headers, view = http_json(
                "GET", f"{alipay_base}/api/orders/{alipay_order_no}"
                       f"?token={urllib.parse.quote(alipay_order_token)}")
            _require(status2 == 200 and view.get("status") == "pending",
                     f"WAIT_BUYER_PAY 不应改变订单状态，实际 {view.get('status')!r}")
            _require(view.get("license") is None, "WAIT_BUYER_PAY 不应签发 license")
            return "success（ack）且不签发，订单保持 pending"
        _check(report, "notify_wait_status", "支付宝 notify WAIT_BUYER_PAY → success 且不签发",
               step_notify_wait)

        # ── 20) TRADE_SUCCESS → paid + license ──────────────────────
        trade_no = "2026110222001400000000000001"
        success_form = _sign_notify(_build_notify_form(
            order_no=alipay_order_no, amount_cents=alipay_amount_cents,
            trade_no=trade_no, app_id="test-app",
        ), alipay_private_key)

        def step_notify_success() -> str:
            status, text = _post_notify(alipay_base, success_form)
            _require(status == 200 and text == "success",
                     f"TRADE_SUCCESS 通知应 success，实际 HTTP {status} {text!r}")
            status2, _headers, view = http_json(
                "GET", f"{alipay_base}/api/orders/{alipay_order_no}"
                       f"?token={urllib.parse.quote(alipay_order_token)}")
            _require(status2 == 200 and isinstance(view, dict), f"GET 订单 HTTP {status2}")
            _require(view.get("status") == "paid", f"通知后订单应 paid，实际 {view.get('status')!r}")
            _require(bool(view.get("paid_at")), "paid 订单缺少 paid_at")
            license_data = view.get("license")
            _require(isinstance(license_data, dict), "paid 订单缺少 license")
            text_license = str(license_data.get("text") or "")
            _require(bool(text_license), "license.text 为空")
            container = json.loads(text_license)
            _require(container.get("key_id") == "test-2026", "license key_id 应为 test-2026")
            _require(_norm_code(container.get("payload", {}).get("machine_codes", [""])[0])
                     == _norm_code(machine_code), "license 绑定的机器码与本机不一致")
            _require(license_data.get("edition") == "专业版",
                     f"license edition 应为 专业版，实际 {license_data.get('edition')!r}")
            report.artifacts.update({
                "alipay_license_id": str(license_data.get("license_id")),
                "alipay_license_text": text_license,
            })
            return (f"paid license_id={license_data.get('license_id')} "
                    f"edition=专业版 trade_no={trade_no}")
        _check(report, "notify_success_paid", "支付宝 notify TRADE_SUCCESS → paid + license",
               step_notify_success)
        alipay_license_id = report.artifacts["alipay_license_id"]

        # ── 21) 重复通知幂等 ────────────────────────────────────────
        def step_notify_duplicate() -> str:
            status, text = _post_notify(alipay_base, success_form)
            _require(status == 200 and text == "success",
                     f"重复通知应 success，实际 HTTP {status} {text!r}")
            status2, _headers, view = http_json(
                "GET", f"{alipay_base}/api/orders/{alipay_order_no}"
                       f"?token={urllib.parse.quote(alipay_order_token)}")
            _require((view.get("license") or {}).get("license_id") == alipay_license_id,
                     "重复通知后 license_id 变化（幂等被破坏）")
            payments = _db_rows(report.artifacts["alipay_db"],
                                "SELECT COUNT(*) FROM payments WHERE order_no=?",
                                (alipay_order_no,))[0][0]
            licenses = _db_rows(report.artifacts["alipay_db"],
                                "SELECT COUNT(*) FROM licenses WHERE order_no=?",
                                (alipay_order_no,))[0][0]
            _require(int(payments) == 1, f"重复通知后 payments 应为 1，实际 {payments}")
            _require(int(licenses) == 1, f"重复通知后 licenses 应为 1，实际 {licenses}")
            return f"重复通知 success，同一 license_id；DB payments=1 licenses=1"
        _check(report, "notify_duplicate", "重复异步通知幂等（success，不重复签发）",
               step_notify_duplicate)

        # ── 22) 支付宝通道签发的 license 也能被本机导入 ─────────────
        def step_alipay_license_import() -> str:
            status, _headers, result = http_json(
                "POST", f"{app_base}/api/license/import",
                json_body={"content": report.artifacts["alipay_license_text"]})
            _require(status == 200 and isinstance(result, dict), f"导入 HTTP {status}: {result}")
            _require(result.get("state") == "paid", f"导入后 state 应为 paid，实际 {result.get('state')!r}")
            _require(result.get("edition") == "专业版", "导入后 edition 应为 专业版")
            _require((result.get("license") or {}).get("license_id") == alipay_license_id,
                     "导入后 license_id 与支付宝通道签发的不一致")
            return f"state=paid edition=专业版 license_id={alipay_license_id}"
        _check(report, "alipay_license_import",
               "支付宝通道签发的 license 可被本机导入解锁（跨通道）", step_alipay_license_import)

    except CheckpointFailed:
        report.error = "存在失败的检查点"
    except KeyboardInterrupt:
        report.error = "用户中断（KeyboardInterrupt）"
        print("\n[中断] 收到 Ctrl+C，正在清理子进程与临时目录…")
    except Exception as exc:  # noqa: BLE001 - 记录后继续清理
        report.error = f"{type(exc).__name__}: {exc}"
        print(f"[ERROR] 端到端流程异常：{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        if report.services:
            print("-" * 78)
            print("清理：停止子进程…")
        for child in reversed(report.services):
            try:
                child.stop()
            except Exception:  # noqa: BLE001 - 清理阶段不抛
                pass
        if report.failed() or report.error:
            _print_failure_context(report)
        if keep_tmp:
            print(f"[INFO] --keep-tmp：保留临时目录 {tmp_dir}")
        else:
            for _attempt in range(3):
                shutil.rmtree(tmp_dir, ignore_errors=True)
                if not tmp_dir.exists():
                    break
                time.sleep(0.3)
            report.tmp_removed = not tmp_dir.exists()
            print(f"[INFO] 临时目录已清理：{report.tmp_removed}（{tmp_dir}）")

    _print_summary(report)
    return report


def _print_failure_context(report: E2EReport) -> None:
    """失败时打印各服务日志尾部，便于定位（不含 token/私钥：日志本身已脱敏）。"""
    failed = report.failed()
    if failed:
        print(f"失败检查点：{', '.join(item.key for item in failed)}")
    if report.error:
        print(f"流程错误：{report.error}")
    for child in report.services:
        tail = child.tail(15)
        if tail.strip():
            print(f"--- {child.name} 日志尾部 ---")
            print(tail)


def _print_summary(report: E2EReport) -> None:
    total = len(report.results)
    passed = sum(1 for item in report.results if item.ok)
    print("=" * 78)
    if report.passed_all():
        print(f"E2E 结果：{passed}/{total} 检查点 PASS，耗时 {report.duration_s:.1f}s ✅")
    else:
        print(f"E2E 结果：{passed}/{total} 检查点 PASS，失败 {total - passed} 个，"
              f"耗时 {report.duration_s:.1f}s ❌")
        for item in report.failed():
            print(f"  - {item.key}: {item.title} → {item.detail}")
        if report.error:
            print(f"  流程错误：{report.error}")
    print("=" * 78)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="L3 付费解锁端到端验收（真实 HTTP：mock 支付 + 支付宝异步通知）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--keep-tmp", action="store_true",
                        help="保留 commercial/var/_e2e_tmp 下的临时目录（排障用）")
    parser.add_argument("--json", action="store_true",
                        help="最后额外打印机器可读 JSON 摘要（不含 token/license 全文）")
    parser.add_argument("--quiet", action="store_true", help="只打印检查点与结果")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = run_e2e(keep_tmp=args.keep_tmp, verbose=not args.quiet)
    if args.json:
        payload = report.to_dict()
        # 摘要里去掉可能敏感的 license 全文（只保留长度/ID）
        artifacts = dict(payload.get("artifacts") or {})
        for name in ("license_text", "alipay_license_text", "order_token", "alipay_order_token"):
            if name in artifacts:
                artifacts[name] = f"<{len(str(artifacts[name]))} chars>"
        payload["artifacts"] = artifacts
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.passed_all() else 1


if __name__ == "__main__":
    raise SystemExit(main())
