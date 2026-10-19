# -*- coding: utf-8 -*-
"""地理参考读取、坐标中性化数学与 TIFF 结构扫描（正射 TIF P0）。

设计要点
--------
* **可选依赖**：``rasterio``（BSD-3）/``pyproj``（MIT）只在本模块里惰性 import。
  没装时 ``HAS_RASTERIO`` 为 False，由 ``parsers/tiff_parser.py`` 回退 Pillow
  并给出明确 warning（不静默失败）。
* **只做纯逻辑，不写成品**：读地理参考、生成/应用/逆变换参数、低层 TIFF 结构
  扫描（不解码像素）。写文件由 ``tiff_parser.py`` 负责。
* **中性化 v1（已批准 D1/D2/D3）**：随机平移 (dx,dy) + 旋转 θ + 可选缩放；
  成品写 LOCAL_CS 本地任意 CRS（GIS 可打开），参数写入
  ``doc.extra["geo_params"]``（JSON 可序列化），jobs 自动入加密 vault。
* **可还原**：参数里保留 ``src_transform`` / ``src_crs_wkt`` / 锚点，反向公式
  见 :func:`local_to_world` / :func:`world_to_local` / :func:`pixel_to_local`。

代码约定：仿射变换统一用 6 元组 ``(a, b, c, d, e, f)``：
    x_world = a * x_pixel + b * y_pixel + c
    y_world = d * x_pixel + e * y_pixel + f
"""
from __future__ import annotations

import math
import os
import random
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ────────────────────────── 可选依赖 ──────────────────────────

try:  # pragma: no cover - 环境已装
    import rasterio
    from rasterio.transform import Affine as _RioAffine

    HAS_RASTERIO = True
except Exception:  # pragma: no cover - 离线精简环境
    rasterio = None  # type: ignore[assignment]
    _RioAffine = None  # type: ignore[assignment]
    HAS_RASTERIO = False

try:  # pragma: no cover
    import pyproj

    HAS_PYPROJ = True
except Exception:  # pragma: no cover
    pyproj = None  # type: ignore[assignment]
    HAS_PYPROJ = False


def has_rasterio() -> bool:
    return HAS_RASTERIO


def has_pyproj() -> bool:
    return HAS_PYPROJ


# ────────────────────────── 常量 ──────────────────────────

#: 需要识别的真实地理参考标签（GeoTIFF）
GEO_TAG_NAMES: dict[int, str] = {
    33550: "ModelPixelScaleTag",
    33922: "ModelTiepointTag",
    34264: "ModelTransformationTag",
    34735: "GeoKeyDirectoryTag",
    34736: "GeoDoubleParamsTag",
    34737: "GeoAsciiParamsTag",
    42112: "GDAL_METADATA",
    42113: "GDAL_NODATA",
    50844: "RPC",
}

#: 只要有这些标签之一，就认为源文件可能带真实地理参考
REAL_GEOREF_TAGS: frozenset[int] = frozenset({33550, 33922, 34264, 34735, 50844})

#: 中性化成品允许出现的“本地 CRS”标签（不含原 EPSG 语义）
LOCAL_GEOREF_TAGS: frozenset[int] = frozenset({33550, 33922, 34264, 34735, 34736, 34737})

#: 明确是“元数据类”的标签：中性化后必须不存在
META_LEAK_TAGS: dict[int, str] = {
    700: "XMP",
    33723: "IPTC",
    34665: "EXIF_IFD",
    34675: "ICC_PROFILE",
    34853: "GPS_IFD",
    270: "ImageDescription",
    271: "Make",
    272: "Model",
    305: "Software",
    306: "DateTime",
    315: "Artist",
    33432: "Copyright",
}

#: 中性化成品的本地任意 CRS（LOCAL_CS，声明为米制任意直角系）
LOCAL_CRS_NAME = "Sanitized_Data local arbitrary"
LOCAL_CRS_WKT = (
    'LOCAL_CS["Sanitized_Data local arbitrary",'
    'LOCAL_DATUM["sanitized local",0],'
    'UNIT["metre",1],'
    'AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
)

#: 中性化参数版本
PARAMS_VERSION = 1


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def geo_mode() -> str:
    """导出地理处理模式：``neutralized``（默认，写本地任意 CRS）或 ``strip``。"""
    raw = (os.environ.get("YINZHAN_TIFF_GEO_MODE", "neutralized") or "neutralized").strip().lower()
    return "strip" if raw in ("strip", "none", "remove", "off") else "neutralized"


# ────────────────────────── 仿射数学 ──────────────────────────

