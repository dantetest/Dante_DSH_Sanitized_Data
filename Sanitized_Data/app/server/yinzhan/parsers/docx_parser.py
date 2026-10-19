"""DOCX 解析器与破坏性导出器。

parse
    用 python-docx 打开文档，把正文段落、表格（单元格）、内容控件、页眉页脚
    归一化成 UDM 的 text Block；表格另有一个 kind="table" 的 Block 供前端排版。
    **Block.text 严格等于该段落 ``"".join(run.text for run in p.runs)``**，
    保证检测出的字符区间在导出时可以原样映射回 run。

export
    复制原文件到成品路径，在副本上用 python-docx 真正改写文字（破坏性重写）：
    - ``black``：用 “█” 覆盖原长度，原文字彻底消失；
    - ``surrogate`` / ``mask``：直接写入 replacement，长度可变；
    - ``drop``：删除该区间文字；
    随后清空 core properties、docProps/app.xml 里的公司/标题信息，删除批注、
    修订节点、自定义属性、CustomXML、缩略图等隐藏部件（zip 级重写），再保存。

    额外做一次「值级传播」：检测器漏掉的 block 里若仍出现同一实体值，也按同一
    决策覆盖（宁可多涂不可漏），并写 warning 说明覆盖处数。

已知边界（会写 warning，不静默丢弃）：
    - python-docx 的 ``Paragraph.runs`` 不含超链接内的 run，超链接显示文字不参与
      检测；域代码、文本框、脚注尾注、嵌套表格、内嵌对象/图片同样只告警。
    - python-docx 没有自定义属性 API，``docProps/custom.xml`` 在 zip 级删除。
"""
from __future__ import annotations

import os
import posixpath
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from ..registry import Parser, register_parser
from ..udm import (
    Block, Cell, Decision, Document, Entity, ExportError, ExportResult,
    Page, ParseContext,
)

# ── 可选依赖：环境里必装，但缺失时降级成 warning 而不是让插件加载失败 ──
try:  # pragma: no cover - 环境已装
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    _DOCX_OK = True
    _DOCX_ERR = ""
except Exception as _exc:  # pragma: no cover
    docx = None  # type: ignore[assignment]
    qn = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Paragraph = None  # type: ignore[assignment]
    _DOCX_OK = False
    _DOCX_ERR = repr(_exc)

try:  # pragma: no cover - 环境已装
    from lxml import etree

    _LXML_OK = True
except Exception:  # pragma: no cover
    etree = None  # type: ignore[assignment]
    _LXML_OK = False

A4_WIDTH = 595.0
A4_HEIGHT = 842.0
BLACK_CHAR = "█"

# 修订 / 批注相关节点：直接删除（w:ins 里的内容属于未接受的修订，一并删除更安全）
_REVISION_TAGS = frozenset(
    {
        "ins", "del", "moveFrom", "moveTo",
        "commentRangeStart", "commentRangeEnd", "commentReference",
        "rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange",
        "tcPrChange", "numberingChange",
        "moveFromRangeStart", "moveFromRangeEnd",
        "moveToRangeStart", "moveToRangeEnd",
        "cellIns", "cellDel", "cellMerge",
        "customXmlInsRangeStart", "customXmlInsRangeEnd",
        "customXmlDelRangeStart", "customXmlDelRangeEnd",
        "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
        "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd",
    }
)

# settings.xml 里可能泄漏本机路径 / 口令哈希 / 修订记录的节点
_SETTINGS_DROP_TAGS = frozenset(
    {"documentProtection", "writeProtection", "attachedTemplate",
     "mailMerge", "docVars", "trackChanges", "rsids"}
)

# zip 级需要删除的部件（小写匹配）
_REMOVE_PART_EXACT = frozenset(
    {
        "word/comments.xml", "word/commentsextended.xml", "word/commentsids.xml",
        "word/commentsextensible.xml", "word/people.xml",
        "docprops/custom.xml", "docprops/thumbnail.jpeg", "docprops/thumbnail.jpg",
        "docprops/thumbnail.png", "docprops/thumbnail.wmf", "docprops/thumbnail.emf",
    }
)
_REMOVE_PART_RE = (
    re.compile(r"^word/comments.*\.xml$"),
    re.compile(r"^docprops/thumbnail\..*$"),
    re.compile(r"^customxml/.*$"),
)

