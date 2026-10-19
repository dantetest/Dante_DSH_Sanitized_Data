"""位图解析器（JPG/JPEG/PNG/BMP/WEBP）。

解析：PIL 读取像素尺寸，OCR 识别行作为 UDM block（ref 里存像素矩形）。
导出：在像素上破坏性覆盖/重绘，再重新编码成新图——不携带任何 EXIF/XMP/GPS。
"""
from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
import unicodedata

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import registry
from ..udm import (
    Block, Box, Decision, Document, Entity, ExportError, ExportResult,
    Page, ParseContext, ParseError, Span,
)

# 与前端「涂黑」主题色一致
INK: tuple[int, int, int] = (13, 11, 9)
PAPER: tuple[int, int, int] = (244, 238, 225)

# OCR 前把超大图缩到这个最长边，避免正射影像/大幅扫描件爆内存
OCR_MAX_EDGE = 2400
# 预览渲染最长边
MAX_RENDER_EDGE = 2000

FONT_CANDIDATES: tuple[str, ...] = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
)

try:  # Pillow 10+ 的枚举；旧版退回常量
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover - 老 Pillow
    _LANCZOS = Image.LANCZOS


# ────────────────────────────── 小工具 ──────────────────────────────

def _new_doc_id() -> str:
    """生成短文档 id。"""
    return "d_" + uuid.uuid4().hex[:8]


def _safe_box(box: Any) -> Box | None:
    """把任意四元组规范化成 (x0, y0, x1, y1)；非法返回 None。"""
    try:
        x0, y0, x1, y1 = (float(v) for v in box[:4])
    except Exception:
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def _clamp_box(box: Box, width: float, height: float) -> Box:
    """把矩形收进图片边界内。"""
    return (
        max(0.0, box[0]), max(0.0, box[1]),
        min(max(0.0, width), box[2]), min(max(0.0, height), box[3]),
    )


def _denormalize_rect(rect: Any, page: Page) -> Box | None:
    """矩形 span 的兜底换算。

    jobs.py 传进来的 rect 已是页面坐标（图片即像素坐标），max(rect) > 1 时原样返回；
    仅当整组值都在 0..1 时才当作归一化值补乘页面尺寸。
    """
    box = _safe_box(rect)
    if box is None:
        return None
    if (page.width > 1.5 and page.height > 1.5
            and all(-0.001 <= v <= 1.001 for v in box)):
        return (box[0] * page.width, box[1] * page.height,
                box[2] * page.width, box[3] * page.height)
    return box


def _fill(image: Image.Image, rgb: tuple[int, int, int]):
    """按图片模式返回合适的填充色（RGBA 图补 alpha）。"""
    if image.mode == "RGBA":
        return rgb + (255,)
    return rgb


# ────────────────────────────── OCR ──────────────────────────────

def _normalize_ocr_text(text: str) -> str:
    """OCR 文本做 NFKC 归一：RapidOCR 常把数字/字母识别成全角，影响规则命中。"""
    try:
        return unicodedata.normalize("NFKC", text).strip()
    except Exception:
        return text.strip()


def _ocr_blocks(image: Image.Image, page_index: int, ctx: ParseContext | None,
                warnings: list[str], *, with_quad: bool = False) -> list[Block]:
    """对一张位图跑 OCR，每个识别行转成一个 UDM text block。"""
    ocr = getattr(ctx, "ocr", None) if ctx is not None else None
    if ocr is None:
        warnings.append(f"第 {page_index + 1} 页未提供 OCR 服务，无法识别图片文字")
        return []
    try:
        if getattr(ocr, "available", True) is False:
            reason = getattr(ocr, "error", None) or "未知原因"
            warnings.append(f"OCR 不可用（{reason}），第 {page_index + 1} 页未能识别文字")
            return []
    except Exception:
        pass

    scale = 1.0
    ocr_image = image
    try:
        width, height = image.size
        long_edge = max(width, height)
        if long_edge > OCR_MAX_EDGE:
            scale = OCR_MAX_EDGE / float(long_edge)
            ocr_image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))), _LANCZOS)
        lines = ocr.recognize(ocr_image) or []
    except Exception as exc:
        warnings.append(f"第 {page_index + 1} 页 OCR 识别失败：{exc}")
        return []

    blocks: list[Block] = []
    for index, line in enumerate(lines):
        raw_text = str(getattr(line, "text", "") or "")
        text = _normalize_ocr_text(raw_text)
        if not text:
            continue
        box = _safe_box(getattr(line, "box", None))
        if box is not None and scale != 1.0:
            box = (box[0] / scale, box[1] / scale, box[2] / scale, box[3] / scale)
        ref: dict[str, Any] = {"page": page_index, "ocr": True}
        if with_quad:
            ref["quad"] = list(box) if box else []
        blocks.append(Block(
            id=f"b{page_index}_{index}", kind="text", text=text, bbox=box,
            ref=ref, page=page_index,
            attrs={"score": round(float(getattr(line, "score", 0.0)), 4)},
        ))
    if not blocks:
        warnings.append(f"第 {page_index + 1} 页未识别到文字")
    return blocks


