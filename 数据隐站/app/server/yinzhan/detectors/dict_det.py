"""词表检测器。

规则包里的 ``detect.type == "dict"`` 由本检测器执行::

    detect:
      - {type: dict, words: [客户甲, 客户乙]}
      - {type: dict, file: sensitive_words.txt}    # 每行一个词，# 开头为注释

词表文件查找顺序：绝对路径 -> ``yinzhan/rules/`` -> 当前工作目录。
用于客户名单、项目代号、医院名、地名白名单这类"枚举得出"的敏感词。

实现取舍：词表不会很大（几十到几千），直接用 ``re.escape`` 拼 alternation，
由 C 层的正则引擎扫描；比纯 Python 的 AC 自动机在这个规模下更快也更好懂。
拉丁词加词边界，避免 "IBM" 命中 "IBMer"；中文词不加边界（中文没有空格边界）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from ..registry import Detector, iter_text_blocks, register_detector
from ..udm import DetectContext, Document, Entity, Rule, Span

# 词表文件缓存：路径 -> (mtime_ns, size, words)
_FILE_CACHE: dict[str, tuple[int, int, tuple[str, ...]]] = {}

# yinzhan/rules/ 目录（词表文件默认放这里）
_RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


def _candidate_paths(name: str) -> list[Path]:
    """词表文件的候选路径。"""
    raw = Path(name)
    out: list[Path] = []
    if raw.is_absolute():
        out.append(raw)
    else:
        out.append(_RULES_DIR / raw)
        out.append(Path.cwd() / raw)
    return out


def _read_word_file(name: str) -> tuple[str, ...]:
    """读一行一个词的文件；带 mtime 缓存。"""
    path: Path | None = None
    for candidate in _candidate_paths(name):
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        raise FileNotFoundError(f"词表文件不存在: {name}")
    try:
        stat = path.stat()
    except OSError as exc:
        raise FileNotFoundError(f"词表文件不可读: {name}") from exc
    key = str(path.resolve())
    cached = _FILE_CACHE.get(key)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]

    try:
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="gbk", errors="ignore")

    words: list[str] = []
    for line in raw.splitlines():
        word = line.strip()
        if not word or word.startswith("#") or word.startswith("//"):
            continue
        words.append(word)
    result = tuple(dict.fromkeys(words))
    _FILE_CACHE[key] = (stat.st_mtime_ns, stat.st_size, result)
    return result


def _boundary_piece(word: str) -> str:
    """给单个词拼出带边界的正则片段（只对拉丁/数字边界加断言）。"""
    escaped = re.escape(word)
    piece = escaped
    if word and word[0].isascii() and word[0].isalnum():
        piece = r"(?<![A-Za-z0-9])" + piece
    if word and word[-1].isascii() and word[-1].isalnum():
        piece = piece + r"(?![A-Za-z0-9])"
    return piece


class DictDetector(Detector):
    """多模式词表匹配检测器。"""

    id = "dict"

    def __init__(self, words: Iterable[str], confidence: float = 0.95) -> None:
        cleaned: list[str] = []
        for word in words:
            text = str(word).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        if not cleaned:
            raise ValueError("DictDetector 需要非空词表")
        # 长词优先，保证 "上海市第一人民医院" 不会被 "医院" 抢先切碎。
        cleaned.sort(key=lambda w: (-len(w), w))
        self.words: tuple[str, ...] = tuple(cleaned)
        self.confidence = float(confidence)
        self.regex = re.compile("|".join(_boundary_piece(w) for w in cleaned),
                                re.IGNORECASE)

    @classmethod
    def from_spec(cls, spec: dict) -> "DictDetector":
        """从 yaml 的 detect 项创建检测器（``words`` 与 ``file`` 可同时给）。"""
        if not isinstance(spec, dict):
            raise ValueError(f"DictDetector.from_spec 需要 dict，得到 {type(spec).__name__}")
        words: list[str] = []
        raw_words = spec.get("words")
        if isinstance(raw_words, str):
            words.append(raw_words)
        elif isinstance(raw_words, (list, tuple, set)):
            words.extend(str(w) for w in raw_words)
        file_name = spec.get("file")
        if file_name:
            words.extend(_read_word_file(str(file_name)))
        # ``word`` 单数写法也接受，规则作者少踩坑。
        single = spec.get("word")
        if single:
            words.append(str(single))
        if not words:
            raise ValueError("dict detect 需要 words 或 file")
        return cls(words, confidence=float(spec.get("confidence", 0.95)))

    def detect(self, doc: Document, rule: Rule,
               ctx: DetectContext) -> list[Entity]:
        """扫描文本块，同一词多处命中合并为一个 Entity。"""
        merged: dict[str, Entity] = {}
        for _page, block in iter_text_blocks(doc):
            text = block.text
            if not text:
                continue
            for match in self.regex.finditer(text):
                value = match.group(0)
                if not value:
                    continue
                entity = merged.get(value)
                if entity is None:
                    entity = Entity(
                        id=Entity.new_id(),
                        etype=rule.entity,
                        label=rule.label,
                        value=value,
                        spans=[],
                        level=rule.level,
                        confidence=self.confidence,
                        origin="dict",
                        status="accepted",
                    )
                    merged[value] = entity
                entity.spans.append(Span(block_id=block.id,
                                         start=match.start(), end=match.end()))
        return list(merged.values())


register_detector(DictDetector)

__all__ = ["DictDetector"]
