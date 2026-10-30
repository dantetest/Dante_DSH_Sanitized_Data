# -*- coding: utf-8 -*-
"""方案可行性原型：实体质量闸门 / 一致性伪值 / USCC/电话。"""
from __future__ import annotations

import random
import re

# ── 1. 拟议实体质量闸门（原型）──
_FLAG_WORDS = ("公章", "签字", "签名", "盖章", "日期", "管理", "按照", "可以",
               "有权", "所在", "详见", "附件", "公司", "银行", "单位")
_ORG_BAD_START = re.compile(r"^[0-9A-Za-z]|^(按照|依法追究|根据|告|完成|建设|证明)")
_ORG_CATEGORIES = {
    "court": ("人民法院", "人民检察院", "法院", "检察院"),
    "bank": ("银行", "支行", "分行", "信用社"),
    "company": ("公司", "集团", "厂", "店", "合伙企业"),
    "school": ("中学", "小学", "大学", "学院", "学校", "职业技术"),
    "hospital": ("医院", "卫生院", "诊所", "保健院"),
    "firm": ("事务所",),
    "research": ("研究所", "研究院", "中心", "实验室"),
    "gov": ("政府", "委员会", "办公室", "局", "厅", "管委会"),
}


def org_category(v):
    for name, suffixes in _ORG_CATEGORIES.items():
        if any(v.endswith(s) for s in suffixes):
            return name
    return ""


def valid_entity(etype, value, origin="model", confidence=0.8):
    v = (value or "").strip()
    if etype == "PERSON":
        if not (2 <= len(v) <= 4) or not re.fullmatch(r"[\u4e00-\u9fa5]+", v):
            return False, "人名形态不符"
        if any(w in v for w in _FLAG_WORDS) or v in ("管理好", "公章"):
            return False, "公文化/动作词"
        return True, ""
    if etype == "ORG":
        if _ORG_BAD_START.search(v):
            return False, "数字/动词/OCR 前缀开头"
        if any(w in v for w in ("有权", "可以", "按照", "所在", "管理好")):
            return False, "动词/公文片段"
        if not org_category(v):
            return False, "无机构类别后缀"
        return True, ""
    return True, ""


print("=== 质量闸门原型（D07 实测值）===")
cases = [
    ("ORG", "方有权在甲方所在地人民法院"),
    ("PERSON", "管理好"),
    ("PERSON", "公章"),
    ("ORG", "1甲方可以银行"),
    ("ORG", "按照银行"),
    ("ORG", "云基智慧工程股份有限公司"),
    ("ORG", "四川中骏天华工程勘察设计有限公司"),
    ("ORG", "中国建设银行股份有限公司成都玉林支行"),
    ("ORG", "盐源县人民法院"),
    ("ORG", "最高人民法院"),
    ("ORG", "北京市第一中级人民法院"),
]
for et, v in cases:
    ok, why = valid_entity(et, v)
    print(f"  {'PASS' if ok else 'DROP'}  {et:6s} {v}  {why}")

# ── 2. 一致性伪身份证 ──
def id_check(body17):
    w = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    return "10X98765432"[sum(int(c) * x for c, x in zip(body17, w)) % 11]


def gen_id(orig, age=None, gender=None, rng=None):
    rng = rng or random.Random()
    region = re.sub(r"\D", "", orig)[:6]
    year = 2026 - age if age else rng.randint(1950, 2005)
    month = rng.randint(1, 10) if age else rng.randint(1, 12)
    day = rng.randint(1, 28)
    seq = rng.randint(1, 999)
    if gender == "男" and seq % 2 == 0:
        seq += 1 if seq < 999 else -1
    if gender == "女" and seq % 2 == 1:
        seq += 1 if seq < 999 else -1
    body17 = f"{region}{year:04d}{month:02d}{day:02d}{seq:03d}"
    return body17 + id_check(body17)


rng = random.Random(7)
ok = 0
for _ in range(500):
    fake = gen_id("310110195803127747", age=67, gender="女", rng=rng)
    ok += (id_check(fake[:17]) == fake[-1] and 2026 - int(fake[6:10]) == 67
           and int(fake[16]) % 2 == 0)
print("\n=== 一致性身份证原型 ===")
print("  500 次全部校验位/年龄67/女性:", ok == 500)
print("  样例:", gen_id("310110195803127747", age=67, gender="女", rng=rng))

# ── 3. USCC 保留地区码 ──
USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
USCC_W = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def uscc_check(body17):
    total = sum(USCC_CHARS.index(c) * w for c, w in zip(body17, USCC_W))
    return USCC_CHARS[(31 - total % 31) % 31]


def gen_uscc(orig, rng):
    text = re.sub(r"\s", "", orig).upper()
    if len(text) != 18:
        return text
    body17 = text[:8] + "".join(rng.choice(USCC_CHARS) for _ in range(9))
    if body17 == text[:17]:
        body17 = body17[:8] + "A" + body17[9:]
    return body17 + uscc_check(body17)


orig_uscc = "91440300741243026T"
ok = 0
for _ in range(500):
    fake = gen_uscc(orig_uscc, rng)
    ok += (len(fake) == 18 and uscc_check(fake[:17]) == fake[-1]
           and fake[2:8] == orig_uscc[2:8])
print("\n=== USCC 保留地区码原型 ===")
print("  500 次结构/校验/地区码一致:", ok == 500)
print("  样例:", gen_uscc(orig_uscc, rng))

# ── 4. 固话保留区号 ──
print("\n=== 固话保留区号原型 ===")
orig = "028-85033400"
area = re.match(r"^(\d{3,4})", re.sub(r"\D", "", orig)).group(1)
kept = f"{area}-{''.join(rng.choice('0123456789') for _ in range(8))}"
print("  orig:", orig, "→ fake:", kept, "| 区号保持:", kept.startswith(area))
