"""插件注册表与协议。

解析器、检测器、替换器都通过这里注册；管线只面向协议编程。
新增一种文件格式 = 写一个 Parser 子类并 register_parser，不需要动管线。
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
import traceback
from pathlib import Path
from typing import Iterable, Iterator

from .udm import (
    Block, Decision, DetectContext, Document, Entity, ExportResult,
    Page, ParseContext, ReplaceContext, Rule,
)

# 防止循环导入：子模块用 `from ..registry import register_parser`


class Parser:
    """解析器基类。子类必须设置 format_id / extensions。"""

    format_id: str = ""
    extensions: tuple[str, ...] = ()
    can_render: bool = False

    @classmethod
    def sniff(cls, path: Path) -> float:
        """返回 0..1 的匹配置信度。默认按扩展名。"""
        return 1.0 if path.suffix.lower() in cls.extensions else 0.0

    def parse(self, path: Path, ctx: ParseContext) -> Document:  # pragma: no cover
        raise NotImplementedError

    def export(self, doc: Document, entities: list[Entity],
               decisions: dict[str, Decision], out_path: Path,
               options: dict) -> ExportResult:  # pragma: no cover
        raise NotImplementedError

    def render(self, doc: Document, page: int, mode: str) -> bytes | None:
        return None


class Detector:
    """检测器基类。"""

    id: str = ""

    @classmethod
    def from_spec(cls, spec: dict) -> "Detector":  # pragma: no cover
        raise NotImplementedError

    def detect(self, doc: Document, rule: Rule, ctx: DetectContext) -> list[Entity]:
        raise NotImplementedError


# ────────────────────────────── 注册表 ──────────────────────────────

_PARSERS: dict[str, Parser] = {}
_DETECTORS: dict[str, type[Detector]] = {}
_REPLACERS: dict[str, object] = {}
_LOADED: set[str] = set()


def register_parser(cls: type[Parser]) -> type[Parser]:
    inst = cls()
    if not inst.format_id:
        raise ValueError(f"{cls.__name__} 缺少 format_id")
    _PARSERS[inst.format_id] = inst
    for ext in inst.extensions:
        _PARSERS.setdefault("." + ext.lstrip(".").lower(), inst)  # type: ignore[arg-type]
    return cls


def register_detector(cls: type[Detector]) -> type[Detector]:
    if not cls.id:
        raise ValueError(f"{cls.__name__} 缺少 id")
    _DETECTORS[cls.id] = cls
    return cls


def register_replacer(name: str, fn) -> None:
    _REPLACERS[name] = fn


def get_replacer(name: str):
    return _REPLACERS.get(name)


def all_parsers() -> dict[str, Parser]:
    return dict(_PARSERS)


def all_detectors() -> dict[str, type[Detector]]:
    return dict(_DETECTORS)


# 打包成 exe 后，插件源码被压进 PyInstaller 归档，磁盘上没有 parsers/ 目录，
# pkgutil 扫不到 → 这里保留一份静态清单兜底。
_PLUGIN_FALLBACK = {
    "parsers": ("pdf_parser", "docx_parser", "xlsx_parser", "image_parser",
                "text_parser", "tiff_parser", "mesh_parser"),
    "detectors": ("regex_det", "dict_det", "ner_det", "joined_det",
                  "finance_validators", "trade_validators",
                  "pipl_validators", "numeric_det", "table_det"),
    "replacers": ("surrogate", "black", "mask", "amount_consistency"),
}

# 顶层扫描器模块：导入即向 mediascan 注册（人脸/码类等页面图像目标）。
# 不放在 parsers/detectors 包内，是因为它们不属于「解析器/文本检测器」，
# 而是供解析器调用的图像扫描扩展。
_SCANNER_MODULES = ("yinzhan.barcodes", "yinzhan.visual_targets")


def load_plugins() -> list[str]:
    """导入 parsers / detectors / replacers 下所有模块，触发注册。"""
    import yinzhan
    root = Path(yinzhan.__file__).parent
    frozen = bool(getattr(sys, "frozen", False))
    errors: list[str] = []
    for pkgname in ("parsers", "detectors", "replacers"):
        names: list[str] = []
        pkg = root / pkgname
        if pkg.is_dir():
            names = [m.name for m in pkgutil.iter_modules([str(pkg)])
                     if not m.name.startswith("_")]
        if not names:
            # 打包后或目录缺失：用静态清单
            names = list(_PLUGIN_FALLBACK.get(pkgname, ()))
        for name in names:
            full = f"yinzhan.{pkgname}.{name}"
            if full in _LOADED:
                continue
            try:
                importlib.import_module(full)
                _LOADED.add(full)
            except Exception:  # 插件坏了不能带崩整个引擎
                errors.append(f"{full}: {traceback.format_exc(limit=2)}")

    # 页面图像扫描器（人脸/证件/车牌/二维码/条码…）
    for modname in _SCANNER_MODULES:
        if modname in _LOADED:
            continue
        try:
            if importlib.util.find_spec(modname) is None:
                continue          # 该扩展还没落地（开发期），跳过不算错误
            importlib.import_module(modname)
            _LOADED.add(modname)
        except Exception:
            errors.append(f"{modname}: {traceback.format_exc(limit=2)}")
    return errors


def find_parser(path: Path) -> Parser | None:
    """按扩展名快速找；找不到再按文件头嗅探。"""
    suffix = path.suffix.lower()
    inst = _PARSERS.get(suffix)  # type: ignore[index]
    if isinstance(inst, Parser):
        return inst
    best, score = None, 0.0
    for parser in {id(p): p for p in _PARSERS.values()}.values():
        try:
            s = parser.sniff(path)
        except Exception:
            s = 0.0
        if s > score:
            best, score = parser, s
    return best if score > 0.3 else None


# ────────────────────────────── 便利函数 ──────────────────────────────

def iter_text_blocks(doc: Document) -> Iterator[tuple[Page, Block]]:
    for page in doc.pages:
        for block in page.blocks:
            if block.kind == "text" and block.text:
                yield page, block


def iter_all_text(doc: Document) -> Iterator[tuple[Page, Block]]:
    """含表格单元格里的文字（展开成伪 block 由调用方处理）。"""
    yield from iter_text_blocks(doc)


__all__ = [
    "Parser", "Detector", "register_parser", "register_detector",
    "register_replacer", "get_replacer", "all_parsers", "all_detectors",
    "load_plugins", "find_parser", "iter_text_blocks", "iter_all_text",
    "ParseContext", "DetectContext", "ReplaceContext", "ExportResult",
]
