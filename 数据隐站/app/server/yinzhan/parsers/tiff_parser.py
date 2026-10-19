"""TIFF 解析器（含多页 / GeoTIFF 正射影像）。

解析：逐帧读取像素尺寸并 OCR；识别地理参考标签记入 meta 并告警。
导出：逐帧破坏性重绘后重编码，剥离地理参考与设备元数据，并清理同名 sidecar。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from .. import registry
from ..udm import (
    Decision, Document, Entity, ExportError, ExportResult, Page, ParseContext, ParseError,
)
from .image_parser import (
    _apply_decisions, _new_doc_id, _ocr_blocks, _render_image_bytes,
)

# 需要识别并剥离的地理参考标签（键名写进 doc.meta["geo_tags"]）
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

# 明确允许带进新 tiffinfo 的展示性标签：方向、分辨率、分辨率单位。
# 其余标签（地理参考、设备/版权/EXIF/GPS/XMP/IPTC、结构性 Strips 等）一律不照抄：
# 结构性标签必须由 Pillow 重编码时重新生成，照抄会写出损坏文件。
KEEP_TAGS: tuple[int, ...] = (274, 282, 283, 296)


def _scan_geo_tags(source: Image.Image) -> list[str]:
    """读取一帧上的地理参考标签名。"""
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
    """构造新的 TIFF 标签集：只保留方向/分辨率，其余（含地理、设备）全部不带。"""
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
    """删除成品同名的 .tfw/.aux.xml/.prj（只动输出文件名派生出来的，不碰源文件）。"""
    removed: list[str] = []
    candidates = [
        out_path.with_suffix(".tfw"),
        out_path.with_suffix(".aux.xml"),
        out_path.with_suffix(".prj"),
        out_path.parent / (out_path.name + ".aux.xml"),
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                candidate.unlink()
                removed.append(candidate.name)
        except OSError as exc:
            warnings.append(f"删除 sidecar {candidate.name} 失败：{exc}")
    return removed


def _verify_geo_stripped(out_path: Path) -> list[str]:
    """重新打开成品，确认地理标签确实没了。"""
    try:
        with Image.open(out_path) as image:
            return _scan_geo_tags(image)
    except Exception:
        return []


class TiffParser(registry.Parser):
    """TIFF 解析器：多帧逐页 OCR，导出时剥离地理参考。"""

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

    def parse(self, path: Path, ctx: ParseContext) -> Document:
        path = Path(path)
        try:
            source = Image.open(path)
            source.load()
        except Exception as exc:
            raise ParseError(f"无法打开 TIFF：{exc}") from exc
        doc = Document(doc_id=_new_doc_id(), path=str(path), fmt="tiff")
        geo_names: list[str] = []
        try:
            frames = int(getattr(source, "n_frames", 1) or 1)
            doc.meta["format"] = "TIFF"
            doc.meta["frames"] = frames
            for index in range(frames):
                try:
                    source.seek(index)
                    for name in _scan_geo_tags(source):
                        if name not in geo_names:
                            geo_names.append(name)
                    width, height = float(source.size[0]), float(source.size[1])
                    rgb = source.convert("RGB")
                    page = Page(index=index, width=width, height=height, ref={"frame": index})
                    page.blocks = _ocr_blocks(rgb, index, ctx, doc.warnings, with_quad=True)
                    doc.pages.append(page)
                except Exception as exc:
                    doc.warnings.append(f"第 {index + 1} 帧读取失败：{exc}")
        finally:
            try:
                source.close()
            except Exception:
                pass
        doc.meta["geo_tags"] = geo_names
        if geo_names:
            doc.warnings.append(
                "检测到地理参考（" + "、".join(geo_names) + "），导出时将剥离")
        if not doc.pages:
            raise ParseError("TIFF 没有可读取的帧")
        return doc

    def export(self, doc: Document, entities: list[Entity],
               decisions: dict[str, Decision], out_path: Path,
               options: dict) -> ExportResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
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

        _remove_sidecars(out_path, warnings)
        leftovers = _verify_geo_stripped(out_path)
        if leftovers:
            warnings.append("导出后仍检测到地理标签：" + "、".join(leftovers))
        doc.extra["out_path"] = str(out_path)
        try:
            size = out_path.stat().st_size
        except OSError:
            size = 0
        return ExportResult(path=str(out_path), warnings=warnings, size=size)

    def render(self, doc: Document, page: int, mode: str = "orig") -> bytes | None:
        return _render_image_bytes(doc, page, mode)


registry.register_parser(TiffParser)