def affine_identity() -> tuple[float, float, float, float, float, float]:
    return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def affine_is_identity(tr: tuple[float, ...] | list[float] | None) -> bool:
    if not tr or len(tr) < 6:
        return True
    return all(abs(float(tr[i]) - v) < 1e-12 for i, v in enumerate(
        (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)))


def affine_apply(tr: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = (float(v) for v in tr[:6])
    return a * x + b * y + c, d * x + e * y + f


def affine_mult(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, float, float, float, float, float]:
    """返回 ``left ∘ right``（先 right 后 left）的 6 元组。"""
    a1, b1, c1, d1, e1, f1 = (float(v) for v in left[:6])
    a2, b2, c2, d2, e2, f2 = (float(v) for v in right[:6])
    return (a1 * a2 + b1 * d2, a1 * b2 + b1 * e2, a1 * c2 + b1 * f2 + c1,
            d1 * a2 + e1 * d2, d1 * b2 + e1 * e2, d1 * c2 + e1 * f2 + f1)


def affine_invert(tr: tuple[float, ...]) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = (float(v) for v in tr[:6])
    det = a * e - b * d
    if abs(det) < 1e-18:
        raise ValueError("仿射矩阵不可逆（行列式≈0）")
    ia, ib = e / det, -b / det
    id_, ie = -d / det, a / det
    return (ia, ib, -(ia * c + ib * f), id_, ie, -(id_ * c + ie * f))


def affine_scale_translate_rotate(theta_deg: float, scale: float,
                                  tx: float, ty: float) -> tuple[float, float, float, float, float, float]:
    """世界坐标（以锚点为原点）→ 本地坐标：先旋转+缩放，再平移。"""
    r = math.radians(float(theta_deg))
    cos_r, sin_r = math.cos(r), math.sin(r)
    s = float(scale)
    return (s * cos_r, -s * sin_r, tx, s * sin_r, s * cos_r, ty)


def affine_bbox_center(tr: tuple[float, ...], width: int, height: int) -> tuple[float, float]:
    """像素范围四角在世界坐标下的包围盒中心。"""
    pts = [affine_apply(tr, x, y) for x, y in
           ((0.0, 0.0), (float(width), 0.0), (0.0, float(height)),
            (float(width), float(height)))]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


# ────────────────────────── 低层 TIFF 结构扫描 ──────────────────────────

@dataclass
class TiffStructure:
    """不解码像素的 TIFF 结构信息（多帧/尺寸/顶层 tag）。"""

    frames: int = 0
    width: int = 0
    height: int = 0
    #: 第一帧（非 overview）的 IFD tag id 集合
    first_tags: set[int] = field(default_factory=set)
    #: 所有帧出现过的 tag id 并集
    all_tags: set[int] = field(default_factory=set)
    #: 各“页”帧 (tag id 集合)，长度=frames；内部 overview 的 IFD 不计入
    frame_tags: list[set[int]] = field(default_factory=list)
    #: 第一页若干关键 tag 的解析值（33550/33922/34264/34735/256/257/258/259/277/339/254）
    first_values: dict[int, Any] = field(default_factory=dict)
    #: 跳过的 overview IFD 数量
    overview_frames: int = 0
    bigtiff: bool = False
    error: str = ""

    @property
    def pixels(self) -> int:
        return max(0, int(self.width)) * max(0, int(self.height))

    def frame_pixels(self) -> int:
        return self.pixels

    def georef_tags(self, tags: set[int] | None = None) -> list[str]:
        src = self.first_tags if tags is None else tags
        return [GEO_TAG_NAMES[t] for t in sorted(GEO_TAG_NAMES) if t in src]


#: 需要从第一页 IFD 里解析出值的 tag（判断 CRS 类型/尺寸/位深）
_VALUE_TAGS: frozenset[int] = frozenset(
    {254, 256, 257, 258, 259, 277, 339, 33550, 33922, 34264, 34735, 42112, 42113})


def _read_tag_value(fh: Any, endian: str, typ: int, count: int,
                    field_raw: bytes, bigtiff: bool) -> Any:
    """读取 IFD 条目的值：内联小值直接用字段，否则按偏移 seek 读。"""
    if count <= 0 or count > 256:
        return None
    size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}.get(typ)
    if not size:
        return None
    total = size * count
    inline = 8 if bigtiff else 4
    if total <= inline:
        payload = field_raw[:total]
    else:
        try:
            if bigtiff:
                offset = struct.unpack(endian + "Q", field_raw[:8])[0]
            else:
                offset = struct.unpack(endian + "I", field_raw[:4])[0]
            fh.seek(int(offset))
            payload = fh.read(total)
        except Exception:
            return None
        if len(payload) < total:
            return None
    try:
        if typ == 2:
            return payload.split(b"\x00")[0].decode("ascii", "replace")
        if typ == 3:
            return tuple(struct.unpack(endian + "%dH" % count, payload))
        if typ == 4:
            return tuple(struct.unpack(endian + "%dI" % count, payload))
        if typ == 8:
            return tuple(struct.unpack(endian + "%dh" % count, payload))
        if typ == 9:
            return tuple(struct.unpack(endian + "%di" % count, payload))
        if typ == 12:
            return tuple(struct.unpack(endian + "%dd" % count, payload))
        if typ == 1:
            return bytes(payload)
    except struct.error:
        return None
    return None