# ────────────────────────────── 绘制 ──────────────────────────────

def _load_font(size: int) -> ImageFont.FreeTypeFont | None:
    """按字号加载中文字体；系统没有可用字体时返回 None。"""
    for candidate in FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return None


def _bg_color(image: Image.Image, box: Box) -> tuple[int, int, int]:
    """取矩形外扩 3px 一圈像素的中位色，作为覆盖底色。"""
    width, height = image.size
    x0 = max(0, int(box[0]) - 3)
    y0 = max(0, int(box[1]) - 3)
    x1 = min(width, int(box[2]) + 3)
    y1 = min(height, int(box[3]) + 3)
    if x1 <= x0 or y1 <= y0:
        return (255, 255, 255)
    try:
        region = image.crop((x0, y0, x1, y1))
        if region.mode != "RGB":
            region = region.convert("RGB")
        array = np.asarray(region, dtype=np.float64)
    except Exception:
        return (255, 255, 255)
    if array.size == 0:
        return (255, 255, 255)
    edges = np.concatenate(
        [array[0, :, :], array[-1, :, :], array[:, 0, :], array[:, -1, :]], axis=0)
    if edges.size == 0:
        return (255, 255, 255)
    median = np.median(edges, axis=0)
    return (int(median[0]), int(median[1]), int(median[2]))


def _contrast_color(background: tuple[int, int, int]) -> tuple[int, int, int]:
    """按底色亮度选墨色或纸色。"""
    luminance = 0.299 * background[0] + 0.587 * background[1] + 0.114 * background[2]
    return INK if luminance >= 128 else PAPER


def _fit_font(text: str, max_width: int, max_height: int) -> ImageFont.FreeTypeFont | None:
    """在矩形里选一个尽量大的、能放下整段文字的字号。"""
    size = max(6, int(max(4, max_height) * 0.78))
    while size >= 6:
        font = _load_font(size)
        if font is None:
            return None
        try:
            if font.getlength(text) <= max(1, max_width):
                return font
        except Exception:
            return font
        size = int(size * 0.88)
    return _load_font(6)


def _paint_box(image: Image.Image, draw: ImageDraw.ImageDraw, box: Box,
               decision: Decision, warnings: list[str]) -> None:
    """按决策破坏性覆盖一个矩形：black 涂黑；其余先盖底色再写替换文字。"""
    clamped = _clamp_box(box, image.size[0], image.size[1])
    x0, y0, x1, y1 = (int(round(v)) for v in clamped)
    if x1 <= x0 or y1 <= y0:
        return
    black = bool(decision.black) or decision.strategy in ("black", "drop")
    if black:
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=_fill(image, INK))
        return

    replacement = (decision.replacement or "").replace("\r", " ").replace("\n", " ").strip()
    background = _bg_color(image, clamped)
    draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=_fill(image, background))
    if not replacement:
        # 没有替换文本时按删除处理，绝不把原文留在图上
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=_fill(image, INK))
        warnings.append("surrogate/mask 决策缺少替换文本，已按涂黑处理")
        return
    font = _fit_font(replacement, x1 - x0 - 2, y1 - y0)
    if font is None:
        warnings.append("未找到可用的中文字体，已覆盖原文但未能写入替换文字")
        return
    draw.text(((x0 + x1) / 2.0, (y0 + y1) / 2.0), replacement,
              font=font, fill=_fill(image, _contrast_color(background)), anchor="mm")


