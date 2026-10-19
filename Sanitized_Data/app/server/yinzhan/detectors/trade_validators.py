# -*- coding: utf-8 -*-
"""贸易单证号码校验器（trade-cn 规则包专用）。

设计约束（见 docs/red-lines.md E4 / docs/research/05-*）：
- **零依赖、纯函数、确定性**：只做格式与校验位判断，不联网、不读磁盘、不碰全局状态；
- **不修改 regex_det.py**：通过 ``register_validator`` 注册，``registry.load_plugins()``
  扫描 detectors 目录时自动 import 本模块（打包场景需同步 registry._PLUGIN_FALLBACK）；
- **校验不过 = 候选值直接丢弃**（regex_det.py 的 RegexDetector.detect），因此这里
  宁松勿严：只拒"结构明显不合法"的值；"像不像贸易单证"交给 YAML 的 ``near`` 上下文。
- 校验器命名与 trade-cn.yaml 的 ``validate:`` 字段一一对应，改名需同步改 YAML。

已注册校验器
------------
``iso6346_container`` 集装箱号（ISO 6346，含第 11 位校验）
``awb_no``            空运单号（IATA 11 位，末位 unweighted modulus 7）
``customs_decl_no``   报关单号/预录入编号（18 位结构）
``bl_no``             海运提单号（承运人前缀 + 序列，结构校验）
``hs_code``           HS 编码 / 中国商品编号（6/8/10 位，章 01-97）
``trade_invoice_no``  贸易发票号（字母+数字混合；与财务域 8 位发票号码的
                      ``finance_validators.invoice_no`` 区分，避免同名互相覆盖）
``contract_no``       合同号（保守结构）
``origin_cert_no``    原产地证号（保守结构）
``lc_no``             信用证号（保守结构；P1 LC 规则预留）
``license_no``        许可证号（保守结构；排除 USCC/身份证形态）
``seal_no``           封条号（排除 ISO 6346 集装箱形态）

注意（跨域约定，docs/red-lines.md E4）：``swift_bic`` 已由财务域
``finance_validators.swift_bic`` 注册（带 ISO 3166 国别码校验），本模块**不重复注册**，
否则 detectors 目录的 import 顺序会让后导入者覆盖前者（trade_validators 按文件名排在
finance_validators 之后）。同理，财务域 8 位"发票号码"校验器名为 ``invoice_no``，
贸易发票号因此使用独立名 ``trade_invoice_no``。

测试：``python -X utf8 app/tests/test_trade.py``（含正反例与算法交叉验证）。
"""
from __future__ import annotations

import re
from datetime import date

from .regex_det import register_validator

# ────────────────────────────── 通用工具 ──────────────────────────────

_MAX_YEAR = date.today().year + 1


def _norm_alnum(value: str) -> str:
    """去掉空格/横线/点等分隔符，转大写；只留字母数字。"""
    return re.sub(r"[^0-9A-Za-z]", "", value or "").upper()


def _build_iso6346_letter_values() -> dict[str, int]:
    """ISO 6346 字母值：A=10 起递增，跳过 11 的倍数（11/22/33）。"""
    table: dict[str, int] = {}
    value = 10
    for code in range(ord("A"), ord("Z") + 1):
        while value % 11 == 0:
            value += 1
        table[chr(code)] = value
        value += 1
    return table


_ISO6346_LETTER_VALUES = _build_iso6346_letter_values()

# 日期形态（拒绝把纯日期当单证号）
_DATE_LIKE_RE = re.compile(
    r"(?:19|20)\d{2}[-/.]?(?:0[1-9]|1[0-2])[-/.]?(?:0[1-9]|[12]\d|3[01])")
# USCC 形态（18 位、GB 32100 字符集）
_USCC_SHAPE_RE = re.compile(r"[0-9A-HJ-NPQRTUWXY]{18}")
# 身份证/律师执业证形态（17 位数字 + 校验位）
_ID_SHAPE_RE = re.compile(r"\d{17}[\dX]")
# 报关单号结构
_DECL_NO_RE = re.compile(r"\d{4}(?:19|20)\d{2}[01IE]\d{9}")
# 单证参考号字符集（字母/数字/分隔符）
_REF_CHARS_RE = re.compile(r"[A-Z0-9][A-Z0-9\-_/.]*")


def _ref_no(value: str, min_len: int, max_len: int) -> bool:
    """单证参考号的保守结构校验：长度 + 字母数字混合 + 排除已知号码形态。"""
    text = re.sub(r"\s+", "", value or "").upper()
    if not (min_len <= len(text) <= max_len):
        return False
    if not _REF_CHARS_RE.fullmatch(text):
        return False
    has_alpha = any(ch.isalpha() for ch in text)
    has_digit = any(ch.isdigit() for ch in text)
    if not (has_alpha and has_digit):
        return False
    if _DATE_LIKE_RE.fullmatch(text):            # 纯日期不是单证号
        return False
    if _USCC_SHAPE_RE.fullmatch(text):           # USCC 交给 cn.uscc
        return False
    if _ID_SHAPE_RE.fullmatch(text):             # 身份证/执业证交给对应规则
        return False
    if re.fullmatch(r"\d{11}", text):            # AWB/手机号形态交给专门规则
        return False
    return True


# ────────────────────────────── 校验器 ──────────────────────────────


