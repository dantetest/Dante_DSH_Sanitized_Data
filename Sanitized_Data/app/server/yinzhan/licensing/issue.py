# -*- coding: utf-8 -*-
"""W4a 人工签发：构造并签名 license.json（不依赖产品运行/网络）。

私钥由调用方提供，本模块不生成/保存私钥；CLI 见 deploy/license_issue.py。
"""
from __future__ import annotations

import base64
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import tiers
from .service import SCHEMA, canonical_payload

_CODE_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")


def normalize_machine_code(value: str) -> str:
    text = str(value or "").strip().upper()
    compact = "".join(ch for ch in text if ch.isalnum())
    if len(compact) == 16:
        text = "-".join(compact[i:i + 4] for i in range(0, 16, 4))
    if not _CODE_RE.match(text):
        raise ValueError(f"机器码格式无效：{value!r}（应为 XXXX-XXXX-XXXX-XXXX）")
    return text


def default_features(sku: str) -> list[str]:
    tier = tiers.TIERS.get(sku)
    if not tier or sku == "free" or sku == "trial":
        raise ValueError(f"不可签发 SKU：{sku}")
    return list(tier["features"])


def build_payload(*,
                  sku: str,
                  machine_codes: Iterable[str],
                  order_id: str,
                  license_id: str | None = None,
                  term: str = "annual",
                  seats: int | None = None,
                  max_devices: int | None = None,
                  days: int = 365,
                  expires_at: str | None = None,
                  updates_days: int | None = 365,
                  customer_name: str = "",
                  customer_org: str = "",
                  team_id: str = "",
                  notes: str = "",
                  now: datetime | None = None) -> dict:
    if sku not in ("personal", "pro", "team", "org"):
        raise ValueError("sku 必须是 personal/pro/team/org")
    if term not in ("annual", "perpetual"):
        raise ValueError("term 必须是 annual/perpetual")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    codes = [normalize_machine_code(c) for c in machine_codes]
    if not codes:
        raise ValueError("至少需要一个机器码")
    seats = int(seats if seats is not None else (5 if sku == "team" else 50 if sku == "org" else 1))
    if seats < 1:
        raise ValueError("seats 必须 ≥1")
    max_devices = int(max_devices if max_devices is not None else (
        2 if sku == "pro" else seats if sku == "team" else 1))
    if len(codes) > max_devices:
        raise ValueError(f"机器码数量 {len(codes)} 超过 max_devices={max_devices}")
    if not order_id:
        raise ValueError("缺少订单号 order_id")
    if sku in ("team", "org") and not team_id:
        raise ValueError("团队/机构授权需要 team_id")
    if term == "annual":
        if expires_at:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            exp = exp.astimezone(timezone.utc)
        else:
            exp = now + timedelta(days=max(1, int(days)))
        if exp <= now:
            raise ValueError("到期时间必须晚于当前时间")
        updates = exp
    else:
        exp = None
        # 买断：永久使用 + 约定更新期（默认 1 年），不是永久免费升级
        updates = (now + timedelta(days=max(1, int(updates_days)))
                   if updates_days else None)
    license_id = license_id or "lic_" + now.strftime("%Y%m%d") + "_" + secrets.token_hex(4)
    payload = {
        "license_id": license_id,
        "order_id": str(order_id),
        "sku": sku,
        "edition": tiers.TIERS[sku]["edition"],
        "term": term,
        "seats": seats,
        "max_devices": max_devices,
        "machine_codes": codes,
        "customer": {"name": customer_name or None,
                     "org": customer_org or None, "note": notes or ""},
        "issued_at": now.isoformat(),
        "not_before": now.isoformat(),
        "expires_at": exp.isoformat() if exp else None,
        "updates_until": updates.isoformat() if updates else None,
        "grace_days": 14,
        "features": default_features(sku),
        "team_id": team_id or None,
        "issuer": "BLCTEK",
        "notes": notes or "",
    }
    return payload


def sign_license(payload: dict, private_key: Ed25519PrivateKey, key_id: str) -> dict:
    sig = private_key.sign(canonical_payload(payload))
    return {
        "schema": SCHEMA,
        "alg": "Ed25519",
        "key_id": key_id,
        "payload": payload,
        "sig": base64.b64encode(sig).decode("ascii"),
    }


def write_license(container: dict, out_path: Path | str) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(container, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path
