"""端到端自检：样例文件 → 解析 → 检测 → 导出 → 复检。

用法（在 app/server 下）：python ../tests/e2e.py
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER))

from yinzhan import pipeline, registry                      # noqa: E402
from yinzhan.config import OUTPUT_DIR, WORK_DIR              # noqa: E402
from yinzhan.jobs import _load_rules                         # noqa: E402
from yinzhan.ocr import get_ocr                              # noqa: E402
from yinzhan.udm import ParseContext, VerifyError            # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def run_one(path: Path) -> dict:
    parser = registry.find_parser(path)
    if parser is None:
        return {"file": path.name, "ok": False, "err": "没有匹配的解析器"}
    ctx = ParseContext(ocr=get_ocr(), workdir=str(WORK_DIR))
    t0 = time.time()
    try:
        doc = parser.parse(path, ctx)
        t_parse = time.time() - t0
    except Exception as exc:
        return {"file": path.name, "ok": False, "err": f"解析失败 {exc}"}

    if not doc.pages:
        return {"file": path.name, "ok": False, "err": "没有页面"}

    rules, pack, _ = _load_rules(doc.fmt)
    det = pipeline.detect(doc, rules, "standard")
    pipeline.decide(det.entities, det.rules, pipeline.Session())

    info = {
        "file": path.name, "fmt": doc.fmt, "pages": len(doc.pages),
        "blocks": sum(len(p.blocks) for p in doc.pages),
        "entities": len(det.entities),
        "types": {}, "parse_ms": int(t_parse * 1000),
        "detect_ms": det.elapsed_ms, "ok": True, "err": "",
        "exports": {}, "warnings": list(doc.warnings) + list(det.warnings),
    }
    for e in det.entities:
        info["types"][e.label] = info["types"].get(e.label, 0) + 1

    for mode in ("ai", "human"):
        out = OUTPUT_DIR / f"_test_{path.stem}_{mode}{path.suffix}"
        try:
            if mode == "human":
                from yinzhan.udm import Decision
                for e in det.entities:
                    e.decision = Decision(strategy="black", black=True)
            res = pipeline.export(doc, parser, det.entities, out, {"mode": mode})
            try:
                ver = pipeline.verify(out, det.entities, parser, ctx)
                info["exports"][mode] = {
                    "ok": True, "residual": ver.get("residual"),
                    "size": res.size,
                }
            except VerifyError as ve:
                info["exports"][mode] = {"ok": False, "err": str(ve),
                                         "size": res.size}
        except Exception as exc:
            info["exports"][mode] = {"ok": False, "err": f"{type(exc).__name__}: {exc}"}
    return info


def main() -> int:
    print()
    print("  数据隐站 · 端到端自检")
    print("  " + "─" * 58)
    errs = registry.load_plugins()
    if errs:
        for e in errs:
            print(f"  {RED}插件加载失败{RESET} {e.splitlines()[0]}")
    ocr = get_ocr()
    print(f"  OCR  {'就绪' if ocr.available else '不可用 ' + str(ocr.error)}")
    print(f"  解析器 {sorted({p.format_id for p in registry.all_parsers().values() if isinstance(p, registry.Parser)})}")
    print()

    files = sorted([p for p in SAMPLES.iterdir() if p.is_file()])
    if not files:
        print(f"  {YELLOW}没有样例，先跑 make_samples.py{RESET}")
        return 1

    results = []
    for f in files:
        print(f"  ▸ {f.name}")
        r = run_one(f)
        results.append(r)
        if not r["ok"]:
            print(f"    {RED}失败{RESET} {r['err']}")
            continue
        print(f"    {DIM}{r['fmt']} · {r['pages']} 页 · {r['blocks']} 块 · "
              f"识别 {r['entities']} 处 · 解析 {r['parse_ms']}ms · 检测 {r['detect_ms']}ms{RESET}")
        if r["types"]:
            print(f"    {DIM}" + "  ".join(f"{k}×{v}" for k, v in r["types"].items()) + RESET)
        for mode, ex in r["exports"].items():
            label = "涂黑" if mode == "human" else "换名"
            if ex.get("ok"):
                print(f"    {GREEN}✓{RESET} {label}导出 {ex['size']}B · 残留 {ex['residual']}")
            else:
                print(f"    {RED}✗{RESET} {label}导出：{ex.get('err')}")
        for w in r.get("warnings", [])[:3]:
            print(f"    {YELLOW}!{RESET} {w}")
        print()

    ok = sum(1 for r in results if r["ok"])
    print("  " + "─" * 58)
    print(f"  汇总：{ok}/{len(results)} 个文件解析成功")
    failed_verify = [r["file"] for r in results
                     if any(not e.get("ok") for e in r.get("exports", {}).values())]
    if failed_verify:
        print(f"  {RED}导出/复检未通过：{', '.join(failed_verify)}{RESET}")
        return 2
    print(f"  {GREEN}全部通过{RESET}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(3)
