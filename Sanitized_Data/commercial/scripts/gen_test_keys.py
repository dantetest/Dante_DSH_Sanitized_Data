#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 L3 付费解锁的**测试密钥**（仅开发/测试/演示用，绝不用于生产）。

产物（默认 ``commercial/var/test_keys/``，该目录被 ``commercial/.gitignore`` 排除）：

=============================================  ============================================
文件                                            内容
=============================================  ============================================
``license_signing_ed25519.pem``                Ed25519 license 签发私钥（PKCS8 PEM，无口令）
``license_public_keys_test.json``              产品公钥表覆盖文件（格式同 public_keys.json）：
                                               ``{"test-2026": "<base64 raw Ed25519 公钥>"}``
``app_private.pem`` / ``app_public.pem``       「应用密钥对」（RSA 2048，PKCS8 PEM / SPKI PEM）：
                                               商业服务用应用私钥给支付宝请求签名，应用公钥上传到
                                               支付宝开放平台（真实联调时替换成你自己的）
``alipay_private.pem`` / ``alipay_public.pem`` 「支付宝密钥对」（RSA 2048）：测试里扮演支付宝，
                                               用 ``alipay_private.pem`` 给异步通知签名；商业服务用
                                               ``alipay_public.pem`` 验签（真实联调时换成支付宝公钥）
=============================================  ============================================

安全约束：
* 只生成**测试**密钥，文件名/路径都带 test_keys，且默认目录已被 gitignore；
* **不打印任何私钥内容**，只打印路径、算法、位数和公钥 SHA256 指纹；
* 幂等：已存在的文件不覆盖（``--force`` 才重新生成；轮换后旧的测试 license 全部失效）。

用法::

    python -X utf8 commercial/scripts/gen_test_keys.py
    python -X utf8 commercial/scripts/gen_test_keys.py --force
    python -X utf8 commercial/scripts/gen_test_keys.py --out-dir commercial/var/test_keys --json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
#: commercial/ 目录（本文件 = commercial/scripts/gen_test_keys.py）
COMMERCIAL_DIR = SCRIPT_DIR.parent
#: 默认输出目录（gitignore：commercial/var/**）
DEFAULT_OUT_DIR = COMMERCIAL_DIR / "var" / "test_keys"
#: 测试 license 的 key_id（产品公钥表里的键名；生产是 prod-2026）
TEST_KEY_ID = "test-2026"
#: RSA 测试密钥位数（支付宝 RSA2 要求 >= 2048）
RSA_KEY_SIZE = 2048

#: 文件名（对外暴露，e2e / demo 脚本按这些名字定位）
LICENSE_PRIVATE = "license_signing_ed25519.pem"
LICENSE_PUBLIC_JSON = "license_public_keys_test.json"
APP_PRIVATE = "app_private.pem"
APP_PUBLIC = "app_public.pem"
ALIPAY_PRIVATE = "alipay_private.pem"
ALIPAY_PUBLIC = "alipay_public.pem"


class KeyGenError(RuntimeError):
    """密钥生成/校验失败（消息不含私钥内容）。"""


# ---------------------------------------------------------------------------
# 路径 / 基础工具
# ---------------------------------------------------------------------------
def default_paths(out_dir: Path | str | None = None) -> dict[str, Path]:
    """返回全部测试密钥的绝对路径字典（不创建目录）。

    ``out_dir`` 为空时用 ``commercial/var/test_keys``；相对路径按**当前工作目录**解析
    （标准 CLI 语义，便于 ``--out-dir commercial/var/test_keys`` 这种写法）。
    """
    root = Path(out_dir).expanduser() if out_dir else DEFAULT_OUT_DIR
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    return {
        "out_dir": root,
        "license_private": root / LICENSE_PRIVATE,
        "license_public_json": root / LICENSE_PUBLIC_JSON,
        "app_private": root / APP_PRIVATE,
        "app_public": root / APP_PUBLIC,
        "alipay_private": root / ALIPAY_PRIVATE,
        "alipay_public": root / ALIPAY_PUBLIC,
    }


def _sha256_fingerprint(public_der: bytes) -> str:
    """公钥 DER(SPKI) 的 SHA256 指纹（十六进制，只含公钥材料）。"""
    return "SHA256:" + hashlib.sha256(public_der).hexdigest()


