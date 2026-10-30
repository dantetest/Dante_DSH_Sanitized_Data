# -*- coding: utf-8 -*-
"""PDF 还原字体回归：页面已有名为 china-s 的子集字体时，插入真值仍可抽取。

背景（2026-10-30 D08 实测）：回件 PDF 自己嵌入了 china-s Type0 子集；
还原用 insert_text(fontname="china-s") 会复用该字体，get_text 出来是 \\x00，
人眼可能看到值、文本层不可搜索/不可复制。修复：注册独立系统 CJK 字体。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="yz_pdf_font_"))
os.environ["YINZHAN_DATA_DIR"] = str(_TMP / "data")
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(_TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

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


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("数据隐站 · PDF 还原字体回归", flush=True)
    try:
        import fitz
        from yinzhan import restore
    except Exception as exc:
        print(f"  [SKIP] 缺少 PDF 组件：{exc}")
        return 0

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # 模拟回件：页面里已有名为 china-s 的字体资源
    page.insert_text((50, 60), "回件里的假值 上海长风和瑞律师事务所",
                     fontname="china-s", fontsize=12)
    report = restore.Report(fmt="pdf", strategy="", input_name="x.pdf")
    ok, _shrunk, used_custom = restore._pdf_insert(
        page, fitz.Rect(50, 90, 400, 108), "上海建纬(成都)律师事务所",
        12, 102, None, report)
    text = page.get_text("text") or ""
    check("插入调用成功", ok)
    check("检测到字体冲突并改用独立字体", used_custom)
    check("真值可被 get_text 抽取（不是 \\x00）",
          "上海建纬" in text and "\x00" not in text, repr(text[-80:]))
    check("原假值仍在（测试只验证插入）",
          "上海长风和瑞" in text, repr(text[:80]))
    # 保存前 subset，避免整字体嵌入；输出不应明显大于原文件
    doc.subset_fonts()
    out = _TMP / "font_fix.pdf"
    doc.save(str(out), garbage=3, deflate=True)
    doc.close()
    check("subset 后文件体积可控", out.stat().st_size < 1_000_000,
          f"{out.stat().st_size} bytes")
    print("\n" + "=" * 58)
    if FAILS:
        print(f"  FAIL：{len(FAILS)} 项失败 / {PASS + len(FAILS)} 项")
        for name, detail in FAILS:
            print(f"    - {name}  {detail}")
        return 1
    print(f"  PASS：{PASS} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
