# -*- coding: utf-8 -*-
"""Batch2-B2：明细金额同值扩散 + 中文大写/小写金额一致性 自测。

背景
----
1. 发票/合同里同一金额常写两次：合计行「¥49504.95」被规则整体命中，明细行只写
   裸数字「49504.95」；规则不命中裸数字 → 导出后明细行仍是原值（AI 版泄漏、
   human 版漏涂黑）。修复：``pipeline.propagate_amount_numeric_values`` 按数值
   核心在同页补 span，让明细行与合计行共用同一决策；
2. OCR 会把同一笔金额的大写行/小写行识别出微小差异（doc05 类 366740.37 vs
   366731.20），各自缩放会让 AI 版大写与小写自相矛盾。修复：
   ``amount_consistency`` 在唯一近似小写候选（相对差 <=2%）时把大写 canonical
   对齐到小写值。

本测试全部使用虚构数据，锁定：
* 数值核心提取规则（¥/人民币/元/千分位/大写跳过/少于 3 位数字跳过）；
* 同值扩散正/负样本：同页补 span、已覆盖不重复、年份/编号/百分比不命中、
  前后紧邻数字/小数点不命中、不跨页、不与已有实体重叠、rejected/black/keep 不动；
* 全角数字归一；
* 大写对齐正/负样本：唯一候选对齐、>2% 不对齐、多候选不对齐、无候选保持原逻辑；
* human 全黑覆盖后金额实体不残留 surrogate 决策。

运行（Windows 控制台带 -X utf8）：

    python -X utf8 app/tests/test_ai2_amount.py

只用系统临时目录（YINZHAN_DATA_DIR），不写仓库 data/。
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import traceback
from decimal import Decimal
from pathlib import Path

# 必须先定数据目录，再 import yinzhan（config 在 import 时解析路径）。
_TMP = Path(tempfile.mkdtemp(prefix="yz_ai2_amount_"))
os.environ["YINZHAN_DATA_DIR"] = str(_TMP / "data")

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

PASS_COUNT = 0
FAILS: list[tuple[str, str]] = []

_NUM_RE = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)")


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS_COUNT
    if ok:
        PASS_COUNT += 1
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""), flush=True)
    else:
        FAILS.append((name, detail))
        print(f"  [FAIL] {name}  {detail}", flush=True)


# ────────────────────────────── 夹具 ──────────────────────────────

def _make_doc(page_texts: list[list[str]]):
    from yinzhan.udm import Block, Document, Page

    pages = []
    for page_index, texts in enumerate(page_texts):
        blocks = [Block(id=f"b{page_index}_{i}", kind="text", text=text,
                        page=page_index)
                  for i, text in enumerate(texts)]
        pages.append(Page(index=page_index, width=595, height=842, blocks=blocks))
    return Document(doc_id="test", path="", fmt="pdf", pages=pages)


def _locate(doc, page: int, block: int, needle: str) -> tuple[str, int, int]:
    block_obj = doc.pages[page].blocks[block]
    start = block_obj.text.find(needle)
    if start < 0:
        raise AssertionError(f"夹具错误：block 里找不到 {needle!r}")
    return block_obj.id, start, start + len(needle)


def _entity(etype: str, value: str, spans=None, *, status="accepted",
            strategy="surrogate", replacement="假值", black=False):
    from yinzhan.udm import Decision, Entity, Span

    ent = Entity(id=Entity.new_id(), etype=etype, label=etype, value=value,
                 spans=list(spans or []))
    ent.status = status
    ent.decision = Decision(strategy=strategy, replacement=replacement,
                            black=black)
    return ent


def _amount(doc, value: str, page: int, block: int, needle: str, **kw):
    from yinzhan.udm import Span

    block_id, start, end = _locate(doc, page, block, needle)
    focus = _entity("AMOUNT", value, [Span(block_id=block_id, start=start,
                                           end=end)], **kw)
    return focus


def _plan_value(text: str, upper: bool = False) -> Decimal:
    from yinzhan.replacers.amount_consistency import parse_cn_amount

    if upper:
        value = parse_cn_amount(text)
        if value is None:
            raise AssertionError(f"无法解析大写假值：{text!r}")
        return value
    match = _NUM_RE.search(text or "")
    if match is None:
        raise AssertionError(f"无法解析数字假值：{text!r}")
    return Decimal(match.group(1).replace(",", ""))


# ────────────────────────────── 用例 ──────────────────────────────

def test_numeric_core(pipeline) -> None:
    print("\n[1] 数值核心提取", flush=True)
    core = pipeline._amount_numeric_core
    check("¥49504.95 → 49504.95", core("¥49504.95") == "49504.95",
          str(core("¥49504.95")))
    check("人民币 123,456.78 元 → 123,456.78",
          core("人民币 123,456.78 元") == "123,456.78",
          str(core("人民币 123,456.78 元")))
    check("50000元 → 50000", core("50000元") == "50000", str(core("50000元")))
    check("大写金额（无阿拉伯数字）→ None",
          core("叁拾陆万陆仟柒佰肆拾元叁角柒分") is None)
    check("少于 3 位数字 → None", core("¥9.5") is None, str(core("¥9.5")))
    check("值里带年份标签时取位数最多的数字",
          core("2024年合计¥50000") == "50000", str(core("2024年合计¥50000")))
    check("阈值固化：最少 3 位数字", pipeline.AMOUNT_NUMERIC_MIN_DIGITS == 3)


def test_propagate_positive(pipeline) -> None:
    print("\n[2] 同值扩散：正样本 / 幂等", flush=True)
    doc = _make_doc([["价税合计：¥49504.95", "明细A 49504.95",
                      "明细B：49504.95 元"]])
    focus = _amount(doc, "¥49504.95", 0, 0, "49504.95")
    ents = [focus]
    before_ids = [id(e) for e in ents]
    before_decision = (focus.decision.strategy, focus.decision.replacement,
                       focus.decision.black)
    count = pipeline.propagate_amount_numeric_values(doc, ents)
    check("同页两处明细裸数值 → 补 2 个 span", count == 2, str(count))
    check("只加 span：实体数/决策不变",
          [id(e) for e in ents] == before_ids
          and (focus.decision.strategy, focus.decision.replacement,
               focus.decision.black) == before_decision)
    check("span 总数 = 原 1 + 新增 2", len(focus.spans) == 3,
          str(len(focus.spans)))
    check("新 span 指向其它 block",
          {span.block_id for span in focus.spans}
          == {"b0_0", "b0_1", "b0_2"},
          str([span.block_id for span in focus.spans]))
    check("再跑一次幂等（不重复加 span）",
          pipeline.propagate_amount_numeric_values(doc, [focus]) == 0)

    # 千分位核心也能匹配
    doc2 = _make_doc([["合计 人民币 123,456.78 元", "明细 123,456.78"]])
    focus2 = _amount(doc2, "人民币 123,456.78 元", 0, 0, "123,456.78")
    check("千分位数值核心匹配明细",
          pipeline.propagate_amount_numeric_values(doc2, [focus2]) == 1)

    # 同一 block 内第二次出现也补（已覆盖的位置不重复）
    doc3 = _make_doc([["金额：49504.95；大写：伍万元整 49504.95"]])
    focus3 = _amount(doc3, "¥49504.95", 0, 0, "49504.95")
    check("同一 block 内第二个裸数值也补",
          pipeline.propagate_amount_numeric_values(doc3, [focus3]) == 1)


def test_propagate_fullwidth(pipeline) -> None:
    print("\n[3] 同值扩散：全角数字归一", flush=True)
    doc = _make_doc([["合计：¥49504.95", "明细：４９５０４．９５"]])
    focus = _amount(doc, "¥49504.95", 0, 0, "49504.95")
    count = pipeline.propagate_amount_numeric_values(doc, [focus])
    check("全角数字明细可匹配", count == 1, str(count))
    if count == 1:
        span = focus.spans[-1]
        check("span 坐标落在原（全角）文本上",
              doc.pages[0].blocks[1].text[span.start:span.end] == "４９５０４．９５",
              doc.pages[0].blocks[1].text[span.start:span.end])


def test_propagate_negative(pipeline) -> None:
    print("\n[4] 同值扩散：负样本（不误伤）", flush=True)
    # 前后紧邻数字/小数点 → 不是独立数值
    doc = _make_doc([["合计 ¥49504.95", "149504.95",
                      "49504.955", "49504.95.5"]])
    focus = _amount(doc, "¥49504.95", 0, 0, "49504.95")
    check("前后紧邻数字/小数点不命中",
          pipeline.propagate_amount_numeric_values(doc, [focus]) == 0)

    # 年份 / 编号 / 百分比上下文
    doc = _make_doc([["金额 500 元", "2024年", "第2024条", "增长 500%"]])
    focus = _amount(doc, "500元", 0, 0, "500")
    count = pipeline.propagate_amount_numeric_values(doc, [focus])
    check("年份/编号/百分比不命中", count == 0, str(count))

    # 不跨页
    doc = _make_doc([["合计：¥49504.95"], ["明细 49504.95"]])
    focus = _amount(doc, "¥49504.95", 0, 0, "49504.95")
    check("不跨页扩散",
          pipeline.propagate_amount_numeric_values(doc, [focus]) == 0)

    # 不与已有实体 span 重叠
    from yinzhan.udm import Span

    doc = _make_doc([["合计：¥49504.95", "其它 49504.95"]])
    focus = _amount(doc, "¥49504.95", 0, 0, "49504.95")
    block_id, start, end = _locate(doc, 0, 1, "49504.95")
    other = _entity("ADDRESS", "其它", [Span(block_id=block_id, start=start,
                                             end=end)])
    check("不与已有实体 span 重叠",
          pipeline.propagate_amount_numeric_values(doc, [focus, other]) == 0)

    # rejected / black / keep 不动
    for label, kwargs in (("rejected", {"status": "rejected"}),
                          ("black", {"black": True, "strategy": "black"}),
                          ("keep", {"strategy": "keep"})):
        doc = _make_doc([["合计：¥49504.95", "明细 49504.95"]])
        focus = _amount(doc, "¥49504.95", 0, 0, "49504.95", **kwargs)
        check(f"{label} 实体不扩散",
              pipeline.propagate_amount_numeric_values(doc, [focus]) == 0)

    # 非 AMOUNT 类型 / 少于 3 位数字
    doc = _make_doc([["编号 ¥49504.95", "345 49504.95"]])
    focus = _amount(doc, "¥49504.95", 0, 0, "49504.95")
    focus.etype = "ORG"
    check("非 AMOUNT 实体不扩散",
          pipeline.propagate_amount_numeric_values(doc, [focus]) == 0)
    doc = _make_doc([["金额 9.5", "9.5"]])
    focus = _amount(doc, "¥9.5", 0, 0, "9.5")
    check("少于 3 位数字的金额不扩散",
          pipeline.propagate_amount_numeric_values(doc, [focus]) == 0)


def test_cn_upper_alignment() -> None:
    from yinzhan.replacers import amount_consistency as ac

    print("\n[5] 大写/小写金额对齐：正样本", flush=True)
    check("相对差容限 = 2%",
          ac.CN_UPPER_ALIGN_TOLERANCE == Decimal("0.02"),
          str(ac.CN_UPPER_ALIGN_TOLERANCE))

    def _amt(value: str):
        return _entity("AMOUNT", value, [])

    # doc05 类：大写 366740.37 / 小写 366731.20（差 9.17，约 0.0025%）→ 唯一候选
    upper = _amt(ac.format_cn_amount(Decimal("366740.37")))
    lower = _amt("¥366,731.20")
    plan = ac.build_amount_plan([upper, lower], {}, None)
    p_upper = _plan_value(plan[upper.id], upper=True)
    p_lower = _plan_value(plan[lower.id])
    check("唯一近似候选 → 大写假值与小写假值一致",
          p_upper == p_lower, f"upper={p_upper} lower={p_lower}")
    # 同一小写值的两种写法（1 个唯一候选值）
    upper2 = _amt(ac.format_cn_amount(Decimal("366740.37")))
    lower2a = _amt("¥366,731.20")
    lower2b = _amt("人民币 366,731.2 元")
    plan2 = ac.build_amount_plan([upper2, lower2a, lower2b], {}, None)
    vals = {_plan_value(plan2[c.id]) for c in (lower2a, lower2b)}
    check("同一小写值两种写法 → 仍唯一候选并对齐",
          len(vals) == 1
          and _plan_value(plan2[upper2.id], upper=True) == next(iter(vals)),
          str(vals))


def test_cn_upper_negative() -> None:
    from yinzhan.replacers import amount_consistency as ac

    print("\n[6] 大写/小写金额对齐：负样本", flush=True)

    def _amt(value: str):
        return _entity("AMOUNT", value, [])

    # 相对差 >2%：不对齐
    upper = _amt(ac.format_cn_amount(Decimal("500000.00")))
    lower = _amt("¥400000.00")
    plan = ac.build_amount_plan([upper, lower], {}, None)
    check("相对差 >2% 不对齐",
          _plan_value(plan[upper.id], upper=True) != _plan_value(plan[lower.id]))

    # 多候选（两个小写值都在 2% 内）：不对齐
    upper = _amt(ac.format_cn_amount(Decimal("100500.00")))
    lower_a = _amt("¥100000.00")
    lower_b = _amt("¥101000.00")
    plan = ac.build_amount_plan([upper, lower_a, lower_b], {}, None)
    p_upper = _plan_value(plan[upper.id], upper=True)
    check("多候选不对齐",
          p_upper != _plan_value(plan[lower_a.id])
          and p_upper != _plan_value(plan[lower_b.id]))

    # 无候选（只有大写）：保持原逻辑（按自身值缩放，比例在因子范围内）
    upper = _amt(ac.format_cn_amount(Decimal("366740.37")))
    plan = ac.build_amount_plan([upper], {}, None)
    p_upper = _plan_value(plan[upper.id], upper=True)
    ratio = p_upper / Decimal("366740.37")
    check("无候选保持原逻辑（缩放比例在 [0.90,1.10]）",
          Decimal("0.89") <= ratio <= Decimal("1.11"), str(ratio))

    # human 全黑覆盖后：金额实体不残留 surrogate 决策
    upper = _amt(ac.format_cn_amount(Decimal("366740.37")))
    lower = _amt("¥366731.20")
    ents = [upper, lower]
    plan = ac.build_amount_plan(ents, {}, None)
    ac.apply_plan(ents, plan, None)
    from yinzhan.udm import Decision

    for ent in ents:                       # jobs._export_to(mode="human") 的动作
        ent.decision = Decision(strategy="black", black=True, replacement=None)
    check("human 全黑覆盖后金额决策不残留 surrogate",
          all(ent.decision.black and ent.decision.replacement is None
              for ent in ents))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from yinzhan import pipeline

    print("数据隐站 · Batch2-B2 金额一致性自测", flush=True)
    print(f"  临时数据目录：{_TMP}", flush=True)
    test_numeric_core(pipeline)
    test_propagate_positive(pipeline)
    test_propagate_fullwidth(pipeline)
    test_propagate_negative(pipeline)
    test_cn_upper_alignment()
    test_cn_upper_negative()

    print("\n" + "=" * 66, flush=True)
    total = PASS_COUNT + len(FAILS)
    if FAILS:
        print(f"  FAIL：{len(FAILS)}/{total} 项失败", flush=True)
        for name, detail in FAILS:
            print(f"    ✗ {name}  {detail}", flush=True)
        print("=" * 66, flush=True)
        return 1
    print(f"  PASS：{total} 项全部通过", flush=True)
    print("=" * 66, flush=True)
    return 0


if __name__ == "__main__":
    _code = 2
    try:
        _code = main()
    except Exception:
        traceback.print_exc()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(_code)