# ────────────────────────────── 决策 -> 矩形 ──────────────────────────────

def _span_box_in_block(span: Span, block: Block, page: Page) -> Box | None:
    """把 span 映射成 block 内矩形；OCR block 按字符比例横向插值。"""
    if block.bbox is None:
        return None
    text_len = len(block.text)
    if (text_len <= 1 or span.start is None or span.start < 0
            or span.end is None or span.end < 0):
        return block.bbox
    start = max(0, min(int(span.start), text_len))
    end = max(start, min(int(span.end), text_len))
    if start == 0 and end >= text_len:
        return block.bbox
    x0, y0, x1, y1 = block.bbox
    width = x1 - x0
    if width <= 0:
        return block.bbox
    return (x0 + width * start / text_len, y0, x0 + width * end / text_len, y1)


def _apply_decisions(image: Image.Image, doc: Document, page_index: int,
                     entities: list[Entity] | None,
                     decisions: dict[str, Decision] | None,
                     warnings: list[str]) -> int:
    """把某一页上所有实体的决策画到图片上，返回处理的矩形数。"""
    if not (0 <= page_index < len(doc.pages)):
        return 0
    page = doc.pages[page_index]
    block_page: dict[str, int] = {}
    for candidate in doc.pages:
        for block in candidate.blocks:
            block_page[block.id] = candidate.index
    page_block_ids = {block.id: block for block in page.blocks}
    draw = ImageDraw.Draw(image)
    painted = 0
    for entity in entities or []:
        decision = (decisions or {}).get(entity.id) or entity.decision
        if decision is None:
            continue
        if decision.strategy == "keep" and not decision.black:
            continue
        for span in entity.spans or []:
            box: Box | None = None
            if span.block_id:
                page_no = block_page.get(span.block_id)
                if page_no is None:
                    warnings.append(f"实体 {entity.id} 的定位块 {span.block_id} 不存在，已跳过")
                    continue
                if page_no != page_index:
                    continue
                block = page_block_ids.get(span.block_id)
                if block is None:
                    continue
                box = _span_box_in_block(span, block, page)
            elif span.rect:
                # 矩形 span 必带页号；缺页号时只能落在第 0 页（单页图总能命中）
                if span.page is not None:
                    if int(span.page) != page_index:
                        continue
                elif page_index != 0:
                    continue
                box = _denormalize_rect(span.rect, page)
            if box is None:
                warnings.append(f"实体 {entity.id} 有一处定位缺失，已跳过")
                continue
            _paint_box(image, draw, box, decision, warnings)
            painted += 1
    return painted


# ────────────────────────────── 编解码 ──────────────────────────────

def _to_export_image(image: Image.Image) -> Image.Image:
    """转成全新图片（丢弃 info 里的 EXIF/ICC/XMP），有 alpha 则保留。"""
    has_alpha = (
        image.mode in ("RGBA", "LA")
        or (image.mode == "P" and "transparency" in image.info)
    )
    return image.convert("RGBA" if has_alpha else "RGB")


def _output_format(out_path: Path, doc: Document) -> str:
    """按输出扩展名决定编码格式，未知时回退原图格式。"""
    suffix = out_path.suffix.lower()
    mapping = {
        ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
        ".bmp": "BMP", ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF",
    }
    if suffix in mapping:
        return mapping[suffix]
    source = str((doc.meta or {}).get("format") or "").upper()
    return "JPEG" if source in ("JPEG", "MPO") else "PNG"


def _save_image(image: Image.Image, out_path: Path, fmt: str, warnings: list[str]) -> None:
    """按目标格式重新编码；不传 exif/icc_profile 等原图元数据。"""
    target = image
    if fmt == "JPEG" and target.mode != "RGB":
        target = target.convert("RGB")
    kwargs: dict[str, Any] = {}
    if fmt in ("JPEG", "WEBP"):
        kwargs["quality"] = 92
    try:
        target.save(str(out_path), format=fmt, **kwargs)
        return
    except Exception as exc:
        warnings.append(f"按 {fmt} 重编码失败（{exc}），尝试 RGB 默认参数")
    try:
        target.convert("RGB").save(str(out_path), format=fmt)
    except Exception as exc:
        raise ExportError(f"保存图片失败：{exc}") from exc


