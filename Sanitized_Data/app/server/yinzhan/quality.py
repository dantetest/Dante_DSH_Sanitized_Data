# -*- coding: utf-8 -*-
"""上游质量护栏：实体质量闸门（阴影/启用） + 映射一致性检查。

本模块只做**本地、只读/可配置**的安全护栏，不改变导出格式：

* 实体质量闸门：识别「明显不像该类型」的检测结果（如人名=公章、机构=按照银行、
  数字开头的机构）。默认 **shadow**（只统计、不改动）；``enforce`` 时只丢弃
  高精度类别，且身份证/账号等隐私关键类型永不自动丢。
* 映射一致性检查：扫描同一假值是否对应多个原值，尤其是**跨机构类别**的归并
  （法院/学校/公司被并成一个假值）以及超大簇。默认 warn（写 warning/报告），
  可配置为 block（AI 导出前阻断）。
* 所有对外报告只含类型/数量/掩码后的假值，不写原值；原值相关统计只留在本机
  内存/加密映射库中。
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

# ────────────────────────── 配置 ──────────────────────────

QUALITY_MODES = ("off", "shadow", "enforce")
GUARD_MODES = ("off", "warn", "block")


def quality_mode() -> str:
    raw = (os.environ.get("YINZHAN_ENTITY_QUALITY") or "enforce").strip().lower()
    return raw if raw in QUALITY_MODES else "enforce"


def guard_mode() -> str:
    raw = (os.environ.get("YINZHAN_MAPPING_GUARD") or "warn").strip().lower()
    return raw if raw in GUARD_MODES else "warn"


# 机构类别（用于归并核对/映射检查）；后缀按从具体到宽泛的顺序命中
ORG_CATEGORY_SUFFIXES: dict[str, tuple[str, ...]] = {
    "court": ("最高人民法院", "高级人民法院", "中级人民法院", "人民法院", "法院"),
    "procuratorate": ("人民检察院", "检察院"),
    "bank": ("农商银行", "商业银行", "银行", "支行", "分行", "信用社", "信用合作社"),
    "school": ("职业技术学校", "技术学校", "中学", "小学", "幼儿园", "大学",
               "学院", "学校"),
    "hospital": ("人民医院", "中医院", "医院", "卫生院", "诊所", "保健院"),
    "firm": ("律师事务所", "会计师事务所", "事务所"),
    "research": ("研究所", "研究院", "实验室", "研究中心", "中心"),
    "government": ("人民政府", "政府", "管委会", "委员会", "办公室", "管理局",
                   "税务局", "财政局", "公安局", "局", "厅", "委员会"),
    "company": ("股份有限公司", "有限责任公司", "有限公司", "集团公司", "集团",
                "公司", "厂", "店", "商行", "合作社", "工作室"),
}

# 只在 PERSON/ORG 上使用；不用于隐私关键类型。
# 高精度规则必须“几乎不可能出现在真实实体里”，否则 enforce 会误杀漏保护。
_PERSON_JUNK_EXACT = frozenset({
    "公章", "管理好", "签字", "签名", "盖章", "日期", "工作", "管理", "公司",
    "银行", "单位", "本人", "经办", "开票", "复核",
})
_PERSON_JUNK_PHRASES = ("公章", "签字", "签名", "盖章", "财务专用", "合同专用")
_ORG_JUNK_PHRASES = ("有权", "可以", "按照", "所在", "甲方", "乙方", "管理好",
                     "公章", "签字", "盖章", "详见", "附件", "管辖", "所在地",
                     "本所", "本行", "本公司")
_NOISE_PREFIXES = ("根据", "按照", "完成", "建设", "证明", "依法", "关于",
                   "对于", "通过", "以及", "其中", "经查")

# 归并簇大小上限（超出不自动归并，转 review）
MERGE_LIMITS = {"ORG": 4, "ADDRESS": 5}


def mask_value(value: str, keep: int = 2) -> str:
    """报告里的假值打码（不是原值；仅用于识别是哪个簇）。"""
    text = str(value or "")
    if len(text) <= keep + 2:
        return text[:1] + "*" if text else ""
    return text[:keep] + "***" + text[-keep:]


# ────────────────────────── 机构类别 / 归并辅助 ──────────────────────────

def org_category(value: str) -> str:
    """返回机构类别；未知返回空串。"""
    text = str(value or "").strip()
    for name, suffixes in ORG_CATEGORY_SUFFIXES.items():
        for suffix in suffixes:
            if text.endswith(suffix):
                return name
    return ""


_ADMIN_TOKENS = (
    "特别行政区", "自治区", "自治州", "自治县", "自治旗", "省", "市", "县",
    "区", "镇", "乡", "街道", "地区", "盟", "旗",
)


def admin_only_core(core: str) -> bool:
    """纯行政区名（盐源县/北京市/西藏日喀则地区…）不构成机构简称。"""
    text = str(core or "").strip()
    if not text:
        return True
    # 以行政区后缀结尾、且本身不长的 core（盐源县/北京市/西藏日喀则地区）
    for token in _ADMIN_TOKENS:
        if text.endswith(token) and len(text) <= 8:
            return True
    for token in _ADMIN_TOKENS:
        text = text.replace(token, "")
    return len(text) <= 1



def strip_org_noise(core: str) -> str:
    """剥掉 OCR/句子上下文噪声前缀（只剥多字前缀，避免误伤“中石油”等）。"""
    text = str(core or "").strip()
    changed = True
    while changed:
        changed = False
        for prefix in _NOISE_PREFIXES:
            if text.startswith(prefix) and len(text) - len(prefix) >= 2:
                text = text[len(prefix):]
                changed = True
                break
    return text


def org_categories_compatible(a: str, b: str) -> bool:
    """两个机构值是否允许同簇：类别不同（都已知时）直接拒绝。"""
    ca, cb = org_category(a), org_category(b)
    if ca and cb and ca != cb:
        return False
    return True


# ────────────────────────── 实体质量闸门 ──────────────────────────

def classify_entity(etype: str, value: str, origin: str = "",
                    label: str = "") -> dict:
    """判断实体是否是“明显低质量”；返回 {drop: bool, reasons: [..]}。

    只对 PERSON / ORG 给出 drop=True；其它类型一律不动（隐私关键类型保护）。
    """
    etype = (etype or "").upper()
    text = str(value or "").strip()
    reasons: list[str] = []
    if etype not in ("PERSON", "ORG"):
        return {"drop": False, "reasons": reasons}
    if not text:
        return {"drop": False, "reasons": reasons}
    if origin in ("manual",):
        return {"drop": False, "reasons": reasons}

    if etype == "PERSON":
        if text in _PERSON_JUNK_EXACT:
            reasons.append("常见版面词/动作词")
        elif any(phrase in text for phrase in _PERSON_JUNK_PHRASES):
            reasons.append("印章/签名/公文章节词")
        return {"drop": bool(reasons), "reasons": reasons}

    # ORG：只丢“句子片段/动作词/印章词”等几乎不可能是机构名的值。
    # 数字/英文开头、无机构后缀都只算 review（真实“3M中国有限公司”不能被丢）。
    if any(phrase in text for phrase in _ORG_JUNK_PHRASES):
        reasons.append("动词/公文片段")
    if "的" in text:
        reasons.append("含句子虚词")
    if (label and ("法院" in label or "检察院" in label)) and any(
            word in text for word in ("有权", "可以", "按照", "所在", "甲方",
                                      "乙方", "管辖", "所在地", "向", "在",
                                      "于", "由")):
        reasons.append("法院名含句子片段")
    return {"drop": bool(reasons), "reasons": reasons}



def apply_quality_gate(entities: list, mode: str | None = None) -> dict:
    """在 pipeline.decide 之前调用。

    - shadow：只统计，不改实体；
    - enforce：只丢弃高精度低质量 PERSON/ORG（隐私关键类型不动），并计数；
    - off：不做事。
    返回报告（只含类型/数量/原因，不含原值）。
    """
    mode = mode or quality_mode()
    report: dict = {"mode": mode, "total": len(entities or []),
                    "would_drop": {}, "dropped": {}, "review": {},
                    "reasons": {}}
    if mode == "off":
        return report
    for entity in entities or []:
        if getattr(entity, "status", "accepted") == "rejected":
            continue
        verdict = classify_entity(getattr(entity, "etype", ""),
                                  getattr(entity, "value", ""),
                                  getattr(entity, "origin", ""),
                                  getattr(entity, "label", ""))
        if not verdict["drop"]:
            continue
        etype = (getattr(entity, "etype", "") or "OTHER").upper()
        bucket = report["would_drop"] if mode == "shadow" else report["dropped"]
        bucket[etype] = bucket.get(etype, 0) + 1
        for reason in verdict["reasons"]:
            report["reasons"][reason] = report["reasons"].get(reason, 0) + 1
        if mode == "enforce":
            entity.status = "rejected"
            # 内部原因只留在实体上，不进对外 JSON；便于本地审计/单测
            try:
                entity.quality_drop_reason = "；".join(verdict["reasons"])
            except Exception:
                pass
    report["would_drop_total"] = sum(report["would_drop"].values())
    report["dropped_total"] = sum(report["dropped"].values())
    return report


def quality_report_warning(report: dict) -> str | None:
    """把质量闸门报告变成一条可展示 warning（无原值）。"""
    if not report:
        return None
    mode = report.get("mode")
    if mode == "shadow" and report.get("would_drop_total"):
        counts = "、".join(f"{k} {v}" for k, v in sorted(report["would_drop"].items()))
        return (f"实体质量闸门（阴影）：预计可过滤 {report['would_drop_total']} 个"
                f"低质量实体（{counts}），当前未改动")
    if mode == "enforce" and report.get("dropped_total"):
        counts = "、".join(f"{k} {v}" for k, v in sorted(report["dropped"].items()))
        return (f"实体质量闸门：已过滤 {report['dropped_total']} 个低质量实体"
                f"（{counts}），不影响隐私关键类型")
    return None


# ────────────────────────── 映射一致性检查 ──────────────────────────

def _issue(kind: str, etype: str, fake: str, members: list[str],
           categories: Counter | None = None) -> dict:
    item = {"kind": kind, "etype": etype, "fake_masked": mask_value(fake),
            "members": len(members)}
    if categories:
        item["categories"] = dict(categories)
    return item


def _guard_groups(groups: dict[tuple[str, str], list[str]]) -> dict:
    issues: list[dict] = []
    checked = 0
    for (etype, fake), origs in groups.items():
        if len(origs) <= 1:
            continue
        checked += 1
        cats: Counter = Counter()
        if etype == "ORG":
            cats = Counter(org_category(o) for o in origs if org_category(o))
            if len(cats) > 1:
                issues.append(_issue("hetero_category", etype, fake, origs, cats))
        limit = MERGE_LIMITS.get(etype, 6)
        if len(origs) > limit:
            issues.append(_issue("oversized_cluster", etype, fake, origs, cats))
    mode = guard_mode()
    return {
        "mode": mode,
        "ok": not issues,
        "issues": issues,
        "counts": {
            "groups": checked,
            "hetero_category": sum(1 for i in issues if i["kind"] == "hetero_category"),
            "oversized_cluster": sum(1 for i in issues if i["kind"] == "oversized_cluster"),
        },
    }


def check_mapping_guard(entities: list) -> dict:
    """从实体决策（导出前/任务完成时）检查同一假值是否错并。"""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entity in entities or []:
        decision = getattr(entity, "decision", None)
        if decision is None:
            continue
        if getattr(entity, "status", "accepted") == "rejected":
            continue
        if getattr(decision, "strategy", "") != "surrogate":
            continue
        fake = getattr(decision, "replacement", None)
        value = getattr(entity, "value", "")
        if fake and value:
            groups[((getattr(entity, "etype", "") or "OTHER").upper(), fake)].append(value)
    return _guard_groups(groups)


def check_mapping_guard_from_map(mapping: dict | None) -> dict:
    """从 vault 的 {etype: {orig: fake}} 检查（旧映射也能提示）。"""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for etype, table in (mapping or {}).items():
        if not isinstance(table, dict):
            continue
        for orig, fake in table.items():
            if orig and fake:
                groups[((etype or "OTHER").upper(), str(fake))].append(str(orig))
    return _guard_groups(groups)


def guard_report_warning(report: dict | None) -> str | None:
    """把映射一致性检查结果变成可展示 warning（无原值）。"""
    if not report or report.get("ok", True):
        return None
    counts = report.get("counts") or {}
    return (f"映射一致性检查：发现 {counts.get('hetero_category', 0)} 组跨类别归并、"
            f"{counts.get('oversized_cluster', 0)} 个超大簇；"
            "同假值还原可能覆盖多处，请人工核对")


