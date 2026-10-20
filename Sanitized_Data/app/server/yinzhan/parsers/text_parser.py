"""纯文本类解析器与破坏性导出器（.txt / .md / .csv / .tsv / .json / .log）。

parse
    - 普通文本（.txt/.md/.json/.log）：按行切分（兼容 LF / CRLF / 混合换行），
      一行一个 text Block，ref={"line": 行号, "eol": 原行尾}，不改变任何字符内容。
    - 分隔符表格（.csv / .tsv）：用 csv 模块解析（保留原分隔符/引号/换行），
      每个**非空单元格**一个 text Block，ref={"row": 行, "col": 列, "sep": 分隔符,
      "line": 行}；page.ref 保留 dialect 与原始行文本（raw_rows），doc.meta 记录
      delimited/delimiter/quotechar/rows/cols。document.fmt 仍为 "text"（兼容既有
      text-scope 规则）；table_column 检测器按 row/col ref 做列语义识别。

export
    - 普通文本：按 ref 行号重排、替换后按原换行符拼回，统一写 UTF-8。
    - CSV/TSV：按原分隔符/引号用 csv.writer 重写被修改的行（未修改且有原始行时
      逐行写回原文），保持行数/列数/引号语义；black → “█” 覆盖原长度；
      surrogate / mask → 写入 replacement；drop → 空单元格。
    - **CSV/TSV 编码保留（P1.5）**：按解析阶段记录的编码写回——`utf-8-sig` 保留
      BOM、`utf-16` 保留 BOM、`gb18030`（GBK 超集）按原编码写回；未知编码或目标
      编码无法表示的字符 → 写 warning 并回退 UTF-8（不静默改编码）。
    - 两者都做「值级传播」：检测器漏掉的单元格/行里若仍出现同一实体值，也按同一
      决策覆盖。

设计取舍：
    - 解码顺序：UTF-8(BOM) → UTF-16(BOM) → UTF-8 → GB18030 → UTF-8 替换字符。
      普通文本(.txt/.md)导出仍统一 UTF-8；CSV/TSV 按原编码/BOM 写回。
    - 空行也保留成空 Block，保证普通文本导出时行结构逐字节可还原。
    - CSV/TSV 的 header 语义不在这里判断：交给 detectors/table_det.py 的
      table_column（无表头时自然退化为逐单元格的既有规则）。
"""
from __future__ import annotations

import csv
import io
import uuid
from pathlib import Path
from typing import Any

from ..registry import Parser, register_parser
from ..udm import (
    Block, Decision, Document, Entity, ExportError, ExportResult, Page, ParseContext,
)

A4_WIDTH = 595.0
A4_HEIGHT = 842.0
BLACK_CHAR = "█"
# 这些扩展名都按纯文本处理
TEXT_ENCODINGS = ("utf-8", "gb18030")
# 分隔符表格：扩展名 -> 格式 id（document.fmt 保持 "text"，语义放 meta/ref）
DELIMITED_SUFFIXES = {".csv": "csv", ".tsv": "tsv"}


def _decode_text(data: bytes) -> tuple[str, str, list[str]]:
    """把字节解成文本，返回 (text, 编码名, warnings)。"""
    warnings: list[str] = []
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "utf-8-sig", warnings
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16", warnings
        except Exception:
            warnings.append("UTF-16 解码失败，改用 UTF-8 替换解码")
            return data.decode("utf-8", "replace"), "utf-8(replace)", warnings
    for encoding in TEXT_ENCODINGS:
        try:
            return data.decode(encoding), encoding, warnings
        except UnicodeDecodeError:
            continue
    text = data.decode("utf-8", "replace")
    bad = text.count("\ufffd")
    warnings.append(f"文本既非 UTF-8 也非 GB18030，已用替换字符解码（{bad} 处乱码，原文可能受损）")
    return text, "utf-8(replace)", warnings


def _split_lines(text: str) -> list[tuple[str, str]]:
    """按 \\n 切行并记录每行的原始行尾（'', '\\n', '\\r\\n'）。"""
    lines: list[tuple[str, str]] = []
    parts = text.split("\n")
    last = len(parts) - 1
    for index, part in enumerate(parts):
        if index < last:
            if part.endswith("\r"):
                lines.append((part[:-1], "\r\n"))
            else:
                lines.append((part, "\n"))
        else:
            lines.append((part, ""))
    return lines


