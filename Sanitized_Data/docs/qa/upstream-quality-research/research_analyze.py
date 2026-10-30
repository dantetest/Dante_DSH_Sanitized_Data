# -*- coding: utf-8 -*-
"""研究：现有映射库质量统计 + 伪值一致性原型（只读分析，不改产品）。"""
from __future__ import annotations

import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(r"D:\DSH_DESKTOP_WORKSPACE")
import os
os.environ["YINZHAN_DATA_DIR"] = str(BASE / "Sanitized_Data" / "data")
sys.path.insert(0, str(BASE / "Sanitized_Data" / "app" / "server"))

from yinzhan import vault  # noqa: E402
from yinzhan.replacers import surrogate as sur  # noqa: E402
from yinzhan.detectors import pipl_validators as pv  # noqa: E402


def mask(s: str, keep: int = 2) -> str:
    s = str(s or "")
    if len(s) <= keep + 2:
        return s[:1] + "*"
    return s[:keep] + "***" + s[-keep:]


# ── 1. 映射库统计 ──
def analyze_vault():
    jobs = []
    for item in vault.list_vault():
        try:
            m = vault.load_mapping(item["job_id"])
        except Exception:
            continue
        if not m or not m.get("map"):
            continue
        jobs.append(m)
    print(f"vault jobs with text map: {len(jobs)}")

    by_type = Counter()
    entries = 0
    fake_orig = defaultdict(set)       # fake -> set(orig) within one job
    conflicts = []                     # (job, etype, fake, n_orig)
    junk = Counter()
    junk_examples = defaultdict(list)
    id_stats = Counter()
    uscc_stats = Counter()
    phone_stats = Counter()
    amount_stats = Counter()

    junk_person_words = ("公章", "签字", "签名", "盖章", "日期", "管理", "按照",
                         "可以", "有权", "所在", "公司", "银行")
    junk_org_starts = ("按照", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0")
    for m in jobs:
        jid = m.get("job_id")
        fake_orig = defaultdict(set)
        for etype, table in (m.get("map") or {}).items():
            for orig, fake in table.items():
                entries += 1
                by_type[etype] += 1
                fake_orig[fake].add(orig)
                val = orig or ""
                if len(val) <= 1:
                    junk["too_short"] += 1
                elif etype == "PERSON":
                    if any(k in val for k in junk_person_words):
                        junk["person_公文词"] += 1
                        junk_examples["person_公文词"].append(mask(val))
                    if not (2 <= len(val) <= 4) and re.fullmatch(r"[\u4e00-\u9fa5]+", val):
                        junk["person_长度异常"] += 1
                elif etype == "ORG":
                    if val.startswith(junk_org_starts):
                        junk["org_数字开头"] += 1
                        junk_examples["org_数字开头"].append(mask(val))
                    if any(k in val for k in ("按照", "可以", "有权", "管理")):
                        junk["org_公文词"] += 1
                        junk_examples["org_公文词"].append(mask(val))
                if etype == "ID_CARD":
                    id_stats["pairs"] += 1
                    o_digits, f_digits = val.replace(" ", ""), (fake or "").replace(" ", "")
                    if re.fullmatch(r"\d{17}[\dXx]", f_digits):
                        id_stats["fake_format_ok"] += 1
                        if pv_verify_id(f_digits):
                            id_stats["fake_checksum_ok"] += 1
                        if o_digits[:6] == f_digits[:6]:
                            id_stats["region_same"] += 1
                        # 性别位：第 17 位奇男偶女
                        def gender(d):
                            return "男" if int(d[16]) % 2 == 1 else "女"
                        if gender(o_digits) == gender(f_digits):
                            id_stats["gender_same"] += 1
                        def birth_year(d):
                            return int(d[6:10])
                        delta = abs(birth_year(f_digits) - birth_year(o_digits))
                        id_stats["birth_year_delta_avg"] += delta
                if etype == "USCC":
                    uscc_stats["pairs"] += 1
                    f = (fake or "").upper()
                    if len(f) == 18:
                        uscc_stats["len18"] += 1
                        if uscc_checksum_ok(f):
                            uscc_stats["checksum_ok"] += 1
                        uscc_stats["region_changed"] += (f[2:8] != (val or "").upper()[2:8])
                if etype == "PHONE":
                    phone_stats["pairs"] += 1
                    od = re.sub(r"\D", "", val)
                    fd = re.sub(r"\D", "", fake or "")
                    if od[:3] == fd[:3]:
                        phone_stats["same_prefix3"] += 1
                if etype == "AMOUNT":
                    amount_stats["pairs"] += 1
                    if re.search(r"\d", val or "") and re.search(r"\d", fake or ""):
                        amount_stats["numeric"] += 1
        for fake, origs in fake_orig.items():
            if len(origs) > 1:
                conflicts.append((jid, fake, sorted(origs)))
    print("\n类型分布:", dict(by_type.most_common()))
    print("条目总数:", entries)
    print("同假值多原值冲突组:", len(conflicts))
    sizes = [len(x[2]) for x in conflicts]
    if sizes:
        print("  组大小分布:", dict(Counter(sizes)))
        for jid, fake, origs in conflicts[:3]:
            print(f"  e.g. job={jid} fake={mask(fake)} -> {len(origs)} origs: "
                  f"{[mask(o) for o in origs[:3]]}")
    print("\n脏实体启发式命中:", dict(junk))
    for k, vals in junk_examples.items():
        print(f"  {k} 例: {vals[:5]}")
    print("\nID_CARD:", dict(id_stats))
    if id_stats["pairs"]:
        print("  birth_year_delta_avg=", round(id_stats["birth_year_delta_avg"] / id_stats["pairs"], 2))
    print("\nUSCC:", dict(uscc_stats))
    print("PHONE 前3位相同:", dict(phone_stats))
    print("AMOUNT:", dict(amount_stats))


def pv_verify_id(value: str) -> bool:
    """GB 11643 校验。"""
    value = value.upper()
    if not re.fullmatch(r"\d{17}[\dX]", value):
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    checks = "10X98765432"
    total = sum(int(ch) * w for ch, w in zip(value[:17], weights))
    return checks[total % 11] == value[-1]


def uscc_checksum_ok(value: str) -> bool:
    if not re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{18}", value):
        return False
    charset = "0123456789ABCDEFGHJKLMNPQRTUWXY"
    weights = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
    total = sum(charset.index(ch) * w for ch, w in zip(value[:17], weights))
    check = charset[(31 - total % 31) % 31]
    return check == value[-1]


# ── 2. 当前伪身份证生成行为统计 ──
def id_generation_stats():
    orig = "310110195803127747"     # D12 原身份证（1958 女）
    rng = random.Random(20261029)
    genders = Counter()
    ages = Counter()
    for _ in range(2000):
        fake = sur._fake_id(orig, rng)
        digest = re.sub(r"\D", "", fake)
        if len(digest) != 18:
            continue
        genders["男" if int(digest[16]) % 2 else "女"] += 1
        ages[2026 - int(digest[6:10])] += 1
    print("\n当前 _fake_id 行为（2000 次，D12 原身份证 1958/女/67 岁）:")
    print("  性别分布:", dict(genders))
    print("  年龄分布 top:", ages.most_common(8), " 覆盖年龄数:", len(ages))
    print("  与文档 67 岁一致比例:", round(ages.get(67, 0) / max(1, sum(ages.values())), 4))
    print("  与文档女性一致比例:", round(genders.get("女", 0) / max(1, sum(genders.values())), 4))


# ── 3. 一致性伪身份证原型 ──
def gen_consistent_id(orig: str, rng: random.Random, age: int | None = None,
                      gender: str | None = None, today=(2026, 10, 29)) -> str:
    """保持前 6 位地区、按文档年龄/性别生成生日与顺序码、校验位合法。"""
    body = re.sub(r"\D", "", orig)[:17]
    region = body[:6]
    y, m, d = today
    if age is not None:
        year = y - age
        # 生日必须在今年今天之前，保证周岁 = age
        month = rng.randint(1, m)
        day = rng.randint(1, d if month == m else 28)
    else:
        year = rng.randint(1950, 2005)
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
    seq = rng.randint(1, 999)
    if gender == "男" and seq % 2 == 0:
        seq = seq + 1 if seq < 999 else seq - 1
    if gender == "女" and seq % 2 == 1:
        seq = seq + 1 if seq < 999 else seq - 1
    body17 = f"{region}{year:04d}{month:02d}{day:02d}{seq:03d}"
    return body17 + sur._id_check_digit(body17)


def id_prototype():
    rng = random.Random(7)
    print("\n一致性伪身份证原型（D12：女 / 67 岁）：")
    for _ in range(5):
        fake = gen_consistent_id("310110195803127747", rng, age=67, gender="女")
        y = int(fake[6:10])
        g = "男" if int(fake[16]) % 2 else "女"
        print(f"  {mask(fake, 3)}  校验={pv_verify_id(fake)} 推算年龄={2026-y} "
              f"性别={g} 地区={fake[:6]}")
    bad = sum(1 for _ in range(500)
              if not (lambda f: pv_verify_id(f) and
                      (2026 - int(f[6:10])) == 67 and int(f[16]) % 2 == 0)(
                          gen_consistent_id("310110195803127747", rng, age=67, gender="女")))
    print("  500 次生成失败率:", bad / 500)


# ── 4. 电话区号一致性 ──
def phone_prototype():
    rng = random.Random(7)
    orig = "028-85033400"
    area = re.match(r"^(\d{3,4})", re.sub(r"\D", "", orig)).group(1)
    # 当前生成器（随机区号）
    cur = [sur._fake_phone(orig, rng) for _ in range(5)]
    # 保留原区号、替换用户号
    kept = [f"{area}-{''.join(rng.choice('0123456789') for _ in range(8))}"
            for _ in range(5)]
    print("\n固话区号一致性（原文 028 / 成都地址）：")
    print("  当前随机区号示例:", [mask(x, 3) for x in cur])
    print("  保留区号示例:", [mask(x, 3) for x in kept])


if __name__ == "__main__":
    analyze_vault()
    id_generation_stats()
    id_prototype()
    phone_prototype()
