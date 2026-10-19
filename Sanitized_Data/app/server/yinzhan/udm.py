"""数据隐站 · 统一文档模型 (UDM)

所有格式先归一化成这里的结构，管线只认 UDM。
解析器负责「文件 -> UDM」，导出器负责「UDM + 决策 -> 文件」。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

Box = tuple[float, float, float, float]  # x0, y0, x1, y1


# ────────────────────────────── 几何 ──────────────────────────────

def union_boxes(boxes: list[Box]) -> Box | None:
    if not boxes:
        return None
    xs0 = min(b[0] for b in boxes)
    ys0 = min(b[1] for b in boxes)
    xs1 = max(b[2] for b in boxes)
    ys1 = max(b[3] for b in boxes)
    return (xs0, ys0, xs1, ys1)


def box_intersects(a: Box, b: Box) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def box_area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


# ────────────────────────────── 文档结构 ──────────────────────────────

@dataclass
class Cell:
    text: str
    row: int
    col: int

    def to_dict(self) -> dict:
        return {"text": self.text, "row": self.row, "col": self.col}


@dataclass
class Block:
    id: str
    kind: Literal["text", "table", "image"]
    text: str = ""
    bbox: Box | None = None
    cells: list[Cell] = field(default_factory=list)
    ref: dict = field(default_factory=dict)
    page: int = 0
    attrs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "bbox": list(self.bbox) if self.bbox else None,
            "cells": [c.to_dict() for c in self.cells],
            "page": self.page,
            "attrs": self.attrs,
        }


@dataclass
class Page:
    index: int
    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)
    ref: dict = field(default_factory=dict)


@dataclass
class Document:
    doc_id: str
    path: str
    fmt: str                      # pdf | docx | xlsx | image | tiff | mesh | text
    pages: list[Page] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    visual_marks: list["VisualMark"] = field(default_factory=list)

    def all_blocks(self):
        for page in self.pages:
            for block in page.blocks:
                yield page, block

    def text_blocks(self):
        for page, block in self.all_blocks():
            if block.kind == "text" and block.text:
                yield page, block


@dataclass
class VisualMark:
    """OCR 读不出来的东西：红色印章、手写签名。

    这类东西没有文字可供规则匹配，只能靠图像特征找出来。
    rect 用页面坐标系（PDF 是 pt，图片是 px）。
    """
    kind: str                      # seal | signature
    page: int
    rect: Box
    confidence: float = 0.9
    note: str = ""


# ────────────────────────────── 实体与决策 ──────────────────────────────

@dataclass
class Span:
    block_id: str | None = None
    start: int = -1
    end: int = -1
    rect: Box | None = None
    page: int | None = None       # 矩形类 span 必填；文本 span 可空

    def to_dict(self) -> dict:
        d: dict[str, Any] = {}
        if self.block_id is not None:
            d["block_id"] = self.block_id
        if self.start >= 0:
            d["start"] = self.start
            d["end"] = self.end
        if self.rect:
            d["rect"] = list(self.rect)
        if self.page is not None:
            d["page"] = self.page
        return d

    @property
    def is_text(self) -> bool:
        return self.block_id is not None and self.start >= 0


@dataclass
class Decision:
    strategy: str = "black"       # surrogate | mask | black | drop | keep
    replacement: str | None = None
    black: bool = False

    def to_dict(self) -> dict:
        return {"strategy": self.strategy, "replacement": self.replacement, "black": self.black}


@dataclass
class Entity:
    id: str
    etype: str
    label: str
    value: str
    spans: list[Span]
    level: str = "L2"
    confidence: float = 1.0
    origin: str = "regex"
    status: str = "accepted"
    decision: Decision | None = None
    box: Box | None = None        # 由管线按 spans 算好，方便前端

    @staticmethod
    def new_id() -> str:
        return "e_" + uuid.uuid4().hex[:8]

    def to_dict(self, surrogate: str | None = None) -> dict:
        return {
            "id": self.id,
            "type": self.etype,
            "label": self.label,
            "value": self.value,
            "surrogate": surrogate if surrogate is not None
                          else (self.decision.replacement if self.decision else None),
            "level": self.level,
            "confidence": round(self.confidence, 4),
            "origin": self.origin,
            "status": self.status,
            "spans": [s.to_dict() for s in self.spans],
        }


# ────────────────────────────── 规则 ──────────────────────────────

@dataclass
class Rule:
    id: str
    entity: str
    label: str
    level: str
    scope: list[str]
    detect: list[dict]
    replace: dict
    pack: str = ""
    priority: int = 0
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id, "entity": self.entity, "label": self.label,
            "level": self.level, "scope": self.scope, "pack": self.pack,
        }


# ────────────────────────────── 上下文 ──────────────────────────────

@dataclass
class ParseContext:
    ocr: Any = None               # OcrService | None
    workdir: str = "."
    options: dict = field(default_factory=dict)
    progress: Any = None          # callable(done:int, total:int, note:str)
    should_stop: Any = None       # callable() -> bool

    def report(self, done: int, total: int, note: str = "") -> None:
        """解析器在处理过程中上报进度。解析器可以完全不调用，不强制。"""
        if callable(self.progress):
            try:
                self.progress(done, total, note)
            except Exception:
                pass

    def stopped(self) -> bool:
        """用户点了「停止」就返回 True，解析器应在页边界检查并尽快退出。"""
        try:
            return bool(self.should_stop and self.should_stop())
        except Exception:
            return False

    def check_stop(self) -> None:
        """在耗时循环里调用；被取消就抛 ParseError 让上层干净收尾。"""
        if self.stopped():
            raise ParseError("已取消")


@dataclass
class DetectContext:
    strictness: str = "standard"
    ocr: Any = None
    logger: Any = None


@dataclass
class ReplaceContext:
    session: Any = None           # 会话级实体图
    etype: str = ""
    label: str = ""
    seed: int = 0


@dataclass
class ExportResult:
    path: str
    warnings: list[str] = field(default_factory=list)
    size: int = 0

    def to_dict(self) -> dict:
        return {"out": self.path, "warnings": self.warnings, "bytes": self.size}


# ────────────────────────────── 错误 ──────────────────────────────

class YinzhanError(Exception):
    code = "internal"


class ParseError(YinzhanError):
    code = "parse_failed"


class ExportError(YinzhanError):
    code = "export_failed"


class VerifyError(YinzhanError):
    code = "verify_failed"
