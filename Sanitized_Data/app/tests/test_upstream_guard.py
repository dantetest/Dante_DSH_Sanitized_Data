# -*- coding: utf-8 -*-
"""上游质量护栏自测：实体质量闸门（shadow/enforce）+ 映射一致性检查。

锁定的安全语义：
  * 默认 shadow 只统计、不改实体；
  * enforce 只丢高精度 PERSON/ORG 垃圾，身份证/账号等关键类型永不自动丢；
  * 跨机构类别（法院/学校/公司）共享同一假值 → 映射检查必须报错；
  * 纯行政区 core 不能作为机构简称（防止“盐源县法院/学校/公司”被并）；
  * 报告/告警只含类型/数量/掩码，不含原值或完整假值。

全部虚构数据；运行：
    python -X utf8 app/tests/test_upstream_guard.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="yz_upstream_guard_"))
os.environ["YINZHAN_DATA_DIR"] = str(_TMP / "data")
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(_TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import pipeline, quality  # noqa: E402
from yinzhan.udm import Decision, Entity, Span  # noqa: E402

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


def _ent(etype: str, value: str, replacement: str | None = None) -> Entity:
    ent = Entity(id=Entity.new_id(), etype=etype, label=etype, value=value,
                 spans=[Span(block_id="b0", start=0, end=len(value))])
    if replacement is not None:
        ent.decision = Decision(strategy="surrogate", replacement=replacement)
    return ent


# D07 实测的 5 条垃圾 + 真实实体（虚构/公开名称，不取自真实文件）
JUNK = [("PERSON", "管理好"), ("PERSON", "公章"),
        ("ORG", "按照银行"), ("ORG", "1甲方可以银行"),
        ("ORG", "方有权在甲方所在地人民法院"),
        # D08 实测：句子片段被识别成机构名
        ("ORG", "规范化的专业律师事务所"),
        ("ORG", "地有管辖权人民法院")]
REAL = [("ORG", "云基智慧工程股份有限公司"),
        ("ORG", "四川中骏天华工程勘察设计有限公司"),
        ("ORG", "中国建设银行股份有限公司成都玉林支行"),
        ("ORG", "盐源县人民法院"), ("ORG", "最高人民法院"),
        ("ORG", "北京市第一中级人民法院"),
        # 高精度误杀红线：真实公司名里可能含“管理”、数字/英文开头
        ("ORG", "四川华信管理咨询有限公司"),
        ("ORG", "3M中国有限公司"),
        ("ORG", "ABB（中国）有限公司")]
PROTECTED = [("ID_CARD", "110101199001011234"), ("PHONE", "13800138000"),
             ("USCC", "91110000MA00000001"), ("ACCOUNT", "6222020000000000000")]


def test_config_defaults() -> None:
    print("\n[0] 默认开关 / 可回退开关", flush=True)
    old_q = os.environ.pop("YINZHAN_ENTITY_QUALITY", None)
    old_g = os.environ.pop("YINZHAN_MAPPING_GUARD", None)
    try:
        check("质量闸门默认 enforce", quality.quality_mode() == "enforce",
              quality.quality_mode())
        check("映射护栏默认 warn", quality.guard_mode() == "warn",
              quality.guard_mode())
        os.environ["YINZHAN_ENTITY_QUALITY"] = "shadow"
        check("质量闸门可回退 shadow", quality.quality_mode() == "shadow")
        os.environ["YINZHAN_ENTITY_QUALITY"] = "off"
        check("质量闸门可关闭", quality.quality_mode() == "off")
        os.environ["YINZHAN_MAPPING_GUARD"] = "block"
        check("映射护栏可切 block", quality.guard_mode() == "block")
    finally:
        os.environ.pop("YINZHAN_ENTITY_QUALITY", None)
        os.environ.pop("YINZHAN_MAPPING_GUARD", None)
        if old_q is not None:
            os.environ["YINZHAN_ENTITY_QUALITY"] = old_q
        if old_g is not None:
            os.environ["YINZHAN_MAPPING_GUARD"] = old_g


def test_classify() -> None:
    print("\n[1] 实体质量分类（高精度垃圾）", flush=True)
    for etype, value in JUNK:
        verdict = quality.classify_entity(etype, value)
        check(f"垃圾命中：{etype}", verdict["drop"], str(verdict))
    for etype, value in REAL:
        verdict = quality.classify_entity(etype, value)
        check(f"真实实体不误杀：{etype}", not verdict["drop"], str(verdict))
    for etype, value in PROTECTED:
        verdict = quality.classify_entity(etype, value)
        check(f"隐私关键类型不参与：{etype}", not verdict["drop"], str(verdict))


def test_modes() -> None:
    print("\n[2] shadow 不改实体 / enforce 只丢垃圾", flush=True)
    old = os.environ.get("YINZHAN_ENTITY_QUALITY")
    try:
        ents = [_ent(t, v) for t, v in JUNK + REAL]
        os.environ["YINZHAN_ENTITY_QUALITY"] = "shadow"
        report = quality.apply_quality_gate(ents)
        check(f"shadow：报告计数 {len(JUNK)}", report["would_drop_total"] == len(JUNK), str(report))
        check("shadow：实体状态完全不变",
              all(e.status == "accepted" for e in ents), str([e.status for e in ents]))
        warning = quality.quality_report_warning(report) or ""
        check("shadow：warning 不含原值/垃圾值",
              all(v not in warning for _, v in JUNK), warning)

        ents2 = [_ent(t, v) for t, v in JUNK + REAL + PROTECTED]
        os.environ["YINZHAN_ENTITY_QUALITY"] = "enforce"
        report2 = quality.apply_quality_gate(ents2)
        check(f"enforce：丢弃 {len(JUNK)} 个", report2["dropped_total"] == len(JUNK), str(report2))
        dropped = [e for e in ents2 if e.status == "rejected"]
        kept = [e for e in ents2 if e.status != "rejected"]
        check("enforce：只丢垃圾值",
              {e.value for e in dropped} == {v for _, v in JUNK},
              str([e.value for e in dropped]))
        check("enforce：真实实体/关键类型全保留",
              len(kept) == len(REAL) + len(PROTECTED),
              str([e.value for e in kept]))
    finally:
        if old is None:
            os.environ.pop("YINZHAN_ENTITY_QUALITY", None)
        else:
            os.environ["YINZHAN_ENTITY_QUALITY"] = old


def test_mapping_guard() -> None:
    print("\n[3] 映射一致性检查（跨类别/超大簇）", flush=True)
    fake = "知行星泰公司"
    mapping = {
        "ORG": {
            "盐源县人民法院": fake,
            "盐源县职业技术中学": fake,
            "四川远亚建设工程有限公司": fake,
            "云基智慧工程股份有限公司": "云基公司",
        }
    }
    report = quality.check_mapping_guard_from_map(mapping)
    check("检测到跨类别归并", not report["ok"]
          and report["counts"]["hetero_category"] >= 1, str(report))
    blob = json.dumps(report, ensure_ascii=False)
    check("报告不含原值", all(v not in blob for v in mapping["ORG"]), blob[:160])
    check("报告含掩码假值", "fake_masked" in blob and fake not in blob, blob[:160])

    ok = quality.check_mapping_guard_from_map(
        {"ORG": {"云基智慧工程股份有限公司": "云基公司",
                 "云基公司": "云基公司"}})
    check("同类别/同一实体不误报", ok["ok"], str(ok))

    ents = [_ent("ORG", "盐源县人民法院", fake),
            _ent("ORG", "盐源县职业技术中学", fake),
            _ent("ORG", "云基智慧工程股份有限公司", "云基公司")]
    report2 = quality.check_mapping_guard(ents)
    check("从实体决策检查也命中异质簇", not report2["ok"], str(report2))


def test_export_block() -> None:
    """映射护栏 block 模式必须在 AI 导出前阻断（fail-closed）。"""
    print("\n[5] 导出前阻断（YINZHAN_MAPPING_GUARD=block）", flush=True)
    from yinzhan import jobs as jobs_mod
    from yinzhan.udm import YinzhanError

    job = jobs_mod.Job(
        id="j_guard_block_test", name="guard.txt",
        path=str(_TMP / "guard.txt"), fmt="txt", stage="ready")
    job.doc = object()
    job.entities = []
    jobs_mod.MANAGER._jobs[job.id] = job
    old_guard = os.environ.get("YINZHAN_MAPPING_GUARD")
    orig_check = quality.check_mapping_guard
    quality.check_mapping_guard = lambda ents: {
        "ok": False, "issues": [], "mode": "block",
        "counts": {"hetero_category": 1, "oversized_cluster": 0},
    }
    os.environ["YINZHAN_MAPPING_GUARD"] = "block"
    try:
        try:
            jobs_mod.MANAGER.export(job.id, "ai")
            code = ""
        except YinzhanError as exc:
            code = getattr(exc, "code", "")
        check("AI 导出被阻断且错误码正确", code == "mapping_review_required",
              code)
    finally:
        quality.check_mapping_guard = orig_check
        jobs_mod.MANAGER._jobs.pop(job.id, None)
        if old_guard is None:
            os.environ.pop("YINZHAN_MAPPING_GUARD", None)
        else:
            os.environ["YINZHAN_MAPPING_GUARD"] = old_guard


def test_merge_guard() -> None:
    print("\n[4] 归并收敛（类别兼容 / 行政区 core / 簇上限）", flush=True)
    pair = pipeline._consolidate_pair
    norm = pipeline._consolidate_norm
    court = "盐源县人民法院"
    school = "盐源县职业技术中学"
    check("法院 vs 学校：不归并",
          not pair(norm(court), norm(school), "ORG"),
          f"{quality.org_category(court)}/{quality.org_category(school)}")
    check("名校简称仍可归并（云基公司 ↔ 长名）",
          pair(norm("云基公司"), norm("云基智慧工程股份有限公司"), "ORG"))
    check("纯行政区 core 不构成简称",
          quality.admin_only_core("盐源县") and not quality.admin_only_core("盐源县职业"))

    # 6 个相似机构：超过 ORG 簇上限，必须转 review 且不改假值
    ents = []
    for i in range(6):
        ents.append(_ent("ORG", f"成都测试{i + 1}号科技有限公司", f"假城市{i + 1}"))
    session = pipeline.Session()
    stats = pipeline.consolidate_surrogates(ents, {}, session)
    check("超大簇转 review（不自动合并）",
          stats.get("review_groups", 0) >= 1, str(stats))
    check("超大簇成员假值保持原样",
          len({e.decision.replacement for e in ents}) == 6,
          str([e.decision.replacement for e in ents]))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("数据隐站 · 上游质量护栏自测", flush=True)
    test_config_defaults()
    test_classify()
    test_modes()
    test_mapping_guard()
    test_export_block()
    test_merge_guard()
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
