# -*- coding: utf-8 -*-
"""AI 导出文件名中性化自测（「给 AI 看」导出名不得泄漏源文件名/原值 token）。

背景
----
换名版（mode=ai）此前的导出名 = 源文件名 stem + ``_换名_`` + 交付编号 + 源扩展名。
真实测试集里源文件名常带公司全名、金额、人名（doc01/doc02/doc08 等），喂给 AI 时
文件名本身就在泄漏。本轮修复：ai 导出名改为内容哈希驱动的中性名。

本测试锁定（全部虚构数据，仓库不放真实样例）：
1. jobs 导出（``MANAGER.export``）ai 名 = ``AI视图_{sha256前8}_换名_{seal}{ext}``，
   不含源 stem 的任何 token；human 名与改动前规则逐字一致（``{stem}_已隐去_{seal}``）；
2. MCP ``sanitize_document`` 走 JobManager（L2/M2 起）后与工作台/审计同源，
   ai 输出名 = ``AI视图_{sha256前8}_换名_{seal}{suffix}``，
   human 输出名 = ``{stem}_已隐去_{seal}{suffix}``（seal 命名是 M2 的预期变化，
   这样成品名与 audit.out_name / seal_no 一致）；
3. 同一内容换一个源文件名导出，sha8 相同、名字仍中性（取自内容哈希而非文件名）。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_ai_export_naming.py

只用系统临时目录（YINZHAN_DATA_DIR / YINZHAN_OUTPUT_DIR），不写仓库 data/。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

# 必须先定数据/输出目录，再 import yinzhan（config 在 import 时解析路径）。
_TMP = Path(tempfile.mkdtemp(prefix="yz_ai_export_naming_"))
os.environ["YINZHAN_DATA_DIR"] = str(_TMP / "data")
os.environ["YINZHAN_OUTPUT_DIR"] = str(_TMP / "data" / "out")
# hybrid 后端：本测试数据目录没有 runtime.json，固定 local 后端保证确定性
# （MCP 段校验本地 JobManager 命名；HTTP 后端见 test_mcp_v2.py 的 TestHttpBackend）。
os.environ["YINZHAN_MCP_BACKEND"] = "local"

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

PASS_COUNT = 0
FAILS: list[tuple[str, str]] = []

#: 合成源文件名：中文姓名 + 公司全名 + 票据类型 + 手机号（全部虚构）
SRC1_NAME = "张伟_华鑫建材有限公司_发票_13800138000.pdf"
#: 同一份内容、换一个源文件名（证明命名来自内容哈希，不来自文件名）
SRC2_NAME = "李雷_山海贸易有限公司_合同_13900139000.pdf"

AI_NAME_RE = re.compile(r"^AI视图_([0-9a-f]{8})_换名_(\d{8}-\d{4})\.pdf$")
# L2/M2：MCP sanitize_document 也走 MANAGER.export，命名与工作台/审计同源（带 seal）。
MCP_AI_NAME_RE = AI_NAME_RE
HUMAN_NAME_RE = re.compile(r"^.+_已隐去_(\d{8}-\d{4})\.pdf$")


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS_COUNT
    if ok:
        PASS_COUNT += 1
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""), flush=True)
    else:
        FAILS.append((name, detail))
        print(f"  [FAIL] {name}  {detail}", flush=True)


# ────────────────────────────── 夹具 ──────────────────────────────

def _make_pdf(path: Path) -> None:
    """造一页纯文本 PDF：纯文本抽取器可读，含手机号/邮箱两个可识别实体。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Contact: 13800138000", fontsize=12)
    page.insert_text((72, 130), "Email: test@example.com", fontsize=12)
    doc.save(str(path))
    doc.close()