# 需要从中提取作者/公司等元数据的 app.xml 标签
_APP_META_TAGS = {
    "Company": "company",
    "Manager": "manager",
    "Template": "template",
    "HyperlinkBase": "hyperlinkbase",
}
_CORE_META_FIELDS = (
    "author", "last_modified_by", "title", "subject", "keywords",
    "comments", "category", "content_status", "identifier", "language",
)


# ══════════════════════════════ 小工具 ══════════════════════════════

def _localname(tag: Any) -> str:
    """去掉 XML 命名空间，只留本地名。"""
    text = tag if isinstance(tag, str) else str(tag)
    if text.startswith("{"):
        return text.rsplit("}", 1)[-1]
    return text


def _paragraph_text(paragraph: Any) -> str:
    """段落文字：严格用 run 文本拼接（与导出偏移一致）。"""
    return "".join((run.text or "") for run in paragraph.runs)


def _safe_style_name(paragraph: Any) -> str:
    try:
        return str(paragraph.style.name or "")
    except Exception:
        return ""


def _cell_text(cell: Any) -> str:
    """单元格文字：各段落 run 文本用 \\n 连接（导出按同样方式换算偏移）。"""
    return "\n".join(_paragraph_text(p) for p in cell.paragraphs)


def _fmt_meta_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return ""


def _replacement_text(decision: Decision, length: int) -> str | None:
    """把 Decision 翻译成实际写入的字符串；keep 返回 None。"""
    if decision.strategy == "keep":
        return None
    if decision.strategy == "drop":
        return ""
    if decision.black or decision.strategy == "black" or decision.replacement is None:
        return BLACK_CHAR * max(0, length)
    return decision.replacement


def _propagation_rules(
    entities: list[Entity], decisions: dict[str, Decision], warnings: list[str]
) -> dict[str, str]:
    """值级传播规则：同一实体值在检测范围外的其它出现也按同一决策覆盖。

    启发式检测器可能漏掉页眉、表格、批注锚点等 block，而出口自检按「成品里
    是否还有该值」判定残留；这里做一次防御性传播，宁可多涂也不漏。
    单字符值不传播（会把整篇文档涂花），只记 warning。
    """
    rules: dict[str, str] = {}
    short: set[str] = set()
    for entity in entities:
        if entity.status == "rejected" or not entity.value:
            continue
        decision = decisions.get(entity.id) or entity.decision
        if decision is None:
            decision = Decision(strategy="black", black=True)
        if decision.strategy == "keep":
            continue
        if len(entity.value) < 2:
            short.add(entity.value)
            continue
        replacement = _replacement_text(decision, len(entity.value))
        if replacement is None:
            continue
        existing = rules.get(entity.value)
        if existing is not None and existing != replacement:
            warnings.append(
                f"同一值「{entity.value[:12]}」存在不同的替换决策，已按涂黑处理"
            )
            rules[entity.value] = BLACK_CHAR * len(entity.value)
            continue
        rules[entity.value] = replacement
    if short:
        warnings.append(f"有 {len(short)} 个单字符实体值未做值级传播，请人工复核")
    return rules


def _propagate_in_blocks(
    block_by_id: dict[str, Block],
    tasks: dict[str, list[tuple[int, int, str]]],
    rules: dict[str, str],
) -> int:
    """把值级传播补进替换任务表（按原始 block 偏移），返回新增处数。"""
    added = 0
    ordered = sorted(rules.items(), key=lambda item: len(item[0]), reverse=True)
    for block_id, block in block_by_id.items():
        if block.kind != "text" or not block.text:
            continue
        text = block.text
        occupied = tasks.get(block_id, [])
        additions: list[tuple[int, int, str]] = []
        for value, replacement in ordered:
            start = 0
            while True:
                index = text.find(value, start)
                if index < 0:
                    break
                end = index + len(value)
                overlaps = any(
                    not (end <= old_start or index >= old_end)
                    for old_start, old_end, _old in occupied
                ) or any(
                    not (end <= old_start or index >= old_end)
                    for old_start, old_end, _old in additions
                )
                if not overlaps:
                    additions.append((index, end, replacement))
                start = end
        if additions:
            tasks.setdefault(block_id, []).extend(additions)
            added += len(additions)
    return added