def _sniff_delimiter(text: str, kind: str) -> tuple[str, str]:
    """识别 CSV/TSV 的分隔符与引号字符。

    - ``.tsv`` 固定制表符；
    - ``.csv`` 先用 ``csv.Sniffer``（候选 , ; \\t |），失败则按首行出现次数兜底。
    引号字符默认 ``"``，Sniffer 识别到其它引号时保留。
    """
    if kind == "tsv":
        return "\t", '"'
    sample = text[:16384]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter or ","
        quotechar = dialect.quotechar or '"'
        return delimiter, quotechar
    except Exception:
        first_line = sample.split("\n", 1)[0] if sample else ""
        delimiter = max(",;\t|", key=lambda ch: first_line.count(ch))
        if first_line.count(delimiter) == 0:
            delimiter = ","
        return delimiter, '"'


def _read_delimited_rows(text: str, delimiter: str, quotechar: str) -> list[list[str]]:
    """用 csv 模块解析；保留引号内的换行/分隔符（单元格可跨行）。"""
    reader = csv.reader(io.StringIO(text), delimiter=delimiter,
                        quotechar=quotechar or '"')
    return [list(row) for row in reader]


def _csv_line(values: list[str], delimiter: str, quotechar: str) -> str:
    """把一行单元格写成 CSV/TSV 文本（按需加引号，不带行尾）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter,
                        quotechar=quotechar or '"', lineterminator="")
    writer.writerow(values)
    return buffer.getvalue()


def _encoding_target(encoding: str) -> tuple[str, bool, str]:
    """把解析阶段记录的编码名映射为导出目标。

    返回 ``(输出编码, 是否带 BOM, 来源)``；来源 ``bom/content/fallback`` 用于
    判断是否需要 warning（fallback 表示原编码不可靠，需提示用户）。
    """
    text = str(encoding or "").strip().lower()
    if text == "utf-8-sig":
        return "utf-8-sig", True, "bom"
    if text in ("utf-16", "utf-16-le", "utf-16-be"):
        return "utf-16", True, "bom"
    if text in ("gb18030", "gbk", "gb2312"):
        return "gb18030", False, "content"
    if text == "utf-8":
        return "utf-8", False, "content"
    return "utf-8", False, "fallback"


def _encode_output_preserving(
    text: str, encoding: str, warnings: list[str]
) -> bytes:
    """按原编码写回字节；无法表示的字符/未知编码 → warning + 回退 UTF-8。

    - ``utf-8-sig``/``utf-16``：encode 时自带 BOM；
    - ``gb18030``（GBK 超集）：GBK 可表示的字符字节一致；
    - 未知编码（如解码阶段已用替换字符）→ 不静默改编码，明确 warning 后回退 UTF-8。
    """
    target, _bom, source = _encoding_target(encoding)
    if source == "fallback":
        warnings.append(
            f"原文件编码无法确认（记录为 {encoding or '未知'}），成品按 UTF-8 写回，"
            "未保留原编码（请人工确认）"
        )
        return text.encode("utf-8")
    try:
        return text.encode(target)
    except (UnicodeEncodeError, LookupError) as exc:
        warnings.append(
            f"成品含 {target} 无法表示的字符（{type(exc).__name__}: {exc}），"
            f"已回退 UTF-8 写回，原编码 {encoding} 未保留"
        )
        return text.encode("utf-8")


def _fill_delimited_document(
    document: "Document", text: str, kind: str, encoding: str, warnings: list[str]
) -> bool:
    """把 CSV/TSV 文本填进 Document（单元格 Block + page.ref dialect + doc.meta）。

    返回 False 表示解析失败，调用方应退回"按行解析"的普通文本路径。
    """
    delimiter, quotechar = _sniff_delimiter(text, kind)
    try:
        rows = _read_delimited_rows(text, delimiter, quotechar)
    except csv.Error as exc:
        warnings.append(f"{kind.upper()} 解析失败（{exc}），已退回按行解析")
        return False

    eol = "\r\n" if "\r\n" in text else "\n"
    final_newline = text.endswith("\n")
    raw_pairs = _split_lines(text)
    if final_newline and raw_pairs and raw_pairs[-1][0] == "":
        raw_pairs = raw_pairs[:-1]
    # 只有"每条记录占一行"时才能逐行写回原文（引号内换行会破坏行对应）
    raw_ok = (len(raw_pairs) == len(rows)
              and all("\n" not in line for line, _eol in raw_pairs))

    page = Page(index=0, width=A4_WIDTH, height=A4_HEIGHT)
    max_cols = 0
    for row_index, row in enumerate(rows):
        max_cols = max(max_cols, len(row))
        for col_index, cell_value in enumerate(row):
            cell_text = "" if cell_value is None else str(cell_value)
            if not cell_text:
                continue
            page.blocks.append(
                Block(
                    id=f"b{row_index}_{col_index}",
                    kind="text",
                    text=cell_text,
                    ref={
                        "row": row_index,
                        "col": col_index,
                        "sep": delimiter,
                        "line": row_index,
                    },
                    page=0,
                    attrs={"row": row_index, "col": col_index, "sep": delimiter},
                )
            )
    page.ref = {
        "delimited": kind,
        "delimiter": delimiter,
        "quotechar": quotechar,
        "eol": eol,
        "final_newline": final_newline,
        "raw_rows": [line for line, _eol in raw_pairs] if raw_ok else [],
        "row_cols": [len(row) for row in rows],
        "rows": len(rows),
    }
    document.pages.append(page)
    output_encoding, bom, encoding_source = _encoding_target(encoding)
    document.meta = {
        "encoding": encoding,                 # 解析阶段识别到的原始编码
        "encoding_output": output_encoding,   # 导出目标（无法还原时见 encoding_source）
        "encoding_source": encoding_source,   # bom / content / fallback
        "bom": bom,
        "newline": "CRLF" if eol == "\r\n" else "LF",
        "line_count": len(raw_pairs),
        "delimited": kind,
        "delimiter": delimiter,
        "quotechar": quotechar,
        "rows": len(rows),
        "cols": max_cols,
        "final_newline": final_newline,
        "raw_rows_ok": raw_ok,
    }
    if not rows:
        warnings.append(f"{kind.upper()} 没有可解析的数据行")
    ragged = sum(1 for row in rows if len(row) != max_cols)
    if ragged:
        warnings.append(
            f"{kind.upper()} 有 {ragged} 行列数与最大列数（{max_cols}）不一致，"
            "导出时按各行列数原样保留"
        )
    return True


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
    """值级传播规则：同一实体值在其它行/其它位置出现时也按同一决策覆盖。

    检测器可能漏行，而出口自检按「成品里是否还有该值」判定残留；这里做防御
    性传播。单字符值不传播，只记 warning。
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


