# -*- coding: utf-8 -*-
"""TIFF 解析器（含多页 / GeoTIFF 正射影像）。

P0（2026-10-20，方案 A）：
- **大图**：优先用可选依赖 ``rasterio`` 做窗口/分块读写，绕过 Pillow 的
  ``Image.MAX_IMAGE_PIXELS`` 限制（>1.79 亿像素正射可处理）；没装 rasterio 时
  回退 Pillow 并在超限时给出明确中文报错（不静默失败）。
- **位深/波段**：rasterio 路线保留原 dtype / 波段数；Pillow 回退统一 8bit RGB
  并 warning。
- **坐标中性化 v1**：默认写 LOCAL_CS 本地任意 CRS（随机平移+旋转，D3 已批准），
  参数放 ``doc.extra["geo_params"]``（jobs 自动入加密 vault，D2 可还原）；
  ``geo_mode="strip"`` 可彻底去参考。
- **元数据**：剥离 Geo / EXIF / GPS / XMP / IPTC / ICC；清理
  ``.tfw/.prj/.aux.xml/.ovr/.kml/.rpc`` 等派生 sidecar。
- **复检**：解析器侧 ``georef.verify_exported_geo`` + 管线 ``pipeline.verify``
  双重兜底；真实地理参考/泄漏元数据残留一律 ``ExportError``。
- 保留 Lead 加好的 ``mediascan`` 逐帧扫描 hook（大图自动降采样扫描并提示）。

设计文档见 ``docs/impl/tiff.md``；纯逻辑（地理数学/TIFF 结构扫描）在
``yinzhan/georef.py``。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import registry
from ..udm import (
    Decision, Document, Entity, ExportError, ExportResult, Page, ParseContext, ParseError,
)
from .image_parser import (
    _apply_decisions, _denormalize_rect, _new_doc_id, _ocr_blocks, _paint_box,
    _render_image_bytes, _span_box_in_block,
)

# ────────────────────────── 兼容旧接口的常量 ──────────────────────────

#: 需要识别并剥离的地理参考标签（键名写进 doc.meta，保持旧接口语义）
GEO_TAG_NAMES: dict[int, str] = {
    33550: "ModelPixelScaleTag",
    33922: "ModelTiepointTag",
    34264: "ModelTransformationTag",
    34735: "GeoKeyDirectoryTag",
    34736: "GeoDoubleParamsTag",
    34737: "GeoAsciiParamsTag",
    42112: "GDAL_METADATA",
    42113: "GDAL_NODATA",
}

#: Pillow 回退时明确允许带进新 tiffinfo 的展示性标签：方向、分辨率、分辨率单位。
KEEP_TAGS: tuple[int, ...] = (274, 282, 283, 296)

# ────────────────────────── 可调参数（环境变量） ──────────────────────────


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    return (os.environ.get(name, "") or default).strip()


#: 整帧像素超过该值：OCR/图像扫描改用降采样预览（控制内存与耗时）
FULLREAD_MAX_PIXELS = _env_int("YINZHAN_TIFF_FULLREAD_MAX_PIXELS", 40_000_000)
#: 全分辨率图像扫描上限（条码/人脸等）；超过则只用预览图扫描并提示人工复核
SCAN_MAX_PIXELS = _env_int("YINZHAN_TIFF_SCAN_MAX_PIXELS", 50_000_000)
#: 大图预览最长边（OCR 自身还会缩到 1600，这里保证坐标回算精度）
PREVIEW_MAX_SIDE = max(1200, _env_int("YINZHAN_TIFF_PREVIEW_MAX_SIDE", 3000))
#: rasterio 导出窗口大小（像素）
EXPORT_WINDOW = max(256, _env_int("YINZHAN_TIFF_WINDOW", 2048))
#: 输出 TIFF 分块大小
EXPORT_BLOCK = max(128, _env_int("YINZHAN_TIFF_BLOCK", 256))
#: 输出压缩方式
EXPORT_COMPRESS = _env_str("YINZHAN_TIFF_COMPRESS", "deflate") or "deflate"
#: 是否生成内部 overview（金字塔）
EXPORT_OVERVIEWS = _env_str("YINZHAN_TIFF_OVERVIEWS", "1") not in ("0", "false", "no", "off")
#: 预览渲染最长边（与 image_parser 保持一致）
MAX_RENDER_EDGE = 2000
#: Pillow 默认像素上限（运行时读取，兜底值）
_PIL_DEFAULT_MAX_PIXELS = 89_478_485


def _georef():
    """惰性导入 georef（避免服务启动时强制 import rasterio）。"""
    from .. import georef as _g

    return _g


def _pil_max_pixels() -> int:
    try:
        value = Image.MAX_IMAGE_PIXELS
        return int(value) if value else _PIL_DEFAULT_MAX_PIXELS
    except Exception:
        return _PIL_DEFAULT_MAX_PIXELS


# ────────────────────────── Pillow 回退的小工具 ──────────────────────────

def _scan_geo_tags(source: Image.Image) -> list[str]:
    """读取一帧上的地理参考标签名（Pillow 回退路径用）。"""
    names: list[str] = []
    try:
        tags = source.tag_v2
    except Exception:
        return names
    for tag, name in GEO_TAG_NAMES.items():
        try:
            if tag in tags and name not in names:
                names.append(name)
        except Exception:
            continue
    return names


def _safe_tiffinfo(source: Image.Image) -> dict[int, Any]:
    """构造新的 TIFF 标签集：只保留方向/分辨率，其余（含地理、设备、ICC）不带。"""
    try:
        tags = dict(source.tag_v2.items())
    except Exception:
        return {}
    info: dict[int, Any] = {}
    for tag in KEEP_TAGS:
        if tag not in tags:
            continue
        value = tags[tag]
        try:
            info[tag] = int(value) if tag in (274, 296) else float(value)
        except (TypeError, ValueError):
            continue
    return info


def _remove_sidecars(out_path: Path, warnings: list[str]) -> list[str]:
    """删除成品名派生的 GIS sidecar（含 .ovr/.kml/.rpc 等），不碰源文件。"""
    g = _georef()
    removed: list[str] = []
    candidates: list[Path] = []
    candidates.extend(g.read_linked_sidecars(out_path))
    legacy = [
        out_path.with_suffix(".tfw"),
        out_path.with_suffix(".aux.xml"),
        out_path.with_suffix(".prj"),
        out_path.parent / (out_path.name + ".aux.xml"),
    ]
    for path in legacy:
        if path not in candidates:
            candidates.append(path)
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate).lower()
            if key in seen or not candidate.is_file():
                continue
            seen.add(key)
            candidate.unlink()
            removed.append(candidate.name)
        except OSError as exc:
            warnings.append(f"删除 sidecar {candidate.name} 失败：{exc}")
    return removed


def _verify_geo_stripped(out_path: Path) -> list[str]:
    """Pillow 回退路径的复检：重新打开成品，确认 Geo 标签没了。"""
    try:
        with Image.open(out_path) as image:
            return _scan_geo_tags(image)
    except Exception:
        return []


# ────────────────────────── rasterio 读图工具 ──────────────────────────

def _band_indexes(count: int) -> tuple[int, ...]:
    """取用于 OCR/扫描/预览的 RGB 波段索引（1-based）。"""
    if count >= 3:
        return (1, 2, 3)
    return (1,)


def _to_uint8_rgb(data: np.ndarray, count: int) -> np.ndarray:
    """把任意 dtype/波段数读出来的窗口转成 uint8 RGB（只为 OCR/扫描/预览）。"""
    if data.ndim == 2:
        data = data[None, ...]
    count = int(data.shape[0]) if data.shape[0] else 1
    if count == 1:
        gray = data[0]
        rgb = np.repeat(gray[:, :, None], 3, axis=2)
    else:
        rgb = np.transpose(data[:3], (1, 2, 0))
        if rgb.shape[2] == 2:
            rgb = np.repeat(rgb[:, :, :1], 3, axis=2)
    arr = rgb
    if arr.dtype == np.uint8:
        return np.ascontiguousarray(arr)
    if np.issubdtype(arr.dtype, np.integer):
        info = np.iinfo(arr.dtype)
        if info.max > 255:
            arr = (arr.astype(np.float32) * (255.0 / float(info.max))).clip(0, 255)
        return np.ascontiguousarray(arr.astype(np.uint8))
    # 浮点：按 2%~98% 分位拉伸到 8bit
    work = arr.astype(np.float32)
    finite = np.isfinite(work)
    if not finite.any():
        return np.zeros(work.shape, dtype=np.uint8)
    lo = float(np.percentile(work[finite], 2))
    hi = float(np.percentile(work[finite], 98))
    if hi <= lo:
        hi = lo + 1.0
    return np.ascontiguousarray(
        ((work - lo) * (255.0 / (hi - lo))).clip(0, 255).astype(np.uint8))


def _rasterio_read_preview(src, max_side: int | None) -> tuple[Image.Image, float]:
    """用 rasterio 读一张（可降采样的）RGB 预览图，返回 (PIL RGB, scale)。"""
    width, height = int(src.width), int(src.height)
    scale = 1.0
    out_w, out_h = width, height
    if max_side and max(width, height) > int(max_side):
        scale = float(max_side) / float(max(width, height))
        out_w = max(1, int(round(width * scale)))
        out_h = max(1, int(round(height * scale)))
    indexes = _band_indexes(int(src.count))
    try:
        from rasterio.enums import Resampling

        resampling = Resampling.bilinear if (out_w != width) else Resampling.nearest
    except Exception:  # pragma: no cover
        resampling = None
    kwargs: dict[str, Any] = {"indexes": indexes, "out_shape": (len(indexes), out_h, out_w)}
    if resampling is not None:
        kwargs["resampling"] = resampling
    data = src.read(**kwargs)
    rgb = _to_uint8_rgb(data, len(indexes))
    return Image.fromarray(rgb, "RGB"), scale


def _rasterio_read_full(src) -> tuple[Image.Image, float]:
    return _rasterio_read_preview(src, None)


def _scale_blocks(blocks: list, sx: float, sy: float) -> None:
    """把预览图坐标系里的 OCR block 缩回原图像素坐标（原地修改）。"""
    if not blocks or (abs(sx - 1.0) < 1e-9 and abs(sy - 1.0) < 1e-9):
        return
    for block in blocks:
        try:
            if block.bbox:
                x0, y0, x1, y1 = block.bbox
                block.bbox = (x0 * sx, y0 * sy, x1 * sx, y1 * sy)
            quad = (block.ref or {}).get("quad")
            if quad and len(quad) >= 4:
                block.ref["quad"] = [quad[0] * sx, quad[1] * sy,
                                     quad[2] * sx, quad[3] * sy]
        except Exception:
            continue


def _scan_marks(image_rgb: Image.Image | np.ndarray, page_index: int,
                ctx: ParseContext, doc: Document, *,
                scale: float = 1.0) -> None:
    """调用 mediascan 图像目标扫描（保留 Lead 的 hook 语义）。

    ``scale`` 为“传入图 → 原图”的放大比例（预览图 < 1 时 >1）。
    """
    try:
        from .. import mediascan

        if not mediascan.has_enabled():
            return
        text_boxes = []
        if 0 <= page_index < len(doc.pages):
            text_boxes = [{"text": b.text, "rect": b.bbox}
                          for b in doc.pages[page_index].blocks if b.bbox]
        options: dict[str, Any] = {"text_boxes": text_boxes}
        if scale and abs(scale - 1.0) > 1e-9:
            options["downsampled"] = True
            options["preview_scale"] = float(scale)
        marks, scan_warns = mediascan.analyze(
            image_rgb, page=page_index, dpi=0.0, options=options)
        if marks:
            doc.visual_marks.extend(
                mediascan.to_visual_marks(marks, page=page_index, scale=float(scale)))
        doc.warnings.extend(scan_warns)
    except Exception as scan_exc:  # 扫描失败不能带崩解析
        doc.warnings.append(f"第 {page_index + 1} 帧图像目标扫描跳过：{scan_exc}")


def _detect_meta_leaks(structure, warnings: list[str]) -> None:
    """源文件元数据泄漏面提示（ICC/EXIF/GPS/XMP 等），导出时会被剥离。"""
    g = _georef()
    leaks = g.leak_tags_present(set(structure.first_tags))
    if leaks:
        warnings.append("源文件含元数据（导出时剥离）：" + "、".join(leaks))


# ────────────────────────── 解析 ──────────────────────────

class TiffParser(registry.Parser):
    """TIFF 解析器：多帧逐页 OCR；导出剥离地理参考并做坐标中性化。"""

    format_id = "tiff"
    extensions = (".tif", ".tiff")
    can_render = True

    @classmethod
    def sniff(cls, path: Path) -> float:
        if Path(path).suffix.lower() in cls.extensions:
            return 1.0
        try:
            with open(path, "rb") as handle:
                head = handle.read(4)
        except OSError:
            return 0.0
        return 0.9 if head in (b"II*\x00", b"MM\x00*") else 0.0

    # ── 解析入口 ──

    def parse(self, path: Path, ctx: ParseContext) -> Document:
        path = Path(path)
        g = _georef()
        structure = g.scan_tiff(path)
        # rasterio 路线：单页（真正的正射影像主场景）
        if g.has_rasterio() and not structure.error and structure.frames <= 1:
            try:
                return self._parse_with_rasterio(path, ctx, structure)
            except ParseError:
                raise
            except Exception as exc:
                document = self._parse_with_pillow(path, ctx, structure)
                document.warnings.append(f"rasterio 解析失败，已回退 Pillow：{exc}")
                return document
        return self._parse_with_pillow(path, ctx, structure)

    def _parse_with_rasterio(self, path: Path, ctx: ParseContext,
                             structure) -> Document:
        g = _georef()
        with g.rasterio.open(path) as src:
            width, height = int(src.width), int(src.height)
            count = int(src.count)
            dtype = str(src.dtypes[0]) if src.dtypes else "uint8"
            document = Document(doc_id=_new_doc_id(), path=str(path), fmt="tiff")
            document.meta.update({
                "format": "TIFF",
                "frames": 1,
                "size": [width, height],
                "bands": count,
                "dtype": dtype,
                "compression": (src.compression.value if src.compression else "none"),
                "engine": "rasterio",
            })
            ref = g.read_georef(path, structure)
            kind = ref.kind if ref.kind != "none" else "none"
            names = [GEO_TAG_NAMES[t] for t in GEO_TAG_NAMES if t in structure.first_tags]
            if kind == "real":
                document.meta["geo_tags"] = names
                document.meta["geo_source_tags"] = names
                document.warnings.append(
                    "检测到真实地理参考（" + "、".join(names) + "），"
                    "导出时将剥离并做坐标中性化")
                if ref.transform is not None:
                    document.extra["geo_transform"] = {
                        "crs": ref.crs_desc,
                        "epsg": ref.epsg,
                        "transform": [round(float(v), 9) for v in ref.transform],
                        "size": [width, height],
                    }
            elif kind == "local":
                document.meta["geo_local_tags"] = names
                document.warnings.append(
                    "源文件已是本地/任意 CRS（无真实定位），导出按中性化处理")
                if ref.transform is not None:
                    document.extra["geo_transform"] = {
                        "crs": ref.crs_desc or "local",
                        "epsg": None,
                        "transform": [round(float(v), 9) for v in ref.transform],
                        "size": [width, height],
                    }
            _detect_meta_leaks(structure, document.warnings)
            try:
                interp = " ".join(str(ci) for ci in src.colorinterp).lower()
                if "cmyk" in interp or "ycbcr" in interp:
                    document.warnings.append(
                        "源 TIFF 色彩空间为 CMYK/YCbCr：预览/OCR 按近似 RGB 处理，"
                        "成品按原波段保留（查看器需支持该色彩空间）")
            except Exception:
                pass

            pixels = width * height
            full_read = pixels <= FULLREAD_MAX_PIXELS
            max_side = None if full_read else PREVIEW_MAX_SIDE
            rgb, scale = _rasterio_read_preview(src, max_side)
            if not full_read:
                document.warnings.append(
                    f"画面 {width}×{height}（{pixels / 1e6:.1f}M 像素）超过整帧读取上限，"
                    f"OCR/图像扫描使用 1/{1.0 / scale:.2f} 降采样预览；"
                    "条码/小字等细节检测可能漏检，请人工框选复核")
        page = Page(index=0, width=float(width), height=float(height), ref={"frame": 0})
        page.blocks = _ocr_blocks(rgb, 0, ctx, document.warnings, with_quad=True)
        _scale_blocks(page.blocks, 1.0 / scale if scale else 1.0,
                      1.0 / scale if scale else 1.0)
        document.pages.append(page)
        # 大图扫描保护：整帧像素超过扫描上限时只用降采样图扫描（已在上面的预览里）
        scan_scale = 1.0 / scale if scale else 1.0
        if pixels > SCAN_MAX_PIXELS and scale >= 1.0:
            document.warnings.append(
                f"画面 {pixels / 1e6:.1f}M 像素超过图像扫描上限 "
                f"YINZHAN_TIFF_SCAN_MAX_PIXELS={SCAN_MAX_PIXELS}，已跳过全分辨率扫描")
        elif pixels > SCAN_MAX_PIXELS:
            document.warnings.append(
                f"画面 {pixels / 1e6:.1f}M 像素超过全分辨率扫描上限，"
                "仅对降采样图做人脸/证件等扫描（条码类可能漏检）")
        elif pixels > FULLREAD_MAX_PIXELS:
            document.warnings.append(
                "画面较大，图像扫描基于降采样图；条码类细节可能漏检，建议人工复核")
        _scan_marks(rgb, 0, ctx, document, scale=scan_scale)
        document.meta["full_read"] = bool(full_read)
        document.meta["preview_scale"] = round(float(scale), 6)
        return document

    def _parse_with_pillow(self, path: Path, ctx: ParseContext,
                           structure) -> Document:
        """Pillow 回退路径（无 rasterio / 多帧 / rasterio 打不开时）。"""
        g = _georef()
        pixels = int(getattr(structure, "pixels", 0) or 0)
        pil_limit = _pil_max_pixels()
        if pixels > pil_limit * 2:
            hint = ("未安装可选依赖 rasterio，Pillow 无法处理如此大的影像；"
                    "请安装 rasterio（离线 wheel 见 docs/impl/tiff.md）或先切图")
            raise ParseError(
                f"TIFF 过大（{structure.width}×{structure.height}，{pixels / 1e6:.0f}M 像素）："
                f"{hint}")
        if pixels > pil_limit:
            # 让 Pillow 自己抛 DecompressionBombWarning，但至少给出中文提示
            pass
        try:
            source = Image.open(path)
            source.load()
        except Exception as exc:
            raise ParseError(f"无法打开 TIFF：{exc}") from exc
        document = Document(doc_id=_new_doc_id(), path=str(path), fmt="tiff")
        geo_names: list[str] = []
        try:
            frames = int(getattr(source, "n_frames", 1) or 1)
            document.meta["format"] = "TIFF"
            document.meta["frames"] = frames
            document.meta["engine"] = "pillow"
            if pixels > pil_limit:
                document.warnings.append(
                    f"画面 {pixels / 1e6:.0f}M 像素超过 Pillow 常规上限"
                    f"（{pil_limit / 1e6:.1f}M），可能存在解压风险/高内存")
            for index in range(frames):
                try:
                    source.seek(index)
                    for name in _scan_geo_tags(source):
                        if name not in geo_names:
                            geo_names.append(name)
                    width, height = float(source.size[0]), float(source.size[1])
                    rgb = source.convert("RGB")
                    page = Page(index=index, width=width, height=height,
                                ref={"frame": index})
                    page.blocks = _ocr_blocks(rgb, index, ctx, document.warnings,
                                              with_quad=True)
                    document.pages.append(page)
                    _scan_marks(rgb, index, ctx, document, scale=1.0)
                except Exception as exc:
                    document.warnings.append(f"第 {index + 1} 帧读取失败：{exc}")
            tags = dict(source.tag_v2.items()) if hasattr(source, "tag_v2") else {}
            present = [GEO_TAG_NAMES[t] for t in GEO_TAG_NAMES if t in tags]
            kind = "real" if present else "none"
            if present:
                try:
                    if g._classify_tags(tags) == "local":
                        kind = "local"
                except Exception:
                    pass
            if kind == "real":
                document.meta["geo_tags"] = present
                document.meta["geo_source_tags"] = present
                document.warnings.append(
                    "检测到地理参考（" + "、".join(present) + "），导出时将剥离")
                transform = None
                try:
                    transform = g._parse_geotransform(tags)
                except Exception:
                    transform = None
                if transform:
                    document.extra["geo_transform"] = {
                        "crs": "未解析（Pillow 回退）",
                        "epsg": None,
                        "transform": [round(float(v), 9) for v in transform],
                        "size": [int(document.pages[0].width),
                                 int(document.pages[0].height)],
                    }
            elif kind == "local":
                document.meta["geo_local_tags"] = present
                document.warnings.append("源文件已是本地/任意 CRS，导出按中性化处理")
            leaks = g.leak_tags_present(set(tags))
            if leaks:
                document.warnings.append(
                    "源文件含元数据（导出时剥离）：" + "、".join(leaks))
        finally:
            try:
                source.close()
            except Exception:
                pass
        if not document.pages:
            raise ParseError("TIFF 没有可读取的帧")
        return document

    # ── 渲染（预览） ──

    def render(self, doc: Document, page: int, mode: str = "orig") -> bytes | None:
        path = Path((doc.extra or {}).get("out_path") or doc.path) if mode == "out" else Path(doc.path)
        g = _georef()
        if g.has_rasterio() and path.is_file():
            try:
                structure = g.scan_tiff(path)
                if not structure.error and structure.frames <= 1:
                    with g.rasterio.open(path) as src:
                        rgb, _scale = _rasterio_read_preview(src, MAX_RENDER_EDGE)
                    from io import BytesIO

                    buffer = BytesIO()
                    rgb.save(buffer, format="PNG")
                    return buffer.getvalue()
            except Exception:
                pass
        return _render_image_bytes(doc, page, mode)

    # ── 导出 ──

    def export(self, doc: Document, entities: list[Entity],
               decisions: dict[str, Decision], out_path: Path,
               options: dict) -> ExportResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:  # 覆盖旧成品（含上次失败留下的半成品），rasterio 打不开损坏文件
            if out_path.exists():
                out_path.unlink()
        except OSError as exc:
            raise ExportError(f"无法覆盖已有成品 {out_path.name}：{exc}") from exc
        options = options or {}
        warnings: list[str] = []
        g = _georef()
        source = Path(doc.path)
        if not source.is_file():
            raise ExportError(f"原文件不存在：{source}")

        mode = str(options.get("geo_mode") or g.geo_mode()).strip().lower()
        mode = "strip" if mode in ("strip", "none", "remove", "off") else "neutralized"
        try:
            structure = g.scan_tiff(source)
            ref = g.read_georef(source, structure)
        except Exception as exc:
            structure = g.TiffStructure(error=f"{type(exc).__name__}: {exc}")
            ref = g.GeoRef()
        single_frame = (not structure.error and structure.frames <= 1) or structure.frames == 0
        can_raster = g.has_rasterio() and single_frame

        geo_params: dict | None = None
        if can_raster and mode == "neutralized":
            geo_params = g.make_params(ref, seed=options.get("geo_seed"))
        elif can_raster and mode == "strip":
            geo_params = g.strip_params(ref)
        elif ref.has_georef:
            # Pillow 回退：只能剥离，无法中性化
            geo_params = g.strip_params(ref)
            warnings.append(
                "未安装/无法使用 rasterio 或文件为多帧 TIFF：Pillow 回退模式，"
                "已剥离地理参考与元数据，但**未做坐标中性化**；"
                "需要中性化请安装 rasterio（见 docs/impl/tiff.md）")
        if geo_params:
            issues = g.validate_params(geo_params)
            if issues:
                raise ExportError("坐标中性化参数异常：" + "；".join(issues))
            doc.extra["geo_params"] = geo_params

        try:
            if can_raster:
                self._export_with_rasterio(doc, entities, decisions, out_path,
                                           options, warnings, structure, ref, geo_params, mode)
            else:
                self._export_with_pillow(doc, entities, decisions, out_path, warnings)
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError(f"TIFF 导出失败：{exc}") from exc

        _remove_sidecars(out_path, warnings)
        expect_neutralized = bool(
            geo_params and geo_params.get("method") in ("translate+rotate", "translate+rotate+scale")
            and mode == "neutralized" and can_raster)
        try:
            geo_issues = g.verify_exported_geo(
                out_path, expect_neutralized=expect_neutralized, params=geo_params)
        except Exception as exc:
            geo_issues = [f"复检异常：{exc}"]
        if geo_issues:
            raise ExportError("导出后地理/元数据复检失败：" + "；".join(geo_issues))
        # 兼容旧接口：Pillow 回退时再扫一遍 Geo 标签
        if not can_raster:
            leftovers = _verify_geo_stripped(out_path)
            if leftovers:
                raise ExportError("导出后仍检测到地理标签：" + "、".join(leftovers))
        if expect_neutralized:
            doc.meta["geo_neutralized"] = True
            warnings.append(
                "已做坐标中性化：随机平移+旋转，成品为本地任意 CRS"
                f"（θ={geo_params.get('theta_deg')}°，dx={geo_params.get('dx')}m，"
                f"dy={geo_params.get('dy')}m）；参数已交上层加密保存")
        elif mode == "strip" and can_raster:
            warnings.append("按 geo_mode=strip 导出：成品不含任何 Geo 标签，坐标不可还原")
        doc.extra["out_path"] = str(out_path)
        try:
            size = out_path.stat().st_size
        except OSError:
            size = 0
        return ExportResult(path=str(out_path), warnings=warnings, size=size)

    # ── rasterio 导出（大图/位深/中性化） ──

    def _export_with_rasterio(self, doc: Document, entities: list[Entity],
                              decisions: dict[str, Decision], out_path: Path,
                              options: dict, warnings: list[str],
                              structure, ref, geo_params: dict | None,
                              mode: str) -> None:
        g = _georef()
        from rasterio.windows import Window

        with g.rasterio.open(doc.path) as src:
            width, height = int(src.width), int(src.height)
            count = int(src.count)
            dtypes = list(src.dtypes or ("uint8",))
            dtype = dtypes[0] if dtypes else "uint8"
            if any(str(d) != str(dtype) for d in dtypes):
                warnings.append("源 TIFF 各波段 dtype 不一致，按第一波段 dtype 统一写出")
            compression = str(options.get("tiff_compress") or EXPORT_COMPRESS).lower()
            if compression in ("jpeg", "jpg") and (dtype != "uint8" or count not in (1, 3)):
                compression = "deflate"
                warnings.append("源位深/波段不支持 JPEG 压缩，输出改用 DEFLATE")
            predictor = None
            if compression in ("deflate", "lzw", "zstd"):
                if dtype in ("uint8", "int8", "uint16", "int16"):
                    predictor = 2
                elif dtype.startswith("float"):
                    predictor = 3
            profile: dict[str, Any] = {
                "driver": "GTiff",
                "width": width,
                "height": height,
                "count": count,
                "dtype": dtype,
                "compress": compression,
                "BIGTIFF": "IF_SAFER",
            }
            # GTiff 分块必须是 16 的倍数；小图不强求 tiled
            if width >= 256 and height >= 256:
                block_x = max(16, (min(EXPORT_BLOCK, width) // 16) * 16)
                block_y = max(16, (min(EXPORT_BLOCK, height) // 16) * 16)
                profile.update(tiled=True, blockxsize=block_x, blockysize=block_y)
            else:
                profile["tiled"] = False
            if predictor is not None:
                profile["predictor"] = predictor
            if count >= 3 and dtype in ("uint8", "uint16"):
                try:
                    profile["photometric"] = "RGB"
                except Exception:
                    pass
            if count > 3:
                warnings.append(
                    f"源 TIFF 为 {count} 波段：前 3 波段按 RGB 处理，第 4+ 波段原样保留；"
                    "RGBN/alpha 语义未单独校验")
            if geo_params and mode == "neutralized" and geo_params.get("method") != "strip":
                profile["crs"] = g.make_local_crs()
                dst = geo_params.get("dst_transform")
                if dst:
                    profile["transform"] = g.rasterio.transform.Affine(*[float(v) for v in dst])
            # strip 模式：不要 crs / transform（rasterio 默认 identity）
            nodata = src.nodata
            if nodata is not None:
                profile["nodata"] = nodata
            items = _collect_paint_items(doc, entities, decisions)
            black_count = 0
            text_count = 0
            warn_once = {"nonrgb": False}

            with g.rasterio.open(out_path, "w", **profile) as dst:
                for row in range(0, height, EXPORT_WINDOW):
                    for col in range(0, width, EXPORT_WINDOW):
                        w = min(EXPORT_WINDOW, width - col)
                        h = min(EXPORT_WINDOW, height - row)
                        win = Window(col, row, w, h)
                        data = src.read(window=win)
                        black_n, text_n = _paint_window(
                            data, win, items, warnings, warn_once)
                        black_count += black_n
                        text_count += text_n
                        dst.write(data, window=win)
                if EXPORT_OVERVIEWS:
                    levels = [f for f in (2, 4, 8, 16, 32) if f <= max(width, height) // 512]
                    if levels:
                        try:
                            from rasterio.enums import Resampling

                            dst.build_overviews(levels, Resampling.average)
                        except Exception as exc:
                            warnings.append(f"生成金字塔失败（不影响导出）：{exc}")
        if geo_params and geo_params.get("method") in (
                "translate+rotate", "translate+rotate+scale"):
            warnings.append(
                "rasterio 路线：保留原 dtype/波段，写出本地任意 CRS + 内部金字塔；"
                f"换名/涂黑处理文本框 {text_count} 个、涂黑框 {black_count} 个")
        else:
            warnings.append(
                "rasterio 路线 strip 模式：保留原 dtype/波段，成品不含 Geo 标签")

    # ── Pillow 导出（回退：只剥离，不中性化） ──

    def _export_with_pillow(self, doc: Document, entities: list[Entity],
                            decisions: dict[str, Decision], out_path: Path,
                            warnings: list[str]) -> None:
        try:
            source = Image.open(doc.path)
            source.load()
        except Exception as exc:
            raise ExportError(f"无法打开 TIFF：{exc}") from exc
        try:
            frame_count = int(getattr(source, "n_frames", 1) or 1)
            new_info = _safe_tiffinfo(source)
            targets = max(len(doc.pages), frame_count, 1)
            frames: list[Image.Image] = []
            for index in range(targets):
                try:
                    source.seek(min(index, frame_count - 1))
                    frame = source.convert("RGB")
                except Exception as exc:
                    warnings.append(f"第 {index + 1} 帧读取失败：{exc}")
                    continue
                # 清掉 ICC/XMP 等残留（convert 会带 info）
                try:
                    frame.info.pop("icc_profile", None)
                    frame.info.pop("xmp", None)
                    frame.info.pop("exif", None)
                except Exception:
                    pass
                _apply_decisions(frame, doc, index, entities, decisions, warnings)
                frames.append(frame)
        finally:
            try:
                source.close()
            except Exception:
                pass
        if not frames:
            raise ExportError("TIFF 没有可导出的帧")
        save_kwargs: dict[str, Any] = {"format": "TIFF", "compression": "tiff_lzw"}
        if len(frames) > 1:
            save_kwargs["save_all"] = True
            save_kwargs["append_images"] = frames[1:]
        try:
            frames[0].save(str(out_path), tiffinfo=new_info, **save_kwargs)
        except Exception as exc:
            warnings.append(f"写入 TIFF 标签失败（{exc}），已降级为普通 TIFF")
            try:
                frames[0].save(str(out_path), **save_kwargs)
            except Exception as exc2:
                raise ExportError(f"保存 TIFF 失败：{exc2}") from exc2


# ────────────────────────── 导出涂黑/换名辅助 ──────────────────────────

def _collect_paint_items(doc: Document, entities: list[Entity],
                         decisions: dict[str, Decision]) -> list[dict]:
    """收集第 0 页上所有待处理矩形（像素坐标），供窗口化导出使用。"""
    items: list[dict] = []
    if not doc.pages:
        return items
    page = doc.pages[0]
    block_page: dict[str, int] = {}
    for candidate in doc.pages:
        for block in candidate.blocks:
            block_page[block.id] = candidate.index
    page_blocks = {block.id: block for block in page.blocks}
    for entity in entities or []:
        decision = (decisions or {}).get(entity.id) or entity.decision
        if decision is None:
            continue
        if decision.strategy == "keep" and not decision.black:
            continue
        for span in entity.spans or []:
            box = None
            if span.block_id:
                page_no = block_page.get(span.block_id)
                if page_no is None or page_no != 0:
                    continue
                block = page_blocks.get(span.block_id)
                if block is None:
                    continue
                box = _span_box_in_block(span, block, page)
            elif span.rect:
                if span.page is not None and int(span.page) != 0:
                    continue
                box = _denormalize_rect(span.rect, page)
            if box is None:
                continue
            items.append({"box": box, "decision": decision})
    return items


def _is_black_decision(decision: Decision) -> bool:
    replacement = (decision.replacement or "").replace("\r", " ").replace("\n", " ").strip()
    return (bool(decision.black) or decision.strategy in ("black", "drop")
            or not replacement)


def _fill_black_rect(data: np.ndarray, box: tuple[float, float, float, float]) -> None:
    """把窗口数组（count, h, w）里矩形范围内所有波段置 0（涂黑），自动裁剪。"""
    try:
        h, w = int(data.shape[1]), int(data.shape[2])
        x0 = max(0, min(w, int(round(box[0]))))
        y0 = max(0, min(h, int(round(box[1]))))
        x1 = max(0, min(w, int(round(box[2]))))
        y1 = max(0, min(h, int(round(box[3]))))
        if x1 <= x0 or y1 <= y0:
            return
        data[:, y0:y1, x0:x1] = 0
    except Exception:
        return


def _paint_window(data: np.ndarray, window, items: list[dict],
                  warnings: list[str], warn_once: dict) -> tuple[int, int]:
    """在 rasterio 读出的窗口数组上执行涂黑/换名，返回 (黑框数, 文本框数)。"""
    black_n = 0
    text_n = 0
    if not items:
        return black_n, text_n
    try:
        ox, oy = int(window.col_off), int(window.row_off)
        win_w, win_h = int(window.width), int(window.height)
        win_box = (ox, oy, ox + win_w, oy + win_h)
        count = int(data.shape[0])
        dtype = data.dtype
        for item in items:
            box = item["box"]
            decision = item["decision"]
            if (box[2] <= win_box[0] or box[0] >= win_box[2]
                    or box[3] <= win_box[1] or box[1] >= win_box[3]):
                continue
            if _is_black_decision(decision):
                _fill_black_rect(data, (box[0] - ox, box[1] - oy,
                                        box[2] - ox, box[3] - oy))
                black_n += 1
                continue
            # 换名：只有 8bit 多波段才写替换文字；其余退化为涂黑 + warning
            if dtype != np.uint8 or count < 3:
                if not warn_once.get("nonrgb"):
                    warn_once["nonrgb"] = True
                    warnings.append(
                        "源影像非 8bit RGB：换名文本以涂黑代替（保持位深/波段不变）")
                _fill_black_rect(data, (box[0] - ox, box[1] - oy,
                                        box[2] - ox, box[3] - oy))
                black_n += 1
                continue
            # 文本框只在“中心落在本窗口”的窗口里绘制，避免跨窗口重复/截断
            cx = (box[0] + box[2]) / 2.0
            cy = (box[1] + box[3]) / 2.0
            if not (win_box[0] <= cx < win_box[2] and win_box[1] <= cy < win_box[3]):
                continue
            try:
                rgb = np.ascontiguousarray(np.transpose(data[:3], (1, 2, 0)))
                pil = Image.fromarray(rgb, "RGB")
                from PIL import ImageDraw

                draw = ImageDraw.Draw(pil)
                _paint_box(pil, draw, (box[0] - ox, box[1] - oy,
                                       box[2] - ox, box[3] - oy),
                           decision, warnings)
                painted = np.asarray(pil)
                if painted.shape == data[:3].transpose(1, 2, 0).shape:
                    data[:3] = np.transpose(painted, (2, 0, 1))
                    text_n += 1
                else:  # pragma: no cover - 正常不会发生
                    _fill_black_rect(data, (box[0] - ox, box[1] - oy,
                                            box[2] - ox, box[3] - oy))
                    black_n += 1
            except Exception as exc:
                warnings.append(f"窗口换名绘制失败，已涂黑：{exc}")
                _fill_black_rect(data, (box[0] - ox, box[1] - oy,
                                        box[2] - ox, box[3] - oy))
                black_n += 1
    except Exception as exc:
        warnings.append(f"窗口绘制异常：{exc}")
    return black_n, text_n


registry.register_parser(TiffParser)
