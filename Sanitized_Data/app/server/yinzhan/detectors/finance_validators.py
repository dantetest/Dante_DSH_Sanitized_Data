"""财务域校验器（P0）。

本模块由 ``registry.load_plugins()`` 自动导入，导入即向
``detectors.regex_det._VALIDATORS`` 注册（沿用 ``@register_validator`` 约定），
规则包 YAML 里直接写 ``validate: <名字>`` 即可。

注册名（全部小写，与 YAML ``validate`` 对齐）。**一律带 ``fin_`` 前缀**：
校验函数名是全局注册表（``regex_det._VALIDATORS``），贸易域（trade_validators.py）
也注册了 ``invoice_no`` / ``swift_bic`` 等通用名；插件按文件名顺序导入，
后导入者会静默覆盖前者。为避免跨域互相踩，财务域统一用 ``fin_`` 命名空间，
trade 域用它们自己的命名空间，互不覆盖：

- ``fin_invoice_code``      发票代码（12 位电子票结构 / 10 位历史票种）
- ``fin_invoice_no``        发票号码（8 位）
- ``fin_swift_bic``         SWIFT/BIC（ISO 9362，8 或 11 位）
- ``fin_org_code``          组织机构代码（GB 11714，8 位本体 + 1 位校验）
- ``fin_business_reg_no``   工商注册号（15 位，三证合一前；无校验位，只做结构）
- ``fin_audit_report_no``   审计报告编号（无统一校验位，只做结构）
- ``fin_account_license``   开户许可证核准号 / 基本存款账户编号（J + 13 位）
- ``fin_tax_no``            纳税人识别号（18 位复用 USCC；15 位复用企业注册号）

约定（与 ``regex_det.py`` 顶部注释一致）：**宁可漏，不可错**。拿不准的编号
一律返回 False，交给规则里的 ``near`` 上下文或用户人工确认兜底。

写作用域：本文件为财务域新增文件（docs/red-lines.md 扩展点 E4）。
"""

from __future__ import annotations

import re

from .regex_det import register_validator, uscc_checksum

# ══════════════════════ 公共小工具 ══════════════════════

# 省级行政区划代码前两位（企业/税务登记相关编号的前缀）
_PROVINCES = frozenset({
    "11", "12", "13", "14", "15",
    "21", "22", "23",
    "31", "32", "33", "34", "35", "36", "37",
    "41", "42", "43", "44", "45", "46",
    "50", "51", "52", "53", "54",
    "61", "62", "63", "64", "65",
    "71", "81", "82", "91",
})