def _export_delimited(
    document: "Document",
    entities: list[Entity],
    decisions: dict[str, Decision],
    out_path: Path,
    warnings: list[str],
) -> ExportResult:
    """CSV/TSV 破坏性写回：逐格替换、按原 dialect 重写，保持行数/列数。"""
    page = document.pages[0] if document.pages else None
    ref = dict(getattr(page, "ref", None) or {})
    rows_meta = list(ref.get("row_cols") or [])
    nrows = int(ref.get("rows") or len(rows_meta))
    delimiter = str(ref.get("delimiter") or ",")
    quotechar = str(ref.get("quotechar") or '"')[:1] or '"'
    eol = str(ref.get("eol") or "\n")
    final_newline = bool(ref.get("final_newline", True))
    raw_rows = list(ref.get("raw_rows") or [])

    blocks = [block for page_ in document.pages for block in page_.blocks]
    block_by_id = {block.id: block for block in blocks}
    grid: dict[tuple[int, int], str] = {}
    for block in blocks:
        block_ref = block.ref or {}
        row = block_ref.get("row")
        col = block_ref.get("col")
        if isinstance(row, int) and isinstance(col, int):
            grid[(row, col)] = block.text

    def cell_text(row: int, col: int) -> str:
        return grid.get((row, col), "")

    unresolved = 0
    edits: dict[tuple[int, int], list[tuple[int, int, str]]] = {}
    edited_rows: set[int] = set()
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
            block_ref = (block.ref if block else None) or {}
            row = block_ref.get("row")
            col = block_ref.get("col")
            if not isinstance(row, int) or not isinstance(col, int):
                unresolved += 1
                continue
            text = cell_text(row, col)
            start = max(0, min(int(span.start), len(text)))
            end = max(start, min(int(span.end), len(text)))
            if end <= start:
                unresolved += 1
                continue
            replacement = _replacement_text(decision, end - start)
            if replacement is None:
                continue
            edits.setdefault((row, col), []).append((start, end, replacement))
            edited_rows.add(row)

    # 值级传播：检测范围外（其它单元格）若还有同值出现，也按同一决策覆盖
    propagation = _propagation_rules(entities, decisions, warnings)
    if propagation:
        ordered = sorted(propagation.items(), key=lambda item: len(item[0]),
                         reverse=True)
        propagated = 0
        for (row, col), text in list(grid.items()):
            cell_edits = edits.setdefault((row, col), [])
            for value, replacement in ordered:
                position = 0
                while True:
                    hit = text.find(value, position)
                    if hit < 0:
                        break
                    end = hit + len(value)
                    if not any(not (end <= old_start or hit >= old_end)
                               for old_start, old_end, _old in cell_edits):
                        cell_edits.append((hit, end, replacement))
                        edited_rows.add(row)
                        propagated += 1
                    position = end
            if not cell_edits:
                edits.pop((row, col), None)
        if propagated:
            warnings.append(
                f"检测范围外另有 {propagated} 处同值出现，已按同一决策一并覆盖"
            )

    for (row, col), cell_edits in edits.items():
        current = cell_text(row, col)
        for start, end, replacement in sorted(cell_edits, key=lambda item: item[0],
                                              reverse=True):
            start = max(0, min(start, len(current)))
            end = max(start, min(end, len(current)))
            current = current[:start] + replacement + current[end:]
        grid[(row, col)] = current

    lines: list[str] = []
    for row in range(nrows):
        cols_count = rows_meta[row] if row < len(rows_meta) else 0
        if row not in edited_rows and row < len(raw_rows):
            lines.append(raw_rows[row])          # 未修改行按原文写回（保引号/空格）
            continue
        values = [cell_text(row, col) for col in range(cols_count)]
        lines.append(_csv_line(values, delimiter, quotechar))
    output = eol.join(lines)
    if nrows and final_newline:
        output += eol

    if unresolved:
        warnings.append(f"有 {unresolved} 处标记无法映射到 CSV/TSV 单元格，请人工复核成品")

    # P1.5：按原编码/BOM 写回（未知编码或不可编码字符 → warning + 回退 UTF-8）
    meta = document.meta or {}
    data = _encode_output_preserving(
        output, str(meta.get("encoding") or "utf-8"), warnings
    )
    try:
        with open(out_path, "wb") as handle:
            handle.write(data)
    except Exception as exc:
        out_path.unlink(missing_ok=True)
        raise ExportError(f"CSV/TSV 写出失败，未产出成品：{exc}") from exc

    return ExportResult(
        path=str(out_path), warnings=warnings, size=out_path.stat().st_size
    )


