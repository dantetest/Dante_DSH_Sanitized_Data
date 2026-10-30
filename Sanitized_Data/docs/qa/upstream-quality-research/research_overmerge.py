# -*- coding: utf-8 -*-
"""研究：量化近似实体归并的过合并风险（只看已有映射库）。"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(r"D:\DSH_DESKTOP_WORKSPACE")
os.environ["YINZHAN_DATA_DIR"] = str(BASE / "Sanitized_Data" / "data")
sys.path.insert(0, str(BASE / "Sanitized_Data" / "app" / "server"))
from yinzhan import vault  # noqa: E402

CATS = [
    ("法院", ("人民法院", "法院")),
    ("检察院", ("人民检察院", "检察院")),
    ("银行", ("银行", "支行", "分行", "信用社")),
    ("公司", ("公司", "集团", "厂", "店", "商行", "合伙企业")),
    ("学校", ("中学", "小学", "大学", "学院", "学校", "幼儿园", "职业技术")),
    ("医院", ("医院", "卫生院", "诊所", "保健院")),
    ("事务所", ("事务所", "律师事务所", "会计师事务所")),
    ("科研", ("研究所", "研究院", "中心", "实验室")),
    ("政府", ("政府", "管委会", "管理局", "委员会", "办公室", "局", "厅",
              "委员会", "镇政府", "乡政府")),
    ("其他", ()),
]


def category(value: str) -> str:
    text = value or ""
    for name, suffixes in CATS:
        for suf in suffixes:
            if text.endswith(suf):
                return name
    return "其他"


def mask(s, keep=2):
    s = str(s or "")
    return s if len(s) <= keep + 2 else s[:keep] + "***" + s[-keep:]


stats = Counter()
groups_total = 0
hetero = []
big = []
jobs_affected = set()
for item in vault.list_vault():
    m = vault.load_mapping(item["job_id"])
    if not m:
        continue
    fake2orig = defaultdict(list)
    for et, table in (m.get("map") or {}).items():
        for orig, fake in table.items():
            fake2orig[(et, fake)].append(orig)
    for (et, fake), origs in fake2orig.items():
        if len(origs) <= 1:
            continue
        groups_total += 1
        jobs_affected.add(item["job_id"])
        cats = Counter(category(o) for o in origs)
        # 排除「其他」后不同类别数
        known = {c for c in cats if c != "其他"}
        if len(known) > 1 or (len(cats) > 1 and "其他" in cats and len(known) >= 1):
            hetero.append((item["job_id"], et, fake, origs, cats))
        if len(origs) >= 5:
            big.append((item["job_id"], et, fake, len(origs), cats))
        if et == "ORG" and len(cats) > 1:
            stats["org_hetero"] += 1
print("同假值多原值组总数:", groups_total)
print("涉及 job 数:", len(jobs_affected))
print("跨类别异质组:", len(hetero))
print("成员>=5 的大组:", len(big))
for jid, et, fake, origs, cats in hetero[:6]:
    print(f"  异质 {jid} {et} fake={mask(fake)} 成员={len(origs)} 类别={dict(cats)}")
for jid, et, fake, n, cats in big[:6]:
    print(f"  大组 {jid} {et} fake={mask(fake)} 成员={n} 类别={dict(cats)}")
print("\n异质组按类别组合:", dict(Counter(tuple(sorted(c)) for _, _, _, _, c in hetero)))