def _render_image_bytes(doc: Document, page: int, mode: str) -> bytes | None:
    """读取原图/成品图，返回最长边不超过 2000px 的 PNG 字节。"""
    path = Path(doc.path)
    out_path = (doc.extra or {}).get("out_path")
    if mode == "out" and out_path:
        path = Path(out_path)
    if not path.is_file():
        return None
    try:
        with Image.open(path) as image:
            frames = int(getattr(image, "n_frames", 1) or 1)
            index = int(page)
            if index < 0 or index >= frames:
                return None
            if index:
                image.seek(index)
            frame = image.convert("RGB")
        frame.thumbnail((MAX_RENDER_EDGE, MAX_RENDER_EDGE), _LANCZOS)
        buffer = BytesIO()
        frame.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return None


# ────────────────────────────── 解析器 ──────────────────────────────

class ImageParser(registry.Parser):
    """位图解析器：单页，页面尺寸 = 像素尺寸，OCR 行作为 text block。"""

    format_id = "image"
    extensions = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
    can_render = True

    @classmethod
    def sniff(cls, path: Path) -> float:
        if Path(path).suffix.lower() in cls.extensions:
            return 1.0
        try:
            with open(path, "rb") as handle:
                head = handle.read(16)
        except OSError:
            return 0.0
        if head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:2] == b"BM":
            return 0.8
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return 0.8
        return 0.0

    def parse(self, path: Path, ctx: ParseContext) -> Document:
        path = Path(path)
        try:
            with Image.open(path) as image:
                width, height = float(image.size[0]), float(image.size[1])
                pil_format = (image.format or "").upper()
                mode = image.mode
                frames = int(getattr(image, "n_frames", 1) or 1)
                rgb = image.convert("RGB")
        except Exception as exc:
            raise ParseError(f"无法打开图片：{exc}") from exc

        doc = Document(
            doc_id=_new_doc_id(), path=str(path), fmt="image",
            meta={"format": pil_format, "size": [int(width), int(height)], "mode": mode},
        )
        if frames > 1:
            doc.warnings.append(f"图片包含 {frames} 帧，仅处理第 1 帧")
        page = Page(index=0, width=width, height=height)
        page.blocks = _ocr_blocks(rgb, 0, ctx, doc.warnings, with_quad=True)
        page.ref = {"format": pil_format, "ocr": bool(page.blocks)}
        # 图像目标扫描（人脸/证件/车牌/二维码/条码…）：没有文字可读的目标在这里找。
        # 没有注册扫描器时 has_enabled() 为 False，不产生任何开销。
        try:
            from .. import mediascan

            if mediascan.has_enabled():
                text_boxes = [{"text": b.text, "rect": b.bbox}
                              for b in page.blocks if b.bbox]
                marks, scan_warns = mediascan.analyze(
                    rgb, page=0, dpi=0.0, options={"text_boxes": text_boxes})
                doc.visual_marks.extend(mediascan.to_visual_marks(marks))
                doc.warnings.extend(scan_warns)
        except Exception as exc:      # 扩展扫描失败不能带崩主流程
            doc.warnings.append(f"图像目标扫描跳过：{exc}")
        doc.pages.append(page)
        return doc

    def export(self, doc: Document, entities: list[Entity],
               decisions: dict[str, Decision], out_path: Path,
               options: dict) -> ExportResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        try:
            with Image.open(doc.path) as source:
                source.load()
                base = _to_export_image(source)
        except Exception as exc:
            raise ExportError(f"无法打开图片：{exc}") from exc
        if len(doc.pages) > 1:
            warnings.append("图片格式只导出第 1 帧，其余帧已丢弃")
        _apply_decisions(base, doc, 0, entities, decisions, warnings)
        _save_image(base, out_path, _output_format(out_path, doc), warnings)
        doc.extra["out_path"] = str(out_path)
        try:
            size = out_path.stat().st_size
        except OSError:
            size = 0
        return ExportResult(path=str(out_path), warnings=warnings, size=size)

    def render(self, doc: Document, page: int, mode: str = "orig") -> bytes | None:
        return _render_image_bytes(doc, page, mode)


registry.register_parser(ImageParser)
