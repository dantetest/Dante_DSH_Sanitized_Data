# -*- coding: utf-8 -*-
"""CSV/TSV 列语义 + 多级/合并表头继承（P1-B）单测 —— task-32。

运行（仓库任意目录，必须 -X utf8）：

    python -X utf8 app/tests/test_csv_table.py

也可用 pytest 收集（函数名 ``test_*``，内部惰性初始化引擎）。
只写系统临时目录（``YINZHAN_DATA_DIR`` / ``YINZHAN_OUTPUT_DIR`` 指向 temp），不污染仓库 data/。

覆盖（对应 task-32 验收）：
1. text_parser：`.tsv` 支持；CSV/TSV 单元格 Block + ref={row,col,sep}；doc.meta/page.ref
   保留 dialect 与原始行；普通 .txt 仍按行切块（行为不变）；
2. 变体：UTF-8 BOM / GBK / 分号分隔 / CRLF / 引号内换行 / 空文件 / 列数不齐；
3. table_column 在 CSV/TSV 上的整列覆盖（虚构客户名单 14/14；含缺位身份证、空格手机号、
   无后缀单位、文本金额）；
4. 两级表头（个人信息→姓名/身份证/手机…）、合并表头（空槽向左继承）、纵向继承（父行+叶行）；
5. 无表头 CSV/TSV 不误报（table-cn 0 命中），legacy 规则仍逐格生效（退化正常）；
6. 导出：按原分隔符/引号重写，行数/列数不变、未修改行原文写回、residual=0（AI/涂黑/TSV）；
7. 规则/注册：table-cn 的 11 条 scope 含 text（Lead 已接）、text 格式可编译、pdf 不含。
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

# 必须在 import yinzhan.config 之前把数据/输出目录指向临时目录（不污染仓库）
_TMP = Path(tempfile.mkdtemp(prefix="yz_csv_table_test_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(_TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "csv_table"
sys.path.insert(0, str(SERVER))

from yinzhan import pipeline, registry, rules as rules_mod       # noqa: E402
from yinzhan.detectors.table_det import TableColumnDetector      # noqa: E402
from yinzhan.ocr import get_ocr                                   # noqa: E402
from yinzhan.parsers.text_parser import TextParser                # noqa: E402
from yinzhan.udm import DetectContext, ParseContext               # noqa: E402

TABLE_RULE_IDS = {
    "table.person", "table.contact", "table.id_card", "table.phone",
    "table.address", "table.email", "table.org", "table.bank_card",
    "table.account", "table.amount", "table.txn_no",
}

_ENGINE: tuple | None = None
_PASS = {"ok": 0, "fail": 0}
_WORK = _TMP / "work"


# ────────────────────────────── 断言工具 ──────────────────────────────


def check(name: str, cond: bool, detail: str = "") -> bool:
    if cond:
        _PASS["ok"] += 1
        print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))
        return True
    _PASS["fail"] += 1
    print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))
    raise AssertionError(name + (f": {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ────────────────────────────── 引擎/IO 辅助 ──────────────────────────────


def _engine():
    global _ENGINE
    if _ENGINE is None:
        errors = registry.load_plugins()
        if errors:
            raise RuntimeError(f"插件加载失败: {errors}")
        packs = rules_mod.load_packs()
        ctx = ParseContext(ocr=get_ocr(), workdir=str(_WORK))
        _ENGINE = (packs, ctx, rules_mod.load_errors())
    return _ENGINE


def _packs() -> dict:
    return _engine()[0]


def _ctx() -> ParseContext:
    return _engine()[1]


def _parse(path: Path):
    return registry.find_parser(path).parse(path, _ctx())


def _table_rules(only_own: bool = True) -> list:
    return [rule for rule in _packs().get("table-cn", [])
            if rule.pack == "table-cn" and (not only_own or rule.id in TABLE_RULE_IDS)]


def _rule_by_id(rule_id: str):
    for rule in _table_rules():
        if rule.id == rule_id:
            return rule
    raise KeyError(rule_id)


def _detect_direct(doc, rule) -> list:
    detector = TableColumnDetector.from_spec(rule.detect[0])
    return detector.detect(doc, rule, DetectContext())


def _direct_map(doc) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for rule in _table_rules():
        values = sorted({entity.value for entity in _detect_direct(doc, rule)})
        if values:
            result[rule.id] = values
    return result


def _full_detect(path: Path):
    doc = _parse(path)
    compiled = [rule for rule in rules_mod.compile_rules(_packs(), doc.fmt)
                if rule.enabled]
    det = pipeline.detect(doc, compiled, "standard")
    return doc, det


def _table_entities(det) -> list[tuple[str, str, str]]:
    out = []
    for entity in det.entities:
        rule = det.rules.get(entity.id)
        if rule is not None and rule.pack == "table-cn":
            out.append((rule.id, entity.etype, entity.value))
    return out


def _export_and_verify(path: Path, mode: str, name: str) -> tuple[Path, dict]:
    doc, det = _full_detect(path)
    pipeline.decide(det.entities, det.rules, pipeline.Session())
    if mode == "human":
        from yinzhan.udm import Decision
        for entity in det.entities:
            entity.decision = Decision(strategy="black", black=True, replacement=None)
    parser = registry.find_parser(path)
    out = _WORK / "out" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    pipeline.export(doc, parser, det.entities, out, {"mode": mode})
    info = pipeline.verify(out, det.entities, parser, _ctx())
    return out, info


def _write_text(name: str, content: str, encoding: str = "utf-8",
                newline: str = "\n", bom: bool = False) -> Path:
    path = _WORK / name
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.replace("\n", newline).encode(encoding)
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)
    return path


def _read_csv_rows(path: Path, delimiter: str = ",") -> list[list[str]]:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return [list(row) for row in csv.reader(handle, delimiter=delimiter)]


def _expected() -> dict:
    return json.loads((FIXTURES / "expected.json").read_text("utf-8"))


def _as_sets(mapping: dict) -> dict[str, set]:
    return {rule_id: set(values) for rule_id, values in mapping.items()}


# ────────────────────────────── 1. 解析器 ──────────────────────────────


def test_parser_delimited():
    section("1. CSV/TSV 解析：单元格 Block + dialect/原行；.txt 行为不变")
    check(".tsv 已加入 TextParser.extensions", ".tsv" in TextParser.extensions,
          str(TextParser.extensions))
    check(".tsv 由 TextParser 解析",
          registry.find_parser(_WORK / "x.tsv").format_id == "text")

    csv_path = FIXTURES / "client_list.csv"
    doc = _parse(csv_path)
    meta = doc.meta or {}
    check("CSV document.fmt 仍为 text（兼容既有 text 规则）", doc.fmt == "text", doc.fmt)
    check("meta.delimited=csv", meta.get("delimited") == "csv", str(meta))
    check("meta.delimiter=','", meta.get("delimiter") == ",", str(meta.get("delimiter")))
    check("meta.rows/cols", meta.get("rows") == 3 and meta.get("cols") == 7, str(meta))
    check("meta.raw_rows_ok", meta.get("raw_rows_ok") is True, str(meta))
    blocks = doc.pages[0].blocks
    check("3 行 × 7 列非空单元格 = 21 Block", len(blocks) == 21, str(len(blocks)))
    check("单元格 ref 带 row/col/sep",
          all({"row", "col", "sep"} <= set(block.ref or {}) for block in blocks),
          str([block.ref for block in blocks[:2]]))
    check("Block id 按行列唯一", len({block.id for block in blocks}) == 21)
    quoted = [block.text for block in blocks if block.text == "12,000.00"]
    check("引号内逗号被保留为单元格内容", bool(quoted), str(quoted))
    page_ref = doc.pages[0].ref or {}
    check("page.ref 保留原行文本", len(page_ref.get("raw_rows") or []) == 3,
          str(len(page_ref.get("raw_rows") or [])))
    check("原行首行=表头原文",
          (page_ref.get("raw_rows") or [""])[0].startswith("姓名,身份证号"),
          str((page_ref.get("raw_rows") or [""])[0])[:30])

    tsv_doc = _parse(FIXTURES / "client_list.tsv")
    tsv_meta = tsv_doc.meta or {}
    check("TSV delimited=tsv / delimiter 制表符",
          tsv_meta.get("delimited") == "tsv" and tsv_meta.get("delimiter") == "\t",
          str(tsv_meta))
    check("TSV 单元格 Block 数一致", len(tsv_doc.pages[0].blocks) == 21,
          str(len(tsv_doc.pages[0].blocks)))

    # 普通 txt 仍按行切块，不产生 row/col ref（table_column 不会触发）
    txt = _write_text("plain.txt", "姓名,身份证号\n张伟,310104198703124510\n")
    txt_doc = _parse(txt)
    check("txt 无 delimited 标记", not (txt_doc.meta or {}).get("delimited"))
    check("txt 按行切块（3 Block）", len(txt_doc.pages[0].blocks) == 3,
          str(len(txt_doc.pages[0].blocks)))
    check("txt Block 无 row/col ref",
          all("row" not in (block.ref or {}) for block in txt_doc.pages[0].blocks))
    check("txt 上 table_column 不产生实体", not _direct_map(txt_doc), str(_direct_map(txt_doc)))


# ────────────────────────────── 2. 编码/分隔符/换行变体 ──────────────────────────────


def test_parser_variants():
    section("2. 编码/分隔符/换行/引号内换行/空文件/列数不齐")
    content = "姓名,手机号\n张伟,13817264451\n"

    bom_path = _write_text("bom.csv", content, encoding="utf-8", bom=True)
    bom_doc = _parse(bom_path)
    check("UTF-8 BOM：encoding=utf-8-sig",
          (bom_doc.meta or {}).get("encoding") == "utf-8-sig",
          str((bom_doc.meta or {}).get("encoding")))
    header = bom_doc.pages[0].blocks[0].text
    check("BOM 已剥离（首格=姓名）", header == "姓名", header)

    gbk_path = _WORK / "gbk.csv"
    gbk_path.parent.mkdir(parents=True, exist_ok=True)
    gbk_path.write_bytes(content.encode("gbk"))
    gbk_doc = _parse(gbk_path)
    check("GBK：按 gb18030 解码",
          (gbk_doc.meta or {}).get("encoding") == "gb18030",
          str((gbk_doc.meta or {}).get("encoding")))
    check("GBK：单元格文本正确",
          any(block.text == "张伟" for block in gbk_doc.pages[0].blocks))

    semi_path = _write_text("semi.csv", "姓名;联系方式;邮箱\n张伟;13817264451;z@e.com\n")
    semi_doc = _parse(semi_path)
    check("分号分隔被识别",
          (semi_doc.meta or {}).get("delimiter") == ";",
          str((semi_doc.meta or {}).get("delimiter")))
    check("分号单元格 ref.sep=;",
          all(block.ref.get("sep") == ";" for block in semi_doc.pages[0].blocks))

    crlf_path = _write_text("crlf.csv", content, newline="\r\n")
    crlf_doc = _parse(crlf_path)
    check("CRLF：page.ref.eol=\\r\\n",
          (crlf_doc.pages[0].ref or {}).get("eol") == "\r\n",
          repr((crlf_doc.pages[0].ref or {}).get("eol")))
    crlf_out, crlf_info = _export_and_verify(crlf_path, "ai", "crlf_out.csv")
    check("CRLF 导出仍为 CRLF", b"\r\n" in crlf_out.read_bytes())
    check("CRLF 导出 residual=0", crlf_info.get("residual") == 0, str(crlf_info))

    multi_path = _write_text("multiline.csv", '姓名,备注\n张伟,"多行\n备注"\n')
    multi_doc = _parse(multi_path)
    check("引号内换行：2 行 × 2 列",
          (multi_doc.meta or {}).get("rows") == 2
          and (multi_doc.meta or {}).get("cols") == 2,
          str(multi_doc.meta))
    check("引号内换行：raw_rows_ok=False（走 writer 重写）",
          (multi_doc.meta or {}).get("raw_rows_ok") is False,
          str((multi_doc.meta or {}).get("raw_rows_ok")))
    multi_out, _info = _export_and_verify(multi_path, "ai", "multiline_out.csv")
    multi_rows = _read_csv_rows(multi_out)
    check("引号内换行：导出行/列数不变",
          len(multi_rows) == 2 and all(len(row) == 2 for row in multi_rows),
          str([len(row) for row in multi_rows]))
    check("引号内换行：单元格内容保留",
          multi_rows[1][1] == "多行\n备注", repr(multi_rows[1][1]))

    empty_path = _write_text("empty.csv", "")
    empty_doc = _parse(empty_path)
    check("空 CSV：0 行 0 Block",
          (empty_doc.meta or {}).get("rows") == 0 and not empty_doc.pages[0].blocks,
          str(empty_doc.meta))
    empty_out = _WORK / "out" / "empty_out.csv"
    empty_out.parent.mkdir(parents=True, exist_ok=True)
    pipeline.export(empty_doc, registry.find_parser(empty_path), [], empty_out, {})
    check("空 CSV 导出为空文件", empty_out.is_file() and empty_out.stat().st_size == 0)

    ragged_path = _write_text("ragged.csv", "姓名,手机号,邮箱\n张伟,13817264451\n")
    ragged_doc = _parse(ragged_path)
    check("列数不齐：row_cols=[3,2]",
          (ragged_doc.pages[0].ref or {}).get("row_cols") == [3, 2],
          str((ragged_doc.pages[0].ref or {}).get("row_cols")))


# ────────────────────────────── 3. 整列覆盖 ──────────────────────────────


def test_detector_coverage():
    section("3. 虚构客户名单整列覆盖（CSV 14/14、TSV 14/14、两级表头 10/10）")
    expected = _expected()
    for name in ("client_list.csv", "client_list.tsv", "two_level.csv"):
        doc = _parse(FIXTURES / name)
        got = _direct_map(doc)
        want = expected[name]
        check(f"{name} 覆盖集合与期望一致", _as_sets(got) == _as_sets(want),
              f"缺少 {sorted(set(want) - set(got))} 多出 {sorted(set(got) - set(want))}")
        total = sum(len(values) for values in want.values())
        actual = sum(len(values) for values in got.values())
        check(f"{name} 覆盖 {actual}/{total}", actual == total, f"{actual}/{total}")
    check("虚构值包含缺位身份证", "31010419870312451"
          in _direct_map(_parse(FIXTURES / "client_list.csv")).get("table.id_card", []))
    check("虚构值包含空格手机号", "138 1726 4451"
          in _direct_map(_parse(FIXTURES / "client_list.csv")).get("table.phone", []))
    check("虚构值包含无后缀单位", "某单位"
          in _direct_map(_parse(FIXTURES / "client_list.csv")).get("table.org", []))
    check("虚构值包含文本金额", "8000"
          in _direct_map(_parse(FIXTURES / "client_list.csv")).get("table.amount", []))


# ────────────────────────────── 4. 多级/合并表头 ──────────────────────────────


def test_multilevel_headers():
    section("4. 多级/合并表头继承")
    expected = _expected()
    merged = _direct_map(_parse(FIXTURES / "merged_header.csv"))
    want = expected["merged_header.csv"]
    check("合并表头：空槽向左继承为第 2 个电话列",
          set(merged.get("table.phone") or []) == set(want["table.phone"]),
          str(merged.get("table.phone")))
    check("合并表头：PERSON/ORG 不受电话列串扰",
          set(merged.get("table.person") or []) == set(want["table.person"])
          and set(merged.get("table.org") or []) == set(want["table.org"]),
          str(merged))

    vertical = _direct_map(_parse(FIXTURES / "vertical_multilevel.csv"))
    want_v = expected["vertical_multilevel.csv"]
    check("纵向继承：父行'联系方式'+叶行'手机号'两列均生效",
          set(vertical.get("table.phone") or []) == set(want_v["table.phone"]),
          str(vertical))
    check("纵向继承：PERSON/ORG 不被表头词误当数据",
          set(vertical.get("table.person") or []) == set(want_v["table.person"])
          and set(vertical.get("table.org") or []) == set(want_v["table.org"]),
          str(vertical))

    # inherit_merged=False 时不向左继承（只保留显式列）
    doc = _parse(FIXTURES / "merged_header.csv")
    rule = _rule_by_id("table.phone")
    detector = TableColumnDetector.from_spec(
        {"headers": rule.detect[0]["headers"], "inherit_merged": False})
    values = sorted({entity.value for entity in detector.detect(doc, rule, DetectContext())})
    check("inherit_merged=False 关闭合并继承",
          values == ["13817264451", "13901827733"], str(values))

    # max_inherit_span 限制：表头 1 个 + 后续 4 个空槽，跨度=1 时只覆盖前 3 列
    span_path = _write_text(
        "inherit_span.csv",
        "联系方式,,,,\n"
        "13817264451,13901827733,13611223344,13711112222,13512345678\n",
    )
    span_doc = _parse(span_path)
    detector = TableColumnDetector.from_spec(
        {"headers": ["联系方式"], "max_inherit_span": 1})
    values = sorted({entity.value for entity in detector.detect(span_doc, rule, DetectContext())})
    check("max_inherit_span=1 限制继承跨度",
          values == ["13611223344", "13817264451", "13901827733"], str(values))


# ────────────────────────────── 5. 无表头退化 ──────────────────────────────


def test_no_header_degradation():
    section("5. 无表头不误报 + legacy 规则退化生效")
    path = FIXTURES / "no_header.csv"
    doc = _parse(path)
    check("无表头 CSV：table_column 直接检测 0 命中", not _direct_map(doc),
          str(_direct_map(doc)))
    _doc, det = _full_detect(path)
    check("无表头 CSV：全量规则下 table-cn 0 命中", not _table_entities(det),
          str(_table_entities(det)))
    legacy = {entity.etype for entity in det.entities
              if (det.rules.get(entity.id) or None) is not None
              and det.rules[entity.id].pack != "table-cn"}
    check("无表头 CSV：legacy 规则仍逐格命中（退化正常）",
          len(legacy) >= 2, str(legacy))

    # 同样的内容存成 .txt：table-cn 不应命中
    txt_path = _write_text("no_header.txt", path.read_text("utf-8"))
    _doc2, det2 = _full_detect(txt_path)
    check("同内容 .txt：table-cn 0 命中", not _table_entities(det2),
          str(_table_entities(det2)))

    # 有"名称/数量"但不在白名单 → 不误报
    generic_path = _write_text("generic.csv", "产品名称,数量\n笔记本,3\n")
    _doc3, det3 = _full_detect(generic_path)
    check("泛词表头（产品名称/数量）不触发 table-cn",
          not _table_entities(det3), str(_table_entities(det3)))


# ────────────────────────────── 6. 导出/复检 ──────────────────────────────


def test_export_roundtrip():
    section("6. 导出：行数/列数不变、residual=0、原值不残留")
    csv_path = FIXTURES / "client_list.csv"
    original_rows = _read_csv_rows(csv_path)
    sensitive = ["张伟", "欧阳娜娜", "310104198703124510", "31010419870312451",
                 "13817264451", "138 1726 4451", "zhang@example.com",
                 "li@example.com", "某单位", "某某贸易有限公司", "12,000.00"]

    ai_out, ai_info = _export_and_verify(csv_path, "ai", "client_ai.csv")
    ai_rows = _read_csv_rows(ai_out)
    check("AI 版 residual=0", ai_info.get("residual") == 0, str(ai_info))
    check("AI 版行数不变", len(ai_rows) == len(original_rows), f"{len(ai_rows)}")
    check("AI 版列数不变",
          [len(row) for row in ai_rows] == [len(row) for row in original_rows],
          str([len(row) for row in ai_rows]))
    check("AI 版表头原文保留", ai_rows[0] == original_rows[0], str(ai_rows[0]))
    ai_text = ai_out.read_text("utf-8")
    leaked = [value for value in sensitive if value in ai_text]
    check("AI 版无敏感原值残留（抽样）", not leaked, str(leaked))

    human_out, human_info = _export_and_verify(csv_path, "human", "client_human.csv")
    human_rows = _read_csv_rows(human_out)
    check("涂黑版 residual=0", human_info.get("residual") == 0, str(human_info))
    check("涂黑版列数不变",
          [len(row) for row in human_rows] == [len(row) for row in original_rows],
          str([len(row) for row in human_rows]))
    human_text = human_out.read_text("utf-8")
    check("涂黑版含 █ 覆盖", "█" in human_text)
    leaked_human = [value for value in sensitive if value in human_text]
    check("涂黑版无敏感原值残留（抽样）", not leaked_human, str(leaked_human))

    tsv_path = FIXTURES / "client_list.tsv"
    tsv_out, tsv_info = _export_and_verify(tsv_path, "ai", "client_ai.tsv")
    tsv_rows = _read_csv_rows(tsv_out, delimiter="\t")
    check("TSV 导出 residual=0", tsv_info.get("residual") == 0, str(tsv_info))
    check("TSV 导出保持制表符分隔",
          tsv_rows[0][0] == "姓名" and len(tsv_rows[0]) == 7,
          str(tsv_rows[0]))
    check("TSV 导出列数不变",
          [len(row) for row in tsv_rows]
          == [len(row) for row in _read_csv_rows(tsv_path, delimiter="\t")],
          str([len(row) for row in tsv_rows]))


# ────────────────────────────── 7. 注册/规则 scope ──────────────────────────────


def test_rule_scope_and_registration():
    section("7. 规则 scope 与格式注册（Lead 已接点）")
    text_rules = [rule for rule in _table_rules()
                  if "text" in rule.scope]
    check("table-cn 11 条规则的 scope 均含 text（Lead 已接）",
          len(text_rules) == len(TABLE_RULE_IDS), f"实际 {len(text_rules)}")
    compiled_text = {rule.id for rule in rules_mod.compile_rules(_packs(), "text")}
    check("text 格式可编译出全部 table 规则", TABLE_RULE_IDS <= compiled_text,
          f"缺少 {sorted(TABLE_RULE_IDS - compiled_text)}")
    compiled_pdf = {rule.id for rule in rules_mod.compile_rules(_packs(), "pdf")}
    check("pdf 格式不含 table 规则", not (TABLE_RULE_IDS & compiled_pdf),
          f"多出 {sorted(TABLE_RULE_IDS & compiled_pdf)}")
    # 兼容：即使将来 doc.fmt 切到 "csv"/"tsv"，检测器也能工作
    doc = _parse(FIXTURES / "client_list.csv")
    doc.fmt = "csv"
    rule = _rule_by_id("table.phone")
    values = sorted({entity.value for entity in _detect_direct(doc, rule)})
    check("doc.fmt=csv 时检测器仍可用（兼容未来 EXT_TO_FMT）",
          values == ["138 1726 4451", "13817264451"], str(values))


# ────────────────────────────── 主流程 ──────────────────────────────

_TESTS = [
    test_parser_delimited,
    test_parser_variants,
    test_detector_coverage,
    test_multilevel_headers,
    test_no_header_degradation,
    test_export_roundtrip,
    test_rule_scope_and_registration,
]


def main() -> int:
    print("=" * 68)
    print("  CSV/TSV 列语义 + 多级/合并表头继承（P1-B）单测 · task-32")
    print("=" * 68)
    failed = 0
    for test in _TESTS:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {test.__name__}: {exc}")
        except Exception:
            failed += 1
            print(f"  [ERROR] {test.__name__}")
            traceback.print_exc()
    total = _PASS["ok"] + _PASS["fail"]
    print("\n" + "=" * 68)
    if failed:
        print(f"  失败 {failed}/{len(_TESTS)} 个测试组；"
              f"断言 {_PASS['ok']} 通过 / {_PASS['fail']} 失败（共 {total} 项）")
        print("=" * 68)
        return 1
    print(f"  全部通过：{len(_TESTS)} 个测试组，{_PASS['ok']} 项断言全绿")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
