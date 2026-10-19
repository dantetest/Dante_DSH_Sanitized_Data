"""页面图像扫描器（mediascan）——给「只能靠图像找」的目标统一入口。

背景：OCR 只能读字；人脸、证件照片、车牌、二维码/条码、印章这些目标没有可靠
的文字可供规则匹配。原先只有 PDF 解析器在 ``_collect_visual`` 里找红章/签名，
图片和 TIFF 完全没有图像检测。这里提供一个与格式无关的扫描框架：

- 各领域在自己的模块里 ``register_scanner(name, fn, kinds=...)``；
- 解析器拿到页面 RGB 图后调用 :func:`analyze`，得到统一坐标系的命中框；
- 命中框写进 ``Document.visual_marks``，由 jobs 层转成 Entity 并涂黑；
- Verify 阶段对成品重解析、再扫一次，残留的同类标记即视为脱敏失败。

坐标约定（重要）：返回的 rect 一律是「传入图像」的像素坐标。
调用方负责换算：image/tiff 直接用原图像素；PDF 用渲染 dpi 折算成 pt。

扫描器性能：本模块不做统一缩放（条码需要原始分辨率，人脸可以自己缩），
由各扫描器自己控制成本；``YINZHAN_SCAN_MAX_PIXELS`` 只作为大图保护提示。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

try:  # numpy 是硬依赖（PIL/OCR 环境都有）
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

Box = tuple[float, float, float, float]


# ────────────────────────────── 数据结构 ──────────────────────────────

@dataclass
class ScanHit:
    """一个扫描命中：kind 与 visual mark / etype 对应（如 face、qrcode）。"""

    kind: str
    rect: Box
    confidence: float = 0.9
    note: str = ""


@dataclass
class ScanContext:
    """扫描上下文。扫描器不应修改图像本身。"""

    page: int = 0
    dpi: float = 0.0              # 该渲染图的 dpi；图片/TIFF 可为 0
    image_size: tuple[int, int] = (0, 0)
    options: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


ScannerFn = Callable[[Any, ScanContext], list[ScanHit]]


# ────────────────────────────── 注册表 ──────────────────────────────

_SCANNERS: list[tuple[str, ScannerFn, tuple[str, ...], int]] = []
_LOCK = threading.RLock()


def register_scanner(name: str, fn: ScannerFn, *, kinds: tuple[str, ...] = (),
                     priority: int = 50) -> None:
    """注册一个页面图像扫描器。

    :param name: 唯一名字（用于日志、环境变量开关、去重），如 ``visual_targets``。
    :param fn: ``fn(image_rgb: np.ndarray, ctx: ScanContext) -> list[ScanHit]``。
    :param kinds: 该扫描器可能返回的 kind 集合（用于 ``has_enabled`` 快速判断）。
    :param priority: 数字大的先执行（默认 50）。
    """
    if not name or not callable(fn):
        raise ValueError("register_scanner 需要 name 与可调用 fn")
    with _LOCK:
        for idx, (existing, *_rest) in enumerate(_SCANNERS):
            if existing == name:
                _SCANNERS[idx] = (name, fn, tuple(kinds), int(priority))
                return
        _SCANNERS.append((name, fn, tuple(kinds), int(priority)))


def unregister_scanner(name: str) -> None:
    """测试用：移除一个扫描器。"""
    with _LOCK:
        _SCANNERS[:] = [item for item in _SCANNERS if item[0] != name]


def reset_scanners() -> None:
    """测试用：清空扫描器（插件重新加载后可重注册）。"""
    with _LOCK:
        _SCANNERS.clear()


def scanner_names() -> list[str]:
    with _LOCK:
        return [name for name, *_ in _SCANNERS]


# ────────────────────────────── 开关 ──────────────────────────────

def _flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _disabled() -> set[str]:
    raw = os.environ.get("YINZHAN_SCAN_DISABLE", "") or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _enabled_scanners() -> list[tuple[str, ScannerFn, tuple[str, ...], int]]:
    """按环境变量过滤：总开关 + 分域开关 + 显式禁用名单。"""
    if not _flag("YINZHAN_SCAN", True):
        return []
    visual_on = _flag("YINZHAN_SCAN_VISUAL", True)
    barcode_on = _flag("YINZHAN_SCAN_BARCODE", True)
    disabled = _disabled()
    out = []
    with _LOCK:
        for name, fn, kinds, priority in _SCANNERS:
            low_name = name.lower()
            low_kinds = {k.lower() for k in kinds}
            if low_name in disabled or (low_kinds & disabled):
                continue
            is_barcode = ("barcode" in low_name or "qrcode" in low_name
                          or bool({"qrcode", "barcode"} & low_kinds))
            if is_barcode and not barcode_on:
                continue
            if not is_barcode and not visual_on:
                continue
            out.append((name, fn, kinds, priority))
    return sorted(out, key=lambda item: -item[3])


def has_enabled() -> bool:
    """是否有启用的扫描器（解析器据此决定是否多渲染一张图，避免空跑）。"""
    return bool(_enabled_scanners())


def max_pixels() -> int:
    try:
        return int(os.environ.get("YINZHAN_SCAN_MAX_PIXELS", "0") or 0)
    except ValueError:
        return 0


# ────────────────────────────── 扫描 ──────────────────────────────

def _iou(a: Box, b: Box) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _merge_hits(hits: list[ScanHit], iou_threshold: float = 0.5) -> list[ScanHit]:
    """同 kind 且高度重叠的框合并（不同扫描器/多尺度重复命中）。"""
    merged: list[ScanHit] = []
    for hit in hits:
        placed = False
        for item in merged:
            if item.kind == hit.kind and _iou(item.rect, hit.rect) >= iou_threshold:
                item.rect = (
                    min(item.rect[0], hit.rect[0]),
                    min(item.rect[1], hit.rect[1]),
                    max(item.rect[2], hit.rect[2]),
                    max(item.rect[3], hit.rect[3]),
                )
                item.confidence = max(item.confidence, hit.confidence)
                if hit.note and hit.note not in item.note:
                    item.note = (item.note + " " + hit.note).strip()
                placed = True
                break
        if not placed:
            merged.append(ScanHit(hit.kind, hit.rect, hit.confidence, hit.note))
    return merged


def analyze(image_rgb: Any, *, page: int = 0, dpi: float = 0.0,
            options: dict | None = None) -> tuple[list[dict], list[str]]:
    """对一张页面图跑所有启用的扫描器。

    返回 ``(marks, warnings)``：
      marks = [{"kind","rect","confidence","note","page"}]，rect 为传入图像像素坐标；
      warnings = 扫描器异常/大图提示（解析器并入 doc.warnings，不阻断主流程）。
    """
    warnings: list[str] = []
    if image_rgb is None or np is None:
        return [], warnings
    try:  # 兼容传 PIL.Image 的调用方（图片/TIFF 解析器持有的是 PIL 对象）
        if not hasattr(image_rgb, "shape") and hasattr(image_rgb, "convert"):
            image_rgb = np.asarray(image_rgb.convert("RGB"))
        height, width = int(image_rgb.shape[0]), int(image_rgb.shape[1])
    except Exception:
        return [], ["mediascan: 图像形状不可读"]
    ctx = ScanContext(page=page, dpi=float(dpi or 0.0),
                      image_size=(width, height),
                      options=dict(options or {}),
                      warnings=warnings)
    limit = max_pixels()
    if limit and width * height > limit:
        warnings.append(
            f"页面图像 {width}×{height} 超过扫描上限 YINZHAN_SCAN_MAX_PIXELS={limit}，"
            "部分扫描器可能被跳过")
        ctx.options.setdefault("oversize", True)

    hits: list[ScanHit] = []
    for name, fn, _kinds, _priority in _enabled_scanners():
        try:
            found = fn(image_rgb, ctx) or []
        except Exception as exc:  # 扫描失败不能带崩解析
            warnings.append(f"mediascan:{name} 扫描失败：{exc}")
            continue
        for hit in found:
            try:
                kind = str(hit.kind or "").strip()
                rect = tuple(float(v) for v in hit.rect[:4])
                if not kind or len(rect) != 4:
                    continue
                x0, y0, x1, y1 = rect
                if x1 <= x0 or y1 <= y0:
                    continue
                hits.append(ScanHit(kind, (max(0.0, x0), max(0.0, y0),
                                           min(float(width), x1), min(float(height), y1)),
                                    float(hit.confidence), str(hit.note or "")))
            except Exception:
                continue
    marks = [
        {"kind": h.kind, "rect": h.rect, "confidence": round(h.confidence, 3),
         "note": h.note, "page": int(page)}
        for h in _merge_hits(hits)
    ]
    return marks, warnings


def to_visual_marks(marks: list[dict], *, dpi: float = 0.0,
                    scale: float = 1.0, page: int | None = None) -> list:
    """把 analyze() 的 marks 转成 ``udm.VisualMark``。

    :param scale: 像素 -> 目标坐标系的比例（PDF 的 pt 用 72/dpi；图片/TIFF 用 1）。
    :param page: 覆盖页码（缺省用 mark 自带 page）。
    """
    from .udm import VisualMark

    out = []
    for mark in marks or []:
        try:
            rect = tuple(float(v) * scale for v in mark["rect"])
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                continue
            out.append(VisualMark(
                kind=str(mark["kind"]),
                page=int(mark.get("page", 0) if page is None else page),
                rect=rect,
                confidence=float(mark.get("confidence", 0.9)),
                note=str(mark.get("note", "")),
            ))
        except Exception:
            continue
    return out


__all__ = [
    "ScanHit", "ScanContext", "register_scanner", "unregister_scanner",
    "reset_scanners", "scanner_names", "has_enabled", "analyze",
    "to_visual_marks", "max_pixels",
]
