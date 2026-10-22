# -*- coding: utf-8 -*-
"""Batch2-B1 补检测规则测试：发票开票人（跨块）/ 联系人 / 账户名称 / 并列当事人。

覆盖 10 份真实 PDF 反馈的 4 类漏检（规则见 finance-cn / legal-cn / common）：

1. 电子发票版式把「开票人：」和姓名拆成两个 block——joined_regex 跨块兜住；
2. 公函「联系人:王铁栓」标签 + 姓名；
3. 合同「账户名称:冯彪」标签 + 姓名；
4. 受理通知书「包罗、殷莹：」与「……、杨尚坤一案」并列姓名。

每一类都配负样本：详见附件/见合同/办公室、甲方乙方标签、公司个人一案、
正文顿号短语（经签字、盖章 / 文件、合法 / 路面、桥涵）等，避免把说明文字
或标签词当人名（宁少勿错）。

只构造纯文本 Document，不读写仓库 data/；临时目录见 TMP。
运行（仓库任意目录，建议 -X utf8）：

    python -X utf8 app/tests/test_ai2_rules.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
ROOT = TEST_DIR.parents[1]
TMP = Path(tempfile.mkdtemp(prefix="yz_ai2_rules_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(TMP / "out"))
sys.path.insert(0, str(ROOT / "app" / "server"))

from yinzhan import pipeline, registry  # noqa: E402
from yinzhan.detectors.joined_det import JoinedRegexDetector  # noqa: E402
from yinzhan.jobs import _load_rules  # noqa: E402
from yinzhan.udm import Block, DetectContext, Document, Page  # noqa: E402

PASS = 0
FAIL = 0

#: 本轮新增/扩展的规则与标签。负样本只针对这些规则断言"不命中"，
#: 避免把 cn.person（NER）等既有规则的历史行为算到本批头上。
NEW_LABELS = {"开票人", "联系人", "账户名称", "当事人"}
NEW_RULE_IDS = {"cn.account_name", "cn.contact_person", "legal.party_name"}


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))


def make_doc(blocks: list[str]) -> Document:
    doc = Document(doc_id="d_ai2", path="", fmt="text")
    page = Page(index=0, width=595, height=842)
    for i, text in enumerate(blocks):
        page.blocks.append(Block(id=f"b{i}", kind="text", text=text,
                                 bbox=(40.0, 40.0 + i * 20, 560.0, 55.0 + i * 20),
                                 page=0))
    doc.pages.append(page)
    return doc


def detect(blocks: list[str]):
    doc = make_doc(blocks)
    rules, _pack, _ = _load_rules("text")
    det = pipeline.detect(doc, rules, "standard")
    return doc, det


def persons(det) -> list:
    return [e for e in det.entities if e.etype == "PERSON"]


def has_person(det, value: str, label: str | None = None) -> bool:
    return any(e.etype == "PERSON" and e.value == value
               and (label is None or e.label == label)
               for e in det.entities)


def new_rule_hits(det, labels=None) -> list:
    labels = labels or NEW_LABELS
    return [e for e in det.entities if e.label in labels]


def rule_spec(rule_id: str, kind: str) -> tuple:
    rules, _pack, _ = _load_rules("text")
    for rule in rules:
        if rule.id != rule_id:
            continue
        for spec in rule.detect:
            if spec.get("type") == kind:
                return rule, spec
    raise AssertionError(f"找不到规则 {rule_id} 的 {kind} detect")


def main() -> int:
    errors = registry.load_plugins()
    check("插件加载无错误", not errors, str(errors[:1]))

    # ── 规则落盘 ──────────────────────────────────────────────
    rules, _pack, _ = _load_rules("text")
    by_id = {rule.id: rule for rule in rules}
    for rid in NEW_RULE_IDS:
        check(f"规则存在 {rid}", rid in by_id)
    check("cn.invoice_person 含 regex + joined_regex 两种写法",
          any(spec.get("type") == "regex" for spec in by_id["cn.invoice_person"].detect)
          and any(spec.get("type") == "joined_regex"
                  for spec in by_id["cn.invoice_person"].detect))
    joined = next(spec for spec in by_id["cn.invoice_person"].detect
                  if spec.get("type") == "joined_regex")
    check("发票跨块 max_blocks 在 2–3", 2 <= int(joined.get("max_blocks", 0)) <= 3,
          str(joined.get("max_blocks")))
    check("发票跨块用 (?P<value>) 只取姓名",
          "(?P<value>" in str(joined.get("pattern", "")))
    check("新增规则实体类型均为 PERSON",
          all(by_id[rid].entity == "PERSON" for rid in NEW_RULE_IDS))
    check("新增规则替换策略均为 surrogate",
          all(by_id[rid].replace.get("strategy") == "surrogate"
              for rid in NEW_RULE_IDS))
    check("规则 id 不重复", len(by_id) == len(rules))

    # ── 正样本 1：发票开票人跨 block ─────────────────────────
    _doc, det = detect(["电子发票（普通发票）", "备注", "开票人：", "马靖"])
    check("发票跨块：开票人 马靖", has_person(det, "马靖", "开票人"),
          str([(e.label, e.value) for e in persons(det)]))
    check("发票跨块：实体值不含标签/冒号",
          not any("开票人" in e.value or "：" in e.value for e in persons(det)))

    # 跨块也应支持同页后面还有其它文本块（姓名后是块尾）
    _doc, det = detect(["开票人：", "包罗", "下载次数：1"])
    check("发票跨块：包罗（姓名后跟其它页边块）", has_person(det, "包罗", "开票人"),
          str([(e.label, e.value) for e in persons(det)]))

    _doc, det = detect(["开票人：", "马靖 下载次数：1"])
    check("发票跨块：姓名后跟空格+页边文字", has_person(det, "马靖", "开票人"),
          str([(e.label, e.value) for e in persons(det)]))

    # 同一 block 写法保持原有能力
    _doc, det = detect(["开票人：李静怡"])
    check("发票同块：开票人 李静怡", has_person(det, "李静怡", "开票人"))

    # ── 正样本 2：联系人 ─────────────────────────────────────
    _doc, det = detect(["云基函〔2026〕55号", "联系人:王铁栓"])
    check("公函：联系人 王铁栓", has_person(det, "王铁栓", "联系人"),
          str([(e.label, e.value) for e in persons(det)]))

    _doc, det = detect(["联系人:王铁栓 电话:13800000000"])
    check("联系人后跟空格+电话：仍命中王铁栓", has_person(det, "王铁栓", "联系人"),
          str([(e.label, e.value) for e in persons(det)]))

    _doc, det = detect(["联系人：欧阳小丽"])
    check("联系人：复姓 4 字姓名", has_person(det, "欧阳小丽"))

    # ── 正样本 3：合同账户名称 ───────────────────────────────
    _doc, det = detect(["乙方指定以下账户作为收款账户:", "账户名称:冯彪"])
    check("合同：账户名称 冯彪", has_person(det, "冯彪", "账户名称"),
          str([(e.label, e.value) for e in persons(det)]))

    _doc, det = detect(["账户名称:冯彪 开户行:某某银行"])
    check("账户名称后跟空格+开户行：仍命中冯彪", has_person(det, "冯彪", "账户名称"),
          str([(e.label, e.value) for e in persons(det)]))

    _doc, det = detect(["户名：王铁栓"])
    check("合同：户名 王铁栓", has_person(det, "王铁栓", "账户名称"))

    # ── 正样本 4：并列当事人 / 被告 ──────────────────────────
    _doc, det = detect(["包罗、殷莹：",
                        "你诉绵阳力嘉实业有限公司、杨尚坤一案的起诉状已收到。经"])
    got = {e.value for e in persons(det)}
    check("受理通知书：收件人 包罗、殷莹", {"包罗", "殷莹"} <= got, str(got))
    check("受理通知书：被告 杨尚坤", "杨尚坤" in got, str(got))
    check("受理通知书：当事人标签", all(e.label == "当事人" for e in persons(det)),
          str([(e.label, e.value) for e in persons(det)]))

    _doc, det = detect(["你诉杨尚坤一案的起诉状已收到。经"])
    check("单一被告：诉 X 一案", has_person(det, "杨尚坤", "当事人"),
          str([(e.label, e.value) for e in persons(det)]))

    # ── 负样本：说明文字 / 标签词 / 正文顿号短语 ─────────────
    for text in ("联系人：详见附件", "联系人：见合同", "联系人：办公室",
                 "负责人：法定代表人", "经办人：见附件"):
        _doc, det = detect([text])
        hits = new_rule_hits(det, {"联系人"})
        check(f"负样本 联系人 不误报：{text}", not hits,
              str([(e.label, e.value) for e in hits]))

    for text in ("账户名称：见合同", "账户名称：详见附件", "账户名称：公司",
                 "户名：个人", "账户名称：项目部"):
        _doc, det = detect([text])
        hits = new_rule_hits(det, {"账户名称"})
        check(f"负样本 账户名称 不误报：{text}", not hits,
              str([(e.label, e.value) for e in hits]))

    for blocks in (["甲方、乙方："],
                   ["公司、个人一案"],
                   ["经签字、盖章后生效"],
                   ["文件、合法有效"],
                   ["路面、桥涵(隧道)、绿化环保"],
                   ["经签字、盖章后生效，文件、合法有效"]):
        _doc, det = detect(blocks)
        hits = new_rule_hits(det, {"当事人"})
        check(f"负样本 当事人 不误报：{blocks[0]}", not hits,
              str([(e.label, e.value) for e in hits]))

    # 法定代表人等既有标签：新规则不应把标签词当人名
    _doc, det = detect(["法定代表人：莫俊林", "联系人：详见附件"])
    hits = new_rule_hits(det, {"联系人", "账户名称", "当事人"})
    check("负样本 法定代表人标签不被新规则当人名", not hits,
          str([(e.label, e.value) for e in hits]))
    check("既有 法定代表人 规则仍命中", has_person(det, "莫俊林", "法定代表人"))

    for text in ("代表人：张三", "法定代理人：李四", "甲方：某某公司", "乙方：某某公司"):
        _doc, det = detect([text])
        hits = new_rule_hits(det, {"联系人", "账户名称", "当事人"})
        check(f"负样本 标签词不误报：{text}", not hits,
              str([(e.label, e.value) for e in hits]))

    # ── 发票 joined_regex 本身的负样本（隔离既有同块规则）────────
    rule, spec = rule_spec("cn.invoice_person", "joined_regex")
    detector = JoinedRegexDetector.from_spec(spec)
    doc = make_doc(["开票人：详见附件"])
    ents = detector.detect(doc, rule, DetectContext(strictness="standard"))
    check("负样本 发票跨块规则不吃「详见附件」", not ents,
          str([e.value for e in ents]))

    # ── 替换：姓名被换名、标签保留，值不泄漏 ─────────────────
    doc, det = detect(["开票人：", "马靖"])
    session = pipeline.Session()
    pipeline.decide(det.entities, det.rules, session)
    ent = next(e for e in persons(det) if e.value == "马靖")
    repl = ent.decision.replacement if ent.decision else ""
    check("发票姓名换名：replacement 非空且不等于原值",
          bool(repl) and repl != "马靖", repr(repl))
    check("发票姓名换名：标签块文本未被改动（标签不参与实体）",
          doc.pages[0].blocks[0].text == "开票人：")

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
