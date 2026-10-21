# -*- coding: utf-8 -*-
"""数据隐站 · 全套测试顺序执行器（qa 所有）。

跑什么（按优先级）：
  1. ``regression.py``  —— 红线门禁（硬项失败=禁止提交）；必须第一个跑；
  2. 各功能域自测 —— ``test_barcodes.py`` / ``test_finance.py`` /
     ``test_trade.py`` / ``test_visual.py`` / ``test_legal_detect.py`` /
     ``test_pipl_rules.py`` / ``test_table_column.py`` /
     ``test_pdf_hidden.py`` / ``test_sign_targets.py`` / ``test_office_media.py`` /
     ``test_csv_table.py`` / ``test_pdf_embedded.py`` / ``test_mesh.py`` /
     ``test_tiff.py`` / ``test_surrogate.py`` / ``test_pdf_verify_fast.py`` /
     ``test_id_wholepage.py`` / ``test_warnings_pending.py`` /
     ``test_preview_boxes.py``（存在才跑）。

用法（Windows 控制台必须 -X utf8）：

    python -X utf8 app/tests/run_all.py                  # 全套
    python -X utf8 app/tests/run_all.py --skip domain     # 只跑红线门禁
    python -X utf8 app/tests/run_all.py --only finance,trade
    python -X utf8 app/tests/run_all.py -v               # 透传各测试输出
    python -X utf8 app/tests/run_all.py --list           # 看将执行哪些

约定：
  * 每个子测试用独立系统临时数据目录（YINZHAN_DATA_DIR / YINZHAN_OUTPUT_DIR），
    **不写仓库 data/**；跑完删除临时目录（--keep 可保留排查）；
  * 顺序执行（各测试都会加载 OCR/插件，并行容易抢 CPU 造成误报）；
  * 退出码：0 = 全部通过；1 = 有失败；2 = 执行器自身错误。
  * 域测试是开发者的功能自测，不是红线门禁；红线只看 regression.py。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
PY = sys.executable
GREEN, RED, YELLOW, DIM, RESET = ("\033[32m", "\033[31m", "\033[33m",
                                  "\033[2m", "\033[0m")


def _specs() -> list[dict]:
    """要执行的测试清单（顺序即执行顺序）。"""
    specs = [
        {"key": "regression", "file": TESTS / "regression.py", "timeout": 900,
         "label": "红线门禁（硬：功能/安全/契约）"},
        {"key": "barcodes", "file": TESTS / "test_barcodes.py", "timeout": 600,
         "label": "条码/二维码域自测"},
        {"key": "finance", "file": TESTS / "test_finance.py", "timeout": 600,
         "label": "财务域自测"},
        {"key": "trade", "file": TESTS / "test_trade.py", "timeout": 600,
         "label": "贸易域自测"},
        {"key": "visual", "file": TESTS / "test_visual.py", "timeout": 600,
         "label": "图像域自测"},
        {"key": "legal", "file": TESTS / "test_legal_detect.py", "timeout": 300,
         "label": "判决书类文本检测回归（跨行案号/地址/标签姓名/带空格金额）"},
        {"key": "previewboxes", "file": TESTS / "test_preview_boxes.py", "timeout": 120,
         "label": "预览矩形一致性（OCR 行块按字符比例插值，不整行涂黑）"},
        {"key": "previewparity", "file": TESTS / "test_preview_export_parity.py",
         "timeout": 300, "label": "预览=成品一致性（预览黑框与 human 导出同一套矩形）"},
        {"key": "pipl", "file": TESTS / "test_pipl_rules.py", "timeout": 600,
         "label": "PIPL 补充规则 + XLSX 纯数值单元格自测"},
        {"key": "table", "file": TESTS / "test_table_column.py", "timeout": 600,
         "label": "表格表头列级检测（table-cn）自测"},
        {"key": "pdfhidden", "file": TESTS / "test_pdf_hidden.py", "timeout": 600,
         "label": "PDF 隐藏数据（附件/批注/表单/JS/缩略图）剥离自测"},
        {"key": "signs", "file": TESTS / "test_sign_targets.py", "timeout": 600,
         "label": "P1-A 招牌扫描器（门牌/路牌/店招/单位铭牌）自测"},
        {"key": "officemedia", "file": TESTS / "test_office_media.py", "timeout": 900,
         "label": "P1-B Office 内嵌图自动脱敏/隐藏对象自测"},
        {"key": "csv", "file": TESTS / "test_csv_table.py", "timeout": 600,
         "label": "P1-B CSV/TSV 列语义与多级表头自测"},
        {"key": "pdfembedded", "file": TESTS / "test_pdf_embedded.py", "timeout": 600,
         "label": "P1-B PDF 文本层内嵌图检测/涂黑自测"},
        {"key": "mesh", "file": TESTS / "test_mesh.py", "timeout": 600,
         "label": "P0 PLY 三维完整性（三子格式/结构自检/失败阻断）自测"},
        {"key": "tiff", "file": TESTS / "test_tiff.py", "timeout": 900,
         "label": "正射 TIF 域自测（可选）"},
        {"key": "surrogate", "file": TESTS / "test_surrogate.py", "timeout": 120,
         "label": "R04 假值不保留完整原值 + verify 豁免收紧自测"},
        {"key": "pdfverifyfast", "file": TESTS / "test_pdf_verify_fast.py", "timeout": 600,
         "label": "P2 扫描件 verify 快速复检（零 OCR + 故障注入拦截）自测"},
        {"key": "idwholepage", "file": TESTS / "test_id_wholepage.py", "timeout": 300,
         "label": "P2 整页证件过判收紧（字段级命中抑制整页）自测"},
        {"key": "warnings", "file": TESTS / "test_warnings_pending.py", "timeout": 300,
         "label": "P2/P3 warning 去重 + 异常大码框待确认自测"},
    ]
    return specs


def _tail(text: str, n: int = 40) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(lines[-n:]) if lines else "(无输出)"


def run_all(only: set[str] | None = None, skip: set[str] | None = None,
            verbose: bool = False, keep: bool = False,
            workdir: str = "") -> int:
    todos = []
    for spec in _specs():
        if only and spec["key"] not in only:
            continue
        if skip and spec["key"] in skip:
            continue
        todos.append(spec)
    # 不存在的新域测试直接跳过（例如 test_tiff.py 还没落盘）
    runnable = []
    for spec in todos:
        if spec["file"].is_file():
            runnable.append(spec)
        else:
            print(f"{YELLOW}[SKIP]{RESET} {spec['key']} - 文件不存在 "
                  f"({spec['file'].name})")
    if not runnable:
        print("没有可执行的测试。")
        return 2

    work = Path(workdir) if workdir else Path(
        tempfile.mkdtemp(prefix="yz_runall_"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"{DIM}数据隐站 · 全套测试{RESET}")
    print(f"  仓库：{REPO}")
    print(f"  临时区：{work}")
    print(f"  顺序：{' → '.join(s['key'] for s in runnable)}\n")

    results = []
    t_all = time.time()
    for spec in runnable:
        key = spec["key"]
        env = dict(os.environ)
        data = work / f"data_{key}"
        env.update({
            "YINZHAN_DATA_DIR": str(data),
            "YINZHAN_OUTPUT_DIR": str(data / "out"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        })
        env.pop("YINZHAN_DEBUG", None)
        cmd = [PY, "-X", "utf8", str(spec["file"])]
        print(f"▶ {key}: {spec['label']}")
        t0 = time.time()
        try:
            if verbose:
                p = subprocess.run(cmd, cwd=str(REPO), env=env,
                                   timeout=spec["timeout"])
                out, err, code = "", "", p.returncode
            else:
                p = subprocess.run(cmd, cwd=str(REPO), env=env,
                                   capture_output=True, timeout=spec["timeout"])
                out = p.stdout.decode("utf-8", "replace")
                err = p.stderr.decode("utf-8", "replace")
                code = p.returncode
            ok = code == 0
        except subprocess.TimeoutExpired:
            out = err = ""
            code = None
            ok = False
            err = f"超时（> {spec['timeout']}s）"
        except Exception as exc:  # noqa: BLE001
            out = err = ""
            code = None
            ok = False
            err = f"{type(exc).__name__}: {exc}"
        elapsed = round(time.time() - t0, 2)
        results.append({"key": key, "label": spec["label"], "ok": ok,
                        "code": code, "elapsed": elapsed})
        if ok:
            print(f"  {GREEN}[PASS]{RESET} {key} ({elapsed}s)")
        else:
            print(f"  {RED}[FAIL]{RESET} {key} exit={code} ({elapsed}s)")
            tail = _tail((out or "") + "\n" + (err or ""))
            if tail and tail != "(无输出)":
                print(f"{DIM}{tail}{RESET}")

    elapsed_all = round(time.time() - t_all, 2)
    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    print(f"\n{'=' * 66}")
    print(f"  全套测试汇总：{passed}/{len(results)} 通过，耗时 {elapsed_all}s")
    if failed:
        print(f"  {RED}失败：{', '.join(r['key'] for r in failed)}{RESET}")
        print(f"  {RED}结论：禁止提交/交付。修好或回滚后重跑。{RESET}")
    else:
        print(f"  {GREEN}结论：红线 + 各功能域自测全部通过。{RESET}")
    print(f"{'=' * 66}\n")
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"{DIM}临时目录保留：{work}{RESET}")
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    specs = _specs()
    ap = argparse.ArgumentParser(
        description="数据隐站全套测试（红线门禁 + 各功能域自测），顺序执行并汇总退出码")
    ap.add_argument("--only", default="",
                    help="只跑这些（逗号分隔）：regression,barcodes,finance,trade,visual,tiff")
    ap.add_argument("--skip", default="",
                    help="跳过这些（逗号分隔），如 --skip domain 表示只跑红线门禁")
    ap.add_argument("-v", "--verbose", action="store_true", help="透传各测试输出")
    ap.add_argument("--keep", action="store_true", help="保留临时数据目录")
    ap.add_argument("--workdir", default="", help="固定临时目录（默认系统临时）")
    ap.add_argument("--list", action="store_true", help="列出将执行的测试")
    args = ap.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if "domain" in skip:
        skip = {"barcodes", "finance", "trade", "visual", "tiff"}
    known = {s["key"] for s in specs}
    bad = (only or set()) - known
    if bad:
        print(f"未知测试：{bad}；可选：{sorted(known)}")
        return 2
    if args.list:
        for s in specs:
            state = "存在" if s["file"].is_file() else "缺失(会跳过)"
            print(f"  {s['key']:<11} {s['label']:<28} {state}  {s['file']}")
        return 0
    try:
        return run_all(only, skip, args.verbose, args.keep, args.workdir)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
