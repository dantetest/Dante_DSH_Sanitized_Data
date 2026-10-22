"""PDF 解析器。

解析：有文本层的页用 rawdict 提取「block -> 逐字符 bbox」；扫描页按 200dpi 渲染后 OCR。
      同时统计隐藏数据（附件/批注/表单控件/JavaScript）写进 doc.meta 与 warning。
导出：用 PyMuPDF 的 redaction 真正移除内容（不是叠黑框），再写回替换文字，
      清空文档元数据与 XMP；默认**剥离 PDF 隐藏数据**（附件/批注/表单/JS/页面缩略图），
      导出后可复检成品必须为 0（`keep_pdf_hidden=True` 时保留并由 verify 放行）。
      redaction 必须先 apply 再 insert。
"""
from __future__ import annotations

import os
import re
import time
import uuid
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Sequence

import fitz  # PyMuPDF
from PIL import Image

from .. import registry
from ..udm import (
    Block, Box, Decision, Document, Entity, ExportError, ExportResult,
    Page, ParseContext, ParseError, Span, VisualMark, box_intersects, union_boxes,
)

OCR_DPI = 200
RENDER_DPI = 110
VISUAL_DPI = 150          # 找红章/手写用的渲染精度，150 够且快
# 文本层少于这个字符数就当作扫描页走 OCR
MIN_TEXT_CHARS = 8
MIN_FONT_SIZE = 5.0

# ── 「给 AI 看」扫描页不可见文本层（F3）──
# 扫描页本身没有文本层，换名版导出时把 OCR 正文（实体已换成假值/占位）用
# render_mode=3（不可见）写回页面，纯文本抽取器才能读到正文。渲染像素不变，
# 但 get_text/PDF 抽取能读到；只对 mode="ai" 生效，human 涂黑版不写。
AI_TEXT_LAYER_MIN_PT = 3.0     # 不可见文本层最小字号（越小越放得下，反正看不见）
AI_TEXT_LAYER_MAX_PT = 11.0    # 不可见文本层最大字号
AI_TEXT_LAYER_BLACK_MARK = "█"  # black 实体的原值占位（不得写回原值）

# ── Phase A1：扫描页 AI 模式的可见替换文字改图片渲染（去重）──
# 背景：可见替换文字也是真文字，会和不可见文本层里的假值叠加，导致 get_text
# 里同一假值出现两次（doc08 ORG x15 等）。扫描页 AI 模式改为把假值渲染成
# 像素图贴回（文字不再进文本层），可提取文本只剩隐藏文本层一份。
# YINZHAN_AI_REPLACEMENT_IMAGE=0 可回退到旧「写文字」路径（测试/兼容用）。
AI_REPLACEMENT_IMAGE = os.environ.get(
    "YINZHAN_AI_REPLACEMENT_IMAGE", "1").strip().lower() not in (
        "0", "false", "no", "off", "none")
AI_REPLACEMENT_IMAGE_MAX_DPI = 288     # 渲染分辨率上限（288 ≈ 4x）
AI_REPLACEMENT_IMAGE_MIN_DPI = 120
AI_REPLACEMENT_IMAGE_MAX_PX = 1400     # 单条图片最大像素宽，控制成品体积

# ── Batch2-B4：AI 模式低置信 FACE 误检降级（human 模式不受影响）──
# 扫描合同页上的 Haar 人脸检测经常把标题/表格误判成 FACE 并打黑块（doc07 第 1 页
# 标题等）。AI 换名版可跳过「无证件/内嵌图实体页」上的自动 FACE（视觉内容恢复
# 可读）；human 涂黑版保持原样（隐私优先）。
# 阈值说明：Haar frontal 基准分硬编码 0.92（小脸系数后 0.874/0.782…），所以
# 「自动 FACE」实际都 < 0.95；0.95 上限 = 跳过自动检测到的 FACE，但保留未来接入
# 更高置信人脸模型/手工框的余地。可用 YINZHAN_AI_FACE_SKIP_MAX_CONF 覆盖。
AI_FACE_SKIP_ENABLED = os.environ.get(
    "YINZHAN_AI_FACE_SKIP", "1").strip().lower() not in (
        "0", "false", "no", "off", "none")
try:
    AI_FACE_SKIP_MAX_CONFIDENCE = float(
        os.environ.get("YINZHAN_AI_FACE_SKIP_MAX_CONF", "0.95") or 0.95)
except (TypeError, ValueError):
    AI_FACE_SKIP_MAX_CONFIDENCE = 0.95
# 这些实体所在页说明页面里有真实证件/证件头像/内嵌图片，禁止跳 FACE（宁可不跳）
AI_FACE_PROTECT_ETYPES = frozenset(
    ("ID_DOCUMENT", "ID_PHOTO", "EMBEDDED_IMAGE"))

# ── 扫描件成品快速复检（P2）──
# 纯扫描 PDF 导出后不再整册重跑 OCR：低 DPI 渲染成品页，逐个导出矩形验证
# 「黑框确实黑 / 替换框确实盖掉了原图墨迹」，文本层/元数据/隐藏数据仍按原逻辑检查。
FAST_RECHECK_DPI = 96
FAST_BLACK_MAX = 45          # 单通道 <= 45 视为黑像素（与内嵌图涂黑复检一致）
FAST_BLACK_MIN = 0.98        # 黑框内部黑像素占比下限
FAST_REPAINT_MAX = 0.80      # 替换框内「原墨迹仍为深色」的占比上限
FAST_MIN_INK_PIXELS = 5      # 原图墨迹太少时不据此判断（无可泄漏的字）
# 解析进 doc.meta、导出时整体清空的 PDF 元数据键
_META_KEYS = (
    "author", "title", "producer", "creator", "subject",
    "keywords", "creationDate", "modDate",
)


# ────────────────────────────── 打开与坐标 ──────────────────────────────

def _new_doc_id() -> str:
    """生成短文档 id。"""
    return "d_" + uuid.uuid4().hex[:8]


def _open_pdf(path: Path) -> fitz.Document:
    """打开 PDF；损坏或加密打不开时抛 ParseError。"""
    try:
        pdf = fitz.open(str(path))
    except Exception as exc:
        raise ParseError(f"无法打开 PDF：{exc}") from exc
    try:
        if pdf.needs_pass and not pdf.authenticate(""):
            pdf.close()
            raise ParseError("PDF 已加密，需要密码才能解析")
    except ParseError:
        raise
    except Exception as exc:
        try:
            pdf.close()
        except Exception:
            pass
        raise ParseError(f"PDF 解密失败：{exc}") from exc
    return pdf


def _open_pdf_for_export(path: Path) -> fitz.Document:
    """导出时重新打开源文件；加密且无法空密码解开时抛 ExportError。"""
    try:
        pdf = fitz.open(str(path))
    except Exception as exc:
        raise ExportError(f"无法重新打开 PDF：{exc}") from exc
    if pdf.needs_pass and not pdf.authenticate(""):
        pdf.close()
        raise ExportError("PDF 已加密，需要密码才能导出")
    return pdf


def _rotation(page: fitz.Page) -> int:
    """页面的显示旋转角（0/90/180/270）。"""
    try:
        return int(page.rotation) % 360
    except Exception:
        return 0


def _to_page_space(rect: Any, page: fitz.Page) -> Box:
    """PDF 内部坐标 -> 页面显示坐标（与 page.rect / 渲染图一致）。"""
    value = fitz.Rect(rect)
    if _rotation(page):
        value = value * page.rotation_matrix
    return (float(value.x0), float(value.y0), float(value.x1), float(value.y1))


def _to_pdf_space(rect: Any, page: fitz.Page) -> fitz.Rect:
    """页面显示坐标 -> PDF 内部坐标（redaction / insert_textbox 用）。"""
    value = fitz.Rect(rect)
    if _rotation(page):
        value = value * page.derotation_matrix
    return value


def _normalize_rect(rect: Any, page: Page) -> Box | None:
    """矩形 span 的兜底换算。

    jobs.py 传进来的 rect 已经是「页面坐标系」（归一化值已乘过 width/height），
    因此只要 max(rect) > 1 就原样返回；仅当整组值都落在 0..1 时才当作归一化值补乘。
    """
    try:
        x0, y0, x1, y1 = (float(v) for v in rect[:4])
    except Exception:
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if (page.width > 1.5 and page.height > 1.5
            and all(-0.001 <= v <= 1.001 for v in (x0, y0, x1, y1))):
        return (x0 * page.width, y0 * page.height, x1 * page.width, y1 * page.height)
    return (x0, y0, x1, y1)


# ────────────────────────────── 解析 ──────────────────────────────

def _document_meta(pdf: fitz.Document) -> dict[str, Any]:
    """收集 PDF 元数据（导出时全部丢弃）。"""
    meta: dict[str, Any] = {}
    try:
        raw = pdf.metadata or {}
    except Exception:
        raw = {}
    for key in _META_KEYS:
        value = raw.get(key)
        if value:
            meta[key] = str(value)
    try:
        meta["pages"] = int(pdf.page_count)
    except Exception:
        pass
    return meta


# ───────────────────────── PDF 隐藏数据（附件/批注/表单/JS）P0 ─────────────────────────
#
# 实测缺口（docs/research/08a C3、08b F3/F4）：旧版导出只做 redaction + metadata/XMP，
# 成品仍能提取附件 secret.txt、批注原文原样保留，而 pipeline.verify 判通过。
# 本段实现：解析统计 →（默认）导出前剥离 → 保存后成品复检；`keep_pdf_hidden=True` 保留。

#: 写进 doc.meta 的隐藏数据键；pipeline.verify 按这些键名检查，勿改名。
HIDDEN_META_KEYS = ("embedded_files", "pdf_annotations", "pdf_forms", "pdf_js")
#: JS 全量扫描的 xref 上限（超大 PDF 保护；不影响已识别对象的清理）
_JS_SCAN_MAX = max(1000, int(os.environ.get("YINZHAN_PDF_JS_SCAN_MAX", "50000") or 50000))
_JS_XREF_RE = re.compile(r"/JS(?![A-Za-z0-9])")
_JS_FULL_RE = re.compile(r"/JavaScript")


def _pdf_embfile_names(pdf: fitz.Document) -> list[str]:
    """附件名列表（不读内容）。"""
    try:
        return [str(name) for name in (pdf.embfile_names() or [])]
    except Exception:
        return []


def _pdf_annotation_stats(pdf: fitz.Document) -> tuple[int, dict[str, int]]:
    """全部页面注释计数 + 类型摘要（不读内容）。"""
    total = 0
    types: dict[str, int] = {}
    for page in pdf:
        try:
            annots = page.annots() or ()
        except Exception:
            continue
        try:
            for annot in annots:
                total += 1
                name = "?"
                try:
                    pair = annot.type
                    if isinstance(pair, (tuple, list)) and len(pair) > 1:
                        name = str(pair[1])
                    else:
                        name = str(pair)
                except Exception:
                    pass
                types[name] = types.get(name, 0) + 1
        except Exception:
            continue
    return total, types


def _pdf_widget_count(pdf: fitz.Document) -> int:
    """表单控件（widget）数量。"""
    total = 0
    for page in pdf:
        try:
            total += len(list(page.widgets() or []))
        except Exception:
            continue
    return total


def _xref_value_has_js(pdf: fitz.Document, typ: str, value: Any) -> bool:
    """xref_get_key 的返回值里是否含 JavaScript 动作。"""
    text = str(value or "")
    if _JS_FULL_RE.search(text) or _JS_XREF_RE.search(text):
        return True
    if str(typ) == "xref":
        try:
            xref = int(text.split()[0])
        except Exception:
            return False
        try:
            obj = pdf.xref_object(xref, compressed=False) or ""
        except Exception:
            return False
        return bool(_JS_FULL_RE.search(obj) or _JS_XREF_RE.search(obj))
    return False


def _pdf_has_js(pdf: fitz.Document) -> bool:
    """检测 PDF 里是否还有 JavaScript（目录动作/名称树/页面动作/xref 全扫）。"""
    try:
        catalog = pdf.pdf_catalog()
        keys = set(pdf.xref_get_keys(catalog) or ())
        for key in ("OpenAction", "AA"):
            if key not in keys:
                continue
            typ, value = pdf.xref_get_key(catalog, key)
            if _xref_value_has_js(pdf, typ, value):
                return True
        if "Names" in keys:
            typ, value = pdf.xref_get_key(catalog, "Names")
            if _xref_value_has_js(pdf, typ, value):
                return True
            if str(typ) == "xref":
                try:
                    names_xref = int(str(value).split()[0])
                    if "JavaScript" in set(pdf.xref_get_keys(names_xref) or ()):
                        return True
                except Exception:
                    pass
            if "JavaScript" in str(value):
                return True
        for page in pdf:
            try:
                if "AA" not in set(pdf.xref_get_keys(page.xref) or ()):
                    continue
                typ, value = pdf.xref_get_key(page.xref, "AA")
                if _xref_value_has_js(pdf, typ, value):
                    return True
            except Exception:
                continue
        limit = min(pdf.xref_length(), _JS_SCAN_MAX + 1)
        for xref in range(1, limit):
            try:
                obj = pdf.xref_object(xref, compressed=False) or ""
            except Exception:
                continue
            if _JS_FULL_RE.search(obj) or _JS_XREF_RE.search(obj):
                return True
    except Exception:
        return False
    return False