def _write_private_file(path: Path, data: bytes) -> None:
    """写私钥文件：先写临时文件再原子替换；POSIX 下尽力收紧到 0600。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    try:  # Windows 上 chmod 基本无意义，忽略失败
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_public_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _load_ed25519_private(path: Path) -> Any:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as exc:  # noqa: BLE001 - 只报告类型，不回显密钥内容
        raise KeyGenError(
            f"已有 license 私钥无法解析（{path}，{type(exc).__name__}）；"
            "如确认要重新生成请加 --force"
        ) from None
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyGenError(
            f"已有 license 私钥类型不是 Ed25519（{path}）；如要重新生成请加 --force")
    return key


def _ed25519_public_raw(key: Any) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _load_rsa_private(path: Path) -> Any:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as exc:  # noqa: BLE001
        raise KeyGenError(
            f"已有 RSA 私钥无法解析（{path}，{type(exc).__name__}）；"
            "如确认要重新生成请加 --force"
        ) from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise KeyGenError(
            f"已有 RSA 私钥类型不对（{path}）；如要重新生成请加 --force")
    return key


def _rsa_public_der(key: Any) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


# ---------------------------------------------------------------------------
# 生成逻辑
# ---------------------------------------------------------------------------
def _generate_license_pair(paths: dict[str, Path], *, force: bool, key_id: str,
                           report: dict[str, Any]) -> None:
    """Ed25519 license 密钥对：私钥 PEM + 产品公钥表 JSON。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv_path = paths["license_private"]
    json_path = paths["license_public_json"]
    created: list[str] = report["created"]
    reused: list[str] = report["reused"]

    key = None
    if force:
        pass
    elif priv_path.is_file():
        key = _load_ed25519_private(priv_path)
        reused.append(str(priv_path))
    elif json_path.is_file():
        # 只有公钥、没有私钥：无法恢复私钥，必须 --force 重新生成（否则会静默产生“签不出”的环境）
        raise KeyGenError(
            f"只有 license 公钥表（{json_path}）而没有私钥（{priv_path}），"
            "无法恢复；请加 --force 重新生成一对测试密钥"
        )

    if key is None:
        key = Ed25519PrivateKey.generate()
        _write_private_file(priv_path, key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        created.append(str(priv_path))

    public_raw = _ed25519_public_raw(key)
    public_b64 = base64.b64encode(public_raw).decode("ascii")

    need_json = True
    if not force and json_path.is_file():
        try:
            existing = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 格式坏了就按需重写（公钥可无损从私钥推导）
            existing = None
        if isinstance(existing, dict) and existing.get(key_id) == public_b64:
            need_json = False
            reused.append(str(json_path))
            report["license_public_b64"] = public_b64
        elif isinstance(existing, dict) and existing.get(key_id) and existing.get(key_id) != public_b64:
            # 私钥与公钥表不匹配：这比缺文件更危险（签出的 license 无法导入），必须显式重建
            raise KeyGenError(
                f"license 私钥与公钥表不匹配（{json_path} 里的 {key_id} 公钥不是当前私钥对应的公钥）；"
                "请加 --force 重新生成一对测试密钥"
            )
    if need_json:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps({key_id: public_b64}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        created.append(str(json_path))
        report["license_public_b64"] = public_b64

    report["fingerprints"]["license_ed25519"] = _sha256_fingerprint(public_raw)
    report["key_id"] = key_id


def _generate_rsa_pair(private_path: Path, public_path: Path, *,
                       force: bool, report: dict[str, Any],
                       label: str, fingerprint_key: str) -> None:
    """RSA 密钥对（PKCS8 私钥 PEM + SPKI 公钥 PEM）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    created: list[str] = report["created"]
    reused: list[str] = report["reused"]

    key = None
    if force:
        pass
    elif private_path.is_file():
        key = _load_rsa_private(private_path)
        reused.append(str(private_path))
    elif public_path.is_file():
        raise KeyGenError(
            f"只有 {label} 公钥（{public_path}）而没有私钥（{private_path}），"
            "无法恢复；请加 --force 重新生成一对测试密钥"
        )

    if key is None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)
        _write_private_file(private_path, key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        created.append(str(private_path))

    expected_der = _rsa_public_der(key)
    need_public = True
    if not force and public_path.is_file():
        try:
            current = serialization.load_pem_public_key(public_path.read_bytes())
            current_der = current.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        except Exception:  # noqa: BLE001 - 坏了就用私钥重写公钥（公钥可无损推导）
            current_der = None
        if current_der == expected_der:
            need_public = False
            reused.append(str(public_path))
    if need_public:
        _write_public_file(public_path, key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
        created.append(str(public_path))

    report["fingerprints"][fingerprint_key] = _sha256_fingerprint(expected_der)
    report["rsa_bits"] = RSA_KEY_SIZE


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------
def ensure_test_keys(out_dir: Path | str | None = None, *, force: bool = False,
                     key_id: str = TEST_KEY_ID, quiet: bool = False) -> dict[str, Any]:
    """确保测试密钥存在（幂等），返回可供脚本/e2e 使用的信息。

    返回字典::

        {
          "out_dir": str, "key_id": "test-2026",
          "paths": {license_private, license_public_json, app_private, app_public,
                    alipay_private, alipay_public},
          "created": [str, ...], "reused": [str, ...],
          "fingerprints": {"license_ed25519": "SHA256:...", "app_rsa": "...", "alipay_rsa": "..."},
          "license_public_b64": "..."
        }

    失败抛 :class:`KeyGenError`（消息里只有路径/类型，不含私钥内容）。
    """
    paths = default_paths(out_dir)
    paths["out_dir"].mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "out_dir": str(paths["out_dir"]),
        "key_id": key_id,
        "paths": {name: str(path) for name, path in paths.items() if name != "out_dir"},
        "created": [],
        "reused": [],
        "fingerprints": {},
        "license_public_b64": "",
        "rsa_bits": RSA_KEY_SIZE,
    }

    _generate_license_pair(paths, force=force, key_id=key_id, report=report)
    _generate_rsa_pair(paths["app_private"], paths["app_public"], force=force,
                       report=report, label="应用 RSA", fingerprint_key="app_rsa")
    _generate_rsa_pair(paths["alipay_private"], paths["alipay_public"], force=force,
                       report=report, label="支付宝 RSA", fingerprint_key="alipay_rsa")

    if not quiet:
        _print_summary(report)
    return report


def _print_summary(report: dict[str, Any]) -> None:
    """打印路径/类型/指纹（绝不打印私钥或口令内容）。"""
    print("=" * 78)
    print("L3 测试密钥（仅开发/测试/演示用；商业用途请换成你自己的密钥）")
    print(f"  输出目录 : {report['out_dir']}   (commercial/var/** 已被 gitignore)")
    print(f"  新增文件 : {len(report['created'])} 个；复用已有 : {len(report['reused'])} 个")
    for name, value in (("license 私钥", "license_private"),
                        ("license 公钥表", "license_public_json"),
                        ("应用 RSA 私钥", "app_private"),
                        ("应用 RSA 公钥", "app_public"),
                        ("支付宝 RSA 私钥", "alipay_private"),
                        ("支付宝 RSA 公钥", "alipay_public")):
        path = report["paths"][value]
        mark = "新增" if path in report["created"] else "复用"
        print(f"  {name:14s}: {path}  [{mark}]")
    print(f"  license key_id : {report['key_id']}")
    for label, key in (("license 公钥指纹", "license_ed25519"),
                       ("应用 RSA 公钥指纹", "app_rsa"),
                       ("支付宝 RSA 公钥指纹", "alipay_rsa")):
        print(f"  {label:14s}: {report['fingerprints'].get(key, '?')}")
    print("  说明：license 私钥参与签发；支付宝 RSA 私钥仅用于模拟异步通知（测试里扮演支付宝）。")
    print("  提示：--force 会轮换密钥；已签发的测试 license 需重新导入，商业服务需重启。")
    print("=" * 78)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成 L3 测试密钥（Ed25519 license + 两组 RSA；默认不覆盖已有文件）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--out-dir", default=None,
                        help=f"输出目录（默认 {DEFAULT_OUT_DIR}）")
    parser.add_argument("--force", action="store_true",
                        help="重新生成并覆盖已有测试密钥（会让旧的测试 license 全部失效）")
    parser.add_argument("--key-id", default=TEST_KEY_ID,
                        help="license key_id（产品公钥表键名；默认 test-2026）")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 打印摘要（供脚本解析；同样不含私钥）")
    parser.add_argument("--quiet", action="store_true", help="只输出错误")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    args = _build_parser().parse_args(argv)
    try:
        report = ensure_test_keys(
            args.out_dir, force=args.force, key_id=args.key_id, quiet=args.quiet)
    except KeyGenError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 不打印堆栈里的密钥材料（也不打印堆栈）
        print(f"[错误] 生成测试密钥失败（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
