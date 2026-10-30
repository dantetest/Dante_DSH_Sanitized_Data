# -*- coding: utf-8 -*-
"""文档级一致性：标签写法归一 + 伪值生成所需事实提取。

两类问题（P1-1）：

1. 同一实体因标签写法不同被拆成两个实体（D12：``住院号：ZY…`` 与
   ``住院号 ZY…``），各自生成不同假值，AI 视图里同一住院号出现两个值。
   这里在 decide 之前把「同类型 + 同核心值、仅标签分隔符不同」的实体合并，
   保留一个实体（合并 spans），映射库只落一条。
2. 身份证假值需要“与文档保留的年龄/性别一致”，否则 AI 从假身份证推生日时
   会与“67 岁/女”自相矛盾。这里从文档文本提取年龄/性别，交给 surrogate 使用。

只做本地解析，不访问网络；报告/日志不写原值。
"""
from __future__ import annotations

import re
from collections import defaultdict

# 这些类型的值常带「住院号：」「账号:」等标签前缀；同核心值应合并为一个实体。
LABELED_ETYPES = frozenset({
    "MEDICAL_RECORD", "RECORD_NO", "ACCOUNT", "CASE_NO", "INVOICE_NO",
    "INVOICE_CODE", "TXN_NO", "LICENSE", "CREDIT_LINE", "SHEET_NO",
    "CONTROL_POINT",
})

_LABEL_COLON_RE = re.compile(
    r"^\s*([\u4e00-\u9fa5A-Za-z]{2,10})\s*[:：]\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-]{3,30})\s*$")
_LABEL_SPACE_RE = re.compile(
    r"^\s*([\u4e00-\u9fa5A-Za-z]{2,10})\s+"
    r"([A-Za-z0-9][A-Za-z0-9\-]{3,30})\s*$")

_GENDER_RE = re.compile(r"性别\s*[:：]?\s*(男|女)")
_AGE_RE = re.compile(r"年龄\s*[:：]?\s*(\d{1,3})")
_AGE_SUFFIX_RE = re.compile(r"(\d{1,3})\s*岁")
_DATE_RE = re.compile(r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})日?")
_DATE_LABEL_RE = re.compile(
    r"(入院|入院日期|入院时间|就诊|就诊日期|检查|检查日期|报告|报告日期|"
    r"采集|采样|申请|出具|签署|签订|日期|时间)\s*[:：]?\s*$")


def _find_reference_date(text: str) -> tuple[int, int, int] | None:
    """找文档里的“事件日期”（入院/就诊/报告/签署等），用于计算年龄。

    D12 的年龄 67 是入院日期 2025-03-17 时的年龄；假身份证生日必须按这个
    基准日推导，否则按“今天”推导会与入院日期差 1–2 岁（AI 会指出矛盾）。
    """
    for match in _DATE_RE.finditer(text or ""):
        year, month, day = (int(match.group(1)), int(match.group(2)),
                            int(match.group(3)))
        if not (1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
            continue
        prefix = text[max(0, match.start() - 12):match.start()]
        if _DATE_LABEL_RE.search(prefix):
            return year, month, day
    return None


def split_label_value(value: str) -> tuple[str, str] | None:
    """拆出 (标签, 核心值)；不是“标签+结构化值”的形态返回 None。

    标签保留原始写法（可能带冒号/空格），核心值用于同值判断。
    """
    text = str(value or "").strip()
    for regex in (_LABEL_COLON_RE, _LABEL_SPACE_RE):
        match = regex.match(text)
        if not match:
            continue
        label, core = match.group(1), match.group(2)
        # 排除把普通中文词+数字误当标签（如“金额 123”不在 LABELED_ETYPES 内，
        # 但同类型内部也要避免把“第 123 条”这种当实体；核心至少 4 位）。
        if len(core) < 4 or len(label) > 10:
            return None
        return label, core
    return None


def _span_key(span) -> tuple:
    return (getattr(span, "block_id", None), getattr(span, "start", -1),
            getattr(span, "end", -1), getattr(span, "page", None),
            tuple(getattr(span, "rect", None) or ()))


def merge_labeled_variants(entities: list) -> dict:
    """把同类型、同核心值、仅标签写法不同的实体合并为一个（合并 spans）。

    典型：``住院号：ZY202503170045`` / ``住院号 ZY202503170045``。
    返回 ``{"groups": n, "merged": m}``；不改 entity.value 以外语义，
    只删除重复实体并把 spans 并入 canonical。
    """
    stats = {"groups": 0, "merged": 0}
    buckets: dict[tuple[str, str], list] = defaultdict(list)
    for entity in entities or []:
        etype = (getattr(entity, "etype", "") or "").upper()
        if etype not in LABELED_ETYPES:
            continue
        if getattr(entity, "status", "accepted") == "rejected":
            continue
        parsed = split_label_value(getattr(entity, "value", ""))
        if not parsed:
            continue
        _label, core = parsed
        buckets[(etype, core.upper())].append(entity)

    remove: set[int] = set()
    for (_etype, _core), group in buckets.items():
        if len(group) < 2:
            continue
        # canonical：优先带冒号（更完整的标签写法），其次原值更长，再按首次出现
        canonical = max(
            group,
            key=lambda e: (":" in e.value or "：" in e.value,
                           len(e.value), -group.index(e)))
        spans = list(canonical.spans or [])
        seen = {_span_key(s) for s in spans}
        for entity in group:
            if entity is canonical:
                continue
            for span in entity.spans or []:
                key = _span_key(span)
                if key not in seen:
                    seen.add(key)
                    spans.append(span)
            remove.add(id(entity))
        canonical.spans = spans
        stats["groups"] += 1
        stats["merged"] += len(group) - 1
    if remove:
        entities[:] = [e for e in entities if id(e) not in remove]
    return stats


def merge_warning(stats: dict) -> str | None:
    if not stats or not stats.get("groups"):
        return None
    return (f"同值标签写法归一 {stats['groups']} 组"
            f"（{stats['merged']} 个重复实体并入），AI 视图不再出现同一值两种假值")


def extract_facts(doc) -> dict:
    """从文档文本提取一致性事实：年龄、性别。

    D12 这类单证件文档足够；多证件场景由调用方把 ``id_count`` 设为 >1，
    surrogate 会退回安全的随机生成，不强行套用文档级年龄。
    """
    parts: list[str] = []
    try:
        for _page, block in doc.all_blocks():
            if getattr(block, "kind", "") == "text" and getattr(block, "text", ""):
                parts.append(block.text)
    except Exception:
        parts = []
    text = "\n".join(parts)
    facts: dict = {}
    genders = set(_GENDER_RE.findall(text))
    if len(genders) == 1:
        facts["gender"] = genders.pop()
    ages = {int(m) for m in _AGE_RE.findall(text) if 0 <= int(m) <= 120}
    if not ages:
        ages = {int(m) for m in _AGE_SUFFIX_RE.findall(text) if 0 <= int(m) <= 120}
    if len(ages) == 1:
        facts["age"] = ages.pop()
    ref = _find_reference_date(text)
    if ref:
        facts["ref_date"] = ref
    return facts