def _pdf_hidden_stats(pdf: fitz.Document) -> dict[str, Any]:
    """统计 PDF 隐藏数据（附件/批注/表单/JS）；不含任何原文内容。"""
    names = _pdf_embfile_names(pdf)
    annot_total, annot_types = _pdf_annotation_stats(pdf)
    stats: dict[str, Any] = {
        "embedded_files": len(names),
        "pdf_annotations": int(annot_total),
        "pdf_forms": int(_pdf_widget_count(pdf)),
        "pdf_js": bool(_pdf_has_js(pdf)),
    }
    if names:
        stats["embedded_file_names"] = names
    if annot_types:
        stats["pdf_annotation_types"] = annot_types
    return stats


def _pdf_hidden_positive(stats: dict[str, Any]) -> bool:
    for key in HIDDEN_META_KEYS:
        try:
            if int(stats.get(key) or 0):
                return True
        except (TypeError, ValueError):
            if stats.get(key):
                return True
    return False


def _pdf_hidden_summary(stats: dict[str, Any]) -> str:
    parts: list[str] = []
    try:
        count = int(stats.get("embedded_files") or 0)
        if count:
            names = stats.get("embedded_file_names") or []
            shown = "、".join(str(n) for n in names[:5])
            parts.append(f"附件 {count} 个" + (f"（{shown}）" if shown else ""))
    except (TypeError, ValueError):
        pass
    try:
        count = int(stats.get("pdf_annotations") or 0)
        if count:
            parts.append(f"批注 {count} 条")
    except (TypeError, ValueError):
        pass
    try:
        count = int(stats.get("pdf_forms") or 0)
        if count:
            parts.append(f"表单字段 {count} 个")
    except (TypeError, ValueError):
        pass
    if stats.get("pdf_js"):
        parts.append("JavaScript")
    return "、".join(parts)


def _pdf_hidden_meta(pdf: fitz.Document) -> tuple[dict[str, Any], str]:
    """统计隐藏数据并返回可并入 ``doc.meta`` 的键值与摘要（不读原文内容）。

    解析与快速复检共用同一份键名，避免 verify 的隐藏数据检查因路径不同而漂移。
    """
    hidden = _pdf_hidden_stats(pdf)
    meta: dict[str, Any] = {}
    for key in ("embedded_files", "pdf_annotations", "pdf_forms"):
        meta[key] = int(hidden.get(key) or 0)
    if hidden.get("embedded_file_names"):
        meta["embedded_file_names"] = list(hidden["embedded_file_names"])
    if hidden.get("pdf_annotation_types"):
        meta["pdf_annotation_types"] = dict(hidden["pdf_annotation_types"])
    meta["pdf_js"] = bool(hidden.get("pdf_js"))
    return meta, _pdf_hidden_summary(hidden)


def _strip_pdf_js(pdf: fitz.Document, warnings: list[str]) -> int:
    """删除 PDF JavaScript（目录/名称树/页面动作/孤立动作对象），返回清理处数。"""
    removed = 0
    try:
        catalog = pdf.pdf_catalog()
    except Exception:
        return removed
    # 1) 目录级 OpenAction / AA
    try:
        keys = set(pdf.xref_get_keys(catalog) or ())
        for key in ("OpenAction", "AA"):
            if key not in keys:
                continue
            try:
                typ, value = pdf.xref_get_key(catalog, key)
                if _xref_value_has_js(pdf, typ, value):
                    pdf.xref_set_key(catalog, key, "null")
                    removed += 1
            except Exception as exc:
                warnings.append(f"清除 PDF 目录 {key} 失败：{exc}")
    except Exception as exc:
        warnings.append(f"读取 PDF 目录动作失败：{exc}")
    # 2) Names / JavaScript 名称树
    try:
        keys = set(pdf.xref_get_keys(catalog) or ())
        if "Names" in keys:
            typ, value = pdf.xref_get_key(catalog, "Names")
            if str(typ) == "xref":
                try:
                    names_xref = int(str(value).split()[0])
                except Exception:
                    names_xref = 0
                if names_xref:
                    if "JavaScript" in set(pdf.xref_get_keys(names_xref) or ()):
                        pdf.xref_set_key(names_xref, "JavaScript", "null")
                        removed += 1
            if "JavaScript" in str(value):
                # 内联 Names 字典：P0 直接清空名称树（可能丢命名目标，写 warning）
                pdf.xref_set_key(catalog, "Names", "null")
                removed += 1
                warnings.append("已清空 PDF 名称树（含 JavaScript；命名目标/书签可能丢失）")
    except Exception as exc:
        warnings.append(f"清除 PDF 名称树 JavaScript 失败：{exc}")
    # 3) 页面级 Additional Actions
    for page in pdf:
        try:
            if "AA" not in set(pdf.xref_get_keys(page.xref) or ()):
                continue
            typ, value = pdf.xref_get_key(page.xref, "AA")
            if _xref_value_has_js(pdf, typ, value):
                pdf.xref_set_key(page.xref, "AA", "null")
                removed += 1
        except Exception:
            continue
    # 4) 其余孤立 JavaScript 动作对象
    limit = min(pdf.xref_length(), _JS_SCAN_MAX + 1)
    for xref in range(1, limit):
        if xref == catalog:
            continue
        try:
            obj = pdf.xref_object(xref, compressed=False) or ""
        except Exception:
            continue
        if not (_JS_FULL_RE.search(obj) or _JS_XREF_RE.search(obj)):
            continue
        if "/Type /Catalog" in obj or "/Type /Page" in obj:
            warnings.append(f"对象 {xref} 含 JS 但疑似目录/页面对象，未自动清空，请人工复核")
            continue
        try:
            keys = set(pdf.xref_get_keys(xref) or ())
            if "JavaScript" in keys:
                pdf.xref_set_key(xref, "JavaScript", "null")
                removed += 1
            elif "JS" in keys:
                pdf.xref_set_key(xref, "JS", "null")
                removed += 1
            else:
                pdf.update_object(xref, "<<>>")
                removed += 1
        except Exception:
            try:
                pdf.update_object(xref, "<<>>")
                removed += 1
            except Exception as exc:
                warnings.append(f"清空 JS 对象 {xref} 失败：{exc}")
    return removed


def _strip_pdf_hidden(pdf: fitz.Document, warnings: list[str]) -> dict[str, int]:
    """默认导出清理：附件 / 表单控件 / 批注 / JavaScript / 页面缩略图。"""
    removed = {"embedded_files": 0, "pdf_annotations": 0, "pdf_forms": 0,
               "pdf_js": 0, "thumbnails": 0}
    # 1) 附件（embfile_del；内容不再打包）
    for name in _pdf_embfile_names(pdf):
        try:
            pdf.embfile_del(name)
            removed["embedded_files"] += 1
        except Exception as exc:
            warnings.append(f"删除 PDF 附件 {name!r} 失败：{exc}")
    # 2) 表单控件：清值 + 删 widget；随后清 AcroForm（字段字典/值/动作一起没）
    for page in pdf:
        try:
            widgets = list(page.widgets() or [])
        except Exception:
            widgets = []
        for widget in widgets:
            try:
                widget.field_value = ""
                widget.update()
                page.delete_widget(widget)
                removed["pdf_forms"] += 1
            except Exception:
                try:
                    page.delete_widget(widget)
                    removed["pdf_forms"] += 1
                except Exception as exc:
                    warnings.append(f"删除 PDF 表单控件失败：{exc}")
    try:
        if removed["pdf_forms"] or pdf.is_form_pdf:
            pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm", "null")
    except Exception as exc:
        warnings.append(f"清除 PDF AcroForm 失败：{exc}")
    # 3) 批注（含 Text/FreeText/Highlight/Stamp/Link 等）
    for page in pdf:
        try:
            annots = list(page.annots() or [])
        except Exception:
            annots = []
        for annot in annots:
            try:
                page.delete_annot(annot)
                removed["pdf_annotations"] += 1
            except Exception as exc:
                warnings.append(f"删除 PDF 批注失败：{exc}")
    # 4) JavaScript
    removed["pdf_js"] = _strip_pdf_js(pdf, warnings)
    # 5) 附加：页面缩略图（可能保存了未脱敏画面）与附件模式标记
    for page in pdf:
        try:
            typ, _value = pdf.xref_get_key(page.xref, "Thumb")
            if typ not in ("null", None):
                pdf.xref_set_key(page.xref, "Thumb", "null")
                removed["thumbnails"] += 1
        except Exception:
            continue
    try:
        typ, value = pdf.xref_get_key(pdf.pdf_catalog(), "PageMode")
        if typ != "null" and "UseAttachments" in str(value):
            pdf.xref_set_key(pdf.pdf_catalog(), "PageMode", "UseNone")
    except Exception:
        pass
    return removed


def _verify_pdf_hidden_cleared(path: Path) -> dict[str, Any]:
    """重新打开成品，返回仍存在的隐藏数据计数（空 dict = 干净）。"""
    pdf = fitz.open(str(path))
    try:
        stats = _pdf_hidden_stats(pdf)
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    leftovers: dict[str, Any] = {}
    for key in HIDDEN_META_KEYS:
        value = stats.get(key)
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            count = 1 if value else 0
        if count:
            leftovers[key] = count
    return leftovers


def _page_text_blocks(page: fitz.Page, page_index: int) -> list[Block]:
    """把一页的文本层拆成 UDM block；ref["chars"] 与 text 逐字符对齐。"""
    try:
        raw = page.get_text("rawdict")
    except Exception:
        return []
    blocks: list[Block] = []
    for block_no, raw_block in enumerate(raw.get("blocks") or []):
        if raw_block.get("type") != 0:   # 非文本块（图片等）
            continue
        text_parts: list[str] = []
        char_rects: list[list[float]] = []
        first_size = 0.0
        font_name = ""
        for line_no, line in enumerate(raw_block.get("lines") or []):
            if line_no:
                # 行间补 "\n"，并用上一字符右端的零宽矩形占位，保证 chars 与 text 对齐
                text_parts.append("\n")
                if char_rects:
                    x0, y0, x1, y1 = char_rects[-1]
                    char_rects.append([x1, y0, x1, y1])
                else:
                    char_rects.append([0.0, 0.0, 0.0, 0.0])
            for span in line.get("spans") or []:
                if not first_size:
                    first_size = float(span.get("size") or 0.0)
                    font_name = str(span.get("font") or "")
                for char in span.get("chars") or []:
                    content = char.get("c") or ""
                    if not content:
                        continue
                    bbox = char.get("bbox") or (0.0, 0.0, 0.0, 0.0)
                    text_parts.append(str(content))
                    char_rects.append(list(_to_page_space(bbox, page)))
        text = "".join(text_parts)
        if not text.strip():
            continue
        bbox = union_boxes([tuple(rect) for rect in char_rects])
        if bbox is None:
            continue
        attrs: dict[str, Any] = {}
        if first_size:
            attrs["size"] = round(first_size, 2)
        if font_name:
            attrs["font"] = font_name
        blocks.append(Block(
            id=f"b{page_index}_{block_no}", kind="text", text=text, bbox=bbox,
            ref={"page": page_index, "chars": char_rects},
            page=page_index, attrs=attrs,
        ))
    return blocks


def _normalize_ocr_text(text: str) -> str:
    """OCR 文本做 NFKC 归一：RapidOCR 常把数字/字母识别成全角，影响规则命中。"""
    try:
        return unicodedata.normalize("NFKC", text).strip()
    except Exception:
        return text.strip()


# ──────────────────────── 文本层页内嵌图片扫描（P1-B） ────────────────────────
#
# 背景：有文本层的页只处理文字，页面内嵌的证件照/聊天截图/二维码不做识别。
# 这里对「面积 ≥5% 页面的内嵌图片」按图片区域单独渲染 + 扫描；命中敏感目标
# （人脸/证件/车牌/二维码/条码/聊天截图/招牌）时把**整块图片区域**产出一个
# kind="embedded_image" 的视觉标记（保守：避免图片内文字/局部目标漏涂）。
# 纯文本页（无图片）不进入本函数；图片解码/缩放前先按页面面积粗筛。
EMBEDDED_MIN_AREA_RATIO = 0.05
EMBEDDED_SCAN_DPI = 150
EMBEDDED_MAX_IMAGES = 8
EMBEDDED_OCR_MIN_PIXELS = 60_000

#: 命中这些 kind 才把整块内嵌图片判为敏感（保守；宁可多涂不可漏）
_EMBEDDED_SENSITIVE_KINDS = frozenset((
    "face", "id_document", "id_photo", "id_name", "id_number", "id_address",
    "id_authority", "id_validity", "plate",
    "qrcode", "barcode",
    "chat_avatar", "chat_name", "chat_group", "chat_time", "chat_input",
    "road_sign", "door_address", "shop_sign", "org_plaque",
))


