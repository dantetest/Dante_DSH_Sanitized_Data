# -*- coding: utf-8 -*-
"""跨行拼接正则检测器（``joined_regex``）。

判决书/合同这类文本层 PDF，每一「视觉行」是一个 block；地址、案号、落款
经常被折成两三行（如「（2026）」+「川3423 民再1 号」）。逐 block 跑正则会
漏掉跨行的实体。本检测器把一个页面内相邻的文本块按阅读顺序拼起来跑正则，
再把命中区间映射回**原本的行 block**，因此导出涂黑仍按行定位、不会整段
误涂。

规则写法与 ``regex`` 一致，额外支持：

* ``pattern`` 里可用命名组 ``(?P<value>...)`` 指定真正要脱敏的片段；
* ``value_clean``：``collapse_ws`` 去掉命中值里的空白/换行；
* ``validate``：复用 ``regex`` 的校验器名（如身份证校验位）；
* ``max_blocks``：一个页面最多拼多少块（默认 12，防止超长页）。
"""
from __future__ import annotations

import re
from typing import Any

from ..registry import Detector, register_detector
from ..udm import DetectContext, Document, Entity, Rule, Span

try:  # 校验器与 regex 检测器共用同一个注册表
    from .regex_det import _VALIDATORS, _parse_flags
except Exception:  # pragma: no cover
    _VALIDATORS = {}
    _parse_flags = lambda _v: 0  # type: ignore[assignment]


class JoinedRegexDetector(Detector):
    """把同页相邻文本块拼起来跑正则，命中再映射回逐行 span。"""

    id = "joined_regex"

    def __init__(self, pattern: str, validate: str = "none", flags: int = 0,
                 confidence: float = 0.97, value_clean: str = "",
                 max_blocks: int = 12) -> None:
        if not pattern:
            raise ValueError("JoinedRegexDetector 需要非空 pattern")
        self.pattern = str(pattern)
        self.regex = re.compile(self.pattern, flags)
        self.validate_name = str(validate or "none").strip().lower()
        self.validator = _VALIDATORS.get(self.validate_name)
        self.confidence = float(confidence)
        self.value_clean = str(value_clean or "").strip().lower()
        self.max_blocks = max(2, int(max_blocks))

    @classmethod
    def from_spec(cls, spec: dict) -> "JoinedRegexDetector":
        if not isinstance(spec, dict):
            raise ValueError("joined_regex detect 需要 dict")
        pattern = spec.get("pattern") or spec.get("regex")
        if not pattern:
            raise ValueError("joined_regex detect 缺少 pattern")
        return cls(
            pattern=str(pattern),
            validate=str(spec.get("validate") or "none"),
            flags=_parse_flags(spec.get("flags")),
            confidence=float(spec.get("confidence", 0.97)),
            value_clean=str(spec.get("value_clean") or ""),
            max_blocks=int(spec.get("max_blocks", 12)),
        )

    def _clean(self, value: str) -> str:
        if self.value_clean == "collapse_ws":
            return re.sub(r"\s+", "", value)
        if self.value_clean == "strip":
            return value.strip()
        return value.strip()

    def detect(self, doc: Document, rule: Rule,
               ctx: DetectContext) -> list[Entity]:
        merged: dict[str, Entity] = {}
        for page in doc.pages:
            blocks = [b for b in page.blocks
                      if b.kind == "text" and (b.text or "").strip() and b.bbox]
            if not blocks:
                continue
            blocks.sort(key=lambda b: (round(float(b.bbox[1]), 1),
                                       float(b.bbox[0])))
            # 拼接全页文本，同时记录每块在拼接串里的区间
            pieces: list[str] = []
            maps: list[tuple[int, int, Any]] = []
            pos = 0
            for block in blocks:
                text = block.text
                maps.append((pos, pos + len(text), block))
                pieces.append(text)
                pos += len(text)
            joined = "".join(pieces)
            if not joined:
                continue
            for match in self.regex.finditer(joined):
                groups = match.groupdict()
                if "value" in groups and groups["value"] is not None:
                    value = match.group("value")
                    value_start, value_end = match.span("value")
                else:
                    value = match.group(0)
                    value_start, value_end = match.span(0)
                cleaned = self._clean(value)
                if not cleaned:
                    continue
                if self.validator is not None and not self.validator(cleaned):
                    continue
                spans: list[Span] = []
                for part_start, part_end, block in maps:
                    if part_end <= value_start:
                        continue
                    if part_start >= value_end:
                        break
                    rel_start = max(part_start, value_start) - part_start
                    rel_end = min(part_end, value_end) - part_start
                    if rel_end <= rel_start:
                        continue
                    spans.append(Span(block_id=block.id,
                                      start=rel_start, end=rel_end))
                if not spans:
                    continue
                entity = merged.get(cleaned)
                if entity is None:
                    entity = Entity(
                        id=Entity.new_id(),
                        etype=rule.entity,
                        label=rule.label,
                        value=cleaned,
                        spans=[],
                        level=rule.level,
                        confidence=self.confidence,
                        origin="regex",
                        status="accepted",
                    )
                    merged[cleaned] = entity
                existing = {(s.block_id, s.start, s.end) for s in entity.spans}
                for span in spans:
                    key = (span.block_id, span.start, span.end)
                    if key not in existing:
                        entity.spans.append(span)
                        existing.add(key)
                entity.level = (rule.level
                                if _level_rank(rule.level) > _level_rank(entity.level)
                                else entity.level)
        return list(merged.values())


def _level_rank(level: str) -> int:
    return {"L0": 0, "L1": 1, "L2": 2, "L3": 3}.get(str(level).upper(), 2)


register_detector(JoinedRegexDetector)
