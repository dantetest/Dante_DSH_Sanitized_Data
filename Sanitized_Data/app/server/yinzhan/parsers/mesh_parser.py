"""三维点云 / 网格解析器与导出器（.obj / .ply / .stl / .glb）。

三维文件本身极少被大模型直接消费，但其中经常藏着泄漏点：
    - OBJ 的 ``#`` 注释（工程名、坐标系、项目编号）、``mtllib`` 材质路径、
      ``o/g/usemtl`` 名称；
    - PLY header 的 ``comment`` / ``obj_info`` 行；
    - STL 的 80 字节头部（常被写进文件名、构建时间、作者）；
    - GLB 的 JSON chunk：``asset.extras`` / ``asset.copyright``、各对象 ``name``。
另外，测量/工程模型的顶点常常带大地坐标偏移（坐标绝对值 > 1e5），本身就是
“这份数据在哪个城市”的强提示。

parse
    不加载几何体，只抽取上述文本作为 text Block。OBJ 额外在 attrs 里携带
    ``vmin`` / ``vmax`` / ``vertices`` / ``coordinate_offset``，供 UI 与导出判断。
export
    OBJ / PLY：把顶点坐标减去包围盒中心（重定位到原点），并覆盖注释/名称里的
    敏感串；STL / GLB：二进制透传，STL 头部含敏感串时置零、GLB 只在 JSON chunk
    内做**等字节长度**覆盖（保证 chunk 长度不变），并明确 warning「暂不支持几何
    重定位」。PLY 二进制用 numpy 结构化 dtype 就地改写顶点，OBJ 用流式两遍扫描
    避免把超大模型整个读进内存。

任何格式解析失败都只返回带 warning 的空 Document，不向用户抛异常。
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from ..registry import Parser, register_parser
from ..udm import (
    Block, Decision, Document, Entity, ExportError, ExportResult, Page, ParseContext,
)

try:  # pragma: no cover - 环境已装
    import numpy as np

    _NUMPY_OK = True
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _NUMPY_OK = False

A4_WIDTH = 595.0
A4_HEIGHT = 842.0
BLACK_CHAR = "█"
# 超过这个绝对值就认为顶点带大地坐标 / 工程坐标偏移（WGS84 或地方坐标）
BIG_COORD_THRESHOLD = 100_000.0
# 二进制 PLY 超过这个尺寸时只做头部清理，不载入几何
PLY_BINARY_MEMORY_LIMIT = 256 * 1024 * 1024

# PLY property 类型 -> (numpy dtype 基础码, 字节数)
_PLY_TYPES: dict[str, tuple[str, int]] = {
    "char": ("i1", 1), "int8": ("i1", 1),
    "uchar": ("u1", 1), "uint8": ("u1", 1),
    "short": ("i2", 2), "int16": ("i2", 2),
    "ushort": ("u2", 2), "uint16": ("u2", 2),
    "int": ("i4", 4), "int32": ("i4", 4),
    "uint": ("u4", 4), "uint32": ("u4", 4),
    "float": ("f4", 4), "float32": ("f4", 4),
    "double": ("f8", 8), "float64": ("f8", 8),
    "int64": ("i8", 8), "uint64": ("u8", 8),
}

# GLB JSON 里可能藏文本的对象数组
_GLB_TEXT_ARRAYS = (
    "scenes", "nodes", "meshes", "materials", "animations", "cameras",
    "images", "skins", "textures", "samplers",
)


# ══════════════════════════════ 小工具 ══════════════════════════════

def _decode_line(raw: bytes) -> str:
    """逐行解码：优先 UTF-8，失败退回 GB18030，再失败用 Latin-1 保底。"""
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def _decode_header(raw: bytes) -> str:
    """解码 STL 80 字节头：丢掉 NUL 等不可打印字符。"""
    text = raw.decode("utf-8", "replace")
    return "".join(ch if ch.isprintable() else " " for ch in text).strip()


def _fmt_float(value: float) -> str:
    """尽量短且不丢精度的浮点写法（用于重写顶点坐标）。"""
    if value == 0:
        return "0"
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    text = f"{value:.9f}".rstrip("0").rstrip(".")
    return text or "0"


def _replacement_text(decision: Decision, length: int) -> str | None:
    """把 Decision 翻译成实际写入的字符串；keep 返回 None。"""
    if decision.strategy == "keep":
        return None
    if decision.strategy == "drop":
        return ""
    if decision.black or decision.strategy == "black" or decision.replacement is None:
        return BLACK_CHAR * max(0, length)
    return decision.replacement


def _entity_rules(
    entities: list[Entity], decisions: dict[str, Decision]
) -> list[tuple[str, str, bool]]:
    """整理待替换项：[(原值, 替换文本, 是否涂黑)]，长值优先避免子串误伤。"""
    items: list[tuple[str, str, bool]] = []
    for entity in entities:
        if entity.status == "rejected" or not entity.value:
            continue
        decision = decisions.get(entity.id) or entity.decision
        if decision is None:
            decision = Decision(strategy="black", black=True)
        if decision.strategy == "keep":
            continue
        replacement = _replacement_text(decision, len(entity.value))
        if replacement is None:
            continue
        black = decision.black or decision.strategy == "black" or decision.strategy == "drop"
        items.append((entity.value, replacement, black))
    items.sort(key=lambda item: len(item[0]), reverse=True)
    return items


def _sanitize_text(text: str, rules: list[tuple[str, str, bool]]) -> tuple[str, int]:
    """覆盖文本里的敏感串，返回 (新文本, 命中次数)。"""
    hits = 0
    for value, replacement, _black in rules:
        count = text.count(value)
        if count:
            text = text.replace(value, replacement)
            hits += count
    return text, hits


def _eol_of(raw: bytes) -> bytes:
    if raw.endswith(b"\r\n"):
        return b"\r\n"
    if raw.endswith(b"\n"):
        return b"\n"
    return b""


def _copy_fallback(
    source: Path, out_path: Path, warnings: list[str], reason: str
) -> None:
    """导出失败时原样复制（随后 pipeline.verify 会重新检测并拒绝残留成品）。"""
    try:
        shutil.copyfile(source, out_path)
    except Exception as exc:
        raise ExportError(f"三维文件导出失败且无法复制原文件：{exc}") from exc
    warnings.append(f"{reason}，已原样复制原文件（可能未脱敏，出口自检会拦截）")


def _replace_file(source: Path, target: Path) -> None:
    """原子替换；Windows 上成品可能被杀软/索引器短暂占用，失败后退化为覆盖写。"""
    for attempt in range(6):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if attempt == 5:
                last_error = exc
                break
            time.sleep(0.05 * (attempt + 1))
    try:
        with open(source, "rb") as reader, open(target, "wb") as writer:
            shutil.copyfileobj(reader, writer)
        source.unlink(missing_ok=True)
    except Exception as exc:
        raise OSError(f"替换 {target} 失败：{last_error}；覆盖写也失败：{exc}") from exc


# ══════════════════════════════ OBJ ══════════════════════════════

def _obj_line_kind(text: str) -> str:
    """判断 OBJ 行里可能含文本信息的类型。"""
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith("#"):
        return "comment"
    parts = stripped.split(None, 1)
    key = parts[0].lower() if parts else ""
    if key in ("mtllib", "usemtl", "o", "g"):
        return key
    return ""


def _scan_obj_geometry(path: Path) -> tuple[int, list[float], list[float], int]:
    """流式扫描 OBJ 顶点，返回 (顶点数, vmin, vmax, 坏行数)。"""
    vmin = [float("inf")] * 3
    vmax = [float("-inf")] * 3
    count = 0
    bad = 0
    try:
        with open(path, "rb") as handle:
            for raw in handle:
                line = _decode_line(raw).strip()
                if not line or line[0] == "#":
                    continue
                if line[0] not in "vV" or (len(line) > 1 and not line[1].isspace()):
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                try:
                    values = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError:
                    bad += 1
                    continue
                for axis in range(3):
                    vmin[axis] = min(vmin[axis], values[axis])
                    vmax[axis] = max(vmax[axis], values[axis])
                count += 1
    except Exception:
        raise
    return count, vmin, vmax, bad


def _obj_center(vcount: int, vmin: list[float], vmax: list[float]) -> list[float] | None:
    if vcount <= 0 or any(value in (float("inf"), float("-inf")) for value in vmin + vmax):
        return None
    if _NUMPY_OK:
        low = np.asarray(vmin, dtype="float64")
        high = np.asarray(vmax, dtype="float64")
        return [float(value) for value in (low + high) / 2.0]
    return [(vmin[axis] + vmax[axis]) / 2.0 for axis in range(3)]


# ══════════════════════════════ PLY ══════════════════════════════

def _read_ply_header(path: Path) -> dict[str, Any]:
    """读取 PLY 头部：格式、element/property 布局、原始头行。"""
    raw_lines: list[bytes] = []
    elements: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    fmt = ""
    offset = 0
    with open(path, "rb") as handle:
        first = handle.readline()
        offset += len(first)
        if not first:
            raise ValueError("空文件")
        raw_lines.append(first)
        if first.strip().lower() != b"ply":
            raise ValueError("不是 PLY 文件（缺少 ply 魔数）")
        while True:
            raw = handle.readline()
            if not raw:
                raise ValueError("PLY 头部缺少 end_header")
            offset += len(raw)
            if offset > 8 * 1024 * 1024:
                raise ValueError("PLY 头部异常大")
            raw_lines.append(raw)
            text = _decode_line(raw).strip()
            if not text:
                continue
            parts = text.split()
            key = parts[0].lower()
            if key == "format" and len(parts) >= 2:
                fmt = parts[1].lower()
            elif key == "element" and len(parts) >= 3:
                try:
                    count = int(parts[2])
                except ValueError as exc:
                    raise ValueError(f"PLY element 行非法：{text}") from exc
                current = {"name": parts[1], "count": count, "props": []}
                elements.append(current)
            elif key == "property":
                if current is None:
                    raise ValueError("PLY property 出现在 element 之前")
                if len(parts) >= 5 and parts[1].lower() == "list":
                    current["props"].append(
                        {"type": "list", "count_type": parts[2].lower(),
                         "item_type": parts[3].lower(), "name": parts[4]}
                    )
                elif len(parts) >= 3:
                    current["props"].append({"type": parts[1].lower(), "name": parts[2]})
                else:
                    raise ValueError(f"PLY property 行非法：{text}")
            elif key == "end_header":
                break
    return {
        "format": fmt or "ascii",
        "elements": elements,
        "raw_lines": raw_lines,
        "data_offset": offset,
    }


def _ply_header_text_lines(header: dict[str, Any]) -> Iterator[tuple[int, str]]:
    """产出 header 里 comment / obj_info 行的 (行号, 文本)。"""
    for index, raw in enumerate(header["raw_lines"]):
        text = _decode_line(raw).strip()
        low = text.lower()
        if low.startswith("comment") or low.startswith("obj_info"):
            yield index, text


def _ply_vertex_element(header: dict[str, Any]) -> dict[str, Any] | None:
    for element in header["elements"]:
        if element["name"].lower() == "vertex":
            return element
    return None


def _ply_scalar_size(prop: dict[str, Any]) -> int:
    if prop["type"] == "list":
        raise ValueError("PLY 顶点属性含 list 类型，暂不支持")
    try:
        return _PLY_TYPES[prop["type"]][1]
    except KeyError as exc:
        raise ValueError(f"未知 PLY 属性类型：{prop['type']}") from exc


def _ply_element_stride(element: dict[str, Any]) -> int:
    return sum(_ply_scalar_size(prop) for prop in element["props"])


def _ply_ascii_center(path: Path, header: dict[str, Any]) -> list[float] | None:
    """扫描 ASCII PLY 的 vertex 行，返回包围盒中心。"""
    vertex = _ply_vertex_element(header)
    if vertex is None or vertex["count"] <= 0:
        return None
    prop_names = [prop["name"].lower() for prop in vertex["props"]]
    try:
        x_pos, y_pos, z_pos = (prop_names.index(axis) for axis in ("x", "y", "z"))
    except ValueError:
        return None
    vmin = [float("inf")] * 3
    vmax = [float("-inf")] * 3
    with open(path, "rb") as handle:
        handle.seek(header["data_offset"])
        offset = 0
        for element in header["elements"]:
            for _index in range(int(element["count"])):
                raw = handle.readline()
                if not raw:
                    raise ValueError("PLY 数据行数少于 header 声明")
                if element is vertex:
                    parts = _decode_line(raw).split()
                    if len(parts) < len(prop_names):
                        raise ValueError("PLY vertex 行列数不足")
                    for axis, position in enumerate((x_pos, y_pos, z_pos)):
                        value = float(parts[position])
                        vmin[axis] = min(vmin[axis], value)
                        vmax[axis] = max(vmax[axis], value)
        if any(value in (float("inf"), float("-inf")) for value in vmin + vmax):
            return None
    return [(vmin[axis] + vmax[axis]) / 2.0 for axis in range(3)]


def _ply_binary_vertex_layout(
    header: dict[str, Any], byteorder: str
) -> tuple[int, int, Any] | None:
    """返回 (顶点块字节偏移, 顶点数, numpy structured dtype)；不支持时返回 None。"""
    vertex = _ply_vertex_element(header)
    if vertex is None or vertex["count"] <= 0:
        return None
    if not _NUMPY_OK:
        return None
    offset = 0
    for element in header["elements"]:
        stride = _ply_element_stride(element)
        if element is vertex:
            dtype_fields = []
            for prop in element["props"]:
                if prop["type"] not in _PLY_TYPES:
                    return None
                code = _PLY_TYPES[prop["type"]][0]
                if not code.startswith(("i1", "u1")):
                    code = byteorder + code
                dtype_fields.append((prop["name"], code))
            try:
                dtype = np.dtype(dtype_fields)
            except Exception:
                return None
            return offset, int(element["count"]), dtype
        offset += int(element["count"]) * stride
    return None


def _ply_byteorder(fmt: str) -> str | None:
    if fmt.startswith("binary_little_endian"):
        return "<"
    if fmt.startswith("binary_big_endian"):
        return ">"
    return None


def _ply_build_header(
    header: dict[str, Any], rules: list[tuple[str, str, bool]]
) -> tuple[bytes, int]:
    """重写 PLY 头部：覆盖 comment/obj_info 敏感串，其余行原样保留。"""
    output = bytearray()
    hits = 0
    for raw in header["raw_lines"]:
        text = _decode_line(raw).rstrip("\r\n")
        low = text.lower()
        if low.startswith("comment") or low.startswith("obj_info"):
            new_text, line_hits = _sanitize_text(text, rules)
            hits += line_hits
            if line_hits:
                eol = _eol_of(raw)
                output.extend(new_text.encode("utf-8", "replace"))
                output.extend(eol)
                continue
        output.extend(raw)
    return bytes(output), hits


# ══════════════════════════════ STL ══════════════════════════════

def _stl_is_ascii(data: bytes, total_size: int | None = None) -> bool:
    """判断 STL 是否 ASCII：二进制 STL 总长应等于 84 + 三角形数 × 50。"""
    if not data[:5].lower() == b"solid":
        return False
    total = len(data) if total_size is None else int(total_size)
    if total < 84:
        return True
    try:
        triangles = struct.unpack("<I", data[80:84])[0]
    except Exception:
        return True
    return total != 84 + triangles * 50


# ══════════════════════════════ GLB ══════════════════════════════

def _glb_json_chunk(data: bytes) -> tuple[int, int] | None:
    """返回 JSON chunk 数据在文件中的 (起始偏移, 长度)。"""
    if len(data) < 20 or data[:4] != b"glTF":
        return None
    try:
        _version, total = struct.unpack("<II", data[4:12])
    except struct.error:
        return None
    if total > len(data):
        return None
    position = 12
    limit = min(len(data), total)
    while position + 8 <= limit:
        try:
            chunk_length, = struct.unpack("<I", data[position:position + 4])
        except struct.error:
            return None
        chunk_type = data[position + 4:position + 8]
        start = position + 8
        end = start + chunk_length
        if end > limit:
            return None
        if chunk_type == b"JSON":
            return start, chunk_length
        position = end
    return None


def _walk_glb_strings(node: Any, path: str, depth: int = 0) -> Iterator[tuple[str, str]]:
    """递归收集 JSON 片段里的字符串。"""
    if depth > 8:
        return
    if isinstance(node, str):
        if node.strip():
            yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_glb_strings(value, f"{path}.{key}", depth + 1)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_glb_strings(value, f"{path}[{index}]", depth + 1)


def _json_string_mask(chunk: bytes) -> bytearray:
    """标记 JSON chunk 中位于字符串字面量内部的字节（1=字符串内容）。"""
    mask = bytearray(len(chunk))
    inside = False
    escaped = False
    for index, byte in enumerate(chunk):
        if escaped:
            escaped = False
            if inside:
                mask[index] = 1
            continue
        if byte == 0x5C:  # backslash
            escaped = True
            if inside:
                mask[index] = 1
            continue
        if byte == 0x22:  # double quote
            inside = not inside
            continue
        if inside:
            mask[index] = 1
    return mask


def _collect_glb_texts(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """从 GLB JSON 里挑出可能泄漏的文本字段（路径, 文本）。"""
    found: list[tuple[str, str]] = []
    asset = payload.get("asset") or {}
    if isinstance(asset, dict):
        for key in ("generator", "copyright", "version", "minVersion"):
            value = asset.get(key)
            if isinstance(value, str) and value.strip():
                found.append((f"$.asset.{key}", value))
        found.extend(_walk_glb_strings(asset.get("extras"), "$.asset.extras"))
    for array_name in _GLB_TEXT_ARRAYS:
        array = payload.get(array_name)
        if not isinstance(array, list):
            continue
        for index, item in enumerate(array):
            if not isinstance(item, dict):
                continue
            base = f"$.{array_name}[{index}]"
            for key in ("name", "uri", "mimeType"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    found.append((f"{base}.{key}", value))
            found.extend(_walk_glb_strings(item.get("extras"), f"{base}.extras"))
    for index, value in enumerate(payload.get("extensionsUsed") or []):
        if isinstance(value, str) and value.strip():
            found.append((f"$.extensionsUsed[{index}]", value))
    extensions = payload.get("extensions")
    if isinstance(extensions, dict):
        found.extend(_walk_glb_strings(extensions, "$.extensions"))
    return found


# ══════════════════════════════ 解析器 ══════════════════════════════

class MeshParser(Parser):
    """三维文件解析器（flow 类格式，不做位图渲染）。"""

    format_id = "mesh"
    extensions = (".obj", ".ply", ".glb", ".stl")
    can_render = False

    # ── 判型 ──
    @classmethod
    def sniff(cls, path: Path) -> float:
        try:
            if Path(path).suffix.lower() in cls.extensions:
                return 1.0
        except Exception:
            pass
        return 0.0

    # ── 解析 ──
    def parse(self, path: Path, ctx: ParseContext) -> Document:
        path = Path(path)
        document = Document(
            doc_id="d_" + uuid.uuid4().hex[:8],
            path=str(path),
            fmt=self.format_id,
        )
        suffix = path.suffix.lower()
        try:
            if suffix == ".obj":
                page = self._parse_obj(path, document)
            elif suffix == ".ply":
                page = self._parse_ply(path, document)
            elif suffix == ".stl":
                page = self._parse_stl(path, document)
            elif suffix == ".glb":
                page = self._parse_glb(path, document)
            else:
                document.warnings.append(f"不支持的三维扩展名：{suffix or '无'}")
                return document
            if page is not None:
                document.pages.append(page)
        except Exception as exc:  # 解析失败降级为带 warning 的空 Document
            document.pages.clear()
            document.warnings.append(f"三维文件解析失败（{suffix}）：{exc}")
        return document

    # ── 解析：OBJ ──
    def _parse_obj(self, path: Path, document: Document) -> Page:
        page = Page(index=0, width=A4_WIDTH, height=A4_HEIGHT)
        counters = {"blocks": 0}
        texts: list[tuple[str, int, str]] = []

        def add_block(text: str, ref: dict, attrs: dict) -> None:
            page.blocks.append(
                Block(
                    id=f"b{counters['blocks']}",
                    kind="text",
                    text=text,
                    ref=ref,
                    page=0,
                    attrs=attrs,
                )
            )
            counters["blocks"] += 1

        with open(path, "rb") as handle:
            for line_number, raw in enumerate(handle, start=1):
                text = _decode_line(raw).strip()
                if not text:
                    continue
                kind = _obj_line_kind(text)
                if kind:
                    texts.append((kind, line_number, text))
        vcount, vmin, vmax, bad = _scan_obj_geometry(path)
        coordinate_offset = bool(
            vcount
            and any(abs(value) > BIG_COORD_THRESHOLD for value in vmin + vmax)
        )
        geometry_attrs: dict[str, Any] = {
            "vertices": vcount,
            "coordinate_offset": coordinate_offset,
        }
        if vcount:
            geometry_attrs["vmin"] = [round(value, 6) for value in vmin]
            geometry_attrs["vmax"] = [round(value, 6) for value in vmax]
        for kind, line_number, text in texts:
            add_block(
                text,
                {"line": line_number, "source": kind},
                {"source": kind, **geometry_attrs},
            )
        if not vcount:
            document.warnings.append("OBJ 未找到顶点坐标（可能不是标准 OBJ，或只有几何索引）")
        elif coordinate_offset:
            document.warnings.append(
                "OBJ 顶点坐标绝对值超过 1e5，疑似大地/工程坐标，导出时将减去包围盒中心重定位到原点"
            )
        if bad:
            document.warnings.append(f"OBJ 有 {bad} 行顶点坐标无法解析，已忽略")
        document.meta = {
            "format": "obj",
            "vertices": vcount,
            "coordinate_offset": coordinate_offset,
        }
        page.ref = {"format": "obj"}
        return page

    # ── 解析：PLY ──
    def _parse_ply(self, path: Path, document: Document) -> Page:
        page = Page(index=0, width=A4_WIDTH, height=A4_HEIGHT)
        header = _read_ply_header(path)
        vertex = _ply_vertex_element(header)
        vertex_count = int(vertex["count"]) if vertex else 0
        for index, text in _ply_header_text_lines(header):
            page.blocks.append(
                Block(
                    id=f"b{index}",
                    kind="text",
                    text=text,
                    ref={"line": index, "source": "comment"},
                    page=0,
                    attrs={
                        "source": "comment",
                        "ply_format": header["format"],
                        "vertices": vertex_count,
                    },
                )
            )
        document.meta = {
            "format": "ply",
            "ply_format": header["format"],
            "vertices": vertex_count,
        }
        page.ref = {"format": "ply"}
        return page

    # ── 解析：STL ──
    def _parse_stl(self, path: Path, document: Document) -> Page:
        page = Page(index=0, width=A4_WIDTH, height=A4_HEIGHT)
        try:
            size = path.stat().st_size
            with open(path, "rb") as handle:
                head = handle.read(80)
                tail = handle.read(4)
        except Exception as exc:
            raise ValueError(f"STL 头部读取失败：{exc}") from exc
        header_text = _decode_header(head) if head else ""
        if header_text:
            page.blocks.append(
                Block(
                    id="b0",
                    kind="text",
                    text=header_text,
                    ref={"part": "header", "offset": 0, "length": len(head)},
                    page=0,
                    attrs={"source": "header", "size": size},
                )
            )
        is_ascii = _stl_is_ascii(head + tail, size)
        document.meta = {"format": "stl", "ascii": is_ascii, "size": size}
        page.ref = {"format": "stl"}
        return page

    # ── 解析：GLB ──
    def _parse_glb(self, path: Path, document: Document) -> Page:
        page = Page(index=0, width=A4_WIDTH, height=A4_HEIGHT)
        data = path.read_bytes()
        chunk = _glb_json_chunk(data)
        if chunk is None:
            document.warnings.append("GLB 结构非法或缺少 JSON chunk")
            page.ref = {"format": "glb"}
            return page
        start, length = chunk
        payload: dict[str, Any] | None = None
        try:
            raw_json = data[start:start + length].rstrip(b" \x00")
            payload = json.loads(raw_json.decode("utf-8", "replace"))
        except Exception as exc:
            document.warnings.append(f"GLB JSON chunk 解析失败：{exc}")
        if not isinstance(payload, dict):
            document.meta = {"format": "glb"}
            page.ref = {"format": "glb"}
            return page
        texts = _collect_glb_texts(payload)
        for index, (json_path, text) in enumerate(texts):
            page.blocks.append(
                Block(
                    id=f"b{index}",
                    kind="text",
                    text=text,
                    ref={"json_path": json_path},
                    page=0,
                    attrs={"source": "glb_json", "json_path": json_path},
                )
            )
        document.meta = {"format": "glb", "json_texts": len(texts), "size": len(data)}
        page.ref = {"format": "glb"}
        return page

    # ── 导出 ──
    def export(
        self,
        doc: Document,
        entities: list[Entity],
        decisions: dict[str, Decision],
        out_path: Path,
        options: dict,
    ) -> ExportResult:
        warnings: list[str] = []
        source = Path(doc.path)
        out_path = Path(out_path)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        if not source.is_file():
            raise ExportError(f"原文件不存在：{source}")
        rules = _entity_rules(entities, decisions)
        suffix = source.suffix.lower()
        try:
            if suffix == ".obj":
                self._export_obj(source, out_path, rules, warnings)
            elif suffix == ".ply":
                self._export_ply(source, out_path, rules, warnings)
            elif suffix == ".stl":
                self._export_stl(source, out_path, rules, warnings)
            elif suffix == ".glb":
                self._export_glb(source, out_path, rules, warnings)
            else:
                _copy_fallback(source, out_path, warnings, f"未知三维扩展名 {suffix}")
        except Exception as exc:  # 导出失败降级（出口自检会拦截未脱敏残留）
            _copy_fallback(source, out_path, warnings, f"三维导出失败：{exc}")
        if not out_path.exists():
            raise ExportError("三维文件导出没有产出成品")
        return ExportResult(
            path=str(out_path), warnings=warnings, size=out_path.stat().st_size
        )

    # ── 导出：OBJ ──
    def _export_obj(
        self,
        source: Path,
        out_path: Path,
        rules: list[tuple[str, str, bool]],
        warnings: list[str],
    ) -> None:
        vcount, vmin, vmax, _bad = _scan_obj_geometry(source)
        center = _obj_center(vcount, vmin, vmax)
        offset = (
            any(abs(value) > BIG_COORD_THRESHOLD for value in vmin + vmax)
            if vcount else False
        )
        sanitized_lines = 0
        text_hits = 0
        tmp_path = out_path.with_name(out_path.name + ".tmp")
        try:
            with open(source, "rb") as reader, open(tmp_path, "wb") as writer:
                for raw in reader:
                    text = _decode_line(raw)
                    content = text.rstrip("\r\n")
                    eol = _eol_of(raw)
                    kind = _obj_line_kind(content)
                    if kind:
                        new_text, hits = _sanitize_text(content, rules)
                        if hits:
                            sanitized_lines += 1
                            text_hits += hits
                            writer.write(new_text.encode("utf-8", "replace") + eol)
                            continue
                        writer.write(raw)
                        continue
                    stripped = content.strip()
                    if stripped[:1].lower() == "v" and (
                        len(stripped) == 1 or stripped[1:2].isspace()
                    ):
                        parts = stripped.split()
                        if len(parts) >= 4 and center is not None:
                            try:
                                values = [float(parts[1]), float(parts[2]), float(parts[3])]
                                parts[1] = _fmt_float(values[0] - center[0])
                                parts[2] = _fmt_float(values[1] - center[1])
                                parts[3] = _fmt_float(values[2] - center[2])
                                writer.write(" ".join(parts).encode("utf-8") + eol)
                                continue
                            except ValueError:
                                pass
                        writer.write(raw)
                        continue
                    writer.write(raw)
            _replace_file(tmp_path, out_path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        if center is None:
            warnings.append("OBJ 未找到顶点坐标，跳过几何重定位")
        else:
            warnings.append(
                "OBJ 顶点已减去包围盒中心重定位到原点"
                + ("（原坐标疑似大地/工程坐标）" if offset else "")
            )
        if text_hits:
            warnings.append(
                f"OBJ 已覆盖 {text_hits} 处敏感文本（涉及 {sanitized_lines} 行注释/材质/对象名）"
            )
        elif rules:
            warnings.append("OBJ 注释/材质/对象名中未发现待替换文本，请人工复核")

    # ── 导出：PLY ──
    def _export_ply(
        self,
        source: Path,
        out_path: Path,
        rules: list[tuple[str, str, bool]],
        warnings: list[str],
    ) -> None:
        header = _read_ply_header(source)
        header_bytes, text_hits = _ply_build_header(header, rules)
        if text_hits:
            warnings.append(f"PLY header 已覆盖 {text_hits} 处敏感文本")
        elif rules:
            warnings.append("PLY header 的 comment/obj_info 中未发现待替换文本")

        fmt = header["format"]
        byteorder = _ply_byteorder(fmt)
        tmp_path = out_path.with_name(out_path.name + ".tmp")
        try:
            if fmt == "ascii":
                self._export_ply_ascii(source, tmp_path, header, header_bytes, warnings)
            elif byteorder is not None:
                self._export_ply_binary(source, tmp_path, header, header_bytes, byteorder, warnings)
            else:
                with open(source, "rb") as reader, open(tmp_path, "wb") as writer:
                    writer.write(header_bytes)
                    reader.seek(header["data_offset"])
                    shutil.copyfileobj(reader, writer)
                warnings.append(f"未知 PLY 格式 {fmt}，仅清理头部文本，几何数据原样透传")
            _replace_file(tmp_path, out_path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def _export_ply_ascii(
        self,
        source: Path,
        tmp_path: Path,
        header: dict[str, Any],
        header_bytes: bytes,
        warnings: list[str],
    ) -> None:
        vertex = _ply_vertex_element(header)
        center: list[float] | None = None
        positions: tuple[int, int, int] | None = None
        if vertex is not None:
            prop_names = [prop["name"].lower() for prop in vertex["props"]]
            try:
                positions = (
                    prop_names.index("x"), prop_names.index("y"), prop_names.index("z")
                )
            except ValueError:
                positions = None
            try:
                center = _ply_ascii_center(source, header)
            except Exception as exc:
                warnings.append(f"PLY 顶点坐标扫描失败，跳过几何重定位：{exc}")
        with open(source, "rb") as reader, open(tmp_path, "wb") as writer:
            writer.write(header_bytes)
            reader.seek(header["data_offset"])
            for element in header["elements"]:
                count = int(element["count"])
                is_vertex = element is vertex
                for _index in range(count):
                    raw = reader.readline()
                    if not raw:
                        raise ValueError("PLY 数据行数少于 header 声明")
                    if is_vertex and center is not None and positions is not None:
                        text = _decode_line(raw).rstrip("\r\n")
                        parts = text.split()
                        if len(parts) >= len(positions):
                            try:
                                for axis, position in enumerate(positions):
                                    parts[position] = _fmt_float(
                                        float(parts[position]) - center[axis]
                                    )
                                writer.write(" ".join(parts).encode("utf-8") + _eol_of(raw))
                                continue
                            except ValueError:
                                pass
                    writer.write(raw)
        if center is None:
            warnings.append("PLY 未找到可重定位的 vertex 坐标，几何数据原样保留")
        else:
            warnings.append("PLY 顶点已减去包围盒中心重定位到原点（ASCII 重写）")

    def _export_ply_binary(
        self,
        source: Path,
        tmp_path: Path,
        header: dict[str, Any],
        header_bytes: bytes,
        byteorder: str,
        warnings: list[str],
    ) -> None:
        size = source.stat().st_size
        vertex = _ply_vertex_element(header)
        layout = None
        if vertex is not None:
            try:
                layout = _ply_binary_vertex_layout(header, byteorder)
            except Exception as exc:
                warnings.append(f"PLY 二进制顶点布局解析失败，跳过几何重定位：{exc}")
        can_recenter = (
            layout is not None and _NUMPY_OK and size <= PLY_BINARY_MEMORY_LIMIT
        )
        if layout is None and vertex is not None:
            warnings.append("PLY 顶点属性含 list/未知类型，跳过几何重定位")
        elif vertex is not None and size > PLY_BINARY_MEMORY_LIMIT:
            warnings.append("PLY 文件过大，跳过几何重定位（仅清理头部文本）")
        elif not _NUMPY_OK and vertex is not None:
            warnings.append("缺少 numpy，跳过 PLY 几何重定位")

        if not can_recenter:
            with open(source, "rb") as reader, open(tmp_path, "wb") as writer:
                writer.write(header_bytes)
                reader.seek(header["data_offset"])
                shutil.copyfileobj(reader, writer)
            if vertex is None:
                warnings.append("PLY 未找到 vertex 元素，几何数据原样保留")
            return

        start, count, dtype = layout  # type: ignore[misc]
        raw_data = source.read_bytes()
        vertex_offset = header["data_offset"] + start
        if vertex_offset + count * dtype.itemsize > len(raw_data):
            warnings.append("PLY 顶点数据长度小于 header 声明，跳过几何重定位")
            with open(source, "rb") as reader, open(tmp_path, "wb") as writer:
                writer.write(header_bytes)
                reader.seek(header["data_offset"])
                shutil.copyfileobj(reader, writer)
            return
        try:
            vertex_array = np.frombuffer(bytearray(raw_data), dtype=dtype, count=count,
                                         offset=vertex_offset)
        except Exception as exc:
            warnings.append(f"PLY 顶点数组解析失败，跳过几何重定位：{exc}")
            with open(source, "rb") as reader, open(tmp_path, "wb") as writer:
                writer.write(header_bytes)
                reader.seek(header["data_offset"])
                shutil.copyfileobj(reader, writer)
            return
        names = set(dtype.names or ())
        if not {"x", "y", "z"} <= names:
            warnings.append("PLY vertex 缺少 x/y/z 属性，跳过几何重定位")
            with open(source, "rb") as reader, open(tmp_path, "wb") as writer:
                writer.write(header_bytes)
                reader.seek(header["data_offset"])
                shutil.copyfileobj(reader, writer)
            return
        center = [
            (float(vertex_array[axis].min()) + float(vertex_array[axis].max())) / 2.0
            for axis in ("x", "y", "z")
        ]
        for axis in ("x", "y", "z"):
            vertex_array[axis] -= center[("x", "y", "z").index(axis)]
        output = bytearray(raw_data)
        output[: header["data_offset"]] = header_bytes
        output[vertex_offset:vertex_offset + count * dtype.itemsize] = vertex_array.tobytes()
        with open(tmp_path, "wb") as writer:
            writer.write(output)
        warnings.append("PLY 顶点已减去包围盒中心重定位到原点（二进制原地改写）")

    # ── 导出：STL ──
    def _export_stl(
        self,
        source: Path,
        out_path: Path,
        rules: list[tuple[str, str, bool]],
        warnings: list[str],
    ) -> None:
        data = source.read_bytes()
        header = data[:80]
        header_text = _decode_header(header) if header else ""
        ascii_mode = _stl_is_ascii(data)
        hits = 0
        if header_text:
            _new_text, hits = _sanitize_text(header_text, rules)
        if hits:
            if ascii_mode:
                # ASCII STL：同名文本可能同时出现在 solid / endsolid 行，
                # 全局用等字节长度空格覆盖，避免破坏 ASCII 结构；
                # 过短的值可能命中坐标数字，跳过并告警（出口自检会兜底）。
                skipped = 0
                for value, _replacement, _black in rules:
                    pattern = value.encode("utf-8")
                    if len(pattern) < 2:
                        skipped += 1
                        continue
                    if pattern and pattern in data:
                        data = data.replace(pattern, b" " * len(pattern))
                if skipped:
                    warnings.append(f"ASCII STL 有 {skipped} 个过短敏感值未覆盖，请人工复核")
                warnings.append("ASCII STL 头部敏感文本已用等长空格覆盖（几何数据未重定位）")
            else:
                data = b"\x00" * 80 + data[80:]
                warnings.append("二进制 STL 头含敏感信息，头部 80 字节已整体置零")
        warnings.append("STL 该格式暂不支持几何重定位（二进制/ASCII 透传）")
        with open(out_path, "wb") as writer:
            writer.write(data)

    # ── 导出：GLB ──
    def _export_glb(
        self,
        source: Path,
        out_path: Path,
        rules: list[tuple[str, str, bool]],
        warnings: list[str],
    ) -> None:
        data = source.read_bytes()
        chunk = _glb_json_chunk(data)
        if chunk is None:
            _copy_fallback(source, out_path, warnings, "GLB 结构非法或缺少 JSON chunk")
            return
        start, length = chunk
        chunk_bytes = bytearray(data[start:start + length])
        string_mask = _json_string_mask(bytes(chunk_bytes))
        total_hits = 0
        length_mismatch = 0
        skipped = 0
        for value, replacement, black in rules:
            value_bytes = value.encode("utf-8")
            patterns = [value_bytes]
            if b"/" in value_bytes:
                patterns.append(value_bytes.replace(b"/", b"\\/"))
            try:
                escaped = json.dumps(value, ensure_ascii=True)[1:-1].encode("utf-8")
                if escaped and escaped not in patterns:
                    patterns.append(escaped)
            except Exception:
                pass
            for pattern in patterns:
                if len(pattern) < 4:
                    if pattern and bytes(chunk_bytes).count(pattern):
                        skipped += 1
                    continue
                raw_replacement = replacement.encode("utf-8")
                safe = (
                    len(raw_replacement) == len(pattern)
                    and not any(
                        byte in raw_replacement for byte in (b'"', b"\\", b"\n", b"\r")
                    )
                )
                patch = raw_replacement if (safe and not black) else b" " * len(pattern)
                pattern_hits = 0
                position = 0
                while True:
                    position = bytes(chunk_bytes).find(pattern, position)
                    if position < 0:
                        break
                    if all(string_mask[position:position + len(pattern)]):
                        chunk_bytes[position:position + len(pattern)] = patch
                        pattern_hits += 1
                        total_hits += 1
                    else:
                        skipped += 1
                    position += len(pattern)
                if pattern_hits and not black and not safe:
                    length_mismatch += 1
        if total_hits:
            output = bytearray(data)
            output[start:start + length] = chunk_bytes
            with open(out_path, "wb") as writer:
                writer.write(output)
            warnings.append(
                f"GLB JSON chunk 内已等字节长度覆盖 {total_hits} 处敏感文本（chunk 长度不变）"
            )
            if length_mismatch:
                warnings.append(
                    "部分替换值与原值字节长度不同，GLB 内已退化为空格覆盖，请人工复核"
                )
            if skipped:
                warnings.append(
                    f"GLB 有 {skipped} 处敏感串不在 JSON 字符串内或过短，未覆盖，请人工复核"
                )
        else:
            _copy_fallback(
                source, out_path, warnings,
                "GLB JSON chunk 内未找到可覆盖的敏感文本（可能被转义或位于 BIN 块）",
            )
        warnings.append("GLB 该格式暂不支持几何重定位（二进制透传，仅清理 JSON 文本）")


register_parser(MeshParser)