def _unpack_inline(endian: str, typ: int, count: int, raw_field: bytes) -> Any:
    """尽量把 IFD 条目的值读出来；只处理小数组/标量，读不出返回 None。"""
    if count <= 0 or count > 32:
        return None
    size = {1: 1, 2: 1, 3: 2, 4: 4, 6: 1, 7: 1, 8: 2, 9: 4, 11: 4, 12: 8}.get(typ)
    if not size or size * count > len(raw_field):
        return None
    try:
        if typ == 2:
            return raw_field[:count].split(b"\x00")[0].decode("ascii", "replace")
        if typ in (3,):
            return tuple(struct.unpack(endian + "%dH" % count, raw_field[:size * count]))
        if typ in (4,):
            return tuple(struct.unpack(endian + "%dI" % count, raw_field[:size * count]))
        if typ == 8:
            return tuple(struct.unpack(endian + "%dh" % count, raw_field[:size * count]))
        if typ == 9:
            return tuple(struct.unpack(endian + "%di" % count, raw_field[:size * count]))
        if typ == 12:
            return tuple(struct.unpack(endian + "%dd" % count, raw_field[:size * count]))
    except struct.error:
        return None
    return None


def scan_tiff(path: str | Path) -> TiffStructure:
    """只读 TIFF 头/IFD 链表，拿页数、尺寸、tag id；不碰像素数据。

    GDAL 内部 overview（tag 254 NewSubfileType 的 reduced-resolution 位）不计入
    ``frames``，否则带金字塔的 GeoTIFF 会被误判成多帧。

    失败时返回带 ``error`` 的空结构（不抛异常，调用方据此走 Pillow 回退）。
    """
    structure = TiffStructure()
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
            if len(head) < 8:
                structure.error = "文件过短"
                return structure
            if head[:2] == b"II":
                endian = "<"
            elif head[:2] == b"MM":
                endian = ">"
            else:
                structure.error = "不是 TIFF（缺少 II/MM 字节序标记）"
                return structure
            magic = struct.unpack(endian + "H", head[2:4])[0]
            if magic == 42:
                off = struct.unpack(endian + "I", head[4:8])[0]
                bigtiff = False
            elif magic == 43:
                if len(head) < 16:
                    structure.error = "BigTIFF 头不完整"
                    return structure
                off = struct.unpack(endian + "Q", head[8:16])[0]
                bigtiff = True
            else:
                structure.error = f"未知 TIFF magic={magic}"
                return structure

            seen: set[int] = set()
            next_off = int(off)
            file_size = Path(path).stat().st_size
            first_page = True
            while next_off and next_off not in seen and len(seen) < 8192:
                seen.add(next_off)
                if next_off >= file_size:
                    structure.error = "IFD 偏移越界"
                    break
                fh.seek(next_off)
                if bigtiff:
                    raw = fh.read(8)
                    if len(raw) < 8:
                        break
                    n_entries = struct.unpack(endian + "Q", raw)[0]
                    entries_off = next_off + 8
                    entry_size, count_size = 20, 8
                else:
                    raw = fh.read(2)
                    if len(raw) < 2:
                        break
                    n_entries = struct.unpack(endian + "H", raw)[0]
                    entries_off = next_off + 2
                    entry_size, count_size = 12, 4
                if n_entries <= 0 or n_entries > 4096:
                    structure.error = f"IFD 条目数异常：{n_entries}"
                    break
                block_len = n_entries * entry_size + count_size
                fh.seek(entries_off)
                data = fh.read(block_len)
                if len(data) < block_len:
                    structure.error = "IFD 数据不完整"
                    break
                tags: set[int] = set()
                values: dict[int, Any] = {}
                for i in range(n_entries):
                    base = i * entry_size
                    tag = struct.unpack(endian + "H", data[base:base + 2])[0]
                    typ = struct.unpack(endian + "H", data[base + 2:base + 4])[0]
                    if bigtiff:
                        count = struct.unpack(endian + "Q", data[base + 4:base + 12])[0]
                        field_raw = data[base + 12:base + 20]
                    else:
                        count = struct.unpack(endian + "I", data[base + 4:base + 8])[0]
                        field_raw = data[base + 8:base + 12]
                    tags.add(tag)
                    if tag == 254 or (first_page and tag in _VALUE_TAGS):
                        # 254 每页都要读：判断内部 overview（reduced-resolution 位）
                        value = _read_tag_value(fh, endian, typ, count, field_raw, bigtiff)
                        if value is not None:
                            values[tag] = value
                structure.all_tags |= tags
                subfile_type = 0
                if 254 in values:
                    try:
                        subfile_type = int(values[254][0])
                    except Exception:
                        subfile_type = 0
                is_overview = bool(subfile_type & 1)
                if is_overview:
                    structure.overview_frames += 1
                else:
                    structure.frame_tags.append(tags)
                    structure.frames += 1
                    if first_page:
                        structure.first_tags = set(tags)
                        structure.first_values = values
                        try:
                            structure.width = int(values[256][0])
                            structure.height = int(values[257][0])
                        except Exception:
                            structure.width = structure.width or 0
                            structure.height = structure.height or 0
                        first_page = False
                tail = data[n_entries * entry_size:]
                if bigtiff:
                    next_off = struct.unpack(endian + "Q", tail[:8])[0] if len(tail) >= 8 else 0
                else:
                    next_off = struct.unpack(endian + "I", tail[:4])[0] if len(tail) >= 4 else 0
            structure.bigtiff = bigtiff
    except Exception as exc:  # 扫描失败走回退
        structure.error = f"{type(exc).__name__}: {exc}"
    return structure


