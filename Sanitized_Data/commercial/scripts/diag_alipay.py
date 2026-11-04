#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""支付宝接入自检（只读，不创建交易/不付款）。

用途：在拿到 APPID、上传「应用公钥」之后，用本地应用私钥签名调用只读接口
`alipay.trade.query`，判断：
  - APPID 是否有效
  - 应用公钥是否已上传/签名是否通过
  - 应用/产品是否可用（看返回 code/sub_code）

注意：
- 只调用查询接口，不会产生订单、不会扣款；
- 不打印私钥/口令；响应里可能带支付宝调试图标，仅用于排错；
- 尚未配置「支付宝公钥」时，本脚本不做响应验签，只做接入诊断；
  配置了 --alipay-public-key 才会验签响应。

用法（在 Sanitized_Data 目录）：
    python commercial\\scripts\\diag_alipay.py --app-id 2021xxxxxxxxxxxx ^
        --private-key commercial\\var\\keys\\alipay_app_private.pem
    # 可选：--gateway https://openapi-sandbox.dl.alipaydev.com/gateway.do
    # 可选：--alipay-public-key commercial\\var\\keys\\alipay_public.pem（验响应签名）
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

COMMERCIAL_DIR = Path(__file__).resolve().parents[1]
if str(COMMERCIAL_DIR) not in sys.path:
    sys.path.insert(0, str(COMMERCIAL_DIR))

from server.payments.alipay import (  # noqa: E402
    GATEWAY_PROD,
    build_request_params,
    build_page_url,
    load_private_key,
    load_public_key,
    sign_params,
    verify_content,
    extract_json_object_text,
)


def _reconfigure() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _http_get(url: str, timeout: float = 15.0) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; SanitizedDataDiag/1.0)",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def main(argv: list[str] | None = None) -> int:
    _reconfigure()
    ap = argparse.ArgumentParser(description="支付宝 APPID/密钥/产品 只读自检")
    ap.add_argument("--app-id", required=True)
    ap.add_argument("--private-key", required=True, help="应用私钥 PEM")
    ap.add_argument("--alipay-public-key", default="", help="可选：支付宝公钥；提供则验响应签名")
    ap.add_argument("--gateway", default=GATEWAY_PROD)
    ap.add_argument("--out-trade-no", default="",
                    help="查询用的不存在订单号（默认自动生成，不会创建交易）")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args(argv)

    app_id = args.app_id.strip()
    if not app_id:
        raise SystemExit("APPID 为空")
    private_key = load_private_key(args.private_key)  # 不打印
    out_trade_no = args.out_trade_no.strip() or (
        "DIAG" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))

    params = build_request_params(
        method="alipay.trade.query",
        biz={"out_trade_no": out_trade_no},
        config={"ALIPAY_APP_ID": app_id},
    )
    params = sign_params(params, private_key)
    url = build_page_url(args.gateway, params)

    print(f"[diag] gateway={args.gateway}")
    print(f"[diag] app_id={app_id} out_trade_no={out_trade_no}")
    print("[diag] 正在调用只读接口 alipay.trade.query（不会创建交易/不会扣款）…")
    status, body, ctype = _http_get(url, timeout=args.timeout)
    text = body.decode("utf-8", "replace")
    print(f"[diag] HTTP {status} content-type={ctype} bytes={len(body)}")

    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        pass

    if parsed:
        resp = parsed.get("alipay_trade_query_response") or parsed.get("error_response") or {}
        code = str(resp.get("code", ""))
        sub_code = str(resp.get("sub_code", ""))
        msg = str(resp.get("msg", ""))
        sub_msg = str(resp.get("sub_msg", ""))
        print(f"[diag] code={code} sub_code={sub_code} msg={msg} sub_msg={sub_msg}")
        # 常见判定
        if code == "10000":
            print("[diag] 签名/APPID 基本通过（查询本身返回成功；对不存在的订单不应出现，仅作参考）")
        elif sub_code == "ACQ.TRADE_NOT_EXIST":
            print("[diag] ✅ 签名通过、APPID 有效（查询到“订单不存在”是预期，因为订单号是假的）")
        elif sub_code in ("isv.invalid-app-id", "isv.app-not-exist", "isv.invalid-appid"):
            print("[diag] ❌ APPID 无效或不属于当前应用")
        elif sub_code in ("isv.missing-signature-config", "isv.missing-signature"):
            print("[diag] ❌ 应用还没配置公钥（或签名算法不匹配）：请在开放平台 开发设置 → 接口加签方式 → 公钥模式，上传应用公钥并保存为 RSA2")
        elif sub_code in ("isv.invalid-signature", "isv.invalid-signature-type",
                          "isv.invalid-encrypt", "isv.signature-check-fail"):
            print("[diag] ❌ 请求验签失败：应用公钥未上传/上传的不是这把私钥对应的公钥/密钥不匹配")
        elif sub_code in ("isv.insufficient-isv-permissions", "isv.insufficient-permissions"):
            print("[diag] ❌ 应用没有该接口权限：最常见是「电脑网站支付」产品还没绑定到这个应用。"
                  "请到开放平台应用详情 → 产品绑定/产品中心，添加并确认「电脑网站支付」已生效")
        elif sub_code in ("isv.app-not-online",):
            print("[diag] ⚠ 应用未上线：先在开放平台提交应用上线审核，通过后再联调")
        elif sub_code in ("ACQ.ACCESS_FORBIDDEN", "isv.insufficient-permissions"):
            print("[diag] ⚠ 接口无权限：确认「电脑网站支付」已绑定到该应用")
        # 配置了支付宝公钥则验签响应
        if args.alipay_public_key:
            try:
                pub = load_public_key(args.alipay_public_key)
                raw = extract_json_object_text(text, "alipay_trade_query_response")
                if raw is None:
                    raw = extract_json_object_text(text, "error_response")
                signed = raw is not None and bool(parsed.get("sign"))
                ok = bool(signed and verify_content(raw, str(parsed.get("sign")), pub))
                print(f"[diag] 响应验签={'PASS' if ok else 'FAIL'}")
                if not ok:
                    print("[diag] ❌ 响应验签失败：ALIPAY_PUBLIC_KEY 不是当前 APPID 的『支付宝公钥』。"
                          "请到开放平台 → 应用详情 → 开发设置 → 接口加签方式 → 公钥模式，"
                          "复制页面展示的『支付宝公钥』（不是应用公钥！）到 ALIPAY_PUBLIC_KEY_PATH")
            except Exception as exc:
                print(f"[diag] 响应验签异常：{type(exc).__name__}: {exc}")
    else:
        # 通常是 HTML 错误页
        print("[diag] 非 JSON 响应（前 400 字）：")
        print(text[:400])
        if "验签" in text or "sign" in text.lower():
            print("[diag] ❌ 很可能应用公钥未上传或与私钥不匹配")
        elif "应用" in text or "APPID" in text.upper():
            print("[diag] ⚠ 检查 APPID、应用类型、产品绑定、应用上线状态")

    print("[diag] 提示：本脚本只读；真实支付仍需公网 HTTPS notify_url 与 0.01 元测试。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
