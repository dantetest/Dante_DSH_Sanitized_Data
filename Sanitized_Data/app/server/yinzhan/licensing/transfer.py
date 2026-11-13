# -*- coding: utf-8 -*-
"""离线授权转移/重绑（W4b，人工签发模式）。

- 产品端：根据旧 license + 新机器码生成 transfer request JSON（不含私钥）；
- 销售端：离线验证旧 license 签名，重新签发新 machine_codes 的 license；
- 不做在线账号/设备表；长期方案另行规划。
"""
from __future__ import annotations

import json
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .issue import normalize_machine_code, reissue_payload, sign_license
from .service import LicenseService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_transfer_request(old_license: str | bytes | dict,
                            new_machine_code: str) -> dict:
    """生成给销售/客服的转移请求；旧 license 原样携带以便验签。"""
    with tempfile.TemporaryDirectory(prefix="yz_transfer_") as tmp:
        svc = LicenseService(Path(tmp))
        payload = svc.verify_license_content(old_license)
    code = normalize_machine_code(new_machine_code)
    request = {
        "schema": 1,
        "type": "license_transfer_request",
        "created_at": _utcnow().isoformat(),
        "nonce": secrets.token_hex(8),
        "old_license_id": payload.get("license_id"),
        "old_order_id": payload.get("order_id"),
        "old_machine_codes": list(payload.get("machine_codes") or []),
        "new_machine_code": code,
        "old_license": old_license,
    }
    return request


def reissue_from_request(request: dict, private_key: Ed25519PrivateKey,
                         key_id: str, *, note: str = "") -> dict:
    """销售端：验证转移请求中的旧 license 后，重签新机器码。"""
    if not isinstance(request, dict) or request.get("type") != "license_transfer_request":
        raise ValueError("不是有效的授权转移请求")
    if request.get("schema") != 1:
        raise ValueError("转移请求版本不支持")
    old_content = request.get("old_license")
    if not old_content:
        raise ValueError("转移请求缺少旧 license")
    with tempfile.TemporaryDirectory(prefix="yz_transfer_") as tmp:
        svc = LicenseService(Path(tmp))
        payload = svc.verify_license_content(old_content)
    code = normalize_machine_code(request.get("new_machine_code") or "")
    new_payload = reissue_payload(payload, [code], note=note)
    return sign_license(new_payload, private_key, key_id)


def reissue_from_license(old_license: str | bytes | dict,
                         new_machine_code: str,
                         private_key: Ed25519PrivateKey,
                         key_id: str, *,
                         max_devices: int | None = None,
                         note: str = "") -> dict:
    """直接把旧 license 重签到新机器码（人工客服最常用）。"""
    with tempfile.TemporaryDirectory(prefix="yz_transfer_") as tmp:
        svc = LicenseService(Path(tmp))
        payload = svc.verify_license_content(old_license)
    code = normalize_machine_code(new_machine_code)
    new_payload = reissue_payload(payload, [code], max_devices=max_devices, note=note)
    return sign_license(new_payload, private_key, key_id)