def leak_tags_present(tags: set[int]) -> list[str]:
    return [name for tid, name in sorted(META_LEAK_TAGS.items()) if tid in tags]


def georef_tag_names(tags: set[int]) -> list[str]:
    return [GEO_TAG_NAMES[t] for t in sorted(GEO_TAG_NAMES) if t in tags]


# ────────────────────────── 地理参考读取 ──────────────────────────

@dataclass
class GeoRef:
    """读出来的地理参考（真实或本地）。"""

    kind: str = "none"                       # real | local | none
    crs_wkt: str = ""
    crs_desc: str = ""
    epsg: int | None = None
    transform: tuple[float, float, float, float, float, float] | None = None
    width: int = 0
    height: int = 0
    frames: int = 1
    source_tags: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def has_georef(self) -> bool:
        return self.kind in ("real", "local") and self.transform is not None


def _crs_classify(crs_wkt: str) -> str:
    head = (crs_wkt or "").lstrip().upper()
    if not head:
        return "none"
    if head.startswith("LOCAL_CS") or head.startswith("ENGCRS") or "SANITIZED_DATA LOCAL ARBITRARY" in head:
        return "local"
    return "real"


def _crs_description(crs: Any) -> tuple[str, str, int | None]:
    """返回 (wkt, 描述, epsg)。crs 可以是 rasterio.crs.CRS 或 WKT 串。"""
    wkt = ""
    try:
        if crs is None:
            return "", "", None
        if isinstance(crs, str):
            wkt = crs
        else:
            wkt = crs.to_wkt() if hasattr(crs, "to_wkt") else str(crs)
    except Exception:
        wkt = ""
    epsg = None
    if not wkt:
        return "", "", None
    kind = _crs_classify(wkt)
    if kind == "local":
        return wkt, LOCAL_CRS_NAME, None
    if HAS_PYPROJ:
        try:
            pcrs = pyproj.CRS.from_user_input(wkt)
            epsg = pcrs.to_epsg()
            desc = f"EPSG:{epsg}" if epsg else (pcrs.name or "用户自定义 CRS")
            return wkt, str(desc), epsg
        except Exception:
            pass
    if HAS_RASTERIO:
        try:
            epsg = rasterio.crs.CRS.from_wkt(wkt).to_epsg() if hasattr(crs, "to_wkt") else None
        except Exception:
            epsg = None
    return wkt, ("EPSG:%s" % epsg if epsg else "未识别 CRS"), epsg


