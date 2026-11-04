# -*- coding: utf-8 -*-
"""授权签发（复用产品 ``yinzhan.licensing.issue``，不重复实现密码学逻辑）。

- 私钥只在商业服务机器上；本模块不生成私钥、不打印私钥/口令。
- 签发结果 ``text`` 是紧凑单行 JSON（容器：schema/alg/key_id/payload/sig），
  与产品「授权」页粘贴导入使用的一整行授权码格式完全一致。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("commercial.signer")

#: Sanitized_Data/ 根目录（本文件 = commercial/server/signer.py）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
#: 产品后端源码目录（含 yinzhan 包）
APP_SERVER_DIR = PROJECT_ROOT / "app" / "server"

#: 年度授权天数（契约 §6：days=365）
LICENSE_DAYS = 365
#: 签发备注（契约 §6：notes="online"）
LICENSE_NOTES = "online"

#: 允许自助售卖的 SKU（orders 只用这两个）
SELF_SERVE_SKUS = ("personal", "pro")


class SignerError(Exception):
    """签发/机器码错误；``code``/``message`` 直接映射到 API 错误体。"""

    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def ensure_app_server_path() -> None:
    """把 ``Sanitized_Data/app/server`` 加入 sys.path（幂等）。"""
    path = str(APP_SERVER_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)


ensure_app_server_path()

# noqa: E402 —— 依赖上面的 sys.path 注入，必须放在函数调用后导入
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from yinzhan.licensing import tiers  # noqa: E402
from yinzhan.licensing.issue import (  # noqa: E402
    build_payload,
    normalize_machine_code as _normalize_machine_code,
    sign_license,
)

__all__ = [
    "SignerError", "LicenseSigner",
    "normalize_machine_code", "is_valid_machine_code", "machine_code_display",
    "ensure_app_server_path", "LICENSE_DAYS", "LICENSE_NOTES", "SELF_SERVE_SKUS",
]


def normalize_machine_code(value: Any) -> str:
    """校验并规范化机器码（返回 XXXX-XXXX-XXXX-XXXX 形式）。

    非法时抛 :class:`SignerError`（code=machine_code_invalid, HTTP 400），
    错误信息不回显用户原始输入之外的内容，便于 API 直接返回。
    """
    text = "" if value is None else str(value)
    try:
        return _normalize_machine_code(text)
    except ValueError:
        raise SignerError(
            "machine_code_invalid",
            "机器码格式无效，应为 XXXX-XXXX-XXXX-XXXX（在软件「授权」页复制本机识别码）",
            400,
        ) from None


def is_valid_machine_code(value: Any) -> bool:
    try:
        normalize_machine_code(value)
        return True
    except SignerError:
        return False


def machine_code_display(value: Any) -> str:
    """容错地取展示形式；非法时返回空串（用于展示，不抛异常）。"""
    try:
        return normalize_machine_code(value)
    except SignerError:
        return ""


class LicenseSigner:
    """Ed25519 license 签发器（懒加载私钥；线程安全）。

    Parameters
    ----------
    private_key_path:
        PEM 私钥路径（配置项 ``LICENSE_PRIVATE_KEY_PATH``）。
    key_id:
        写入 license 容器的 ``key_id``（生产 ``prod-2026``，测试 ``test-2026``）。
    password_env:
        私钥口令所在环境变量名（``LICENSE_KEY_PASSWORD_ENV``）；未设置按无口令处理。
    days:
        年度授权天数（默认 365）。
    """

    def __init__(self, private_key_path: str | Path,
                 key_id: str = "prod-2026",
                 password_env: str = "LICENSE_KEY_PASSWORD",
                 days: int = LICENSE_DAYS):
        self.private_key_path = Path(private_key_path).expanduser()
        self.key_id = str(key_id or "prod-2026")
        self.password_env = str(password_env or "LICENSE_KEY_PASSWORD")
        self.days = int(days if days and days > 0 else LICENSE_DAYS)
        self._key: Ed25519PrivateKey | None = None
        self._lock = threading.Lock()

    # ── 私钥 ──
    def _password(self) -> bytes | None:
        raw = os.environ.get(self.password_env) or ""
        return raw.encode("utf-8") if raw else None

    def _load_key(self) -> Ed25519PrivateKey:
        with self._lock:
            if self._key is not None:
                return self._key
            path = self.private_key_path
            if not path.is_file():
                # 只报路径（运维需要），绝不读取/打印内容；路径不是密钥本身。
                raise SignerError(
                    "license_key_missing",
                    f"授权签名私钥不存在：{path}；请配置 LICENSE_PRIVATE_KEY_PATH 后重启服务",
                    503,
                )
            try:
                key = serialization.load_pem_private_key(path.read_bytes(), password=self._password())
            except TypeError:
                # cryptography 在「私钥未加密但给了口令」/「类型不对」时抛 TypeError
                raise SignerError(
                    "license_key_invalid",
                    "授权签名私钥读取失败（口令与私钥不匹配，请检查 LICENSE_KEY_PASSWORD）",
                    503,
                ) from None
            except Exception:
                logger.error("license private key load failed: path=%s", path)
                raise SignerError(
                    "license_key_invalid",
                    "授权签名私钥读取失败（文件格式或口令错误）",
                    503,
                ) from None
            if not isinstance(key, Ed25519PrivateKey):
                raise SignerError(
                    "license_key_invalid", "授权签名私钥类型错误（需要 Ed25519 私钥）", 503)
            self._key = key
            logger.info("license signing key loaded: key_id=%s", self.key_id)
            return key

    # ── 签发 ──
    def issue(self, *, sku: str, machine_code: str, order_no: str,
              days: int | None = None) -> dict[str, Any]:
        """构造并签名 license；返回供 API/DB 使用的字段字典。

        失败抛 :class:`SignerError`（不会把私钥/口令写进消息或日志）。
        """
        key = self._load_key()
        if not order_no:
            raise SignerError("license_issue_failed", "缺少订单号，无法签发授权", 500)
        if sku not in tiers.TIERS or sku in ("free", "trial"):
            raise SignerError("license_issue_failed", "套餐不支持签发授权", 500)
        try:
            payload = build_payload(
                sku=sku,
                machine_codes=[machine_code],
                order_id=str(order_no),
                days=int(days if days is not None else self.days),
                notes=LICENSE_NOTES,
            )
            container = sign_license(payload, key, self.key_id)
        except SignerError:
            raise
        except Exception:
            logger.exception("license signing failed: order=%s sku=%s", order_no, sku)
            raise SignerError("license_issue_failed", "授权签发失败，请稍后重试", 500) from None
        # 紧凑单行 JSON：与「授权页粘贴整行授权码」兼容
        text = json.dumps(container, ensure_ascii=False, separators=(",", ":"))
        payload = container["payload"]
        return {
            "license_id": str(payload["license_id"]),
            "text": text,
            "sku": str(payload["sku"]),
            "machine_code": str((payload.get("machine_codes") or [""])[0]),
            "issued_at": str(payload.get("issued_at") or ""),
            "expires_at": payload.get("expires_at"),
            "edition": payload.get("edition"),
        }

    # ── 运维自检（不含密钥内容） ──
    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "key_id": self.key_id,
            "private_key_configured": self.private_key_path.is_file(),
            "loaded": self._key is not None,
        }
        if not info["private_key_configured"]:
            return info
        try:
            self._load_key()
            info["loaded"] = True
            info["error"] = None
        except SignerError as exc:
            info["loaded"] = False
            info["error"] = {"code": exc.code, "message": exc.message}
        return info