def _remove_element(element: Any) -> None:
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


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


def _strip_revisions(root: Any) -> int:
    """删除 root 下所有修订/批注标记节点，返回删除个数。"""
    removed = 0
    for element in list(root.iter()):
        try:
            tag = _localname(element.tag)
        except Exception:
            continue
        if tag in _REVISION_TAGS:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
                removed += 1
    return removed


def _clean_settings(document: Any) -> int:
    """清掉 settings.xml 里的保护口令、附加模板、邮件合并数据源等。"""
    try:
        settings = document.settings.element
    except Exception:
        return 0
    removed = 0
    for element in list(settings.iter()):
        if _localname(element.tag) in _SETTINGS_DROP_TAGS:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
                removed += 1
    return removed


# ══════════════════════════════ 导出：run 级替换 ══════════════════════════════

def _replace_in_paragraph(paragraph: Any, start: int, end: int, new: str) -> bool:
    """把段落字符区间 [start, end) 换成 new，必要时拆分/合并 run。

    段落文本按 ``"".join(run.text for run in paragraph.runs)`` 计算；替换后的新文字
    写入区间起始 run，保留该 run 的字体格式，其余覆盖到的 run 删除或截断。
    """
    runs = list(paragraph.runs)
    if not runs:
        return False
    texts = [(run.text or "") for run in runs]
    total = sum(len(text) for text in texts)
    start = max(0, min(int(start), total))
    end = max(0, min(int(end), total))
    if start > end:
        start, end = end, start

    # 纯插入：找插入点所在 run，在它内部插入
    if start == end:
        if not new:
            return True
        acc = 0
        for run, text in zip(runs, texts):
            length = len(text)
            if start <= acc + length:
                offset = start - acc
                run.text = text[:offset] + new + text[offset:]
                return True
            acc += length
        # 落在段落末尾
        runs[-1].text = texts[-1] + new
        return True

    # 定位起始 run i（覆盖 start 的 run）
    acc = 0
    i = -1
    i_off = 0
    for idx, text in enumerate(texts):
        length = len(text)
        if length and start < acc + length:
            i, i_off = idx, start - acc
            break
        acc += length
    if i < 0:
        return False

    # 定位结束 run j（覆盖 end-1 的 run）
    acc = 0
    j = -1
    j_off = 0
    for idx, text in enumerate(texts):
        length = len(text)
        if length and end <= acc + length:
            j, j_off = idx, end - acc
            break
        acc += length
    if j < 0 or j < i:
        return False

    prefix = texts[i][:i_off]
    suffix = texts[j][j_off:]
    if i == j:
        runs[i].text = prefix + new + suffix
        return True

    # 删除 (i, j) 之间的 run，再处理首尾
    for idx in range(j - 1, i, -1):
        _remove_element(runs[idx]._element)
    runs[i].text = prefix + new
    runs[j].text = suffix
    return True


def _replace_in_cell(cell: Any, start: int, end: int, new: str) -> bool:
    """把单元格（多段落以 \\n 连接）的字符区间换成 new。"""
    paragraphs = list(cell.paragraphs)
    if not paragraphs:
        return False
    texts = [_paragraph_text(p) for p in paragraphs]
    total = sum(len(t) for t in texts) + max(0, len(texts) - 1)
    start = max(0, min(int(start), total))
    end = max(0, min(int(end), total))
    if start > end:
        start, end = end, start

    pos = 0
    placed = False
    for paragraph, text in zip(paragraphs, texts):
        length = len(text)
        p_start, p_end = pos, pos + length
        pos = p_end + 1  # 段落间的 "\n" 占一个字符
        if end < p_start or start > p_end:
            continue
        lo = max(start, p_start)
        hi = min(end, p_end)
        if hi < lo:
            continue
        if not placed:
            local_start = 0 if start <= p_start else lo - p_start
            local_end = hi - p_start
            _replace_in_paragraph(paragraph, local_start, local_end, new)
            placed = True
        else:
            _replace_in_paragraph(paragraph, 0, hi - p_start, "")
    return placed