def _wait(job, timeout: float = 180.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if job.stage in ("ready", "error"):
            return
        time.sleep(0.05)
    raise TimeoutError(f"任务超时：{job.stage}")


def _new_job(src: Path):
    from yinzhan.jobs import MANAGER

    job = MANAGER.create(src, "standard")
    _wait(job)
    if job.stage != "ready":
        raise RuntimeError(f"任务未就绪：{job.stage} / {job.error}")
    return job


def _export(job, mode: str) -> dict:
    from yinzhan.jobs import MANAGER

    return MANAGER.export(job.id, mode)


def _assert_neutral(name: str, tokens: list[str], label: str) -> None:
    folded = name.casefold()
    leaked = [t for t in tokens if t and t.casefold() in folded]
    check(f"{label} 不含源文件名 token", not leaked,
          f"泄漏={leaked} name={name}")


def _mcp_payload(resp: dict) -> dict:
    try:
        return json.loads(resp["content"][0]["text"])
    except Exception:
        return {}


# ────────────────────────────── 主流程 ──────────────────────────────

def main() -> int:
    try:                                   # 直接运行时控制台可能是 GBK
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from yinzhan import registry
    from yinzhan.utils import safe_name, sha256_file

    print("数据隐站 · AI 导出文件名中性化自测", flush=True)
    print(f"  临时数据目录：{_TMP}", flush=True)
    errors = registry.load_plugins()
    if errors:
        print(f"  ! 插件加载失败 {len(errors)} 个：{errors[0].splitlines()[0]}",
              flush=True)

    src1 = _TMP / "src" / SRC1_NAME
    src1.parent.mkdir(parents=True, exist_ok=True)
    _make_pdf(src1)
    src_hash = sha256_file(src1)
    sha8 = src_hash[:8]
    tokens1 = ["张伟", "华鑫", "建材", "有限公司", "发票",
               "13800138000", src1.stem]

    # ── 1) jobs：ai 中性 / human 不变 ──
    print("\n[1] jobs（MANAGER.export）", flush=True)
    job = _new_job(src1)
    ai = _export(job, "ai")
    human = _export(job, "human")
    ai_path = Path(ai["out"])
    human_path = Path(human["out"])
    ai_name = ai_path.name
    human_name = human_path.name

    m = AI_NAME_RE.match(ai_name)
    check("ai 名格式 = AI视图_{sha8}_换名_{seal}.pdf", m is not None, ai_name)
    check("ai 名 sha8 = 源文件 sha256 前 8 位",
          bool(m) and m.group(1) == sha8,
          f"name_sha8={m.group(1) if m else '?'} src_sha8={sha8}")
    _assert_neutral(ai_name, tokens1, "jobs ai 名")
    check("ai 名保留 `_换名_` 语义 token", "_换名_" in ai_name, ai_name)
    check("ai 扩展名正确", ai_path.suffix == ".pdf", ai_path.suffix)
    check("ai 成品存在且非空",
          ai_path.is_file() and ai_path.stat().st_size > 0, str(ai_path))
    check("ai verify residual=0",
          (ai.get("verify") or {}).get("residual") == 0, str(ai.get("verify")))

    human_expect = (f"{safe_name(Path(job.name).stem, 60)}"
                    f"_已隐去_{human['seal_no']}.pdf")
    check("human 命名规则与改动前逐字一致", human_name == human_expect,
          f"got={human_name} expect={human_expect}")
    check("human 仍含源 stem（给人看，命名不变）",
          Path(job.name).stem in human_name, human_name)
    check("human 扩展名正确", human_path.suffix == ".pdf", human_path.suffix)
    check("human verify residual=0",
          (human.get("verify") or {}).get("residual") == 0, str(human.get("verify")))

    # audit：out_name 随命名中性化（本轮目的），doc_name 语义不变（不记原值无关）；链完整
    from yinzhan import audit
    recs = [r for r in audit.recent(10) if r.get("job_id") == job.id]
    rec_ai = next((r for r in recs if r.get("mode") == "ai"), {})
    rec_h = next((r for r in recs if r.get("mode") == "human"), {})
    check("audit ai out_name 已中性（目的）", rec_ai.get("out_name") == ai_name,
          str(rec_ai.get("out_name")))
    check("audit human out_name 保持旧规则", rec_h.get("out_name") == human_name,
          str(rec_h.get("out_name")))
    check("audit doc_name 语义不变（仍为源文件名）",
          rec_ai.get("doc_name") == job.name and rec_h.get("doc_name") == job.name,
          str(rec_ai.get("doc_name")))
    chain = audit.verify_chain()
    check("audit 链校验通过", chain.get("ok") is True, str(chain))

    # ── 2) 同一内容换源文件名：sha8 相同、名字仍中性 ──
    print("\n[2] 同内容换源文件名", flush=True)
    src2 = _TMP / "src" / SRC2_NAME
    src2.write_bytes(src1.read_bytes())
    check("两份源文件内容哈希相同", sha256_file(src2) == src_hash,
          sha256_file(src2)[:12])
    tokens2 = ["李雷", "山海", "贸易", "有限公司", "合同",
               "13900139000", src2.stem]
    job2 = _new_job(src2)
    ai2_name = Path(_export(job2, "ai")["out"]).name
    m2 = AI_NAME_RE.match(ai2_name)
    check("换源名后仍按内容 sha8 命名",
          bool(m2) and m2.group(1) == sha8, ai2_name)
    _assert_neutral(ai2_name, tokens2, "jobs ai 名（换源名）")
    check("两份同内容导出共享同一 sha8（不随源名变）",
          bool(m2) and m2.group(1) == sha8, f"{ai_name} / {ai2_name}")

    # ── 3) MCP sanitize_document：L2/M2 起走 JobManager，命名与工作台/审计同源 ──
    print("\n[3] MCP sanitize_document（JobManager/seal 命名）", flush=True)
    old_cwd = os.getcwd()
    cwd = _TMP / "mcp_cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        os.chdir(cwd)
        import mcp_server

        p_ai = _mcp_payload(mcp_server.tool_sanitize_document(
            {"path": str(src1), "mode": "ai"}))
        out_ai = Path(p_ai.get("output") or "")
        mm = MCP_AI_NAME_RE.match(out_ai.name)
        check("MCP ai 调用 ok", p_ai.get("ok") is True, str(p_ai)[:200])
        check("MCP ai 名格式 = AI视图_{sha8}_换名_{seal}.pdf（同 MANAGER）",
              mm is not None, out_ai.name)
        check("MCP ai 名 sha8 = 源文件 sha256 前 8 位",
              bool(mm) and mm.group(1) == sha8,
              f"name_sha8={mm.group(1) if mm else '?'} src_sha8={sha8}")
        _assert_neutral(out_ai.name, tokens1, "MCP ai 名")
        check("MCP ai 成品存在且非空",
              out_ai.is_file() and out_ai.stat().st_size > 0, str(out_ai))
        check("MCP ai 成品落 data/out（不再 cwd/sanitized_output）",
              out_ai.parent.resolve() == (Path(os.environ["YINZHAN_OUTPUT_DIR"])).resolve(),
              str(out_ai.parent))
        check("MCP ai verify residual=0",
              (p_ai.get("verify") or {}).get("residual") == 0,
              str(p_ai.get("verify")))
        # 本测试数据目录是临时目录、没有 runtime.json → hybrid 自动走 local 后端：
        # 此时不得返回一个打开必然 404 的 Web 深链，必须明确 workbench_available=false。
        check("MCP ai 返回 job_id/origin + workbench 可用性",
              bool(p_ai.get("job_id")) and p_ai.get("origin") == "mcp"
              and p_ai.get("workbench_available") is False
              and p_ai.get("workbench_url") is None,
              f"job_id={p_ai.get('job_id')} origin={p_ai.get('origin')} "
              f"wb={p_ai.get('workbench_available')}")

        p_h = _mcp_payload(mcp_server.tool_sanitize_document(
            {"path": str(src1), "mode": "human"}))
        out_h = Path(p_h.get("output") or "")
        mh = HUMAN_NAME_RE.match(out_h.name)
        check("MCP human 命名 = {stem}_已隐去_{seal}{suffix}（同 MANAGER）",
              mh is not None and out_h.name.startswith(f"{src1.stem}_已隐去_"),
              out_h.name)
        check("MCP human 成品存在且非空",
              out_h.is_file() and out_h.stat().st_size > 0, str(out_h))
    finally:
        os.chdir(old_cwd)

    # ── 汇总 ──
    print("\n" + "=" * 66, flush=True)
    total = PASS_COUNT + len(FAILS)
    if FAILS:
        print(f"  FAIL：{len(FAILS)}/{total} 项失败", flush=True)
        for name, detail in FAILS:
            print(f"    ✗ {name}  {detail}", flush=True)
        print("=" * 66, flush=True)
        return 1
    print(f"  PASS：{total} 项全部通过", flush=True)
    print("=" * 66, flush=True)
    return 0


if __name__ == "__main__":
    _code = 2
    try:
        _code = main()
    except Exception:
        traceback.print_exc()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(_code)