def read_georef(path: str | Path, structure: TiffStructure | None = None) -> GeoRef:
    """读取文件的地理参考。

    优先 rasterio（支持大图/完整 GeoKey 语义）；不可用时用 Pillow tag_v2
    （只适合小文件，且不解析 CRS，按“真实”保守处理）。
    """
    structure = structure or scan_tiff(path)
    ref = GeoRef(frames=max(1, structure.frames), width=structure.width,
                 height=structure.height,
                 source_tags=georef_tag_names(structure.first_tags))
    if HAS_RASTERIO:
        try:
            with rasterio.open(path) as src:
                ref.width, ref.height = int(src.width), int(src.height)
                ref.frames = max(1, structure.frames)
                tr = src.transform
                transform = (float(tr.a), float(tr.b), float(tr.c),
                             float(tr.d), float(tr.e), float(tr.f))
                crs = src.crs
                wkt, desc, epsg = _crs_description(crs)
                if crs is not None and not affine_is_identity(transform):
                    kind = _crs_classify(wkt) if wkt else "real"
                    ref.kind = "local" if kind == "local" else "real"
                    ref.crs_wkt, ref.crs_desc, ref.epsg = wkt, desc, epsg
                    ref.transform = transform
                    if not ref.source_tags:
                        ref.source_tags = georef_tag_names(structure.first_tags)
                elif structure.first_tags & REAL_GEOREF_TAGS:
                    # 有 Geo 标签/工程坐标但 rasterio 给不出 CRS：按“真实”保守处理，
                    # 仍可做中性化（写本地任意 CRS），只是没有源 CRS 可记录。
                    if not affine_is_identity(transform):
                        ref.kind = "real"
                        ref.transform = transform
                        ref.crs_desc = "源文件无 CRS（仅像素/工程坐标）"
                        ref.note = "有 Geo 标签但 CRS 未识别"
                        if not ref.source_tags:
                            ref.source_tags = georef_tag_names(structure.first_tags)
                    else:
                        ref.kind = "real"
                        ref.note = "有 Geo 标签但未读到有效 geotransform"
                return ref
        except Exception as exc:
            ref.note = f"rasterio 读取失败，尝试 Pillow 回退：{exc}"
    # Pillow 回退
    try:
        from PIL import Image

        with Image.open(path) as image:
            tags = dict(image.tag_v2.items())
        ref.width, ref.height = int(ref.width or image.width), int(ref.height or image.height)
        present = [name for tid, name in sorted(GEO_TAG_NAMES.items()) if tid in tags]
        ref.source_tags = present
        if not (set(GEO_TAG_NAMES) & set(tags)):
            return ref
        transform = _parse_geotransform(tags)
        if transform is None or affine_is_identity(transform):
            if present:
                ref.kind = "real"
                ref.note = "只读到元数据标签，未读到有效 geotransform"
            return ref
        ref.transform = transform
        ref.kind = _classify_tags(tags)
        ref.crs_desc = "Pillow 读取（CRS 未解析）"
    except Exception as exc:
        ref.note = f"{ref.note}；Pillow 回退失败：{exc}".strip("；")
    return ref


def _parse_geotransform(tags: dict[int, Any]) -> tuple[float, float, float, float, float, float] | None:
    """从 TIFF tag 字典还原因像 geotransform（像素 -> 世界）。"""
    try:
        if 34264 in tags:  # ModelTransformation
            m = [float(v) for v in tags[34264]]
            if len(m) >= 16:
                # 4x4 行主序：x' = m0*x + m1*y + m3
                return (m[0], m[1], m[3], m[4], m[5], m[7])
        if 33550 in tags and 33922 in tags:  # PixelScale + Tiepoint
            sx, sy = float(tags[33550][0]), float(tags[33550][1])
            tp = [float(v) for v in tags[33922]]
            # tiepoint: i,j,k, x,y,z → 像素 (i,j) 对应世界 (x,y)
            i, j, x, y = tp[0], tp[1], tp[3], tp[4]
            return (sx, 0.0, x - i * sx, 0.0, -sy, y + j * sy)
    except Exception:
        return None
    return None


def _classify_tags(tags: dict[int, Any]) -> str:
    """用 GeoKeyDirectory/GeoAsciiParams 判断是“真实 CRS”还是“本地任意 CRS”。"""
    ascii_params = tags.get(34737)
    try:
        text = ascii_params if isinstance(ascii_params, str) else str(ascii_params or "")
        if LOCAL_CRS_NAME.lower() in text.lower():
            return "local"
    except Exception:
        pass
    geo = tags.get(34735)
    if not geo:
        return "real"
    try:
        vals = [int(v) for v in geo]
        if len(vals) < 4:
            return "real"
        n_keys = vals[3]
        model_type = None
        geographic_type = None
        projected_type = None
        for k in range(n_keys):
            base = 4 + k * 4
            if base + 3 >= len(vals):
                break
            key_id, _loc, _count, value = vals[base:base + 4]
            if key_id == 1024:
                model_type = value
            elif key_id == 2048:
                geographic_type = value
            elif key_id == 3072:
                projected_type = value
        if model_type == 32767 or geographic_type == 32767 or projected_type == 32767:
            return "local"
    except Exception:
        pass
    return "real"


