# -*- coding: utf-8 -*-
"""表头列级检测器（``table_column``，P0/P1）。

背景与设计
==========
表格是财务/人事/客户名单的主载体，现有正则/NER 只逐块检测单元格：列里
"缺位身份证""带空格手机号""没有公司后缀的单位名"等值会漏检。本检测器按
**表头语义**整列处理，零解析器改动——只读解析器已给出的 ``Block.ref``：

* XLSX：``ref={"sheet", "cell", "row", "col"}``（一个非空单元格一个 text Block）；
* DOCX：``ref={"t", "r", "c"}``（表格第 t 张、第 r 行、第 c 列）；
* CSV/TSV（P1）：``ref={"row", "col", "sep", "line"}``（text_parser 用 csv 模块
  解析，document.fmt 仍为 ``"text"``，靠 ``doc.meta["delimited"]`` 标识）。

P1 起支持**多级/合并表头继承**（08b E6）：
* 前 ``header_rows``（默认 3）行里选"命中列头最多的一行"，同分取更深的叶行；
* 每列列语义按 "本格 → 向上继承（父级表头同列） → 向左继承（同一行合并跨列，
  越过空白、遇下一个非空表头即停，跨度受 ``max_inherit_span`` 限制）" 解析；
  不要求解析器提供真实 merged ranges，xlsx 合并区只剩左上角值、docx 合并去重、
  CSV 合并表头只写首列等场景都可用；
* 无表头（前 N 行没有任何 headers 命中）→ 返回空，TXT/MD 等无行列 ref 的块也
  自然不触发，保证不误报。

规则写法（``rules/table-cn.yaml``，每条规则 = 一类列语义）::

    - id: table.person
      entity: PERSON
      label: 表格姓名列
      level: L2
      scope: [xlsx, docx, text]     # text 由 Lead 统一加（CSV/TSV 的 doc.fmt=text）
      priority: 14
      detect:
        - type: table_column
          headers: [姓名, 名字, 联系人, 客户名称, 员工姓名]
          header_rows: 3            # 前 1–3 行找表头（默认 3）
          confidence: 0.90
          status: accepted          # 可设 pending（高风险列人工确认）
          inherit_merged: true      # 多级/合并表头继承（P1，默认开）
          max_inherit_span: 8       # 向左合并继承最多跨几列
          value_pattern: '^[\u4e00-\u9fa5·]{2,20}$'   # 可选：值形状守卫
          exclude_validate: [uscc_checksum]           # 可选：命中即跳过（让位更权威规则）
          min_len: 2
          max_len: 40
      replace: {strategy: surrogate}

检测流程
========
1. 按 ``(页面, 表格标识)`` 分组：xlsx 用 ``sheet``，docx 用 ``t``，CSV/TSV 一组；
2. 在前 ``header_rows`` 行内选表头行（``headers`` 精确匹配，仅忽略空白/大小写/
   末尾冒号，"名称"这类泛词不会因包含关系误认）；
3. 解析每列列语义（含向上/向左继承），对表头行以下、同列数据单元格整格生成
   ``Entity``（span 覆盖 trim 后文本）；
4. 跳过：空单元格、公式/数值/日期单元格（交给 ``numeric_cell``）、重复表头文本；
5. 可选 ``value_pattern`` 值形状守卫与 ``exclude_validate`` 让位；可选 ``status``；
6. 同值合并/重叠裁剪交给 ``pipeline._merge``；本检测器不直接处理导出。

边界（P1）
==========
* 同一 sheet/文件内上下堆叠多张表时只按最前面的表头行处理（P1 之后再做多表切分）；
* 数值型手机号/身份证（Excel 里存成数字）仍由 ``numeric_cell`` 的 P1 扩展负责；
* 合并单元格的"跨行纵向合并数据格"只取左上角 block（解析器既定行为）。

注册：``registry.load_plugins()`` 扫描 detectors 目录自动导入；打包兜底清单
``registry._PLUGIN_FALLBACK`` 已含 ``table_det``。
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

#: 常见列头词表（归一化后）。用途：多级表头里"父级匹配、叶行是其它语义列头"
#: 时，避免把叶行的列头文字（如"手机号/邮箱"）当成数据实体生成。值恰好等于
#: 这些词的真实数据极少，跳过可接受；新增列头词时同步扩充（见 docs/impl/csv-table.md）。
_HEADER_VOCAB = frozenset(_HEADER_CLEAN.sub("", word).rstrip("：:").lower() for word in (
    "姓名", "名字", "联系人", "联系人姓名", "客户名称", "员工姓名", "申请人",
    "参保人", "患者姓名", "学生姓名", "开户人姓名", "法定代表人", "经办人",
    "身份证号", "身份证号码", "身份证", "证件号", "证件号码", "身份证件号",
    "居民身份证号", "护照号", "手机号", "手机号码", "手机", "联系电话", "电话",
    "联系方式", "电话号码", "办公电话", "家庭电话", "地址", "住址", "联系地址",
    "家庭住址", "通讯地址", "户籍地址", "详细地址", "邮箱", "电子邮箱",
    "电子邮件", "邮件地址", "email", "e-mail", "单位", "工作单位", "公司",
    "公司名称", "单位名称", "机构名称", "用人单位", "银行卡号", "银行卡", "卡号",
    "银行账号", "银行帐号", "账号", "帐号", "账户", "账户号", "客户号",
    "客户编号", "金额", "工资", "应发工资", "实发工资", "余额", "合计", "小计",
    "收入", "费用", "成本", "报销金额", "借款金额", "订单号", "订单编号", "票号",
    "交易流水号", "流水号", "凭证号", "合同号",
    # 组合表头/常见非敏感列（用于避免被误当数据）
    "个人信息", "基本信息", "客户信息", "联系方式", "备注", "序号", "日期",
    "时间", "数量", "单价", "总计",
))


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
        inherit_merged: bool = True,
        max_inherit_span: int = 8,
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
        self.inherit_merged = bool(inherit_merged)
        self.max_inherit_span = max(1, int(max_inherit_span))

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
            inherit_merged=bool(spec.get("inherit_merged", True)),
            max_inherit_span=int(spec.get("max_inherit_span", 8)),
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
        meta = getattr(doc, "meta", None) or {}
        fmt = str(getattr(doc, "fmt", "") or "")
        delimited = str(meta.get("delimited") or "").strip().lower()
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
                elif ("row" in ref and "col" in ref
                      and ("sep" in ref or delimited in ("csv", "tsv")
                           or fmt in ("csv", "tsv"))):
                    # CSV/TSV：text_parser 给单元格 ref={row,col,sep,line}
                    key = (page_index, "delimited", delimited or fmt)
                    row_key, col_key = "row", "col"
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

    # ── 表头识别（多级/合并继承） ──
    @staticmethod
    def _grid(
        items: list[tuple[int, int, Any]],
    ) -> tuple[list[int], list[int], dict[tuple[int, int], str]]:
        rows = sorted({row for row, _col, _block in items})
        cols = sorted({col for _row, col, _block in items})
        cell: dict[tuple[int, int], str] = {}
        for row, col, block in items:
            cell[(row, col)] = str(getattr(block, "text", "") or "")
        return rows, cols, cell

    def _header_block(self, rows: list[int], cols: list[int],
                      cell: dict[tuple[int, int], str]) -> list[int]:
        """识别表头块（前 header_rows 行里的连续表头行）。

        - 从第一个"命中至少一个列头"的行开始；
        - 仅当下一行连续且带来**新的列**命中时才并入（两级表头）；
        - 数据行里偶然出现一个表头词不会把该行误当表头（不扩展）。
        """
        block: list[int] = []
        covered: set[int] = set()
        for row in rows[: self.header_rows]:
            matched = {col for col in cols
                       if _norm_header(cell.get((row, col), "")) in self.headers}
            if not block:
                if not matched:
                    continue
                block.append(row)
                covered = set(matched)
                continue
            if row == block[-1] + 1 and (matched - covered):
                block.append(row)
                covered |= matched
            else:
                break
        return block

    def _resolve_header(self, header_block: list[int], col: int,
                        cell: dict[tuple[int, int], str]) -> str:
        """解析某列列语义：表头块内自深向浅 → 本格命中 → 向左继承合并跨列。"""
        for row in reversed(header_block):
            own = _norm_header(cell.get((row, col), ""))
            if own:
                return own if own in self.headers else ""
            # 合并表头：本行向左找最近非空（越过空白，遇非空即停）
            if not self.inherit_merged:
                continue
            gap = 0
            for left in range(col - 1, -1, -1):
                left_text = _norm_header(cell.get((row, left), ""))
                if not left_text:
                    gap += 1
                    if gap > self.max_inherit_span:
                        break
                    continue
                if left_text in self.headers:
                    return left_text
                break
        return ""

    # ── 检测入口 ──
    def detect(self, doc: Document, rule: Rule,
               ctx: DetectContext) -> list[Entity]:
        fmt = str(getattr(doc, "fmt", "") or "")
        meta = getattr(doc, "meta", None) or {}
        delimited = str(meta.get("delimited") or "").strip().lower()
        if fmt not in ("xlsx", "xlsm", "docx", "csv", "tsv") and delimited not in ("csv", "tsv"):
            return []
        entities: list[Entity] = []
        for _key, items in self._groups(doc).items():
            if not items:
                continue
            rows, cols, cell = self._grid(items)
            header_block = self._header_block(rows, cols, cell)
            if not header_block:
                continue                     # 无表头 → 不误报（退化为既有规则）
            header_row = header_block[-1]
            if header_row >= rows[-1]:
                continue                     # 表头下面没有数据行
            # 每列列语义（含多级/合并表头继承）
            col_headers: dict[int, str] = {}
            for col in cols:
                header = self._resolve_header(header_block, col, cell)
                if header:
                    col_headers[col] = header
            if not col_headers:
                continue
            block_of = {(row, col): block for row, col, block in items}
            for row in rows:
                if row <= header_row:
                    continue
                for col, _header in col_headers.items():
                    block = block_of.get((row, col))
                    if block is None:
                        continue
                    if self._skip_block(block):
                        continue
                    raw = str(getattr(block, "text", "") or "")
                    value = raw.strip()
                    if not value:
                        continue
                    value_header = _norm_header(value)
                    if value_header in self.headers or value_header in _HEADER_VOCAB:
                        continue                # 空值/重复表头/表头词（多级表头叶行）
                    if not (self.min_len <= len(value) <= self.max_len):
                        continue
                    if not self._value_ok(value):
                        continue
                    lead = len(raw) - len(raw.lstrip())
                    entities.append(Entity(
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
                    ))
        return entities


register_detector(TableColumnDetector)

__all__ = ["TableColumnDetector"]