@register_validator("iso6346_container")
def iso6346_container(value: str) -> bool:
    """ISO 6346 集装箱号：4 位字母（3 箱主 + 1 类别 U/J/Z）+ 6 位序列 + 1 位校验。"""
    text = _norm_alnum(value)
    if len(text) != 11:
        return False
    if not re.fullmatch(r"[A-Z]{3}[UJZ]\d{7}", text):
        return False
    total = 0
    for idx, ch in enumerate(text[:10]):
        number = _ISO6346_LETTER_VALUES[ch] if ch.isalpha() else int(ch)
        total += number * (2 ** idx)
    check = total % 11
    if check == 10:                       # 标准建议换序列，但存量箱存在 10→0
        check = 0
    return check == int(text[10])


@register_validator("awb_no")
def awb_no(value: str) -> bool:
    """IATA 空运单号：11 位数字，前 10 位 mod 7 == 第 11 位。"""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 11:
        return False
    return int(digits[:10]) % 7 == int(digits[10])


@register_validator("customs_decl_no")
def customs_decl_no(value: str) -> bool:
    """报关单号/预录入编号：18 位 = 4 关区 + 4 年份 + 1 进出口标志 + 9 顺序号。"""
    text = re.sub(r"[\s\-]", "", value or "").upper()
    if not _DECL_NO_RE.fullmatch(text):
        return False
    year = int(text[4:8])
    return 1980 <= year <= _MAX_YEAR


@register_validator("bl_no")
def bl_no(value: str) -> bool:
    """海运提单号：承运人字母前缀（3-4）+ 6-12 位序列；排除集装箱号形态。"""
    text = _norm_alnum(value)
    if not (9 <= len(text) <= 16):
        return False
    if not re.fullmatch(r"[A-Z]{3,4}\d{6,12}", text):
        return False
    # 集装箱号交给 iso6346_container 规则，避免同一值出现两个类型
    if len(text) == 11 and text[3] in "UJZ" and text[4:].isdigit():
        return False
    return True


@register_validator("hs_code")
def hs_code(value: str) -> bool:
    """HS 编码 / 中国商品编号：6、8、10 位数字，章（前 2 位）01-97。"""
    text = re.sub(r"[.\s]", "", value or "")
    if len(text) not in (6, 8, 10) or not text.isdigit():
        return False
    return 1 <= int(text[:2]) <= 97


@register_validator("trade_invoice_no")
def invoice_no(value: str) -> bool:
    """贸易发票号：保守结构（5-40 位，字母+数字混合，排除日期/USCC/身份证形态）。

    与财务域的 ``invoice_no``（8 位纯数字"发票号码"）区分：名字带 ``trade_`` 前缀，
    避免两个域各自注册同名校验器时按 import 顺序互相覆盖。
    """
    return _ref_no(value, 5, 40)


@register_validator("contract_no")
def contract_no(value: str) -> bool:
    """合同号：保守结构（5-40 位，字母+数字混合）。"""
    return _ref_no(value, 5, 40)


@register_validator("origin_cert_no")
def origin_cert_no(value: str) -> bool:
    """原产地证号：保守结构（6-40 位，字母+数字混合）。"""
    return _ref_no(value, 6, 40)


@register_validator("lc_no")
def lc_no(value: str) -> bool:
    """信用证号：保守结构（8-35 位，字母+数字混合），排除 USCC 形态。"""
    return _ref_no(value, 8, 35)


@register_validator("license_no")
def license_no(value: str) -> bool:
    """许可证号：保守结构（8-24 位，字母+数字+分隔符），排除 USCC/身份证形态。

    许可证格式由发证机关各自编制，没有公开统一校验位；这里只拦"明显不是许可证"的值，
    真正的准确率依赖 YAML 的 ``near``（许可证号/出口许可证/配额证…）+ 人工复核。
    """
    text = re.sub(r"\s+", "", value or "").upper()
    if not (8 <= len(text) <= 24):
        return False
    if not _REF_CHARS_RE.fullmatch(text):
        return False
    if not (any(ch.isalpha() for ch in text) and any(ch.isdigit() for ch in text)):
        return False
    if not any(ch in "-_/." for ch in text):     # 许可证号通常含分隔符
        return False
    if _USCC_SHAPE_RE.fullmatch(text):
        return False
    if _ID_SHAPE_RE.fullmatch(text):
        return False
    if _DATE_LIKE_RE.fullmatch(text):
        return False
    return True


@register_validator("seal_no")
def seal_no(value: str) -> bool:
    """封条/关锁号：2-4 位字母前缀 + 6-12 位数字；排除 ISO 6346 集装箱形态。

    封条号没有公开统一校验位（高保安封条按 ISO 17712 分类），这里只做形态判断，
    并避免把集装箱号误报成封条号（集装箱号交给 ``trade.container_no`` 规则）。
    """
    text = _norm_alnum(value)
    if not re.fullmatch(r"[A-Z]{2,4}\d{6,12}", text):
        return False
    if len(text) == 11 and text[3] in "UJZ" and text[:4].isalpha() and text[4:].isdigit():
        return False
    return True


__all__ = [
    "iso6346_container",
    "awb_no",
    "customs_decl_no",
    "bl_no",
    "hs_code",
    "invoice_no",
    "contract_no",
    "origin_cert_no",
    "lc_no",
    "license_no",
    "seal_no",
]