# ────────────────────────── 中性化参数 ──────────────────────────

def make_local_crs() -> str:
    return LOCAL_CRS_WKT


def make_params(src: GeoRef, *, seed: int | None = None,
                scale: float = 1.0, yaw_range_deg: float | None = None,
                shift_range_m: tuple[float, float] | None = None,
                method: str | None = None) -> dict:
    """为一次导出生成可 JSON 序列化的中性化参数。

    - 旋转 θ ∈ [-yaw_range, +yaw_range]（默认 ±3°，环境变量
      ``YINZHAN_TIFF_YAW_RANGE`` 可调）；
    - 平移 (dx, dy) 在 ``shift_range_m``（默认 200~5000m）内随机取正负；
    - 缩放 ``scale``（默认 1.0，P0 不降采样，保留原分辨率）；
    - 参数里保存源 transform / 源 CRS / 锚点，供 :func:`local_to_world` 可还原。
    """
    if src.transform is None or affine_is_identity(src.transform) or not src.has_georef:
        return {
            "version": PARAMS_VERSION,
            "method": "strip",
            "reason": "源文件没有有效地理参考，只做剥离",
            "crs_before": src.crs_desc or "",
            "crs_after": "",
            "src_size": [int(src.width), int(src.height)],
        }
    yaw_range = _env_float("YINZHAN_TIFF_YAW_RANGE", 3.0) if yaw_range_deg is None else float(yaw_range_deg)
    shift_lo, shift_hi = shift_range_m or (200.0, 5000.0)
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    theta = round(rng.uniform(-yaw_range, yaw_range), 6)
    # 避免 θ 恰好为 0：v1 至少要有旋转
    if abs(theta) < 0.05:
        theta = round(math.copysign(0.5, theta or 1.0), 6)
    scale = float(scale or 1.0)
    dx = round(rng.uniform(shift_lo, shift_hi) * rng.choice((-1, 1)), 3)
    dy = round(rng.uniform(shift_lo, shift_hi) * rng.choice((-1, 1)), 3)
    anchor = affine_bbox_center(src.transform, src.width, src.height)
    to_anchor = (1.0, 0.0, -anchor[0], 0.0, 1.0, -anchor[1])
    local_map = affine_scale_translate_rotate(theta, scale, dx, dy)
    world_to_local = affine_mult(local_map, to_anchor)
    dst_transform = affine_mult(world_to_local, src.transform)
    return {
        "version": PARAMS_VERSION,
        "method": method or ("translate+rotate" if abs(scale - 1.0) < 1e-9 else "translate+rotate+scale"),
        "theta_deg": theta,
        "dx": dx,
        "dy": dy,
        "scale": scale,
        "anchor": [round(anchor[0], 6), round(anchor[1], 6)],
        "crs_before": src.crs_desc or "",
        "crs_before_wkt": src.crs_wkt or "",
        "crs_after": LOCAL_CRS_NAME,
        "dst_crs_wkt": LOCAL_CRS_WKT,
        "src_transform": [round(float(v), 9) for v in src.transform],
        "dst_transform": [round(float(v), 9) for v in dst_transform],
        "src_size": [int(src.width), int(src.height)],
        "invertible": True,
        "note": "只降密不匿名：随机平移+旋转后，地物形状/纹理仍可能被比对定位",
    }


def strip_params(src: GeoRef) -> dict:
    """``geo_mode="strip"``：彻底去参考，参数只做审计记录（不可还原坐标）。"""
    return {
        "version": PARAMS_VERSION,
        "method": "strip",
        "theta_deg": 0.0,
        "dx": 0.0,
        "dy": 0.0,
        "scale": 1.0,
        "crs_before": src.crs_desc or "",
        "crs_after": "",
        "src_size": [int(src.width), int(src.height)],
        "invertible": False,
        "note": "成品不含任何 Geo 标签，坐标不可还原（人看模式）",
    }


def _param_transform(params: dict, key: str) -> tuple[float, ...] | None:
    raw = params.get(key)
    if not isinstance(raw, (list, tuple)) or len(raw) < 6:
        return None
    try:
        return tuple(float(v) for v in raw[:6])
    except (TypeError, ValueError):
        return None


