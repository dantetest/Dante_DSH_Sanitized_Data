"""正则检测器。

规则包里的 ``detect.type == "regex"`` 由本检测器执行::

    detect:
      - {type: regex, pattern: '(?<!\\d)1[3-9]\\d{9}(?!\\d)'}
      - {type: regex, pattern: '...', validate: cn_id_checksum, flags: I}

扩展约定（不违反 CONTRACT，``detect`` 是自由 dict）：

- ``validate``：校验函数名，见下方 ``_VALIDATORS``；校验不过直接丢弃
  （宁可漏不可错）。
- ``flags``：``"I|M|S"`` 或 ``["I", "M"]``。
- ``near``：上下文提示词列表；命中窗口内必须出现其一，用于压误报
  （例如"执业证号"这种单看数字无法区分的编号）。默认向前看 24 字符、
  向后看 8 字符，可用 ``near_before`` / ``near_after`` 调整。

同一 ``value`` 在全文档多处命中会合并成一个 Entity（多个 spans），
这是 CONTRACT §4 的硬要求。
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Callable

from ..registry import Detector, iter_text_blocks, register_detector
from ..udm import DetectContext, Document, Entity, Rule, Span

# 校验函数注册表
_VALIDATORS: dict[str, Callable[[str], bool]] = {}


def register_validator(name: str):
    """把函数登记为 YAML 可引用的 ``validate`` 名。"""

    def deco(fn: Callable[[str], bool]) -> Callable[[str], bool]:
        _VALIDATORS[name] = fn
        return fn

    return deco


# 身份证 GB 11643 校验位
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"
# 省级行政区划代码前两位（用于再压一层误报）
_PROVINCE_CODES = frozenset({
    "11", "12", "13", "14", "15",
    "21", "22", "23",
    "31", "32", "33", "34", "35", "36", "37",
    "41", "42", "43", "44", "45", "46",
    "50", "51", "52", "53", "54",
    "61", "62", "63", "64", "65",
    "71", "81", "82", "91",
})


@register_validator("cn_id_checksum")
def cn_id_checksum(value: str) -> bool:
    """中国居民身份证（GB 11643-1999）18 位校验。

    校验：长度、前 17 位数字、校验位、出生日期合法、省级区划码合法。
    最后一位允许 ``X``（大小写均可）。
    """
    text = (value or "").strip().upper()
    if len(text) != 18:
        return False
    body, check = text[:17], text[17]
    if not body.isdigit() or check not in "0123456789X":
        return False
    if text[:2] not in _PROVINCE_CODES:
        return False
    total = sum(int(ch) * w for ch, w in zip(body, _ID_WEIGHTS))
    if _ID_CHECK[total % 11] != check:
        return False
    try:
        birthday = _dt.date(int(body[6:10]), int(body[10:12]), int(body[12:14]))
    except ValueError:
        return False
    today = _dt.date.today()
    return _dt.date(1900, 1, 1) <= birthday <= today


def id_check_digit(body17: str) -> str:
    """由身份证前 17 位算出校验位（替换器生成假号时复用）。"""
    total = sum(int(ch) * w for ch, w in zip(body17[:17], _ID_WEIGHTS))
    return _ID_CHECK[total % 11]


@register_validator("luhn")
def luhn(value: str) -> bool:
    """银行卡号 Luhn 校验（允许空格/短横线分组，16–19 位）。"""
    digits = re.sub(r"[\s-]", "", value or "")
    if not digits.isdigit() or not (12 <= len(digits) <= 19):
        return False
    if digits[0] == "0":
        return False
    if len(set(digits)) == 1:                      # 000000 / 111111 之类
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


_IBAN_CHARS = {chr(ord("A") + i): str(10 + i) for i in range(26)}


@register_validator("iban")
def iban(value: str) -> bool:
    """IBAN（ISO 13616）校验：长度 + mod 97。"""
    text = re.sub(r"\s", "", value or "").upper()
    if not (15 <= len(text) <= 34):
        return False
    if not (text[:2].isalpha() and text[2:4].isdigit()):
        return False
    if not re.fullmatch(r"[A-Z0-9]+", text):
        return False
    rearranged = text[4:] + text[:4]
    remainder = 0
    for ch in rearranged:
        chunk = _IBAN_CHARS.get(ch, ch)
        for digit in chunk:
            remainder = (remainder * 10 + (ord(digit) - 48)) % 97
    return remainder == 1


# 统一社会信用代码 GB 32100-2015
_USCC_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
_USCC_INDEX = {ch: i for i, ch in enumerate(_USCC_CHARSET)}


@register_validator("uscc_checksum")
def uscc_checksum(value: str) -> bool:
    """统一社会信用代码（GB 32100-2015）18 位校验。"""
    text = (value or "").strip().upper()
    if len(text) != 18 or any(ch not in _USCC_INDEX for ch in text):
        return False
    total = sum(_USCC_INDEX[ch] * w for ch, w in zip(text[:17], _USCC_WEIGHTS))
    check = (31 - total % 31) % 31
    return _USCC_CHARSET[check] == text[17]


def uscc_check_digit(body17: str) -> str:
    """由前 17 位算统一社会信用代码校验位（替换器复用）。"""
    total = sum(_USCC_INDEX.get(ch, 0) * w for ch, w in zip(body17[:17], _USCC_WEIGHTS))
    return _USCC_CHARSET[(31 - total % 31) % 31]


@register_validator("none")
def _always_ok(value: str) -> bool:
    """不校验（``validate: none``）。"""
    return bool(value)


_FLAG_MAP = {
    "i": re.IGNORECASE, "ignorecase": re.IGNORECASE,
    "m": re.MULTILINE, "multiline": re.MULTILINE,
    "s": re.DOTALL, "dotall": re.DOTALL,
    "x": re.VERBOSE, "verbose": re.VERBOSE,
    "a": re.ASCII, "ascii": re.ASCII,
}


def _parse_flags(spec_flags: Any) -> int:
    """把 yaml 里的 flags 写法转成 re 的 flag 位。"""
    if not spec_flags:
        return 0
    if isinstance(spec_flags, int):
        return spec_flags
    flags = 0
    if isinstance(spec_flags, str):
        parts = re.split(r"[|,;\s]+", spec_flags)
    else:
        parts = [str(x) for x in spec_flags]
    for part in parts:
        flags |= _FLAG_MAP.get(part.strip().lower(), 0)
    return flags


class RegexDetector(Detector):
    """在 ``block.text`` 上跑正则的检测器。"""

    id = "regex"

    def __init__(self, pattern: str, validate: str = "none",
                 near: list[str] | tuple[str, ...] | None = None,
                 near_before: int = 24, near_after: int = 8,
                 flags: int = 0, confidence: float = 0.99,
                 strip_prefix: list[str] | tuple[str, ...] | None = None,
                 strip_suffix: list[str] | tuple[str, ...] | None = None,
                 unless_validate: str | list[str] | tuple[str, ...] | None = None,
                 exclude_near: list[str] | tuple[str, ...] | None = None,
                 exclude_before: int = 24, exclude_after: int = 8) -> None:
        if not pattern:
            raise ValueError("RegexDetector 需要非空 pattern")
        self.pattern = str(pattern)
        self.regex = re.compile(self.pattern, flags)
        self.validate_name = str(validate or "none").strip().lower()
        validator = _VALIDATORS.get(self.validate_name)
        if validator is None:
            # 未知校验名：宁可不校验也不要让整条规则失效，但记为 none。
            self.validate_name = "none"
            validator = _always_ok
        self.validator = validator
        # 反向校验：命中这些校验器之一就丢弃（典型：银行卡规则要排除「其实像身份证」的值）
        if isinstance(unless_validate, str):
            unless_validate = [unless_validate]
        self.unless_validators = tuple(
            _VALIDATORS[str(name).strip().lower()]
            for name in (unless_validate or ())
            if str(name).strip().lower() in _VALIDATORS
        )
        self.near = tuple(str(x) for x in near) if near else ()
        self.near_before = max(0, int(near_before))
        self.near_after = max(0, int(near_after))
        # 排除上下文：窗口出现这些词就不算（典型：固定电话规则不要吃掉「发票代码：0310...」）
        self.exclude_near = tuple(str(x) for x in exclude_near) if exclude_near else ()
        self.exclude_before = max(0, int(exclude_before))
        self.exclude_after = max(0, int(exclude_after))
        self.confidence = float(confidence)
        # 机构类正则容易把前面的"委托/被告/公司的"一起吃进来，允许裁剪。
        self.strip_prefix = tuple(
            sorted((str(x) for x in strip_prefix or () if str(x)), key=len, reverse=True)
        )
        self.strip_suffix = tuple(
            sorted((str(x) for x in strip_suffix or () if str(x)), key=len, reverse=True)
        )

    # ── 构造 ──
    @classmethod
    def from_spec(cls, spec: dict) -> "RegexDetector":
        """从 yaml 的 detect 项创建检测器。"""
        if not isinstance(spec, dict):
            raise ValueError(f"RegexDetector.from_spec 需要 dict，得到 {type(spec).__name__}")
        pattern = spec.get("pattern") or spec.get("regex")
        if not pattern:
            raise ValueError("regex detect 缺少 pattern")
        near = spec.get("near")
        if isinstance(near, str):
            near = [near]
        strip_prefix = spec.get("strip_prefix")
        if isinstance(strip_prefix, str):
            strip_prefix = [strip_prefix]
        strip_suffix = spec.get("strip_suffix")
        if isinstance(strip_suffix, str):
            strip_suffix = [strip_suffix]
        exclude_near = spec.get("exclude_near")
        if isinstance(exclude_near, str):
            exclude_near = [exclude_near]
        return cls(
            pattern=str(pattern),
            validate=str(spec.get("validate") or "none"),
            near=near,
            near_before=int(spec.get("near_before", 24)),
            near_after=int(spec.get("near_after", 8)),
            flags=_parse_flags(spec.get("flags")),
            confidence=float(spec.get("confidence", 0.99)),
            strip_prefix=strip_prefix,
            strip_suffix=strip_suffix,
            unless_validate=spec.get("unless_validate"),
            exclude_near=exclude_near,
            exclude_before=int(spec.get("exclude_before", 24)),
            exclude_after=int(spec.get("exclude_after", 8)),
        )

    # ── 上下文窗口 ──
    def _near_ok(self, text: str, start: int, end: int) -> bool:
        if not self.near:
            return True
        window = text[max(0, start - self.near_before):min(len(text), end + self.near_after)]
        return any(hint in window for hint in self.near)

    def _exclude_ok(self, text: str, start: int, end: int) -> bool:
        if not self.exclude_near:
            return True
        window = text[max(0, start - self.exclude_before):
                      min(len(text), end + self.exclude_after)]
        return not any(hint in window for hint in self.exclude_near)

    # ── 检测 ──
    def detect(self, doc: Document, rule: Rule,
               ctx: DetectContext) -> list[Entity]:
        """扫描全部文本块，按 value 合并 spans。"""
        merged: dict[str, Entity] = {}
        for _page, block in iter_text_blocks(doc):
            text = block.text
            if not text:
                continue
            for match in self.regex.finditer(text):
                start, end = match.start(), match.end()
                value = match.group(0)
                # 机构类规则的前导干扰词裁剪（"患者入住上海市…" -> "上海市…"）
                changed = True
                while changed and value:
                    changed = False
                    for noise in self.strip_prefix:
                        if (value.startswith(noise) and len(value) > len(noise)):
                            value = value[len(noise):]
                            start += len(noise)
                            changed = True
                            break
                    for noise in self.strip_suffix:
                        if (value.endswith(noise) and len(value) > len(noise)):
                            value = value[:-len(noise)]
                            end -= len(noise)
                            changed = True
                            break
                if not value or not self.validator(value):
                    continue
                if any(fn(value) for fn in self.unless_validators):
                    continue
                if not self._near_ok(text, start, end):
                    continue
                if not self._exclude_ok(text, start, end):
                    continue
                entity = merged.get(value)
                if entity is None:
                    entity = Entity(
                        id=Entity.new_id(),
                        etype=rule.entity,
                        label=rule.label,
                        value=value,
                        spans=[],
                        level=rule.level,
                        confidence=self.confidence,
                        origin="regex",
                        status="accepted",
                    )
                    merged[value] = entity
                entity.spans.append(Span(block_id=block.id, start=start, end=end))
        return list(merged.values())


register_detector(RegexDetector)

__all__ = ["RegexDetector", "cn_id_checksum", "id_check_digit", "luhn",
           "iban", "uscc_checksum", "uscc_check_digit", "register_validator"]
