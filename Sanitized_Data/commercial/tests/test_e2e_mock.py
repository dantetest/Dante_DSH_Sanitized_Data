# -*- coding: utf-8 -*-
"""T4 · L3 端到端验收（真实 HTTP 子进程；复用 ``commercial/scripts/e2e_demo.py``）。

覆盖 ``commercial/CONTRACT.md`` §8「端到端（T4）」：
  1. 测试密钥生成（Ed25519 test-2026 + 应用/支付宝两组 RSA；幂等、不覆盖已有）；
  2. mock 全链路（真实 HTTP）：商业服务 ``run_dev.py --provider mock`` + 本机 app
     ``app/server/run.py``（测试数据目录 + ``YINZHAN_PUBLIC_KEYS_PATH``）：
     下单（qr_svg）→ 模拟支付 → license.text → 本机 ``/api/license/import`` → paid/专业版；
  3. 支付宝异步通知（真实 HTTP）：第二个商业服务（page 模式）→ 独立 RSA2 签名的
     notify 表单 → `failure`/`success`/幂等/金额核对/非支付状态；
  4. 安全/边界：CORS 预检允许本机 Origin、拒绝无关 Origin、token 错误 404、过期订单不可支付；
  5. 清理：服务进程全部停止、仓库内临时目录已删除（不依赖系统 tmp）。

设计说明：
* **真实 HTTP**：两个商业服务与一个本机 app 都是独立子进程，通过 ``urllib`` 走 TCP，
  不使用 TestClient 短路，因此能抓 CORS/静态页/进程环境等集成问题；
* **独立签名**：notify 表单的 RSA2 签名在 ``e2e_demo._rsa2_sign_params`` 里**独立实现**
  （不调用被测服务 ``server/payments/alipay.py`` 的签名函数），避免「自签自验」掩盖问题；
* 临时目录统一在 ``commercial/var/_e2e_tmp/``（gitignore），不使用 pytest ``tmp_path``；
* 整个模块只启动一次服务（module 级 fixture），逐检查点断言，便于定位失败环节。

如本机环境不允许起子进程/占端口，可设置 ``COMMERCIAL_E2E_SKIP=1`` 跳过（CI 受限时）。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

# pytest 直接运行本文件时（未加载 commercial/tests/conftest.py）也能 import 商业服务
TESTS_DIR = Path(__file__).resolve().parent
COMMERCIAL_DIR = TESTS_DIR.parent
PROJECT_ROOT = COMMERCIAL_DIR.parent
SCRIPTS_DIR = COMMERCIAL_DIR / "scripts"
for _candidate in (str(COMMERCIAL_DIR), str(PROJECT_ROOT / "app" / "server"), str(SCRIPTS_DIR)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

import e2e_demo  # noqa: E402
import gen_test_keys  # noqa: E402


def _skip_requested() -> bool:
    return str(os.environ.get("COMMERCIAL_E2E_SKIP", "")).strip().lower() in (
        "1", "true", "yes", "on")


@pytest.fixture(scope="module")
def e2e_report() -> e2e_demo.E2EReport:
    """启动真实服务跑完整 E2E（module 级，只跑一次）；返回报告供逐项断言。"""
    if _skip_requested():
        pytest.skip("COMMERCIAL_E2E_SKIP=1：按环境变量跳过真实 HTTP 端到端")
    report = e2e_demo.run_e2e(verbose=True)
    return report


def _assert_report_pass(report: e2e_demo.E2EReport) -> None:
    if report.passed_all():
        return
    lines = [f"{item.key}: {item.title} → {item.detail}"
             for item in report.results if not item.ok]
    missing = [key for key in e2e_demo.CHECKPOINT_KEYS if report.checkpoint(key) is None]
    pytest.fail(
        "E2E 未全部通过。\n"
        f"  失败检查点：{lines or '（无，但流程异常）'}\n"
        f"  未执行检查点：{missing}\n"
        f"  流程错误：{report.error}\n"
        f"  临时目录：{report.tmp_dir}",
        pytrace=False,
    )


def _check(report: e2e_demo.E2EReport, key: str) -> e2e_demo.Checkpoint:
    _assert_report_pass(report)
    item = report.checkpoint(key)
    assert item is not None, f"缺少检查点 {key}"
    assert item.ok, f"检查点 {key} 失败：{item.detail}"
    return item


# ---------------------------------------------------------------------------
# 1) 报告完整性 / 测试密钥
# ---------------------------------------------------------------------------
def test_e2e_report_covers_all_checkpoints(e2e_report: e2e_demo.E2EReport) -> None:
    _assert_report_pass(e2e_report)
    keys = [item.key for item in e2e_report.results]
    assert keys == list(e2e_demo.CHECKPOINT_KEYS), (
        f"检查点集合/顺序与预期不一致：{keys}")
    # 端口与进程清理状态
    assert set(e2e_report.ports) == {"mock", "alipay", "app"}
    assert all(not child.running() for child in e2e_report.services), "E2E 结束后仍有服务进程存活"


def test_test_keys_are_valid_and_idempotent() -> None:
    """gen_test_keys 幂等、不覆盖；license 公钥表格式/长度符合产品 keys.py 约定。"""
    out_dir = COMMERCIAL_DIR / "var" / "_e2e_tmp" / "keys-unit"
    shutil.rmtree(out_dir, ignore_errors=True)
    try:
        first = gen_test_keys.ensure_test_keys(out_dir=out_dir, quiet=True)
        assert first["key_id"] == "test-2026"
        assert len(first["created"]) == 6, f"首次应生成 6 个文件，实际 {first['created']}"
        assert all(Path(path).is_file() for path in first["created"])

        second = gen_test_keys.ensure_test_keys(out_dir=out_dir, quiet=True)
        assert second["created"] == [], f"第二次不应覆盖/新增文件：{second['created']}"
        assert second["license_public_b64"] == first["license_public_b64"]
        for name, fingerprint in first["fingerprints"].items():
            assert second["fingerprints"][name] == fingerprint, f"{name} 指纹变化（被意外轮换）"

        public_json = json.loads(
            Path(first["paths"]["license_public_json"]).read_text(encoding="utf-8"))
        assert set(public_json) == {"test-2026"}
        raw = base64.b64decode(public_json["test-2026"])
        assert len(raw) == 32, f"Ed25519 raw 公钥应为 32 字节，实际 {len(raw)}"
        assert base64.b64encode(raw).decode() == public_json["test-2026"]
        # RSA 私钥/公钥文件成对存在且是 PEM
        for name in ("app_private", "app_public", "alipay_private", "alipay_public"):
            text = Path(first["paths"][name]).read_text(encoding="ascii")
            assert "BEGIN" in text and "-----END" in text, f"{name} 不是 PEM"
        assert "PRIVATE KEY" in Path(first["paths"]["app_private"]).read_text(encoding="ascii")
        assert "PRIVATE KEY" not in Path(first["paths"]["app_public"]).read_text(encoding="ascii")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2) mock 全链路
# ---------------------------------------------------------------------------
def test_mock_service_health_and_catalog(e2e_report: e2e_demo.E2EReport) -> None:
    _check(e2e_report, "mock_health")
    _check(e2e_report, "catalog")
    assert e2e_report.artifacts["test_keys_dir"]


def test_order_create_and_mock_pay_issue_license(e2e_report: e2e_demo.E2EReport) -> None:
    _check(e2e_report, "order_create")
    _check(e2e_report, "mock_pay")
    _check(e2e_report, "order_idempotent")

    artifacts = e2e_report.artifacts
    assert artifacts["order_no"].startswith("ord_")
    assert artifacts["amount_cents"] == 49900
    text = artifacts["license_text"]
    assert "\n" not in text, "license.text 必须是单行紧凑 JSON"
    container = json.loads(text)
    assert container["key_id"] == "test-2026"
    assert container["alg"].lower().startswith("ed25519")
    payload = container["payload"]
    assert payload["sku"] == "pro"
    assert payload["order_id"] == artifacts["order_no"]
    normalized = artifacts["machine_code"].replace("-", "").upper()
    assert payload["machine_codes"][0].replace("-", "").upper() == normalized


def test_purchase_page_served_by_local_app(e2e_report: e2e_demo.E2EReport) -> None:
    _check(e2e_report, "app_health")
    _check(e2e_report, "purchase_page")
    _check(e2e_report, "local_status")
    app_base = e2e_report.artifacts["app_base"]
    assert app_base.startswith("http://127.0.0.1:")
    assert e2e_report.artifacts["app_health"]["net"] == "blocked", "本机引擎应零外呼"


# ---------------------------------------------------------------------------
# 3) 本机自动解锁
# ---------------------------------------------------------------------------
def test_local_import_unlocks_paid(e2e_report: e2e_demo.E2EReport) -> None:
    _check(e2e_report, "local_import")
    _check(e2e_report, "local_status_paid")
    assert e2e_report.artifacts["license_id"].startswith("lic_")


# ---------------------------------------------------------------------------
# 4) 安全/边界：CORS、token、过期
# ---------------------------------------------------------------------------
def test_cors_token_and_expiry_guards(e2e_report: e2e_demo.E2EReport) -> None:
    _check(e2e_report, "cors_allowed")
    _check(e2e_report, "cors_denied")
    _check(e2e_report, "token_404")
    _check(e2e_report, "order_expired")


# ---------------------------------------------------------------------------
# 5) 支付宝异步通知（真实 HTTP）
# ---------------------------------------------------------------------------
def test_alipay_page_order_and_notify_guards(e2e_report: e2e_demo.E2EReport) -> None:
    _check(e2e_report, "alipay_health")
    _check(e2e_report, "alipay_order_page")
    _check(e2e_report, "alipay_mock_disabled")
    _check(e2e_report, "notify_amount_tampered")
    _check(e2e_report, "notify_wait_status")


def test_alipay_notify_success_duplicate_and_import(e2e_report: e2e_demo.E2EReport) -> None:
    _check(e2e_report, "notify_success_paid")
    _check(e2e_report, "notify_duplicate")
    _check(e2e_report, "alipay_license_import")
    artifacts = e2e_report.artifacts
    assert artifacts["alipay_license_id"].startswith("lic_")
    assert artifacts["alipay_license_id"] != artifacts["license_id"], (
        "两个通道是两笔独立订单，license_id 应不同")
    # 检查点标题/详情里不得出现订单令牌或 license 全文（日志脱敏检查）
    summary_text = json.dumps(e2e_report.to_dict()["checkpoints"], ensure_ascii=False)
    assert artifacts["order_token"] not in summary_text, "检查点输出泄露了订单 token"
    assert artifacts["alipay_order_token"] not in summary_text, "检查点输出泄露了订单 token"
    assert artifacts["license_text"] not in summary_text, "检查点输出泄露了 license 全文"


# ---------------------------------------------------------------------------
# 6) 清理
# ---------------------------------------------------------------------------
def test_e2e_cleaned_up_services_and_tmp_dir(e2e_report: e2e_demo.E2EReport) -> None:
    for child in e2e_report.services:
        assert not child.running(), f"{child.name} 仍在运行（pid={child.pid}）"
    assert e2e_report.tmp_removed, f"临时目录未清理：{e2e_report.tmp_dir}"
    assert e2e_report.tmp_dir is not None and not e2e_report.tmp_dir.exists()