def world_to_local(params: dict, x: float, y: float) -> tuple[float, float]:
    """原始世界坐标 → 中性化本地坐标。"""
    anchor = params.get("anchor") or [0.0, 0.0]
    theta = math.radians(float(params.get("theta_deg", 0.0)))
    scale = float(params.get("scale", 1.0)) or 1.0
    dx, dy = float(params.get("dx", 0.0)), float(params.get("dy", 0.0))
    px, py = float(x) - float(anchor[0]), float(y) - float(anchor[1])
    return (scale * (math.cos(theta) * px - math.sin(theta) * py) + dx,
            scale * (math.sin(theta) * px + math.cos(theta) * py) + dy)


def local_to_world(params: dict, x: float, y: float) -> tuple[float, float]:
    """中性化本地坐标 → 原始世界坐标（可还原，D2）。"""
    anchor = params.get("anchor") or [0.0, 0.0]
    theta = math.radians(float(params.get("theta_deg", 0.0)))
    scale = float(params.get("scale", 1.0)) or 1.0
    dx, dy = float(params.get("dx", 0.0)), float(params.get("dy", 0.0))
    lx, ly = (float(x) - dx) / scale, (float(y) - dy) / scale
    return (math.cos(theta) * lx + math.sin(theta) * ly + float(anchor[0]),
            -math.sin(theta) * lx + math.cos(theta) * ly + float(anchor[1]))


def _check_invertible(params: dict) -> None:
    if params.get("method") == "strip" or not params.get("invertible"):
        raise ValueError("strip 模式不可还原坐标")
    for key in ("src_transform", "dst_transform", "anchor"):
        if key not in params:
            raise ValueError(f"参数缺少 {key}，不可还原")


def pixel_to_local(params: dict, x_px: float, y_px: float) -> tuple[float, float]:
    """原始像素坐标 → 成品本地坐标（用源 transform + 同一套中性化参数）。"""
    _check_invertible(params)
    src_tr = _param_transform(params, "src_transform")
    if src_tr is None:
        raise ValueError("参数缺少 src_transform")
    wx, wy = affine_apply(src_tr, float(x_px), float(y_px))
    return world_to_local(params, wx, wy)


def local_to_pixel(params: dict, x: float, y: float) -> tuple[float, float]:
    """成品本地坐标 → 原始像素坐标（可还原，D2）。"""
    _check_invertible(params)
    src_tr = _param_transform(params, "src_transform")
    if src_tr is None:
        raise ValueError("参数缺少 src_transform")
    wx, wy = local_to_world(params, float(x), float(y))
    inv = affine_invert(src_tr)
    return affine_apply(inv, wx, wy)


def params_to_json(params: dict) -> str:
    """序列化（vault 用；确保纯 JSON 类型）。"""
    import json

    return json.dumps(params, ensure_ascii=False, sort_keys=True)


def validate_params(params: dict) -> list[str]:
    """返回参数问题列表（空=通过）。"""
    issues: list[str] = []
    if not isinstance(params, dict):
        return ["参数不是 dict"]
    if params.get("method") not in ("translate+rotate", "translate+rotate+scale", "strip"):
        issues.append(f"未知 method={params.get('method')!r}")
    if params.get("method") == "strip":
        return issues
    for key in ("theta_deg", "dx", "dy", "scale", "anchor",
                "src_transform", "dst_transform", "crs_after"):
        if key not in params:
            issues.append(f"缺少 {key}")
    try:
        if not math.isfinite(float(params.get("theta_deg", 0.0))):
            issues.append("theta_deg 非有限数")
        if float(params.get("scale", 1.0)) <= 0:
            issues.append("scale 必须 > 0")
        tr = _param_transform(params, "dst_transform")
        if tr is not None:
            affine_invert(tr)  # 不可逆会抛
    except Exception as exc:
        issues.append(f"参数数值异常：{exc}")
    return issues


# ────────────────────────── 成品复检辅助 ──────────────────────────

