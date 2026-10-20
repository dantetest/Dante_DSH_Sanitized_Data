# -*- coding: utf-8 -*-
"""纯数值单元格检测器（``numeric_cell``，P0）。

背景
----
XLSX 解析器把每个单元格的 ``str(value)`` 当成文本 Block；纯数值金额（如
``28600``）既没有"元/万元"单位、也没有千分位，``common.cn.amount`` 命中不了，
财报/工资表/流水的金额等于"裸奔"（实测 0 命中）。

本检测器依赖 ``xlsx_parser`` 在 Block.attrs 里新增的元数据
（``numeric`` / ``date`` / ``number_format``，**只加元数据、不改解析语义**），
按"列头语义 + 数值类型"生成实体：

- ``mode: amount``  → AMOUNT：列头含 金额/余额/工资/收入/成本/费用/利润/税额…
- ``mode: account`` → ACCOUNT/BANK 等：列头含 账号/卡号/客户号…
- ``mode: date``    → DATE：列头含 日期/时间；默认 **keep**（日期保留，P1 再做模糊化）

与 trade 域的 ``table_det``（表头列级，针对文本单元格）分开：本检测器只处理
**数值/日期单元格**，不抢文本列。

元数据登记表
------------
Entity 没有 attrs 字段（udm.py 由 Lead 维护，本批不改），因此数值单元格的
"小数位/原始类型/所在单元格"记在模块级 ``_META[entity_id]``，由
``replacers/amount_consistency.py`` 与 ``xlsx_parser.export`` 通过
``get_numeric_meta(entity.id)`` 读取。实体在内存里随 Job 存活，导出/预览
与检测同进程，安全；``clear_numeric_meta()`` 供测试/长跑清理。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ..registry import Detector, register_detector
from ..udm import DetectContext, Document, Entity, Rule, Span

#: entity_id -> {kind, number_format, decimals, sheet, cell}
_META: dict[str, dict[str, Any]] = {}

#: 表头归一化：去空白/全角括号/标点
_HEADER_CLEAN = re.compile(r"[\s\u3000（）()【】\[\]、,，:：/／]+")
#: number_format 里第一个格式段的小数位数（"#,##0.00" → 2；"0%" → 0）
_DEC_RE = re.compile(r"\.([0#]+)")


def get_numeric_meta(entity_id: str) -> dict[str, Any] | None:
    """读取某个数值单元格实体的元数据（不存在返回 None）。"""
    return _META.get(str(entity_id or ""))


def clear_numeric_meta() -> None:
    """清空元数据表（测试/长跑可调用；正常随进程结束回收）。"""
    _META.clear()


def _norm_header(text: str) -> str:
    return _HEADER_CLEAN.sub("", str(text or ""))


def decimals_from_format(number_format: str) -> int:
    """从 Excel number_format 推断显示小数位（"#,##0.00" → 2）。"""
    section = str(number_format or "").split(";")[0]
    if "%" in section:
        return 0
    match = _DEC_RE.search(section)
    return len(match.group(1)) if match else 0


class NumericCellDetector(Detector):
    """按"列头语义 + 数值/日期类型"生成实体的表格检测器。"""

    id = "numeric_cell"

    def __init__(self, headers: Iterable[str], mode: str = "amount",
                 header_rows: int = 3, confidence: float = 0.90,
                 min_value: float = 0.0, min_digits: int = 6) -> None:
        cleaned = [_norm_header(h) for h in (headers or ()) if _norm_header(h)]
        if not cleaned:
            raise ValueError("NumericCellDetector 需要非空 headers")
        self.headers: tuple[str, ...] = tuple(cleaned)
        self.mode = str(mode or "amount").strip().lower()
        if self.mode not in ("amount", "account", "date"):
            self.mode = "amount"
        self.header_rows = max(1, int(header_rows))
        self.confidence = float(confidence)
        self.min_value = float(min_value)
        self.min_digits = max(1, int(min_digits))

    @classmethod
    def from_spec(cls, spec: dict) -> "NumericCellDetector":
        if not isinstance(spec, dict):
            raise ValueError("numeric_cell detect 需要 dict")
        headers = (spec.get("headers") or spec.get("header_keywords")
                   or spec.get("columns") or ())
        if isinstance(headers, str):
            headers = [headers]
        if not headers:
            raise ValueError("numeric_cell detect 缺少 headers")
        return cls(
            headers=headers,
            mode=str(spec.get("mode") or spec.get("value_kind") or "amount"),
            header_rows=int(spec.get("header_rows", 3)),
            confidence=float(spec.get("confidence", 0.90)),
            min_value=float(spec.get("min_value", 0.0)),
            min_digits=int(spec.get("min_digits", 6)),
        )

    # ── 表头匹配 ──
    def _match_header(self, text: str) -> bool:
        header = _norm_header(text)
        if not header:
            return False
        return any(keyword in header for keyword in self.headers)

    # ── 单元格筛选 ──
    def _cell_ok(self, block) -> bool:
        attrs = getattr(block, "attrs", None) or {}
        if attrs.get("formula"):
            return False
        number_format = str(attrs.get("number_format") or "")
        if "%" in number_format:
            return False                      # 百分比列不是金额（08b D3）
        if self.mode == "date":
            return bool(attrs.get("date"))
        if not attrs.get("numeric"):
            return False
        if self.mode == "account":
            digits = re.sub(r"\D", "", str(getattr(block, "text", "") or ""))
            return len(digits) >= self.min_digits
        return True

    def _build(self, block, rule: Rule, sheet: str) -> Entity | None:
        text = str(getattr(block, "text", "") or "").strip()
        if not text:
            return None
        attrs = getattr(block, "attrs", None) or {}
        number_format = str(attrs.get("number_format") or "")
        ref = getattr(block, "ref", None) or {}
        meta: dict[str, Any] = {
            "kind": self.mode,
            "number_format": number_format,
            "decimals": decimals_from_format(number_format),
            "sheet": sheet,
            "cell": str(ref.get("cell") or ""),
            "numeric": bool(attrs.get("numeric")),
            "date": bool(attrs.get("date")),
        }
        if self.mode == "amount" and self.min_value:
            match = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
            if match:
                try:
                    if abs(float(match.group(0).replace(",", ""))) < self.min_value:
                        return None
                except ValueError:
                    pass
        entity = Entity(
            id=Entity.new_id(),
            etype=rule.entity,
            label=rule.label,
            value=text,
            spans=[Span(block_id=block.id, start=0, end=len(text))],
            level=rule.level,
            confidence=self.confidence,
            origin="numeric_cell",
            status="accepted",
        )
        _META[entity.id] = meta
        return entity

    # ── 检测入口 ──
    def detect(self, doc: Document, rule: Rule,
               ctx: DetectContext) -> list[Entity]:
        if getattr(doc, "fmt", "") not in ("xlsx", "xlsm"):
            return []
        # (page, sheet) -> [(row, col, block)]
        groups: dict[tuple[int, str], list[tuple[int, int, Any]]] = {}
        for page in doc.pages:
            for block in page.blocks:
                if block.kind != "text" or not (block.text or "").strip():
                    continue
                ref = getattr(block, "ref", None) or {}
                sheet = ref.get("sheet")
                if not sheet:
                    continue                    # 只有 xlsx 有 sheet 列语义
                try:
                    row = int(ref.get("row"))
                    col = int(ref.get("col"))
                except (TypeError, ValueError):
                    continue
                groups.setdefault((getattr(page, "index", 0), str(sheet)), []).append(
                    (row, col, block))

        entities: list[Entity] = []
        for (_page_index, sheet), items in groups.items():
            min_row = min(row for row, _col, _block in items)
            header_limit = min_row + self.header_rows
            # 在前 header_rows 行里找"命中最多的那一行"当表头：
            # 兼容标题行（第 1 行是"利润表"、第 2 行才是"项目/金额"）。
            candidate_rows = sorted({row for row, _col, _block in items
                                     if row < header_limit})
            best_row: int | None = None
            best_score: tuple[int, int] = (0, 0)
            for candidate in candidate_rows:
                row_blocks = [(block.text or "").strip()
                              for row, _col, block in items if row == candidate]
                row_attrs = [(getattr(block, "attrs", None) or {})
                             for row, _col, block in items if row == candidate]
                # 表头行必须是纯文本行：含数值/日期/公式的行是数据行
                # （"收入A" 这类文本里含表头关键词，不能把数据行误判成表头）
                if any((attrs.get("numeric") or attrs.get("date")
                        or attrs.get("formula")) for attrs in row_attrs):
                    continue
                matched = sum(1 for text in row_blocks
                              if text and self._match_header(text))
                non_empty = sum(1 for text in row_blocks if text)
                score = (matched, non_empty)
                if score > best_score:
                    best_row, best_score = candidate, score
            if best_row is None or best_score[0] == 0:
                continue
            headers_by_col: dict[int, str] = {}
            for row, col, block in items:
                if row != best_row:
                    continue
                text = (block.text or "").strip()
                if text:
                    headers_by_col[col] = text
            if not headers_by_col:
                continue
            for col, header_text in headers_by_col.items():
                if not self._match_header(header_text):
                    continue
                for row, item_col, block in items:
                    if item_col != col or row <= best_row:
                        continue
                    if not self._cell_ok(block):
                        continue
                    entity = self._build(block, rule, sheet)
                    if entity is not None:
                        entities.append(entity)
        return entities


register_detector(NumericCellDetector)

__all__ = [
    "NumericCellDetector", "get_numeric_meta", "clear_numeric_meta",
    "decimals_from_format",
]
