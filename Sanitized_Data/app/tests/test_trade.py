# -*- coding: utf-8 -*-
"""贸易 P0 单测：trade-cn 规则包 + trade_validators 校验器（task-10）。

运行（仓库根或任意目录均可）：

    python -X utf8 app/tests/test_trade.py

覆盖：
1. 校验器正反例（ISO 6346 / AWB mod 7 / 报关单号 / 提单号 / SWIFT / HS /
   发票号 / 合同号 / 原产地证号 / 许可证号 / 封条号），并用独立算法交叉验证；
2. trade-cn.yaml 加载：load_errors 为空、16 条 trade 规则、必须的 rule id 存在；
3. fixtures/trade/*.txt：每份虚构单证的期望规则全部命中，且被检出值的校验器 100% 通过；
   invalid_numbers.txt 期望 0 命中（校验位/结构不合法必须被丢弃）；
4. 运行时生成一张 XLSX（不落仓库，避免二进制夹具）验证表格路径；
5. keep 规则（HS/价格条款/出口管制提示）经真实导出 + verify 链路，不得被判为残留；
6. 回归隔离：5 个既有样例在"有/无 trade-cn"两次检测下类型与数量完全一致、
   trade-cn 新增命中为 0（红线：新规则不得给既有样例制造误报）。

退出码：0 = 全通过；1 = 有失败项。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "app" / "server"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "trade"
sys.path.insert(0, str(SERVER))

from yinzhan import pipeline, registry, rules as rules_mod  # noqa: E402
from yinzhan.detectors import regex_det  # noqa: E402
from yinzhan.ocr import get_ocr  # noqa: E402
from yinzhan.udm import Decision, ParseContext  # noqa: E402

TRADE_PACK = "trade-cn"
# trade-cn P0 自有规则（SWIFT/BIC 由财务域 cn.swift_bic 覆盖，不在本包重复定义）
REQUIRED_RULE_IDS = {
    "trade.export_control_hint",
    "trade.container_no",
    "trade.declaration_no",
    "trade.awb_no",
    "trade.bl_no",
    "trade.license_no",
    "trade.invoice_no",
    "trade.contract_no",
    "trade.origin_cert_no",
    "trade.seal_no",
    "trade.org_en",
    "trade.amount_fx",
    "trade.hs_code",
    "trade.price_term",
    "trade.vessel_voyage",
}
EXPECTED_RULE_COUNT = len(REQUIRED_RULE_IDS)

# 每份 fixture 期望命中的 trade 规则（值在断言时再单独核对）
FIXTURE_EXPECT = {
    "commercial_invoice.txt": {
        "trade.invoice_no", "trade.contract_no", "trade.hs_code",
        "trade.amount_fx", "trade.price_term", "trade.org_en",
    },
    "packing_list.txt": {"trade.container_no", "trade.seal_no", "trade.invoice_no"},
    "bill_of_lading.txt": {
        "trade.bl_no", "trade.container_no", "trade.seal_no",
        "trade.vessel_voyage", "trade.org_en",
    },
    "customs_declaration.txt": {
        "trade.declaration_no", "trade.hs_code", "trade.price_term",
        "trade.license_no", "trade.export_control_hint", "trade.org_en",
    },
    "sales_contract.txt": {
        "trade.contract_no", "trade.org_en", "trade.amount_fx", "trade.price_term",
    },
    "lc_excerpt.txt": {
        "trade.amount_fx", "trade.org_en", "trade.export_control_hint",
    },
    "invalid_numbers.txt": set(),          # 期望 0 命中
}

_FAILS: list[str] = []
_CHECKS = {"pass": 0, "fail": 0}


# ────────────────────────────── 断言工具 ──────────────────────────────


def check(name: str, cond: bool, detail: str = "") -> bool:
    if cond:
        _CHECKS["pass"] += 1
        print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))
        return True
    _CHECKS["fail"] += 1
    _FAILS.append(name)
    print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))
    return False


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ────────────────────────────── 引擎准备 ──────────────────────────────


def setup_engine():
    errors = registry.load_plugins()
    if errors:
        raise SystemExit(f"插件加载失败：{errors}")
    packs = rules_mod.load_packs()
    load_errors = rules_mod.load_errors()
    ctx = ParseContext(ocr=get_ocr(), workdir=tempfile.mkdtemp(prefix="trade_test_"))
    return packs, load_errors, ctx


def trade_rules(packs) -> list:
    return [r for r in packs.get(TRADE_PACK, []) if r.pack == TRADE_PACK]


def detect_file(path: Path, packs, ctx) -> pipeline.Detection:
    parser = registry.find_parser(path)
    if parser is None:
        raise AssertionError(f"没有解析器：{path.name}")
    doc = parser.parse(path, ctx)
    compiled = rules_mod.compile_rules(packs, doc.fmt)
    det = pipeline.detect(doc, compiled, "standard")
    pipeline.decide(det.entities, det.rules, pipeline.Session())
    return det


def trade_hits(det: pipeline.Detection) -> list[tuple[str, str, str]]:
    out = []
    for e in det.entities:
        rule = det.rules.get(e.id)
        if rule is not None and rule.pack == TRADE_PACK:
            out.append((rule.id, e.value, e.etype))
    return out


# ────────────────────────────── 1. 校验器 ──────────────────────────────


def test_validators() -> None:
    section("1. 校验器（正反例 + 独立算法交叉验证）")
    v = regex_det._VALIDATORS
    for name in ("iso6346_container", "awb_no", "customs_decl_no", "bl_no",
                 "hs_code", "trade_invoice_no", "contract_no",
                 "origin_cert_no", "license_no", "seal_no"):
        check(f"校验器已注册：{name}", name in v)
    # SWIFT/BIC 由财务域注册（跨域协同，trade-cn 不重复注册同名校验器）。
    # 财务域用 fin_ 前缀（如 fin_swift_bic），这里兼容两种命名后再测。
    swift_name = next((n for n in ("fin_swift_bic", "swift_bic") if n in v), None)
    check("SWIFT/BIC 校验器已注册（财务域）", swift_name is not None)

    iso = v["iso6346_container"]
    check("ISO 6346 合法（CSQU3054383）", iso("CSQU3054383"))
    check("ISO 6346 非法校验位（CSQU3054384）", not iso("CSQU3054384"))
    check("ISO 6346 非法类别字母（CSQX3054383）", not iso("CSQX3054383"))
    # 独立算法交叉验证：手工按标准权重算一遍
    letters = {}
    n = 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        while n % 11 == 0:
            n += 1
        letters[ch] = n
        n += 1
    total = sum((letters[c] if c.isalpha() else int(c)) * (2 ** i)
                for i, c in enumerate("CSQU3054383"[:10]))
    expected = total % 11 if total % 11 != 10 else 0
    check("ISO 6346 算法交叉（CSQU3054383 校验位=3）",
          expected == 3 and int("CSQU3054383"[10]) == expected,
          f"sum={total} rem={expected}")

    awb = v["awb_no"]
    check("AWB 合法（99912345676）", awb("99912345676"))
    check("AWB 非法校验位（99912345677）", not awb("99912345677"))
    check("AWB 位数不足（1234567）", not awb("1234567"))
    check("AWB 算法交叉", int("9991234567") % 7 == 6 and int("99912345676"[10]) == 6)

    decl = v["customs_decl_no"]
    check("报关单号合法（224420250012345678）", decl("224420250012345678"))
    check("报关单号年份早于 1980（224418000012345678）", not decl("224418000012345678"))
    check("报关单号进出口标志非法（224420252012345678）",
          not decl("224420252012345678"))
    check("报关单号位数不足", not decl("2244202500123456"))

    bl = v["bl_no"]
    check("提单号合法（COSU6301234567）", bl("COSU6301234567"))
    check("提单号排除集装箱形态（CSQU3054383）", not bl("CSQU3054383"))
    check("提单号过短", not bl("COSU123"))

    swift = v[swift_name] if swift_name else (lambda _v: False)
    check("SWIFT 8 位合法（BOFAUS3N，财务域校验器）", swift("BOFAUS3N"))
    check("SWIFT 11 位合法（BKCHCNBJXXX）", swift("BKCHCNBJXXX"))
    check("SWIFT 9 位非法", not swift("BOFAUS3NX"))
    check("SWIFT 国别码非字母非法", not swift("BOFA1234"))

    hs = v["hs_code"]
    check("HS 10 位合法（8471300000）", hs("8471300000"))
    check("HS 8 位合法（84713000）", hs("84713000"))
    check("HS 6 位合法（847130）", hs("847130"))
    check("HS 7 位非法", not hs("8471300"))
    check("HS 章非法（9971300000）", not hs("9971300000"))
    check("HS 含字母非法", not hs("84AB300000"))

    inv = v["trade_invoice_no"]
    check("发票号合法（INV-2026-0001）", inv("INV-2026-0001"))
    check("发票号拒绝纯日期", not inv("2026-01-01"))
    check("发票号拒绝纯数字", not inv("12345678"))
    check("发票号拒绝 USCC 形态", not inv("91310000MA1FL1XX0G"))
    # 财务域"发票号码"校验器与贸易发票号互不覆盖（跨域约定，名称可能带 fin_ 前缀）
    fin_inv_name = next((n for n in ("fin_invoice_no", "invoice_no")
                         if n in v and n != "trade_invoice_no"), None)
    check("财务域发票号码校验器存在且仍为 8 位数字口径",
          fin_inv_name is not None and v[fin_inv_name]("12345678")
          and not v[fin_inv_name]("INV-2026-0001"),
          f"name={fin_inv_name}")

    con = v["contract_no"]
    check("合同号合法（HX-2026-001）", con("HX-2026-001"))
    check("合同号长度过短", not con("HX-1"))
    check("合同号拒绝纯日期", not con("2025-12-31"))

    origin = v["origin_cert_no"]
    check("原产地证号合法（CO-2026123456）", origin("CO-2026123456"))
    check("原产地证号过短", not origin("CO-12"))

    lic = v["license_no"]
    check("许可证号合法（2026-EX123456）", lic("2026-EX123456"))
    check("许可证号拒绝 USCC 形态（31310000E12345678X）",
          not lic("31310000E12345678X"))
    check("许可证号拒绝身份证形态（330203199001011234）",
          not lic("330203199001011234"))
    check("许可证号要求分隔符", not lic("2026EX123456"))

    seal = v["seal_no"]
    check("封条号合法（CN1234567）", seal("CN1234567"))
    check("封条号排除集装箱形态（CSQU3054383）", not seal("CSQU3054383"))

    # 既有校验器冒烟：Lead 修过 USCC 后应可引用
    check("USCC 校验器可用（91310000MA1FL1XX0G）",
          "uscc_checksum" in v and v["uscc_checksum"]("91310000MA1FL1XX0G"))


# ────────────────────────────── 2. 规则包加载 ──────────────────────────────


def test_rules_load(packs, load_errors) -> None:
    section("2. trade-cn 规则包加载")
    check("规则包加载无错误", not load_errors, str(load_errors))
    check("trade-cn 包存在", TRADE_PACK in packs)
    own = trade_rules(packs)
    ids = {r.id for r in own}
    check(f"trade 规则条数 = {EXPECTED_RULE_COUNT}", len(own) == EXPECTED_RULE_COUNT,
          f"实际 {len(own)}")
    missing = REQUIRED_RULE_IDS - ids
    check("必须的 rule id 全部存在", not missing, f"缺少 {sorted(missing)}")
    extra = ids - REQUIRED_RULE_IDS
    check("无未登记的 rule id（防漏测）", not extra, f"多出 {sorted(extra)}")
    for r in own:
        check(f"规则 {r.id} 字段合法",
              bool(r.entity) and bool(r.label) and r.level in ("L1", "L2", "L3")
              and r.scope and r.replace.get("strategy") in
              ("surrogate", "mask", "black", "drop", "keep"),
              f"entity={r.entity} level={r.level} strategy={r.replace.get('strategy')}")
    # 格式编译：trade 规则在文本/Office/图片格式可用，mesh 不适用
    for fmt in ("pdf", "docx", "xlsx", "text", "image", "tiff"):
        compiled = rules_mod.compile_rules(packs, fmt)
        got = [r for r in compiled if r.pack == TRADE_PACK]
        check(f"{fmt} 格式包含 {EXPECTED_RULE_COUNT} 条 trade 规则",
              len(got) == EXPECTED_RULE_COUNT, f"实际 {len(got)}")


# ────────────────────────────── 3. fixtures 命中 ──────────────────────────────


def test_fixtures(packs, ctx) -> None:
    section("3. fixtures 命中 + 校验合法性（100%）")
    parsed = 0
    for path in sorted(FIXTURES.glob("*.txt")):
        if path.name not in FIXTURE_EXPECT:
            continue            # 新增 fixture 未登记时跳过（避免误判）
        parsed += 1
        det = detect_file(path, packs, ctx)
        hits = trade_hits(det)
        hit_ids = {rid for rid, _value, _etype in hits}
        expected = FIXTURE_EXPECT[path.name]
        check(f"{path.name} fixture 可解析并完成检测", det is not None)
        missing = expected - hit_ids
        check(f"{path.name} 期望规则全部命中", not missing, f"缺少 {sorted(missing)}")
        if not expected:                       # invalid_numbers.txt：断言 0 命中
            check(f"{path.name} 非法号码被校验器丢弃（0 命中）",
                  len(hits) == 0, f"实际命中 {hits}")
        # 检出的每个值必须通过对应规则的 validate（若有）
        invalid = []
        for rid, value, _etype in hits:
            rule = next((r for r in packs[TRADE_PACK] if r.id == rid), None)
            if rule is None:
                continue
            for spec in rule.detect:
                name = str(spec.get("validate") or "none")
                if name != "none" and name in regex_det._VALIDATORS:
                    if not regex_det._VALIDATORS[name](value):
                        invalid.append((rid, value, name))
        check(f"{path.name} 检出值校验合法率 100%", not invalid, f"非法 {invalid}")
    # SWIFT/BIC 跨域协同：trade-cn 不重复建规则，lc_excerpt 中的 BOFAUS3N 应由
    # 财务域 cn.swift_bic 检出（其校验器同样来自 finance_validators）
    lc_path = FIXTURES / "lc_excerpt.txt"
    if lc_path.is_file():
        det = detect_file(lc_path, packs, ctx)
        swift_vals = [(e.value, e.etype) for e in det.entities
                      if e.etype in ("SWIFT", "SWIFT_BIC")]
        check("lc_excerpt SWIFT 被检出（财务域 cn.swift_bic）",
              any(value == "BOFAUS3N" for value, _t in swift_vals),
              f"实际 {swift_vals}")
    check("fixtures 覆盖数 >= 7", parsed >= 7, f"实际 {parsed}")


# ────────────────────────────── 4. XLSX 表格路径 ──────────────────────────────


def test_xlsx_table(packs, ctx) -> None:
    section("4. XLSX 表格路径（运行时生成，不落仓库二进制）")
    try:
        from openpyxl import Workbook
    except Exception as exc:                        # pragma: no cover
        check("openpyxl 可用", False, str(exc))
        return
    tmp = Path(tempfile.mkdtemp(prefix="trade_xlsx_")) / "invoice_lines.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "发票明细"
    rows = [
        ["商业发票明细"],
        ["发票号：INV-2026-0001"],
        ["合同号：HX-2026-001"],
        ["HS编码：8471300000"],
        ["总价：USD 128,000.00"],
        ["价格条款：CIF 汉堡"],
        ["装箱：集装箱号：CSQU3054383"],
    ]
    for row in rows:
        ws.append(row)
    wb.save(str(tmp))
    det = detect_file(tmp, packs, ctx)
    hit_ids = {rid for rid, _v, _e in trade_hits(det)}
    expected = {"trade.invoice_no", "trade.contract_no", "trade.hs_code",
                "trade.amount_fx", "trade.price_term", "trade.container_no"}
    check("XLSX 中贸易字段命中", expected <= hit_ids,
          f"缺少 {sorted(expected - hit_ids)}，命中 {sorted(hit_ids)}")


# ────────────────────────────── 5. 既有样例回归隔离 ──────────────────────────────


def test_existing_samples(packs, ctx) -> None:
    section("5. 既有 5 样例：有/无 trade-cn 检测结果一致（红线）")
    samples = sorted((ROOT / "samples").iterdir())
    samples = [p for p in samples if p.is_file()]
    check("样例目录有 5 个文件", len(samples) == 5, f"实际 {len(samples)}")

    baseline_required = {}
    baseline_json = Path(__file__).resolve().parent / "baseline" / "baseline.json"
    if baseline_json.is_file():
        import json
        try:
            data = json.loads(baseline_json.read_text("utf-8"))
            baseline_required = (data.get("required", {}).get("samples") or {})
        except Exception:
            baseline_required = {}

    for path in samples:
        parser = registry.find_parser(path)
        doc = parser.parse(path, ctx)
        all_rules = rules_mod.compile_rules(packs, doc.fmt)
        no_trade = [r for r in all_rules if r.pack != TRADE_PACK]
        d_base = pipeline.detect(doc, no_trade, "standard")
        d_all = pipeline.detect(doc, all_rules, "standard")
        base_types: dict[str, int] = {}
        for e in d_base.entities:
            base_types[e.label] = base_types.get(e.label, 0) + 1
        all_types: dict[str, int] = {}
        for e in d_all.entities:
            all_types[e.label] = all_types.get(e.label, 0) + 1
        missing_types = {k: base_types[k] for k in base_types if k not in all_types}
        reduced = {k: (base_types[k], all_types[k]) for k in base_types
                   if k in all_types and all_types[k] < base_types[k]}
        added = {k: all_types[k] for k in all_types if k not in base_types}
        trade_ents = trade_hits(d_all)
        check(f"{path.name} 既有类型不减少", not missing_types and not reduced,
              f"missing={missing_types} reduced={reduced}")
        check(f"{path.name} trade-cn 无新增命中（防误报）", not trade_ents,
              f"误报 {trade_ents}")
        check(f"{path.name} 新增类型为 0", not added, f"新增 {added}")
        # 软核对：基线 required_types 仍满足（基线由 qa 维护，缺失则跳过）
        req = (baseline_required.get(path.name) or {}).get("required_types") or {}
        unmet = {k: v for k, v in req.items() if all_types.get(k, 0) < v}
        check(f"{path.name} 基线 required_types 仍满足", not unmet,
              f"未满足 {unmet}" if unmet else "ok（无基线或全满足）")


# ────────────────────────────── 6. keep 规则导出 ──────────────────────────────


def test_keep_export(packs, ctx) -> None:
    """keep 实体（HS/价格条款/管制提示）导出后不得被 verify 当残留（Lead 已修 pipeline）。

    这是本批最容易回归的一条：若 verify 重新把 keep 实体计入残留，含 HS 编码的
    贸易文件会直接导出失败。测试用真实导出 + 复验链路守住它。
    """
    section("6. keep 规则导出/复验（verify 必须跳过 keep）")
    path = FIXTURES / "customs_declaration.txt"
    parser = registry.find_parser(path)
    doc = parser.parse(path, ctx)
    det = pipeline.detect(doc, rules_mod.compile_rules(packs, doc.fmt), "standard")
    pipeline.decide(det.entities, det.rules, pipeline.Session())
    kept = [e for e in det.entities if e.decision and e.decision.strategy == "keep"]
    check("fixture 含 keep 实体", bool(kept), f"{sorted({e.etype for e in kept})}")

    tmp = Path(tempfile.mkdtemp(prefix="trade_export_"))
    for mode in ("ai", "human"):
        ents = det.entities
        if mode == "human":          # 复刻 jobs._export_to 的 human 策略：全部涂黑
            for e in ents:
                e.decision = Decision(strategy="black", black=True, replacement=None)
        out = tmp / f"decl_{mode}.txt"
        res = pipeline.export(doc, parser, ents, out, {"mode": mode})
        check(f"{mode} 导出成功", Path(res.path).is_file())
        try:
            info = pipeline.verify(Path(res.path), ents, parser, ctx)
            check(f"{mode} 导出复验通过（keep 不算残留）",
                  info.get("verified") is True, str(info))
            check(f"{mode} 残留=0", info.get("residual") == 0,
                  f"residual={info.get('residual')}")
        except Exception as exc:      # VerifyError 也应算失败并打印
            check(f"{mode} 导出复验通过（keep 不算残留）", False,
                  f"{type(exc).__name__}: {exc}")
        out_text = Path(res.path).read_text("utf-8", errors="ignore")
        if mode == "ai":
            check("AI 版保留 HS 编码（keep 语义）", "8471300000" in out_text)
            check("AI 版已替换英文公司名",
                  "ABC INTERNATIONAL TRADING" not in out_text)
        else:
            check("人看版已涂黑 HS 编码", "8471300000" not in out_text)


# ────────────────────────────── 主流程 ──────────────────────────────


def main() -> int:
    print("=" * 68)
    print("  贸易 P0 单测：trade-cn 规则包 + 校验器")
    print("=" * 68)
    try:
        packs, load_errors, ctx = setup_engine()
        test_validators()
        test_rules_load(packs, load_errors)
        test_fixtures(packs, ctx)
        test_xlsx_table(packs, ctx)
        test_keep_export(packs, ctx)
        test_existing_samples(packs, ctx)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("\n[FAIL] 测试执行异常")
        return 1

    total = _CHECKS["pass"] + _CHECKS["fail"]
    print("\n" + "=" * 68)
    if _FAILS:
        print(f"  失败 {len(_FAILS)}/{total} 项：")
        for name in _FAILS:
            print(f"    - {name}")
        print("=" * 68)
        return 1
    print(f"  全部通过：{total} 项检查全绿（trade 规则 {EXPECTED_RULE_COUNT} 条）")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