def _embedded_text_ocr_enabled() -> bool:
    """P1.5 图片内「纯文字规则」检测开关（默认开；YINZHAN_PDF_EMBEDDED_TEXT_OCR=0 关）。"""
    raw = os.environ.get("YINZHAN_PDF_EMBEDDED_TEXT_OCR")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "none")


def _embedded_image_bytes(page: fitz.Page, xref: int, rgb: Any) -> tuple[bytes | None, str]:
    """取内嵌图片原始字节/后缀（优先 PDF 内嵌原件，失败时回退渲染 RGB→PNG）。"""
    data: bytes | None = None
    suffix = ""
    try:
        info = page.parent.extract_image(int(xref))
        data = info.get("image")
        ext = str(info.get("ext") or "").strip().lower()
        if ext:
            suffix = "." + ext
    except Exception:
        data, suffix = None, ""
    if not data:
        try:
            import io

            buf = io.BytesIO()
            Image.fromarray(rgb).save(buf, format="PNG")
            data = buf.getvalue()
            suffix = ".png"
        except Exception:
            return None, ""
    if suffix == ".jpe":
        suffix = ".jpg"
    return data, suffix


def _run_embedded_text_detect(page: fitz.Page, item: dict[str, Any], rgb: Any,
                              page_index: int, warn_sink: list[str],
                              seen: set[str]) -> int:
    """P1.5：对单张内嵌图片调用 media_text 检测器；返回规则命中数（0=不敏感）。

    helper 只作为**检测器**使用：不采用其返回的脱敏图（仍由 embedded_image
    整块涂黑）；跳过/预算不足等 warning 原样并入 doc.warnings，不产 mark。
    """
    try:
        from .. import media_text
    except Exception as exc:  # pragma: no cover - 正常情况下模块一定在
        _warn_once(warn_sink, seen, f"内嵌图片文字 OCR 不可用：{exc}")
        return 0
    data, suffix = _embedded_image_bytes(page, int(item["xref"]), rgb)
    if not data:
        _warn_once(warn_sink, seen,
                   f"第 {page_index + 1} 页内嵌图片文字 OCR 跳过（无法提取图片字节）")
        return 0
    try:
        _new_bytes, count, warns = media_text.redact_image_text(data, suffix or ".png")
    except Exception as exc:
        _warn_once(warn_sink, seen,
                   f"内嵌图片文字 OCR 失败（{type(exc).__name__}），请人工复核")
        return 0
    for warning in warns or []:
        _warn_once(warn_sink, seen, str(warning))
    try:
        return max(0, int(count or 0))
    except (TypeError, ValueError):
        return 0


def _warn_once(warn_sink: list[str], seen: set[str], message: str) -> None:
    text = str(message or "")
    if text and text not in seen:
        seen.add(text)
        warn_sink.append(text)



