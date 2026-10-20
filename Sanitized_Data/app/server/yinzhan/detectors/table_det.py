# -*- coding: utf-8 -*-
"""表头列级检测器（``table_column``，P0）。

背景与设计
==========
表格是财务/人事/客户名单的主载体，现有正则/NER 只逐块检测单元格：列里
"缺位身份证""带空格手机号""没有公司后缀的单位名"等值会漏检。本检测器按
**表头语义**整列处理，**零解析器改动**——只读解析器已给出的 ``Block.ref``：

* XLSX：``ref={"sheet", "cell", "row", "col"}``（一个非空单元格一个 text Block）；
* DOCX：``ref={"t", "r", "c"}``（表格第 t 张、第 r 行、第 c 列，一个单元格一个 text Block）。

规则写法（``rules/table-cn.yaml``，每条规则 = 一类列语义）::

    - id: table.person
      entity: PERSON
      label: 表格姓名列
      level: L2
      scope: [xlsx, docx]
      priority: 14
      detect:
        - type: table_column
          headers: [姓名, 名字, 联系人, 客户名称, 员工姓名]
          header_rows: 3            # 在前 1–3 行内找表头（默认 3）
          confidence: 0.90
          status: accepted          # 可设 pending（高风险列人工确认）
          value_pattern: '^[\u4e00-\u9fa5·]{2,20}$'   # 可选：值形状守卫
          exclude_validate: [uscc_checksum]           # 可选：命中即跳过（让位更权威规则）
          min_len: 2
          max_len: 40
      replace: {strategy: surrogate}

检测流程
========
1. 按 ``(页面, 表格标识)`` 分组：xlsx 用 ``sheet``，docx 用 ``t``；
2. 在前 ``header_rows`` 行内选"命中 headers 最多"的一行作为**表头行**
   （必须至少命中 1 个列头；``headers`` 精确匹配，仅忽略空白/大小写/末尾冒号，
   因此"名称"这类泛词不会因为包含关系被误认）；
3. 对表头行以下、同列的数据单元格整格生成 ``Entity``（span 覆盖 trim 后文本）；
4. 跳过：空单元格、公式单元格、数值/日期单元格（``attrs.numeric/date``，交给
   ``numeric_cell`` 检测器）、重复出现的表头文本；
5. 可选 ``value_pattern``（对去掉空白后的值匹配）与 ``exclude_validate``
   （值命中任一校验器则跳过——例如"证件号"列里的合法 USCC 应留给 ``cn.uscc``，
   避免列级规则以更高密级抢走既有实体）；可选 ``status`` 供高风险列转人工确认；
6. 同值合并/重叠裁剪交给 ``pipeline._merge``；本检测器不直接处理导出。

边界（P0）
==========
* 只处理 xlsx/docx（CSV/TSV 需解析器先给出行列 ref，见 docs/impl/table.md P1）；
* 合并单元格/多级表头的"向上/向左继承"不做（P1，08b E6）；
* 数值型手机号/身份证（Excel 里存成数字）不在本检测器范围（``numeric_cell``
  的 P1 扩展，或"文本格式"导入）；
* 同一 sheet 内上下堆叠多张表时只按最前面的表头行处理（P1 多表切分）。

注册：``registry.load_plugins()`` 扫描 detectors 目录自动导入；打包兜底清单
``registry._PLUGIN_FALLBACK`` 已含 ``table_det``（Lead 已接）。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ..registry import Detector, register_detector
from ..udm import DetectContext, Document, Entity, Rule, Span

try:  # 校验器注册表由 regex_det 维护（跨域复用，不重复注册）
    from .regex_det import _VALIDATORS
except Exception:  # pragma: no cover - 极端情况下不影响主流程
    _VALIDATORS = {}


#: 表头归一化：去空白（含全角）、去末尾冒号、ASCII 大小写归一
_HEADER_CLEAN = re.compile(r"[\s\u3000]+")


def _norm_header(text: Any) -> str:
    value = _HEADER_CLEAN.sub("", str(text or ""))
    return value.rstrip("：:").lower()


class TableColumnDetector(Detector):
    """按表头列语义对数据行整格生成实体。"""

    id = "table_column"

    def __init__(
        self,
        headers: Iterable[str],
        header_rows: int = 3,
        confidence: float = 0.90,
        status: str = "",
        value_pattern: str = "",
        exclude_validate: Iterable[str] | str | None = None,
        min_len: int = 1,
        max_len: int = 200,
        skip_numeric: bool = True,
        skip_formula: bool = True,
    ) -> None:
        cleaned: list[str] = []
        for raw in headers or ():
            text = _norm_header(raw)
            if text and text not in cleaned:
                cleaned.append(text)
        if not cleaned:
            raise ValueError("table_column detect 需要非空 headers")
        self.headers: tuple[str, ...] = tuple(cleaned)
        self.header_rows = max(1, min(int(header_rows), 5))
        self.confidence = float(confidence)
        self.status = status if status in ("pending", "accepted") else ""
        self.value_pattern_text = str(value_pattern or "")
        self.value_regex = (re.compile(self.value_pattern_text)
                            if self.value_pattern_text else None)
        if isinstance(exclude_validate, str):
            exclude_validate = [exclude_validate]
        self.exclude_names: tuple[str, ...] = tuple(
            str(name).strip() for name in (exclude_validate or ()) if str(name).strip()
        )
        self.exclude_validators = tuple(
            validator for validator in (_VALIDATORS.get(name) for name in self.exclude_names)
            if callable(validator)
        )
        self.min_len = max(1, int(min_len))
        self.max_len = max(self.min_len, int(max_len))
        self.skip_numeric = bool(skip_numeric)
        self.skip_formula = bool(skip_formula)

    # ── 构造 ──
    @classmethod
    def from_spec(cls, spec: dict) -> "TableColumnDetector":
        if not isinstance(spec, dict):
            raise ValueError("table_column detect 需要 dict")
        headers = (spec.get("headers") or spec.get("header_keywords")
                   or spec.get("columns") or ())
        if isinstance(headers, dict):                 # 兼容 08b 的 columns: {列名: {...}}
            headers = list(headers.keys())
        if isinstance(headers, str):
            headers = [headers]
        if not headers:
            raise ValueError("table_column detect 缺少 headers")
        return cls(
            headers=headers,
            header_rows=int(spec.get("header_rows", 3)),
            confidence=float(spec.get("confidence", 0.90)),
            status=str(spec.get("status") or ""),
            value_pattern=str(spec.get("value_pattern") or ""),
            exclude_validate=spec.get("exclude_validate") or spec.get("unless_validate") or (),
            min_len=int(spec.get("min_len", 1)),
            max_len=int(spec.get("max_len", 200)),
            skip_numeric=bool(spec.get("skip_numeric", True)),
            skip_formula=bool(spec.get("skip_formula", True)),
        )

    # ── 单元格筛选 ──
    def _skip_block(self, block: Any) -> bool:
        attrs = getattr(block, "attrs", None) or {}
        if self.skip_formula and attrs.get("formula"):
            return True
        if self.skip_numeric and (attrs.get("numeric") or attrs.get("date")):
            return True
        return False

    def _value_ok(self, value: str) -> bool:
        """值形状守卫：value_pattern 对去空白值匹配；命中 exclude_validate 则跳过。"""
        collapsed = _HEADER_CLEAN.sub("", value)
        if not collapsed:
            return False
        if self.value_regex is not None and not self.value_regex.search(collapsed):
            return False
        for validator in self.exclude_validators:
            try:
                if validator(collapsed):
                    return False
            except Exception:              # 校验器异常不影响检测
                continue
        return True

    # ── 分组 ──
    @staticmethod
    def _groups(doc: Document) -> dict[tuple, list[tuple[int, int, Any]]]:
        """(page_index, 表格标识) -> [(row, col, block)]；只收有行列 ref 的文本块。"""
        groups: dict[tuple, list[tuple[int, int, Any]]] = {}
        for page in doc.pages:
            page_index = int(getattr(page, "index", 0))
            for block in page.blocks:
                if block.kind != "text" or not (block.text or "").strip():
                    continue
                ref = getattr(block, "ref", None) or {}
                if "sheet" in ref and "row" in ref and "col" in ref:
                    key = (page_index, "xlsx", str(ref.get("sheet")))
                    row_key, col_key = "row", "col"
                elif "t" in ref and "r" in ref and "c" in ref:
                    key = (page_index, "docx", int(ref.get("t") or 0))
                    row_key, col_key = "r", "c"
                else:
                    continue
                try:
                    row = int(ref.get(row_key))
                    col = int(ref.get(col_key))
                except (TypeError, ValueError):
                    continue
                if row < 0 or col < 0:
                    continue
                groups.setdefault(key, []).append((row, col, block))
        return groups

    # ── 检测入口 ──
    def detect(self, doc: Document, rule: Rule,
               ctx: DetectContext) -> list[Entity]:
        if getattr(doc, "fmt", "") not in ("xlsx", "xlsm", "docx"):
            return []
        entities: list[Entity] = []
        for _key, items in self._groups(doc).items():
            if not items:
                continue
            min_row = min(row for row, _col, _block in items)
            max_row = max(row for row, _col, _block in items)
            header_rows = set(range(min_row, min_row + self.header_rows))
            # 在前 N 行里选命中列头最多的一行（同分取最早）
            header_row = -1
            header_cols: dict[int, str] = {}
            best = 0
            for row in sorted(header_rows):
                if row > max_row:
                    break
                matched: dict[int, str] = {}
                for item_row, col, block in items:
                    if item_row != row:
                        continue
                    header = _norm_header(getattr(block, "text", ""))
                    if header in self.headers:
                        matched[col] = header
                if len(matched) > best:
                    best = len(matched)
                    header_row = row
                    header_cols = matched
            if best <= 0 or header_row >= max_row:
                continue
            # 数据行：同列、表头行以下；同值合并/重叠交给 pipeline
            for col, _header in header_cols.items():
                for row, item_col, block in items:
                    if item_col != col or row <= header_row:
                        continue
                    if self._skip_block(block):
                        continue
                    raw = str(getattr(block, "text", "") or "")
                    value = raw.strip()
                    if not value or _norm_header(value) in self.headers:
                        continue                # 空值/重复表头
                    if not (self.min_len <= len(value) <= self.max_len):
                        continue
                    if not self._value_ok(value):
                        continue
                    lead = len(raw) - len(raw.lstrip())
                    entity = Entity(
                        id=Entity.new_id(),
                        etype=rule.entity,
                        label=rule.label,
                        value=value,
                        spans=[Span(block_id=block.id,
                                    start=lead, end=lead + len(value))],
                        level=rule.level,
                        confidence=self.confidence,
                        origin="table",
                        status=self.status or "accepted",
                    )
                    entities.append(entity)
        return entities


register_detector(TableColumnDetector)

__all__ = ["TableColumnDetector"]