class TextParser(Parser):
    """纯文本解析器（flow 类格式，不提供位图渲染）。"""

    format_id = "text"
    extensions = (".txt", ".md", ".csv", ".tsv", ".json", ".log")
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
        try:
            data = path.read_bytes()
        except Exception as exc:
            document.warnings.append(f"文本读取失败：{exc}")
            return document

        try:
            text, encoding, warnings = _decode_text(data)
            document.warnings.extend(warnings)
        except Exception as exc:  # 极端情况：解码器崩溃也不抛给用户
            document.warnings.append(f"文本解码失败：{exc}")
            return document

        suffix = path.suffix.lower()
        if suffix in DELIMITED_SUFFIXES:
            kind = DELIMITED_SUFFIXES[suffix]
            if _fill_delimited_document(document, text, kind, encoding,
                                        document.warnings):
                return document
            # 解析失败：退回下面的"按行解析"普通文本路径

        lines = _split_lines(text)
        page = Page(index=0, width=A4_WIDTH, height=A4_HEIGHT)
        for index, (content, eol) in enumerate(lines):
            page.blocks.append(
                Block(
                    id=f"b{index}",
                    kind="text",
                    text=content,
                    ref={"line": index, "eol": eol},
                    page=0,
                    attrs={"eol": eol},
                )
            )
        document.pages.append(page)
        document.meta = {"encoding": encoding, "line_count": len(lines)}
        if encoding not in ("utf-8", "utf-8-sig"):
            document.warnings.append(f"输入编码为 {encoding}，成品统一按 UTF-8 输出")
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
        out_path = Path(out_path)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        if (doc.meta or {}).get("delimited"):
            return _export_delimited(doc, entities, decisions, out_path, warnings)

        page_blocks = [block for page in doc.pages for block in page.blocks]
        block_by_id = {block.id: block for block in page_blocks}

        # 按 ref["line"] 还原行数组（缺行用空串补齐，保证结构不丢）
        line_text: dict[int, str] = {}
        line_eol: dict[int, str] = {}
        for block in page_blocks:
            ref: dict[str, Any] = block.ref or {}
            index = ref.get("line")
            if not isinstance(index, int) or index < 0:
                continue
            line_text[index] = block.text
            eol = ref.get("eol")
            line_eol[index] = eol if isinstance(eol, str) else "\n"
        if not line_text:
            # 退化路径：没有行号时按 block 顺序拼回
            for index, block in enumerate(page_blocks):
                line_text[index] = block.text
                line_eol[index] = "\n"
            if page_blocks:
                line_eol[len(page_blocks) - 1] = ""

        unresolved = 0
        edits_by_line: dict[int, list[tuple[int, int, str]]] = {}
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
                ref = (block.ref if block else None) or {}
                index = ref.get("line")
                if not isinstance(index, int) or index < 0 or index not in line_text:
                    unresolved += 1
                    continue
                length = max(0, int(span.end) - int(span.start))
                replacement = _replacement_text(decision, length)
                if replacement is None:
                    continue
                edits_by_line.setdefault(index, []).append(
                    (int(span.start), int(span.end), replacement)
                )

        # 值级传播：检测范围外若还有同值出现，也按同一决策覆盖（防漏）
        propagation = _propagation_rules(entities, decisions, warnings)
        if propagation:
            ordered = sorted(
                propagation.items(), key=lambda item: len(item[0]), reverse=True
            )
            propagated = 0
            for index, text in line_text.items():
                edits = edits_by_line.setdefault(index, [])
                for value, replacement in ordered:
                    start = 0
                    while True:
                        position = text.find(value, start)
                        if position < 0:
                            break
                        end = position + len(value)
                        if not any(
                            not (end <= old_start or position >= old_end)
                            for old_start, old_end, _old in edits
                        ):
                            edits.append((position, end, replacement))
                            propagated += 1
                        start = end
                if not edits:
                    edits_by_line.pop(index, None)
            if propagated:
                warnings.append(
                    f"检测范围外另有 {propagated} 处同值出现，已按同一决策一并覆盖"
                )

        for index, edits in edits_by_line.items():
            current = line_text[index]
            for start, end, replacement in sorted(
                edits, key=lambda item: item[0], reverse=True
            ):
                start = max(0, min(start, len(current)))
                end = max(start, min(end, len(current)))
                current = current[:start] + replacement + current[end:]
            line_text[index] = current

        if unresolved:
            warnings.append(f"有 {unresolved} 处标记无法映射到文本行，请人工复核成品")

        last_index = max(line_text) if line_text else -1
        chunks: list[str] = []
        for index in range(last_index + 1):
            content = line_text.get(index, "")
            if index in line_eol:
                eol = line_eol[index]
            else:
                eol = "\n" if index < last_index else ""
            chunks.append(content + eol)
        output = "".join(chunks)

        try:
            with open(out_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(output)
        except Exception as exc:
            out_path.unlink(missing_ok=True)
            raise ExportError(f"文本写出失败，未产出成品：{exc}") from exc

        meta_encoding = (doc.meta or {}).get("encoding")
        if meta_encoding and meta_encoding not in ("utf-8", "utf-8-sig"):
            warnings.append(f"输入编码 {meta_encoding} 已统一转为 UTF-8")
        return ExportResult(
            path=str(out_path), warnings=warnings, size=out_path.stat().st_size
        )


register_parser(TextParser)
