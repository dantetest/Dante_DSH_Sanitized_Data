"""部分遮蔽替换器。

适用于"要能核对、但不能全露"的场景：身份证留前 6 后 4、手机号留前 3 后 4、
地址只留到区、金额只留位数……规则包写法::

    replace:
      strategy: mask
      keep: [head6, tail4]        # 或 first6/last4
      keep_name: first            # 人名：first | last | both

支持的能力：

- ``headN`` / ``firstN``：保留前 N 个字符。
- ``tailN`` / ``lastN``：保留后 N 个字符。
- ``year``：日期只保留年份（``1990-05-12`` -> ``1990-**-**``）。
- ``keep_name``：中文名按 surname-first / surname-last / 首尾都留 三种策略。
- 类型缺省：``ID_CARD`` 前 6 后 4、``PHONE`` 前 3 后 4、``BANK`` 后 4、
  ``ADDRESS`` 保留到"区"、``AMOUNT`` 保留千分位与小数位、``EMAIL`` 只留首字母。

非数字字符（``-`` / 空格 / ``,`` / ``@`` / 货币符号）永远不遮，保证格式可读。
"""
from __future__ import annotations

import re

from ..registry import register_replacer
from ..udm import Decision, ReplaceContext, Rule

# 各类型的缺省保留策略（head, tail），未列出者遮到只剩尾部 2 个字符。
_DEFAULT_KEEP: dict[str, tuple[int, int]] = {
    "ID_CARD": (6, 4),
    "BANK": (0, 4),
    "PHONE": (3, 4),
    "USCC": (4, 4),
    "LICENSE": (4, 4),
    "PLATE": (2, 2),
    "CASE_NO": (6, 2),
    "MEDICAL_RECORD": (0, 4),
    "CUSTOMER_NO": (0, 4),
    "TXN_NO": (0, 4),
    "ACCOUNT": (0, 4),
    "IBAN": (4, 4),
    "CONTROL_POINT": (0, 3),
    "SECRET": (0, 0),
    "GEO_COORD": (0, 0),
    "SHEET_NO": (0, 0),
    "CRS": (0, 0),
}
_DEFAULT_KEEP_OTHER = (0, 2)

# 行政区划标记：地址遮蔽时保留到最后一个区/县/市
_ADMIN_TAIL_RE = re.compile(r"(?:自治区|特别行政区|省|市|地区|盟|区|县|旗|镇|乡|街道)")
# 日期：保留年份、遮住月日
_DATE_DASH_RE = re.compile(r"((?:19|20)\d{2})([-/.])(\d{1,2})\2(\d{1,2})")
_DATE_CN_RE = re.compile(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_LABEL_PREFIX_RE = re.compile(r"^([^\dA-Za-z]{1,16}?[：:]\s*)(.+)$", re.S)
_ASCII_NAME_RE = re.compile(r"[A-Za-z][A-Za-z .'\-]*")


# ────────────────────────────── 工具 ──────────────────────────────

def _parse_keep(keep) -> tuple[int, int, bool]:
    """解析 ``keep: [head6, tail4, year]``，返回 (head, tail, keep_year)。"""
    head = 0
    tail = 0
    keep_year = False
    if isinstance(keep, str):
        keep = [keep]
    for token in keep or []:
        text = str(token).strip().lower()
        if not text:
            continue
        if text == "year":
            keep_year = True
            continue
        match = re.fullmatch(r"(head|first|left)(\d+)", text)
        if match:
            head = max(head, int(match.group(2)))
            continue
        match = re.fullmatch(r"(tail|last|right)(\d+)", text)
        if match:
            tail = max(tail, int(match.group(2)))
    return head, tail, keep_year


def _mask_generic(value: str, head: int, tail: int, mask_char: str = "*") -> str:
    """保留头尾，中间字母/数字/汉字替换为 ``*``；分隔符原样保留。"""
    n = len(value)
    if n == 0:
        return value
    if n == 1:
        return mask_char if value.isalnum() else value
    head = max(0, min(head, n - 1))
    tail = max(0, min(tail, n - head - 1))       # 至少遮住 1 个字符
    out: list[str] = []
    for idx, ch in enumerate(value):
        if idx < head or idx >= n - tail:
            out.append(ch)
        elif ch.isalnum():
            out.append(mask_char)
        else:
            out.append(ch)
    return "".join(out)


def _apply_year(value: str, mask_char: str) -> str:
    """日期只保留年份。"""
    replaced = _DATE_DASH_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}**{m.group(2)}**", value)
    replaced = _DATE_CN_RE.sub(lambda m: f"{m.group(1)}年*月*日", replaced)
    return replaced


