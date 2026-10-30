# -*- coding: utf-8 -*-
"""P1-1 一致性自测：年龄/性别事实、身份证/固话/USCC、同值标签归一。

全部虚构数据；运行：
    python -X utf8 app/tests/test_consistency.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="yz_consistency_"))
os.environ["YINZHAN_DATA_DIR"] = str(_TMP / "data")
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(_TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import consistency  # noqa: E402
from yinzhan.replacers import surrogate  # noqa: E402
from yinzhan.udm import (  # noqa: E402
    Block, Document, Entity, Page, ReplaceContext, Span,
)

PASS = 0
FAILS: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""), flush=True)
    else:
        FAILS.append((name, detail))
        print(f"  [FAIL] {name}  {detail}", flush=True)


def id_ok(value: str) -> bool:
    if not re.fullmatch(r"\d{17}[\dX]", value):
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_digits = "10X98765432"
    total = sum(int(ch) * w for ch, w in zip(value[:17], weights))
    return check_digits[total % 11] == value[-1]


def uscc_ok(value: str) -> bool:
    charset = "0123456789ABCDEFGHJKLMNPQRTUWXY"
    weights = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
    if not re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{18}", value):
        return False
    total = sum(charset.index(ch) * w for ch, w in zip(value[:17], weights))
    return charset[(31 - total % 31) % 31] == value[-1]


def _entity(etype: str, value: str, block: str = "b1") -> Entity:
    return Entity(id=Entity.new_id(), etype=etype, label=etype, value=value,
                  spans=[Span(block_id=block, start=0, end=len(value))])


def test_facts() -> None:
    print("\n[1] 文档事实提取（年龄/性别/事件日期）", flush=True)
    doc = Document(doc_id="d1", path="x.txt", fmt="text", pages=[
        Page(index=0, width=0, height=0, blocks=[
            Block(id="b1", kind="text", text="姓名：张三  性别：女  年龄：67"),
            Block(id="b2", kind="text", text="入院日期：2025-03-17  诊断：待查"),
        ]),
    ])
    facts = consistency.extract_facts(doc)
    check("提取性别=女", facts.get("gender") == "女", str(facts))
    check("提取年龄=67", facts.get("age") == 67, str(facts))
    check("提取事件日期=2025-03-17", tuple(facts.get("ref_date") or ()) == (2025, 3, 17),
          str(facts))
    empty = consistency.extract_facts(Document(
        doc_id="d2", path="y.txt", fmt="text", pages=[]))
    check("无事实时返回空 dict", empty == {}, str(empty))


def test_label_merge() -> None:
    print("\n[2] 同值标签写法归一（住院号）", flush=True)
    ents = [
        _entity("MEDICAL_RECORD", "住院号：ZY202503170045", "b1"),
        _entity("MEDICAL_RECORD", "住院号 ZY202503170045", "b2"),
        _entity("MEDICAL_RECORD", "住院号：ZY202503170099", "b3"),
        _entity("PERSON", "住院号：ZY202503170045", "b4"),  # 类型不同不合并
    ]
    stats = consistency.merge_labeled_variants(ents)
    check("归并 1 组/1 个重复", stats == {"groups": 1, "merged": 1}, str(stats))
    check("重复实体被移除", len(ents) == 3, str([e.value for e in ents]))
    merged_ent = [e for e in ents if e.etype == "MEDICAL_RECORD"][0]
    check("canonical 保留冒号标签", merged_ent.value == "住院号：ZY202503170045",
          merged_ent.value)
    check("spans 合并两处", len(merged_ent.spans) == 2, str(merged_ent.spans))
    check("warning 不含原值", "ZY202503170045" not in (consistency.merge_warning(stats) or ""))


def test_id_consistency() -> None:
    print("\n[3] 身份证假值：年龄/性别一致 + 校验位合法", flush=True)
    orig = "310110195803127747"
    ref = (2025, 3, 17)   # D12 入院日期；文档年龄 67
    ctx = ReplaceContext(session={}, etype="ID_CARD", seed=7,
                         facts={"age": 67, "gender": "女", "id_count": 1,
                                "ref_date": ref})
    rng = __import__("random").Random(20261030)
    def id_part(fake: str) -> str:
        match = re.search(r"\d{17}[\dX]", fake or "")
        return match.group(0) if match else ""

    def age_at(digits: str) -> int:
        birth = (int(digits[6:10]), int(digits[10:12]), int(digits[12:14]))
        years = ref[0] - birth[0]
        if (ref[1], ref[2]) < (birth[1], birth[2]):
            years -= 1
        return years

    ok = 0
    for _ in range(300):
        fake = surrogate._fake_id(orig, rng, 0, ctx)
        digits = id_part(fake)
        if not digits:
            continue
        if not id_ok(digits):
            continue
        age = age_at(digits)
        gender = "男" if int(digits[16]) % 2 else "女"
        if age == 67 and gender == "女" and digits[:6] == orig[:6]:
            ok += 1
    check("300 次入院日年龄/性别/校验位全部一致", ok == 300, f"{ok}/300")
    # 无 facts 时退回随机，但仍然校验合法
    fake = surrogate._fake_id(orig, rng)
    check("无 facts 时仍生成合法身份证", id_ok(id_part(fake)), fake)


def test_phone_uscc() -> None:
    print("\n[4] 固话区号 / USCC 地区码保留", flush=True)
    rng = __import__("random").Random(7)
    for orig in ("028-85033400", "0755-12345678"):
        fake = surrogate._fake_phone(orig, rng)
        orig_digits = re.sub(r"\D", "", orig)
        fake_digits = re.sub(r"\D", "", fake)
        area = "028" if orig.startswith("028") else "0755"
        check(f"固话保留区号 {area}", fake_digits.startswith(area), fake)
        check(f"固话长度不变 {orig}", len(fake_digits) == len(orig_digits), fake)
    uscc = "91440300741243026T"
    fake = surrogate._fake_uscc(uscc, rng)
    fake_body = re.sub(r"\s", "", fake.body if hasattr(fake, "body") else fake)
    check("USCC 前 8 位保留", fake_body[:8] == uscc[:8], fake)
    check("USCC 校验位合法", uscc_ok(fake_body), fake)
    check("USCC 不等于原值", fake_body != uscc, fake)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("数据隐站 · P1-1 一致性自测", flush=True)
    test_facts()
    test_label_merge()
    test_id_consistency()
    test_phone_uscc()
    print("\n" + "=" * 66)
    if FAILS:
        print(f"  FAIL：{len(FAILS)} 项失败 / {PASS + len(FAILS)} 项")
        for name, detail in FAILS:
            print(f"    - {name}  {detail}")
        return 1
    print(f"  PASS：{PASS} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