def verify_exported_geo(path: str | Path, *, expect_neutralized: bool,
                        structure: TiffStructure | None = None,
                        params: dict | None = None) -> list[str]:
    """解析器侧自检：成品里是否还有真实地理参考/泄漏元数据。

    返回问题列表（空=通过）。

    注意：中性化成品写的是 **LOCAL_CS 本地任意 CRS**，物理上仍有
    ModelPixelScale/GeoKeyDirectory 等 Geo 标签结构；这些标签 id 与真实
    GeoTIFF 相同，**必须靠 CRS 语义区分**，不能只看 tag id。
    判定规则：
      * ``expect_neutralized``：CRS 必须是 local（LOCAL_CS/ENGCRS 且无 EPSG），
        且不允许残留 EXIF/GPS/ICC/XMP/IPTC 等标签；
      * ``strip``：不允许任何 Geo/元数据标签。
    """
    issues: list[str] = []
    st = structure or scan_tiff(path)
    tags = set(st.first_tags)
    leak = leak_tags_present(tags)
    if leak:
        issues.append("成品仍含泄漏元数据标签：" + "、".join(leak))

    if not expect_neutralized:
        # strip 模式：物理上不应有任何 Geo 标签
        geo_left = georef_tag_names(tags)
        if geo_left:
            issues.append("strip 成品仍含 Geo 标签：" + "、".join(geo_left))
        return issues

    # neutralized 模式：必须能证明成品 CRS 是“本地任意 CRS”
    crs_local = False
    crs_desc = ""
    if HAS_RASTERIO:
        try:
            with rasterio.open(path) as src:
                wkt, desc, epsg = _crs_description(src.crs)
                crs_desc = desc
                if epsg is not None:
                    issues.append(f"成品 CRS 仍是真实 EPSG:{epsg}")
                elif _crs_classify(wkt) == "local":
                    crs_local = True
                else:
                    issues.append("成品 CRS 不是本地任意 CRS：" + (desc or "unknown"))
                if params is not None and crs_local:
                    dst_tr = _param_transform(params, "dst_transform")
                    if dst_tr is not None:
                        cur = (float(src.transform.a), float(src.transform.b), float(src.transform.c),
                               float(src.transform.d), float(src.transform.e), float(src.transform.f))
                        if any(abs(cur[i] - dst_tr[i]) > 1e-6 for i in range(6)):
                            issues.append("成品 geotransform 与记录的中性化参数不一致")
        except Exception as exc:
            issues.append(f"rasterio 复检失败：{exc}")
    else:
        # 没有 rasterio 时无法写中性化成品；这里按 GeoKey 的 GTModelType 保守判断
        kind = _classify_tags(st.first_values)
        if kind == "local":
            crs_local = True
            crs_desc = LOCAL_CRS_NAME
        else:
            issues.append("缺少 rasterio，无法确认成品为本地任意 CRS")
    if not crs_local and not any("CRS" in i or "rasterio" in i for i in issues):
        issues.append("中性化成品未检出本地任意 CRS")
    if not (tags & LOCAL_GEOREF_TAGS):
        issues.append("中性化成品缺少 Geo 标签结构（预期 LOCAL_CS + 像素尺度/变换）")
    if crs_local and HAS_RASTERIO:
        try:
            with rasterio.open(path) as src:
                gcps, _ = src.gcps
                if gcps:
                    issues.append("成品仍带 GCP 控制点")
        except Exception:
            pass
    return issues


def read_linked_sidecars(out_path: str | Path) -> list[Path]:
    """列出成品名派生的常见 GIS sidecar（只返回存在的）。"""
    out_path = Path(out_path)
    suffixes = (".tfw", ".prj", ".aux.xml", ".ovr", ".kml", ".rpc", ".rpb",
                ".imd", ".tif.xml", ".xml")
    found: list[Path] = []
    for suffix in suffixes:
        candidates = []
        if suffix == ".tif.xml":
            candidates = [out_path.parent / (out_path.name + ".xml")]
        else:
            candidates = [out_path.with_suffix(suffix)]
            if suffix in (".ovr", ".aux.xml"):
                candidates.append(out_path.parent / (out_path.name + suffix))
        for candidate in candidates:
            try:
                if candidate.is_file():
                    found.append(candidate)
            except OSError:
                continue
    # 去重
    unique: list[Path] = []
    seen = set()
    for p in found:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


__all__ = [
    "HAS_RASTERIO", "HAS_PYPROJ", "GEO_TAG_NAMES", "REAL_GEOREF_TAGS",
    "LOCAL_GEOREF_TAGS", "META_LEAK_TAGS", "LOCAL_CRS_NAME", "LOCAL_CRS_WKT",
    "TiffStructure", "GeoRef", "scan_tiff", "read_georef", "has_rasterio",
    "has_pyproj", "geo_mode", "make_params", "strip_params", "world_to_local",
    "local_to_world", "pixel_to_local", "local_to_pixel", "params_to_json",
    "validate_params", "verify_exported_geo", "read_linked_sidecars",
    "affine_apply", "affine_mult", "affine_invert", "affine_is_identity",
    "affine_bbox_center", "make_local_crs", "leak_tags_present",
    "georef_tag_names",
]
