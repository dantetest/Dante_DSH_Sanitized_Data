# -*- coding: utf-8 -*-
"""离线授权服务（W1）：机器码、Ed25519 license、3 天试用、本地状态。

设计约束：
- 文件处理引擎保持零外呼；本模块只做本地读写和密码学校验。
- 私钥不在本模块/仓库/安装包；本模块只内置/加载公钥。
- 时钟回拨不能延长试用/授权：所有时间判断用 ``max(now, last_seen)``。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import keys, machine, tiers

SCHEMA = 1
TRIAL_DAYS = 3
DEFAULT_GRACE_DAYS = 14
_CLOCK_ROLLBACK_HOURS = 48
_STATE_SECRET = b"BLCTEK-license-state-v1"
_LICENSE_MAX_BYTES = 1024 * 1024


class LicenseError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def canonical_payload(payload: dict) -> bytes:
    """签名/验签共用的规范 JSON；不要改成按原文件字节签名。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sign_payload(payload: dict, private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.sign(canonical_payload(payload))).decode("ascii")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        raise LicenseError("license_invalid", "授权文件时间格式无效")


def _canonical_state(state: dict) -> bytes:
    body = {k: v for k, v in state.items() if k != "sig"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _state_sig(machine_code: str, state: dict) -> str:
    key = hashlib.sha256(_STATE_SECRET + machine_code.encode("utf-8")).digest()
    return hmac.new(key, _canonical_state(state), hashlib.sha256).hexdigest()


def _norm_code(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


class LicenseService:
    """一个数据目录对应一个 service；API/MCP/UI 共用。"""

    def __init__(self, data_dir: Path | str | None = None,
                 now: Callable[[], datetime] | None = None):
        if data_dir is None:
            from .. import config
            data_dir = config.DATA_DIR
        self.data_dir = Path(data_dir)
        self.license_dir = self.data_dir / "license"
        self.license_path = self.license_dir / "license.json"
        self.state_path = self.license_dir / "state.json"
        self.events_path = self.license_dir / "events.jsonl"
        self._now = now or _utcnow
        self._lock = threading.RLock()
        self._machine_cache: dict | None = None

    # ── 机器码 ──
    def machine_info(self) -> dict:
        with self._lock:
            if self._machine_cache is None:
                try:
                    self._machine_cache = machine.machine_info()
                except Exception as exc:
                    raise LicenseError("machine_code_failed", f"无法生成本机识别码：{exc}", 500)
            return dict(self._machine_cache)

    def machine_code(self) -> str:
        return self.machine_info()["code"]

    # ── 状态文件 ──
    def _new_state(self) -> dict:
        return {
            "schema": SCHEMA,
            "last_seen_utc": _iso(self._now()),
            "trial": None,
            "usage": {"date": self._now().astimezone().date().isoformat(),
                      "jobs": 0, "ocr_pages": 0},
            "last_license_id": None,
            "flags": {},
        }

    def _load_state(self) -> dict:
        if not self.state_path.is_file():
            state = self._new_state()
            # 状态文件不存在：首次运行可以从 0 开始；但如果是「用过后被删」
            # （.state_seen 标记还在），按 free-tier §3.1 以「今日已用 1 份」恢复，
            # 不能删掉 state.json 就刷满 3 份额度。
            try:
                seeded = (self.license_dir / ".state_seen").is_file()
            except OSError:
                seeded = False
            if seeded:
                state["flags"]["state_recovered"] = True
                state["usage"]["jobs"] = 1
            return state
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            state = json.loads(raw)
            if not isinstance(state, dict) or state.get("schema") != SCHEMA:
                raise ValueError("schema")
            sig = str(state.get("sig") or "")
            expected = _state_sig(self.machine_code(), state)
            if not sig or not hmac.compare_digest(sig, expected):
                raise ValueError("sig")
            return state
        except Exception:
            # 损坏/被改：不惩罚成完全不能用；但也不恢复试用资格（防删文件重置）。
            # 按 free-tier.md §3.1：按「今日已用 1 份」恢复，而不是清零当天额度
            # （否则删掉 state.json 就能刷满 3 份，额度形同虚设）。
            state = self._new_state()
            state["flags"]["state_recovered"] = True
            state["usage"]["jobs"] = 1
            return state

    def _save_state(self, state: dict) -> None:
        try:
            self.license_dir.mkdir(parents=True, exist_ok=True)
            state = dict(state)
            state.pop("sig", None)
            state["sig"] = _state_sig(self.machine_code(), state)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2,
                                      sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(self.state_path)
            # 首次落盘标记：用于区分「首次运行」与「状态文件被删」
            marker = self.license_dir / ".state_seen"
            if not marker.is_file():
                marker.write_text(_iso(self._now()), encoding="utf-8")
        except Exception:
            # 本地状态写失败不应导致整个产品不可用；授权文件本身仍有签名
            pass

    def _event(self, kind: str, **fields: Any) -> None:
        """本地审计：只记事件类型/时间/授权号，不记授权文件内容或 PII。"""
        try:
            self.license_dir.mkdir(parents=True, exist_ok=True)
            record = {"at": _iso(self._now()), "event": str(kind)}
            record.update({k: v for k, v in fields.items() if v is not None})
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            pass

    def _effective_now(self, state: dict) -> tuple[datetime, bool]:
        now = self._now()
        last = _parse_iso(state.get("last_seen_utc"))
        effective = max(now, last) if last else now
        suspect = bool(last and now < last - timedelta(hours=_CLOCK_ROLLBACK_HOURS))
        return effective, suspect

    # ── license 校验 ──
    def _verify_container(self, content: str | bytes | dict) -> dict:
        if not keys.KEYRING_OK or not keys.PUBLIC_KEYS:
            raise LicenseError(
                "keyring_invalid",
                "软件组件可能损坏，请使用官方安装包重新安装",
                403,
            )
        try:
            if isinstance(content, dict):
                container = content
            else:
                text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
                if len(text.encode("utf-8")) > _LICENSE_MAX_BYTES:
                    raise LicenseError("license_invalid", "授权文件过大")
                container = json.loads(text)
        except LicenseError:
            raise
        except Exception:
            raise LicenseError("license_invalid", "授权文件不是有效 JSON")
        if not isinstance(container, dict):
            raise LicenseError("license_invalid", "授权文件格式无效")
        if container.get("schema") != SCHEMA:
            raise LicenseError("license_invalid", "授权文件版本不支持，请升级软件")
        if container.get("alg") != "Ed25519":
            raise LicenseError("license_invalid", "授权文件算法不支持")
        payload = container.get("payload")
        if not isinstance(payload, dict):
            raise LicenseError("license_invalid", "授权文件缺少 payload")
        key_id = str(container.get("key_id") or "")
        raw_key = keys.get_public_key(key_id)
        if not raw_key:
            raise LicenseError("license_invalid", "授权文件公钥未知，请重新下载")
        try:
            pub = Ed25519PublicKey.from_public_bytes(raw_key)
            sig = base64.b64decode(str(container.get("sig") or ""), validate=True)
            pub.verify(sig, canonical_payload(payload))
        except (InvalidSignature, ValueError, TypeError):
            raise LicenseError("license_invalid", "授权文件签名无效")
        self._validate_payload(payload)
        return payload

    def _validate_payload(self, payload: dict) -> None:
        required = ("license_id", "order_id", "sku", "edition", "term", "seats",
                    "max_devices", "machine_codes", "issued_at", "not_before",
                    "grace_days", "features", "issuer")
        for field in required:
            if field not in payload:
                raise LicenseError("license_invalid", "授权文件字段缺失")
        if not tiers.valid_sku(str(payload.get("sku") or "")):
            raise LicenseError("license_invalid", "授权文件 SKU 无效")
        if payload.get("term") not in ("annual", "perpetual"):
            raise LicenseError("license_invalid", "授权文件期限类型无效")
        if payload.get("issuer") != "BLCTEK":
            raise LicenseError("license_invalid", "授权文件签发方无效")
        machine_codes = payload.get("machine_codes")
        max_devices = payload.get("max_devices")
        if not isinstance(machine_codes, list) or not isinstance(max_devices, int) or max_devices < 1:
            raise LicenseError("license_invalid", "授权文件机器绑定字段无效")
        if not (1 <= len(machine_codes) <= max_devices):
            raise LicenseError("license_invalid", "授权文件绑定设备数无效")
        if not isinstance(payload.get("seats"), int) or payload["seats"] < 1:
            raise LicenseError("license_invalid", "授权文件席位数无效")
        features = payload.get("features")
        if not isinstance(features, list) or any(f not in tiers.KNOWN_FEATURES for f in features):
            raise LicenseError("license_invalid", "授权文件功能位无效")
        grace = payload.get("grace_days")
        if not isinstance(grace, int) or not (0 <= grace <= 60):
            raise LicenseError("license_invalid", "授权文件宽限期无效")
        if payload.get("term") == "annual" and not payload.get("expires_at"):
            raise LicenseError("license_invalid", "年度授权缺少到期日")
        if payload.get("term") == "perpetual" and payload.get("expires_at") is not None:
            raise LicenseError("license_invalid", "买断授权不应包含到期日")
        if payload.get("sku") in ("team", "org") and not payload.get("team_id"):
            raise LicenseError("license_invalid", "团队/机构授权缺少 team_id")
        not_before = _parse_iso(payload.get("not_before"))
        expires = _parse_iso(payload.get("expires_at"))
        if not_before is None:
            raise LicenseError("license_invalid", "授权文件生效时间无效")
        if payload.get("term") == "annual":
            if expires is None or expires <= not_before:
                raise LicenseError("license_invalid", "授权文件到期时间无效")

    def _load_license(self, effective_now: datetime) -> dict:
        if not self.license_path.is_file():
            return {"status": "none"}
        try:
            content = self.license_path.read_text(encoding="utf-8")
            payload = self._verify_container(content)
        except LicenseError as exc:
            return {"status": "invalid", "error": {"code": exc.code, "message": exc.message}}
        except Exception:
            return {"status": "invalid", "error": {"code": "license_invalid",
                                                    "message": "授权文件读取失败"}}
        not_before = _parse_iso(payload.get("not_before"))
        expires = _parse_iso(payload.get("expires_at"))
        if not_before and effective_now < not_before:
            return {"status": "not_yet_valid", "payload": payload,
                    "error": {"code": "license_not_yet_valid", "message": "授权尚未生效，请校正系统时间"}}
        code = self.machine_code()
        if not any(_norm_code(c) == code for c in payload.get("machine_codes") or []):
            return {"status": "machine_mismatch", "payload": payload,
                    "error": {"code": "license_machine_mismatch",
                              "message": "授权文件不适用于本机，请使用本机识别码重新签发"}}
        if payload.get("term") == "perpetual":
            return {"status": "paid", "payload": payload, "features": payload["features"]}
        grace_days = int(payload.get("grace_days") or DEFAULT_GRACE_DAYS)
        if expires is None:
            return {"status": "invalid", "payload": payload,
                    "error": {"code": "license_invalid", "message": "授权到期时间缺失"}}
        grace_end = expires + timedelta(days=grace_days)
        if effective_now > grace_end:
            return {"status": "expired", "payload": payload,
                    "error": {"code": "license_expired", "message": "授权已到期，请续费"}}
        if effective_now > expires:
            days_left = max(0, (grace_end - effective_now).days)
            return {"status": "grace", "payload": payload, "features": payload["features"],
                    "grace_days_left": days_left}
        days_left = max(0, (expires - effective_now).days)
        return {"status": "paid", "payload": payload, "features": payload["features"],
                "days_left": days_left, "expires_at": _iso(expires),
                "updates_until": _iso(_parse_iso(payload.get("updates_until")))}

    # ── 对外状态 ──
    def status(self) -> dict:
        with self._lock:
            state = self._load_state()
            effective_now, clock_suspect = self._effective_now(state)
            license_info = self._load_license(effective_now)
            trial = state.get("trial") if isinstance(state.get("trial"), dict) else None
            trial_expires = _parse_iso(trial.get("expires_utc")) if trial else None
            trial_active = bool(trial_expires and effective_now < trial_expires)
            trial_days_left = max(0, (trial_expires - effective_now).days) if trial_active else 0

            result: dict = {
                "state": "free",
                "sku": "free",
                "edition": tiers.TIERS["free"]["edition"],
                "term": None,
                "features": [],
                "limits": tiers.TIERS["free"]["limits"],
                "machine_code": self.machine_code(),
                "machine_display": self.machine_info()["display"],
                "clock_suspect": clock_suspect,
                "can_import": True,
                "keyring_ok": bool(keys.KEYRING_OK),
                "keyring_error": None if keys.KEYRING_OK else "keyring_invalid",
                "license": None,
                "state_recovered": bool(
                    (state.get("flags") or {}).get("state_recovered")),
                "trial": {
                    "used": bool(trial),
                    "active": trial_active,
                    "expires_at": _iso(trial_expires),
                    "days_left": trial_days_left,
                },
            }
            lic_status = license_info.get("status")
            if lic_status in ("paid", "grace"):
                payload = license_info["payload"]
                # 2026-11-05：MCP 归纳到个人版。旧个人版 license 的签名 features
                # 里没有 mcp，这里做本地兼容，避免老用户重新签发才能用 MCP。
                features = list(payload.get("features") or [])
                if payload.get("sku") == "personal" and "mcp" not in features:
                    features.append("mcp")
                result.update({
                    "state": lic_status,
                    "sku": payload["sku"],
                    "edition": payload.get("edition") or tiers.tier_info(payload["sku"])["edition"],
                    "term": payload.get("term"),
                    "features": features,
                    "limits": None,
                    "license": {
                        "license_id": payload.get("license_id"),
                        "order_id": payload.get("order_id"),
                        "sku": payload.get("sku"),
                        "edition": payload.get("edition"),
                        "term": payload.get("term"),
                        "seats": payload.get("seats"),
                        "max_devices": payload.get("max_devices"),
                        "expires_at": payload.get("expires_at"),
                        "updates_until": payload.get("updates_until"),
                        "grace_days": payload.get("grace_days"),
                        "team_id": payload.get("team_id"),
                        "days_left": license_info.get("days_left"),
                        "grace_days_left": license_info.get("grace_days_left"),
                    },
                })
            elif trial_active:
                result.update({
                    "state": "trial",
                    "sku": "trial",
                    "edition": tiers.TIERS["trial"]["edition"],
                    "term": None,
                    "features": tiers.features_for("trial"),
                    "limits": None,
                })
            if lic_status in ("invalid", "machine_mismatch", "not_yet_valid", "expired"):
                result["license_error"] = license_info.get("error")
                result["state"] = "free" if not trial_active else "trial"
            # 保存 last_seen（只前进）与 trial 状态
            old_last = _parse_iso(state.get("last_seen_utc"))
            new_last = max(old_last, effective_now) if old_last else effective_now
            state["last_seen_utc"] = _iso(new_last)
            if clock_suspect:
                state.setdefault("flags", {})["clock_suspect"] = True
            self._save_state(state)
            return result

    def import_license(self, content: str | bytes | dict) -> dict:
        with self._lock:
            try:
                payload = self._verify_container(content)
            except LicenseError as exc:
                # R7：导入失败也要留本地事件（只记类型/错误码，不记内容）
                self._event("license_import_failed", code=exc.code)
                raise
            # 先按当前机器/时间完整校验；过期/错机不允许导入
            state = self._load_state()
            effective_now, _ = self._effective_now(state)
            checked = self._load_license_from_payload(payload, effective_now)
            if checked.get("status") not in ("paid", "grace"):
                error = checked.get("error") or {"code": "license_invalid", "message": "授权文件无效"}
                self._event("license_import_failed", code=error.get("code"))
                raise LicenseError(error["code"], error["message"], 403)
            self.license_dir.mkdir(parents=True, exist_ok=True)
            if self.license_path.is_file():
                try:
                    self.license_path.replace(self.license_path.with_suffix(".json.bak"))
                except Exception:
                    pass
            if isinstance(content, dict):
                raw = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
            else:
                raw = content.decode("utf-8") if isinstance(content, bytes) else str(content)
            tmp = self.license_path.with_suffix(".json.tmp")
            tmp.write_text(raw, encoding="utf-8")
            tmp.replace(self.license_path)
            state["last_license_id"] = payload.get("license_id")
            self._save_state(state)
            self._event("license_import", license_id=payload.get("license_id"),
                        sku=payload.get("sku"), term=payload.get("term"))
            return self.status()

    def _load_license_from_payload(self, payload: dict, effective_now: datetime) -> dict:
        # 与 _load_license 相同的业务校验，供导入时使用（避免重复读文件）
        not_before = _parse_iso(payload.get("not_before"))
        expires = _parse_iso(payload.get("expires_at"))
        if not_before and effective_now < not_before:
            return {"status": "not_yet_valid",
                    "error": {"code": "license_not_yet_valid", "message": "授权尚未生效，请校正系统时间"}}
        code = self.machine_code()
        if not any(_norm_code(c) == code for c in payload.get("machine_codes") or []):
            return {"status": "machine_mismatch",
                    "error": {"code": "license_machine_mismatch",
                              "message": "授权文件不适用于本机，请使用本机识别码重新签发"}}
        if payload.get("term") == "perpetual":
            return {"status": "paid", "payload": payload}
        grace_days = int(payload.get("grace_days") or DEFAULT_GRACE_DAYS)
        if expires is None:
            return {"status": "invalid", "error": {"code": "license_invalid", "message": "授权到期时间缺失"}}
        if effective_now > expires + timedelta(days=grace_days):
            return {"status": "expired", "error": {"code": "license_expired", "message": "授权已到期，请续费"}}
        if effective_now > expires:
            return {"status": "grace", "payload": payload}
        return {"status": "paid", "payload": payload}

    def start_trial(self) -> dict:
        with self._lock:
            state = self._load_state()
            effective_now, _ = self._effective_now(state)
            license_info = self._load_license(effective_now)
            if license_info.get("status") in ("paid", "grace"):
                return self.status()
            trial = state.get("trial") if isinstance(state.get("trial"), dict) else None
            if trial:
                expires = _parse_iso(trial.get("expires_utc"))
                if expires and effective_now < expires:
                    return self.status()
                raise LicenseError("trial_expired", "本机试用已结束", 403)
            # 冗余校验（licensing.md §5/T4）：state 被删后 events.jsonl 里的
            # trial_start 历史仍能阻止重新试用，避免删单个文件白拿 3 天。
            if self._trial_history():
                raise LicenseError("trial_expired", "本机试用已结束", 403)
            expires = effective_now + timedelta(days=TRIAL_DAYS)
            state["trial"] = {
                "used": True,
                "first_run_utc": _iso(effective_now),
                "expires_utc": _iso(expires),
            }
            self._save_state(state)
            self._event("trial_start", expires_utc=_iso(expires))
            return self.status()

    # ── W2 免费额度（复用同一份 state.usage）──
    def _usage_today(self, state: dict, effective_now: datetime, clock_suspect: bool) -> dict:
        usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
        today = effective_now.astimezone().date().isoformat()
        used = int(usage.get("jobs") or 0)
        used_ocr = int(usage.get("ocr_pages") or 0)
        if usage.get("date") != today and not clock_suspect:
            # 本地自然日重置；时钟异常时不重置（防止调到明天刷额度）
            usage = {"date": today, "jobs": 0, "ocr_pages": 0}
        elif not usage.get("date"):
            usage = {"date": today, "jobs": used, "ocr_pages": used_ocr}
        else:
            usage = {"date": str(usage.get("date")), "jobs": used,
                     "ocr_pages": used_ocr}
        state["usage"] = usage
        return usage

    def quota_status(self) -> dict:
        status = self.status()
        with self._lock:
            state = self._load_state()
            effective_now, clock_suspect = self._effective_now(state)
            usage = self._usage_today(state, effective_now, clock_suspect)
            self._save_state(state)
            free = status.get("state") == "free"
            limit = int(tiers.FREE_LIMITS.get("jobs_per_day", 0))
            used = max(0, int(usage.get("jobs") or 0))
            today = effective_now.astimezone().date()
            reset_at = datetime.combine(today + timedelta(days=1),
                                        datetime.min.time()).astimezone().isoformat()
            return {
                "state": status.get("state"),
                "edition": status.get("edition"),
                "free": free,
                "unlimited": not free,
                "limit_today": limit,
                "used_today": used,
                "remaining": max(0, limit - used) if free else None,
                "date": today.isoformat(),
                "reset_at": reset_at,
                "clock_suspect": clock_suspect,
                "state_recovered": bool(
                    (state.get("flags") or {}).get("state_recovered")),
                "ocr_pages_used": int(usage.get("ocr_pages") or 0),
            }

    def consume_free_job(self) -> dict:
        with self._lock:
            status = self.status()
            if status.get("state") != "free":
                return self.quota_status()
            q = self.quota_status()
            if int(q.get("remaining") or 0) <= 0:
                raise LicenseError("quota_exceeded",
                                   "今天的 3 份免费额度用完了。明天 0 点恢复；也可以现在升级，今天就能继续。",
                                   403)
            state = self._load_state()
            effective_now, clock_suspect = self._effective_now(state)
            usage = self._usage_today(state, effective_now, clock_suspect)
            usage["jobs"] = int(usage.get("jobs") or 0) + 1
            state["usage"] = usage
            self._save_state(state)
            return self.quota_status()

    def refund_free_job(self) -> dict:
        with self._lock:
            state = self._load_state()
            effective_now, clock_suspect = self._effective_now(state)
            usage = self._usage_today(state, effective_now, clock_suspect)
            usage["jobs"] = max(0, int(usage.get("jobs") or 0) - 1)
            state["usage"] = usage
            self._save_state(state)
            return self.quota_status()

    def add_ocr_pages(self, pages: int) -> dict:
        """累加当日 OCR 页数（H-16：/api/quota/status.ocr_pages_used 不再恒为 0）。"""
        try:
            count = max(0, int(pages or 0))
        except (TypeError, ValueError):
            count = 0
        with self._lock:
            state = self._load_state()
            effective_now, clock_suspect = self._effective_now(state)
            usage = self._usage_today(state, effective_now, clock_suspect)
            usage["ocr_pages"] = int(usage.get("ocr_pages") or 0) + count
            state["usage"] = usage
            self._save_state(state)
            return self.quota_status()

    def _trial_history(self) -> bool:
        """events.jsonl 里是否已有本次安装的 trial_start（试用防删冗余）。"""
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        for line in lines:
            try:
                if json.loads(line).get("event") == "trial_start":
                    return True
            except Exception:
                continue
        return False

    def license_content(self) -> str | None:
        try:
            if self.license_path.is_file():
                return self.license_path.read_text(encoding="utf-8")
        except Exception:
            pass
        return None

    def events(self, limit: int = 20) -> list[dict]:
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        out: list[dict] = []
        for line in lines[-max(1, min(100, int(limit))):]:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    out.append(item)
            except Exception:
                continue
        return out

    def has_feature(self, feature: str) -> bool:
        return feature in (self.status().get("features") or [])