# ISO 3166-1 alpha-2：SWIFT/BIC 第 5–6 位必须是国家/地区代码。
# 用于压掉 "INDUSTRY" 这类普通大写单词的误报。
_ISO_ALPHA2 = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ
EC EE EG EH ER ES ET
FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
HK HM HN HR HT HU
ID IE IL IM IN IO IQ IR IS IT
JE JM JO JP
KE KG KH KI KM KN KP KR KW KY KZ
LA LB LC LI LK LR LS LT LU LV LY
MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ
OM
PA PE PF PG PH PK PL PM PN PR PS PT PW PY
QA
RE RO RS RU RW
SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
UA UG UM US UY UZ
VA VC VE VG VI VN VU
WF WS
YE YT
ZA ZM ZW
""".split())


def _clean(value: str) -> str:
    """去掉空白和常见分隔符，统一大写。"""
    return re.sub(r"[\s\-—－]", "", value or "").upper()


# ══════════════════════ 发票 ══════════════════════

@register_validator("fin_invoice_code")
def invoice_code(value: str) -> bool:
    """发票代码。

    国家税务总局公告 2015 年第 84 号：增值税电子普通发票代码为 12 位，
    第 1 位为 0、2–5 位省市、6–7 位年度、8–10 位批次、11–12 位票种。
    10 位为纸质票/历史票种，单看无法校验，只验"纯数字 + 长度"，
    上下文由规则里的 ``near`` / 标签负责。
    """
    text = _clean(value)
    if not text.isdigit():
        return False
    if len(text) == 12:
        if text[0] != "0":
            return False
        # 第 6–7 位是年度后两位；不做 19xx/20xx 硬约束，避免历史票被拒
        return 0 <= int(text[5:7]) <= 99
    if len(text) == 10:
        return True
    return False


@register_validator("fin_invoice_no")
def invoice_no(value: str) -> bool:
    """发票号码：8 位数字，按年度、批次编制，无校验位。"""
    text = _clean(value)
    if not text.isdigit() or len(text) != 8:
        return False
    if len(set(text)) == 1:            # 00000000 / 11111111
        return False
    return not text.startswith("0000")


# ══════════════════════ 银行/外汇 ══════════════════════

@register_validator("fin_swift_bic")
def swift_bic(value: str) -> bool:
    """SWIFT/BIC（ISO 9362）：8 或 11 位。

    结构：4 位银行代码（字母）+ 2 位 ISO 国家/地区代码（字母）
    + 2 位位置码（字母/数字）+ 可选 3 位分行码（字母/数字）。
    """
    text = _clean(value)
    if len(text) not in (8, 11):
        return False
    if not re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?", text):
        return False
    return text[4:6] in _ISO_ALPHA2


@register_validator("fin_account_license")
def account_license(value: str) -> bool:
    """开户许可证核准号 / 基本存款账户编号。

    中国人民银行公开答复：取消企业银行账户许可后，基本存款账户编号由
    14 位字母数字组成——``J`` + 4 位开户行地区代码 + 7 位顺序码 + 2 位换发次数，
    替代原核准号使用。本校验只验 ``J + 13 位字母数字`` 这一结构。
    """
    text = _clean(value)
    return bool(re.fullmatch(r"J[0-9A-Z]{13}", text))


# ══════════════════════ 工商 / 税务 ══════════════════════

@register_validator("fin_org_code")
def org_code(value: str) -> bool:
    """组织机构代码（GB 11714）：8 位本体码 + 1 位校验码，可带连字符。

    校验：加权因子 ``[3, 7, 9, 10, 5, 8, 4, 2]``，加权和 mod 11；
    余数 0 → 校验位 0，余数 1 → 校验位 X（10），其余 → 11 - 余数。
    字符值：0–9 → 0–9，A–Z → 10–35（大写）。
    """
    text = _clean(value)
    if len(text) != 9:
        return False
    body, check = text[:8], text[8]
    if not re.fullmatch(r"[0-9A-Z]{8}", body):
        return False
    weights = (3, 7, 9, 10, 5, 8, 4, 2)
    total = 0
    for ch, weight in zip(body, weights):
        if ch.isdigit():
            val = ord(ch) - 48
        elif "A" <= ch <= "Z":
            val = ord(ch) - ord("A") + 10
        else:
            return False
        total += val * weight
    remainder = total % 11
    if remainder == 0:
        expect = "0"
    elif remainder == 1:
        expect = "X"
    else:
        expect = str(11 - remainder)
    return check == expect


@register_validator("fin_business_reg_no")
def business_reg_no(value: str) -> bool:
    """工商注册号（三证合一前的 15 位注册号）。

    无校验位，只能做结构约束：纯数字、15 位、前 2 位是省级区划、
    不是全相同数字。误报靠规则里的 ``near``（注册号/营业执照号）压。
    """
    text = _clean(value)
    if not text.isdigit() or len(text) != 15:
        return False
    if len(set(text)) == 1:
        return False
    return text[:2] in _PROVINCES


@register_validator("fin_tax_no")
def tax_no_checksum(value: str) -> bool:
    """纳税人识别号。

    - 18 位：绝大多数已统一为社会信用代码，复用 USCC mod-31 校验；
    - 15 位：旧税务登记号/工商注册号形态，复用 ``business_reg_no``；
    - 17 / 20 位：历史或特殊纳税人识别号，只做"字母数字"结构校验。

    18 位纯数字/身份证号形态会被 USCC 校验拒绝（宁漏勿错）——
    这类值若确为纳税人识别号，也会被 ``cn.idcard`` 等规则覆盖，不会漏脱敏。
    """
    text = _clean(value)
    if len(text) == 18:
        return uscc_checksum(text)
    if len(text) == 15:
        return business_reg_no(text)
    if len(text) in (17, 20):
        return bool(re.fullmatch(r"[0-9A-Z]+", text)) and len(set(text)) > 1
    return False


# ══════════════════════ 审计 ══════════════════════

@register_validator("fin_audit_report_no")
def audit_report_no(value: str) -> bool:
    """审计报告编号。

    各会计师事务所格式不一（如 ``信会师报字[2025]第ZA12345号``），
    没有全国统一校验位。这里只做结构判断：长度合理、含"字/号/报告"
    且至少 2 位数字。误报由规则里的 ``near``（审计报告/报告编号）压。
    """
    text = (value or "").strip()
    if not (4 <= len(text) <= 60):
        return False
    digits = sum(1 for ch in text if ch.isdigit())
    if digits < 2:
        return False
    keywords = ("号", "字", "报告", "审字", "报字")
    return any(key in text for key in keywords)


__all__ = [
    "invoice_code", "invoice_no", "swift_bic", "org_code",
    "business_reg_no", "tax_no_checksum", "account_license",
    "audit_report_no",
]
