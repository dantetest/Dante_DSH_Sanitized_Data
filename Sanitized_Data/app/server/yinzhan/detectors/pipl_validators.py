# -*- coding: utf-8 -*-
"""PIPL 补充域校验器（P0）。

由 ``registry.load_plugins()`` 自动导入并注册到
``detectors.regex_det._VALIDATORS``；规则包 ``rules/pipl-cn.yaml`` 通过
``validate: pipl_xxx`` 引用。命名统一 ``pipl_`` 前缀，避免与
``finance_validators`` / ``trade_validators`` / ``regex_det`` 的同名函数
在导入顺序中互相覆盖（本项目已实测过该问题）。

约定（与 regex_det 一致）：**宁可漏，不可错**。拿不准返回 False，
让规则里的 ``near`` 上下文或人工确认兜底。

写作用域：PIPL 域新增文件（docs/red-lines.md 扩展点 E4）。
"""
from __future__ import annotations

import ipaddress
import re

from .regex_det import register_validator

# VIN 字符集：不含 I/O/Q（ISO 3779）
_VIN_CHARS = frozenset("0123456789ABCDEFGHJKLMNPRSTUVWXYZ")
# ISO 3779 校验位：字母转数字（位置 9），权重
_VIN_TRANSLIT = {
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
_VIN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

# 有公开前缀的快递/物流单号（结构校验，无统一校验位）
_EXPRESS_PREFIXES = (
    "SF", "JD", "JTSD", "JT", "YT", "YD", "ZTO", "STO", "EMS", "DPD",
    "CN", "DBL", "HT", "ST", "TS", "EY",
)

# 银行卡/设备类常见分隔符
_SEP_RE = re.compile(r"[\s\-]")


def _clean(value: str) -> str:
    return (value or "").strip()


def _digits(value: str) -> str:
    return _SEP_RE.sub("", _clean(value))


# ══════════════════════ A. 文本直接标识 ══════════════════════

@register_validator("pipl_phone_digits")
def pipl_phone_digits(value: str) -> bool:
    """手机号（允许空格/短横线分组）：归一化后 11 位、1[3-9] 开头。"""
    digits = _digits(value)
    return bool(re.fullmatch(r"1[3-9]\d{9}", digits))


@register_validator("pipl_social_wxid")
def pipl_social_wxid(value: str) -> bool:
    """微信号 wxid：``wxid_`` + 6–20 位字母数字下划线/短横线。"""
    return bool(re.fullmatch(r"wxid_[A-Za-z0-9_\-]{6,20}", _clean(value)))


@register_validator("pipl_social_generic")
def pipl_social_generic(value: str) -> bool:
    """平台账号 ID：4–32 位字母数字/下划线/短横线/点，拒绝纯标点。"""
    text = _clean(value)
    if not (4 <= len(text) <= 32):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.\-]+", text)) and any(
        ch.isalnum() for ch in text)


@register_validator("pipl_nickname")
def pipl_nickname(value: str) -> bool:
    """昵称/群名：2–24 个字符，至少含 1 个可见字符，拒绝纯符号。"""
    text = _clean(value)
    return 2 <= len(text) <= 24 and any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
                                        for ch in text)


# ══════════════════════ B. 交易/票务 ══════════════════════

@register_validator("pipl_express_no")
def pipl_express_no(value: str) -> bool:
    """快递/物流单号：前缀白名单 + 数字，或 10–20 位纯数字（near 约束）。"""
    text = _clean(value).upper().replace(" ", "").replace("-", "")
    if not text:
        return False
    if len(set(text)) == 1:          # 0000000000 之类
        return False
    for prefix in _EXPRESS_PREFIXES:
        if text.startswith(prefix):
            tail = text[len(prefix):]
            return tail.isdigit() and 8 <= len(tail) <= 16
    return text.isdigit() and 10 <= len(text) <= 20


@register_validator("pipl_ticket_no")
def pipl_ticket_no(value: str) -> bool:
    """机票/火车票/行程单票号：10–13 位数字（可带 1 个分隔），排除全同。"""
    digits = re.sub(r"[\s\-]", "", _clean(value))
    if not digits.isdigit() or not (8 <= len(digits) <= 13):
        return False
    if len(set(digits)) == 1:
        return False
    return True


@register_validator("pipl_cvv")
def pipl_cvv(value: str) -> bool:
    """信用卡安全码 CVV/CVC：3–4 位数字（near 强烈依赖）。"""
    return bool(re.fullmatch(r"\d{3,4}", _digits(value)))


# ══════════════════════ C. 车辆/设备 ══════════════════════

def vin_check_digit(body17: str) -> str:
    """由 VIN 前 17 位计算 ISO 3779 校验位（位置 9）。"""
    text = (body17 or "").upper().strip()
    if len(text) < 17:
        return ""
    total = 0
    for ch, weight in zip(text[:17], _VIN_WEIGHTS):
        total += _VIN_TRANSLIT.get(ch, 0) * weight
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