def _embedded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _embedded_env_float(name: str, default: float, minimum: float,
                        maximum: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _iou_rect(a: Box, b: Box) -> float:
    """两个矩形的交并比（内嵌图片区域去重用）。"""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _iter_embedded_images(page: fitz.Page) -> tuple[list[dict[str, Any]], int]:
    """枚举页面内嵌图片（面积 ≥ 阈值）。

    返回 ``(待扫描项, 去重后命中面积阈值的总数)``；待扫描项按面积降序、
    重叠去重、最多 ``YINZHAN_PDF_EMBEDDED_MAX_IMAGES`` 张。渲染用
    ``pdf_rect``；visual mark 用 ``display_rect``（与 OCR/导出坐标一致，
    旋转页由 ``_to_page_space`` 换算）。
    """
    min_ratio = _embedded_env_float("YINZHAN_PDF_EMBEDDED_MIN_AREA",
                                    EMBEDDED_MIN_AREA_RATIO, 0.01, 0.60)
    max_images = _embedded_env_int("YINZHAN_PDF_EMBEDDED_MAX_IMAGES",
                                   EMBEDDED_MAX_IMAGES, 1, 64)
    try:
        infos = page.get_images(full=True)
    except Exception:
        return [], 0
    page_rect = page.rect
    page_area = max(1.0, float(page_rect.width) * float(page_rect.height))
    items: list[dict[str, Any]] = []
    seen_xref: set[int] = set()
    for info in infos or []:
        try:
            xref = int(info[0])
        except (TypeError, ValueError, IndexError):
            continue
        if xref <= 0 or xref in seen_xref:
            continue
        seen_xref.add(xref)
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for raw_rect in rects or []:
            try:
                pdf_rect = fitz.Rect(raw_rect)
                pdf_rect.normalize()
                if pdf_rect.is_empty or pdf_rect.width < 4 or pdf_rect.height < 4:
                    continue
                display = _to_page_space(pdf_rect, page)
                display_rect = fitz.Rect(display)
                display_rect.normalize()
                # 显示坐标收进页面范围（旋转页也可用）
                clip = display_rect & page_rect
                if clip.is_empty or clip.width < 4 or clip.height < 4:
                    continue
                ratio = float(clip.width * clip.height) / page_area
                if ratio < min_ratio:
                    continue
                items.append({
                    "xref": xref,
                    "pdf_rect": pdf_rect,
                    "display_rect": (float(display_rect.x0), float(display_rect.y0),
                                     float(display_rect.x1), float(display_rect.y1)),
                    "area_ratio": round(ratio, 4),
                })
            except Exception:
                continue
    # 同一块图片被多个 xref/多次引用：按面积降序，重叠 ≥0.85 只保留一个
    items.sort(key=lambda item: -float(item["area_ratio"]))
    kept: list[dict[str, Any]] = []
    for item in items:
        rect = item["display_rect"]
        if any(_iou_rect(rect, other["display_rect"]) >= 0.85 for other in kept):
            continue
        kept.append(item)
    total = len(kept)
    if total > max_images:
        kept = kept[:max_images]
    return kept, total


def _embedded_sensitive_hits(marks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从扫描命中里筛出足够可信的敏感目标（避免正常配图误判整块涂黑）。"""
    picked: list[dict[str, Any]] = []
    for mark in marks or []:
        try:
            kind = str(mark.get("kind") or "")
            if kind not in _EMBEDDED_SENSITIVE_KINDS:
                continue
            note = str(mark.get("note") or "")
            confidence = float(mark.get("confidence") or 0.0)
            if kind in ("qrcode", "barcode") and "/detect" in note:
                continue                    # 仅检测未解码：不可信，不据此整块涂黑
            if kind == "face" and confidence < 0.75:
                continue
            if kind == "plate" and (confidence < 0.80 or "unconfirmed" in note):
                continue
            if kind in ("id_document", "id_photo", "id_name", "id_number",
                        "id_address", "id_authority", "id_validity") \
                    and confidence < 0.65:
                continue
            if kind.startswith("chat_") and confidence < 0.60:
                continue
            picked.append(mark)
        except Exception:
            continue
    return picked


def _scan_embedded_images(page: fitz.Page, page_index: int, doc: Document,
                          ctx: ParseContext | None) -> None:
    """扫描一页文本层页里的内嵌图片；有敏感命中时产 embedded_image 视觉标记。"""
    if ctx is None:
        return
    from .. import mediascan

    if not mediascan.has_enabled():
        return
    items, total = _iter_embedded_images(page)
    if not items:
        return
    if total > len(items):
        doc.warnings.append(
            f"第 {page_index + 1} 页内嵌图片 {total} 张，仅扫描面积最大的 "
            f"{len(items)} 张（YINZHAN_PDF_EMBEDDED_MAX_IMAGES），其余请人工复核")
    dpi = _embedded_env_int("YINZHAN_PDF_EMBEDDED_DPI", EMBEDDED_SCAN_DPI, 96, 300)
    ocr = getattr(ctx, "ocr", None)
    try:
        ocr_ready = bool(ocr is not None and getattr(ocr, "available", True))
    except Exception:
        ocr_ready = False
    ocr_min_pixels = _embedded_env_int("YINZHAN_PDF_EMBEDDED_OCR_MIN_PIXELS",
                                       EMBEDDED_OCR_MIN_PIXELS, 10_000, 4_000_000)
    debug = str(os.environ.get("YINZHAN_PDF_EMBEDDED_DEBUG", "") or "").strip() \
        not in ("", "0", "false", "no", "off")
    processed = 0
    hit_images = 0
    text_hit_images = 0
    text_seen_warnings: set[str] = set()
    t0 = time.time() if debug else 0.0

    def _ocr_boxes(rgb: Any) -> list[dict[str, Any]]:
        if not ocr_ready or rgb.shape[0] * rgb.shape[1] < ocr_min_pixels:
            return []
        try:
            lines = ocr.recognize(Image.fromarray(rgb), should_stop=ctx.stopped) or []
        except Exception:
            return []
        return [{"text": line.text, "rect": tuple(line.box)} for line in lines]

    for item in items:
        try:
            pix = page.get_pixmap(clip=item["pdf_rect"], dpi=dpi, alpha=False)
            if pix.width < 8 or pix.height < 8:
                continue
            try:
                import numpy as np

                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n)
            except Exception:
                continue
            if arr.shape[2] < 3:
                continue
            rgb = np.ascontiguousarray(arr[:, :, :3])
        except Exception as exc:
            doc.warnings.append(
                f"第 {page_index + 1} 页内嵌图片解码跳过（{type(exc).__name__}），"
                "如为 JPG2000/CMYK/遮罩等异常格式请人工复核")
            continue
        processed += 1
        sensitive: list[dict[str, Any]] = []
        text_hint = False
        try:
            marks, _warn = mediascan.analyze(
                rgb, page=page_index, dpi=float(dpi),
                options={"fmt": "pdf_embedded"})
            sensitive = _embedded_sensitive_hits(marks)
            if not sensitive:
                # 文本类目标（证件/聊天/招牌/车牌文字）需要 OCR 图片内文字
                boxes = _ocr_boxes(rgb)
                if boxes:
                    text_hint = sum(len(str(b["text"]).strip()) for b in boxes) >= 4
                    marks2, _warn2 = mediascan.analyze(
                        rgb, page=page_index, dpi=float(dpi),
                        options={"fmt": "pdf_embedded", "text_boxes": boxes})
                    sensitive = _embedded_sensitive_hits(marks2)
        except Exception as exc:
            doc.warnings.append(
                f"第 {page_index + 1} 页内嵌图片扫描跳过（{type(exc).__name__}）")
            continue
        # P1.5：视觉无命中时，对「有文字迹象」的图片再跑纯文字规则检测
        # （media_text.redact_image_text 只当检测器；命中→整块图片涂黑）。
        text_count = 0
        if not sensitive and _embedded_text_ocr_enabled() and (text_hint or not ocr_ready):
            text_count = _run_embedded_text_detect(
                page, item, rgb, page_index, doc.warnings, text_seen_warnings)
            if text_count > 0:
                text_hit_images += 1
        if not sensitive and text_count <= 0:
            continue
        display = item["display_rect"]
        if any(mark.kind == "embedded_image" and mark.page == page_index
               and _iou_rect(tuple(mark.rect), display) >= 0.85
               for mark in doc.visual_marks):
            continue
        area = float(item["area_ratio"])
        if text_count > 0 and not sensitive:
            note = f"engine=embedded_image;text={text_count};area={area:.3f}"
        else:
            note = f"engine=embedded_image;n={len(sensitive)};area={area:.3f}"
        doc.visual_marks.append(VisualMark(
            kind="embedded_image", page=page_index, rect=display,
            confidence=0.8, note=note))
        hit_images += 1
    if debug:
        elapsed = (time.time() - t0) * 1000.0
        doc.warnings.append(
            f"内嵌图片扫描 第 {page_index + 1} 页：处理 {processed}/{len(items)} 张，"
            f"命中 {hit_images} 张（文字 OCR {text_hit_images} 张），{elapsed:.0f}ms")


def _verify_embedded_images_black(out_path: Path, entities: Iterable[Entity],
                                  warnings: list[str]) -> None:
    """解析器侧成品复检：embedded_image 实体覆盖区域应被实心涂黑（黑像素≥95%）。"""
    targets: list[tuple[int, Box]] = []
    for entity in entities or []:
        try:
            if getattr(entity, "etype", "") != "EMBEDDED_IMAGE":
                continue
            if getattr(entity, "status", "accepted") == "rejected":
                continue
            for span in getattr(entity, "spans", None) or []:
                if not span.rect:
                    continue
                page = int(span.page) if span.page is not None else 0
                targets.append((page, tuple(float(v) for v in span.rect)))
        except Exception:
            continue
    if not targets:
        return
    try:
        pdf = fitz.open(str(out_path))
    except Exception as exc:
        warnings.append(f"内嵌图片涂黑复检无法重开成品（{type(exc).__name__}）")
        return
    try:
        for page_no, rect in targets:
            try:
                if page_no < 0 or page_no >= pdf.page_count:
                    continue
                page = pdf[page_no]
                clip = _to_pdf_space(rect, page)
                clip.normalize()
                clip = clip & page.rect
                if clip.is_empty or clip.width < 2 or clip.height < 2:
                    continue
                pix = page.get_pixmap(clip=clip, dpi=72, alpha=False)
                import numpy as np

                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n)
                rgb = arr[:, :, :3]
                black = float((rgb.max(axis=2) <= 45).mean())
                if black < 0.95:
                    warnings.append(
                        f"第 {page_no + 1} 页内嵌图片区域未完全涂黑"
                        f"（黑像素 {black:.0%}），请人工复核")
            except Exception as exc:
                warnings.append(
                    f"第 {page_no + 1} 页内嵌图片涂黑复检失败（{type(exc).__name__}）")
    finally:
        try:
            pdf.close()
        except Exception:
            pass


def _collect_visual(page: fitz.Page, blocks: list[Block], page_index: int,
                    doc: Document, scanned: bool = False,
                    ctx: ParseContext | None = None) -> None:
    """在页面上找红章和手写签名候选，结果记进 doc.visual_marks。

    这里单独渲染一次（150dpi），不复用 OCR 那张 200dpi 的图：
    一是精度够，二是就算 OCR 被跳过（有文本层的页）也能查印章。
    ``scanned=True`` 表示这页是无文本层的扫描页；跨媒体扫描（人脸/码类）
    只在扫描页或含内嵌图片的页上跑，纯文本页跳过，避免普通合同 PDF 变慢。
    """
    try:
        import numpy as np
        from .. import visual

        pix = page.get_pixmap(dpi=VISUAL_DPI)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        to_px = VISUAL_DPI / 72.0
        text_boxes_px = []
        for b in blocks:
            if b.bbox:
                x0, y0, x1, y1 = b.bbox
                text_boxes_px.append((x0 * to_px, y0 * to_px, x1 * to_px, y1 * to_px))
        to_pt = 72.0 / VISUAL_DPI
        for kind, box in visual.analyze_page(arr, VISUAL_DPI, text_boxes_px):
            doc.visual_marks.append(VisualMark(
                kind=kind, page=page_index,
                rect=(box[0] * to_pt, box[1] * to_pt, box[2] * to_pt, box[3] * to_pt),
            ))

        # 跨媒体目标扫描（二维码/条码/人脸/证件…）。只有注册了扫描器才多渲染一张，
        # 没有扩展模块时普通 PDF 的解析耗时与基线完全一致。纯文本页（无内嵌图片、
        # 非扫描页）直接跳过：合同 PDF 不该为一次条码扫描多花一倍时间。
        try:
            from .. import mediascan

            if mediascan.has_enabled():
                has_images = False
                if not scanned:
                    try:
                        has_images = bool(page.get_images(full=True))
                    except Exception:
                        has_images = True      # 判断失败时宁可多扫，不漏
                    if not has_images:
                        return
                try:
                    scan_dpi = int(os.environ.get("YINZHAN_SCAN_DPI", "200") or 200)
                except ValueError:
                    scan_dpi = 200
                scan_dpi = max(72, min(400, scan_dpi))
                if scan_dpi == int(VISUAL_DPI):
                    scan_arr = arr
                else:
                    scan_pix = page.get_pixmap(dpi=scan_dpi)
                    scan_arr = np.frombuffer(scan_pix.samples, dtype=np.uint8)
                    scan_arr = scan_arr.reshape(scan_pix.height, scan_pix.width, 3)
                scan_to_px = scan_dpi / 72.0
                scan_boxes = []
                for b in blocks:
                    if b.bbox:
                        x0, y0, x1, y1 = b.bbox
                        scan_boxes.append({
                            "text": b.text,
                            "rect": (x0 * scan_to_px, y0 * scan_to_px,
                                     x1 * scan_to_px, y1 * scan_to_px),
                        })
                marks, scan_warns = mediascan.analyze(
                    scan_arr, page=page_index, dpi=float(scan_dpi),
                    options={"text_boxes": scan_boxes, "fmt": "pdf"})
                doc.visual_marks.extend(mediascan.to_visual_marks(
                    marks, scale=72.0 / scan_dpi, page=page_index))
                doc.warnings.extend(scan_warns)
                # P1-B：文本层页的内嵌图片逐张扫描；命中敏感目标 → 整块图片区域
                # 产 embedded_image 视觉标记。纯文本页 / 扫描页不进入这一支。
                if not scanned and has_images:
                    try:
                        _scan_embedded_images(page, page_index, doc, ctx)
                    except Exception as embedded_exc:
                        doc.warnings.append(
                            f"第 {page_index + 1} 页内嵌图片扫描跳过：{embedded_exc}")
        except Exception as scan_exc:
            doc.warnings.append(f"第 {page_index + 1} 页图像目标扫描跳过：{scan_exc}")
    except Exception as exc:  # 视觉检测失败不能影响主流程
        doc.warnings.append(f"第 {page_index + 1} 页图像检测跳过：{exc}")


def _ocr_page_blocks(page: fitz.Page, page_index: int, ctx: ParseContext | None,
                     warnings: list[str]) -> list[Block] | None:
    """扫描页 OCR。返回 None 表示 OCR 不可用（调用方保留原文本层）。"""
    ocr = getattr(ctx, "ocr", None) if ctx is not None else None
    if ocr is None:
        warnings.append(f"第 {page_index + 1} 页无文本层，且未提供 OCR 服务")
        return None
    try:
        if getattr(ocr, "available", True) is False:
            reason = getattr(ocr, "error", None) or "未知原因"
            warnings.append(f"第 {page_index + 1} 页无文本层，OCR 不可用（{reason}）")
            return None
    except Exception:
        pass
    try:
        pixmap = page.get_pixmap(dpi=OCR_DPI)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        stop = (ctx.stopped if ctx is not None else None)
        lines = ocr.recognize(image, should_stop=stop) or []
        if ctx is not None:
            ctx.check_stop()
    except Exception as exc:
        if isinstance(exc, ParseError):
            raise
        warnings.append(f"第 {page_index + 1} 页 OCR 失败：{exc}")
        return None

    scale = 72.0 / OCR_DPI   # OCR 像素 -> PDF pt（渲染图已带页面旋转）
    blocks: list[Block] = []
    for index, line in enumerate(lines):
        raw_text = str(getattr(line, "text", "") or "")
        text = _normalize_ocr_text(raw_text)
        if not text:
            continue
        bbox: Box | None = None
        try:
            x0, y0, x1, y1 = (float(v) for v in getattr(line, "box")[:4])
            bbox = (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
        except Exception:
            bbox = None
        blocks.append(Block(
            id=f"b{page_index}_o{index}", kind="text", text=text, bbox=bbox,
            ref={"page": page_index, "ocr": True}, page=page_index,
            attrs={"score": round(float(getattr(line, "score", 0.0)), 4)},
        ))
    if not blocks:
        warnings.append(f"第 {page_index + 1} 页 OCR 未识别到文字")
    return blocks


# ────────────────────────────── 导出 ──────────────────────────────

def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _span_rects_from_chars(chars: Sequence[Sequence[float]], start: int,
                           end: int) -> list[Box]:
    """把字符矩形序列 [start, end) 合成逐行矩形：同行合并、跨行拆开。"""
    if not chars:
        return []
    total = len(chars)
    start = max(0, min(int(start), total))
    end = max(start, min(int(end), total))
    if end <= start:
        return []
    rows: list[list[float]] = []
    for raw in chars[start:end]:
        try:
            x0, y0, x1, y1 = (float(v) for v in raw[:4])
        except Exception:
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        if not rows:
            rows.append([x0, y0, x1, y1])
            continue
        current = rows[-1]
        overlap = min(current[3], y1) - max(current[1], y0)
        min_height = min(current[3] - current[1], y1 - y0)
        if min_height > 0 and overlap > min_height * 0.5:
            current[0] = min(current[0], x0)
            current[1] = min(current[1], y0)
            current[2] = max(current[2], x1)
            current[3] = max(current[3], y1)
        else:
            rows.append([x0, y0, x1, y1])
    result: list[Box] = []
    for row in rows:
        if row[2] - row[0] > 0.05 and row[3] - row[1] > 0.05:
            result.append((row[0], row[1], row[2], row[3]))
    return result


def _interpolate_span(block: Block, span: Span) -> Box:
    """OCR block 没有字符级坐标，按字符比例在 block.bbox 内横向插值。"""
    bbox = block.bbox or (0.0, 0.0, 0.0, 0.0)
    text_len = len(block.text)
    if (text_len <= 0 or span.start is None or span.start < 0
            or span.end is None or span.end < 0):
        return bbox
    start = max(0, min(int(span.start), text_len))
    end = max(start, min(int(span.end), text_len))
    width = bbox[2] - bbox[0]
    if width <= 0 or text_len <= 1:
        return bbox
    return (
        bbox[0] + width * start / text_len, bbox[1],
        bbox[0] + width * end / text_len, bbox[3],
    )


def _span_rects_from_block(block: Block, span: Span) -> list[Box]:
    """按 block.ref 把 span 换算成待涂改矩形。"""
    if block.ref.get("ocr"):
        return [_interpolate_span(block, span)]
    chars = block.ref.get("chars")
    if not isinstance(chars, list) or not chars:
        return [block.bbox] if block.bbox else []
    text_len = len(block.text)
    start = int(span.start) if span.start is not None and span.start >= 0 else 0
    end = int(span.end) if span.end is not None and span.end >= 0 else text_len
    if start == 0 and end >= text_len and block.bbox is not None:
        return [block.bbox]
    return _span_rects_from_chars(chars, start, end)


def _collect_ops(doc: Document, entities: Iterable[Entity] | None,
                 decisions: dict[str, Decision] | None,
                 warnings: list[str]) -> dict[int, list[dict[str, Any]]]:
    """把实体决策换算成「页号 -> 待处理矩形」；矩形用页面显示坐标。"""
    block_map: dict[str, tuple[int, Block]] = {}
    for page in doc.pages:
        for block in page.blocks:
            block_map[block.id] = (page.index, block)

    ops_by_page: dict[int, list[dict[str, Any]]] = {}
    page_count = len(doc.pages)
    for entity in entities or []:
        decision = (decisions or {}).get(entity.id) or entity.decision
        if decision is None:
            continue
        if decision.strategy == "keep" and not decision.black:
            continue
        black = bool(decision.black) or decision.strategy in ("black", "drop")
        text = "" if black else (decision.replacement or "")
        for span in entity.spans or []:
            page_no: int | None = None
            rects: list[Box] = []
            if span.block_id:
                hit = block_map.get(span.block_id)
                if hit is None:
                    warnings.append(f"实体 {entity.id} 的定位块 {span.block_id} 不存在，已跳过")
                    continue
                page_no, block = hit
                rects = _span_rects_from_block(block, span)
            elif span.rect:
                # 矩形 span 由 jobs.py 从手动画框生成，必带页号（udm.Span.page）
                page_no = (int(span.page) if span.page is not None
                           else (0 if page_count == 1 else None))
                if page_no is None or not (0 <= page_no < page_count):
                    warnings.append(
                        f"实体 {entity.id} 的矩形 span 缺少有效页号，已跳过")
                    continue
                normalized = _normalize_rect(span.rect, doc.pages[page_no])
                rects = [normalized] if normalized else []
            if page_no is None:
                continue
            page_ops = ops_by_page.setdefault(int(page_no), [])
            for rect in rects:
                if rect[2] <= rect[0] or rect[3] <= rect[1]:
                    continue
                page_ops.append({"rect": rect, "black": black, "text": text})
    return ops_by_page


# 黑框合并策略：只合并「完全相同 / 一个包含另一个」的框（近重合重复），
# 其余交叠框各自绘制。取外接矩形 union 会在两框之间凭空多涂一块（实测：
# 签署区 539×187 上沿与印章框交叠，union 后顶边上移 38pt，把上一行条款涂掉）。
BLACK_MERGE_ONE_SIDE_EPS = 0.5   # 包含判定容差（pt），吸收 OCR 抖动


def _rect_covers(outer: Box, inner: Box,
                 eps: float = BLACK_MERGE_ONE_SIDE_EPS) -> bool:
    return (outer[0] - eps <= inner[0] and outer[1] - eps <= inner[1]
            and outer[2] + eps >= inner[2] and outer[3] + eps >= inner[3])


def _merge_black_rects(rects: list[Box]) -> list[Box]:
    """合并黑框，但绝不让外接矩形膨胀。

    v1.1.2 修复：以前对交叠黑框无条件取外接矩形（union），签名区/印章/误检块
    相互交叠时会被并成一个大黑块，把中间的条款正文、空白甚至上一行也涂掉
    （测试集实测：劳务派遣协议第 5 页黑条压掉「题，一切后果由乙方承担。」）。
    现在只合并「一框包含另一框」的重复/近重合框；其余交叠框各自绘制，
    黑区 = 各框并集，不额外扩大。
    """
    out: list[Box] = []
    for rect in rects:
        merged = rect
        changed = True
        while changed:
            changed = False
            rest: list[Box] = []
            for existing in out:
                if _rect_covers(merged, existing) or _rect_covers(existing, merged):
                    merged = _union(merged, existing)
                    changed = True
                else:
                    rest.append(existing)
            out = rest
        out.append(merged)
    return out


def _merge_ops(ops: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    """合并交叠矩形：黑框合并并优先；同文本的替换框合并，避免 redaction 互相切割。

    F1（v1.1.2 后修）：替换框与黑框相交时**不能丢弃**。以前直接 ``continue``，
    该处原文既没 redaction 也没写假值，只被黑框遮住中间，两端原值可见
    （实测 doc05 第 1 页「四[黑块]察设计有限公司」）。现在把相交的替换矩形
    并入黑框集合（迭代到不动点后重新合并黑框），该区域整块涂黑、原文被真正
    redaction 掉；不与黑框相交的替换框行为不变。
    """
    black_rects = _merge_black_rects([op["rect"] for op in ops if op["black"]])

    pending = [op for op in ops if not op["black"]]
    # 先把与黑框相交的替换框吸进黑框集合；新并入的矩形可能又和别的替换框
    # 相交，因此迭代到不动点，每次变化后重新按同一规则合并黑框。
    absorbed = 0
    changed = True
    while changed and pending:
        changed = False
        remaining: list[dict[str, Any]] = []
        for op in pending:
            if any(box_intersects(op["rect"], rect) for rect in black_rects):
                black_rects.append(op["rect"])
                absorbed += 1
                changed = True
            else:
                remaining.append(op)
        pending = remaining
        if changed:
            black_rects = _merge_black_rects(black_rects)
    if absorbed:
        warnings.append(
            f"{absorbed} 个替换区域与涂黑区域重叠，已按涂黑处理（原文已删除）")

    replacements: list[dict[str, Any]] = []
    for op in pending:
        target = None
        for existing in replacements:
            if box_intersects(op["rect"], existing["rect"]):
                if op["text"] == existing["text"]:
                    target = existing
                else:
                    warnings.append("多个替换区域重叠且替换文本不同，已分别写入")
                break
        if target is not None:
            target["rect"] = _union(target["rect"], op["rect"])
        else:
            replacements.append(dict(op))

    return ([{"rect": rect, "black": True, "text": ""} for rect in black_rects]
            + replacements)


def _replacement_fallback_font(text: str, width: float, height: float) -> float:
    """兜底单行字号：同时受矩形宽/高约束，避免文字画到替换框甚至页面之外。

    china-s 的 ASCII 字形偏宽；旧兜底只按高度估字号，遇到窄框会把后半截画到
    页面外，文本层抽取只剩前半截（doc01/02 的金额假值被截断即此因）。
    """
    font = max(MIN_FONT_SIZE, min(float(height) * 0.72, 12.0))
    try:
        unit = float(fitz.get_text_length(text, fontname="china-s", fontsize=1.0))
    except Exception:
        unit = 0.0
    if unit > 0:
        font = min(font, max(1.0, float(width) * 0.98 / unit))
    return max(1.0, font)


def _insert_replacement(page: fitz.Page, rect: fitz.Rect, text: str) -> None:
    """redaction 之后把替换文字写回矩形；字号按矩形高度估算并逐步收缩。

    F2（v1.1.2 后修）：页面带 /Rotate 时，``insert_textbox`` 的矩形是**未旋转
    坐标**，若不传 ``rotate``，文字在显示方向上是旋转/错位的（实测 doc08
    /Rotate=270，OCR 只能读出 5/16 个假值）。这里统一传 ``rotate=page.rotation``，
    显示方向即为水平；旋转 90/270 时未旋转矩形的高对应显示行宽、宽对应显示
    行高，字号按短边（inner.width）估算。旋转 0 页的参数与旧实现完全一致，
    输出不变。
    """
    text = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if not text or rect.width <= 0.2 or rect.height <= 0.2:
        return
    box = fitz.Rect(rect)
    pad = min(0.6, float(box.height) * 0.05)
    inner = fitz.Rect(box.x0 + pad, box.y0 + pad, box.x1 - pad, box.y1 - pad)
    if inner.is_empty:
        inner = box
    rotation = _rotation(page)
    # 旋转 90/270：未旋转矩形的宽 = 显示行高（决定字号），高 = 显示行宽。
    if rotation in (90, 270):
        size = max(MIN_FONT_SIZE, float(inner.width) * 0.72)
    else:
        size = max(MIN_FONT_SIZE, float(inner.height) * 0.72)
    while size >= MIN_FONT_SIZE:
        try:
            status = page.insert_textbox(
                inner, text, fontname="china-s", fontsize=size,
                color=(0, 0, 0), align=0, overlay=True, rotate=rotation,
            )
        except Exception:
            status = -1
        if status >= 0:
            return
        size *= 0.85
    # 兜底：矩形太小放不下，直接写一行，保证假值确实落在原位置
    try:
        if not rotation:
            page.insert_text(
                (inner.x0, min(inner.y1, inner.y0 + inner.height * 0.85)), text,
                fontname="china-s",
                fontsize=_replacement_fallback_font(text, rect.width,
                                                    rect.height),
                color=(0, 0, 0), overlay=True,
            )
        else:
            # 旋转页：先在显示坐标里取文本框左下角，再换算回未旋转坐标，
            # 并按页面旋转写一行，显示方向保持水平。
            display = fitz.Rect(rect) * page.rotation_matrix
            start = fitz.Point(
                display.x0, min(display.y1, display.y0 + display.height * 0.85))
            start = start * page.derotation_matrix
            page.insert_text(
                start, text, fontname="china-s",
                fontsize=_replacement_fallback_font(
                    text, display.width, display.height),
                color=(0, 0, 0), overlay=True, rotate=rotation,
            )
    except Exception:
        pass


# ── Phase A1：扫描页 AI 模式的可见替换文字 → 图片渲染（避免文本层重复）──
#
# 可见假值如果仍是真文字，会和不可见文本层里的同一假值叠加：get_text 里一个
# 假值出现两次，页尾还会多出一串按内容流顺序排队的替换文字。扫描页没有原文
# 文本层、正文完全由隐藏层提供，因此这里把可见假值渲染成像素图贴回：
#   * 视觉/OCR 仍能读到假值（形状、字号、换行与旧文字路径一致）；
#   * 文本抽取只剩隐藏文本层一份，假值计数 == 原值在 OCR 正文里的出现次数；
#   * 只对 mode=ai 且 scanned=True 的页生效，human 模式不受影响。

def _ai_replacement_image_enabled() -> bool:
    return AI_REPLACEMENT_IMAGE


def _render_replacement_pixmap(display_rect: fitz.Rect,
                               text: str) -> fitz.Pixmap | None:
    """把替换文字在「显示方向」的小页上排版后渲染成白底黑字像素图。

    排版逻辑尽量与旧 ``_insert_replacement`` 一致（同样的字号估算与收缩），
    这样可见效果不变，只是文字变成了图片、不再进文本层。
    """
    width = max(6.0, float(display_rect.width))
    height = max(6.0, float(display_rect.height))
    tmp = fitz.open()
    try:
        page = tmp.new_page(width=width, height=height)
        pad = min(0.6, height * 0.05)
        inner = fitz.Rect(pad, pad, width - pad, height - pad)
        if inner.is_empty or inner.width <= 0.5 or inner.height <= 0.5:
            inner = fitz.Rect(0, 0, width, height)
        size = max(MIN_FONT_SIZE, float(inner.height) * 0.72)
        placed = False
        while size >= 2.5:
            try:
                status = page.insert_textbox(
                    inner, text, fontname="china-s", fontsize=size,
                    color=(0, 0, 0), align=0, overlay=True)
            except Exception:
                status = -1
            if status >= 0:
                placed = True
                break
            size *= 0.85
        if not placed:
            # 兜底：单行缩到能塞进矩形。图片不能像文字那样溢出，否则白底会
            # 盖住相邻正文；宁可小一点也不能超出替换区域。
            try:
                unit = float(fitz.get_text_length(
                    text, fontname="china-s", fontsize=1.0))
            except Exception:
                unit = 0.0
            if unit <= 0:
                unit = max(1.0, len(text) * 1.0)
            fit = min(float(inner.height) * 0.9, float(inner.width) / unit)
            fit = max(1.5, fit)
            try:
                page.insert_text(
                    (inner.x0, inner.y0 + fit), text, fontname="china-s",
                    fontsize=fit, color=(0, 0, 0), overlay=True)
                placed = True
            except Exception:
                placed = False
        if not placed:
            return None
        # 像素规模控制：默认约 288dpi；超宽时降 dpi，避免成品体积膨胀
        dpi = min(AI_REPLACEMENT_IMAGE_MAX_DPI,
                  AI_REPLACEMENT_IMAGE_MAX_PX * 72.0 / width)
        dpi = max(AI_REPLACEMENT_IMAGE_MIN_DPI, dpi)
        return page.get_pixmap(dpi=dpi, alpha=False)
    except Exception:
        return None
    finally:
        try:
            tmp.close()
        except Exception:
            pass


def _insert_replacement_image(page: fitz.Page, rect: fitz.Rect,
                              text: str) -> bool:
    """扫描页 AI 模式：把替换文字渲染成图片贴回矩形（不产生可提取文字）。

    ``rect`` 是未旋转坐标（``_to_pdf_space`` 的结果）。返回 False 表示图片
    路径不可用，调用方回退到旧的文字写回。
    """
    text = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if not text or rect.width <= 0.2 or rect.height <= 0.2:
        return True
    if not _ai_replacement_image_enabled():
        return False
    try:
        display = _to_page_space(rect, page)
        pix = _render_replacement_pixmap(fitz.Rect(display), text)
        if pix is None:
            return False
        page.insert_image(
            fitz.Rect(rect), pixmap=pix, rotate=_rotation(page),
            overlay=True, keep_proportion=False)
        return True
    except Exception:
        return False


# ─────────────────── 「给 AI 看」扫描页不可见文本层（F3）───────────────────
#
# 背景：扫描件没有文本层，换名版导出只写回实体替换文字 → 纯文本抽取器只能读到
# 几十到几百字的假值片段，读不到正文。F3 在 mode="ai" 且该页是扫描页时，把
# parse 阶段的 OCR 文本（实体原值换成假值/占位）用 render_mode=3 写回页面：
# 渲染像素不变，但 get_text / pdftotext 能读到正文 + 假值。
# 安全约束：文本层里不得出现任何已检出实体的原值（否则 pipeline.verify 会判
# 残留并拒绝交付）；keep/rejected 保留原文，black/drop 用占位符，其余用假值。
# human 模式（涂黑版）绝不写这层。

def _ai_text_layer_enabled(options: dict | None) -> bool:
    """只有「给 AI 看」的换名版才写不可见文本层。"""
    return str((options or {}).get("mode") or "").strip().lower() == "ai"


def _ai_text_target(entity: Entity, decision: Decision | None) -> str | None:
    """实体在不可见文本层里的替换文本；None 表示保留原文（keep/未决策）。"""
    if decision is None or decision.strategy == "keep":
        return None
    if decision.black or decision.strategy in ("black", "drop"):
        # 视觉实体（印章/手写签名等）没有文字原值，不需要占位
        if not entity.value:
            return None
        return AI_TEXT_LAYER_BLACK_MARK * len(entity.value)
    # surrogate / mask / 其它非黑策略：写回假值（与可见替换文字一致）
    return decision.replacement or ""


def _ai_text_targets(entities: Iterable[Entity] | None,
                     decisions: dict[str, Decision] | None
                     ) -> tuple[dict[str, list[tuple[int, int, str, str]]],
                                dict[str, str]]:
    """收集「块内 span 替换」和「全文值级替换」两张表，供文本层替换使用。

    span 条目带原值（第 4 项）：值级扩散/折行会让 span 只覆盖原值的一段，
    写文本层时要用假值对应片段替换，否则同一个假值会被造出多次、破坏
    「假值出现次数 == 原值出现次数」不变量。
    """
    span_targets: dict[str, list[tuple[int, int, str, str]]] = {}
    value_targets: dict[str, str] = {}
    for entity in entities or []:
        decision = (decisions or {}).get(entity.id) or entity.decision
        target = _ai_text_target(entity, decision)
        if target is None:
            continue
        if entity.value:
            value_targets[entity.value] = target
        for span in entity.spans or []:
            if not span.block_id or span.start is None or span.start < 0:
                continue
            start, end = int(span.start), int(span.end)
            if end <= start:
                continue
            span_targets.setdefault(span.block_id, []).append(
                (start, end, target, entity.value or ""))
    return span_targets, value_targets


def _ai_fragment_target(fragment: str, value: str, target: str) -> str:
    """span 只覆盖原值的一段时，取假值的对应片段（避免伪造出整个假值）。

    值级扩散/折行产生的 span 只覆盖原值的一部分；如果直接写整个假值，同一个
    假值会被多造出几次，破坏「假值出现次数 == 原值出现次数」不变量。这里按
    长度比例映射到假值片段，并保证**永远不返回完整假值**（除非 fragment 就是
    完整原值），否则短假值（如 12 字公司名被 16 字原值的前缀命中）会整段重复。
    """
    if not fragment or not value or fragment == value:
        return target
    flen, vlen, tlen = len(fragment), len(value), len(target or "")
    if tlen <= 0:
        return target
    if flen >= vlen or tlen <= 1:
        return AI_TEXT_LAYER_BLACK_MARK * flen
    take = max(1, min(tlen - 1, int(round(flen * tlen / vlen))))
    if value.startswith(fragment):
        return target[:take]
    if value.endswith(fragment):
        return target[-take:]
    pos = value.find(fragment)
    if pos >= 0:
        start = min(max(0, int(round(pos * tlen / vlen))), tlen - take)
        return target[start:start + take]
    # 对不上（OCR 变体/跨块碎片）：用占位，避免原值残留、也避免假值重复计数
    return AI_TEXT_LAYER_BLACK_MARK * flen


def _ai_substituted_text(text: str, block_id: str,
                         span_targets: dict[str, list[tuple[int, int, str, str]]],
                         value_targets: dict[str, str]) -> str:
    """把一块 OCR 文本里的实体原值换成假值/占位（span 内 + 全块第二处出现）。"""
    if not text:
        return text
    spans = sorted(span_targets.get(block_id) or [],
                   key=lambda item: (item[0], -(item[1] - item[0])))
    parts: list[str] = []
    cursor = 0
    for start, end, target, value in spans:
        start = max(0, min(start, len(text)))
        end = max(start, min(end, len(text)))
        if start < cursor or end <= start:
            continue
        parts.append(text[cursor:start])
        parts.append(_ai_fragment_target(text[start:end], value, target))
        cursor = end
    parts.append(text[cursor:])
    result = "".join(parts)
    # 值级扩散：span 之外的第二处出现也要换（长值优先，避免短值先吃掉长值）。
    for value, target in sorted(value_targets.items(), key=lambda kv: -len(kv[0])):
        if value and value in result:
            result = result.replace(value, target)
    # 兜底：替换后仍残留的原值再清一遍（自身假值包含原值的情况跳过，
    # 与 pipeline.verify 的「假值区间豁免」口径保持一致）。
    for value, target in sorted(value_targets.items(), key=lambda kv: -len(kv[0])):
        if value and value in result and value not in target:
            result = result.replace(value, target)
    return result


def _write_ai_text_layer_rotated(page: fitz.Page,
                                 prepared: list[tuple[Any, str]],
                                 rotation: int) -> int:
    """旋转页的不可见文本层：按阅读顺序写成「未旋转坐标里的横排」整页文本块。

    不可见文本层的位置不参与显示（render_mode=3），但会参与文本抽取：
    若仍按原 bbox 竖排写入，``get_text(sort=True)``（QA/审计读取口径）会把
    竖排字形按 x 排序成逐字换行，正文/假值会被拆散。这里直接整页横排写一遍，
    保证 sort=True/False 都能按行读出连续正文。旋转 90/270 的页面才走这里。
    """
    lines = [text.replace("\r", " ").replace("\n", " ") for _b, text in prepared]
    try:
        raw_rect = fitz.Rect(0.0, 0.0, float(page.rect.width),
                             float(page.rect.height)) * page.derotation_matrix
    except Exception:
        return 0
    raw_rect.normalize()
    if raw_rect.is_empty or raw_rect.width <= 1 or raw_rect.height <= 1:
        return 0
    body = "\n".join(lines)
    fontsize = min(AI_TEXT_LAYER_MAX_PT, 5.0)
    while fontsize >= AI_TEXT_LAYER_MIN_PT:
        try:
            status = page.insert_textbox(
                raw_rect, body, fontname="china-s", fontsize=fontsize,
                color=(0, 0, 0), align=0, overlay=True,
                render_mode=3, rotate=0,
            )
        except Exception:
            status = -1
        if status >= 0:
            return len(prepared)
        fontsize -= 0.5
    # 兜底：放不下时保证「文本层里有内容」优先于位置精度（反正不可见）
    try:
        page.insert_text(
            (raw_rect.x0, max(raw_rect.y0 + AI_TEXT_LAYER_MIN_PT,
                              raw_rect.y1 - AI_TEXT_LAYER_MIN_PT)),
            body, fontname="china-s", fontsize=AI_TEXT_LAYER_MIN_PT,
            color=(0, 0, 0), overlay=True, render_mode=3, rotate=0,
        )
        return len(prepared)
    except Exception:
        return 0


def _write_ai_text_layer(page: fitz.Page, udm_page: Page,
                         span_targets: dict[str, list[tuple[int, int, str, str]]],
                         value_targets: dict[str, str]) -> int:
    """向扫描页写不可见（render_mode=3）文本层；返回写入的块数。

    Phase A1：同 bbox + 同文本的 OCR 重复块只写一次，避免隐藏层内部再放大重复。
    """
    rotation = _rotation(page)
    prepared: list[tuple[Any, str]] = []
    seen_blocks: set[tuple[Any, ...]] = set()
    for block in udm_page.blocks:
        if block.kind != "text":
            continue
        text = block.text or ""
        if not text.strip():
            continue
        new_text = _ai_substituted_text(text, block.id, span_targets, value_targets)
        if not new_text.strip():
            continue
        bbox_key = (tuple(round(float(v), 1) for v in block.bbox)
                    if block.bbox else None)
        key = (text, bbox_key)
        if key in seen_blocks:
            continue
        seen_blocks.add(key)
        prepared.append((block, new_text))
    if not prepared:
        return 0
    if rotation in (90, 270):
        # 旋转页：整页横排写，避免 sort=True 抽取把竖排字形拆成逐字换行
        return _write_ai_text_layer_rotated(page, prepared, rotation)
    written = 0
    for block, new_text in prepared:
        if not block.bbox:
            continue
        display = fitz.Rect(block.bbox)
        try:
            raw = display * page.derotation_matrix if rotation else fitz.Rect(display)
        except Exception:
            continue
        raw.normalize()
        if raw.is_empty or raw.width <= 0.1 or raw.height <= 0.1:
            continue
        # 字号按显示行高选，尽量一行放下
        fontsize = min(AI_TEXT_LAYER_MAX_PT,
                       max(AI_TEXT_LAYER_MIN_PT, float(display.height) * 0.65))
        placed = False
        while fontsize >= AI_TEXT_LAYER_MIN_PT:
            try:
                status = page.insert_textbox(
                    raw, new_text, fontname="china-s", fontsize=fontsize,
                    color=(0, 0, 0), align=0, overlay=True,
                    render_mode=3, rotate=0,
                )
            except Exception:
                status = -1
            if status >= 0:
                placed = True
                break
            fontsize -= 0.5
        if placed:
            written += 1
            continue
        # 兜底：放不下时保证「文本层里有内容」优先于位置精度（反正不可见）
        try:
            start = fitz.Point(display.x0, display.y1)
            page.insert_text(
                start, new_text, fontname="china-s",
                fontsize=AI_TEXT_LAYER_MIN_PT, color=(0, 0, 0),
                overlay=True, render_mode=3, rotate=0,
            )
            written += 1
        except Exception:
            pass
    return written


# ──────────────────── Batch2-B4：AI 模式 FACE 误检降级 ────────────────────
#
# 背景：扫描合同页的 Haar 人脸检测会把标题/表格纹理误判成 FACE，导出后打黑块，
# 把「给 AI 看」的换名版标题等正文遮住。降级规则（只在 mode=ai 生效）：
#   * 该 FACE 所在页没有 ID_DOCUMENT / ID_PHOTO / EMBEDDED_IMAGE 实体
#     （有证件/证件头像/内嵌图片的页宁可多涂，不跳）；
#   * 且 confidence < 阈值（默认 0.85，可用 YINZHAN_AI_FACE_SKIP_MAX_CONF 覆盖）；
# human 涂黑版不做任何过滤（10/10 像素 diff=0）。
# 跳过的 FACE id 会记录在 doc.extra["ai_skipped_faces"]（含本次成品路径），
# 供 fast_verify 复检时按同一口径排除，避免「导出没画、复检却要求黑块」。

def _entity_pages(doc: Document, entity: Entity) -> set[int]:
    """实体涉及的页号集合（矩形 span 取 span.page；文本 span 反查 block 所在页）。"""
    pages: set[int] = set()
    total = len(getattr(doc, "pages", None) or [])
    for span in entity.spans or []:
        if span.rect:
            if span.page is not None:
                try:
                    page = int(span.page)
                except (TypeError, ValueError):
                    page = -1
                if 0 <= page < total:
                    pages.add(page)
            elif total == 1:
                pages.add(0)
        elif span.block_id:
            for page in doc.pages:
                if any(block.id == span.block_id for block in page.blocks):
                    pages.add(int(page.index))
                    break
    return pages


def _ai_filter_face_entities(doc: Document,
                             entities: Iterable[Entity] | None
                             ) -> tuple[list[Entity], list[Entity]]:
    """AI 模式：跳过「无证件/内嵌图页」上的低置信 FACE（误检黑块降级）。

    返回 ``(保留实体, 被跳过的 FACE 实体)``。human 模式不调用本函数。
    """
    items = list(entities or [])
    protected_pages: set[int] = set()
    for entity in items:
        if (entity.etype or "").upper() in AI_FACE_PROTECT_ETYPES:
            protected_pages |= _entity_pages(doc, entity)
    kept: list[Entity] = []
    skipped: list[Entity] = []
    for entity in items:
        if (entity.etype or "").upper() != "FACE":
            kept.append(entity)
            continue
        try:
            confidence = float(entity.confidence or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        pages = _entity_pages(doc, entity)
        # 只有「扫描页」上的 FACE 才属于本策略；文本层页保留原逻辑（避免误伤
        # 文本层 PDF 里真实证件照片/内嵌图场景）。
        scanned_only = bool(pages) and all(
            0 <= page < len(doc.pages)
            and (doc.pages[page].ref or {}).get("scanned")
            for page in pages)
        if (confidence < AI_FACE_SKIP_MAX_CONFIDENCE and scanned_only
                and not (pages & protected_pages)):
            skipped.append(entity)
        else:
            kept.append(entity)
    return kept, skipped


# ──────────────────── 扫描件成品快速复检（P2）────────────────────
#
# 背景：纯扫描 PDF 的 parse 已经把每页文字 OCR 出来；导出后再整册 OCR 一遍
# （verify 重解析 + 内嵌图文字助检）只是把同一批像素再识别一次，多页扫描件
# 因此耗时翻倍。这里提供**不削弱安全性**的快速复检：
#   1. 只在「整册都是扫描页」时启用；文本层 PDF 仍走 parser.parse 完整重解析；
#   2. 不跑 OCR：只提取成品文本层（残留/假值/隐藏文字）与元数据、隐藏数据；
#   3. 低 DPI 渲染成品页，按导出时**同一套**矩形逐项验证：
#      - 黑框：内部黑像素 ≥ 98%，证明涂黑确实落在成品上；
#      - 替换框：原图墨迹在成品同一位置仍为深色的比例 ≤ 80%（替换框先用白底
#        redaction 盖掉原图再写假值；比例接近 1 说明原图没被盖住）；
#   4. 覆盖证明：parse 阶段文本里出现的每个实体值，其字符中心必须落在某个导出
#      矩形内，否则**回退慢路径**（不冒险跳过）——「检出但没涂到」的第二处
#      出现不会因为不 OCR 而漏掉。

def _fast_verify_enabled() -> bool:
    """快速复检开关（默认开；YINZHAN_PDF_FAST_VERIFY=0 可强制回退慢路径）。"""
    raw = os.environ.get("YINZHAN_PDF_FAST_VERIFY")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _fast_recheck_dpi() -> int:
    try:
        value = int(os.environ.get("YINZHAN_PDF_FAST_VERIFY_DPI", "") or FAST_RECHECK_DPI)
    except (TypeError, ValueError):
        value = FAST_RECHECK_DPI
    return max(72, min(150, value))


def _effective_ops(ops_by_page: dict[int, list[dict[str, Any]]]) -> dict[int, list[dict[str, Any]]]:
    """按导出时同一顺序合并矩形，得到成品上真正生效的绘制操作。"""
    effective: dict[int, list[dict[str, Any]]] = {}
    for page_no, ops in (ops_by_page or {}).items():
        try:
            merged = _merge_ops(ops, [])
        except Exception:
            return {}
        if merged:
            effective[int(page_no)] = merged
    return effective


def _box_contains(outer: Box, inner: Box, eps: float = 0.05) -> bool:
    return (outer[0] - eps <= inner[0] and outer[1] - eps <= inner[1]
            and outer[2] + eps >= inner[2] and outer[3] + eps >= inner[3])


def _ops_cover_entity_spans(doc: Document, entities: Iterable[Entity],
                            ops_by_page: dict[int, list[dict[str, Any]]]) -> bool:
    """每个待处理实体的 span 都必须被成品上的某个操作矩形完整覆盖。

    有任何一个 span 换算不出矩形、或矩形没被导出操作覆盖，都说明快速通道
    无法证明它被处理过 —— 返回 False 让调用方回退完整重解析。
    """
    block_map: dict[str, tuple[int, Block]] = {}
    for page in doc.pages:
        for block in page.blocks:
            block_map[block.id] = (page.index, block)
    page_count = len(doc.pages)
    for entity in entities or []:
        if entity.status == "rejected":
            continue
        if entity.decision is not None and entity.decision.strategy == "keep":
            continue
        for span in entity.spans or []:
            if span.block_id is not None:
                hit = block_map.get(span.block_id)
                if hit is None:
                    return False
                page_no, block = hit
                rects = _span_rects_from_block(block, span)
            elif span.rect:
                page_no = (int(span.page) if span.page is not None
                           else (0 if page_count == 1 else -1))
                if page_no < 0 or page_no >= page_count:
                    return False
                normalized = _normalize_rect(span.rect, doc.pages[page_no])
                rects = [normalized] if normalized else []
            else:
                return False
            if not rects:
                return False
            ops = ops_by_page.get(page_no) or []
            for rect in rects:
                if rect[2] <= rect[0] or rect[3] <= rect[1]:
                    return False
                if not any(_box_contains(op["rect"], rect) for op in ops):
                    return False
    return True


def _block_char_centers(block: Block) -> list[tuple[float, float]]:
    """每个字符的中心点：有字符级坐标就用，OCR block 按 bbox 比例插值。"""
    text = block.text or ""
    bbox = block.bbox
    if not text or not bbox:
        return []
    chars = (block.ref or {}).get("chars")
    if isinstance(chars, list) and len(chars) >= len(text):
        centers: list[tuple[float, float]] = []
        for raw in chars[:len(text)]:
            try:
                x0, y0, x1, y1 = (float(v) for v in raw[:4])
            except Exception:
                centers.append(((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0))
            else:
                centers.append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
        return centers
    x0, y0, x1, y1 = bbox
    count = len(text)
    cy = (y0 + y1) / 2.0
    return [(x0 + (x1 - x0) * (index + 0.5) / count, cy) for index in range(count)]


def _covered_by_rects(x: float, y: float, rects: Iterable[Box], eps: float = 0.6) -> bool:
    for rect in rects:
        if (rect[0] - eps <= x <= rect[2] + eps
                and rect[1] - eps <= y <= rect[3] + eps):
            return True
    return False


def _has_uncovered_occurrences(doc: Document, entities: Iterable[Entity],
                               ops_by_page: dict[int, list[dict[str, Any]]]) -> bool:
    """parse 阶段文本里是否有实体值没落在任何导出矩形内（漏涂的第二处）。"""
    rects_by_page: dict[int, list[Box]] = {}
    for page_no, ops in (ops_by_page or {}).items():
        rects_by_page[int(page_no)] = [tuple(op["rect"]) for op in ops]
    blocks = [(page.index, block) for page in doc.pages for block in page.blocks]
    for entity in entities or []:
        if entity.status == "rejected" or not entity.value:
            continue
        if entity.decision is not None and entity.decision.strategy == "keep":
            continue
        needle = entity.value.strip()
        if len(needle) < 2:
            continue
        for page_no, block in blocks:
            text = block.text or ""
            if needle not in text:
                continue
            centers = _block_char_centers(block)
            if not centers:
                # 有文字却没有几何信息：无法证明这处出现被矩形盖住，回退慢路径
                return True
            rects = rects_by_page.get(page_no) or []
            start = 0
            while True:
                pos = text.find(needle, start)
                if pos < 0:
                    break
                for index in range(pos, min(pos + len(needle), len(centers))):
                    if not _covered_by_rects(centers[index][0], centers[index][1], rects):
                        return True
                start = pos + 1
    return False


def _render_rgb(page: fitz.Page, dpi: int):
    """按显示坐标系渲染整页（旋转页也一致），返回 HxWx3 uint8。"""
    import numpy as np

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return np.ascontiguousarray(arr[:, :, :3])


def _rect_pixels(rect: Box, dpi: int, width: int, height: int) -> tuple[int, int, int, int] | None:
    """页面坐标 -> 像素框；内缩 1px 避开 redaction 填充边缘的抗锯齿。"""
    scale = dpi / 72.0
    x0 = max(0, int(float(rect[0]) * scale))
    y0 = max(0, int(float(rect[1]) * scale))
    x1 = min(int(width), int(round(float(rect[2]) * scale)))
    y1 = min(int(height), int(round(float(rect[3]) * scale)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    if x1 - x0 > 3:
        x0 += 1
        x1 -= 1
    if y1 - y0 > 3:
        y0 += 1
        y1 -= 1
    return x0, y0, x1, y1


def _black_ratio(rgb, pixels: tuple[int, int, int, int]) -> float:
    import numpy as np

    x0, y0, x1, y1 = pixels
    sub = rgb[y0:y1, x0:x1]
    if sub.size == 0:
        return 0.0
    return float((np.max(sub, axis=2) <= FAST_BLACK_MAX).mean())


def _ink_survival(orig_rgb, out_rgb, pixels: tuple[int, int, int, int]) -> float | None:
    """原图深色墨迹在成品同一位置仍为深色的比例；墨迹太少时返回 None。"""
    import numpy as np

    x0, y0, x1, y1 = pixels
    original = np.max(orig_rgb[y0:y1, x0:x1], axis=2)
    output = np.max(out_rgb[y0:y1, x0:x1], axis=2)
    ink = original <= 96
    count = int(ink.sum())
    if count < FAST_MIN_INK_PIXELS:
        return None
    return float((output[ink] <= 96).mean())


def _fast_pixel_check(pdf: fitz.Document, doc: Document,
                      ops_by_page: dict[int, list[dict[str, Any]]]):
    """逐页低 DPI 渲染并核验导出矩形；无法证明时返回 None（回退慢路径）。"""
    dpi = _fast_recheck_dpi()
    needs_original = any(not op["black"] for ops in ops_by_page.values() for op in ops)
    source = None
    if needs_original:
        try:
            source = _open_pdf(Path(doc.path))
        except Exception:
            return None
    black_checked = replacement_checked = rendered = 0
    unblacked: list[int] = []
    untouched: list[int] = []
    try:
        for page_no in sorted(ops_by_page):
            if page_no < 0 or page_no >= pdf.page_count:
                return None
            ops = ops_by_page[page_no]
            try:
                out_rgb = _render_rgb(pdf[page_no], dpi)
            except Exception:
                return None
            rendered += 1
            orig_rgb = None
            if source is not None and any(not op["black"] for op in ops):
                if page_no >= source.page_count:
                    return None
                try:
                    orig_rgb = _render_rgb(source[page_no], dpi)
                except Exception:
                    return None
                if orig_rgb.shape != out_rgb.shape:
                    return None
            for op in ops:
                rect = op.get("rect")
                if not rect:
                    continue
                pixels = _rect_pixels(rect, dpi, out_rgb.shape[1], out_rgb.shape[0])
                if pixels is None:
                    return None
                if op["black"]:
                    black_checked += 1
                    if _black_ratio(out_rgb, pixels) < FAST_BLACK_MIN:
                        unblacked.append(int(page_no))
                else:
                    if orig_rgb is None:
                        return None
                    replacement_checked += 1
                    survival = _ink_survival(orig_rgb, out_rgb, pixels)
                    if survival is not None and survival > FAST_REPAINT_MAX:
                        untouched.append(int(page_no))
    finally:
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
    return black_checked, unblacked, replacement_checked, untouched, rendered


# ────────────────────────────── 解析器 ──────────────────────────────

class PdfParser(registry.Parser):
    """PDF 解析器：文本层优先，扫描页降级 OCR。"""

    format_id = "pdf"
    extensions = (".pdf",)
    can_render = True

    @classmethod
    def sniff(cls, path: Path) -> float:
        if Path(path).suffix.lower() in cls.extensions:
            return 1.0
        try:
            with open(path, "rb") as handle:
                return 0.9 if handle.read(5) == b"%PDF-" else 0.0
        except OSError:
            return 0.0

    def export_black_rects(self, doc: Document, entities: list[Entity],
                           decisions: dict[str, Decision] | None = None
                           ) -> dict[int, list[Box]] | None:
        """返回与 human 导出完全一致的涂黑矩形（按页、页面显示坐标）。

        与 `export()` 共用 `_collect_ops` + `_merge_ops`：在线预览按这里返回的
        矩形绘制，因此「预览所见」=「下载成品所涂」，不会再出现预览整行黑、
        成品只涂实体的情况。
        """
        try:
            warnings: list[str] = []
            ops_by_page = _collect_ops(doc, entities, decisions or {}, warnings)
        except Exception:
            return None
        result: dict[int, list[Box]] = {}
        for page_no, ops in (ops_by_page or {}).items():
            try:
                merged = _merge_ops(ops, [])
            except Exception:
                return None
            rects = [op["rect"] for op in merged if op["black"]]
            rects = [r for r in rects if r[2] > r[0] and r[3] > r[1]]
            if rects:
                result[int(page_no)] = rects
        return result

    def parse(self, path: Path, ctx: ParseContext) -> Document:
        path = Path(path)
        pdf = _open_pdf(path)
        doc = Document(doc_id=_new_doc_id(), path=str(path), fmt="pdf",
                       meta=_document_meta(pdf))
        # P0 隐藏数据统计（附件/批注/表单/JS）：写 meta 供 pipeline.verify 复检，
        # 导出时默认剥离；显式 keep_pdf_hidden=True 才保留。
        try:
            hidden_meta, hidden_summary = _pdf_hidden_meta(pdf)
            doc.meta.update(hidden_meta)
            if hidden_summary:
                doc.warnings.append(
                    f"检测到 PDF 隐藏数据（{hidden_summary}），导出时将默认剥离；"
                    "如需保留请显式使用 keep_pdf_hidden=True")
        except Exception as exc:
            doc.warnings.append(f"PDF 隐藏数据统计失败：{exc}")
        try:
            try:
                encryption = str((pdf.metadata or {}).get("encryption") or "")
            except Exception:
                encryption = ""
            if pdf.is_encrypted or encryption:
                doc.warnings.append("PDF 使用空密码加密，已解密处理")
            try:
                page_count = int(pdf.page_count)
            except Exception:
                page_count = 0
            if page_count == 0:
                doc.warnings.append("PDF 没有页面")
            for page_index in range(page_count):
                # 用户点了「停止」就立刻收手，别让扫描件把整台机器占住
                ctx.check_stop()
                try:
                    page = pdf[page_index]
                    udm_page = Page(
                        index=page_index,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        ref={"rotation": _rotation(page)},
                    )
                    blocks = _page_text_blocks(page, page_index)
                    text = "".join(block.text for block in blocks)
                    scanned = len(text.strip()) < MIN_TEXT_CHARS
                    udm_page.ref["scanned"] = bool(scanned)
                    if scanned:
                        ctx.report(page_index, page_count,
                                   f"正在识别第 {page_index + 1}/{page_count} 页（扫描件）")
                        ocr_blocks = _ocr_page_blocks(page, page_index, ctx, doc.warnings)
                        if ocr_blocks is not None:
                            if ocr_blocks:
                                blocks = ocr_blocks
                            elif blocks:
                                doc.warnings.append(
                                    f"第 {page_index + 1} 页 OCR 未识别到文字，"
                                    "已保留原文本层")
                    udm_page.blocks = blocks
                    doc.pages.append(udm_page)
                    # OCR 读不出来的东西（红章、手写签名、码类/人脸…）
                    _collect_visual(page, blocks, page_index, doc,
                                    scanned=scanned, ctx=ctx)
                    ctx.report(page_index + 1, page_count,
                               f"已读取 {page_index + 1}/{page_count} 页")
                except Exception as exc:
                    if isinstance(exc, ParseError):
                        raise
                    doc.warnings.append(f"第 {page_index + 1} 页解析失败：{exc}")
                    doc.pages.append(Page(index=page_index, width=0.0, height=0.0))
        finally:
            try:
                pdf.close()
            except Exception:
                pass
        return doc

    def fast_verify(self, exported: Path, entities: list[Entity],
                    ctx: ParseContext | None = None, *, doc: Document | None = None,
                    options: dict | None = None) -> dict | None:
        """扫描件成品的快速复检（不跑 OCR）。

        返回 ``{"doc": 回填了成品文本层/元数据的 Document, "info": 统计}``；
        任何「无法证明安全」的情况都返回 ``None``，由 ``pipeline.verify``
        回退到原来的完整重解析 + OCR 复检。判定条件：

        * 原始文档整册都是扫描页（``Page.ref["scanned"]``），否则不适用；
        * 每个待处理实体的 span 都能换算成矩形，且被成品上的操作矩形覆盖；
        * parse 阶段文本里实体值的每一处出现，字符中心都被导出矩形覆盖
          （漏涂的第二处不会因为不 OCR 而漏掉）；
        * 成品页数/页面尺寸/旋转与原件一致，文本层/元数据/隐藏数据可读；
        * 每个黑框内部 ≥98% 黑像素；每个替换框内原图墨迹存活率 ≤80%。
        """
        if not _fast_verify_enabled():
            return None
        if (options or {}).get("keep_pdf_hidden"):
            # 显式保留批注/表单/附件时，批注外观可能盖在涂黑框上影响像素判定；
            # 这类非常规选项直接走完整重解析，保证判定口径不变。
            return None
        if doc is None or getattr(doc, "fmt", "") != "pdf":
            return None
        pages = list(getattr(doc, "pages", None) or [])
        if not pages:
            return None
        # 文本层 PDF 仍走原有重解析 + 残留检查，行为完全不变
        if any(not bool((page.ref or {}).get("scanned")) for page in pages):
            return None
        # Batch2-B4：本次 AI 导出按策略跳过的 FACE 不参与快速复检的
        # 「覆盖/像素」判定（按成品路径绑定，后续 human 导出不会误用同一策略）。
        skip_ids: set[str] = set()
        try:
            marker = (getattr(doc, "extra", None) or {}).get(
                "ai_skipped_faces") or {}
            if str(marker.get("path") or "") == str(Path(exported).resolve()):
                skip_ids = {str(item) for item in (marker.get("ids") or [])}
        except Exception:
            skip_ids = set()
        # 没有决策的实体（如待确认的大面积码框）在导出阶段可能被过滤，也可能被
        # 调用方兜底成 black；快速通道无法确认它到底画没画，直接回退慢路径。
        if any(e.status != "rejected" and e.decision is None
               for e in (entities or [])):
            return None
        active = [e for e in entities or []
                  if e.status != "rejected" and e.decision is not None
                  and e.id not in skip_ids]
        decisions = {e.id: e.decision for e in active}
        collect_warnings: list[str] = []
        try:
            raw_ops = _collect_ops(doc, active, decisions, collect_warnings)
        except Exception:
            return None
        if collect_warnings:
            # 有 span 换算不出矩形（定位块缺失/页号非法…），导出本身可能漏涂
            return None
        ops_by_page = _effective_ops(raw_ops)
        if not ops_by_page:
            # 没有绘制操作时无法证明「定位块仍在」；保守回退
            if any(getattr(e, "spans", None) for e in active):
                return None
        if not _ops_cover_entity_spans(doc, active, ops_by_page):
            return None
        if _has_uncovered_occurrences(doc, entities or [], ops_by_page):
            return None
        try:
            pdf = fitz.open(str(exported))
        except Exception:
            return None
        try:
            if int(pdf.page_count) != len(pages):
                return None
            for index, out_page in enumerate(pdf):
                original = pages[index]
                if abs(float(out_page.rect.width) - float(original.width)) > 0.5:
                    return None
                if abs(float(out_page.rect.height) - float(original.height)) > 0.5:
                    return None
                want_rotation = int((original.ref or {}).get("rotation") or 0)
                if _rotation(out_page) != want_rotation:
                    return None
            # 只读文本层：没有任何 OCR
            re_doc = Document(doc_id=_new_doc_id(), path=str(exported),
                              fmt="pdf", meta=_document_meta(pdf))
            try:
                hidden_meta, _summary = _pdf_hidden_meta(pdf)
            except Exception:
                return None
            re_doc.meta.update(hidden_meta)
            for index in range(len(pages)):
                page = pdf[index]
                re_doc.pages.append(Page(
                    index=index,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                    blocks=_page_text_blocks(page, index),
                    ref={"rotation": _rotation(page), "scanned": False},
                ))
            checked = _fast_pixel_check(pdf, doc, ops_by_page)
        except Exception:
            return None
        finally:
            try:
                pdf.close()
            except Exception:
                pass
        if checked is None:
            return None
        black_checked, unblacked, replacement_checked, untouched, rendered = checked
        info = {
            "used": True,
            "ocr_calls": 0,
            "pages": len(pages),
            "rendered_pages": rendered,
            "black_checked": black_checked,
            "replacement_checked": replacement_checked,
            "unblacked": list(unblacked),
            "untouched": list(untouched),
        }
        return {"doc": re_doc, "info": info}

    def export(self, doc: Document, entities: list[Entity],
               decisions: dict[str, Decision], out_path: Path,
               options: dict) -> ExportResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        # Batch2-B4：AI 模式下把「无证件/内嵌图页」上的低置信 FACE 误检降级
        # （不画黑块）；human 模式不做过滤，涂黑版保持原样。
        ai_mode = _ai_text_layer_enabled(options)
        skipped_faces: list[Entity] = []
        if ai_mode and AI_FACE_SKIP_ENABLED:
            try:
                entities, skipped_faces = _ai_filter_face_entities(doc, entities)
            except Exception as exc:
                skipped_faces = []
                warnings.append(f"AI 模式 FACE 过滤跳过：{exc}")
            if skipped_faces:
                # 每页一条 warning，便于审计解释「AI 版为什么少了一块黑」
                page_counts: dict[int, int] = {}
                for entity in skipped_faces:
                    pages = _entity_pages(doc, entity) or {-1}
                    for page_no in pages:
                        page_counts[int(page_no)] = (
                            page_counts.get(int(page_no), 0) + 1)
                for page_no in sorted(page_counts):
                    where = f"第 {page_no + 1} 页 " if page_no >= 0 else ""
                    warnings.append(
                        f"{where}AI 视图跳过 {page_counts[page_no]} 个自动人脸误检"
                        "（仅换名版；涂黑版不变）")
        ops_by_page = _collect_ops(doc, entities, decisions, warnings)

        source = Path(doc.path)
        target = out_path
        same_file = False
        try:
            same_file = source.resolve() == out_path.resolve()
        except OSError:
            same_file = False
        if same_file:
            target = out_path.with_suffix(".tmp" + (out_path.suffix or ".pdf"))

        pdf = _open_pdf_for_export(source)

        # P0 隐藏数据：默认先剥离再 redaction（避免批注/控件干扰 redaction 与成品复检）。
        keep_hidden = bool((options or {}).get("keep_pdf_hidden"))
        hidden_before: dict[str, Any] = {}
        removed_hidden: dict[str, int] = {}
        try:
            hidden_before = _pdf_hidden_stats(pdf)
        except Exception as exc:
            warnings.append(f"PDF 隐藏数据统计失败：{exc}")
        if keep_hidden:
            if _pdf_hidden_positive(hidden_before):
                warnings.append(
                    "keep_pdf_hidden=True：按调用方要求保留 PDF 隐藏数据（"
                    + _pdf_hidden_summary(hidden_before)
                    + "）；附件/批注/表单/JS 可能含原文，存在外发泄漏风险")
        else:
            try:
                removed_hidden = _strip_pdf_hidden(pdf, warnings)
                removed_summary = _pdf_hidden_summary(removed_hidden)
                if removed_summary:
                    warnings.append("已剥离 PDF 隐藏数据：" + removed_summary)
                if removed_hidden.get("thumbnails"):
                    warnings.append(
                        f"已删除 {removed_hidden['thumbnails']} 个页面缩略图"
                        "（缩略图可能残留未脱敏画面）")
            except Exception as exc:
                warnings.append(f"PDF 隐藏数据剥离失败：{exc}")

        # F3：换名版扫描页要补「不可见文本层」。处理范围 = 有绘制操作的页
        # ∪ 全部扫描页（没有实体的扫描页也要让 AI 读到正文）。
        ai_text_layer = ai_mode
        ai_span_targets: dict[str, list[tuple[int, int, str, str]]] = {}
        ai_value_targets: dict[str, str] = {}
        pages_to_draw = sorted(ops_by_page)
        if ai_text_layer:
            ai_span_targets, ai_value_targets = _ai_text_targets(entities, decisions)
            scanned_pages = {int(p.index) for p in doc.pages
                             if (p.ref or {}).get("scanned")}
            pages_to_draw = sorted(set(pages_to_draw) | scanned_pages)
        pages_by_index = {int(p.index): p for p in doc.pages}

        try:
            for page_no in pages_to_draw:
                try:
                    if page_no < 0 or page_no >= pdf.page_count:
                        warnings.append(f"第 {page_no + 1} 页不存在，已跳过")
                        continue
                    page = pdf[page_no]
                    udm_page = pages_by_index.get(page_no)
                    scanned_page = bool(
                        udm_page is not None
                        and (udm_page.ref or {}).get("scanned"))
                    replacements: list[tuple[fitz.Rect, str]] = []
                    ops = _merge_ops(ops_by_page.get(page_no, []), warnings)
                    for op in ops:
                        rect = _to_pdf_space(op["rect"], page)
                        rect.normalize()
                        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                            continue
                        if op["black"]:
                            page.add_redact_annot(rect, fill=(0, 0, 0))
                        else:
                            page.add_redact_annot(rect, fill=(1, 1, 1))
                            replacements.append((rect, op["text"]))
                    if ops:
                        page.apply_redactions()
                    # F3：扫描页先写不可见文本层，再写可见替换文字，
                    # 纯文本抽取时正文在前、假值在后。
                    if ai_text_layer and scanned_page:
                        try:
                            _write_ai_text_layer(page, udm_page,
                                                 ai_span_targets,
                                                 ai_value_targets)
                        except Exception as exc:
                            warnings.append(
                                f"第 {page_no + 1} 页 AI 文本层写入失败：{exc}")
                    # 必须在 apply_redactions 之后写回替换文字。
                    # 扫描页 AI 模式改图片渲染：可见层保留假值，但文本层只有
                    # 隐藏层一份（否则同一假值会在 get_text 里出现两次）。
                    for rect, text in replacements:
                        placed = False
                        if ai_text_layer and scanned_page:
                            placed = _insert_replacement_image(page, rect, text)
                        if not placed:
                            _insert_replacement(page, rect, text)
                except Exception as exc:
                    warnings.append(f"第 {page_no + 1} 页导出失败：{exc}")

            try:
                pdf.set_metadata({})
            except Exception as exc:
                warnings.append(f"清空 PDF 元数据失败：{exc}")
            try:
                pdf.del_xml_metadata()
            except Exception as exc:
                warnings.append(f"删除 PDF XMP 元数据失败：{exc}")
            try:
                pdf.save(str(target), garbage=4, deflate=True)
            except Exception as exc:
                raise ExportError(f"保存 PDF 失败：{exc}") from exc
        except ExportError:
            if same_file:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        finally:
            try:
                pdf.close()
            except Exception:
                pass

        if same_file:
            try:
                os.replace(target, out_path)
            except OSError as exc:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ExportError(f"覆盖原 PDF 失败：{exc}") from exc
        # 解析器侧成品复检：P1-B 内嵌图片区域必须已被实心涂黑（黑像素≥95%）。
        # 不通过只写 warning，最终由 pipeline.verify 的视觉残留/复检兜底。
        try:
            _verify_embedded_images_black(out_path, entities, warnings)
        except Exception as exc:
            warnings.append(f"内嵌图片涂黑复检跳过：{type(exc).__name__}")
        # 解析器侧成品复检：默认剥离模式下，附件/批注/表单/JS 必须为 0。
        # （keep_pdf_hidden=True 显式保留时跳过，由 pipeline.verify 按同一 option 放行）
        if not keep_hidden:
            try:
                leftovers = _verify_pdf_hidden_cleared(out_path)
            except Exception as exc:
                leftovers = {"error": f"{type(exc).__name__}: {exc}"}
            if leftovers:
                if not same_file:
                    try:
                        out_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise ExportError(
                    "导出后 PDF 隐藏数据复检失败："
                    + "；".join(f"{k}={v}" for k, v in leftovers.items()))
        try:
            doc.extra["pdf_hidden"] = {
                "before": {key: hidden_before.get(key) for key in HIDDEN_META_KEYS},
                "removed": dict(removed_hidden),
                "kept": keep_hidden,
            }
        except Exception:
            pass

        # Batch2-B4：记录本次 AI 导出跳过的 FACE（绑定成品路径），
        # 供 fast_verify 用同一口径复检（human 导出的路径不同，不会误用）。
        if ai_mode and skipped_faces:
            try:
                doc.extra["ai_skipped_faces"] = {
                    "path": str(out_path.resolve()),
                    "ids": [str(entity.id) for entity in skipped_faces],
                }
            except Exception:
                pass

        doc.extra["out_path"] = str(out_path)
        try:
            size = out_path.stat().st_size
        except OSError:
            size = 0
        return ExportResult(path=str(out_path), warnings=warnings, size=size)

    def render(self, doc: Document, page: int, mode: str = "orig") -> bytes | None:
        path = Path(doc.path)
        out_path = (doc.extra or {}).get("out_path")
        if mode == "out" and out_path:
            path = Path(out_path)
        if not path.is_file():
            return None
        try:
            with fitz.open(str(path)) as pdf:
                index = int(page)
                if index < 0 or index >= pdf.page_count:
                    return None
                pixmap = pdf[index].get_pixmap(dpi=RENDER_DPI)
                return pixmap.tobytes("png")
        except Exception:
            return None


registry.register_parser(PdfParser)
