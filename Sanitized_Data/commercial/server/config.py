# -*- coding: utf-8 -*-
"""商业服务配置：环境变量名 -> ``Config`` 同名属性（便于 T2 支付宝适配器按名读取）。

设计要点：
- 属性名与 env 完全相同（``config.ALIPAY_APP_ID`` 等），值做最小规范化
  （路径展开绝对化、布尔/整数转换、CORS 列表拆分）。
- ``Config(env={})`` 可用于测试构造完全确定的配置；``Config.load()`` 会额外读取
  ``commercial/.env``（不覆盖已存在的进程环境变量）。
- 任何 ``repr``/日志辅助都不输出私钥路径、口令名、admin token 等敏感信息。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

#: commercial/ 目录（本文件 = commercial/server/config.py）
COMMERCIAL_DIR = Path(__file__).resolve().parents[1]
#: Sanitized_Data/ 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]
#: 静态页面目录
STATIC_DIR = Path(__file__).resolve().parent / "static"
#: 打包数据目录（默认 COMMERCIAL_DATA_DIR=commercial/var）
DEFAULT_DATA_DIR = "commercial/var"
DEFAULT_DB_PATH = "commercial/var/commercial.db"

_VERSION = "1.0.0"

_TRUE_TEXT = {"1", "true", "yes", "on", "y", "t"}
_FALSE_TEXT = {"0", "false", "no", "off", "n", "f", ""}


class ConfigError(ValueError):
    """配置错误：启动阶段直接失败，避免带病上线。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _default_license_key_path() -> str:
    """默认私钥路径：%USERPROFILE%\\.sanitized-data-license-keys\\license_signing_ed25519.pem"""
    raw = os.path.expandvars(r"%USERPROFILE%\.sanitized-data-license-keys\license_signing_ed25519.pem")
    if "%USERPROFILE%" in raw or raw.startswith("%"):  # 非 Windows 兜底
        return str(Path.home() / ".sanitized-data-license-keys" / "license_signing_ed25519.pem")
    return raw


def _resolve_path(value: str, base: Path = PROJECT_ROOT) -> Path:
    """展开 %VAR%/~，相对路径按 Sanitized_Data 根目录解析（与运行时 cwd 无关）。"""
    text = os.path.expandvars(os.path.expanduser(str(value or "").strip()))
    path = Path(text)
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def _get(env: Mapping[str, str], name: str, default: str = "") -> str:
    raw = env.get(name)
    if raw is None:
        return default
    return str(raw).strip()


def _get_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _get(env, name)
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} 必须是整数，当前值：{raw!r}") from None


def _get_bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = _get(env, name)
    if raw == "":
        return default
    lowered = raw.lower()
    if lowered in _TRUE_TEXT:
        return True
    if lowered in _FALSE_TEXT:
        return False
    raise ConfigError(f"{name} 必须是布尔值（true/false/1/0），当前值：{raw!r}")


def _split_origins(raw: str) -> list[str]:
    out: list[str] = []
    for item in str(raw or "").split(","):
        origin = item.strip().rstrip("/")
        if origin and origin not in out:
            out.append(origin)
    return out