# ══════════════════════════════ zip 级加固 ══════════════════════════════

def _resolve_rel_target(rels_name: str, target: str) -> str:
    """把 .rels 里的相对 Target 解析成包内绝对部件名。"""
    rel_name = rels_name.replace("\\", "/")
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    owner_dir = posixpath.dirname(posixpath.dirname(rel_name))
    return posixpath.normpath(posixpath.join(owner_dir, target))


def _clean_rels(name: str, data: bytes, removed: set[str]) -> tuple[bytes, int, int]:
    """删除指向已移除部件的 Relationship；外部链接关系保留（打印告警）。"""
    if not _LXML_OK:
        return data, 0, 0
    try:
        root = etree.fromstring(data)
    except Exception:
        return data, 0, 0
    removed_lower = {item.lower() for item in removed}
    dropped = 0
    external = 0
    for rel in list(root):
        if _localname(rel.tag) != "Relationship":
            continue
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            external += 1
            continue
        try:
            resolved = _resolve_rel_target(name, target).lower()
        except Exception:
            continue
        if resolved in removed_lower:
            root.remove(rel)
            dropped += 1
    if not dropped:
        return data, 0, external
    try:
        out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    except Exception:
        return data, 0, external
    return out, dropped, external


def _clean_content_types(data: bytes, removed: set[str]) -> tuple[bytes, int]:
    """删除 [Content_Types].xml 里指向已移除部件的 Override。"""
    if not _LXML_OK:
        return data, 0
    try:
        root = etree.fromstring(data)
    except Exception:
        return data, 0
    removed_lower = {item.lower() for item in removed}
    dropped = 0
    for element in list(root):
        if _localname(element.tag) != "Override":
            continue
        part_name = (element.get("PartName") or "").lstrip("/").lower()
        if part_name in removed_lower:
            root.remove(element)
            dropped += 1
    if not dropped:
        return data, 0
    try:
        out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    except Exception:
        return data, 0
    return out, dropped


def _clean_app_xml(data: bytes) -> bytes:
    """清空 docProps/app.xml 里的公司/经理/模板/超链接基址，删除标题大纲缓存。"""
    if not _LXML_OK:
        return data
    try:
        root = etree.fromstring(data)
    except Exception:
        return data
    changed = False
    for element in list(root):
        tag = _localname(element.tag)
        if tag in _APP_META_TAGS:
            if (element.text or "").strip():
                element.text = ""
                changed = True
        elif tag in ("TitlesOfParts", "HeadingPairs"):
            root.remove(element)
            changed = True
    if not changed:
        return data
    try:
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    except Exception:
        return data


def _harden_zip(path: Path, warnings: list[str]) -> None:
    """删除 python-docx 无法管理的敏感部件，并清理 app.xml / rels / 内容类型。"""
    if not _LXML_OK:
        warnings.append("缺少 lxml，无法做 zip 级元数据清理（自定义属性/批注/缩略图可能残留）")
        return
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except Exception as exc:
        warnings.append(f"zip 级元数据清理失败（文件可能仍含批注/自定义属性）：{exc}")
        return

    removed: set[str] = set()
    for name in names:
        low = name.lower()
        if low in _REMOVE_PART_EXACT or any(p.match(low) for p in _REMOVE_PART_RE):
            removed.add(name)

    tmp_path = path.with_name(path.name + ".harden.tmp")
    try:
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(
            tmp_path, "w", zipfile.ZIP_DEFLATED
        ) as target:
            rels_dropped = 0
            external = 0
            overrides_dropped = 0
            for item in source.infolist():
                name = item.filename
                if name in removed:
                    continue
                data = source.read(name)
                if name.endswith(".rels"):
                    data, dropped, ext = _clean_rels(name, data, removed)
                    rels_dropped += dropped
                    external += ext
                elif name == "[Content_Types].xml":
                    data, dropped = _clean_content_types(data, removed)
                    overrides_dropped += dropped
                elif name == "docProps/app.xml":
                    data = _clean_app_xml(data)
                target.writestr(item, data)
        _replace_file(tmp_path, path)
    except Exception as exc:  # 清理失败不能留下半个文件
        warnings.append(f"zip 级元数据清理失败：{exc}")
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return

    if removed:
        warnings.append(f"已删除 {len(removed)} 个隐藏部件（批注/自定义属性/缩略图/CustomXML）")
    if external:
        warnings.append(f"文档含 {external} 个外部链接关系，可能暴露本机路径，已告警")
    if rels_dropped or overrides_dropped:
        warnings.append(
            f"已同步清理 {rels_dropped} 条失效关系、{overrides_dropped} 条内容类型声明"
        )


