# -*- coding: utf-8 -*-
"""AI 回件还原：把大模型交回来的新文件，用本机映射把假值换回真值。

背景
----
换名版导出时，映射库（``data/vault/<job_id>.bin``）保存了每个实体的
「原值 → 假值」。大模型拿着换名版干完活交回一个**新文件**，里面出现的
假名/假金额/假机构需要用同一份映射换回真值，再交给人看。

还原对象是**实体值**，不是文件 diff：AI 会重写、重排、摘要，逐字对照原件
没有意义。本模块负责：

* 列出本机可还原的映射（``list_mapping_infos``）；
* 从回件提取文本并给映射打分（``prepare_file``）；
* 按格式还原：txt/md/csv/json/docx/xlsx/pptx/pdf（文本层）/图片（OCR 重绘）；
* 生成还原报告（替换明细、告警、需人工核对项）与审计记录。

边界（必须保留在产品文案里）
--------------------------
* 只能还原「换名」的文本实体；涂黑区域、图片内文字、AI 自己改写/重算的
  内容不在映射里，还原不了。
* 输入/输出都只在本机处理，绝不外呼；报告含真实信息，不要回传云端模型。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from lxml import etree

from . import audit, quality, vault
from .config import OUTPUT_DIR, WORK_DIR
from .utils import safe_name, sha256_file, unique_path

try:  # 复用实体类型的中文标签；循环导入不了时退回 etype 原文
    from .jobs import _TYPE_LABEL as TYPE_LABEL
except Exception:  # pragma: no cover - 正常导入不会走到
    TYPE_LABEL = {}

# 映射库里还有一批 jobs._TYPE_LABEL 没覆盖的实体类型（旧版/行业规则）
_EXTRA_LABELS = {
    "USCC": "统一社会信用代码", "ACCOUNT": "银行账号",
    "RECORD_NO": "住院号/病历号", "MEDICAL_RECORD": "病历号/住院号",
    "LICENSE": "许可证号", "INVOICE_CODE": "发票代码", "INVOICE_NO": "发票号码",
    "TXN_NO": "交易号", "CREDIT_LINE": "授信额度", "SHEET_NO": "图幅号",
    "CONTROL_POINT": "控制点", "AMOUNT_CN": "大写金额",
}

# ────────────────────────── 常量 ──────────────────────────

TEXT_EXTS = {".txt": "txt", ".md": "md", ".markdown": "md", ".log": "txt",
             ".text": "txt", ".json": "json", ".csv": "csv", ".tsv": "csv",
             ".html": "txt", ".htm": "txt", ".xml": "txt"}
IMAGE_EXTS = {".png": "image", ".jpg": "image", ".jpeg": "image",
              ".webp": "image", ".bmp": "image", ".tif": "image",
              ".tiff": "image"}
OFFICE_EXTS = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx",
               ".pdf": "pdf"}
ALL_EXTS = {**TEXT_EXTS, **IMAGE_EXTS, **OFFICE_EXTS}

RESTORE_DIR = WORK_DIR / "restore"
RESTORE_DIR.mkdir(parents=True, exist_ok=True)

# 候选最多返回几个；没有命中时退回最近几个映射供手动选择
MAX_CANDIDATES = 8
FALLBACK_CANDIDATES = 5
# 回件临时副本保留条数（超出后惰性清理）
MAX_ITEMS = 24
ITEM_TTL = 24 * 3600

PREVIEW_CHARS = 800


class RestoreError(Exception):
    """可预期的还原失败；API 层转成结构化错误。"""

    def __init__(self, message: str, code: str = "restore_failed", status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def type_label(etype: str) -> str:
    if not etype:
        return "其他"
    return TYPE_LABEL.get(etype) or _EXTRA_LABELS.get(etype) or etype


# ────────────────────────── 映射 ──────────────────────────

_AMOUNT_SYMBOL_RE = re.compile(r"^([¥￥])\s*")
_PREFIX_RE = re.compile(r"^[^:：]{1,12}[:：]\s*")

# 带标签前缀的结构化值：“账号:6679…”“住院号：WR…”。AI 回件常常只抄号码、
# 把“账号:”/“住院号：”当成栏目标签；必须为这种无前缀写法生成派生匹配项，
# 否则会漏还原（D07 银行账号实测）或把前缀重复插入（D12 住院号实测）。
_LABELED_VALUE_RE = re.compile(r"^([^:：\s]{1,12})(?:[:：]\s*|\s+)(\S.*)$")
_CORE_ETYPES = {
    "RECORD_NO", "MEDICAL_RECORD", "ACCOUNT", "INVOICE_NO", "INVOICE_CODE",
    "TXN_NO", "CASE_NO", "LICENSE", "CREDIT_LINE", "SHEET_NO", "CONTROL_POINT",
}
# 结构化值在 AI 回件里常见的写法差异：空格、全角/中文括号、大小写
_STRUCTURED_ETYPES = {
    "USCC", "ID_CARD", "PASSPORT", "PHONE", "BANK", "ACCOUNT", "EMAIL",
    "CASE_NO", "INVOICE_NO", "INVOICE_CODE", "TXN_NO", "LICENSE",
    "CREDIT_LINE", "SHEET_NO", "CONTROL_POINT", "RECORD_NO", "MEDICAL_RECORD",
}
_BRACKET_MAP = str.maketrans({"【": "[", "】": "]", "〔": "[", "〕": "]",
                              "［": "[", "］": "]", "「": "", "」": ""})


@dataclass(frozen=True)
class Pair:
    """一组「假值 → 原值」。

    普通实体：fake/orig 就是全部语义。
    派生写法（derived=True，例如“账号:…”去掉标签后的号码）：fake/orig 是
    实际匹配/替换用的核心值；report_fake/report_orig 指回原始实体，供报告
    展示与覆盖率统计，避免把派生写法算成新实体。
    """

    fake: str
    orig: str
    etype: str
    report_fake: str | None = None
    report_orig: str | None = None
    derived: bool = False

    @property
    def label(self) -> str:
        return type_label(self.etype)

    @property
    def display_fake(self) -> str:
        return self.report_fake or self.fake

    @property
    def display_orig(self) -> str:
        return self.report_orig or self.orig

    @property
    def key(self) -> str:
        """同一实体的稳定 key（派生写法归并回原实体）。"""
        return self.report_fake or self.fake


@dataclass
class MappingInfo:
    job_id: str
    name: str
    seal: str
    saved_at: str
    pairs: list[Pair]
    by_type: dict[str, int]
    conflicts: list[str] = field(default_factory=list)
    derived: list[Pair] = field(default_factory=list)
    quality_warnings: list[str] = field(default_factory=list)
    mapping_guard: dict = field(default_factory=dict)
    _index: "_Index | None" = field(default=None, init=False, repr=False)

    @property
    def count(self) -> int:
        return len(self.pairs)

    @property
    def all_pairs(self) -> list[Pair]:
        return self.pairs + self.derived

    @property
    def index(self) -> "_Index":
        if self._index is None:
            self._index = _Index(self.all_pairs)
        return self._index

    def public(self) -> dict:
        return {"job_id": self.job_id, "name": self.name, "seal": self.seal,
                "saved_at": self.saved_at, "pairs": self.count,
                "by_type": self.by_type}


def _normalize(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _amount_variants(fake: str) -> set[str]:
    """金额写法：¥/￥、空格、千分位、裸数字（含“元”后缀场景）。"""
    out: set[str] = set()
    s = fake.strip()
    m = _AMOUNT_SYMBOL_RE.match(s)
    symbol = ""
    if m:
        symbol = m.group(1)
        s = s[m.end():]
    s = s.strip()
    if not s:
        return out
    digits = s.replace(",", "")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", digits):
        return out
    grouped = ""
    if "." in digits:
        head, tail = digits.split(".", 1)
        grouped = f"{int(head):,}.{tail}" if head.isdigit() else digits
    else:
        grouped = f"{int(digits):,}" if digits.isdigit() else digits
    bodies = {digits, grouped}
    for body in bodies:
        out.add(body)
        for sym in ("¥", "￥", "") if not symbol else (symbol, "¥", "￥", ""):
            if sym:
                out.add(sym + body)
            else:
                out.add(body)
    # 带空格的常见写法
    for body in bodies:
        for sym in ("¥", "￥"):
            out.add(f"{sym} {body}")
    # 裸数字太短时容易误伤（如 ¥12），只保留带符号/小数/长数字
    if "." not in digits and len(digits) < 5:
        out = {v for v in out if v not in bodies}
    return out


_AMOUNT_UNIT_RE = re.compile(r"^(.*?)(万元|元)$")


def _amount_number(text: str) -> tuple["Decimal", str] | None:
    """把「¥2,057,500.00」「205.75万元」「91000元」拆成 (数值, 单位)。

    返回的数值是**按文本原单位**的值：205.75万元 → Decimal('205.75')，单位「万元」；
    91000元 → Decimal('91000')，单位「元」；没有单位按「元」处理。
    """
    from decimal import Decimal, InvalidOperation

    s = (text or "").strip()
    unit = "元"
    m = _AMOUNT_UNIT_RE.match(s)
    if m:
        s, unit = m.group(1), m.group(2)
    s = s.replace("¥", "").replace("￥", "").replace(",", "").replace(" ", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return None
    try:
        return Decimal(s), unit
    except InvalidOperation:
        return None


def _numeric_amount_map(pairs: list[Pair]) -> dict[str, tuple[str, Pair]]:
    """给 Excel 数值单元格用的「假金额数值 → 真金额数值」表。

    AI 回件常把金额写成纯数字单元格（不带 ¥/元/万元），文本节点替换抓不到；
    这里按数量精确匹配：原单位数值 + 折算成元的数值都建索引。只用于 AMOUNT。
    """
    from decimal import Decimal

    mapping: dict[str, tuple[str, Pair]] = {}
    for pair in pairs:
        if (pair.etype or "").upper() != "AMOUNT" or pair.derived:
            continue
        fake = _amount_number(pair.fake)
        orig = _amount_number(pair.orig)
        if fake is None or orig is None:
            continue
        fake_value, fake_unit = fake
        orig_value, orig_unit = orig
        candidates: list[tuple[Decimal, Decimal, str]] = []
        # 同单位写法：205.75万元 → 188.05万元
        candidates.append((fake_value, orig_value, _number_text(pair.orig)))
        # 折算成元：2057500 → 1880500（同一数值，只换数）
        if fake_unit != orig_unit:
            continue
        factor = Decimal(10000) if fake_unit == "万元" else Decimal(1)
        candidates.append((fake_value * factor, orig_value * factor,
                          _number_text_value(orig_value * factor)))
        for fake_num, orig_num, orig_text in candidates:
            key = _decimal_key(fake_num)
            if key and key not in mapping:
                mapping[key] = (orig_text, pair)
    return mapping


def _decimal_key(value) -> str:
    from decimal import Decimal

    try:
        return str(Decimal(value).normalize())
    except Exception:
        return ""


def _number_text(raw: str) -> str:
    """从原始金额文本里保留数字写法（去掉符号/分隔符/单位）。"""
    value = _amount_number(raw)
    if value is None:
        return ""
    number, _unit = value
    text = format(number, "f")
    return text


def _number_text_value(value) -> str:
    from decimal import Decimal

    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _structured_variants(base: str, etype: str) -> set[str]:
    """结构化值的写法差异：空格、中文/全角括号、大小写、纯数字。"""
    out: set[str] = set()
    compact = re.sub(r"\s+", "", base)
    if compact != base:
        out.add(compact)
    bracket = base.translate(_BRACKET_MAP)
    if bracket != base:
        out.add(bracket)
    if etype in ("USCC", "ID_CARD", "PASSPORT"):
        out.add(base.upper())
        out.add(base.lower())
    if etype in ("PHONE", "BANK", "ACCOUNT"):
        digits = re.sub(r"\D", "", base)
        if len(digits) >= 7:
            out.add(digits)
    return out


def _variants_for(fake: str, etype: str) -> set[str]:
    base = fake.strip()
    if not base:
        return set()
    out = {base, _normalize(base)}
    etype = (etype or "").upper()
    if etype == "AMOUNT":
        out |= _amount_variants(base)
    elif etype == "EMAIL":
        out.add(base.lower())
    elif etype in ("ORG", "ADDRESS", "PERSON"):
        out.add(_normalize(base))
    if etype in _STRUCTURED_ETYPES:
        out |= _structured_variants(base, etype)
    # 清洗：去掉首尾空白、空串、单字符（误伤概率太高）；派生核心写法由
    # _build_pairs 额外生成 Pair，不在这里塞进同一变体（否则替换结果会带错误前缀）。
    return {v for v in out if v and v.strip() and len(v.strip()) >= 2 and v == v.strip()}


def _label_core(value: str) -> str | None:
    """把“账号:6679…”/“住院号：WR…”拆成核心号码；不是带标签值则返回 None。"""
    m = _LABELED_VALUE_RE.match(value or "")
    if not m:
        return None
    core = m.group(2).strip()
    return core if len(core) >= 4 else None


def _build_pairs(mapping: dict) -> tuple[list[Pair], list[Pair], dict[str, int], list[str]]:
    """把 vault 映射转成去重后的 Pair 列表。

    返回 (canonical 实体对, 派生写法对, by_type, 冲突提示)：
    * canonical 参与映射条数/覆盖率统计与报告展示；
    * 派生写法（无标签前缀的核心号码）只参与匹配/替换，不改变实体计数。
    """
    fake2pair: dict[str, Pair] = {}
    conflicts: list[str] = []
    by_type: dict[str, int] = {}
    for etype, table in (mapping.get("map") or {}).items():
        if not isinstance(table, dict):
            continue
        for orig, fake in table.items():
            if not fake or not orig:
                continue
            prev = fake2pair.get(fake)
            if prev is not None and prev.orig != orig:
                conflicts.append(
                    f"假值「{_mask(fake)}」对应多个原值（近似实体归并），已按先出现的还原；"
                    "如上下文不匹配请人工核对")
                continue
            if prev is not None:
                continue
            fake2pair[fake] = Pair(fake=fake, orig=orig, etype=etype)
            by_type[etype] = by_type.get(etype, 0) + 1
    pairs = sorted(fake2pair.values(), key=lambda p: len(p.fake), reverse=True)

    derived: list[Pair] = []
    seen = {p.fake: p for p in pairs}
    for pair in pairs:
        if pair.etype not in _CORE_ETYPES:
            continue
        core_fake = _label_core(pair.fake)
        core_orig = _label_core(pair.orig)
        if not core_fake or not core_orig or core_fake == pair.fake:
            continue
        prev = seen.get(core_fake)
        if prev is not None:
            if prev.orig != core_orig:
                conflicts.append(
                    f"派生写法「{_mask(core_fake)}」对应多个原值，已按先出现的还原")
            continue
        item = Pair(fake=core_fake, orig=core_orig, etype=pair.etype,
                    report_fake=pair.fake, report_orig=pair.orig, derived=True)
        derived.append(item)
        seen[core_fake] = item
    derived.sort(key=lambda p: len(p.fake), reverse=True)
    return pairs, derived, by_type, conflicts


def _quality_warnings(pairs: list[Pair]) -> list[str]:
    """给明显像 OCR/版面误检的映射对提示，避免还原出语义异常的值。"""
    bad = []
    for pair in pairs:
        orig = pair.orig or ""
        etype = (pair.etype or "").upper()
        if len(orig) <= 1:
            bad.append(pair)
        elif etype == "PERSON" and any(k in orig for k in
                                       ("公章", "签字", "签名", "盖章", "日期",
                                        "管理", "公司", "银行")):
            bad.append(pair)
        elif etype == "ORG" and (orig[:1].isdigit()
                                 or orig.startswith(("按照", "1甲方", "方有权在"))
                                 or "有权在甲方" in orig):
            bad.append(pair)
    if not bad:
        return []
    return [
        f"映射库含 {len(bad)} 组疑似 OCR/版面误检实体（如人名=公章、机构=按照银行），"
        "还原结果可能出现语义异常；建议核对映射来源"
    ]


def _mask(s: str, keep: int = 2) -> str:
    """报告/日志里给敏感值打码；行内展示给本机用户的地方才用原值。"""
    s = str(s or "")
    if len(s) <= keep + 2:
        return s[:1] + "*"
    return s[:keep] + "***" + s[-keep:]


def load_mapping_info(job_id: str) -> MappingInfo:
    raw = vault.load_mapping(job_id)
    if raw is None:
        raise RestoreError(f"找不到任务 {job_id} 的映射（可能当时导出的是涂黑版，或映射已被擦除）",
                           "mapping_not_found", 404)
    pairs, derived, by_type, conflicts = _build_pairs(raw)
    if not pairs:
        raise RestoreError("这个任务的映射里没有可还原的实体", "mapping_not_found", 404)
    meta = raw.get("meta") or {}
    # 映射一致性护栏（P0-1 前哨）：旧映射也重新算一遍，跨类别归并/超大簇
    # 在还原报告里显式提示；报告修复后仍保留该检查，防止未来回归。
    mapping_guard = quality.check_mapping_guard_from_map(raw.get("map") or {})
    return MappingInfo(
        job_id=job_id,
        name=str(meta.get("name") or job_id),
        seal=str(meta.get("seal") or ""),
        saved_at=str(raw.get("saved_at") or ""),
        pairs=pairs,
        by_type=by_type,
        conflicts=conflicts,
        derived=derived,
        quality_warnings=_quality_warnings(pairs),
        mapping_guard=mapping_guard,
    )


def list_mapping_infos() -> list[dict]:
    """本机所有可还原的映射（按保存时间倒序）。"""
    out: list[dict] = []
    for item in vault.list_vault():
        try:
            info = load_mapping_info(item["job_id"])
        except Exception:
            continue
        out.append(info.public())
    out.sort(key=lambda x: x.get("saved_at") or "", reverse=True)
    return out


# ────────────────────────── 扫描器 ──────────────────────────

class _Index:
    """按首字符分桶、长串优先的替换索引。"""

    def __init__(self, pairs: Iterable[Pair]):
        by_first: dict[str, list[tuple[str, Pair]]] = {}
        seen: dict[str, Pair] = {}
        for pair in pairs:
            variants = _variants_for(pair.fake, pair.etype)
            for v in variants:
                if v in seen:
                    continue
                seen[v] = pair
                by_first.setdefault(v[0], []).append((v, pair))
        for bucket in by_first.values():
            bucket.sort(key=lambda item: len(item[0]), reverse=True)
        self.by_first = by_first
        self.size = len(seen)

    def match(self, text: str, pos: int):
        bucket = self.by_first.get(text[pos]) if pos < len(text) else None
        if not bucket:
            return None
        for variant, pair in bucket:
            if text.startswith(variant, pos):
                return variant, pair
        return None


@dataclass(frozen=True)
class Event:
    start: int
    end: int
    pair: Pair
    variant: str

    @property
    def length(self) -> int:
        return self.end - self.start


def scan_replace(text: str, index: _Index) -> tuple[str, list[Event]]:
    """单遍扫描：长串优先、不级联。返回 (还原后文本, 事件列表)。"""
    if not text:
        return text, []
    out: list[str] = []
    events: list[Event] = []
    i, n = 0, len(text)
    while i < n:
        hit = index.match(text, i)
        if hit is None:
            out.append(text[i])
            i += 1
            continue
        variant, pair = hit
        out.append(pair.orig)
        events.append(Event(i, i + len(variant), pair, variant))
        i += len(variant)
    return "".join(out), events


def count_text(text: str, index: _Index) -> tuple[int, int]:
    """返回 (命中处数, 命中不同实体数)。派生写法归并回原实体计数。"""
    if not text:
        return 0, 0
    _, events = scan_replace(text, index)
    return len(events), len({e.pair.key for e in events})


# ────────────────────────── 报告 ──────────────────────────

@dataclass
class Artifact:
    kind: str            # main / text
    path: Path
    name: str


@dataclass
class Report:
    fmt: str
    strategy: str
    replaced: int = 0
    scan_pages: int = 0
    warnings: list[str] = field(default_factory=list)
    replacements: dict[str, dict] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    elapsed_ms: int = 0
    input_name: str = ""
    input_sha256: str = ""

    def add_event(self, event: Event) -> None:
        self.replaced += 1
        key = event.pair.key
        item = self.replacements.get(key)
        if item is None:
            self.replacements[key] = {
                "fake": event.pair.display_fake, "orig": event.pair.display_orig,
                "etype": event.pair.etype, "label": event.pair.label,
                "count": 1,
            }
        else:
            item["count"] += 1

    def add_events(self, events: Iterable[Event]) -> None:
        for event in events:
            self.add_event(event)

    def by_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.replacements.values():
            out[item["etype"]] = out.get(item["etype"], 0) + 1
        return out

    @property
    def unique_replaced(self) -> int:
        return len(self.replacements)

    def public(self) -> dict:
        items = sorted(self.replacements.values(),
                       key=lambda x: x["count"], reverse=True)
        warnings = list(dict.fromkeys(self.warnings))
        return {
            "format": self.fmt,
            "strategy": self.strategy,
            "counts": {"replaced": self.replaced,
                       "unique_replaced": self.unique_replaced,
                       "unmatched": 0,
                       "scan_pages": self.scan_pages,
                       "warnings": len(self.warnings)},
            "by_type": self.by_type(),
            "replacements": items,
            "warnings": warnings,
            "outputs": [],   # API/MCP 层补 url / path
            "elapsed_ms": self.elapsed_ms,
        }


# ────────────────────────── 通用工具 ──────────────────────────

def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    fmt = ALL_EXTS.get(suffix)
    if fmt:
        return fmt
    # 兜底：未知后缀只有「确实能严格解成文本」才按纯文本还原；
    # 二进制必须先判不支持，否则会拿 errors=replace 乱改一遍。
    try:
        raw = path.read_bytes()
    except OSError:
        return "unknown"
    if b"\x00" in raw[:8192]:
        return "unknown"
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            raw.decode(enc)
            return "txt"
        except UnicodeDecodeError:
            continue
    return "unknown"


def _decode_text(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def _write_text(path: Path, text: str, encoding: str) -> None:
    enc = encoding if encoding != "utf-8" else "utf-8"
    try:
        data = text.encode(enc)
    except UnicodeEncodeError:
        data = text.encode("utf-8")
    path.write_bytes(data)


def _zip_rewrite(src: Path, dst: Path, handler) -> None:
    """逐部件重写 zip；handler(name, bytes) -> bytes|None。"""
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                try:
                    new_data = handler(info.filename, data)
                except Exception:
                    new_data = None
                zout.writestr(info, data if new_data is None else new_data)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _xml_tostring(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                          standalone=True)


def _nearest(el, tag: str):
    for anc in el.iterancestors():
        if anc.tag == tag:
            return anc
    return None


def _restore_nodes(nodes: list, index: _Index, report: Report) -> bool:
    """docx 段落 / xlsx 富文本单元：把一组文本节点的拼接文本还原后回填。

    替换可能跨节点（Word 常把一个词拆成多个 run）；原值统一写进替换起点
    所在的节点，其它被覆盖的节点文本清空，后缀保留在最后一个节点。
    """
    texts = [(node.text or "") for node in nodes]
    full = "".join(texts)
    if not full:
        return False
    new_texts: list[str] = [""] * len(nodes)
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for text in texts:
        offsets.append((cursor, cursor + len(text)))
        cursor += len(text)
    new_full, events = scan_replace(full, index)
    if not events:
        return False
    plan = {event.start: event for event in events}
    node_i = 0
    pos = 0
    total = len(full)
    while pos < total:
        while node_i < len(offsets) and offsets[node_i][1] <= pos:
            node_i += 1
        target = node_i if node_i < len(nodes) else len(nodes) - 1
        event = plan.get(pos)
        if event is not None:
            new_texts[target] += event.pair.orig
            pos = event.end
        else:
            new_texts[target] += full[pos]
            pos += 1
    changed = False
    for i, node in enumerate(nodes):
        value = new_texts[i]
        if value != texts[i]:
            node.text = value
            if value[:1].isspace() or value[-1:].isspace():
                node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            changed = True
    report.add_events(events)
    return changed


# ────────────────────────── 各格式还原 ──────────────────────────

def _restore_plain_text(src: Path, dst: Path, index: _Index, report: Report) -> None:
    text, enc = _decode_text(src.read_bytes())
    new_text, events = scan_replace(text, index)
    _write_text(dst, new_text, enc)
    report.add_events(events)
    report.strategy = "text/plain"
    if enc not in ("utf-8", "utf-8-sig"):
        report.warnings.append(f"按原编码 {enc} 写回")


_CSV_SEPS = [",", ";", "\t", "|"]


def _restore_csv(src: Path, dst: Path, index: _Index, report: Report) -> None:
    text, enc = _decode_text(src.read_bytes())
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(_CSV_SEPS))
    except Exception:
        dialect = csv.excel_tab if src.suffix.lower() == ".tsv" else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    events = []
    for row in rows:
        for i, cell in enumerate(row):
            new_cell, cell_events = scan_replace(cell, index)
            if cell_events:
                row[i] = new_cell
                events.extend(cell_events)
    out = io.StringIO()
    terminator = "\r\n" if "\r\n" in text else "\n"
    writer = csv.writer(out, dialect=dialect, lineterminator=terminator)
    writer.writerows(rows)
    _write_text(dst, out.getvalue(), enc if enc != "utf-8" else "utf-8")
    report.add_events(events)
    report.strategy = "table/csv-cell"
    report.warnings.append("表格按单元值重写，引号/换行风格可能与原文件不同")
    if enc not in ("utf-8", "utf-8-sig"):
        report.warnings.append(f"按原编码 {enc} 写回")


def _restore_json(src: Path, dst: Path, index: _Index, report: Report) -> None:
    text, enc = _decode_text(src.read_bytes())
    try:
        data = json.loads(text)
    except Exception as exc:
        raise RestoreError(f"回件不是合法 JSON：{exc}", "restore_failed", 400)
    events: list[Event] = []

    def walk(value):
        if isinstance(value, str):
            new_value, found = scan_replace(value, index)
            if found:
                events.extend(found)
            return new_value
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    new_data = walk(data)
    payload = json.dumps(new_data, ensure_ascii=False, indent=2) + "\n"
    _write_text(dst, payload, "utf-8")
    report.add_events(events)
    report.strategy = "json/values"
    report.warnings.append("JSON 已按标准缩进重写（字段与值不变）")


# ── DOCX ──

_DOCX_PARTS = re.compile(
    r"^word/(document|header\d*|footer\d*|footnotes|endnotes|comments|"
    r"glossary/document)\.xml$")
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_P = f"{{{_W}}}p"
_W_T = f"{{{_W}}}t"


def _restore_docx(src: Path, dst: Path, index: _Index, report: Report) -> None:
    def handler(name: str, data: bytes):
        if not _DOCX_PARTS.match(name):
            return None
        root = etree.fromstring(data)
        for p in root.iter(_W_P):
            nodes = [t for t in p.iter(_W_T) if _nearest(t, _W_P) is p]
            if nodes:
                _restore_nodes(nodes, index, report)
        return _xml_tostring(root)

    _zip_rewrite(src, dst, handler)
    report.strategy = "docx/ooxml-text"
    report.warnings.append("Word 内的图片/图表/嵌入对象中的文字不参与还原")


# ── XLSX ──

_XLSX_PART = re.compile(r"^xl/.*\.xml$")
_X_NS = ("http://schemas.openxmlformats.org/spreadsheetml/2006/main",)


_S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_S_C = f"{{{_S_NS}}}c"
_S_V = f"{{{_S_NS}}}v"
_S_F = f"{{{_S_NS}}}f"


def _restore_xlsx(src: Path, dst: Path, index: _Index, report: Report,
                  numeric_map: dict[str, tuple[str, Pair]] | None = None) -> None:
    numeric_map = numeric_map or {}
    numeric_hits = [0]

    def _child(el, tag):
        for child in el:
            if child.tag == tag:
                return child
        return None

    def handler(name: str, data: bytes):
        if not _XLSX_PART.match(name):
            return None
        try:
            root = etree.fromstring(data)
        except Exception:
            return None
        changed = False
        for el in root.iter():
            try:
                localname = etree.QName(el.tag).localname
            except Exception:
                continue
            if localname == "t":
                if el.text:
                    new_text, events = scan_replace(el.text, index)
                    if events:
                        el.text = new_text
                        if new_text[:1].isspace() or new_text[-1:].isspace():
                            el.set("{http://www.w3.org/XML/1998/namespace}space",
                                   "preserve")
                        report.add_events(events)
                        changed = True
                continue
            if localname != "c" or not numeric_map:
                continue
            # 数值单元格：只处理纯数字、无公式的单元格（AI 常把金额写成数字）
            if el.get("t") not in (None, "n"):
                continue
            if _child(el, _S_F) is not None:
                continue
            value_el = _child(el, _S_V)
            if value_el is None or not (value_el.text or "").strip():
                continue
            try:
                from decimal import Decimal
                key = _decimal_key(Decimal(value_el.text.strip()))
            except Exception:
                continue
            hit = numeric_map.get(key)
            if hit is None:
                continue
            orig_text, pair = hit
            value_el.text = orig_text
            report.add_event(Event(0, 0, pair, pair.fake))
            numeric_hits[0] += 1
            changed = True
        if not changed:
            return None
        return _xml_tostring(root)

    _zip_rewrite(src, dst, handler)
    report.strategy = "xlsx/xml-text"
    report.warnings.append("Excel 的公式、图表、图片里的文字不参与还原")
    if numeric_hits[0]:
        report.warnings.append(
            f"Excel 数值单元格按金额映射还原 {numeric_hits[0]} 处；"
            "请核对单元格单位（元/万元）与格式")


# ── PPTX ──

_PPTX_PART = re.compile(r"^ppt/.*\.xml$")
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_A_T = f"{{{_A_NS}}}t"
_A_P = f"{{{_A_NS}}}p"


def _restore_pptx(src: Path, dst: Path, index: _Index, report: Report) -> None:
    def handler(name: str, data: bytes):
        if not _PPTX_PART.match(name):
            return None
        try:
            root = etree.fromstring(data)
        except Exception:
            return None
        changed = False
        for p in root.iter(_A_P):
            nodes = [t for t in p.iter(_A_T) if _nearest(t, _A_P) is p]
            if nodes and _restore_nodes(nodes, index, report):
                changed = True
        if not changed:
            return None
        return _xml_tostring(root)

    _zip_rewrite(src, dst, handler)
    report.strategy = "pptx/ooxml-text"
    report.warnings.append("幻灯片里的图片/图表中的文字不参与还原")


def _extract_docx_text(path: Path) -> str:
    parts: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not _DOCX_PARTS.match(name):
                    continue
                root = etree.fromstring(z.read(name))
                for p in root.iter(_W_P):
                    text = "".join(t.text or "" for t in p.iter(_W_T))
                    if text:
                        parts.append(text)
    except Exception:
        pass
    return "\n".join(parts)


def _extract_xlsx_text(path: Path) -> str:
    parts: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not _XLSX_PART.match(name):
                    continue
                try:
                    root = etree.fromstring(z.read(name))
                except Exception:
                    continue
                for el in root.iter():
                    try:
                        localname = etree.QName(el.tag).localname
                    except Exception:
                        continue
                    if localname == "t" and el.text:
                        parts.append(el.text)
    except Exception:
        pass
    return "\n".join(parts)


def _extract_pptx_text(path: Path) -> str:
    parts: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not _PPTX_PART.match(name):
                    continue
                try:
                    root = etree.fromstring(z.read(name))
                except Exception:
                    continue
                for el in root.iter(_A_T):
                    if el.text:
                        parts.append(el.text)
    except Exception:
        pass
    return "\n".join(parts)


# ── PDF ──

def _pdf_span_at(spans: list[dict], rect) -> dict | None:
    cx = (rect.x0 + rect.x1) / 2.0
    cy = (rect.y0 + rect.y1) / 2.0
    for span in spans:
        bx0, by0, bx1, by1 = span["bbox"]
        if bx0 - 1 <= cx <= bx1 + 1 and by0 - 1 <= cy <= by1 + 1:
            return span
    return None


def _rect_overlap(a, b) -> float:
    x0, y0 = max(a.x0, b.x0), max(a.y0, b.y0)
    x1, y1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    small = min(a.width * a.height, b.width * b.height)
    return inter / small if small > 0 else 0.0


RESTORE_CJK_FONT = "restorecjk"
_RESTORE_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
)


def _ensure_restore_font(page) -> tuple[str, bool]:
    """确保页面用可抽取的 CJK 字体；返回 (字体名, 是否用了自定义字体)。

    P1 修复：很多回件 PDF 自己嵌入了名为 ``china-s`` 的 Type0 子集字体；
    直接 ``insert_text(fontname="china-s")`` 会复用这个子集，抽取出来是
    ``\\x00``（值看起来还原了，实际文本层不可读/不可搜索）。这里用系统中文字体
    注册独立字体名 ``restorecjk``（页面没有 ``china-s`` 冲突时仍走旧路径），
    保存前再 subset，避免整字体嵌入导致文件暴涨。
    """
    try:
        fonts = page.get_fonts(full=True)
        names = {(item[4] if len(item) > 4 else "") for item in fonts}
    except Exception:
        names = set()
    if "china-s" not in names:
        return "china-s", False
    if RESTORE_CJK_FONT in names:
        return RESTORE_CJK_FONT, True
    for path in _RESTORE_FONT_CANDIDATES:
        try:
            if not os.path.exists(path):
                continue
            page.insert_font(fontname=RESTORE_CJK_FONT, fontfile=path)
            return RESTORE_CJK_FONT, True
        except Exception:
            continue
    return "china-s", False


def _pdf_insert(page, rect, text: str, size: float, origin_y: float,
                color: tuple | None, report: Report) -> tuple[bool, bool, bool]:
    """在 PDF 原位插入真值；返回 (是否成功, 是否缩小字号, 是否用自定义字体)。"""
    try:
        import fitz
        font_size = size or 10.0
        width_limit = max(4.0, rect.width)
        # 宽度估算用内置 china-s 近似；实际插入用可抽取的独立字体
        text_width = fitz.get_text_length(text, fontname="china-s",
                                          fontsize=font_size)
        shrunk = False
        if text_width > width_limit:
            font_size = max(4.0, font_size * width_limit / text_width)
            shrunk = True
        fontname, used_custom = _ensure_restore_font(page)
        page.insert_text((rect.x0, origin_y), text, fontsize=font_size,
                         fontname=fontname, color=color or (0, 0, 0))
        return True, shrunk, used_custom
    except Exception:
        return False, False, False


def _restore_pdf(src: Path, dst: Path, item: dict, index: _Index,
                 report: Report) -> Path | None:
    """还原 PDF 文本层；返回扫描页侧车文本路径（没有则 None）。

    扫描页（无文本层）不做版式回写，只 OCR 出还原后的文本放进侧车。
    """
    try:
        import fitz
    except Exception:
        raise RestoreError("本机缺少 PDF 组件，无法还原 PDF", "restore_failed", 503)
    try:
        doc = fitz.open(str(src))
    except Exception as exc:
        raise RestoreError(f"打不开这个 PDF：{exc}", "restore_failed", 400)
    if doc.needs_pass:
        doc.close()
        raise RestoreError("这份 PDF 有密码，先解锁后再还原", "restore_failed", 400)
    scanned_pages: list[tuple[int, str]] = []
    shrunk = 0
    custom_font = False
    text_layer_replaced = 0
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            raw_text = page.get_text("text") or ""
            if not raw_text.strip():
                # 扫描页：OCR 出文本，供侧车还原
                try:
                    pix = page.get_pixmap(dpi=200, alpha=False)
                    from PIL import Image
                    image = Image.frombytes("RGB", (pix.width, pix.height),
                                            pix.samples)
                    from .ocr import get_ocr
                    lines = get_ocr().recognize(image)
                except Exception:
                    lines = []
                changed_lines: list[str] = []
                for line in lines:
                    new_text, events = scan_replace(line.text, index)
                    if events:
                        report.add_events(events)
                        changed_lines.append(new_text)
                    else:
                        changed_lines.append(line.text)
                if lines:
                    scanned_pages.append((pno + 1, "\n".join(changed_lines)))
                continue
            spans: list[dict] = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    spans.extend(line.get("spans", []))
            variants: list[tuple[str, Pair]] = []
            seen_variants: set[str] = set()
            for pair in index_pairs(index):
                for variant in _variants_for(pair.fake, pair.etype):
                    if variant not in seen_variants:
                        seen_variants.add(variant)
                        variants.append((variant, pair))
            variants.sort(key=lambda item_: len(item_[0]), reverse=True)
            selected: list[tuple[Any, Pair]] = []
            for variant, pair in variants:
                try:
                    rects = page.search_for(variant)
                except Exception:
                    continue
                for rect in rects:
                    if rect.is_empty:
                        continue
                    if any(_rect_overlap(rect, old) > 0.35 for old, _ in selected):
                        continue
                    selected.append((rect, pair))
            if not selected:
                continue
            jobs = []
            for rect, pair in selected:
                span = _pdf_span_at(spans, rect)
                size = float(span.get("size") or 10.0) if span else 10.0
                origin_y = float(span["origin"][1]) if span and span.get("origin") else rect.y1 - 2
                color = None
                try:
                    if span and span.get("color") is not None:
                        color = fitz.sRGB_to_pdf(int(span["color"]))
                except Exception:
                    color = None
                jobs.append((rect, pair, size, origin_y, color))
            for rect, _pair, _size, _y, _color in jobs:
                try:
                    page.add_redact_annot(rect, fill=None)
                except Exception:
                    pass
            try:
                page.apply_redactions()
            except Exception:
                pass
            inserted_events: list[Event] = []
            for rect, pair, size, origin_y, color in jobs:
                ok, was_shrunk, used_custom = _pdf_insert(
                    page, rect, pair.orig, size, origin_y, color, report)
                if not ok:
                    report.warnings.append("有 1 处 PDF 文本插入失败，已跳过")
                    continue
                custom_font = custom_font or used_custom
                inserted_events.append(Event(0, 0, pair, pair.fake))
                if was_shrunk:
                    shrunk += 1
            if inserted_events:
                text_layer_replaced += len(inserted_events)
                report.add_events(inserted_events)
        if shrunk:
            report.warnings.append(f"{shrunk} 处真值较长，PDF 中原位置已缩字")
        sidecar: Path | None = None
        if scanned_pages:
            report.scan_pages = len(scanned_pages)
            text_path = dst.with_name(dst.stem.replace("_还原", "") + "_还原文本.txt")
            chunks = ["# 扫描页 OCR 还原文本（原 PDF 无文本层，版式未回写）\n"]
            for pno, text in scanned_pages:
                chunks.append(f"===== 第 {pno} 页 =====\n{text}\n")
            text_path = unique_path(text_path)
            text_path.write_text("\n".join(chunks), "utf-8")
            report.artifacts.append(Artifact(kind="text", path=text_path,
                                             name=text_path.name))
            sidecar = text_path
            report.warnings.append(
                f"{len(scanned_pages)} 页没有文本层，已输出 OCR 还原文本，原版式未回写")
        if text_layer_replaced:
            if custom_font:
                try:
                    doc.subset_fonts()       # 只嵌入实际用到的字形，避免文件暴涨
                except Exception:
                    pass
            doc.save(str(dst), garbage=3, deflate=True)
        else:
            dst.unlink(missing_ok=True)
        report.strategy = "pdf/text-layer"
        return sidecar if dst.exists() else None
    finally:
        try:
            doc.close()
        except Exception:
            pass


def index_pairs(index: _Index) -> list[Pair]:
    pairs: list[Pair] = []
    seen: set[str] = set()
    for bucket in index.by_first.values():
        for _variant, pair in bucket:
            if pair.fake not in seen:
                seen.add(pair.fake)
                pairs.append(pair)
    return pairs


# ── 图片 ──

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def _find_font_path() -> str | None:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _ocr_lines(image) -> list:
    try:
        from .ocr import get_ocr
        ocr = get_ocr()
        if not ocr.available:
            raise RestoreError(
                "本机 OCR 不可用，图片/扫描件无法还原；可把回件另存为 txt/docx 再试",
                "ocr_unavailable", 503)
        return ocr.recognize(image)
    except RestoreError:
        raise
    except Exception as exc:
        raise RestoreError(f"OCR 失败：{exc}", "ocr_unavailable", 503)


def _restore_image(src: Path, dst_png: Path, txt_path: Path, item: dict,
                   index: _Index, report: Report) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        raise RestoreError("本机缺少图片组件，无法还原图片", "restore_failed", 503)
    try:
        image = Image.open(src).convert("RGB")
    except Exception as exc:
        raise RestoreError(f"打不开这张图片：{exc}", "restore_failed", 400)
    lines = _ocr_lines(image)
    if not lines:
        raise RestoreError("图片里没有识别到文字，无法还原", "no_match", 400)
    restored_lines: list[str] = []
    changed_lines: list[tuple] = []
    for line in lines:
        new_text, events = scan_replace(line.text, index)
        restored_lines.append(new_text)
        if events:
            report.add_events(events)
            changed_lines.append((line, new_text))
    txt_path.write_text("\n".join(restored_lines) + "\n", "utf-8")
    report.artifacts.append(Artifact(kind="text", path=txt_path,
                                     name=txt_path.name))
    font_path = _find_font_path()
    if not font_path or not changed_lines:
        report.warnings.append(
            "图片只输出了 OCR 还原文本；未做图片重绘" if changed_lines
            else "图片只输出了 OCR 还原文本")
        report.strategy = "image/ocr-text"
        return
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    fonts: dict[int, Any] = {}

    def font_for(size: int):
        if size not in fonts:
            fonts[size] = ImageFont.truetype(font_path, size)
        return fonts[size]

    for line, new_text in changed_lines:
        x0, y0, x1, y1 = line.box
        w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
        # 背景色：取框上下边缘像素的中位数，尽量贴近原底
        bg = _sample_bg(image, (x0, y0, x1, y1))
        draw.rectangle([x0, y0, x1, y1], fill=bg)
        size = max(8, int(h * 0.82))
        font = font_for(size)
        try:
            text_w = draw.textlength(new_text, font=font)
        except Exception:
            text_w = len(new_text) * size
        while text_w > w and size > 6:
            size -= 1
            font = font_for(size)
            try:
                text_w = draw.textlength(new_text, font=font)
            except Exception:
                text_w = len(new_text) * size
        draw.text((x0, y0 + max(0, (h - size) / 2 - 1)), new_text, font=font,
                  fill=(24, 24, 24))
    canvas.save(dst_png, "PNG")
    report.artifacts.append(Artifact(kind="main", path=dst_png, name=dst_png.name))
    report.strategy = "image/ocr-overlay"
    report.warnings.append("图片为 OCR 识别后重绘，字体/版式是近似还原；跨行实体可能漏还原")


def _sample_bg(image, box) -> tuple[int, int, int]:
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    width, height = image.size
    pixels = []

    def add(x, y):
        if 0 <= x < width and 0 <= y < height:
            pixels.append(image.getpixel((x, y)))

    for x in range(max(0, x0), min(width, int(x1) + 1), 3):
        add(x, y0 - 1)
        add(x, int(y1) + 1)
    for y in range(max(0, y0), min(height, int(y1) + 1), 3):
        add(x0 - 1, y)
        add(int(x1) + 1, y)
    if not pixels:
        return (255, 255, 255)
    pixels.sort(key=lambda p: sum(p))
    r = sorted(p[0] for p in pixels)[len(pixels) // 2]
    g = sorted(p[1] for p in pixels)[len(pixels) // 2]
    b = sorted(p[2] for p in pixels)[len(pixels) // 2]
    return (r, g, b)


# ────────────────────────── 主流程 ──────────────────────────

def _out_stem(name: str) -> str:
    return safe_name(Path(name or "回件").stem, 60) or "回件"


def restore_path(src: Path, name: str, mapping: MappingInfo,
                 out_dir: Path | None = None,
                 pre_extracted: dict | None = None) -> Report:
    """还原一份回件，返回报告（含产物路径）。同步阻塞，调用方负责并发。"""
    started = time.time()
    fmt = detect_format(src)
    if fmt == "unknown":
        raise RestoreError("暂不支持这种回件格式", "unsupported_format", 400)
    out_root = Path(out_dir or OUTPUT_DIR)
    out_root.mkdir(parents=True, exist_ok=True)
    stem = _out_stem(name)
    report = Report(fmt=fmt, strategy="", input_name=name,
                    input_sha256=sha256_file(src))
    report.warnings.extend(mapping.conflicts)
    report.warnings.extend(mapping.quality_warnings)
    guard_msg = quality.guard_report_warning(mapping.mapping_guard)
    if guard_msg:
        report.warnings.append(guard_msg)
    if fmt == "pdf":
        report.warnings.append(
            "PDF 为文本层重绘：字体/字距可能变化，长真值会自动缩字，版式可能轻微位移")

    def out_path(suffix: str) -> Path:
        return unique_path(out_root / f"{stem}_还原{suffix}")

    if fmt in ("txt", "md"):
        dst = out_path(src.suffix or ".txt")
        _restore_plain_text(src, dst, mapping.index, report)
        report.artifacts.append(Artifact(kind="main", path=dst, name=dst.name))
    elif fmt == "csv":
        dst = out_path(src.suffix or ".csv")
        _restore_csv(src, dst, mapping.index, report)
        report.artifacts.append(Artifact(kind="main", path=dst, name=dst.name))
    elif fmt == "json":
        dst = out_path(".json")
        _restore_json(src, dst, mapping.index, report)
        report.artifacts.append(Artifact(kind="main", path=dst, name=dst.name))
    elif fmt == "docx":
        dst = out_path(".docx")
        _restore_docx(src, dst, mapping.index, report)
        report.artifacts.append(Artifact(kind="main", path=dst, name=dst.name))
    elif fmt == "xlsx":
        dst = out_path(".xlsx")
        _restore_xlsx(src, dst, mapping.index, report,
                      numeric_map=_numeric_amount_map(mapping.pairs))
        report.artifacts.append(Artifact(kind="main", path=dst, name=dst.name))
    elif fmt == "pptx":
        dst = out_path(".pptx")
        _restore_pptx(src, dst, mapping.index, report)
        report.artifacts.append(Artifact(kind="main", path=dst, name=dst.name))
    elif fmt == "pdf":
        dst = out_path(".pdf")
        _restore_pdf(src, dst, pre_extracted or {}, mapping.index, report)
        if dst.exists():
            report.artifacts.insert(0, Artifact(kind="main", path=dst, name=dst.name))
    elif fmt == "image":
        png = unique_path(out_root / f"{stem}_还原.png")
        txt = unique_path(out_root / f"{stem}_还原文本.txt")
        _restore_image(src, png, txt, pre_extracted or {}, mapping.index, report)
        if not any(a.kind == "main" for a in report.artifacts):
            report.artifacts.insert(0, Artifact(kind="text", path=txt,
                                                name=txt.name))
    else:  # pragma: no cover - detect_format 已兜底
        raise RestoreError("暂不支持这种回件格式", "unsupported_format", 400)

    if not report.replaced and not report.scan_pages:
        for artifact in report.artifacts:
            try:
                artifact.path.unlink()
            except OSError:
                pass
        raise RestoreError(
            "回件里没有找到这份映射中的假值；请确认选对了映射，或回件是否被 AI 大幅改写",
            "no_match", 400)
    # 纯扫描件/图片没有「主成品」时，把文本侧车提升为主产物，保证前端有主下载项
    if not any(artifact.kind == "main" for artifact in report.artifacts):
        for artifact in report.artifacts:
            artifact.kind = "main"
            break
    report.warnings.append(
        "回件里由 AI 自行计算/推断的数字与结论（不在映射表内）不会自动还原，"
        "可能与真值不一致，请人工复核")
    report.elapsed_ms = int((time.time() - started) * 1000)
    return report


# ────────────────────────── 上传件/会话管理 ──────────────────────────

def extract_probe(path: Path, fmt: str) -> tuple[str, list[str]]:
    """提取回件文本用于匹配打分；返回 (文本, 备注)。"""
    notes: list[str] = []
    if fmt in ("txt", "md", "csv", "json"):
        text, _enc = _decode_text(path.read_bytes())
        return text, notes
    if fmt == "docx":
        return _extract_docx_text(path), notes
    if fmt == "xlsx":
        return _extract_xlsx_text(path), notes
    if fmt == "pptx":
        return _extract_pptx_text(path), notes
    if fmt == "pdf":
        try:
            import fitz
            doc = fitz.open(str(path))
            text = "".join((doc[i].get_text("text") or "")
                           for i in range(doc.page_count))
            if text.strip():
                doc.close()
                return text, notes
            # 没有文本层（扫描件）：OCR 前 2 页先做映射匹配
            from PIL import Image
            from .ocr import get_ocr
            ocr = get_ocr()
            parts: list[str] = []
            for i in range(min(2, doc.page_count)):
                pix = doc[i].get_pixmap(dpi=150, alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height),
                                        pix.samples)
                lines = ocr.recognize(image) if ocr.available else []
                parts.extend(line.text for line in lines)
            doc.close()
            notes.append("PDF 没有文本层（扫描件）：匹配基于前 2 页 OCR，还原时会处理全部页")
            return "\n".join(parts), notes
        except Exception:
            return "", notes
    if fmt == "image":
        try:
            from PIL import Image
            image = Image.open(path).convert("RGB")
            lines = _ocr_lines(image)
            notes.append("图片将用 OCR 文本还原，并尽量重绘回图片")
            return "\n".join(line.text for line in lines), notes
        except RestoreError as exc:
            notes.append(exc.message)
            return "", notes
        except Exception:
            return "", notes
    return "", notes


def _score_candidates(text: str, infos: list[dict] | None = None,
                      name: str | None = None) -> tuple[list[dict], int]:
    """给回件文本与各映射打分。返回 (候选列表, 命中的映射总数)。"""
    infos = infos if infos is not None else list_mapping_infos()
    results: list[dict] = []
    for info in infos:
        try:
            mapping = load_mapping_info(info["job_id"])
        except Exception:
            continue
        # 快速预筛：任何一个变体出现才做精确扫描（派生写法也要算）
        hit_any = False
        for pair in mapping.all_pairs:
            if any(v in text for v in _variants_for(pair.fake, pair.etype)):
                hit_any = True
                break
        if not hit_any:
            continue
        hits, matched = count_text(text, mapping.index)
        if not hits:
            continue
        item = dict(info)
        item.update({"hits": hits, "matched": matched,
                     "coverage": round(matched / max(1, mapping.count), 4),
                     "name_match": bool(name and info.get("name") == name)})
        results.append(item)
    results.sort(key=lambda x: (not x.get("name_match"), -x["coverage"], -x["hits"]))
    total = len(results)
    if results:
        return results[:MAX_CANDIDATES], total
    # 全都没有命中：返回最近几个映射，让用户手动选
    fallback = []
    for info in infos[:FALLBACK_CANDIDATES]:
        item = dict(info)
        item.update({"hits": 0, "matched": 0, "coverage": 0.0,
                     "name_match": bool(name and info.get("name") == name)})
        fallback.append(item)
    return fallback, 0


# ───────── 页面对照预览（回件 ↔ 还原稿） ─────────
_PREVIEW_DPI = 110
_PREVIEW_MAX_SIDE = 1600


def preview_page_count(path: Path) -> int | None:
    """可页面对照预览时返回页数；docx/xlsx/pptx 等暂不支持。"""
    fmt = detect_format(path)
    try:
        if fmt == "pdf":
            import fitz
            doc = fitz.open(str(path))
            try:
                return int(doc.page_count)
            finally:
                doc.close()
        if fmt == "image":
            return 1
    except Exception:
        return None
    return None


def render_preview_png(path: Path, page: int) -> bytes:
    """把回件/还原稿的一页渲染成 PNG（只支持 PDF / 图片）。"""
    fmt = detect_format(path)
    if fmt == "pdf":
        import fitz
        doc = fitz.open(str(path))
        try:
            if doc.page_count <= 0:
                raise RestoreError("文件没有可预览的页", "preview_unsupported", 415)
            idx = max(0, min(int(page), doc.page_count - 1))
            pix = doc[idx].get_pixmap(dpi=_PREVIEW_DPI, alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()
    if fmt == "image":
        from PIL import Image
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            w, h = rgb.size
            longest = max(w, h)
            if longest > _PREVIEW_MAX_SIDE:
                scale = _PREVIEW_MAX_SIDE / float(longest)
                rgb = rgb.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.LANCZOS)
            buf = io.BytesIO()
            rgb.save(buf, "PNG")
            return buf.getvalue()
    raise RestoreError("该格式暂不支持页面对照预览，请下载文件后查看",
                       "preview_unsupported", 415)


def _preview_meta(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"renderable": False, "pages": 0, "format": "", "name": ""}
    fmt = detect_format(path)
    pages = preview_page_count(path)
    return {"renderable": bool(pages), "pages": int(pages or 0),
            "format": fmt, "name": path.name}


@dataclass
class Prepared:
    rid: str
    path: Path
    name: str
    fmt: str
    sha256: str
    chars: int
    preview: str
    candidates: list[dict]
    notes: list[str]
    created: float
    candidates_total: int = 0

    def public(self) -> dict:
        return {
            "restore_id": self.rid,
            "name": self.name,
            "format": self.fmt,
            "bytes": self.path.stat().st_size,
            "sha256": self.sha256,
            "chars": self.chars,
            "preview": self.preview,
            "candidates": self.candidates,
            "candidates_total": self.candidates_total,
            "notes": self.notes,
        }


class RestoreManager:
    """上传回件的临时会话（本机内存）；退出时由 purge_runtime_dirs 清目录。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, Prepared] = {}

    # ── 上传/匹配 ──
    def prepare(self, upload_id: str, path: Path, name: str) -> Prepared:
        fmt = detect_format(path)
        if fmt == "unknown":
            raise RestoreError("暂不支持这种回件格式（支持 txt/md/csv/json/"
                               "docx/xlsx/pptx/pdf/图片）",
                               "unsupported_format", 400)
        text, notes = extract_probe(path, fmt)
        candidates, candidates_total = _score_candidates(text, name=name)
        preview = text.strip().replace("\r\n", "\n")[:PREVIEW_CHARS]
        prepared = Prepared(
            rid=upload_id, path=path, name=name, fmt=fmt,
            sha256=sha256_file(path), chars=len(text),
            preview=preview, candidates=candidates, notes=notes,
            created=time.time(), candidates_total=candidates_total,
        )
        with self._lock:
            self._items[upload_id] = prepared
            self._evict_locked()
        return prepared

    def _evict_locked(self) -> None:
        now = time.time()
        stale = [rid for rid, item in self._items.items()
                 if now - item.created > ITEM_TTL]
        for rid in stale:
            self._items.pop(rid, None)
        while len(self._items) > MAX_ITEMS:
            oldest = min(self._items.values(), key=lambda x: x.created)
            self._items.pop(oldest.rid, None)

    def get(self, rid: str) -> Prepared:
        with self._lock:
            item = self._items.get(rid)
        if item is None:
            raise RestoreError("回件会话已过期，请重新上传", "bad_request", 400)
        return item

    # ── 还原 ──
    def run(self, rid: str, job_id: str) -> tuple[Prepared, MappingInfo, Report]:
        prepared = self.get(rid)
        mapping = load_mapping_info(job_id)
        report = restore_path(prepared.path, prepared.name, mapping)
        return prepared, mapping, report

    # ── 下载 ──
    def artifact(self, rid: str, kind: str) -> Artifact:
        prepared = self.get(rid)
        report = getattr(prepared, "_report", None)
        if report is None:
            raise RestoreError("还原结果不在了，请重新还原", "bad_request", 400)
        for artifact in report.artifacts:
            if artifact.kind == kind:
                return artifact
        raise RestoreError("没有这个下载项", "not_found", 404)

    def attach_report(self, prepared: Prepared, report: Report) -> None:
        prepared._report = report  # type: ignore[attr-defined]

    # ── 页面对照预览 ──
    def preview_meta(self, rid: str) -> dict:
        """回件与主成品的页面对照预览信息（供结果页左右对照）。"""
        prepared = self.get(rid)
        report = getattr(prepared, "_report", None)
        main = next((a for a in report.artifacts if a.kind == "main"), None) if report else None
        return {
            "input": _preview_meta(prepared.path),
            "output": _preview_meta(main.path) if main else _preview_meta(None),
        }

    def preview_png(self, rid: str, side: str, page: int) -> tuple[bytes, int]:
        """返回 (PNG bytes, 总页数)；side=input 回件 / output 还原稿。"""
        prepared = self.get(rid)
        if side == "input":
            path = prepared.path
        elif side == "output":
            report = getattr(prepared, "_report", None)
            main = next((a for a in report.artifacts if a.kind == "main"), None) if report else None
            if main is None:
                raise RestoreError("还原结果不在了，请重新还原", "bad_request", 400)
            path = main.path
        else:
            raise RestoreError("side 只能是 input 或 output", "bad_request", 400)
        pages = preview_page_count(path)
        if not pages:
            raise RestoreError("该格式暂不支持页面对照预览，请下载文件后查看",
                               "preview_unsupported", 415)
        page = max(0, min(int(page), pages - 1))
        return render_preview_png(path, page), int(pages)

    def cleanup(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
        return count


RESTORE_MANAGER = RestoreManager()


# ────────────────────────── MCP / CLI 直通 ──────────────────────────

def run_path(path: str | Path, job_id: str, out_dir: str | Path | None = None) -> dict:
    """给 MCP/CLI 用：对任意本地回件直接还原（不经过上传会话）。"""
    src = Path(path)
    if not src.is_file():
        raise RestoreError(f"回件不存在：{src}", "bad_request", 400)
    mapping = load_mapping_info(job_id)
    report = restore_path(src, src.name, mapping,
                          out_dir=Path(out_dir) if out_dir else None)
    record_audit(mapping, src, report)
    return report.public() | {"artifacts": [
        {"kind": a.kind, "name": a.name, "path": str(a.path),
         "bytes": a.path.stat().st_size, "sha256": sha256_file(a.path)}
        for a in report.artifacts
    ]}


def record_audit(mapping: MappingInfo, src: Path, report: Report) -> None:
    """写一条还原审计（只记类型/数量/哈希，不记原值）。"""
    main = next((a for a in report.artifacts if a.kind == "main"), None)
    try:
        audit.append(
            job_id=mapping.job_id,
            doc_name=report.input_name or src.name,
            doc_hash=report.input_sha256 or sha256_file(src),
            fmt=report.fmt,
            counts={"replaced": report.replaced,
                    "unique_replaced": report.unique_replaced,
                    "by_type": report.by_type(),
                    "scan_pages": report.scan_pages,
                    "warnings": len(report.warnings),
                    "restore_of": mapping.job_id},
            rule_pack="restore",
            mode="restore",
            out_hash=sha256_file(main.path) if main else "",
            out_name=main.name if main else "",
        )
    except Exception:
        # 审计写失败不能吞掉还原结果，但要在返回里留下痕迹
        report.warnings.append("审计记录写入失败（不影响还原结果）")