class Config:
    """商业服务运行配置（全部来自环境变量/构造覆盖，不读文件系统私密内容）。"""

    VERSION = _VERSION

    def __init__(self, env: Mapping[str, str] | None = None,
                 overrides: Mapping[str, Any] | None = None) -> None:
        base: dict[str, str] = {}
        if env is not None:
            base.update({str(k): str(v) for k, v in env.items() if v is not None})
        if overrides:
            base.update({str(k): str(v) for k, v in overrides.items() if v is not None})
        env = base

        # ── 基础 ──
        self.COMMERCIAL_HOST = _get(env, "COMMERCIAL_HOST", "127.0.0.1")
        self.COMMERCIAL_PORT = _get_int(env, "COMMERCIAL_PORT", 8787)
        self.COMMERCIAL_BASE_URL = _get(
            env, "COMMERCIAL_BASE_URL",
            f"http://{self.COMMERCIAL_HOST}:{self.COMMERCIAL_PORT}",
        ).rstrip("/")
        self.COMMERCIAL_DATA_DIR = _get(env, "COMMERCIAL_DATA_DIR", DEFAULT_DATA_DIR)
        self.COMMERCIAL_DB = _get(
            env, "COMMERCIAL_DB",
            str(Path(self.COMMERCIAL_DATA_DIR) / "commercial.db") if self.COMMERCIAL_DATA_DIR
            else DEFAULT_DB_PATH,
        )
        self.COMMERCIAL_CORS_EXTRA_ORIGINS = _get(env, "COMMERCIAL_CORS_EXTRA_ORIGINS", "")

        # ── 支付通道 ──
        self.PAYMENT_PROVIDER = (_get(env, "PAYMENT_PROVIDER", "mock") or "mock").lower()
        mock_default = self.PAYMENT_PROVIDER == "mock"
        self.PAYMENT_MOCK_ENABLED = _get_bool(env, "PAYMENT_MOCK_ENABLED", mock_default) or mock_default

        # ── 支付宝（T2 读取；未配置时留空，由适配器给出明确错误） ──
        self.ALIPAY_APP_ID = _get(env, "ALIPAY_APP_ID", "")
        self.ALIPAY_APP_PRIVATE_KEY_PATH = str(_resolve_path(
            _get(env, "ALIPAY_APP_PRIVATE_KEY_PATH", "commercial/var/keys/alipay_app_private.pem")))
        self.ALIPAY_PUBLIC_KEY_PATH = str(_resolve_path(
            _get(env, "ALIPAY_PUBLIC_KEY_PATH", "commercial/var/keys/alipay_public.pem")))
        self.ALIPAY_GATEWAY = _get(env, "ALIPAY_GATEWAY", "https://openapi.alipay.com/gateway.do")
        self.ALIPAY_PAY_MODE = (_get(env, "ALIPAY_PAY_MODE", "precreate") or "precreate").lower()
        # 电脑网站支付扫码模式：2=支付宝页面扫码（跳转），0/1/3/4=商家页面 iframe 扫码
        self.ALIPAY_QR_PAY_MODE = _get(env, "ALIPAY_QR_PAY_MODE", "").strip()
        self.ALIPAY_QRCODE_WIDTH = _get_int(env, "ALIPAY_QRCODE_WIDTH", 220)
        notify_url = _get(env, "ALIPAY_NOTIFY_URL", "")
        self.ALIPAY_NOTIFY_URL = notify_url.rstrip("/") if notify_url else f"{self.COMMERCIAL_BASE_URL}/notify/alipay"
        self.ALIPAY_SELLER_ID = _get(env, "ALIPAY_SELLER_ID", "")
        self.ALIPAY_APP_ID_CHECK = _get_bool(env, "ALIPAY_APP_ID_CHECK", True)

        # ── 支付宝主动查单兜底（notify 不可达/延迟时，GET 订单轮询按订单节流主动查单） ──
        # 默认关闭：只有显式开启后，GET /api/orders/{no} 才会对 pending 的支付宝订单调用
        # alipay.trade.query；notify 回调始终保留且幂等，两条链路谁先到谁签发。
        self.ALIPAY_QUERY_FALLBACK = _get_bool(env, "ALIPAY_QUERY_FALLBACK", False)
        self.ALIPAY_QUERY_MIN_INTERVAL_SECONDS = _get_int(
            env, "ALIPAY_QUERY_MIN_INTERVAL_SECONDS", 5)
        self.ALIPAY_QUERY_TIMEOUT = _get_int(env, "ALIPAY_QUERY_TIMEOUT", 10)

        # ── 授权签发 ──
        self.LICENSE_PRIVATE_KEY_PATH = str(_resolve_path(
            _get(env, "LICENSE_PRIVATE_KEY_PATH", _default_license_key_path())))
        self.LICENSE_KEY_PASSWORD_ENV = _get(env, "LICENSE_KEY_PASSWORD_ENV", "LICENSE_KEY_PASSWORD")
        self.LICENSE_KEY_ID = _get(env, "LICENSE_KEY_ID", "prod-2026")

        # ── 价格（分） ──
        self.COMMERCIAL_PRICE_PERSONAL_CENTS = _get_int(env, "COMMERCIAL_PRICE_PERSONAL_CENTS", 19900)
        self.COMMERCIAL_PRICE_PRO_CENTS = _get_int(env, "COMMERCIAL_PRICE_PRO_CENTS", 49900)
        self.COMMERCIAL_PRICE_TEAM_CENTS = _get_int(env, "COMMERCIAL_PRICE_TEAM_CENTS", 199900)
        # 线上自助只保留个人版（价格页/授权页已只展示 199）；专业版仍可由后台/人工开通。
        self.COMMERCIAL_SELF_SERVE_PRO_ENABLED = _get_bool(
            env, "COMMERCIAL_SELF_SERVE_PRO_ENABLED", True)

        # ── 管理令牌（预留：本地 admin CLI 以文件访问为权限，token 供未来接口/运维使用） ──
        self.COMMERCIAL_ADMIN_TOKEN = _get(env, "COMMERCIAL_ADMIN_TOKEN", "")

        # ── 可选扩展（默认值即契约行为；.env.example 有注释） ──
        self.COMMERCIAL_ORDER_TTL_SECONDS = _get_int(env, "COMMERCIAL_ORDER_TTL_SECONDS", 15 * 60)
        self.COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE = _get_int(
            env, "COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE", 10)
        self.COMMERCIAL_LOG_LEVEL = (_get(env, "COMMERCIAL_LOG_LEVEL", "INFO") or "INFO").upper()
        self.COMMERCIAL_TRUST_PROXY = _get_bool(env, "COMMERCIAL_TRUST_PROXY", False)

        # ── 派生路径/派生值 ──
        self.data_dir = _resolve_path(self.COMMERCIAL_DATA_DIR)
        self.db_path = _resolve_path(self.COMMERCIAL_DB)
        self.cors_extra_origins = _split_origins(self.COMMERCIAL_CORS_EXTRA_ORIGINS)
        self.prices: dict[str, int] = {
            "personal": self.COMMERCIAL_PRICE_PERSONAL_CENTS,
            "pro": self.COMMERCIAL_PRICE_PRO_CENTS,
            "team": self.COMMERCIAL_PRICE_TEAM_CENTS,
        }
        self._validate()

    # ── 构造 ──
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None,
                 overrides: Mapping[str, Any] | None = None) -> "Config":
        """纯进程环境（测试用；不读 .env 文件）。"""
        return cls(env=os.environ if env is None else env, overrides=overrides)

    @classmethod
    def load(cls, env_path: Path | str | None = None,
             overrides: Mapping[str, Any] | None = None) -> "Config":
        """读取 env 文件（默认 commercial/.env，存在才读），进程环境变量优先。"""
        merged: dict[str, str] = {}
        path = Path(env_path) if env_path else (COMMERCIAL_DIR / ".env")
        if path.is_file():
            merged.update(read_env_file(path))
        merged.update({str(k): str(v) for k, v in os.environ.items() if v is not None})
        return cls(env=merged, overrides=overrides)

    # ── 校验/派生 ──
    def _validate(self) -> None:
        if self.PAYMENT_PROVIDER not in ("mock", "alipay"):
            raise ConfigError(f"PAYMENT_PROVIDER 必须是 mock 或 alipay，当前：{self.PAYMENT_PROVIDER!r}")
        if self.ALIPAY_PAY_MODE not in ("precreate", "page"):
            raise ConfigError(f"ALIPAY_PAY_MODE 必须是 precreate 或 page，当前：{self.ALIPAY_PAY_MODE!r}")
        if self.ALIPAY_QR_PAY_MODE not in ("", "2"):
            raise ConfigError(
                "当前仅支持 ALIPAY_QR_PAY_MODE=2（支付宝页面扫码，最安全），"
                f"当前：{self.ALIPAY_QR_PAY_MODE!r}")
        if not (75 <= int(self.ALIPAY_QRCODE_WIDTH) <= 1000):
            raise ConfigError("ALIPAY_QRCODE_WIDTH 必须在 75~1000 之间")
        if int(self.ALIPAY_QUERY_MIN_INTERVAL_SECONDS) < 2:
            raise ConfigError(
                "ALIPAY_QUERY_MIN_INTERVAL_SECONDS 至少为 2 秒（避免轮询高频查单触发支付宝限流）")
        if not (1 <= int(self.ALIPAY_QUERY_TIMEOUT) <= 60):
            raise ConfigError("ALIPAY_QUERY_TIMEOUT 必须在 1~60 秒之间")
        if not (0 < int(self.COMMERCIAL_PORT) <= 65535):
            raise ConfigError(f"COMMERCIAL_PORT 非法：{self.COMMERCIAL_PORT}")
        for name in ("COMMERCIAL_PRICE_PERSONAL_CENTS", "COMMERCIAL_PRICE_PRO_CENTS",
                     "COMMERCIAL_PRICE_TEAM_CENTS"):
            if int(getattr(self, name)) <= 0:
                raise ConfigError(f"{name} 必须为正整数（单位：分）")
        if int(self.COMMERCIAL_ORDER_TTL_SECONDS) < 60:
            raise ConfigError("COMMERCIAL_ORDER_TTL_SECONDS 太小（至少 60 秒）")
        if int(self.COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE) < 1:
            raise ConfigError("COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE 至少为 1")

    @property
    def mock_enabled(self) -> bool:
        return bool(self.PAYMENT_MOCK_ENABLED)

    def ensure_dirs(self) -> None:
        """创建数据目录（不创建私钥目录，避免误以为生成密钥）。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def to_env_dict(self) -> dict[str, str]:
        """导出可回填进程环境的公开配置（供 run_dev --reload 子进程复用）。"""
        return {
            "COMMERCIAL_HOST": self.COMMERCIAL_HOST,
            "COMMERCIAL_PORT": str(self.COMMERCIAL_PORT),
            "COMMERCIAL_BASE_URL": self.COMMERCIAL_BASE_URL,
            "COMMERCIAL_DATA_DIR": self.COMMERCIAL_DATA_DIR,
            "COMMERCIAL_DB": self.COMMERCIAL_DB,
            "COMMERCIAL_CORS_EXTRA_ORIGINS": self.COMMERCIAL_CORS_EXTRA_ORIGINS,
            "PAYMENT_PROVIDER": self.PAYMENT_PROVIDER,
            "PAYMENT_MOCK_ENABLED": "true" if self.PAYMENT_MOCK_ENABLED else "false",
            "ALIPAY_APP_ID": self.ALIPAY_APP_ID,
            "ALIPAY_APP_PRIVATE_KEY_PATH": self.ALIPAY_APP_PRIVATE_KEY_PATH,
            "ALIPAY_PUBLIC_KEY_PATH": self.ALIPAY_PUBLIC_KEY_PATH,
            "ALIPAY_GATEWAY": self.ALIPAY_GATEWAY,
            "ALIPAY_PAY_MODE": self.ALIPAY_PAY_MODE,
            "ALIPAY_QR_PAY_MODE": self.ALIPAY_QR_PAY_MODE,
            "ALIPAY_QRCODE_WIDTH": str(self.ALIPAY_QRCODE_WIDTH),
            "ALIPAY_NOTIFY_URL": self.ALIPAY_NOTIFY_URL,
            "ALIPAY_SELLER_ID": self.ALIPAY_SELLER_ID,
            "ALIPAY_APP_ID_CHECK": "1" if self.ALIPAY_APP_ID_CHECK else "0",
            "ALIPAY_QUERY_FALLBACK": "true" if self.ALIPAY_QUERY_FALLBACK else "false",
            "ALIPAY_QUERY_MIN_INTERVAL_SECONDS": str(self.ALIPAY_QUERY_MIN_INTERVAL_SECONDS),
            "ALIPAY_QUERY_TIMEOUT": str(self.ALIPAY_QUERY_TIMEOUT),
            "LICENSE_PRIVATE_KEY_PATH": self.LICENSE_PRIVATE_KEY_PATH,
            "LICENSE_KEY_PASSWORD_ENV": self.LICENSE_KEY_PASSWORD_ENV,
            "LICENSE_KEY_ID": self.LICENSE_KEY_ID,
            "COMMERCIAL_PRICE_PERSONAL_CENTS": str(self.COMMERCIAL_PRICE_PERSONAL_CENTS),
            "COMMERCIAL_PRICE_PRO_CENTS": str(self.COMMERCIAL_PRICE_PRO_CENTS),
            "COMMERCIAL_PRICE_TEAM_CENTS": str(self.COMMERCIAL_PRICE_TEAM_CENTS),
            "COMMERCIAL_SELF_SERVE_PRO_ENABLED": (
                "true" if self.COMMERCIAL_SELF_SERVE_PRO_ENABLED else "false"),
            "COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE": str(
                self.COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE),
            "COMMERCIAL_ORDER_TTL_SECONDS": str(self.COMMERCIAL_ORDER_TTL_SECONDS),
            "COMMERCIAL_LOG_LEVEL": self.COMMERCIAL_LOG_LEVEL,
        }

    def safe_summary(self) -> dict[str, Any]:
        """可安全打印/返回的摘要（无路径、无 token、无口令）。"""
        return {
            "provider": self.PAYMENT_PROVIDER,
            "mock_enabled": bool(self.PAYMENT_MOCK_ENABLED),
            "base_url": self.COMMERCIAL_BASE_URL,
            "data_dir_configured": bool(self.COMMERCIAL_DATA_DIR),
            "db_configured": bool(self.COMMERCIAL_DB),
            "license_key_id": self.LICENSE_KEY_ID,
            "alipay_query_fallback": bool(self.ALIPAY_QUERY_FALLBACK),
            "prices": dict(self.prices),
            "cors_extra_origin_count": len(self.cors_extra_origins),
            "version": self.VERSION,
        }

    def __repr__(self) -> str:  # 不含敏感信息
        return (f"Config(provider={self.PAYMENT_PROVIDER!r}, "
                f"base_url={self.COMMERCIAL_BASE_URL!r}, "
                f"key_id={self.LICENSE_KEY_ID!r})")


def read_env_file(path: Path | str) -> dict[str, str]:
    """最小 .env 解析（KEY=VALUE，# 注释，可选引号）。

    不引入 python-dotenv 运行依赖；仅供 run_dev/运维本地使用。
    """
    result: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[len("export "):].strip()
        if "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result


__all__ = [
    "Config", "ConfigError", "read_env_file",
    "COMMERCIAL_DIR", "PROJECT_ROOT", "STATIC_DIR",
]
