# -*- coding: utf-8 -*-
"""T1 签发模块测试：机器码规范化 / Ed25519 签发与验签 / 产品 LicenseService 导入 / 错误处理。"""
from __future__ import annotations

import base64
import json
from datetime import datetime

import pytest

from server.signer import (
    LICENSE_DAYS,
    LicenseSigner,
    SignerError,
    is_valid_machine_code,
    normalize_machine_code,
)


def _make_signer(test_license_key) -> LicenseSigner:
    return LicenseSigner(
        test_license_key["path"],
        key_id=test_license_key["key_id"],
        password_env="LICENSE_KEY_PASSWORD",
    )


# ── 机器码 ──
def test_normalize_machine_code_accepts_variants():
    expected = "ZH39-7N5C-98CD-A8GB"
    assert normalize_machine_code(expected) == expected
    assert normalize_machine_code(expected.lower()) == expected
    assert normalize_machine_code("ZH397N5C98CDA8GB") == expected
    assert normalize_machine_code(" ZH39 7N5C 98CD A8GB ") == expected
    assert is_valid_machine_code("ZH397N5C98CDA8GB") is True


@pytest.mark.parametrize("bad", [
    "", None, "abc", "IIII-IIII-IIII-IIII", "1234-5678-90AB-CDE", "1234-5678-90AB-CDEU",
])
def test_normalize_machine_code_rejects(bad):
    with pytest.raises(SignerError) as excinfo:
        normalize_machine_code(bad)
    assert excinfo.value.code == "machine_code_invalid"
    assert excinfo.value.status == 400
    assert "XXXX-XXXX-XXXX-XXXX" in excinfo.value.message
    assert is_valid_machine_code(bad) is False


# ── 签发 ──
def test_sign_license_is_compact_verifiable_json(test_license_key):
    signer = _make_signer(test_license_key)
    license_data = signer.issue(
        sku="pro", machine_code="ZH39-7N5C-98CD-A8GB",
        order_no="ord_20260101000000_ab12cd34")

    assert license_data["license_id"].startswith("lic_")
    assert license_data["sku"] == "pro"
    assert license_data["edition"] == "专业版"
    text = license_data["text"]
    # 紧凑单行 JSON（授权码粘贴格式）
    assert "\n" not in text
    assert ", " not in text and ": " not in text

    container = json.loads(text)
    assert container["schema"] == 1
    assert container["alg"] == "Ed25519"
    assert container["key_id"] == test_license_key["key_id"]
    payload = container["payload"]
    assert payload["license_id"] == license_data["license_id"]
    assert payload["order_id"] == "ord_20260101000000_ab12cd34"
    assert payload["sku"] == "pro"
    assert payload["edition"] == "专业版"
    assert payload["term"] == "annual"
    assert payload["machine_codes"] == ["ZH39-7N5C-98CD-A8GB"]
    assert payload["issuer"] == "BLCTEK"
    assert payload["notes"] == "online"
    assert payload["features"] == [
        "all_packs", "custom_packs", "tif", "d3d", "mcp", "batch", "audit_export", "ocr"]
    issued = datetime.fromisoformat(payload["issued_at"])
    expires = datetime.fromisoformat(payload["expires_at"])
    assert abs((expires - issued).total_seconds() - LICENSE_DAYS * 86400) <= 120

    # 独立验签（测试公钥）
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from yinzhan.licensing.service import canonical_payload

    public_key = Ed25519PublicKey.from_public_bytes(
        base64.b64decode(test_license_key["public_raw_b64"]))
    public_key.verify(base64.b64decode(container["sig"]), canonical_payload(payload))


def test_license_imported_by_product_license_service(test_license_key, license_service,
                                                     real_machine_code):
    signer = _make_signer(test_license_key)
    machine_code = normalize_machine_code(real_machine_code)
    license_data = signer.issue(
        sku="personal", machine_code=machine_code,
        order_no="ord_20260101000000_00000001")

    status = license_service.import_license(license_data["text"])
    assert status["state"] == "paid"
    assert status["sku"] == "personal"
    assert status["edition"] == "个人版"
    assert status["license"]["license_id"] == license_data["license_id"]
    assert "custom_packs" not in status["features"]
    assert "ocr" in status["features"]


def test_tampered_license_rejected(test_license_key, license_service):
    from yinzhan.licensing.service import LicenseError

    signer = _make_signer(test_license_key)
    license_data = signer.issue(
        sku="pro", machine_code="ZH39-7N5C-98CD-A8GB",
        order_no="ord_20260101000000_00000002")
    container = json.loads(license_data["text"])
    container["payload"]["expires_at"] = "2099-01-01T00:00:00+00:00"
    tampered = json.dumps(container, ensure_ascii=False)
    with pytest.raises(LicenseError) as excinfo:
        license_service.import_license(tampered)
    assert excinfo.value.code == "license_invalid"


def test_signer_rejects_unsupported_sku(test_license_key):
    signer = _make_signer(test_license_key)
    with pytest.raises(SignerError) as excinfo:
        signer.issue(sku="free", machine_code="ZH39-7N5C-98CD-A8GB",
                     order_no="ord_20260101000000_00000003")
    assert excinfo.value.code == "license_issue_failed"


# ── 私钥错误处理（不泄露密钥/口令） ──
def test_missing_private_key_gives_clear_error(tmp_dir):
    signer = LicenseSigner(tmp_dir / "missing-dir" / "license.pem", key_id="test-2026")
    with pytest.raises(SignerError) as excinfo:
        signer.issue(sku="pro", machine_code="ZH39-7N5C-98CD-A8GB", order_no="ord_x")
    error = excinfo.value
    assert error.code == "license_key_missing"
    assert error.status == 503
    assert "私钥" in error.message
    assert "BEGIN" not in error.message and "PRIVATE KEY" not in error.message
    info = signer.describe()
    assert info["loaded"] is False
    assert info["private_key_configured"] is False


def test_invalid_private_key_file_gives_clear_error(tmp_dir):
    bad_key = tmp_dir / "bad.pem"
    bad_key.write_text(
        "-----BEGIN PRIVATE KEY-----\nnot-a-valid-key\n-----END PRIVATE KEY-----\n",
        encoding="utf-8")
    signer = LicenseSigner(bad_key, key_id="test-2026")
    with pytest.raises(SignerError) as excinfo:
        signer.issue(sku="pro", machine_code="ZH39-7N5C-98CD-A8GB", order_no="ord_x")
    assert excinfo.value.code == "license_key_invalid"
    assert excinfo.value.status == 503


def test_password_env_mismatch_is_reported(monkeypatch, test_license_key):
    # 测试私钥未加密：误设口令应给出明确错误而不是崩溃
    monkeypatch.setenv("LICENSE_KEY_PASSWORD", "wrong-password")
    signer = _make_signer(test_license_key)
    with pytest.raises(SignerError) as excinfo:
        signer.issue(sku="pro", machine_code="ZH39-7N5C-98CD-A8GB", order_no="ord_x")
    assert excinfo.value.code == "license_key_invalid"


def test_describe_after_key_load_has_no_secret(test_license_key):
    signer = _make_signer(test_license_key)
    signer.issue(sku="pro", machine_code="ZH39-7N5C-98CD-A8GB", order_no="ord_x")
    info = signer.describe()
    assert info["loaded"] is True
    assert info["key_id"] == test_license_key["key_id"]
    dumped = json.dumps(info, ensure_ascii=False)
    assert "PRIVATE KEY" not in dumped
    assert "BEGIN" not in dumped