# ══════════════════════════════ 解析辅助 ══════════════════════════════

def _read_app_meta(path: Path) -> dict[str, str]:
    """从 docProps/app.xml 读取公司/经理/模板等属性（失败返回空）。"""
    meta: dict[str, str] = {}
    if not _LXML_OK:
        return meta
    try:
        with zipfile.ZipFile(path) as archive:
            if "docProps/app.xml" not in archive.namelist():
                return meta
            root = etree.fromstring(archive.read("docProps/app.xml"))
    except Exception:
        return meta
    for element in root:
        key = _APP_META_TAGS.get(_localname(element.tag))
        if not key:
            continue
        value = (element.text or "").strip()
        if value:
            meta[key] = value
    return meta


def _collect_core_meta(document: Any) -> dict[str, str]:
    """收集非空 core properties（导出时全部清空）。"""
    meta: dict[str, str] = {}
    try:
        properties = document.core_properties
    except Exception:
        return meta
    for field_name in _CORE_META_FIELDS:
        try:
            value = _fmt_meta_value(getattr(properties, field_name, ""))
        except Exception:
            continue
        if value.strip():
            meta[field_name] = value
    return meta


# ══════════════════════════════ 解析器 ══════════════════════════════

class DocxParser(Parser):
    """DOCX 流式文档解析器（flow 类格式，不提供位图渲染）。"""

    format_id = "docx"
    extensions = (".docx",)
    can_render = False

    # ── 判型 ──
    @classmethod
    def sniff(cls, path: Path) -> float:
        try:
            if Path(path).suffix.lower() in cls.extensions:
                return 1.0
            with open(path, "rb") as handle:
                head = handle.read(2)
            if head == b"PK":
                with zipfile.ZipFile(path) as archive:
                    if "word/document.xml" in archive.namelist():
                        return 0.9
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
        page = Page(index=0, width=A4_WIDTH, height=A4_HEIGHT)
        document.pages.append(page)

        if not _DOCX_OK:
            document.warnings.append(f"缺少 python-docx，DOCX 无法解析：{_DOCX_ERR}")
            document.pages.clear()
            return document

        try:
            parsed = docx.Document(str(path))
        except Exception as exc:  # 解析失败降级，不向用户抛异常
            document.pages.clear()
            document.warnings.append(f"DOCX 解析失败：{exc}")
            return document

        counters = {"blocks": 0, "hyperlinks": 0, "nested_tables": 0}
        blocks = page.blocks

        def add_block(
            kind: str = "text",
            text: str = "",
            ref: dict | None = None,
            attrs: dict | None = None,
        ) -> Block:
            block = Block(
                id=f"b{counters['blocks']}",
                kind=kind,  # type: ignore[arg-type]
                text=text,
                ref=dict(ref or {}),
                page=0,
                attrs=dict(attrs or {}),
            )
            counters["blocks"] += 1
            blocks.append(block)
            return block

        def scan_paragraph(paragraph: Any, ref: dict, attrs_extra: dict | None = None) -> str:
            text = _paragraph_text(paragraph)
            try:
                if len(paragraph._p.hyperlink_lst or []):
                    counters["hyperlinks"] += 1
            except Exception:
                pass
            if text.strip():
                attrs = {"style": _safe_style_name(paragraph)}
                if attrs_extra:
                    attrs.update(attrs_extra)
                add_block("text", text, ref, attrs)
            return text

        def scan_cell(cell: Any, table_index: int, row: int, col: int) -> str:
            text = _cell_text(cell)
            if getattr(cell, "tables", None):
                counters["nested_tables"] += len(cell.tables)
            attrs = {"table": table_index}
            add_block("text", text, {"t": table_index, "r": row, "c": col}, attrs)
            return text

        def scan_table(table: Any, table_index: int) -> None:
            try:
                rows = len(table.rows)
                cols = len(table.columns)
            except Exception:
                rows, cols = 0, 0
            table_block = add_block(
                "table", "", {"t": table_index}, {"rows": rows, "cols": cols}
            )
            # 合并单元格会在 row.cells 里重复出现：按底层 <w:tc> 去重。
            # 注意必须同时保存元素引用，否则 lxml 代理被回收后 id() 会被复用。
            seen: dict[int, Any] = {}
            cells: list[Cell] = []
            for row_index, row in enumerate(table.rows):
                for col_index, cell in enumerate(row.cells):
                    tc = cell._tc
                    key = id(tc)
                    if key in seen:
                        continue
                    seen[key] = tc
                    text = scan_cell(cell, table_index, row_index, col_index)
                    cells.append(Cell(text=text, row=row_index, col=col_index))
            table_block.cells = cells

        def scan_sdt(element: Any, body_index: int) -> None:
            try:
                content = element.find(qn("w:sdtContent"))
            except Exception:
                return
            if content is None:
                return
            paragraph_index = 0
            for child in content.iterchildren():
                tag = _localname(child.tag)
                if tag == "p":
                    scan_paragraph(
                        Paragraph(child, parsed),
                        {"sdt": [body_index, paragraph_index]},
                        {"part": "sdt"},
                    )
                    paragraph_index += 1
                elif tag in ("tbl", "sdt"):
                    counters["nested_tables"] += 1

        try:
            body = parsed.element.body
            paragraph_index = 0
            table_index = 0
            for body_index, child in enumerate(body.iterchildren()):
                tag = _localname(child.tag)
                if tag == "p":
                    scan_paragraph(Paragraph(child, parsed), {"p": paragraph_index})
                    paragraph_index += 1
                elif tag == "tbl":
                    scan_table(Table(child, parsed), table_index)
                    table_index += 1
                elif tag == "sdt":
                    scan_sdt(child, body_index)
            # 页眉页脚（含首页/奇偶页变体）
            for section_index, section in enumerate(parsed.sections):
                slots = [("header", "header"), ("footer", "footer")]
                try:
                    if section.different_first_page_header_footer:
                        slots += [
                            ("header_first", "first_page_header"),
                            ("footer_first", "first_page_footer"),
                        ]
                except Exception:
                    pass
                try:
                    if parsed.settings.odd_and_even_pages_header_footer:
                        slots += [
                            ("header_even", "even_page_header"),
                            ("footer_even", "even_page_footer"),
                        ]
                except Exception:
                    pass
                for part_name, attr_name in slots:
                    header_footer = getattr(section, attr_name, None)
                    if header_footer is None:
                        continue
                    try:
                        if header_footer.is_linked_to_previous:
                            continue  # 继承自上一节，已扫过
                    except Exception:
                        pass
                    try:
                        for paragraph_index, paragraph in enumerate(header_footer.paragraphs):
                            scan_paragraph(
                                paragraph,
                                {"part": part_name, "section": section_index,
                                 "p": paragraph_index},
                                {"part": part_name},
                            )
                        tables = getattr(header_footer, "tables", None)
                        if tables:
                            counters["nested_tables"] += len(tables)
                    except Exception as exc:
                        document.warnings.append(
                            f"页眉/页脚解析失败（第 {section_index} 节 {part_name}）：{exc}"
                        )
        except Exception as exc:  # 结构异常时保留已解析内容
            document.warnings.append(f"DOCX 结构遍历中断，已保留部分内容：{exc}")

        # 未覆盖子特性的告警（契约要求：不能静默丢弃）
        try:
            field_count = len(body.xpath(".//w:instrText"))
        except Exception:
            field_count = 0
        try:
            textbox_count = len(body.xpath(".//w:txbxContent"))
        except Exception:
            textbox_count = 0
        if counters["hyperlinks"]:
            document.warnings.append(
                f"文档含 {counters['hyperlinks']} 处超链接，超链接显示文字未纳入检测（python-docx runs 不含超链接 run）"
            )
        if field_count:
            document.warnings.append(f"文档含 {field_count} 处域代码（如 HYPERLINK/INCLUDETEXT），未纳入检测")
        if textbox_count:
            document.warnings.append(f"文档含 {textbox_count} 个文本框，文本框文字未纳入检测")
        if counters["nested_tables"]:
            document.warnings.append(
                f"文档含 {counters['nested_tables']} 个嵌套表格/内容控件表格，未展开检测"
            )

        # 隐藏部件与元数据
        try:
            part_names = [str(part.partname) for part in parsed.part.package.parts]
        except Exception:
            part_names = []
        media_parts = [n for n in part_names if "/word/media/" in n]
        embed_parts = [n for n in part_names if "/word/embeddings/" in n]
        if media_parts:
            document.warnings.append("文档含内嵌图片，图片内容未做 OCR/脱敏")
        if embed_parts:
            document.warnings.append("文档含内嵌对象（OLE/附件），其中内容未做脱敏")
        if any("/word/charts/" in name for name in part_names):
            document.warnings.append("文档含图表，图表标题/数据标签文字未纳入检测")
        if any("glossary" in name for name in part_names):
            document.warnings.append("文档含词汇表（构建基块），其中文字未纳入检测")
        if any("footnotes" in name or "endnotes" in name for name in part_names):
            document.warnings.append("文档含脚注/尾注，其中文字未纳入检测")

        meta = _collect_core_meta(parsed)
        meta.update(_read_app_meta(path))
        # P0 安全默认：内嵌图片/对象当前不会自动脱敏，导出时必须二次确认
        # （见 docs/red-lines.md 扩展点 E3）。re_parse 成品时这段同样生效，
        # Verify 据此判断"成品里还有未处理媒体"。
        if media_parts or embed_parts:
            meta["embedded_media"] = {
                "count": len(media_parts) + len(embed_parts),
                "parts": (media_parts + embed_parts)[:20],
            }
        document.meta = meta
        return document

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
        shutil.copyfile(source, out_path)

        if not _DOCX_OK:
            warnings.append(f"缺少 python-docx，导出的 DOCX 未做任何脱敏：{_DOCX_ERR}")
            return ExportResult(
                path=str(out_path), warnings=warnings, size=out_path.stat().st_size
            )

        try:
            parsed = docx.Document(str(out_path))
        except Exception as exc:
            warnings.append(f"DOCX 打开失败，已原样复制（未脱敏）：{exc}")
            return ExportResult(
                path=str(out_path), warnings=warnings, size=out_path.stat().st_size
            )

        block_by_id = {block.id: block for _page, block in doc.all_blocks()}
        tasks: dict[str, list[tuple[int, int, str]]] = {}
        unresolved = 0
        for entity in entities:
            if entity.status == "rejected" or not entity.value:
                continue
            decision = decisions.get(entity.id) or entity.decision
            if decision is None:
                decision = Decision(strategy="black", black=True)
            if decision.strategy == "keep":
                continue
            for span in entity.spans:
                if not span.is_text:
                    unresolved += 1
                    continue
                block = block_by_id.get(span.block_id or "")
                if block is None or block.kind != "text":
                    unresolved += 1
                    continue
                length = max(0, int(span.end) - int(span.start))
                replacement = _replacement_text(decision, length)
                if replacement is None:
                    continue
                tasks.setdefault(span.block_id, []).append(
                    (int(span.start), int(span.end), replacement)
                )

        # 值级传播：检测范围外若还有同值出现，也按同一决策覆盖（防漏）
        propagation = _propagation_rules(entities, decisions, warnings)
        if propagation:
            propagated = _propagate_in_blocks(block_by_id, tasks, propagation)
            if propagated:
                warnings.append(
                    f"检测范围外另有 {propagated} 处同值出现，已按同一决策一并覆盖"
                )

        for block_id, items in tasks.items():
            block = block_by_id[block_id]
            for start, end, replacement in sorted(items, key=lambda item: item[0], reverse=True):
                try:
                    applied = self._apply_span(parsed, block, start, end, replacement)
                except Exception:
                    applied = False
                if not applied:
                    unresolved += 1
        if unresolved:
            warnings.append(
                f"有 {unresolved} 处标记无法映射到 DOCX 的 run，请人工复核成品"
            )

        # 修订 / 批注 / 保护口令等 XML 级清理
        removed_revisions = 0
        try:
            for part in parsed.part.package.parts:
                element = getattr(part, "element", None)
                if element is None:
                    continue
                if _localname(element.tag) in ("document", "hdr", "ftr", "footnotes", "endnotes"):
                    removed_revisions += _strip_revisions(element)
            if removed_revisions:
                warnings.append(
                    f"已删除 {removed_revisions} 个修订/批注标记节点（含未接受的修订内容）"
                )
            removed_settings = _clean_settings(parsed)
            if removed_settings:
                warnings.append(
                    f"已删除 {removed_settings} 个设置项（文档保护口令/附加模板/邮件合并数据源等）"
                )
        except Exception as exc:
            warnings.append(f"清理修订/批注节点失败：{exc}")

        # core properties 清空
        cleared = 0
        try:
            properties = parsed.core_properties
            for field_name in _CORE_META_FIELDS:
                try:
                    if _fmt_meta_value(getattr(properties, field_name, "")).strip():
                        cleared += 1
                    setattr(properties, field_name, "")
                except Exception:
                    continue
            if cleared:
                warnings.append(f"已清空 {cleared} 项文档核心属性（作者/标题/关键词等）")
        except Exception as exc:
            warnings.append(f"清空 core properties 失败：{exc}")

        try:
            parsed.save(str(out_path))
        except Exception as exc:
            warnings.append(f"DOCX 保存失败，已回退为原样复制（未脱敏）：{exc}")
            shutil.copyfile(source, out_path)
            return ExportResult(
                path=str(out_path), warnings=warnings, size=out_path.stat().st_size
            )

        _harden_zip(out_path, warnings)
        return ExportResult(
            path=str(out_path), warnings=warnings, size=out_path.stat().st_size
        )

    # ── 导出：按 ref 定位并替换 ──
    @staticmethod
    def _apply_span(parsed: Any, block: Block, start: int, end: int, new: str) -> bool:
        ref = block.ref or {}
        if "t" in ref:
            table = parsed.tables[int(ref["t"])]
            cell = table.cell(int(ref["r"]), int(ref["c"]))
            return _replace_in_cell(cell, start, end, new)
        if "sdt" in ref:
            paragraph = _find_sdt_paragraph(parsed, ref["sdt"])
            return _replace_in_paragraph(paragraph, start, end, new) if paragraph else False
        if "part" in ref:
            paragraph = _find_header_footer_paragraph(parsed, ref)
            return _replace_in_paragraph(paragraph, start, end, new) if paragraph else False
        if "p" in ref:
            paragraphs = parsed.paragraphs
            index = int(ref["p"])
            if index < 0 or index >= len(paragraphs):
                return False
            return _replace_in_paragraph(paragraphs[index], start, end, new)
        return False


