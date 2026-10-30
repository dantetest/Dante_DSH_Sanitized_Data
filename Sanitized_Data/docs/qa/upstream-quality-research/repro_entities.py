# -*- coding: utf-8 -*-
"""研究：复现 D07/D12 里的「脏实体」来源，并统计现有映射库风险。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BASE = Path(r"D:\DSH_DESKTOP_WORKSPACE")
TMP = BASE / ".tmp_research"
os.environ["YINZHAN_DATA_DIR"] = str(TMP / "data")
os.environ["YINZHAN_OUTPUT_DIR"] = str(TMP / "out")
os.environ["YINZHAN_PAYWALL"] = "0"
os.environ["YINZHAN_DESKTOP"] = "1"
sys.path.insert(0, str(BASE / "Sanitized_Data" / "app" / "server"))

from yinzhan import registry  # noqa: E402
from yinzhan.jobs import MANAGER, _load_rules  # noqa: E402

FIXTURES = BASE / "ai_view_test" / "ai2ai_20261029" / "fixtures"
DOCS = ["D07_contract.pdf", "D12_medical.docx"]

SUSPICIOUS = ("公章", "管理好", "按照银行", "1甲方", "方有权", "青禾", "笃行")


def wait_ready(job_id: str, timeout: int = 900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap = MANAGER.get(job_id).snapshot()
        if snap["stage"] == "ready":
            return snap
        if snap["stage"] in ("error", "cancelled"):
            raise RuntimeError(snap.get("error") or snap["stage"])
        time.sleep(2)
    raise RuntimeError("timeout")


def main():
    registry.load_plugins()
    for name in DOCS:
        src = FIXTURES / name
        print(f"\n=== {name} ===")
        job = MANAGER.create(src, strictness="standard", origin="api")
        wait_ready(job.id)
        ents = job.entities
        print(f"entities={len(ents)} warnings={job.warnings[:4]}")
        for e in ents:
            val = e.value or ""
            if any(k in val for k in SUSPICIOUS) or len(val) <= 2:
                print(f"  [{e.etype:12s}] value={val!r} conf={e.confidence:.2f} "
                      f"origin={e.origin!r} rule={getattr(e,'rule_id',None)!r} "
                      f"label={e.label!r} status={e.status} "
                      f"decision={getattr(e.decision,'strategy',None)!r} "
                      f"spans={len(e.spans)}")
        # 打印所有 ORG/PERSON 便于观察误检
        print("  --- PERSON/ORG 全量 ---")
        for e in ents:
            if e.etype in ("PERSON", "ORG"):
                print(f"    {e.etype:7s} {e.value!r} conf={e.confidence:.2f} "
                      f"origin={e.origin!r}")
        # 归并组：同一假值多原值
        rev = {}
        for e in ents:
            d = e.decision
            if d and d.replacement and d.strategy == "surrogate":
                rev.setdefault(d.replacement, set()).add(e.value)
        for fake, origs in rev.items():
            if len(origs) > 1:
                print(f"  [归并] fake={fake!r} <- {len(origs)} 个原值: "
                      f"{[o for o in list(origs)[:4]]}")


if __name__ == "__main__":
    main()
