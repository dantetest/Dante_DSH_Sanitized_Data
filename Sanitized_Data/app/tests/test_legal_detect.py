# -*- coding: utf-8 -*-
"""判决书类文本检测回归（跨行案号/地址、标签姓名、带空格金额）。

覆盖 2026-10-20 真实判决书反馈的 8 类漏识别：
1) 委托诉讼代理人（特别授权）：X；2) 法定代表人/法人代表人：X；
3) 审判长/审判员/书记员（含竖排换行）；4) （2026）川XXXX 民再/民初/民监/执 X 号；
5) 跨行案号；6) 住址/住所地（含跨行、含"住XX县XX村"）；7) 千分位为空格的大额金额。

全部使用虚构文本，不写入仓库数据目录。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
ROOT = TEST_DIR.parents[1]
TMP = Path(tempfile.mkdtemp(prefix="yz_legal_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(TMP / "out"))
sys.path.insert(0, str(ROOT / "app" / "server"))

from yinzhan import pipeline, registry  # noqa: E402
from yinzhan.jobs import _load_rules  # noqa: E402
from yinzhan.udm import Block, Document, Page  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))


def make_doc(blocks: list[str]) -> Document:
    doc = Document(doc_id="d_test", path="", fmt="text")
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


def has(det, etype: str, value: str) -> bool:
    return any(e.etype == etype and e.value == value for e in det.entities)


def main() -> int:
    errors = registry.load_plugins()
    check("插件加载无错误", not errors, str(errors[:1]))

    blocks = [
        "民事判决书",
        "（2026）川3423",                       # 跨行案号前半
        "民再1 号",
        "委托诉讼代理人（特别授权）：杨小超，四川某某律师事务所律师。",
        "原审被告：四川某某工程有限公司，住址：成都市天府",   # 跨行地址前半
        "新区华府大道一段1 号2 栋1 单元28 层13 号。",
        "法定代表人：莫小俊，公司总经理。",
        "法人代表人：欧阳小丽，公司经理。",
        "委托诉讼代理人（特别授权）：王小飘，四川某某律师事务所律师。",
        "委托诉讼代理人（特别授权）：邱小巧，四川某某律师事务所律师。",
        "被告陆续支付了930 700.00 元，扣除钢管租金11 250.00 元。",
        "审判长\n李小清",
        "审判员\n戴小宏",
        "审判员\n何\n荐",
        "（2024）川3423 民初2058 号民事判决书",
        "（2025）川3423 执136 号裁定书",
        "经查，杨小书平户籍地四川省，村民，住峨边彝族自治县宜坪村水溪子组3号。",
    ]
    doc, det = detect(blocks)

    check("跨行案号（2026）川3423 民再1 号",
          any(e.etype == "CASE_NO" and "（2026）" in e.value
              and "川3423" in e.value and "民再1" in e.value for e in det.entities),
          str([e.value for e in det.entities if e.etype == "CASE_NO"]))
    check("案号（2024）川3423 民初2058 号", has(det, "CASE_NO", "（2024）川3423 民初2058 号"))
    check("案号（2025）川3423 执136 号", has(det, "CASE_NO", "（2025）川3423 执136 号"))
    check("委托诉讼代理人（特别授权）杨小超", has(det, "PERSON", "杨小超"))
    check("委托诉讼代理人 王小飘", has(det, "PERSON", "王小飘"))
    check("委托诉讼代理人 邱小巧", has(det, "PERSON", "邱小巧"))
    check("法定代表人 莫小俊", has(det, "PERSON", "莫小俊"))
    check("法人代表人 欧阳小丽（复姓）", has(det, "PERSON", "欧阳小丽"))
    check("审判长 李小清", has(det, "PERSON", "李小清"))
    check("审判员 戴小宏", has(det, "PERSON", "戴小宏"))
    check("审判员竖排 何荐", has(det, "PERSON", "何荐"))
    check("住址跨行 成都市天府新区…13 号",
          any(e.etype == "ADDRESS" and "华府大道一段1 号" in e.value
              for e in det.entities))
    check("无标签地址 峨边彝族自治县…3号",
          any(e.etype == "ADDRESS" and "宜坪村水溪子组3号" in e.value
              for e in det.entities))
    check("带空格金额 930 700.00 元", has(det, "AMOUNT", "930 700.00 元"))
    check("带空格金额 11 250.00 元", has(det, "AMOUNT", "11 250.00 元"))

    # 金额/案号换名保形：分隔符、单位、号字保留
    session = pipeline.Session()
    pipeline.decide(det.entities, det.rules, session)
    amount = next(e for e in det.entities if e.value == "930 700.00 元")
    repl = amount.decision.replacement if amount.decision else ""
    check("金额换名保留空格千分位与单位",
          bool(repl) and "元" in repl and " " in repl and repl != amount.value, repr(repl))
    case = next(e for e in det.entities if e.etype == "CASE_NO")
    crepl = case.decision.replacement if case.decision else ""
    check("案号换名保留号字", bool(crepl) and "号" in crepl, repr(crepl))

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