_SECTION_HF_ATTR = {
    "header": "header",
    "footer": "footer",
    "header_first": "first_page_header",
    "footer_first": "first_page_footer",
    "header_even": "even_page_header",
    "footer_even": "even_page_footer",
}


def _find_sdt_paragraph(parsed: Any, path: Any) -> Any | None:
    """按 ref={"sdt": [body_child_index, paragraph_index]} 找到内容控件里的段落。"""
    try:
        body_index, paragraph_index = int(path[0]), int(path[1])
        children = list(parsed.element.body.iterchildren())
        content = children[body_index].find(qn("w:sdtContent"))
        if content is None:
            return None
        paragraphs = content.findall(qn("w:p"))
        if paragraph_index < 0 or paragraph_index >= len(paragraphs):
            return None
        return Paragraph(paragraphs[paragraph_index], parsed)
    except Exception:
        return None


def _find_header_footer_paragraph(parsed: Any, ref: dict) -> Any | None:
    """按 ref={"part": ..., "section": i, "p": j} 找到页眉/页脚段落。"""
    try:
        section = parsed.sections[int(ref["section"])]
        header_footer = getattr(section, _SECTION_HF_ATTR[str(ref["part"])])
        paragraphs = header_footer.paragraphs
        index = int(ref["p"])
        if index < 0 or index >= len(paragraphs):
            return None
        return paragraphs[index]
    except Exception:
        return None


register_parser(DocxParser)
