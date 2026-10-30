# -*- coding: utf-8 -*-
"""研究补充 v2：归并组真值对照 + D01 AI 视图文本顺序量化。

运行前由调用方设置 YINZHAN_DATA_DIR 到 e2e 测试数据目录。
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

BASE = Path(r"D:\DSH_DESKTOP_WORKSPACE")
sys_path = BASE / "Sanitized_Data" / "app" / "server"
import sys
sys.path.insert(0, str(sys_path))
from yinzhan import vault  # noqa: E402


def mask(s, keep=2):
    s = str(s or "")
    return s if len(s) <= keep + 2 else s[:keep] + "***" + s[-keep:]


JOB = "j_ea8e77e8"   # D07
m = vault.load_mapping(JOB)
print("D07 mapping loaded:", bool(m))
fake2orig: dict[str, list[str]] = defaultdict(list)
for et, table in (m.get("map") or {}).items():
    for orig, fake in table.items():
        fake2orig[fake].append(orig)
truth = (BASE / "ai_view_test" / "ai2ai_20261029" / "truth" / "D07_contract.txt").read_text(
    "utf-8", errors="replace")
print("\n=== D07 同假值多原值组 ===")
for fake, origs in fake2orig.items():
    if len(origs) <= 1:
        continue
    print(f" fake={mask(fake)}  group_size={len(origs)}")
    for i, o in enumerate(origs):
        print(f"   #{i} len={len(o)} in_truth={o in truth} {mask(o)}")

print("\n=== D01 AI 视图顺序 vs 视觉顺序 ===")
import fitz  # noqa: E402

orig_pdf = BASE / "ai_view_test" / "ai2ai_20261029" / "fixtures" / "D01_invoice.pdf"
ai_pdf = BASE / "e2e_restore_20261029" / "sanitized" / "D01_invoice_AI视图.pdf"
m1 = vault.load_mapping("j_b4070b31")
pairs = []
for et, table in (m1.get("map") or {}).items():
    for orig, fake in table.items():
        pairs.append((et, orig, fake))


def span_rect(page, needle):
    rects = page.search_for(needle)
    return rects[0] if rects else None


for tag, path, idx in (("original", orig_pdf, 1), ("aiview", ai_pdf, 2)):
    doc = fitz.open(str(path))
    page = doc[0]
    rows = []
    for et, orig, fake in pairs:
        needle = orig if tag == "original" else fake
        r = span_rect(page, needle)
        if r:
            rows.append((et, needle, r.x0, r.y0))
    visual = sorted(rows, key=lambda x: (round(x[3] / 6), x[2]))
    raw = page.get_text("text") or ""
    raw_order = sorted(rows, key=lambda x: raw.find(x[1]))
    sorted_text = page.get_text("text", sort=True) or ""
    sort_order = sorted(rows, key=lambda x: sorted_text.find(x[1]))
    print(f"\n-- {tag}: values={len(rows)} --")
    print("  visual :", [mask(x[1]) for x in visual[:8]])
    print("  rawtext:", [mask(x[1]) for x in raw_order[:8]])
    print("  sorttxt:", [mask(x[1]) for x in sort_order[:8]])
    # 反序对：视觉顺序相邻但文本顺序相反
    pos_in_raw = {x[1]: i for i, x in enumerate(raw_order)}
    inversions = 0
    for a, b in zip(visual, visual[1:]):
        if pos_in_raw[a[1]] > pos_in_raw[b[1]]:
            inversions += 1
    print(f"  视觉相邻对在 raw 文本中反序: {inversions}/{max(1, len(visual)-1)}")
    doc.close()
