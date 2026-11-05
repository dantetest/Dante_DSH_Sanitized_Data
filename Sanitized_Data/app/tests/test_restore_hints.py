# -*- coding: utf-8 -*-
"""回件提示（问题1/2/3）测试：F2 restore 段（本文件另有 F1/F3 段，按段追加）。

运行（在 worktree ``Sanitized_Data`` 根下；DSH 受限沙箱先装 ZY_PDF/probes/temp_shim）：

    python -X utf8 app/tests/test_restore_hints.py
    # 或
    python -X utf8 -c "import sys;sys.path.insert(0,r'D:\\DSH_DESKTOP_WORKSPACE\\ZY_PDF\\probes');\\
from temp_shim import install;install(temp_dir='...');\\
import runpy;runpy.run_path('app/tests/test_restore_hints.py',run_name='__main__')"

分段约定（多人并行）：
- 共享 header（本段）只初始化一次：临时目录/env/sys.path/imports/模块级 setUpModule；
- F2 段（restore 问题1/2）由 task-13 追加；
- F1 段（pdf_parser 问题1/3）、F3 段（pipeline/jobs hints）由各自任务追加；
- 新段请插在文件末尾 ``# ===== APPEND-ABOVE =====`` 标记行之前，保持 __main__ 块最后。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

# ── 共享 header：临时数据目录（先设 env，再 import yinzhan）──
_TMP_BASE = Path(os.environ.get("YINZHAN_TEST_TMP") or tempfile.gettempdir())
TMP = _TMP_BASE / f"yz_restore_hints_{os.getpid()}_{os.urandom(4).hex()}"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["YINZHAN_DATA_DIR"] = str(TMP / "data")
os.environ["YINZHAN_OUTPUT_DIR"] = str(TMP / "out")
os.environ["YINZHAN_DESKTOP"] = "1"

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import fitz  # noqa: E402

from yinzhan import restore, vault  # noqa: E402
from yinzhan.config import DATA_DIR, OUTPUT_DIR  # noqa: E402


def setUpModule() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def tearDownModule() -> None:
    shutil.rmtree(TMP, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
# F2：restore 用例（问题1 扫描页 AI 回件重绘 / 问题2 近似实体上下文匹配）
# ══════════════════════════════════════════════════════════════════════

F2_SCAN_FLAG = "YINZHAN_RESTORE_SCAN_AI_V2"
F2_OCC_FLAG = "YINZHAN_RESTORE_OCCURRENCE_HINTS"

F2_FAKE = "上海明远数据科技有限公司"
F2_ORIG_A = "云基科技有限公司"
F2_ORIG_B = "云基集团有限公司"


def _f2_context_hash(text: str, start: int, end: int) -> str:
    """与导出侧 pipeline.hint_context_hash 同规则（NFKC + 去空白）。"""
    prefix = text[max(0, start - 8):start]
    suffix = text[end:end + 8]
    ctx = unicodedata.normalize("NFKC", prefix + "|" + suffix)
    ctx = re.sub(r"\s+", "", ctx)
    return hashlib.sha256(ctx.encode("utf-8")).hexdigest()[:16]


def _f2_save_mapping(job_id: str, mapping: dict, *, name: str = "回件.pdf",
                     hints: dict | None = None) -> None:
    meta = {"name": name, "seal": f"seal-{job_id}"}
    if hints is not None:
        meta["pdf_restore_hints"] = hints
    vault.save_mapping(job_id, mapping, meta)


def _f2_make_fake_image(text: str, size=(400, 120), font_size: int = 22) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", font_size)
    except Exception:
        font = ImageFont.load_default()
    draw.text((8, max(0, size[1] // 2 - font_size // 2 - 6)), text,
              fill=(0, 0, 0), font=font)
    path = TMP / f"f2_fake_{abs(hash(text)) % 10_000_000}.png"
    image.save(path)
    return path


def _f2_build_ai_scan_return(path: Path, rotations: list[int] | None = None,
                             rect=(72, 72, 472, 232),
                             background: bool = False) -> list[dict]:
    """构造 AI 扫描页回件：可见假值图 + 隐藏假值文本层（模拟导出成品）。

    ``background=True`` 时先铺一张整页图（模拟扫描页背景），用于验证
    回件重绘不会把整页扫描图删掉。
    返回与 pdf_parser 记录结构一致的 hints 列表（rect 为 PDF 未旋转坐标）。
    """
    rotations = rotations or [0]
    img = _f2_make_fake_image(F2_FAKE)
    bg = None
    if background:
        from PIL import Image

        bg = TMP / "f2_scan_bg.png"
        Image.new("RGB", (400, 600), (220, 220, 220)).save(bg)
    doc = fitz.open()
    hints: list[dict] = []
    try:
        for pno, rotation in enumerate(rotations):
            page = doc.new_page(width=595, height=400)
            if bg is not None:
                page.insert_image(page.rect, filename=str(bg))
            page.insert_image(fitz.Rect(*rect), filename=str(img),
                              keep_proportion=False)
            page.insert_text((rect[0], rect[3] - 22), F2_FAKE, fontsize=20,
                             fontname="china-s", render_mode=3)
            if rotation:
                page.set_rotation(rotation)
            hints.append({"page": pno, "rect": [float(v) for v in rect],
                          "fake": F2_FAKE, "rotation": int(rotation),
                          "size": 20.0, "color": [0.0, 0.0, 0.0]})
        doc.save(str(path))
    finally:
        doc.close()
    return hints


def _f2_build_mixed_occurrence_return(path: Path) -> dict:
    """doc03 式回件：同一 fake 两次出现，其中第二次是图片（hint）、第一次是可见文字。

    返回 {"lines": [...], "hint_rect": [...], "text_rects": [[...], [...]]}。
    """
    top_line = f"甲方：{F2_FAKE}（盖章）"
    low_line = f"乙方：{F2_FAKE}（签字）"
    img = _f2_make_fake_image(F2_FAKE)
    hint_rect = (72, 170, 472, 214)
    doc = fitz.open()
    try:
        page = doc.new_page(width=595, height=400)
        page.insert_text((72, 100), top_line, fontsize=18, fontname="china-s")
        page.insert_image(fitz.Rect(*hint_rect), filename=str(img),
                          keep_proportion=False)
        page.insert_text((72, 206), F2_FAKE, fontsize=20,
                         fontname="china-s", render_mode=3)
        doc.save(str(path))
    finally:
        doc.close()
    doc = fitz.open(str(path))
    try:
        rects = doc[0].search_for(F2_FAKE)
    finally:
        doc.close()
    return {"lines": [top_line, low_line], "hint_rect": list(hint_rect),
            "text_rects": [list(r) for r in rects]}


def _f2_build_two_occurrence_return(path: Path) -> tuple[list[tuple], list[str]]:
    """构造同一假值出现两次（不同上下文）的回件 PDF。"""
    lines = [f"甲方：{F2_FAKE}（盖章）", f"乙方：{F2_FAKE}（签字）"]
    doc = fitz.open()
    try:
        page = doc.new_page(width=595, height=842)
        for i, line in enumerate(lines):
            page.insert_text((72, 120 + i * 40), line, fontsize=12,
                             fontname="china-s")
        doc.save(str(path))
    finally:
        doc.close()
    doc = fitz.open(str(path))
    try:
        rects = doc[0].search_for(F2_FAKE)
    finally:
        doc.close()
    return lines, [list(r) for r in rects]


def _f2_rect_overlap(a, b) -> float:
    ax0, ay0, ax1, ay1 = [float(v) for v in a]
    bx0, by0, bx1, by1 = [float(v) for v in b]
    x0, y0 = max(ax0, bx0), max(ay0, by0)
    x1, y1 = min(ax1, bx1), min(ay1, by1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    small = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return inter / small if small > 0 else 0.0


def _f2_report_text(report) -> str:
    main = next(a for a in report.artifacts if a.kind == "main")
    doc = fitz.open(str(main.path))
    try:
        return "\n".join(doc[i].get_text("text")
                         for i in range(doc.page_count))
    finally:
        doc.close()


class _F2EnvMixin:
    """开关默认关闭；每个用例结束恢复 env，避免跨用例污染。"""

    F2_ENV_KEYS = (F2_SCAN_FLAG, F2_OCC_FLAG)

    def setUp(self):
        self._f2_prev = {k: os.environ.get(k) for k in self.F2_ENV_KEYS}
        for key in self.F2_ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._f2_prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestF2MappingInfo(_F2EnvMixin, unittest.TestCase):
    def test_old_mapping_without_hints(self):
        _f2_save_mapping("j_f2_old", {"ORG": {F2_ORIG_A: F2_FAKE}})
        info = restore.load_mapping_info("j_f2_old")
        self.assertEqual(info.scan_ai_images, {})
        self.assertEqual(info.occurrence_hints, {})
        self.assertEqual(info.pairs[0].orig, F2_ORIG_A)

    def test_hints_loaded_and_invalid_tolerated(self):
        hints = {
            "version": 1,
            "scan_ai_images": {"0": [{"rect": [0, 0, 10, 10],
                                      "fake": F2_FAKE}]},
            "occurrence_hints": {F2_FAKE: [
                {"orig": F2_ORIG_A, "etype": "ORG", "page": 0, "order": 0,
                 "context_hash": "abc"}]},
        }
        _f2_save_mapping("j_f2_hints", {"ORG": {F2_ORIG_A: F2_FAKE}},
                         hints=hints)
        info = restore.load_mapping_info("j_f2_hints")
        self.assertEqual(list(info.scan_ai_images), ["0"])
        self.assertEqual(info.occurrence_hints[F2_FAKE][0]["orig"], F2_ORIG_A)
        # 非法类型 → 空 dict（旧映射兜底，不抛错）
        _f2_save_mapping("j_f2_bad", {"ORG": {F2_ORIG_A: F2_FAKE}},
                         hints={"version": 1, "scan_ai_images": "bad",
                                "occurrence_hints": [1, 2]})
        info_bad = restore.load_mapping_info("j_f2_bad")
        self.assertEqual(info_bad.scan_ai_images, {})
        self.assertEqual(info_bad.occurrence_hints, {})


class TestF2ScanAiV2(_F2EnvMixin, unittest.TestCase):
    """问题1：扫描页 AI 回件 —— 开关关闭不改行为；打开后删假值图并重绘真值。"""

    def _run(self, job_id: str, src: Path, flag: str) -> tuple:
        os.environ[F2_SCAN_FLAG] = flag
        info = restore.load_mapping_info(job_id)
        report = restore.restore_path(src, src.name, info,
                                      out_dir=OUTPUT_DIR / f"f2_scan_{flag}_{job_id}")
        main = next(a for a in report.artifacts if a.kind == "main")
        doc = fitz.open(str(main.path))
        try:
            texts = [doc[i].get_text("text") for i in range(doc.page_count)]
            # delete_image 会把删掉的图替换成 1x1 占位；只统计真实图片
            images = [len([x for x in doc[i].get_images(full=True)
                           if x[2] > 10 and x[3] > 10])
                      for i in range(doc.page_count)]
            rotations = [int(doc[i].rotation) for i in range(doc.page_count)]
            pages = doc.page_count
        finally:
            doc.close()
        return report, texts, images, rotations, pages

    def test_flag_off_keeps_fake_image_and_old_behavior(self):
        src = TMP / "f2_scan_off.pdf"
        hints = _f2_build_ai_scan_return(src, rotations=[0])
        _f2_save_mapping("j_f2_scan_off", {"ORG": {F2_ORIG_A: F2_FAKE}},
                         hints={"version": 1,
                                "scan_ai_images": {"0": hints}})
        report, texts, images, rotations, pages = self._run(
            "j_f2_scan_off", src, "0")
        self.assertEqual(pages, 1)
        self.assertEqual(rotations, [0])
        self.assertEqual(images, [1])          # 假值像素图仍在（问题1 现象）
        self.assertNotIn(F2_FAKE, texts[0])    # 隐藏文本层 fake 已被旧逻辑清掉
        self.assertIn(F2_ORIG_A, texts[0])     # 真值已写
        self.assertFalse(any("AI 扫描页回件重绘" in w for w in report.warnings))

    def test_flag_on_removes_fake_image_and_repaints(self):
        src = TMP / "f2_scan_on.pdf"
        hints = _f2_build_ai_scan_return(src, rotations=[0, 90])
        _f2_save_mapping("j_f2_scan_on", {"ORG": {F2_ORIG_A: F2_FAKE}},
                         hints={"version": 1,
                                "scan_ai_images": {"0": [hints[0]],
                                                   "1": [hints[1]]}})
        report, texts, images, rotations, pages = self._run(
            "j_f2_scan_on", src, "1")
        self.assertEqual(pages, 2)                    # 页数不变
        self.assertEqual(rotations, [0, 90])          # /Rotate 保持
        self.assertEqual(images, [0, 0])              # 假值像素图已移除
        for pno, text in enumerate(texts):
            self.assertNotIn(F2_FAKE, text, f"page {pno} 仍残留假值")
            self.assertIn(F2_ORIG_A, text, f"page {pno} 未写入真值")
            # 可见真值 + 原有隐藏文本层的 render_mode=3 真值 → 抽取到 2 份
            self.assertEqual(text.count(F2_ORIG_A), 2,
                             f"page {pno} 隐藏文本层真值未写入")
        self.assertTrue(any("AI 扫描页回件重绘" in w for w in report.warnings))
        self.assertEqual(getattr(report, "scan_ai_fixed", None), 2)
        self.assertGreater(report.replaced, 0)

    def test_flag_on_without_hints_keeps_old_behavior(self):
        src = TMP / "f2_scan_nohint.pdf"
        _f2_build_ai_scan_return(src, rotations=[0])
        _f2_save_mapping("j_f2_scan_nohint", {"ORG": {F2_ORIG_A: F2_FAKE}})
        report, texts, images, rotations, pages = self._run(
            "j_f2_scan_nohint", src, "1")
        self.assertEqual(images, [1])                  # 无 hints → 旧逻辑
        self.assertNotIn(F2_FAKE, texts[0])
        self.assertIn(F2_ORIG_A, texts[0])
        self.assertFalse(any("AI 扫描页回件重绘" in w for w in report.warnings))

    def test_flag_on_keeps_full_page_background_image(self):
        """回归（F4 doc03）：只删假值替换小图，不能把整页扫描背景图一起删掉。"""
        src = TMP / "f2_scan_bg.pdf"
        hints = _f2_build_ai_scan_return(src, rotations=[0], background=True)
        _f2_save_mapping("j_f2_scan_bg", {"ORG": {F2_ORIG_A: F2_FAKE}},
                         hints={"version": 1,
                                "scan_ai_images": {"0": hints}})
        report, texts, images, rotations, pages = self._run(
            "j_f2_scan_bg", src, "1")
        self.assertEqual(images, [1], "整页背景图被误删（应只剩背景图）")
        self.assertNotIn(F2_FAKE, texts[0])
        self.assertIn(F2_ORIG_A, texts[0])
        # 背景像素保留：页角仍是 220 灰底，而不是被抹成白页
        main = next(a for a in report.artifacts if a.kind == "main")
        doc = fitz.open(str(main.path))
        try:
            pix = doc[0].get_pixmap(dpi=72, alpha=False)
            docs = doc[0].get_image_rects(doc[0].get_images(full=True)[0][0])
            # 取背景图内、但不在假值 hint 矩形内的一点（hint 区会被铺白底）
            point = (int(docs[0].x0 + 10),
                     int(docs[0].y0 + docs[0].height * 0.9))
            sample = pix.pixel(*point)
        finally:
            doc.close()
        self.assertTrue(all(abs(v - 220) <= 25 for v in sample[:3]),
                        f"背景图未保留: corner={sample}")

    def test_bad_hint_xref_must_not_delete_background(self):
        """硬保护回归（F4 doc08 p0 黑页）：hint xref 若误指背景图，绝不删背景。

        预清理重存会让 xref 重排，旧逻辑把 hint xref 当假值图删 -> 整页 1x1 黑页。
        这里故意把 hint xref 写成背景图 xref：面积门应拒绝它，改用 IoU 找假值小图。
        """
        import numpy as np

        src = TMP / "f2_scan_badxref.pdf"
        hints = _f2_build_ai_scan_return(src, rotations=[0], background=True)
        # 取背景图 xref（第一张，400x600），故意塞给 hint
        probe = fitz.open(str(src))
        try:
            imgs = probe[0].get_images(full=True)
            # 面积最大的那张是整页背景图
            bg = max(imgs, key=lambda x: int(x[2]) * int(x[3]))
            fake = min(imgs, key=lambda x: int(x[2]) * int(x[3]))
            bg_xref, fake_xref = bg[0], fake[0]
        finally:
            probe.close()
        self.assertNotEqual(bg_xref, fake_xref)
        hints[0]["xref"] = bg_xref
        _f2_save_mapping("j_f2_scan_badxref", {"ORG": {F2_ORIG_A: F2_FAKE}},
                         hints={"version": 1,
                                "scan_ai_images": {"0": hints}})
        report, texts, images, rotations, pages = self._run(
            "j_f2_scan_badxref", src, "1")
        # 背景图必须保留（真实图 1 张、尺寸仍是背景 400x600）
        self.assertEqual(images, [1], "背景图被误删（doc08 p0 黑页回归）")
        main = next(a for a in report.artifacts if a.kind == "main")
        doc = fitz.open(str(main.path))
        try:
            page = doc[0]
            real = [x for x in page.get_images(full=True)
                    if x[2] > 10 and x[3] > 10]
            self.assertEqual(len(real), 1)
            self.assertGreaterEqual(max(real[0][2], real[0][3]), 300,
                                    f"保留的不是整页背景图: {real[0][:4]}")
            pm = page.get_pixmap(dpi=36, alpha=False)
            arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(
                pm.height, pm.width, pm.n)[:, :, :3]
            mean = float(arr.mean())
        finally:
            doc.close()
        self.assertGreater(mean, 150.0, f"回件页疑似黑页 mean={mean:.1f}")
        self.assertNotIn(F2_FAKE, texts[0])
        self.assertIn(F2_ORIG_A, texts[0])

    def test_rotated_page_visible_text_upright(self):
        """回归（F4 doc08 /Rotate=270）：真值可见文字必须按页旋转，显示为横排。"""
        import numpy as np

        for rot in (0, 90, 180, 270):
            with self.subTest(rotate=rot):
                src = TMP / f"f2_scan_rot{rot}.pdf"
                hints = _f2_build_ai_scan_return(src, rotations=[rot])
                job = f"j_f2_scan_rot{rot}"
                _f2_save_mapping(
                    job, {"ORG": {F2_ORIG_A: F2_FAKE}},
                    hints={"version": 1, "scan_ai_images": {"0": hints}})
                report, texts, images, rotations, pages = self._run(
                    job, src, "1")
                self.assertEqual(pages, 1)
                self.assertEqual(rotations, [rot])      # /Rotate 保持
                self.assertEqual(images, [0])
                self.assertNotIn(F2_FAKE, texts[0])
                self.assertIn(F2_ORIG_A, texts[0])
                # 显示方向：按 page.rotation_matrix 取回显示坐标裁剪，应为横排
                main = next(a for a in report.artifacts if a.kind == "main")
                doc = fitz.open(str(main.path))
                try:
                    page = doc[0]
                    clip = fitz.Rect(*hints[0]["rect"]) * page.rotation_matrix
                    pm = page.get_pixmap(dpi=150, clip=clip, alpha=False)
                    arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(
                        pm.height, pm.width, pm.n)[:, :, :3]
                finally:
                    doc.close()
                dark = (arr.min(axis=2) < 120)
                ys, xs = np.where(dark)
                self.assertGreater(len(xs), 20, f"rotate={rot} 真值未渲染")
                width = int(xs.max() - xs.min() + 1)
                height = int(ys.max() - ys.min() + 1)
                self.assertGreater(width, height * 1.2,
                                   f"rotate={rot} 真值显示为竖排（{width}x{height}）")

    def test_scan_ai_hint_aligns_to_second_fake_occurrence(self):
        """doc03 式：hint 对应第 2 次 fake 出现；第 1 次是可见文字（无图）。"""
        src = TMP / "f2_scan_mixed.pdf"
        meta = _f2_build_mixed_occurrence_return(src)
        fake = F2_FAKE
        hint_rect = meta["hint_rect"]
        _f2_save_mapping(
            "j_f2_scan_mixed", {"ORG": {F2_ORIG_A: fake, F2_ORIG_B: fake}},
            hints={"version": 1,
                   "scan_ai_images": {"0": [{
                       "page": 0, "rect": hint_rect, "fake": fake,
                       "rotation": 0, "size": 20.0,
                       "color": [0.0, 0.0, 0.0]}]},
                   "occurrence_hints": {fake: [
                       {"orig": F2_ORIG_A, "etype": "ORG", "page": 0,
                        "order": 6},
                       {"orig": F2_ORIG_B, "etype": "ORG", "page": 0,
                        "order": 7}]}})
        os.environ[F2_OCC_FLAG] = "1"   # _run 只设 scan 开关；消歧需同时打开
        report, texts, images, rotations, pages = self._run(
            "j_f2_scan_mixed", src, "1")
        self.assertEqual(images, [0])
        text = texts[0]
        self.assertNotIn(fake, text)
        self.assertIn(F2_ORIG_A, text)
        self.assertIn(F2_ORIG_B, text)
        main = next(a for a in report.artifacts if a.kind == "main")
        doc = fitz.open(str(main.path))
        try:
            rect_a = doc[0].search_for(F2_ORIG_A)
            rect_b = doc[0].search_for(F2_ORIG_B)
        finally:
            doc.close()
        self.assertEqual(len(rect_a), 1)
        self.assertEqual(len(rect_b), 1)
        # A 恢复在第 1 次出现处（顶部文字行），B 在 hint 图位置（第 2 次出现）
        self.assertGreater(_f2_rect_overlap(rect_a[0], meta["text_rects"][0]),
                           0.3)
        self.assertGreater(_f2_rect_overlap(rect_b[0], hint_rect), 0.3)


class TestF2OccurrenceHints(_F2EnvMixin, unittest.TestCase):
    """问题2：同一假值多个候选原值 → context_hash / page+order / canonical 回退。"""

    def _run(self, job_id: str, src: Path, flag: str):
        os.environ[F2_OCC_FLAG] = flag
        info = restore.load_mapping_info(job_id)
        report = restore.restore_path(src, src.name, info,
                                      out_dir=OUTPUT_DIR / f"f2_occ_{flag}_{job_id}")
        return report

    def _occurrence_hints(self, lines: list[str], with_hash: bool = True,
                          page: int = 0) -> dict:
        candidates = []
        for order, line in enumerate(lines):
            start = line.index(F2_FAKE)
            item = {"orig": F2_ORIG_A if order == 0 else F2_ORIG_B,
                    "etype": "ORG", "page": page, "order": order}
            if with_hash:
                item["context_hash"] = _f2_context_hash(line, start,
                                                        start + len(F2_FAKE))
            candidates.append(item)
        return {F2_FAKE: candidates}

    def test_flag_off_uses_canonical_for_all_occurrences(self):
        src = TMP / "f2_occ_off.pdf"
        lines, _rects = _f2_build_two_occurrence_return(src)
        _f2_save_mapping(
            "j_f2_occ_off",
            {"ORG": {F2_ORIG_A: F2_FAKE, F2_ORIG_B: F2_FAKE}},
            hints={"version": 1,
                   "occurrence_hints": self._occurrence_hints(lines)})
        report = self._run("j_f2_occ_off", src, "0")
        text = _f2_report_text(report)
        self.assertEqual(text.count(F2_ORIG_A), 2)   # canonical = 先出现的 A
        self.assertNotIn(F2_ORIG_B, text)
        self.assertFalse(any("无法按上下文判定" in w for w in report.warnings))

    def test_flag_on_restores_by_context_hash(self):
        src = TMP / "f2_occ_on.pdf"
        lines, rects = _f2_build_two_occurrence_return(src)
        self.assertEqual(len(rects), 2)
        _f2_save_mapping(
            "j_f2_occ_on",
            {"ORG": {F2_ORIG_A: F2_FAKE, F2_ORIG_B: F2_FAKE}},
            hints={"version": 1,
                   "occurrence_hints": self._occurrence_hints(lines)})
        report = self._run("j_f2_occ_on", src, "1")
        text = _f2_report_text(report)
        self.assertIn(F2_ORIG_A, text)
        self.assertIn(F2_ORIG_B, text)
        self.assertEqual(text.count(F2_ORIG_A), 1)
        self.assertEqual(text.count(F2_ORIG_B), 1)
        self.assertFalse(any("无法按上下文判定" in w for w in report.warnings))
        # 写入位置正确：A 在第一次出现处、B 在第二次出现处（不串位）
        main = next(a for a in report.artifacts if a.kind == "main")
        doc = fitz.open(str(main.path))
        try:
            page = doc[0]
            rect_a = page.search_for(F2_ORIG_A)
            rect_b = page.search_for(F2_ORIG_B)
        finally:
            doc.close()
        self.assertEqual(len(rect_a), 1)
        self.assertEqual(len(rect_b), 1)
        self.assertGreater(_f2_rect_overlap(rect_a[0], rects[0]), 0.3)
        self.assertGreater(_f2_rect_overlap(rect_b[0], rects[1]), 0.3)
        self.assertLess(_f2_rect_overlap(rect_a[0], rects[1]), 0.3)
        # 报告按「假值 + 实际原值」分条展示
        joined = json.dumps(report.public(), ensure_ascii=False)
        self.assertIn(F2_ORIG_A, joined)
        self.assertIn(F2_ORIG_B, joined)

    def test_flag_on_falls_back_canonical_with_warning(self):
        src = TMP / "f2_occ_fallback.pdf"
        lines, _rects = _f2_build_two_occurrence_return(src)
        # 候选没有 context_hash 且 page 与回件不符 → 无法判定 → canonical + warning
        _f2_save_mapping(
            "j_f2_occ_fallback",
            {"ORG": {F2_ORIG_A: F2_FAKE, F2_ORIG_B: F2_FAKE}},
            hints={"version": 1,
                   "occurrence_hints": self._occurrence_hints(
                       lines, with_hash=False, page=9)})
        report = self._run("j_f2_occ_fallback", src, "1")
        text = _f2_report_text(report)
        self.assertEqual(text.count(F2_ORIG_A), 2)   # 回退 canonical
        self.assertNotIn(F2_ORIG_B, text)
        self.assertTrue(any("无法按上下文判定" in w for w in report.warnings))

    def test_flag_on_no_hash_uses_global_order_rank(self):
        """hint.order 是导出侧全局递增序号（非页内序号）：按同页 order 排序取 rank。"""
        src = TMP / "f2_occ_rank.pdf"
        lines, rects = _f2_build_two_occurrence_return(src)
        hints = self._occurrence_hints(lines, with_hash=False)
        hints[F2_FAKE][0]["order"] = 5          # 全局序号，不是页内 0/1
        hints[F2_FAKE][1]["order"] = 9
        _f2_save_mapping(
            "j_f2_occ_rank",
            {"ORG": {F2_ORIG_A: F2_FAKE, F2_ORIG_B: F2_FAKE}},
            hints={"version": 1, "occurrence_hints": hints})
        report = self._run("j_f2_occ_rank", src, "1")
        text = _f2_report_text(report)
        self.assertEqual(text.count(F2_ORIG_A), 1)
        self.assertEqual(text.count(F2_ORIG_B), 1)
        main = next(a for a in report.artifacts if a.kind == "main")
        doc = fitz.open(str(main.path))
        try:
            rect_a = doc[0].search_for(F2_ORIG_A)
            rect_b = doc[0].search_for(F2_ORIG_B)
        finally:
            doc.close()
        self.assertEqual(len(rect_a), 1)
        self.assertEqual(len(rect_b), 1)
        self.assertGreater(_f2_rect_overlap(rect_a[0], rects[0]), 0.3)
        self.assertGreater(_f2_rect_overlap(rect_b[0], rects[1]), 0.3)

    def test_apply_occurrence_hints_rebuilds_ocr_text(self):
        """扫描页 OCR 行也走同一问题2规则（_apply_occurrence_hints 单元用例）。"""
        line = f"甲方：{F2_FAKE}（盖章）"
        info = restore.MappingInfo(
            job_id="j_f2_unit", name="u", seal="s", saved_at="",
            pairs=[restore.Pair(fake=F2_FAKE, orig=F2_ORIG_A, etype="ORG")],
            by_type={"ORG": 1},
            occurrence_hints={F2_FAKE: [{
                "orig": F2_ORIG_B, "etype": "ORG", "page": 0, "order": 0,
                "context_hash": _f2_context_hash(
                    line, line.index(F2_FAKE),
                    line.index(F2_FAKE) + len(F2_FAKE))}]})
        report = restore.Report(fmt="pdf", strategy="")
        index = restore._Index(info.pairs)
        _new_text, events = restore.scan_replace(line, index)
        self.assertEqual(len(events), 1)
        rebuilt = restore._apply_occurrence_hints(
            line, events, info.occurrence_hints, 0, report)
        self.assertIn(F2_ORIG_B, rebuilt)
        self.assertNotIn(F2_FAKE, rebuilt)
        self.assertEqual(report.replaced, 1)


# ══════════════════════════════════════════════════════════════════════
# F1：pdf_parser 用例（问题1 导出侧 ai_scan_images 记录 / 问题3 标签回填）
#     owner: task-12（prod-inventory）；只追加本段，不改共享 header/F2 段
# ══════════════════════════════════════════════════════════════════════

from contextlib import contextmanager as _f1_contextmanager  # noqa: E402
from unittest import mock as _f1_mock  # noqa: E402

from yinzhan.parsers import pdf_parser as _f1_pp  # noqa: E402
from yinzhan.parsers.pdf_parser import PdfParser as _F1PdfParser  # noqa: E402
from yinzhan.udm import (  # noqa: E402
    Decision as _F1Decision, Document as _F1Document,
    Entity as _F1Entity, Page as _F1Page,
    ParseContext as _F1ParseContext, Span as _F1Span,
)

try:  # reportlab 仅用于生成「标签+值同 span + 嵌入子集字体」夹具
    from reportlab.lib.pagesizes import A4 as _F1_A4  # noqa: E402
    from reportlab.lib.styles import (  # noqa: E402
        ParagraphStyle as _F1ParagraphStyle,
        getSampleStyleSheet as _F1_getSampleStyleSheet,
    )
    from reportlab.pdfbase import pdfmetrics as _F1_pdfmetrics  # noqa: E402
    from reportlab.pdfbase.ttfonts import TTFont as _F1_TTFont  # noqa: E402
    from reportlab.platypus import (  # noqa: E402
        Paragraph as _F1Paragraph, SimpleDocTemplate as _F1SimpleDocTemplate,
    )

    _F1_HAVE_REPORTLAB = True
    _F1_REPORTLAB_ERR = ""
except Exception as _f1_rl_exc:  # pragma: no cover
    _F1_HAVE_REPORTLAB = False
    _F1_REPORTLAB_ERR = f"{type(_f1_rl_exc).__name__}: {_f1_rl_exc}"

_F1_SIMHEI = Path(r"C:\Windows\Fonts\simhei.ttf")
_F1_FAKE = "13900139000"


def _f1_workdir(name: str) -> Path:
    """F1 用例工作目录（建在共享 TMP 下，模块结束时统一清理）。"""
    path = TMP / f"f1_{name}_{os.urandom(3).hex()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@_f1_contextmanager
def _f1_env(**values):
    """临时设置/清除环境变量（None=清除），退出恢复。"""
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[str(key)] = str(value)
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[str(key)] = value


def _f1_norm(text: str) -> str:
    return "".join(str(text or "").split())


def _f1_pdf_text(path: Path, *, sort: bool = False) -> str:
    with fitz.open(str(path)) as pdf:
        if sort:
            return "\n".join(page.get_text("text", sort=True) for page in pdf)
        return "\n".join(page.get_text("text") for page in pdf)


def _f1_page_meta(path: Path) -> list[tuple[float, float, int, int]]:
    with fitz.open(str(path)) as pdf:
        return [(round(page.rect.width, 2), round(page.rect.height, 2),
                 page.rotation, len(page.get_images())) for page in pdf]


def _f1_rect_close(a, b, tol: float = 1.0) -> bool:
    """两个矩形是否在 tol(pt) 内一致（回件按几何 bbox 定位假值图）。"""
    try:
        ar, br = fitz.Rect(a), fitz.Rect(b)
        return (abs(ar.x0 - br.x0) <= tol and abs(ar.y0 - br.y0) <= tol
                and abs(ar.x1 - br.x1) <= tol and abs(ar.y1 - br.y1) <= tol)
    except Exception:
        return False


def _f1_ctx() -> _F1ParseContext:
    return _F1ParseContext(workdir=str(TMP))


# ── 问题1：扫描页 AI 可见假值图位置记录 ──

def _f1_build_scanned_pdf(path: Path, rotation: int = 0,
                          page_size: tuple[float, float] = (420.0, 300.0)
                          ) -> tuple[Path, tuple[float, float]]:
    """把文本页渲染成图片再塞进 PDF = 无文本层扫描件；返回 (路径, 未旋转尺寸)。"""
    src = fitz.open()
    page = src.new_page(width=page_size[0], height=page_size[1])
    page.insert_text((40, 80), f"联系电话 {_F1_FAKE}", fontsize=16)
    png = page.get_pixmap(dpi=150).tobytes("png")
    src.close()
    raw_w, raw_h = ((page_size[1], page_size[0])
                    if rotation in (90, 270) else page_size)
    out = fitz.open()
    scan_page = out.new_page(width=raw_w, height=raw_h)
    scan_page.set_rotation(int(rotation) % 360)
    scan_page.insert_image(fitz.Rect(0, 0, raw_w, raw_h), stream=png,
                           rotate=int(rotation) % 360)
    out.save(str(path))
    out.close()
    return path, (float(raw_w), float(raw_h))


def _f1_scan_document(path: Path, width: float, height: float,
                      rotation: int) -> _F1Document:
    """构造扫描页 UDM Document（blocks 为空 = 纯图片扫描页）。"""
    doc = _F1Document(doc_id="d_f1_scan", path=str(path), fmt="pdf")
    doc.pages.append(_F1Page(index=0, width=width, height=height,
                             ref={"scanned": True, "rotation": rotation}))
    return doc


def _f1_scan_entity(rect: tuple[float, float, float, float]) -> _F1Entity:
    return _F1Entity(
        id=_F1Entity.new_id(), etype="PHONE", label="手机号",
        value="13800138000", spans=[_F1Span(rect=rect, page=0)],
        confidence=0.99, origin="test",
        decision=_F1Decision(strategy="surrogate", replacement=_F1_FAKE))


def _f1_entities_from_values(doc: _F1Document, values) -> list[_F1Entity]:
    entities: list[_F1Entity] = []
    for value in values:
        for page in doc.pages:
            for block in page.blocks:
                pos = (block.text or "").find(value)
                if pos < 0:
                    continue
                entities.append(_F1Entity(
                    id=_F1Entity.new_id(), etype="OTHER", label=value,
                    value=value,
                    spans=[_F1Span(block_id=block.id, start=pos,
                                   end=pos + len(value))],
                    confidence=0.99, origin="test",
                    decision=_F1Decision(strategy="surrogate",
                                         replacement="X" * len(value))))
                break
            else:
                continue
            break
    return entities


class TestPdfParserScanAiImages(unittest.TestCase):
    """问题1 导出侧：扫描页 AI 可见假值图位置记录（doc.extra["ai_scan_images"]）。"""

    @classmethod
    def setUpClass(cls):
        cls.workdir = _f1_workdir("scan_ai")
        cls.parser = _F1PdfParser()

    def test_record_structure_and_rotation(self):
        for rotation in (0, 270):
            with self.subTest(rotation=rotation):
                scan_path, (width, height) = _f1_build_scanned_pdf(
                    self.workdir / f"scan_{rotation}.pdf", rotation)
                rect = (40.0, 40.0, 300.0, 80.0)
                doc = _f1_scan_document(scan_path, width, height, rotation)
                entity = _f1_scan_entity(rect)
                out = self.workdir / f"scan_{rotation}_ai.pdf"
                self.parser.export(doc, [entity], {entity.id: entity.decision},
                                   out, {"mode": "ai"})
                records = doc.extra.get("ai_scan_images")
                self.assertIsInstance(records, list,
                                      "ai_scan_images 必须是 list")
                self.assertEqual(len(records), 1, f"每条替换图一条记录: {records}")
                record = records[0]
                self.assertEqual(record.get("kind"), "image",
                                 "图片路径成功应记录 kind=image")
                self.assertEqual(record.get("page"), 0)
                self.assertIsInstance(record.get("rect"), list)
                self.assertEqual(len(record["rect"]), 4)
                self.assertTrue(all(isinstance(v, (int, float))
                                    for v in record["rect"]))
                self.assertEqual(record.get("fake"), _F1_FAKE)
                self.assertEqual(record.get("rotation"), rotation)
                self.assertGreater(float(record.get("size") or 0), 0)
                self.assertEqual(
                    [round(float(c), 3) for c in record.get("color") or []],
                    [0.0, 0.0, 0.0])
                # rect 必须是「未旋转 PDF 坐标」：与独立用 _to_pdf_space 换算一致
                with fitz.open(str(scan_path)) as pdf:
                    expected = _f1_pp._to_pdf_space(fitz.Rect(rect), pdf[0])
                for got, want in zip(record["rect"],
                                     (expected.x0, expected.y0,
                                      expected.x1, expected.y1)):
                    self.assertAlmostEqual(float(got), float(want), places=2,
                                           msg="记录 rect 应为未旋转 PDF 坐标")
                # rect 不越界（未旋转页面尺寸）
                x0, y0, x1, y1 = [float(v) for v in record["rect"]]
                self.assertLessEqual(x0, x1)
                self.assertLessEqual(y0, y1)
                self.assertGreaterEqual(x0, 0)
                self.assertGreaterEqual(y0, 0)
                self.assertLessEqual(x1, width + 0.5)
                self.assertLessEqual(y1, height + 0.5)
                # 记录不改变成品页数/旋转/图片数量（源 1 张 + 假值图 >=1）
                with fitz.open(str(out)) as pdf:
                    self.assertEqual(pdf.page_count, 1)
                    self.assertEqual(pdf[0].rotation, rotation)
                    self.assertGreaterEqual(len(pdf[0].get_images()), 2)
                    saved_xrefs = {int(info[0])
                                   for info in pdf[0].get_images(full=True)}
                    # xref 必须是**成品里**可用的稳定引用（回件用 delete_image(xref)
                    # 精准删假值小图）；保存后由 _resolve_scan_ai_hint_xrefs 回填
                    self.assertIsInstance(record.get("xref"), int,
                                          "image hint 必须带成品内稳定 xref")
                    self.assertIn(int(record["xref"]), saved_xrefs,
                                  "xref 必须能在成品图片列表中找到")
                    infos = pdf[0].get_image_info(xrefs=True)
                    page_area = (float(pdf[0].rect.width)
                                 * float(pdf[0].rect.height))
                    target = [info for info in infos
                              if int(info.get("xref") or -1)
                              == int(record["xref"])]
                    self.assertEqual(len(target), 1,
                                     "xref 应唯一指向假值小图")
                    bbox = fitz.Rect(target[0]["bbox"])
                    cand_area = float(bbox.width) * float(bbox.height)
                    # 面积保护（F4 doc08 p0 黑页事故防线）：hint xref 绝不能
                    # 指向整页背景图（>30% 页面面积）。
                    self.assertLessEqual(
                        cand_area, page_area * 0.30,
                        "hint xref 指向了整页背景图（面积保护失效）")
                    big = [info for info in infos
                           if fitz.Rect(info["bbox"]).width
                           * fitz.Rect(info["bbox"]).height
                           > page_area * 0.5]
                    self.assertTrue(big, "夹具应有整页扫描底图")
                    self.assertNotIn(
                        int(record["xref"]),
                        {int(info["xref"]) for info in big},
                        "假值图 xref 不能等于整页底图 xref")
                    # 旋转页：hint rect 是未旋转 PDF 坐标，图片 bbox 可能显示/
                    # 未旋转两种口径，任一口径匹配偏差必须 <=1.5pt。
                    dev = _f1_pp._match_image_rect_dev(
                        fitz.Rect(record["rect"]), bbox, pdf[0])
                    self.assertLessEqual(dev, 1.5,
                                         "hint rect 与成品图片 bbox 不匹配")
                    if rotation == 0:
                        matched = [info for info in infos
                                   if _f1_rect_close(fitz.Rect(info["bbox"]),
                                                     record["rect"])]
                        self.assertEqual(len(matched), 1,
                                         "成品中应恰有一张图片 bbox == 记录 rect")
                # 记录 rect 与 insert 矩形一致（rotation 0：显示坐标 == PDF 坐标）
                if rotation == 0:
                    self.assertTrue(_f1_rect_close(
                        fitz.Rect(record["rect"]), rect), "rect 应与贴图矩形一致")

    def test_area_protection_rejects_fullpage_background(self):
        """异常/超大 hint rect 不得把 xref 解析到整页背景图（面积保护）。"""
        scan_path, (width, height) = _f1_build_scanned_pdf(
            self.workdir / "area_guard_scan.pdf", 270)
        doc = _f1_scan_document(scan_path, width, height, 270)
        entity = _f1_scan_entity((40.0, 40.0, 300.0, 80.0))
        out = self.workdir / "area_guard_ai.pdf"
        self.parser.export(doc, [entity], {entity.id: entity.decision},
                           out, {"mode": "ai"})
        bad_doc = _F1Document(doc_id="d_area_guard", path=str(out), fmt="pdf")
        bad_doc.pages.append(_F1Page(index=0, width=width, height=height,
                                     ref={"scanned": True, "rotation": 270}))
        bad_record = {"kind": "image", "page": 0,
                      "rect": [0.0, 0.0, float(width), float(height)],
                      "fake": "x", "rotation": 270, "size": 10.0,
                      "color": [0.0, 0.0, 0.0]}
        bad_doc.extra["ai_scan_images"] = [bad_record]
        _f1_pp._resolve_scan_ai_hint_xrefs(bad_doc, out)
        self.assertNotIn("xref", bad_record,
                         "整页 hint 不得解析到背景图 xref（面积保护应拒绝）")

    def test_no_record_for_text_page_ai(self):
        text_pdf = self.workdir / "text_page.pdf"
        doc0 = fitz.open()
        page0 = doc0.new_page(width=420, height=300)
        page0.insert_text((40, 80), "联系电话 13800138000", fontsize=16)
        doc0.save(str(text_pdf))
        doc0.close()
        doc = self.parser.parse(text_pdf, _f1_ctx())
        entity = None
        for page in doc.pages:
            for block in page.blocks:
                pos = (block.text or "").find("13800138000")
                if pos >= 0:
                    entity = _F1Entity(
                        id=_F1Entity.new_id(), etype="PHONE", label="手机号",
                        value="13800138000",
                        spans=[_F1Span(block_id=block.id, start=pos,
                                       end=pos + 11)],
                        confidence=0.99, origin="test",
                        decision=_F1Decision(strategy="surrogate",
                                             replacement=_F1_FAKE))
                    break
        self.assertIsNotNone(entity, "文本页夹具未解析出手机号 span")
        out = self.workdir / "text_page_ai.pdf"
        self.parser.export(doc, [entity], {entity.id: entity.decision}, out,
                           {"mode": "ai"})
        self.assertFalse(doc.extra.get("ai_scan_images"),
                         "文本层页不应产生扫描页假值图记录")

    def test_no_record_for_human_mode(self):
        scan_path, (width, height) = _f1_build_scanned_pdf(
            self.workdir / "human_scan.pdf", 0)
        doc = _f1_scan_document(scan_path, width, height, 0)
        entity = _f1_scan_entity((40.0, 40.0, 300.0, 80.0))
        out = self.workdir / "scan_human.pdf"
        self.parser.export(doc, [entity], {entity.id: entity.decision}, out,
                           {"mode": "human"})
        self.assertFalse(doc.extra.get("ai_scan_images"),
                         "human 模式不写可见假值图，不应记录")

    def test_wide_rect_image_path_not_fallback(self):
        """宽矩形必须走图片路径：旧实现 dpi 为浮点会抛 TypeError 而回退文字。"""
        scan_path, (width, height) = _f1_build_scanned_pdf(
            self.workdir / "wide_scan.pdf", 0, page_size=(597.0, 300.0))
        rect = (73.3, 84.9, 518.1, 123.5)   # 宽 444.8 → 旧实现 dpi≈226.618
        doc = _f1_scan_document(scan_path, width, height, 0)
        entity = _f1_scan_entity(rect)
        out = self.workdir / "wide_scan_ai.pdf"
        self.parser.export(doc, [entity], {entity.id: entity.decision},
                           out, {"mode": "ai"})
        records = doc.extra.get("ai_scan_images") or []
        self.assertEqual(len(records), 1, f"应恰有 1 条 hint: {records}")
        self.assertEqual(records[0].get("kind"), "image",
                         "宽矩形不应回退文字（浮点 dpi 回归）")
        self.assertIsInstance(records[0].get("xref"), int)
        self.assertEqual(_f1_pdf_text(out, sort=True).strip(), "",
                         "图片化后不应留下可提取的可见假值文字")

    def test_text_fallback_hint_recorded(self):
        """图片路径失败回退可见文字时，也必须记录 kind=text hint 供回件重写。"""
        scan_path, (width, height) = _f1_build_scanned_pdf(
            self.workdir / "textfallback_scan.pdf", 0)
        doc = _f1_scan_document(scan_path, width, height, 0)
        image_entity = _f1_scan_entity((40.0, 40.0, 300.0, 80.0))
        fallback_entity = _F1Entity(
            id=_F1Entity.new_id(), etype="PHONE", label="手机号",
            value="11111111111",
            spans=[_F1Span(rect=(40.0, 120.0, 300.0, 160.0), page=0)],
            confidence=0.99, origin="test",
            decision=_F1Decision(strategy="surrogate", replacement="123"))
        entities = [image_entity, fallback_entity]
        original = _f1_pp._render_replacement_pixmap

        def _render(display_rect, text):
            if text == "123":
                return None      # 仅强制该实体走文字回退
            return original(display_rect, text)

        out = self.workdir / "textfallback_ai.pdf"
        with _f1_mock.patch.object(_f1_pp, "_render_replacement_pixmap",
                                   _render):
            self.parser.export(doc, entities,
                               {e.id: e.decision for e in entities},
                               out, {"mode": "ai"})
        records = doc.extra.get("ai_scan_images") or []
        # 每个替换位置一条 hint（2 个替换 op → 2 条）
        self.assertEqual(len(records), len(entities))
        kinds = {str(r.get("kind") or "image") for r in records}
        self.assertEqual(kinds, {"image", "text"})
        text_hints = [r for r in records if r.get("kind") == "text"]
        self.assertEqual(len(text_hints), 1)
        hint = text_hints[0]
        self.assertEqual(hint.get("fake"), "123")
        self.assertEqual(hint.get("page"), 0)
        self.assertEqual([round(float(v), 1) for v in hint.get("rect") or []],
                         [40.0, 120.0, 300.0, 160.0])
        self.assertGreater(float(hint.get("size") or 0), 0)
        self.assertEqual(len(hint.get("color") or []), 3)
        # 回退文字仍可见（F2 按 text hint 擦除+重写）
        self.assertIn("123", _f1_pdf_text(out))


# ── 问题3：YINZHAN_AI_LABEL_REPAIR 标签回填 ──

def _f1_build_label_fixture(path: Path) -> Path:
    """用 reportlab + 嵌入 SimHei 子集生成「标签+值同 span」PDF（复现生产现象）。"""
    _F1_pdfmetrics.registerFont(_F1_TTFont("SimHei", str(_F1_SIMHEI)))
    styles = _F1_getSampleStyleSheet()
    body = _F1ParagraphStyle("body", parent=styles["Normal"], fontName="SimHei",
                             fontSize=10.5, leading=16)
    doc = _F1SimpleDocTemplate(str(path), pagesize=_F1_A4)
    doc.build([
        _F1Paragraph("合同编号：HT-2026-0099", body),
        _F1Paragraph("委托人：张三，身份证号：110105199001011234，"
                     "联系电话：13800138000。", body),
    ])
    return path


_F1_LABEL_VALUES = ("HT-2026-0099", "110105199001011234", "13800138000")
_F1_LABEL_EXPECTED = ("合同编号：", "身份证号：", "联系电话：")


@unittest.skipUnless(_F1_HAVE_REPORTLAB and _F1_SIMHEI.exists(),
                     f"需要 reportlab+SimHei 生成标签夹具：{_F1_REPORTLAB_ERR}")
class TestPdfParserLabelRepair(unittest.TestCase):
    """问题3：redaction 误删标签片段后回填（开关 YINZHAN_AI_LABEL_REPAIR）。"""

    @classmethod
    def setUpClass(cls):
        cls.workdir = _f1_workdir("label_repair")
        cls.fixture = _f1_build_label_fixture(cls.workdir / "label_fixture.pdf")
        cls.parser = _F1PdfParser()
        cls.base_meta = _f1_page_meta(cls.fixture)

    def _export(self, name: str, *, rotate: int = 0, switch: bool):
        source = self.fixture
        if rotate:
            source = self.workdir / f"label_fixture_rot{rotate}.pdf"
            with fitz.open(str(self.fixture)) as pdf:
                pdf[0].set_rotation(rotate)
                pdf.save(str(source))
        doc = self.parser.parse(source, _f1_ctx())
        entities = _f1_entities_from_values(doc, _F1_LABEL_VALUES)
        self.assertEqual(len(entities), len(_F1_LABEL_VALUES),
                         "夹具未解析出全部实体 span")
        out = self.workdir / name
        with _f1_env(YINZHAN_AI_LABEL_REPAIR=("1" if switch else None)):
            result = self.parser.export(
                doc, entities,
                {entity.id: entity.decision for entity in entities},
                out, {"mode": "human"})
        return out, doc, entities, result

    def test_switch_off_keeps_head_behavior(self):
        """默认关闭：标签仍被整段移除（与 HEAD 现象一致），且不写回填审计。"""
        out, doc, entities, result = self._export("label_off.pdf", switch=False)
        plain = _f1_pdf_text(out)
        sorted_text = _f1_pdf_text(out, sort=True)
        for label in _F1_LABEL_EXPECTED:
            self.assertNotIn(label, plain,
                             f"开关关闭时不应回填标签：{label}")
            self.assertNotIn(label, sorted_text)
        self.assertNotIn("label_repair", doc.extra,
                         "开关关闭时不得写 label_repair 审计")
        self.assertFalse(any("标签回填" in w for w in result.warnings))
        normalized = _f1_norm(plain)
        for entity in entities:
            self.assertNotIn(_f1_norm(entity.value), normalized,
                             "原值残留必须为 0")
        # 页数/几何/旋转不变
        self.assertEqual(_f1_page_meta(out), self.base_meta)

    def test_switch_on_restores_labels_without_residual(self):
        """开关开启：标签回填（按位置排序后可读），原值残留 0，页数/旋转不变。"""
        out, doc, entities, result = self._export("label_on.pdf", switch=True)
        sorted_text = _f1_pdf_text(out, sort=True)
        plain = _f1_pdf_text(out)
        for label in _F1_LABEL_EXPECTED:
            self.assertIn(label, sorted_text,
                          f"回填后标签应恢复：{label}；text={sorted_text[:300]}")
        normalized_plain = _f1_norm(plain)
        for entity in entities:
            self.assertNotIn(_f1_norm(entity.value), normalized_plain,
                             "回填不得写回任何实体原值")
        repair = doc.extra.get("label_repair") or {}
        self.assertEqual(repair.get("version"), 1)
        self.assertGreaterEqual(int(repair.get("total") or 0),
                                len(_F1_LABEL_EXPECTED))
        self.assertIn("0", repair.get("pages") or {})
        self.assertTrue(any("标签回填" in w for w in result.warnings),
                        "回填动作应有 warning 审计")
        meta = _f1_page_meta(out)
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0][2], 0)
        self.assertAlmostEqual(meta[0][0], self.base_meta[0][0], places=1)
        self.assertAlmostEqual(meta[0][1], self.base_meta[0][1], places=1)

    def test_capture_not_called_when_switch_off(self):
        """默认关闭时连「记录片段」都不执行（确保与 HEAD 行为一致）。"""
        doc = self.parser.parse(self.fixture, _f1_ctx())
        entities = _f1_entities_from_values(doc, ("HT-2026-0099",))
        calls = {"n": 0}
        original = _f1_pp._capture_removed_fragments

        def _wrapped(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        with _f1_mock.patch.object(_f1_pp, "_capture_removed_fragments",
                                   _wrapped):
            with _f1_env(YINZHAN_AI_LABEL_REPAIR=None):
                self.parser.export(
                    doc, entities,
                    {entity.id: entity.decision for entity in entities},
                    self.workdir / "capture_off.pdf", {"mode": "human"})
        self.assertEqual(calls["n"], 0, "开关关闭时不得调用片段记录")

    def test_rotated_page_geometry_preserved(self):
        """旋转页：开关开启也不改变页数/旋转（不承诺旋转页标签位置精度）。"""
        out, _doc, entities, _result = self._export("label_rot90.pdf",
                                                    rotate=90, switch=True)
        meta = _f1_page_meta(out)
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0][2], 90, "成品 /Rotate 必须保持 90")
        plain_norm = _f1_norm(_f1_pdf_text(out))
        for entity in entities:
            self.assertNotIn(_f1_norm(entity.value), plain_norm,
                             "旋转页也不得残留原值")


# ══════════════════════════════════════════════════════════════════════
# F3：pipeline/jobs 映射提示（问题2 上下文生成 + 问题1/2 导出侧接线，task-14）
#
# - pipeline.build_occurrence_hints：同一 fake 多原值候选 + context_hash 稳定；
# - jobs.export("ai") 后 vault.load_mapping meta 含 pdf_restore_hints；
# - 无 hints 时 meta 不新增该键（旧映射兼容）。
# ══════════════════════════════════════════════════════════════════════

# 受限沙箱：安装 tempfile ACL 兼容层（优先工作区 probes/temp_shim.py；普通环境失败即忽略）
_PROBES_DIR = REPO.parents[1] / "ZY_PDF" / "probes"
if not _PROBES_DIR.is_dir():
    _PROBES_DIR = Path(r"D:\DSH_DESKTOP_WORKSPACE\ZY_PDF\probes")
F3_SHIM: dict = {"native_ok": True, "patched": False}
try:
    if str(_PROBES_DIR) not in sys.path:
        sys.path.insert(0, str(_PROBES_DIR))
    from temp_shim import install as _f3_install_temp_shim  # noqa: E402

    _f3_tmp = TMP / "f3_tmp"
    _f3_tmp.mkdir(parents=True, exist_ok=True)
    F3_SHIM = _f3_install_temp_shim(temp_dir=str(_f3_tmp))
except Exception as _f3_exc:  # pragma: no cover - 普通环境无 shim
    F3_SHIM = {"native_ok": True, "patched": False,
               "error": f"{type(_f3_exc).__name__}: {_f3_exc}"}

from unittest import mock as _f3_mock  # noqa: E402

from yinzhan import jobs as f3_jobs  # noqa: E402
from yinzhan import pipeline as f3_pipeline  # noqa: E402
from yinzhan import registry as f3_registry  # noqa: E402
from yinzhan.jobs import MANAGER as F3_MANAGER  # noqa: E402
from yinzhan.udm import (  # noqa: E402
    Block as F3Block, Decision as F3Decision, Document as F3Document,
    Entity as F3Entity, Page as F3Page, Span as F3Span,
)

F3_FAKE = "上海明远数据科技有限公司"
F3_ORIG_A = "北京华信科技有限公司"
F3_ORIG_B = "华信科技有限公司"
F3_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
F3_CAND_KEYS = {"orig", "etype", "page", "order", "context_hash"}


def _f3_make_document() -> tuple[F3Document, list[F3Entity]]:
    """构造 2 页文本 UDM：两个近似 ORG 变体共用同一 surrogate 假值。"""
    b0 = F3Block(id="f3b0", kind="text", page=0,
                 text="甲方：北京华信科技有限公司（以下简称甲方）")
    b1 = F3Block(id="f3b1", kind="text", page=1,
                 text="乙方确认华信科技有限公司已完成交付。")
    doc = F3Document(doc_id="f3_doc", path="fictional_f3.pdf", fmt="pdf")
    doc.pages = [
        F3Page(index=0, width=595.0, height=842.0, blocks=[b0]),
        F3Page(index=1, width=595.0, height=842.0, blocks=[b1]),
    ]

    def _ent(eid: str, value: str, block: F3Block,
             *, status: str = "accepted", strategy: str = "surrogate",
             replacement: str | None = F3_FAKE,
             spans: list[F3Span] | None = None) -> F3Entity:
        if spans is None:
            start = block.text.index(value)
            spans = [F3Span(block_id=block.id, start=start,
                            end=start + len(value))]
        return F3Entity(id=eid, etype="ORG", label="机构", value=value,
                        spans=spans, status=status,
                        decision=F3Decision(strategy=strategy,
                                            replacement=replacement))

    ents = [
        _ent("f3e1", F3_ORIG_A, b0),
        _ent("f3e2", F3_ORIG_B, b1),
        # 以下均应被过滤：rejected / 非 surrogate / 空 replacement / 无 spans
        _ent("f3e3", F3_ORIG_A, b0, status="rejected"),
        _ent("f3e4", F3_ORIG_A, b0, strategy="black", replacement=None),
        _ent("f3e5", F3_ORIG_A, b0, replacement=""),
        F3Entity(id="f3e6", etype="PERSON", label="人名", value="李明",
                 spans=[], decision=F3Decision(strategy="surrogate",
                                              replacement="顾俊")),
    ]
    return doc, ents


def _f3_make_pdf(path: Path) -> None:
    """1 页虚构合同 PDF（用于 export 集成用例；全部为虚构数据）。"""
    doc = fitz.open()
    try:
        page = doc.new_page(width=595, height=842)
        lines = [
            "技术服务合同（虚构样例）",
            "委托方：北京云杉测试科技有限公司",
            "联系人：李明 电话：13800138000",
            "合同金额：人民币 12,345.67 元",
            "本页为虚构测试素材，不包含任何真实个人信息。",
        ]
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontname="china-s", fontsize=12)
            y += 26
        doc.save(str(path))
    finally:
        doc.close()


class _F3StubDoc:
    def __init__(self, extra: dict | None = None):
        self.extra = extra or {}


class _F3StubJob:
    def __init__(self, doc=None, entities=None):
        self.doc = doc
        self.entities = entities or []
        self.warnings: list[str] = []


class TestF3OccurrenceHints(unittest.TestCase):
    """问题2 导出侧：build_occurrence_hints 多候选 + hash 规则稳定。"""

    def test_multi_candidates_and_stable_hash(self):
        doc, ents = _f3_make_document()
        hints = f3_pipeline.build_occurrence_hints(doc, ents)
        self.assertEqual(set(hints), {F3_FAKE})
        candidates = hints[F3_FAKE]
        self.assertEqual(len(candidates), 2,
                         f"近似归并的 fake 应保留 2 个原值候选: {candidates}")
        self.assertEqual({c["orig"] for c in candidates},
                         {F3_ORIG_A, F3_ORIG_B})
        self.assertEqual([c["page"] for c in candidates], [0, 1])
        self.assertEqual([c["order"] for c in candidates], [0, 1],
                         "order 应按遍历顺序全局递增")
        for cand in candidates:
            self.assertTrue(F3_CAND_KEYS.issubset(set(cand)), cand)
            self.assertTrue(F3_HASH_RE.match(cand["context_hash"]),
                            f"context_hash 应为 16 位 hex: {cand}")
            self.assertEqual(cand["etype"], "ORG")
        # 上下文口径 = 页级文本（blocks 按 \n 拼接），与回件定位口径一致
        page0_text = "\n".join(b.text for b in doc.pages[0].blocks if b.text)
        start = page0_text.index(F3_ORIG_A)
        expected = f3_pipeline.hint_context_hash(
            page0_text, start, start + len(F3_ORIG_A))
        self.assertEqual(candidates[0]["context_hash"], expected)
        self.assertEqual(f3_pipeline.build_occurrence_hints(doc, ents), hints)

    def test_page_level_context_matches_restore_doc03_style(self):
        """doc03 回归：实体独占一行（块首）时 hint 必须与回件页级上下文一致。

        导出侧若用 block.text 计算，块首实体上下文为 "|..."；回件在页级文本上取
        上下文会带上一行尾部，hash 必然不匹配（doc03 `云基公司` 丢失的根因）。
        """
        b0 = F3Block(id="f3d0", kind="text", page=0,
                     text="云基智慧工程股份有限公司")
        b1 = F3Block(id="f3d1", kind="text", page=0, text="云基函(2026)55号")
        b2 = F3Block(id="f3d2", kind="text", page=0,
                     text="云基公司关于对西藏日喀则地区XX2017年")
        doc = F3Document(doc_id="f3_doc03", path="fictional_doc03.pdf", fmt="pdf")
        doc.pages = [F3Page(index=0, width=595.0, height=842.0,
                            blocks=[b0, b1, b2])]
        fake = "明辨元海工程股份有限公司"
        ents = [
            F3Entity(id="f3d_1", etype="ORG", label="机构",
                     value="云基智慧工程股份有限公司",
                     spans=[F3Span(block_id="f3d0", start=0, end=12)],
                     decision=F3Decision(strategy="surrogate", replacement=fake)),
            F3Entity(id="f3d_2", etype="ORG", label="机构", value="云基公司",
                     spans=[F3Span(block_id="f3d2", start=0, end=4)],
                     decision=F3Decision(strategy="surrogate", replacement=fake)),
        ]
        hints = f3_pipeline.build_occurrence_hints(doc, ents)
        cands = hints[fake]
        self.assertEqual({c["orig"] for c in cands},
                         {"云基智慧工程股份有限公司", "云基公司"})
        page_text = "\n".join(b.text for b in (b0, b1, b2))
        pos_full = page_text.index("云基智慧工程股份有限公司")
        pos_short = page_text.index("云基公司")
        by_orig = {c["orig"]: c for c in cands}
        self.assertEqual(
            by_orig["云基智慧工程股份有限公司"]["context_hash"],
            f3_pipeline.hint_context_hash(page_text, pos_full, pos_full + 12))
        self.assertEqual(
            by_orig["云基公司"]["context_hash"],
            f3_pipeline.hint_context_hash(page_text, pos_short, pos_short + 4))
        # 块级 hash 会失配（守护：不得退回 block.text 口径）
        block_hash = f3_pipeline.hint_context_hash(b2.text, 0, 4)
        self.assertNotEqual(by_orig["云基公司"]["context_hash"], block_hash)
        # 模拟回件：AI 页文本原值→假值后，取假值前后 8 字 hash，应全部命中候选
        ai_page_text = page_text.replace("云基智慧工程股份有限公司", fake)
        ai_page_text = ai_page_text.replace("云基公司", fake)
        got = []
        cursor = 0
        while True:
            idx = ai_page_text.find(fake, cursor)
            if idx < 0:
                break
            got.append(f3_pipeline.hint_context_hash(
                ai_page_text, idx, idx + len(fake)))
            cursor = idx + len(fake)
        self.assertEqual(len(got), 2)
        self.assertEqual(set(got), {c["context_hash"] for c in cands},
                         "回件页级上下文哈希应与导出 hint 全部匹配")

    def test_context_hash_normalizes_whitespace_and_nfkc(self):
        """同上下文仅空白/全角差异 → hash 相同（与 restore 侧规则一致）。

        窗口只取目标前后各 8 字；因此把多余空白放在窗口**之外**（本用例首尾各
        一处），窗口内仍为同一文本。窗口内插空白会顶掉原字符，属于规则本身的
        行为，不应作为本用例断言。
        """
        plain = "甲方：北京华信科技有限公司（以下简称甲方）"
        spaced = "甲方：\u3000\u3000" + F3_ORIG_A + "（以下简称甲方）\u3000"
        a_start = plain.index(F3_ORIG_A)
        b_start = spaced.index(F3_ORIG_A)
        hash_a = f3_pipeline.hint_context_hash(plain, a_start,
                                               a_start + len(F3_ORIG_A))
        hash_b = f3_pipeline.hint_context_hash(spaced, b_start,
                                               b_start + len(F3_ORIG_A))
        self.assertEqual(hash_a, hash_b,
                         "NFKC + 去空白后同一上下文应得到同一 context_hash")
        self.assertTrue(F3_HASH_RE.match(hash_a))

    def test_jobs_scan_ai_grouping_and_empty_hints(self):
        scan_items = [
            {"page": 0, "rect": [10, 20, 30, 40], "fake": "假值甲",
             "rotation": 0, "size": 9.0, "color": [0.0, 0.0, 0.0]},
            {"page": 1, "rect": [50, 60, 70, 80], "fake": "假值乙",
             "rotation": 90},
            {"page": 0, "rect": [11, 21, 31, 41], "fake": "假值丙",
             "rotation": 0},
        ]
        job = _F3StubJob(doc=_F3StubDoc({"ai_scan_images": scan_items}))
        grouped = f3_jobs._collect_scan_ai_images(job.doc)
        self.assertEqual(set(grouped), {"0", "1"})
        self.assertEqual(len(grouped["0"]), 2)
        self.assertEqual(len(grouped["1"]), 1)
        hints = f3_jobs._collect_pdf_restore_hints(job)
        self.assertEqual(hints.get("version"), 1)
        self.assertIn("scan_ai_images", hints)
        self.assertNotIn("occurrence_hints", hints)   # 无实体 → 无候选
        # 无任何提示数据 → 返回空 dict（调用方据此不写 meta 键）
        empty_job = _F3StubJob(doc=_F3StubDoc())
        self.assertEqual(f3_jobs._collect_pdf_restore_hints(empty_job), {})


class TestF3ExportVaultHints(unittest.TestCase):
    """问题1/2 导出侧接线：mode=ai 保存 mapping 时并入 pdf_restore_hints。"""

    @classmethod
    def setUpClass(cls):
        f3_registry.load_plugins()
        cls.pdf = TMP / "f3_fictional_contract.pdf"
        _f3_make_pdf(cls.pdf)
        cls.job = F3_MANAGER.create(
            cls.pdf, strictness="standard", pack_ids=None,
            id_card_mode="fields", time_blur=False, origin="api")
        import time as _time
        t0 = _time.time()
        while cls.job.stage not in ("ready", "failed") and _time.time() - t0 < 120:
            _time.sleep(0.2)
        if cls.job.stage != "ready":
            raise unittest.SkipTest(f"job 未就绪: {cls.job.stage} {cls.job.error}")
        cls.surrogate = [e for e in cls.job.entities
                         if e.decision and e.decision.strategy == "surrogate"]

    def test_export_ai_writes_pdf_restore_hints(self):
        self.assertTrue(self.surrogate, "合成合同应检出 surrogate 实体")
        F3_MANAGER.export(self.job.id, "ai")
        raw = vault.load_mapping(self.job.id)
        self.assertIsNotNone(raw, "AI 导出后应保存 vault 映射")
        meta = raw.get("meta") or {}
        # 旧字段保持（兼容）
        self.assertIn("name", meta)
        self.assertIn("seal", meta)
        hints = meta.get("pdf_restore_hints")
        self.assertIsInstance(hints, dict,
                              f"meta 应含 pdf_restore_hints: {sorted(meta)}")
        self.assertEqual(hints.get("version"), 1)
        occurrence = hints.get("occurrence_hints")
        self.assertIsInstance(occurrence, dict)
        self.assertTrue(occurrence, "应含 occurrence_hints（surrogate 实体候选）")
        flat = [c for cands in occurrence.values() for c in cands]
        self.assertGreaterEqual(len(flat), len(self.surrogate))
        for cand in flat:
            self.assertTrue(F3_CAND_KEYS.issubset(set(cand)), cand)
            self.assertTrue(F3_HASH_RE.match(cand["context_hash"]), cand)
            self.assertIsInstance(cand["page"], int)
            self.assertIsInstance(cand["order"], int)
        # occurrence_hints 顶层 key 是假值文本，且在 vault map 的假值集合里
        fake_values = {fake for table in (raw.get("map") or {}).values()
                       for fake in table.values() if fake}
        self.assertTrue(set(occurrence) & fake_values,
                        "occurrence_hints 的 key 应为映射中的假值")

    def test_no_hints_meta_key_absent(self):
        """无 hints（空候选/无扫描图）时 meta 不新增 pdf_restore_hints 键。"""
        with _f3_mock.patch.object(f3_pipeline, "build_occurrence_hints",
                                   return_value={}):
            F3_MANAGER.export(self.job.id, "ai")
        raw = vault.load_mapping(self.job.id)
        meta = raw.get("meta") or {}
        self.assertNotIn("pdf_restore_hints", meta,
                         "无 hints 时不得新增 meta 键（旧映射兼容）")
        self.assertIn("name", meta)
        self.assertIn("seal", meta)

# ===== APPEND-ABOVE（F1/F3 新段请插在本行之前，保持 __main__ 块在文件末尾）=====

if __name__ == "__main__":
    unittest.main(verbosity=2)