@register_validator("pipl_vin")
def pipl_vin(value: str) -> bool:
    """VIN（ISO 3779）：17 位、不含 I/O/Q、第 9 位校验位正确。"""
    text = _clean(value).upper()
    if len(text) != 17 or any(ch not in _VIN_CHARS for ch in text):
        return False
    if len(set(text)) == 1:
        return False
    return text[8] == vin_check_digit(text)


@register_validator("pipl_imei")
def pipl_imei(value: str) -> bool:
    """IMEI：15 位数字 + Luhn 校验。"""
    digits = _digits(value)
    if not re.fullmatch(r"\d{15}", digits):
        return False
    if len(set(digits)) == 1:
        return False
    total = 0
    for idx, ch in enumerate(reversed(digits)):
        n = ord(ch) - 48
        if idx % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


@register_validator("pipl_mac")
def pipl_mac(value: str) -> bool:
    """MAC 地址：6 组 2 位十六进制，``:``/``-`` 分隔。"""
    return bool(re.fullmatch(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}",
                             _clean(value)))


@register_validator("pipl_device_sn")
def pipl_device_sn(value: str) -> bool:
    """设备序列号（近标签）：6–24 位字母数字/短横线，排除纯符号。"""
    text = _clean(value)
    if not (6 <= len(text) <= 24):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9\-_.]+", text)) and any(
        ch.isalnum() for ch in text)


# ══════════════════════ D. IP / 准标识符 ══════════════════════

@register_validator("pipl_ipv4")
def pipl_ipv4(value: str) -> bool:
    """IPv4：标准库解析（0–255、无前导零歧义）。"""
    try:
        ipaddress.IPv4Address(_clean(value))
        return True
    except ValueError:
        return False


@register_validator("pipl_ipv6")
def pipl_ipv6(value: str) -> bool:
    """IPv6：标准库解析（支持 ``::`` 压缩格式）。"""
    text = _clean(value)
    if ":" not in text:
        return False
    try:
        ipaddress.IPv6Address(text)
        return True
    except ValueError:
        return False


@register_validator("pipl_address_unit")
def pipl_address_unit(value: str) -> bool:
    """独立门牌/楼栋/单元/室：至少含 栋/幢/号楼/座 层级，长度 4–40。"""
    text = _clean(value)
    if not (4 <= len(text) <= 40):
        return False
    required = ("栋", "幢", "号楼", "座")
    if not any(token in text for token in required):
        return False
    # 至少 1 个数字（号/栋/单元/室通常带编号）
    return any(ch.isdigit() for ch in text)


@register_validator("pipl_doorplate")
def pipl_doorplate(value: str) -> bool:
    """标签式门牌/房间/工位：2–30 字符，含数字或路/号/层/室等地址词。"""
    text = _clean(value)
    if not (2 <= len(text) <= 30):
        return False
    return any(ch.isdigit() for ch in text) or any(
        token in text for token in ("路", "街", "号", "层", "室", "栋", "座",
                                    "单元", "工位"))


@register_validator("pipl_flight_no")
def pipl_flight_no(value: str) -> bool:
    """航班号：2 位航司代码（字母/数字）+ 3–4 位数字。"""
    return bool(re.fullmatch(r"[A-Z0-9]{2}\d{3,4}", _clean(value).upper()))


@register_validator("pipl_train_no")
def pipl_train_no(value: str) -> bool:
    """车次：G/C/D/Z/T/K 等字头 + 1–4 位数字。"""
    return bool(re.fullmatch(r"[GDCZTK]\d{1,4}", _clean(value).upper()))


@register_validator("pipl_seat_no")
def pipl_seat_no(value: str) -> bool:
    """座位号：1–3 位排号 + A–F 座（如 32A）。"""
    return bool(re.fullmatch(r"\d{1,3}[A-F]", _clean(value).upper()))


# ══════════════════════ E. 工牌 ══════════════════════

@register_validator("pipl_employee_no")
def pipl_employee_no(value: str) -> bool:
    """工号/员工编号：2–20 位字母数字/短横线，至少 1 位数字。"""
    text = _clean(value)
    if not (2 <= len(text) <= 20):
        return False
    if not re.fullmatch(r"[A-Za-z0-9\-_]+", text):
        return False
    return any(ch.isdigit() for ch in text)


__all__ = [
    "pipl_phone_digits", "pipl_social_wxid", "pipl_social_generic",
    "pipl_nickname", "pipl_express_no", "pipl_ticket_no", "pipl_cvv",
    "vin_check_digit", "pipl_vin", "pipl_imei", "pipl_mac", "pipl_device_sn",
    "pipl_ipv4", "pipl_ipv6", "pipl_address_unit", "pipl_doorplate",
    "pipl_flight_no", "pipl_train_no", "pipl_seat_no", "pipl_employee_no",
]
