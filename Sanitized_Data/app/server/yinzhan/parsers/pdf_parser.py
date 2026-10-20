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


def _collect_visual(page: fitz.Page, blocks: list[Block], page_index: int,
                    doc: Document, scanned: bool = False) -> None:
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
                    options={"text_boxes": scan_boxes})
                doc.visual_marks.extend(mediascan.to_visual_marks(
                    marks, scale=72.0 / scan_dpi, page=page_index))
                doc.warnings.extend(scan_warns)
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


def _merge_ops(ops: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    """合并交叠矩形：黑框彼此合并并优先；同文本的替换框合并，避免 redaction 互相切割。"""
    black_rects: list[Box] = []
    for op in ops:
        if not op["black"]:
            continue
        merged = op["rect"]
        rest: list[Box] = []
        for existing in black_rects:
            if box_intersects(merged, existing):
                merged = _union(merged, existing)
            else:
                rest.append(existing)
        changed = True
        while changed:
            changed = False
            for existing in list(rest):
                if box_intersects(merged, existing):
                    merged = _union(merged, existing)
                    rest.remove(existing)
                    changed = True
        rest.append(merged)
        black_rects = rest

    replacements: list[dict[str, Any]] = []
    for op in ops:
        if op["black"]:
            continue
        if any(box_intersects(op["rect"], rect) for rect in black_rects):
            continue
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


def _insert_replacement(page: fitz.Page, rect: fitz.Rect, text: str) -> None:
    """redaction 之后把替换文字写回矩形；字号按矩形高度估算并逐步收缩。"""
    text = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if not text or rect.width <= 0.2 or rect.height <= 0.2:
        return
    box = fitz.Rect(rect)
    pad = min(0.6, float(box.height) * 0.05)
    inner = fitz.Rect(box.x0 + pad, box.y0 + pad, box.x1 - pad, box.y1 - pad)
    if inner.is_empty:
        inner = box
    size = max(MIN_FONT_SIZE, float(inner.height) * 0.72)
    while size >= MIN_FONT_SIZE:
        try:
            status = page.insert_textbox(
                inner, text, fontname="china-s", fontsize=size,
                color=(0, 0, 0), align=0, overlay=True,
            )
        except Exception:
            status = -1
        if status >= 0:
            return
        size *= 0.85
    # 兜底：矩形太小放不下，直接写一行，保证假值确实落在原位置
    try:
        page.insert_text(
            (inner.x0, min(inner.y1, inner.y0 + inner.height * 0.85)), text,
            fontname="china-s",
            fontsize=max(MIN_FONT_SIZE, min(float(rect.height) * 0.72, 12.0)),
            color=(0, 0, 0), overlay=True,
        )
    except Exception:
        pass


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

    def parse(self, path: Path, ctx: ParseContext) -> Document:
        path = Path(path)
        pdf = _open_pdf(path)
        doc = Document(doc_id=_new_doc_id(), path=str(path), fmt="pdf",
                       meta=_document_meta(pdf))
        # P0 隐藏数据统计（附件/批注/表单/JS）：写 meta 供 pipeline.verify 复检，
        # 导出时默认剥离；显式 keep_pdf_hidden=True 才保留。
        try:
            hidden = _pdf_hidden_stats(pdf)
            for key in ("embedded_files", "pdf_annotations", "pdf_forms"):
                doc.meta[key] = int(hidden.get(key) or 0)
            if hidden.get("embedded_file_names"):
                doc.meta["embedded_file_names"] = list(hidden["embedded_file_names"])
            if hidden.get("pdf_annotation_types"):
                doc.meta["pdf_annotation_types"] = dict(hidden["pdf_annotation_types"])
            doc.meta["pdf_js"] = bool(hidden.get("pdf_js"))
            hidden_summary = _pdf_hidden_summary(hidden)
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
                    _collect_visual(page, blocks, page_index, doc, scanned=scanned)
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

    def export(self, doc: Document, entities: list[Entity],
               decisions: dict[str, Decision], out_path: Path,
               options: dict) -> ExportResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
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

        try:
            for page_no in sorted(ops_by_page):
                try:
                    if page_no < 0 or page_no >= pdf.page_count:
                        warnings.append(f"第 {page_no + 1} 页不存在，已跳过")
                        continue
                    page = pdf[page_no]
                    replacements: list[tuple[fitz.Rect, str]] = []
                    ops = _merge_ops(ops_by_page[page_no], warnings)
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
                    # 必须在 apply_redactions 之后写回替换文字
                    for rect, text in replacements:
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
