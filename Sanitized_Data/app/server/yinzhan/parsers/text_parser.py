"""纯文本类解析器与破坏性导出器（.txt / .md / .csv / .json / .log）。

parse
    按行切分（兼容 LF / CRLF / 混合换行），一行一个 text Block，
    ref={"line": 行号, "eol": 原行尾}，不改变任何字符内容。

export
    按 ref 行号重排、替换后按原换行符拼回，统一写 UTF-8：
    black → 用 “█” 覆盖原长度；surrogate / mask → 写入 replacement；drop → 删除。
    另做一次「值级传播」：检测器漏掉的行里若仍出现同一实体值，也按同一决策覆盖。

设计取舍：
    - 解码顺序：UTF-8(BOM) → UTF-16(BOM) → UTF-8 → GB18030 → UTF-8 替换字符。
      编码不是 UTF-8 时写 warning，成品固定 UTF-8。
    - 空行也保留成空 Block，保证导出时行结构逐字节可还原。
"""
from __future__ import annotations

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


class TextParser(Parser):
    """纯文本解析器（flow 类格式，不提供位图渲染）。"""

    format_id = "text"
    extensions = (".txt", ".md", ".csv", ".json", ".log")
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
