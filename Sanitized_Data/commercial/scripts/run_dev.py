#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地启动数据隐站商业服务（开发/演示用；生产建议 systemd/计划任务 + uvicorn workers）。

示例（PowerShell，在 Sanitized_Data 目录）：
    python commercial\\scripts\\run_dev.py --provider mock
    python commercial\\scripts\\run_dev.py --provider mock --port 8787 --reload
    python commercial\\scripts\\run_dev.py --env-file commercial\\.env

说明：
- 默认读取 commercial/.env（存在才读），再叠加命令行参数；
- 「按 Ctrl+C」停止；--reload 时自动重载 server/ 目录改动；
- access log 默认关闭：订单轮询 URL 带 token，避免令牌进日志（README「安全」有说明）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

COMMERCIAL_DIR = Path(__file__).resolve().parents[1]
SERVER_DIR = COMMERCIAL_DIR / "server"
if str(COMMERCIAL_DIR) not in sys.path:
    sys.path.insert(0, str(COMMERCIAL_DIR))
# --reload 会 spawn 子进程；把 commercial/ 写进 PYTHONPATH，确保子进程能 import server.*
_existing_pythonpath = os.environ.get("PYTHONPATH", "")
if str(COMMERCIAL_DIR) not in _existing_pythonpath.split(os.pathsep):
    os.environ["PYTHONPATH"] = (str(COMMERCIAL_DIR) + os.pathsep + _existing_pythonpath
                                if _existing_pythonpath else str(COMMERCIAL_DIR))


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="启动数据隐站商业服务（FastAPI + SQLite）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default=None, help="监听地址（默认取 COMMERCIAL_HOST）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认取 COMMERCIAL_PORT=8787）")
    parser.add_argument("--provider", choices=("mock", "alipay"), default=None,
                        help="支付通道（默认取 PAYMENT_PROVIDER，缺省 mock）")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 commercial/var）")
    parser.add_argument("--db", default=None, help="SQLite 路径（默认 <data-dir>/commercial.db）")
    parser.add_argument("--base-url", default=None,
                        help="对外访问基地址（默认 http://<host>:<port>）")
    parser.add_argument("--env-file", default=None,
                        help="env 文件路径（默认 commercial/.env，存在才读）")
    parser.add_argument("--log-level", default=None,
                        choices=("debug", "info", "warning", "error"), help="日志级别")
    parser.add_argument("--reload", action="store_true", help="代码改动自动重载（开发用）")
    parser.add_argument("--access-log", action="store_true",
                        help="打印 uvicorn access log（含 URL，生产不要开）")
    return parser


def _load_config(args: argparse.Namespace):
    from server.config import Config

    overrides: dict[str, object] = {}
    if args.host:
        overrides["COMMERCIAL_HOST"] = args.host
    if args.port:
        overrides["COMMERCIAL_PORT"] = args.port
    if args.provider:
        overrides["PAYMENT_PROVIDER"] = args.provider
    if args.data_dir:
        overrides["COMMERCIAL_DATA_DIR"] = args.data_dir
    if args.db:
        overrides["COMMERCIAL_DB"] = args.db
    if args.base_url:
        overrides["COMMERCIAL_BASE_URL"] = args.base_url
    if args.log_level:
        overrides["COMMERCIAL_LOG_LEVEL"] = args.log_level.upper()
    env_path = Path(args.env_file) if args.env_file else None
    return Config.load(env_path=env_path, overrides=overrides)


def _print_banner(config) -> None:
    summary = config.safe_summary()
    key_path = Path(config.LICENSE_PRIVATE_KEY_PATH)
    print("=" * 68)
    print("数据隐站商业服务 · 开发模式")
    print(f"  访问地址 : {config.COMMERCIAL_BASE_URL}/buy.html")
    print(f"  监听地址 : http://{config.COMMERCIAL_HOST}:{config.COMMERCIAL_PORT}")
    print(f"  支付通道 : {config.PAYMENT_PROVIDER} "
          f"(mock_enabled={str(bool(config.PAYMENT_MOCK_ENABLED)).lower()})")
    print(f"  数据目录 : {config.data_dir}")
    print(f"  SQLite   : {config.db_path}")
    print(f"  授权 key : {summary.get('license_key_id')} "
          f"(私钥{'已配置' if key_path.is_file() else '未找到：' + str(key_path)})")
    for sku, cents in config.prices.items():
        print(f"  价格     : {sku:8s} {cents / 100:.2f} 元")
    if not key_path.is_file():
        print("  [警告] 缺少授权签名私钥，下单可以成功但「模拟支付」会在签发时报错；")
        print("         请配置 LICENSE_PRIVATE_KEY_PATH/PASSWORD（生产）或使用测试密钥（测试）。")
    if config.PAYMENT_PROVIDER == "alipay":
        print("  [提示] 真实支付宝模式：请确认 ALIPAY_* 配置与 T2 适配器 payments/alipay.py 已部署。")
    print("=" * 68)


def main(argv: list[str] | None = None) -> int:
    _reconfigure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = _load_config(args)
    config.ensure_dirs()
    # --reload 的子进程通过环境变量重建 Config，保证与父进程参数一致
    for name, value in config.to_env_dict().items():
        os.environ[name] = value
    _print_banner(config)

    import uvicorn

    log_level = (config.COMMERCIAL_LOG_LEVEL or "INFO").lower()
    if args.reload:
        print("[开发模式] uvicorn reload 已开启（修改 commercial/server 下代码自动生效）")
        return uvicorn.run(  # type: ignore[return-value]
            "server.app:create_app",
            factory=True,
            host=config.COMMERCIAL_HOST,
            port=int(config.COMMERCIAL_PORT),
            reload=True,
            reload_dirs=[str(SERVER_DIR)],
            log_level=log_level,
            access_log=bool(args.access_log),
        )
    from server.app import create_app

    print("[提示] 按 Ctrl+C 停止服务")
    uvicorn.run(
        create_app(config),
        host=config.COMMERCIAL_HOST,
        port=int(config.COMMERCIAL_PORT),
        log_level=log_level,
        access_log=bool(args.access_log),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
