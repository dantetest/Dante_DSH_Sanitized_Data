# -*- coding: utf-8 -*-
"""扫描件 PDF 表格列检测器（``pdf_table_column``）单测 —— Batch2-B3 / task-15。

运行（仓库任意目录，必须 -X utf8）：

    python -X utf8 app/tests/test_ai2_pdf_table.py

只写系统临时目录（``YINZHAN_DATA_DIR`` / ``YINZHAN_OUTPUT_DIR`` 指向 temp），
不污染仓库 data/。

覆盖：
1. 检测器注册、规则包（table-cn）加载、pdf 规则 scope/priority/strategy；
2. 合成 Document（Block + bbox）正样本：姓名列表头 ≥2 值 → PERSON 实体；
   证书号列表头 ≥2 值 → LICENSE 实体；
3. 同值多行合并成一个 Entity（多 span）；
4. 负样本：无表头 / x 不对齐 / 单值列 / 同一行重复 / 正文段落 / 表头词回流；
5. x 容差边界（表头宽 40% 与 ±8pt 取大者）；
6. pipeline 集成（compile_rules + detect）能真正产出实体；
7. 既有 xlsx/docx 的 table_column 规则不受影响。
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

# 必须在 import yinzhan.config 之前把数据/输出目录指向临时目录（不污染仓库）
_TMP = Path(tempfile.mkdtemp(prefix="yz_pdf_table_test_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(_TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
sys.path.insert(0, str(SERVER))

from yinzhan import pipeline, registry, rules as rules_mod          # noqa: E402
from yinzhan.detectors.pdf_table import PdfTableColumnDetector      # noqa: E402
from yinzhan.udm import (                                           # noqa: E402
    Block, DetectContext, Document, Entity, Page, Rule, Span,
)

_PASS = {"ok": 0, "fail": 0}
_ENGINE: tuple | None = None

PDF_RULES = (
    "table.pdf_person", "table.pdf_cert", "table.pdf_id_card",
    "table.pdf_phone", "table.pdf_account", "table.pdf_org",
)


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


# ────────────────────────────── 引擎 ──────────────────────────────


def _engine() -> tuple:
    global _ENGINE
    if _ENGINE is None:
        errors = registry.load_plugins()
        if errors:
            raise RuntimeError(f"插件加载失败: {errors}")
        packs = rules_mod.load_packs()
        _ENGINE = (packs, rules_mod.load_errors())
    return _ENGINE


def _packs() -> dict:
    return _engine()[0]


def _rule(rid: str) -> Rule:
    rule = rules_mod.get_rule(_packs(), rid)
    if rule is None:
        raise AssertionError(f"规则缺失: {rid}")
    return rule


def _detector(rid: str) -> PdfTableColumnDetector:
    rule = _rule(rid)
    return PdfTableColumnDetector.from_spec(rule.detect[0])


# ────────────────────────────── 合成 Document ──────────────────────────────


def _block(bid: str, text: str, bbox: tuple[float, float, float, float]) -> Block:
    return Block(id=bid, kind="text", text=text, bbox=bbox, page=0,
                 ref={"ocr": True})


def _doc(blocks: list[Block], page: int = 0) -> Document:
    return Document(
        doc_id=f"synthetic-{page}",
        path="synthetic.pdf",
        fmt="pdf",
        pages=[Page(index=page, width=596.0, height=842.0, blocks=blocks)],
    )


def _col_header(x0: float, text: str) -> Block:
    """表头 block：宽 30pt 或 40pt（按文本长度近似），y=100..116。"""
    width = 30.0 if len(text) <= 3 else 40.0
    return _block(f"h_{text}", text, (x0, 100.0, x0 + width, 116.0))


def _cell(bid: str, text: str, x0: float, y0: float,
          width: float = 30.0) -> Block:
    return _block(bid, text, (x0, y0, x0 + width, y0 + 16.0))


def _names_doc(names: list[str], x0: float = 100.0) -> Document:
    blocks = [_col_header(x0, "姓名")]
    for index, name in enumerate(names):
        blocks.append(_cell(f"n{index}", name, x0, 130.0 + index * 30.0))
    return _doc(blocks)


def _certs_doc(certs: list[str], x0: float = 300.0) -> Document:
    blocks = [_col_header(x0, "证书号")]
    for index, cert in enumerate(certs):
        blocks.append(_cell(f"c{index}", cert, x0, 130.0 + index * 30.0,
                            width=60.0))
    return _doc(blocks)


def _detect(doc: Document, rid: str) -> list[Entity]:
    ctx = DetectContext()
    return _detector(rid).detect(doc, _rule(rid), ctx)


def _values(entities: list[Entity], etype: str = "") -> list[str]:
    return sorted(e.value for e in entities if not etype or e.etype == etype)


# ────────────────────────────── 用例 ──────────────────────────────


def test_registration_and_rules() -> None:
    section("1. 注册与规则")
    detectors = registry.all_detectors()
    check("检测器已注册 pdf_table_column", "pdf_table_column" in detectors,
          f"detectors={sorted(detectors)}")
    check("检测器 id 一致", detectors["pdf_table_column"].id == "pdf_table_column")

    packs = _packs()
    pdf_rules = rules_mod.compile_rules(packs, "pdf")
    pdf_ids = {rule.id for rule in pdf_rules}
    for rid in PDF_RULES:
        check(f"pdf 规则已加载 {rid}", rid in pdf_ids)
        rule = _rule(rid)
        check(f"{rid} scope 含 pdf", "pdf" in rule.scope, str(rule.scope))
        check(f"{rid} priority < 15", rule.priority < 15, str(rule.priority))
        check(f"{rid} surrogate", rule.replace.get("strategy") == "surrogate",
              str(rule.replace))
        check(f"{rid} detect=pdf_table_column",
              rule.detect[0].get("type") == "pdf_table_column")
        check(f"{rid} 有 headers", bool(rule.detect[0].get("headers")))

    person_spec = _rule("table.pdf_person").detect[0]
    cert_spec = _rule("table.pdf_cert").detect[0]
    check("姓名规则 headers 含 姓名", "姓名" in person_spec["headers"])
    check("证书规则 headers 含 证书号", "证书号" in cert_spec["headers"])
    check("姓名规则 value_kind=person", person_spec.get("value_kind") == "person")
    check("证书规则 value_kind=cert", cert_spec.get("value_kind") == "cert")
    # xlsx/docx 仍由 table_column 负责
    xlsx_ids = {rule.id for rule in rules_mod.compile_rules(packs, "xlsx")}
    check("xlsx 不加载 pdf 规则", not (set(PDF_RULES) & xlsx_ids))
    check("xlsx 仍加载 table.person", "table.person" in xlsx_ids)


def test_positive_name_column() -> None:
    section("2. 正样本：姓名列")
    doc = _names_doc(["张伟", "李娜", "王芳"])
    entities = _detect(doc, "table.pdf_person")
    check("姓名列生成 3 个实体", len(entities) == 3, str(len(entities)))
    check("姓名值正确", _values(entities) == ["张伟", "李娜", "王芳"],
          str(_values(entities)))
    for entity in entities:
        check(f"{entity.value} etype=PERSON", entity.etype == "PERSON")
        check(f"{entity.value} label=表格姓名列", entity.label == "表格姓名列")
        check(f"{entity.value} level=L2", entity.level == "L2")
        check(f"{entity.value} confidence=0.85", abs(entity.confidence - 0.85) < 1e-6,
              str(entity.confidence))
        check(f"{entity.value} origin=table", entity.origin == "table")
        check(f"{entity.value} span 定位 block", len(entity.spans) == 1
              and entity.spans[0].block_id and entity.spans[0].start == 0
              and entity.spans[0].end == len(entity.value),
              str(entity.spans[0].to_dict()))


def test_positive_cert_column() -> None:
    section("3. 正样本：证书号列")
    doc = _certs_doc(["G3300191668", "1300101061931"])
    entities = _detect(doc, "table.pdf_cert")
    check("证书号列生成 2 个实体", len(entities) == 2, str(len(entities)))
    check("证书号值正确", _values(entities) == ["1300101061931", "G3300191668"],
          str(_values(entities)))
    for entity in entities:
        check(f"{entity.value} etype=LICENSE", entity.etype == "LICENSE")
        check(f"{entity.value} label=表格证书号列", entity.label == "表格证书号列")


def test_same_value_multi_rows() -> None:
    section("4. 同值多行合并")
    doc = _names_doc(["林力成", "赵金枝", "林力成"])
    entities = _detect(doc, "table.pdf_person")
    check("同值只出一个实体", len(entities) == 2, str(len(entities)))
    same = [entity for entity in entities if entity.value == "林力成"]
    check("林力成 2 个 span", len(same) == 1 and len(same[0].spans) == 2,
          str([s.to_dict() for s in same[0].spans]) if same else "missing")


def test_no_header() -> None:
    section("5. 负样本：无表头")
    blocks = [_cell(f"n{i}", name, 100.0, 130.0 + i * 30.0)
              for i, name in enumerate(["张伟", "李娜"])]
    entities = _detect(_doc(blocks), "table.pdf_person")
    check("无表头不触发", entities == [], str(_values(entities)))


def test_misaligned_x() -> None:
    section("6. 负样本：x 不对齐")
    blocks = [_col_header(100.0, "姓名")]
    for i, name in enumerate(["张伟", "李娜"]):
        # 表头 x 范围 [100,130]，容差 12 -> [88,142]；这里中心 215
        blocks.append(_cell(f"n{i}", name, 200.0, 130.0 + i * 30.0))
    entities = _detect(_doc(blocks), "table.pdf_person")
    check("x 不对齐不触发", entities == [], str(_values(entities)))


def test_single_value() -> None:
    section("7. 负样本：单值列")
    entities = _detect(_names_doc(["张伟"]), "table.pdf_person")
    check("单值列不触发", entities == [], str(_values(entities)))


def test_same_row_duplicates() -> None:
    section("8. 负样本：同一行两个值")
    blocks = [_col_header(100.0, "姓名"),
              _cell("n0", "张伟", 96.0, 130.0),
              _cell("n1", "李娜", 112.0, 130.0)]
    entities = _detect(_doc(blocks), "table.pdf_person")
    check("同一行不成列", entities == [], str(_values(entities)))


def test_body_paragraph() -> None:
    section("9. 负样本：正文段落")
    blocks = [_col_header(100.0, "姓名"),
              _block("para0", "本公司郑重承诺对招标文件的所有内容严格保密",
                     (100.0, 130.0, 430.0, 146.0)),
              _block("para1", "如中标将按合同约定配备人员并接受监督",
                     (100.0, 160.0, 430.0, 176.0))]
    entities = _detect(_doc(blocks), "table.pdf_person")
    check("正文段落不触发", entities == [], str(_values(entities)))
    # 普通正文页：没有表头 block，即使有 2 个像人名的短行也不触发
    body = _doc([_block("p0", "张伟", (100.0, 130.0, 130.0, 146.0)),
                 _block("p1", "李娜", (100.0, 160.0, 130.0, 176.0))])
    check("正文页无表头不触发", _detect(body, "table.pdf_person") == [])


def test_header_word_as_value() -> None:
    section("10. 负样本：表头词回流")
    blocks = [_col_header(100.0, "姓名"),
              _cell("d0", "姓名", 100.0, 130.0),
              _cell("d1", "姓名", 100.0, 160.0)]
    entities = _detect(_doc(blocks), "table.pdf_person")
    check("重复表头不当数据", entities == [], str(_values(entities)))


def test_x_tolerance_boundary() -> None:
    section("11. x 容差边界（表头宽 40% 与 ±8pt 取大者）")
    # 表头宽 30 -> 容差 max(8, 12) = 12；右边界 142
    inside = _doc([
        _col_header(100.0, "姓名"),
        _cell("a0", "张伟", 132.0, 130.0, width=20.0),   # 中心 142
        _cell("a1", "李娜", 132.0, 160.0, width=20.0),
    ])
    check("边界内命中", len(_detect(inside, "table.pdf_person")) == 2,
          str(_values(_detect(inside, "table.pdf_person"))))
    outside = _doc([
        _col_header(100.0, "姓名"),
        _cell("b0", "张伟", 133.0, 130.0, width=20.0),   # 中心 143
        _cell("b1", "李娜", 133.0, 160.0, width=20.0),
    ])
    check("边界外不命中", _detect(outside, "table.pdf_person") == [])


def test_pipeline_integration() -> None:
    section("12. pipeline 集成")
    packs = _packs()
    pdf_rules = rules_mod.compile_rules(packs, "pdf")
    blocks = [
        _col_header(100.0, "姓名"),
        _col_header(300.0, "证书号"),
        _cell("n0", "张伟", 100.0, 130.0),
        _cell("n1", "李娜", 100.0, 160.0),
        _cell("c0", "G3300191668", 300.0, 130.0, width=60.0),
        _cell("c1", "1300101061931", 300.0, 160.0, width=60.0),
    ]
    det = pipeline.detect(_doc(blocks), pdf_rules, "standard")
    values = {(entity.etype, entity.value) for entity in det.entities}
    check("pipeline 产出姓名实体",
          {("PERSON", "张伟"), ("PERSON", "李娜")} <= values, str(sorted(values)))
    check("pipeline 产出证书实体",
          {("LICENSE", "G3300191668"), ("LICENSE", "1300101061931")} <= values,
          str(sorted(values)))
    # 只对本包规则断言：工作区可能并行改别的规则包，不能因无关规则的
    # 编辑器瞬态告警（如 joined_regex 正被改写）判定本检测器失败。
    relevant = [w for w in det.warnings
                if "pdf_table_column" in w or "table.pdf_" in w]
    check("pipeline 无 table.pdf 检测告警", not relevant, str(relevant))


def test_overlap_with_existing() -> None:
    section("13. 与已存在实体重叠时跳过")
    doc = _names_doc(["张伟", "李娜"])
    ctx = DetectContext()
    ctx.entities = [Entity(
        id="manual_1", etype="PERSON", label="人名", value="张伟",
        spans=[Span(block_id="n0", start=0, end=2)],
        level="L2", confidence=1.0, origin="manual",
    )]
    entities = _detector("table.pdf_person").detect(doc, _rule("table.pdf_person"), ctx)
    check("已占位 span 不重复输出", _values(entities) == ["李娜"],
          str(_values(entities)))


# ────────────────────────────── 主入口 ──────────────────────────────


def main() -> int:
    print(f"扫描件 PDF 表格列检测器自测（临时区 {_TMP}）")
    tests = [
        test_registration_and_rules,
        test_positive_name_column,
        test_positive_cert_column,
        test_same_value_multi_rows,
        test_no_header,
        test_misaligned_x,
        test_single_value,
        test_same_row_duplicates,
        test_body_paragraph,
        test_header_word_as_value,
        test_x_tolerance_boundary,
        test_pipeline_integration,
        test_overlap_with_existing,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception:  # noqa: BLE001 - 每个用例独立，失败继续跑完看全貌
            failed += 1
            print(f"  [ERROR] {test.__name__}")
            traceback.print_exc()
    print(f"\n结果：{_PASS['ok']} PASS / {_PASS['fail']} FAIL / {failed} 用例异常")
    if failed or _PASS["fail"]:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