def _split_label(value: str) -> tuple[str, str]:
    """拆出"病历号："这类标签，标签不参与遮蔽。"""
    match = _LABEL_PREFIX_RE.match(value)
    if match:
        return match.group(1), match.group(2)
    return "", value


def _mask_name(value: str, keep_name: str, mask_char: str) -> str:
    """中文人名遮蔽：按姓氏/末字策略。"""
    n = len(value)
    if n <= 0:
        return value
    if n == 1:
        return mask_char
    mode = keep_name or "first"
    if mode == "last":
        return mask_char * (n - 1) + value[-1]
    if mode == "both":
        if n == 2:                                  # 只有姓和名各一字，留姓舍名
            return value[0] + mask_char
        return value[0] + mask_char * (n - 2) + value[-1]
    return value[0] + mask_char * (n - 1)           # first（默认）


def _mask_amount(value: str, mask_char: str) -> str:
    """金额：保留符号/千分位/小数点/单位，数字换成 ``*``。"""
    masked = "".join(mask_char if ch.isdigit() else ch for ch in value)
    if masked == value:                             # 中文大写金额
        masked = _mask_generic(value, 1, 0, mask_char)
    return masked


def _mask_address(value: str, mask_char: str) -> str:
    """地址：保留到「区」，后面换成 ``XX路XX号``。"""
    matches = list(_ADMIN_TAIL_RE.finditer(value))
    if matches:
        prefix = value[:matches[-1].end()]
        rest = value[matches[-1].end():]
        if rest.strip():
            return prefix + "XX路XX号"
        # 整个值就是区划名：再遮掉最后两个汉字
        if len(prefix) > 2:
            return prefix[:-2] + mask_char * 2
        return _mask_generic(value, 1, 0, mask_char)
    return _mask_generic(value, min(3, max(0, len(value) - 1)), 0, mask_char)


def _mask_email(value: str, mask_char: str) -> str:
    """邮箱：本地部分只留首字母。"""
    local, sep, domain = value.partition("@")
    if not sep:
        return _mask_generic(value, 1, 0, mask_char)
    head = local[:1] if local else ""
    return f"{head}{mask_char * 3}@{domain}"


# ────────────────────────────── 对外入口 ──────────────────────────────

def build(rule: Rule, value: str, ctx: ReplaceContext) -> Decision:
    """按规则声明的 ``keep`` / ``keep_name`` 生成部分遮蔽值。"""
    opts = dict(rule.replace or {}) if rule is not None else {}
    etype = (ctx.etype or (rule.entity if rule is not None else "") or "OTHER").upper()
    mask_char = str(opts.get("mask_char") or "*")[:1] or "*"
    keep_name = str(opts.get("keep_name") or "").lower()
    head, tail, keep_year = _parse_keep(opts.get("keep"))

    label, body = _split_label(value) if etype not in ("PERSON",) else ("", value)

    if head or tail or keep_year:
        masked = _mask_generic(body, head, tail, mask_char)
        if keep_year:
            masked = _apply_year(masked, mask_char)
    elif etype == "PERSON":
        if _ASCII_NAME_RE.fullmatch(body):
            masked = _mask_generic(body, 1, 0, mask_char)
        else:
            masked = _mask_name(body, keep_name, mask_char)
    elif etype == "ADDRESS":
        masked = _mask_address(body, mask_char)
    elif etype in ("AMOUNT", "CREDIT_LINE", "BALANCE", "MONEY", "PRICE"):
        masked = _mask_amount(body, mask_char)
    elif etype == "EMAIL":
        masked = _mask_email(body, mask_char)
    else:
        head, tail = _DEFAULT_KEEP.get(etype, _DEFAULT_KEEP_OTHER)
        masked = _mask_generic(body, head, tail, mask_char)

    if masked == value and body:                   # 保底：遮蔽值不能等于原值
        masked = _mask_generic(body, 1, 0, mask_char)
    return Decision(strategy="mask", replacement=label + masked, black=False)


register_replacer("mask", build)

__all__ = ["build"]
