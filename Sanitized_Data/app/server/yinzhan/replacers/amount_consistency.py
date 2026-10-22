"""金额一致性引擎（P0，财务域新增文件）。

背景
----
现有金额替换走 ``replacers/surrogate.py`` 的 ``_fake_amount``：**每一处**金额
独立在 ±25% 内随机浮动。这对"给 AI 读"的财务文件是致命的：

* 合计 ≠ 分项之和（勾稽断裂）；
* 同一笔金额的不同写法（``人民币12,000,000.00元`` / ``¥12,000,000.00`` /
  ``12,000,000.00元``）被换成不同数值（跨字段不一致）；
* 逐处缩放后比率/趋势漂移。

本模块按已批准的方案 A（D2）实现：

1. **同一勾稽组、同一缩放因子 k**（默认 ``[0.90, 1.10]``，确定性生成）；
2. **合计 = 分项重算**：识别到"合计/总计/价税合计"（规则 ``amount_role:
   total``，或数值上满足"最大数 = 其余数之和"）时，合计取分项缩放后之和，
   而不是独立缩放；
3. **同值同映射**：归一化到"元"的同一数值（含中文大写金额）映射到同一数值；
4. **保形**：保留标签前缀、货币符号、单位（元/万元/亿元）、千分位、小数位、
   正负号；中文大写金额由缩放后数值重新生成；
5. **不缩放**：比率/税率/日期/数量（调用方只把 AMOUNT 类实体交给本模块；
   本模块另对含 ``%`` 的值做防御性跳过）。

接口（Lead 在 ``pipeline.decide()`` 之后、导出/预览之前调用）::

    from .replacers import amount_consistency

    plan = amount_consistency.build_amount_plan(job.entities, job.rules, job.session)
    amount_consistency.apply_plan(job.entities, plan, job.session)

* ``rules``：``pipeline.detect()`` 返回的 ``{entity_id: Rule}``（也接受 Rule 列表）；
* ``session``：``pipeline.Session`` / dict / None 均可；写入 ``session.map``
  后，可逆导出的映射库才能还原金额；
* 返回：``{entity_id: replacement_text}``。

局限（P0 明确不做，见 docs/impl/finance.md）：
* 只对**已识别成 AMOUNT 类实体**的文本生效；XLSX 纯数值单元格需要新的表格
  检测器（Lead 管线扩展），本模块不碰解析器；
* "合计=分项和"的自动识别依赖规则角色或"最大数=其余数之和"的数值关系，
  复杂合并报表（多级小计/嵌套合计）P1 再增强；
* 舍入误差：分项以各自单位精度显示，合计按分项显示值求和，保证"显示值相加
  等于合计显示值"；跨单位（元 vs 万元）时以元的 2 位小数为准。
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

# ── 常量 ──────────────────────────────────────────────────────────────

#: 参与金额一致性的实体类型（规则包 entity 字段归一化后的大写）
AMOUNT_ETYPES: frozenset[str] = frozenset({
    "AMOUNT", "CREDIT_LINE", "BALANCE", "MONEY", "PRICE", "TOTAL",
})

#: 默认缩放因子范围（已批准方案 A / 决策 D2；可用 session 属性覆盖）
DEFAULT_FACTOR_RANGE: tuple[Decimal, Decimal] = (Decimal("0.90"), Decimal("1.10"))

_CENT = Decimal("0.01")
_ROUND = ROUND_HALF_UP

_UNIT_SCALE: dict[str, int] = {
    "万亿": 10 ** 12,
    "亿元": 10 ** 8,
    "万元": 10 ** 4,
    "亿": 10 ** 8,
    "万": 10 ** 4,
    "元": 1,
    "圆": 1,
    "块": 1,
}

_CURRENCY_HINTS: tuple[str, ...] = ("人民币", "RMB", "CNY", "¥", "￥")

# 金额文本：任意前缀（标签/货币）+ 可选负号 + 数字（千分位）+ 可选小数 + 可选单位
_AMOUNT_RE = re.compile(
    r"^(?P<lead>.*?)(?P<sign>[+-]?)(?P<int>\d{1,3}(?:,\d{3})+|\d+)"
    r"(?P<dec>\.\d{1,6})?"
    r"(?P<unit>\s*(?:万亿|亿元|万元|亿|万|元|圆|块))?$",
    re.S,
)

# ── 中文大写金额 ──────────────────────────────────────────────────────

_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_VALUE: dict[str, int] = {ch: i for i, ch in enumerate(_CN_DIGITS)}
_CN_VALUE["两"] = 2
_CN_VALUE["俩"] = 2
_CN_SMALL = {"拾": 10, "佰": 100, "仟": 1000}
_CN_UPPER_RE = re.compile(r"[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]")
_CN_BIG_UNITS = ("", "万", "亿", "万亿")


def _parse_cn_int(text: str) -> int | None:
    """解析中文大写整数（不含元/角/分）。无法解析返回 None。"""
    text = text.strip()
    if not text:
        return 0
    if "亿" in text:
        left, _, right = text.partition("亿")
        a, b = _parse_cn_int(left), _parse_cn_int(right)
        return None if a is None or b is None else a * 10 ** 8 + b
    if "万" in text:
        left, _, right = text.partition("万")
        a, b = _parse_cn_int(left), _parse_cn_int(right)
        return None if a is None or b is None else a * 10 ** 4 + b
    total = 0
    num = 0
    for ch in text:
        if ch in _CN_VALUE:
            num = _CN_VALUE[ch]
        elif ch in _CN_SMALL:
            total += (num if num else 1) * _CN_SMALL[ch]
            num = 0
        elif ch in ("零", "〇"):
            num = 0
        else:
            return None
    return total + num


def parse_cn_amount(value: str) -> Decimal | None:
    """把中文大写金额解析成"元"的 Decimal；失败返回 None。

    支持 ``壹仟叁佰伍拾陆万元整`` / ``伍仟元零伍分`` 等常见写法。
    """
    text = (value or "").strip()
    if not text:
        return None
    # 去掉标签前缀（如 "价税合计（大写）："）
    match = _CN_UPPER_RE.search(text)
    if match is None:
        return None
    body = text[match.start():]
    body = body.replace("人民币", "").replace("整", "").replace("正", "").strip()
    for unit in ("圆", "元", "块"):
        if unit in body:
            yuan_text, frac_text = body.split(unit, 1)
            break
    else:
        return None
    yuan = _parse_cn_int(yuan_text)
    if yuan is None:
        return None
    jiao = fen = 0
    frac_text = frac_text.strip()
    if frac_text:
        m = re.fullmatch(r"零?(?:([零壹贰叁肆伍陆柒捌玖])角)?(?:([零壹贰叁肆伍陆柒捌玖])分)?", frac_text)
        if m is None:
            return None
        jiao = _CN_VALUE.get(m.group(1), 0) if m.group(1) else 0
        fen = _CN_VALUE.get(m.group(2), 0) if m.group(2) else 0
    return Decimal(yuan) + Decimal(jiao) / 10 + Decimal(fen) / 100


def _format_cn_four(number: int) -> str:
    """0–9999 转中文大写（不含前导零）。"""
    if number <= 0:
        return ""
    units = ("", "拾", "佰", "仟")
    out: list[str] = []
    for pos in range(3, -1, -1):
        digit = (number // (10 ** pos)) % 10
        if digit == 0:
            if out and out[-1] != "零":
                out.append("零")
        else:
            out.append(_CN_DIGITS[digit] + units[pos])
    while out and out[-1] == "零":
        out.pop()
    return "".join(out)


def _format_cn_int(number: int) -> str:
    """非负整数转中文大写（含 万/亿 分节）。"""
    if number == 0:
        return "零"
    sections: list[int] = []
    while number > 0:
        sections.append(number % 10000)
        number //= 10000
    out: list[str] = []
    for index in range(len(sections) - 1, -1, -1):
        section = sections[index]
        if section == 0:
            if out and not out[-1] == "零":
                out.append("零")
            continue
        part = _format_cn_four(section)
        if out and section < 1000 and not out[-1] == "零":
            out.append("零")
        out.append(part + _CN_BIG_UNITS[index])
    result = "".join(out).rstrip("零")
    return result or "零"


def format_cn_amount(value: Decimal) -> str:
    """把"元"金额格式化成中文大写（角/分按实际值输出）。"""
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError):
        return ""
    sign = "-" if amount < 0 else ""
    amount = abs(amount).quantize(_CENT, rounding=_ROUND)
    cents = int((amount * 100).to_integral_value(rounding=_ROUND))
    yuan, remainder = divmod(cents, 100)
    jiao, fen = divmod(remainder, 10)
    text = sign + _format_cn_int(yuan) + "元"
    if jiao == 0 and fen == 0:
        return text + "整"
    if jiao:
        text += _CN_DIGITS[jiao] + "角"
    elif fen:
        text += "零"
    if fen:
        text += _CN_DIGITS[fen] + "分"
    return text


# ── 单个金额 token ────────────────────────────────────────────────────


@dataclass
class _Token:
    """一个待处理的金额实体解析结果。"""

    entity_id: str
    etype: str
    raw: str
    role: str                    # line | tax | total | subtotal | credit
    rule_id: str = ""
    lead: str = ""               # 数字前的标签/货币符号（原样保留）
    int_text: str = ""
    dec_text: str = ""
    has_comma: bool = False
    decimals: int = 0
    unit_text: str = ""          # 原单位文本（含前导空格），原样保留
    unit_scale: int = 1
    sign: int = 1
    canonical: Decimal = Decimal(0)   # 归一化到"元"
    is_cn_upper: bool = False


def _role_of(entity, rule) -> str:
    """从一个实体/规则推断金额角色。"""
    opts = dict(getattr(rule, "replace", None) or {}) if rule is not None else {}
    role = str(opts.get("amount_role") or "").strip().lower()
    if role:
        return role
    text = f"{getattr(entity, 'label', '') or ''}|{getattr(entity, 'value', '') or ''}"
    if "价税合计" in text or "合计" in text or "总计" in text:
        return "total"
    if "小计" in text:
        return "subtotal"
    if "税额" in text:
        return "tax"
    return "line"


def _numeric_amount_meta(entity) -> dict | None:
    """纯数值单元格（numeric_det 识别）的元数据；非数值金额返回 None。"""
    if str(getattr(entity, "origin", "") or "") != "numeric_cell":
        return None
    try:
        from ..detectors import numeric_det

        meta = numeric_det.get_numeric_meta(getattr(entity, "id", ""))
    except Exception:
        return None
    if not meta or str(meta.get("kind") or "") != "amount":
        return None
    return meta


def _parse_token(entity, rule) -> _Token | None:
    """把一个实体解析成 _Token；无法识别为金额返回 None。"""
    value = str(getattr(entity, "value", "") or "").strip()
    if not value:
        return None
    if "%" in value or "％" in value:      # 比率/税率：绝不缩放
        return None
    etype = str(getattr(entity, "etype", "") or "").upper()
    if etype not in AMOUNT_ETYPES:
        return None

    # 0) 纯数值单元格金额（xlsx numeric_cell）：无"元/万元"文本，按元处理，
    #    小数位来自 number_format（numeric_det 元数据）。
    numeric_meta = _numeric_amount_meta(entity)
    if numeric_meta is not None:
        cleaned = value.replace(",", "").replace("，", "").strip()
        try:
            canonical = Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None
        decimals = int(numeric_meta.get("decimals") or 0)
        return _Token(
            entity_id=getattr(entity, "id", ""),
            etype=etype, raw=value,
            role=_role_of(entity, rule),
            rule_id=str(getattr(rule, "id", "") or ""),
            lead="", int_text=value, dec_text="",
            has_comma="," in value, decimals=decimals,
            unit_text="", unit_scale=1,
            sign=-1 if canonical < 0 else 1,
            canonical=canonical,
        )

    # 1) 中文大写金额
    if not re.search(r"\d", value) and _CN_UPPER_RE.search(value):
        canonical = parse_cn_amount(value)
        if canonical is None:
            return None
        match = _CN_UPPER_RE.search(value)
        lead = value[:match.start()] if match else ""
        return _Token(
            entity_id=getattr(entity, "id", ""),
            etype=etype, raw=value,
            role=_role_of(entity, rule),
            rule_id=str(getattr(rule, "id", "") or ""),
            lead=lead, unit_text="",
            unit_scale=1, canonical=canonical, is_cn_upper=True,
        )

    # 2) 阿拉伯数字金额
    match = _AMOUNT_RE.match(value)
    if match is None:
        return None
    lead = match.group("lead") or ""
    sign_text = match.group("sign") or ""
    int_text = match.group("int") or ""
    dec_text = match.group("dec") or ""
    unit_text = match.group("unit") or ""
    unit_key = unit_text.strip()
    has_comma = "," in int_text

    if unit_key:
        if unit_key not in _UNIT_SCALE:
            return None
        unit_scale = _UNIT_SCALE[unit_key]
    else:
        # 没有单位时，必须有货币前缀（¥ / 人民币 / RMB），否则不当作金额
        if not any(hint in lead for hint in _CURRENCY_HINTS):
            return None
        unit_scale = 1

    digits = int_text.replace(",", "") + (dec_text or "")
    try:
        canonical = Decimal(digits) * unit_scale
    except (InvalidOperation, ValueError):
        return None
    if sign_text == "-":
        canonical = -canonical
    decimals = len(dec_text) - 1 if dec_text else 0
    return _Token(
        entity_id=getattr(entity, "id", ""),
        etype=etype, raw=value,
        role=_role_of(entity, rule),
        rule_id=str(getattr(rule, "id", "") or ""),
        lead=lead, int_text=int_text, dec_text=dec_text,
        has_comma=has_comma, decimals=decimals,
        unit_text=unit_text, unit_scale=unit_scale,
        sign=-1 if sign_text == "-" else 1,
        canonical=canonical,
    )


# ── 格式化 ────────────────────────────────────────────────────────────


#: 中文大写金额与小写金额视为"同一笔钱"的相对误差容限（OCR 变体误差，Batch2-B2）
CN_UPPER_ALIGN_TOLERANCE = Decimal("0.02")


def _align_cn_upper_tokens(tokens: list[_Token]) -> int:
    """把中文大写金额对齐到同文档唯一近似的小写金额（Batch2-B2）。

    背景：OCR 会把同一笔金额在大写行/小写行识别出微小差异（如 366740.37 vs
    366731.20），若各自缩放，AI 版大写与小写会自相矛盾。这里对每个大写 token：
    在同文档的非大写 token 中找相对差 <= 2% 的候选值；**唯一候选**时把大写
    token 的 canonical 对齐到该小写值（随后由 format_cn_amount 重新生成大写，
    与同一 scaled 值一致）；多候选/无候选保持原逻辑。返回对齐的 token 数。
    """
    lower = [token for token in tokens if not token.is_cn_upper]
    aligned = 0
    for token in tokens:
        if not token.is_cn_upper:
            continue
        candidates: dict[Decimal, _Token] = {}
        for candidate in lower:
            scale = max(abs(token.canonical), abs(candidate.canonical))
            if scale == 0:
                continue
            if abs(token.canonical - candidate.canonical) / scale <= CN_UPPER_ALIGN_TOLERANCE:
                candidates[candidate.canonical] = candidate
        if len(candidates) != 1:          # 无候选 / 多候选：保持原逻辑
            continue
        target = next(iter(candidates))
        if target != token.canonical:
            token.canonical = target
            aligned += 1
    return aligned


def _format_number(value: Decimal, decimals: int, has_comma: bool) -> str:
    """按原样的小数位/千分位渲染一个数（value 已经是目标单位）。"""
    quantum = Decimal(1).scaleb(-decimals) if decimals else Decimal(1)
    quantized = Decimal(value).quantize(quantum, rounding=_ROUND)
    text = format(quantized, "f")
    if decimals == 0:
        text = text.split(".")[0]
    if has_comma:
        integer, dot, fraction = text.partition(".")
        if integer.lstrip("-").isdigit():
            integer = f"{int(integer):,}"
        text = integer + (dot + fraction if dot else "")
    return text


def _render(token: _Token, yuan_value: Decimal) -> str:
    """把"元"金额按 token 原格式渲染成替换文本。"""
    if token.is_cn_upper:
        return token.lead + format_cn_amount(yuan_value)
    unit_value = yuan_value / token.unit_scale
    number = _format_number(unit_value, token.decimals, token.has_comma)
    if token.sign < 0 and not number.startswith("-") and yuan_value != 0:
        number = "-" + number
    elif token.sign > 0 and number.startswith("-"):
        number = number[1:]
    return token.lead + number + token.unit_text


def _displayed_yuan(token: _Token, yuan_value: Decimal) -> Decimal:
    """按 token 的显示精度回折算成"元"，用于合计=分项之和的精确一致。"""
    if token.is_cn_upper:
        # 大写金额精确到分，直接返回量化后的值
        return format_cn_roundtrip(token, yuan_value)
    unit_value = yuan_value / token.unit_scale
    quantum = Decimal(1).scaleb(-token.decimals) if token.decimals else Decimal(1)
    quantized = unit_value.quantize(quantum, rounding=_ROUND)
    return quantized * token.unit_scale


def format_cn_roundtrip(token: _Token, yuan_value: Decimal) -> Decimal:
    """中文大写金额显示后能还原的元数值（到分）。"""
    amount = abs(Decimal(yuan_value)).quantize(_CENT, rounding=_ROUND)
    if yuan_value < 0:
        amount = -amount
    return amount


# ── 因子与会话 ────────────────────────────────────────────────────────


def _session_get(session, key: str, default: Any = None) -> Any:
    if session is None:
        return default
    if isinstance(session, dict):
        return session.get(key, default)
    return getattr(session, key, default)


def _factor_range(session) -> tuple[Decimal, Decimal]:
    raw = _session_get(session, "amount_factor_range")
    if raw is None:
        return DEFAULT_FACTOR_RANGE
    try:
        low, high = Decimal(str(raw[0])), Decimal(str(raw[1]))
    except (TypeError, ValueError, IndexError, InvalidOperation):
        return DEFAULT_FACTOR_RANGE
    if not (Decimal(0) < low <= high):
        return DEFAULT_FACTOR_RANGE
    return low, high


def _select_factor(tokens: Iterable[_Token], session) -> Decimal:
    """确定性选取缩放因子 k：同一组金额 + 同一 salt → 同一 k。"""
    low, high = _factor_range(session)
    salt = str(_session_get(session, "amount_salt", "") or "")
    canon = sorted({format(token.canonical, "f") for token in tokens})
    digest = hashlib.md5(("|".join(canon) + "|" + salt).encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    factor = Decimal(str(round(rng.uniform(float(low), float(high)), 6))).quantize(
        Decimal("0.000001"))
    # 必须"变过"，不允许 k=1（否则等于没脱敏）
    if factor == Decimal(1):
        factor = (low + (high - low) * Decimal("0.3")).quantize(Decimal("0.000001"))
    return max(low, min(high, factor))


def _token_quantum(token: _Token) -> Decimal:
    """该 token 的显示精度换算成"元"后的最小变化步长（Decimal 指数形式）。"""
    if token.is_cn_upper:
        return _CENT
    return (Decimal(token.unit_scale) * Decimal(1).scaleb(-token.decimals)).normalize()


def _write_session(session, entity, replacement: str) -> None:
    """把 原值→假值 写回会话图，保证可逆导出的映射库能还原金额。"""
    if session is None:
        return
    etype = str(getattr(entity, "etype", "") or "").upper()
    value = str(getattr(entity, "value", "") or "")
    if not etype or not value:
        return
    if isinstance(session, dict):
        table = session.get(etype)
        if not isinstance(table, dict):
            table = {}
            session[etype] = table
        table[value] = replacement
        return
    flat = getattr(session, "map", None)
    if isinstance(flat, dict):
        flat[(etype, value)] = replacement
        marker = getattr(session, "mark_used", None)
        if callable(marker):
            try:
                marker(etype, replacement)
            except Exception:  # noqa: BLE001 - 会话对象形状不可控
                pass


def _entity_skipped(entity) -> bool:
    """keep / 已涂黑 / rejected 的实体不参与金额缩放。"""
    if str(getattr(entity, "status", "") or "") == "rejected":
        return True
    decision = getattr(entity, "decision", None)
    if decision is not None:
        if getattr(decision, "black", False):
            return True
        if str(getattr(decision, "strategy", "") or "") in ("keep", "black", "drop"):
            return True
    return False


def _rule_skips(rule) -> bool:
    """规则显式声明 keep/black/drop 时不参与缩放（如 L1 的注册资本）。"""
    if rule is None:
        return False
    opts = getattr(rule, "replace", None) or {}
    return str(opts.get("strategy") or "").lower() in ("keep", "black", "drop")


# ── 主接口 ────────────────────────────────────────────────────────────


def build_amount_plan(entities, rules, session) -> dict[str, str]:
    """给一批金额实体生成"组内一致缩放 + 合计重算"的替换文本。

    :param entities: 待处理实体（``pipeline.detect()`` 结果；只处理 AMOUNT 类）
    :param rules: ``{entity_id: Rule}`` 或 ``list[Rule]``；用于读取
                  ``replace.amount_role``（line/tax/total/subtotal）
    :param session: ``pipeline.Session`` / dict / None；用于读取 salt、
                    缩放范围，并回写"原值→假值"映射（可逆导出依赖它）
    :return: ``{entity_id: replacement_text}``；未识别为金额的实体不返回
    """
    rules_map: dict[str, Any] = {}
    if isinstance(rules, dict):
        rules_map = {str(key): value for key, value in rules.items()}
    elif isinstance(rules, (list, tuple)):
        for rule in rules:
            if getattr(rule, "id", None):
                rules_map[str(rule.id)] = rule

    tokens: list[_Token] = []
    entity_by_id: dict[str, Any] = {}
    for entity in entities or []:
        entity_id = str(getattr(entity, "id", ""))
        entity_by_id[entity_id] = entity
        if _entity_skipped(entity):
            continue
        rule = rules_map.get(entity_id)
        if _rule_skips(rule):
            continue
        token = _parse_token(entity, rule)
        if token is not None:
            tokens.append(token)
    if not tokens:
        return {}

    # Batch2-B2：大写金额行与小写金额行常因 OCR 差几分钱（如 366740.37 vs
    # 366731.20）。唯一近似候选时把大写 canonical 对齐到小写值，让两者参与同一
    # 缩放、大写由 format_cn_amount 重新生成，避免 AI 版大小写自相矛盾。
    _align_cn_upper_tokens(tokens)

    factor = _select_factor(tokens, session)

    # 唯一金额值 → 缩放后"元"数值。
    # 精度取同值各写法里最粗的步长（如同时出现"5,000万元"和"50,000,000.00元"，
    # 就量化到 1 万元），保证同一金额在跨单位写法下仍是同一个数值。
    canonical_values = sorted({token.canonical for token in tokens})
    quanta: dict[Decimal, Decimal] = {}
    for token in tokens:
        quantum = max(_CENT, _token_quantum(token))
        quanta[token.canonical] = max(quanta.get(token.canonical, _CENT), quantum)
    scaled: dict[Decimal, Decimal] = {}
    for value in canonical_values:
        quantum = quanta.get(value, _CENT)
        original = value.quantize(quantum, rounding=_ROUND)
        scaled_value = (value * factor).quantize(quantum, rounding=_ROUND)
        if scaled_value == original and value != 0:  # 粗单位下不能"等于没变"
            scaled_value = original + quantum
        # 0 保持 0（零金额不携带信息，改成 1 会破坏合计/勾稽）
        scaled[value] = scaled_value

    # ── 合计识别 ──
    total_values: set[Decimal] = set()
    total_tokens = [token for token in tokens if token.role == "total"]
    part_tokens = [token for token in tokens if token.role in ("line", "tax", "subtotal")]
    if total_tokens and part_tokens:
        total_canon = {token.canonical for token in total_tokens}
        part_canon = {token.canonical for token in part_tokens if token.canonical not in total_canon}
        if len(part_canon) >= 2:
            original_total = sum(total_canon)
            original_parts = sum(part_canon)
            tolerance = max(Decimal("0.01"), abs(original_total) * Decimal("0.0005"))
            if abs(original_total - original_parts) <= tolerance:
                total_values = total_canon
    if not total_values:
        # 数值关系兜底：最大数恰好等于其余各数之和 → 视为合计
        for candidate in sorted(canonical_values, reverse=True):
            others = [value for value in canonical_values if value != candidate]
            if len(others) < 2:
                continue
            tolerance = max(Decimal("0.01"), abs(candidate) * Decimal("0.0005"))
            if abs(candidate - sum(others)) <= tolerance:
                total_values = {candidate}
                break

    # 合计值 = 分项显示值之和（保证"分项相加 = 合计"在显示层严格成立）
    if total_values:
        part_values = [value for value in canonical_values if value not in total_values]
        if len(part_values) >= 2:
            total_effective = Decimal(0)
            for value in part_values:
                sample = next(token for token in tokens if token.canonical == value)
                total_effective += _displayed_yuan(sample, scaled[value])
            for value in total_values:
                scaled[value] = total_effective.quantize(_CENT, rounding=_ROUND)

    plan: dict[str, str] = {}
    for token in tokens:
        if not token.entity_id:
            continue
        replacement = _render(token, scaled[token.canonical])
        plan[token.entity_id] = replacement
        # 顺手写回会话映射（可逆导出的映射库依赖它；apply_plan 会幂等再写一次）
        entity = entity_by_id.get(token.entity_id)
        if entity is not None:
            _write_session(session, entity, replacement)
    return plan


def apply_plan(entities, plan: dict[str, str], session=None) -> int:
    """把 ``build_amount_plan`` 的结果写回实体决策与会话映射。

    :return: 实际覆盖的实体数。
    """
    if not plan:
        return 0
    from ..udm import Decision

    by_id = {str(getattr(entity, "id", "")): entity for entity in entities or []}
    count = 0
    for entity_id, replacement in plan.items():
        entity = by_id.get(str(entity_id))
        if entity is None:
            continue
        entity.decision = Decision(strategy="surrogate", replacement=replacement)
        _write_session(session, entity, replacement)
        count += 1
    return count


__all__ = [
    "AMOUNT_ETYPES",
    "CN_UPPER_ALIGN_TOLERANCE",
    "DEFAULT_FACTOR_RANGE",
    "build_amount_plan",
    "apply_plan",
    "parse_cn_amount",
    "format_cn_amount",
]
