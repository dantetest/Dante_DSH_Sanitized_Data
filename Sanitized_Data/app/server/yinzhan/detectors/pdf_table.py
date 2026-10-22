# -*- coding: utf-8 -*-
"""扫描件 PDF 表格列检测器（``pdf_table_column``，Batch2-B3）。

背景与设计
=========
扫描件 PDF 没有行/列结构：``pdf_parser`` 的 OCR 把表格拆成一个个独立
``Block``（表头「姓名」「证书号」各一块，数据格「林力成」「G3300191668」
各一块），``table_column`` 只认 ``ref={sheet,row,col}`` 的 xlsx/docx，PDF
自然不触发。QA 实测 doc10 第 14 页「人员配备表」因此漏了 9 个姓名和整列
证书号。

本检测器**只读几何**，零解析器改动：

1. 在每页找表头 block：``block.text`` 做 NFKC + 去全部空白 + 去末尾冒号后，
   **精确命中**受控表头词（``headers``，避免「产品名称」被当人名）；
2. 表头下方页内 block：中心 x 落在表头 x 范围 ±容差内（容差 =
   ``max(x_tolerance_pt, 表头宽 × x_tolerance_ratio)``，默认 ±8pt / 40%，
   上限 ``max_tolerance_pt``），且 block 顶边在表头底边以下；
3. 按列语义做值校验（``value_kind``）：

   * ``person``：2–4 汉字（可含 ``·``），首字必须是姓氏（复用
     ``pipeline._SURNAMES``，取不到时用内置常见姓氏兜底）；
   * ``cert``：``[A-Za-z0-9\\-]{6,24}``（职称证/执业证/证书号）；
   * ``id_card``：15/18 位身份证形状（不强制校验位，扫描 OCR 可能错一位，
     但列语义已经足够强）；
   * ``phone``：``1[3-9]\\d{9}``；
   * ``account``：``[A-Za-z0-9\\-]{6,40}``；
   * ``org``：含公司/院/中心/局/所… 等机构后缀；
   * ``generic``：只做长度/可选 ``value_pattern`` 守卫。

4. 同一表头下**至少 ``min_values``（默认 2）个有效值、且至少 2 个不同
   行**才成列（单值列、表头词本身、正文段落都不触发）；同一值多行合并成
   一个 Entity 的多个 span；同一 ``block_id+start/end`` 只出一次；
5. 生成 Entity：``etype=rule.entity``、``label=rule.label``、``level``、
   ``confidence``（默认 0.85）、``origin="table"``、``status``（可配
   pending）。跨规则的同位置去重交给 ``pipeline._merge``（本检测器比既有
   规则 priority 低，普通规则先占位时本检测器的重复 span 会被裁掉）。

规则写法（``rules/table-cn.yaml``）::

    - id: table.pdf_person
      entity: PERSON
      label: 表格姓名列
      level: L2
      scope: [pdf]
      priority: 14
      detect:
        - type: pdf_table_column
          value_kind: person
          headers: [姓名, 名字, 员工姓名, 法定代表人]
          x_tolerance_ratio: 0.4
          x_tolerance_pt: 8
          min_values: 2
          confidence: 0.85
      replace: {strategy: surrogate}

防误报：无表头不触发；单值列不触发；值形状不符（正文整行、页脚
「第 13 页 共 16 页」、序号 1…16）不触发；候选 block 宽度超过表头宽度
``max_width_ratio`` 倍（默认 8）不触发，避免文本层 PDF 的整行段落被当成
单元格。

注册：``registry.load_plugins()`` 扫描 detectors 目录自动导入；打包兜底
清单 ``registry._PLUGIN_FALLBACK["detectors"]`` 已含 ``pdf_table``。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from ..registry import Detector, register_detector
from ..udm import DetectContext, Document, Entity, Rule, Span

#: 表头归一化：NFKC 后去掉全部空白（含全角空格）与末尾冒号，ASCII 大小写归一
_WS_RE = re.compile(r"[\s\u3000]+")

#: 证书号/执业证号：字母数字与短横线，6–24 位（需求口径）
_CERT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{5,23}$")
#: 身份证：15 位或 18 位（末位 X）。不强制校验位，扫描 OCR 错一位也应按列脱敏。
_ID_RE = re.compile(r"^(?:\d{15}|\d{17}[\dXx])$")
#: 手机号：1[3-9] 开头 11 位
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
#: 账号：字母数字短横线
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{5,39}$")
#: 姓名：2–4 个汉字，允许少数民族姓名的间隔号
_NAME_RE = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$")
#: 机构名：允许括号/间隔号
_ORG_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9（）()·&\-]{4,60}$")

#: 机构名后缀（与 pipeline.ORG_SUFFIXES 保持同一口径的常用子集）
_ORG_SUFFIXES = (
    "股份有限公司", "有限责任公司", "集团有限公司", "有限公司", "分公司",
    "律师事务所", "会计师事务所", "事务所", "人民法院", "法院",
    "人民检察院", "检察院", "人民医院", "医院", "研究院", "设计院",
    "学院", "大学", "学校", "银行", "支行", "分行", "集团", "公司",
    "中心", "委员会", "管理局", "政府", "合作社", "工作室", "厂", "局",
    "所", "院", "站", "馆", "酒店", "宾馆", "商场", "超市", "店",
)

#: 取不到 pipeline._SURNAMES 时的兜底（最常见姓氏，够压掉正文误报）
_SURNAME_FALLBACK = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟"
    "平黄和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项"
    "祝董梁杜阮蓝闵席季麻强贾路娄江童颜郭梅盛林刁钟徐邱骆高夏蔡田"
    "樊胡凌霍虞万支柯管卢莫房裘缪解应宗丁宣邓郁单杭洪包诸左石崔吉"
    "龚程邢裴陆荣翁荀羊惠甄曲家封芮储靳段富巫乌焦巴弓牧山谷车侯全"
    "班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束龙叶幸司韶黎薄印宿白"
    "怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳姬申扶"
    "堵冉宰郦雍桑桂牛寿通边扈燕冀浦尚农温别庄晏柴瞿阎充慕连茹习宦"
    "艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃"
    "利蔚越夔隆师巩聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关"
    "蒯相查后荆红游竺权逯盖益桓公"
)

#: 常见表头词。用途：重复表头行/多级表头里「姓名」这类词本身不能当数据脱敏。
_HEADER_WORDS = frozenset(
    "姓名 名字 员工姓名 客户名称 申请人 参保人 患者姓名 学生姓名 开户人姓名 "
    "法定代表人 联系人 联系人姓名 经办人 负责人 项目负责人 证书号 证书编号 "
    "证书号码 执业证号 注册证号 资格证号 证件号 证件号码 身份证 身份证号 "
    "身份证号码 身份证件号 居民身份证号 护照号 手机号 手机号码 手机 联系电话 "
    "电话 联系方式 电话号码 账号 帐号 账户 账户号 客户号 客户编号 单位 单位名称 "
    "公司 公司名称 机构名称 工作单位 用人单位 序号 职称 级别 专业 职务 备注"
    .split()
)


def _norm_header(text: Any) -> str:
    """表头/候选值归一：NFKC + 去空白 + 去末尾冒号 + 小写。"""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = _WS_RE.sub("", value).strip().rstrip("：:")
    return value.lower()


def _surnames() -> frozenset[str]:
    """复用 pipeline 的姓氏表；导入失败时用内置兜底（检测器不能因此不可用）。"""
    try:
        from ..pipeline import _SURNAMES     # 延迟导入：避免插件加载期循环依赖

        if _SURNAMES:
            return frozenset(_SURNAMES)
    except Exception:
        pass
    return _SURNAME_FALLBACK


class PdfTableColumnDetector(Detector):
    """按「表头 + 几何列对齐 + 值形状」在扫描 PDF 上识别整列敏感值。"""

    id = "pdf_table_column"

    def __init__(
        self,
        headers: Iterable[str],
        value_kind: str = "generic",
        x_tolerance_ratio: float = 0.4,
        x_tolerance_pt: float = 8.0,
        max_tolerance_pt: float = 60.0,
        min_values: int = 2,
        min_rows: int = 2,
        confidence: float = 0.85,
        status: str = "",
        value_pattern: str = "",
        min_len: int = 0,
        max_len: int = 0,
        exclude_validate: Iterable[str] | str | None = None,
        max_width_ratio: float = 8.0,
        y_overlap_pt: float = 2.0,
    ) -> None:
        cleaned: list[str] = []
        for raw in headers or ():
            text = _norm_header(raw)
            if text and text not in cleaned:
                cleaned.append(text)
        if not cleaned:
            raise ValueError("pdf_table_column detect 需要非空 headers")
        self.headers: tuple[str, ...] = tuple(cleaned)
        self.header_set = frozenset(cleaned)
        kind = str(value_kind or "generic").strip().lower()
        if kind in ("name", "person_name", "人名", "姓名"):
            kind = "person"
        elif kind in ("license", "certificate", "cert_no", "证书号"):
            kind = "cert"
        elif kind in ("idcard", "id", "身份证"):
            kind = "id_card"
        elif kind in ("mobile", "tel", "手机号"):
            kind = "phone"
        elif kind in ("org", "organization", "org_name", "单位名称"):
            kind = "org"
        elif kind in ("acct",):
            kind = "account"
        if kind not in ("person", "cert", "id_card", "phone", "account", "org", "generic"):
            raise ValueError(f"pdf_table_column 未知 value_kind: {value_kind!r}")
        self.value_kind = kind
        self.x_tolerance_ratio = max(0.0, float(x_tolerance_ratio))
        self.x_tolerance_pt = max(0.0, float(x_tolerance_pt))
        self.max_tolerance_pt = max(self.x_tolerance_pt, float(max_tolerance_pt))
        self.min_values = max(2, int(min_values))
        self.min_rows = max(2, int(min_rows))
        self.confidence = float(confidence)
        self.status = status if status in ("pending", "accepted") else ""
        self.value_pattern_text = str(value_pattern or "")
        self.value_regex = re.compile(self.value_pattern_text) if self.value_pattern_text else None
        self.min_len = max(0, int(min_len))
        self.max_len = max(0, int(max_len))
        if isinstance(exclude_validate, str):
            exclude_validate = [exclude_validate]
        names = [str(name).strip() for name in (exclude_validate or ()) if str(name).strip()]
        try:      # 复用 regex 检测器的校验器；导入失败不影响几何检测
            from .regex_det import _VALIDATORS

            self.exclude_validators = tuple(
                _VALIDATORS[name] for name in names if name in _VALIDATORS
            )
        except Exception:
            self.exclude_validators = ()
        self.max_width_ratio = max(1.0, float(max_width_ratio))
        self.y_overlap_pt = max(0.0, float(y_overlap_pt))
        self._surnames = _surnames()

    # ── 构造 ──
    @classmethod
    def from_spec(cls, spec: dict) -> "PdfTableColumnDetector":
        if not isinstance(spec, dict):
            raise ValueError("pdf_table_column detect 需要 dict")
        headers = (spec.get("headers") or spec.get("header_keywords")
                   or spec.get("columns") or ())
        if isinstance(headers, dict):                 # 兼容 columns: {列名: {...}}
            headers = list(headers.keys())
        if isinstance(headers, str):
            headers = [headers]
        if not headers:
            raise ValueError("pdf_table_column detect 缺少 headers")
        return cls(
            headers=headers,
            value_kind=str(spec.get("value_kind") or spec.get("value_type") or "generic"),
            x_tolerance_ratio=float(spec.get("x_tolerance_ratio", 0.4)),
            x_tolerance_pt=float(spec.get("x_tolerance_pt", 8.0)),
            max_tolerance_pt=float(spec.get("max_tolerance_pt", 60.0)),
            min_values=int(spec.get("min_values", 2)),
            min_rows=int(spec.get("min_rows", 2)),
            confidence=float(spec.get("confidence", 0.85)),
            status=str(spec.get("status") or ""),
            value_pattern=str(spec.get("value_pattern") or ""),
            min_len=int(spec.get("min_len", 0)),
            max_len=int(spec.get("max_len", 0)),
            exclude_validate=spec.get("exclude_validate") or spec.get("unless_validate") or (),
            max_width_ratio=float(spec.get("max_width_ratio", 8.0)),
            y_overlap_pt=float(spec.get("y_overlap_pt", 2.0)),
        )

    # ── 值校验 ──
    def _value_ok(self, value: str) -> bool:
        """按列语义校验单元格值；同时挡掉表头词、正文/页脚等误报。"""
        value = (value or "").strip()
        if not value:
            return False
        norm = _norm_header(value)
        if not norm:
            return False
        # 表头词（含重复表头行）本身不是数据
        if norm in self.header_set or norm in _HEADER_WORDS:
            return False
        if self.value_regex is not None and not self.value_regex.search(value):
            return False
        if self.min_len and len(value) < self.min_len:
            return False
        if self.max_len and len(value) > self.max_len:
            return False
        for validator in self.exclude_validators:
            try:
                if validator(value):
                    return False
            except Exception:
                continue
        kind = self.value_kind
        if kind == "generic":
            return True
        if kind == "person":
            if not _NAME_RE.fullmatch(value):
                return False
            return value[0] in self._surnames
        if kind == "cert":
            return bool(_CERT_RE.fullmatch(value))
        if kind == "id_card":
            return bool(_ID_RE.fullmatch(value))
        if kind == "phone":
            return bool(_PHONE_RE.fullmatch(value))
        if kind == "account":
            return (bool(_ACCOUNT_RE.fullmatch(value))
                    and sum(ch.isdigit() for ch in value) >= 6)
        if kind == "org":
            return (bool(_ORG_RE.fullmatch(value))
                    and value.endswith(_ORG_SUFFIXES))
        return False

    # ── 几何 ──
    @staticmethod
    def _center_y(bbox: tuple[float, float, float, float]) -> float:
        return (float(bbox[1]) + float(bbox[3])) / 2.0

    @staticmethod
    def _center_x(bbox: tuple[float, float, float, float]) -> float:
        return (float(bbox[0]) + float(bbox[2])) / 2.0

    # ── 检测入口 ──
    def detect(self, doc: Document, rule: Rule,
               ctx: DetectContext) -> list[Entity]:
        # 宿主若在 ctx 里带了既有实体，先占位（当前 pipeline 不传，留作扩展）；
        # 跨规则重叠最终仍由 pipeline._merge 统一裁剪。
        taken: set[tuple[str, int, int]] = set()
        for existing in (getattr(ctx, "entities", None) or ()):
            for span in getattr(existing, "spans", ()) or ():
                if span.block_id is not None and span.start >= 0:
                    taken.add((span.block_id, span.start, span.end))
        by_value: dict[str, Entity] = {}
        span_keys: set[tuple[str, int, int]] = set()

        for page in doc.pages:
            blocks = [
                block for block in page.blocks
                if block.kind == "text" and block.bbox and (block.text or "").strip()
            ]
            if len(blocks) < 3:
                continue
            headers = [
                block for block in blocks
                if _norm_header(block.text) in self.header_set
            ]
            if not headers:
                continue
            for header in headers:
                hx0, hy0, hx1, hy1 = (float(v) for v in header.bbox)
                if hx1 <= hx0 or hy1 <= hy0:
                    continue
                header_width = hx1 - hx0
                tol = max(self.x_tolerance_pt, header_width * self.x_tolerance_ratio)
                tol = min(tol, self.max_tolerance_pt)
                max_value_width = header_width * self.max_width_ratio
                candidates: list[tuple[Any, int, int, str]] = []
                for block in blocks:
                    if block is header:
                        continue
                    bx0, by0, bx1, by1 = (float(v) for v in block.bbox)
                    if bx1 <= bx0 or by1 <= by0:
                        continue
                    # 必须在表头下方（允许 2pt 的 OCR 框重叠误差）
                    if by0 < hy1 - self.y_overlap_pt:
                        continue
                    # 列中心对齐
                    if not (hx0 - tol <= self._center_x(block.bbox) <= hx1 + tol):
                        continue
                    # 单元格不会比表头宽太多：挡掉文本层 PDF 的整行段落
                    if (bx1 - bx0) > max(max_value_width, 4.0 * header_width + 24.0):
                        continue
                    raw = block.text or ""
                    value = raw.strip()
                    if not self._value_ok(value):
                        continue
                    lead = len(raw) - len(raw.lstrip())
                    candidates.append((block, lead, lead + len(value), value))
                # 单值列不成列；同一行重复也不算列
                if len(candidates) < self.min_values:
                    continue
                rows = {
                    round(self._center_y(block.bbox))
                    for block, _start, _end, _value in candidates
                }
                if len(rows) < self.min_rows:
                    continue
                for block, start, end, value in candidates:
                    key = (block.id, start, end)
                    if key in span_keys or key in taken:
                        continue
                    if any(bid == key[0] and start < en and s < end
                           for bid, s, en in span_keys):
                        continue
                    span_keys.add(key)
                    entity = by_value.get(value)
                    if entity is None:
                        entity = Entity(
                            id=Entity.new_id(),
                            etype=rule.entity,
                            label=rule.label,
                            value=value,
                            spans=[],
                            level=rule.level,
                            confidence=self.confidence,
                            origin="table",
                            status=self.status or "accepted",
                        )
                        by_value[value] = entity
                    entity.spans.append(Span(block_id=block.id, start=start, end=end))
        return list(by_value.values())


register_detector(PdfTableColumnDetector)

__all__ = ["PdfTableColumnDetector"]
