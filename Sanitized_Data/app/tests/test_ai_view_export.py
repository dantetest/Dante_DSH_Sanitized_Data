# -*- coding: utf-8 -*-
"""「给 AI 看」换名版（mode=ai）导出修复回归测试。

覆盖三项 PDF 导出缺陷（human 涂黑版不受影响）：

- F1 ``_merge_ops``：替换框与黑框相交时必须并入黑框（原文真正 redaction +
  黑覆盖），不得整条丢弃；不相交的替换框保持原样。迭代到不动点（链式相交）。
- F2 旋转页替换文字：合成 /Rotate=270 扫描页导出后，旋转保持 270、渲染 150dpi
  OCR 能逐字读出假值（可见假值按页面旋转绘制为图片）。
- F3 扫描页不可见文本层：mode=ai 且该页 scanned=True 时，文本层字符数显著
  大于 0、含正文关键词和假值、不含任何实体原值；human 模式不写文本层。
- Phase A1 文本层去重：扫描页 AI 模式的可见假值改为图片渲染（不再进文本层），
  每个假值在 get_text 中出现次数 == 原值在 OCR 正文中的出现次数（不得叠加重复）；
  隐藏层对同 bbox+同文本的 OCR 重复块只写一次；图片渲染不可用时回退文字路径。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_ai_view_export.py

数据安全：夹具全部虚构、运行期生成；写到临时目录（YINZHAN_DATA_DIR），
不写仓库 data/。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

# 必须在 import yinzhan 之前指定数据目录，避免写仓库 data/
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="yinzhan_ai_view_"))
os.environ["YINZHAN_DATA_DIR"] = str(_TMP_ROOT / "data")
(_TMP_ROOT / "data").mkdir(parents=True, exist_ok=True)

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import fitz  # PyMuPDF  # noqa: E402

from yinzhan import pipeline, registry                      # noqa: E402
from yinzhan import ocr as ocr_mod                          # noqa: E402
from yinzhan.parsers import pdf_parser as pp                # noqa: E402
from yinzhan.parsers.pdf_parser import (                    # noqa: E402
    PdfParser, _merge_ops, _write_ai_text_layer,
)
from yinzhan.udm import (                                   # noqa: E402
    Block, Decision, Entity, Page, ParseContext, Span,
)

FONT = "china-s"
# 虚构素材：不要替换成真实姓名/号码/单位。
BODY_LINES = (
    "技术服务合同（虚构样例）",
    "委托方：北京云杉测试科技有限公司",
    "联系人：李明 电话：13800138000",
    "备用联系电话：13800138000",
    "合同金额：人民币 123,456.78 元",
    "本页为虚构测试素材，不包含任何真实个人信息。",
)
PHONE = "13800138000"
ORG = "北京云杉测试科技有限公司"
FAKE_PHONE = "15758140237"


# ────────────────────────── F1：合成 ops ──────────────────────────

def _check_f1_merge_replacement_into_black() -> None:
    print("\n■ F1：替换框与黑框相交 → 并入黑框，不再整条丢弃")
    black = (100.0, 100.0, 220.0, 140.0)
    overlap = (200.0, 120.0, 360.0, 138.0)     # 与黑框相交
    near = (205.0, 142.0, 360.0, 160.0)        # 与黑框不相交（y 差 2pt）
    far = (420.0, 400.0, 520.0, 420.0)         # 完全不相交
    ops = [
        {"rect": black, "black": True, "text": ""},
        {"rect": overlap, "black": False, "text": FAKE_PHONE},
        {"rect": near, "black": False, "text": "假值乙123"},
        {"rect": far, "black": False, "text": "假值丙456"},
    ]
    warnings: list[str] = []
    merged = _merge_ops(ops, warnings)

    blacks = [op["rect"] for op in merged if op["black"]]
    reps = [op for op in merged if not op["black"]]
    covered = any(
        rect[0] - 0.5 <= overlap[0] and rect[1] - 0.5 <= overlap[1]
        and rect[2] + 0.5 >= overlap[2] and rect[3] + 0.5 >= overlap[3]
        for rect in blacks)
    print(f"  [{'PASS' if covered else 'FAIL'}] 相交替换矩形被黑框覆盖 "
          f"（黑框 {[(round(r[0]), round(r[2])) for r in blacks]}）")
    leftovers = [op for op in reps
                 if op["text"] == FAKE_PHONE or tuple(op["rect"]) == overlap]
    print(f"  [{'PASS' if not leftovers else 'FAIL'}] 不再残留被丢弃/未涂黑的替换 op")
    print(f"  [{'PASS' if len(blacks) == 2 else 'FAIL'}] 黑框数 = 2（原黑框 + 并入矩形）")
    kept = {tuple(op["rect"]) for op in reps}
    print(f"  [{'PASS' if kept == {near, far} else 'FAIL'}] 不相交替换框行为不变")

    # 链式相交（不动点）：A 与黑框相交，C 只与 A 相交 → 两者都要并入黑框
    chain_black = (0.0, 0.0, 100.0, 40.0)
    chain_a = (90.0, 10.0, 140.0, 30.0)        # 与黑框相交
    chain_c = (130.0, 10.0, 180.0, 30.0)       # 只与 A 相交
    chain_ops = [
        {"rect": chain_black, "black": True, "text": ""},
        {"rect": chain_a, "black": False, "text": "假值A"},
        {"rect": chain_c, "black": False, "text": "假值B"},
    ]
    chain_merged = _merge_ops(chain_ops, [])
    chain_reps = [op for op in chain_merged if not op["black"]]
    chain_blacks = [op["rect"] for op in chain_merged if op["black"]]
    covered_c = any(
        rect[0] - 0.5 <= chain_c[0] and rect[2] + 0.5 >= chain_c[2]
        and rect[1] - 0.5 <= chain_c[1] and rect[3] + 0.5 >= chain_c[3]
        for rect in chain_blacks)
    print(f"  [{'PASS' if not chain_reps and covered_c else 'FAIL'}] "
          f"链式相交迭代到不动点（无替换遗留）")

    faults = []
    if not covered:
        faults.append("overlap_not_covered")
    if leftovers:
        faults.append("replacement_leftover")
    if len(blacks) != 2:
        faults.append("black_count")
    if kept != {near, far}:
        faults.append("unrelated_changed")
    if chain_reps or not covered_c:
        faults.append("chain_not_converged")
    if not warnings:
        faults.append("no_warning")
    if faults:
        raise AssertionError(f"F1 断言失败：{faults}")


# ────────────────────────── F2/F3 合成扫描页 ──────────────────────────

def _display_pdf(path: Path) -> Path:
    """显示方向（横向 400x300）的虚构文本页，仅用于渲染成扫描图。"""
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    y = 60
    for line in BODY_LINES:
        page.insert_text((40, y), line, fontname=FONT, fontsize=13)
        y += 30
    doc.save(str(path))
    doc.close()
    return path


def _rotated_scan_pdf(path: Path, rotation: int) -> Path:
    """文本页渲染成图片再装进 PDF = 无文本层扫描件；整页 /Rotate=rotation。"""
    work = path.with_name("_display.pdf")
    _display_pdf(work)
    with fitz.open(str(work)) as display:
        png = display[0].get_pixmap(dpi=170).tobytes("png")
    work.unlink(missing_ok=True)
    if rotation in (90, 270):
        raw_w, raw_h = 300, 400      # /Rotate=90/270 后显示为 400x300
    else:
        raw_w, raw_h = 400, 300
    doc = fitz.open()
    page = doc.new_page(width=raw_w, height=raw_h)
    page.set_rotation(int(rotation) % 360)
    # 页面带 /Rotate 时，图片要按同一角度插入，显示方向才是「正立」的扫描页
    page.insert_image(fitz.Rect(0, 0, raw_w, raw_h), stream=png,
                      rotate=int(rotation) % 360)
    doc.save(str(path))
    doc.close()
    return path


def _ctx(workdir: Path) -> ParseContext:
    return ParseContext(ocr=ocr_mod.get_ocr(), workdir=str(workdir))


def _ent_for(doc, etype: str, label: str, value: str,
             decision: Decision, *, all_occurrences: bool = False
             ) -> Entity | None:
    """按 OCR 文本里的出现位置造实体；all_occurrences=True 时每个出现都建 span。"""
    spans: list[Span] = []
    for page in doc.pages:
        for block in page.blocks:
            text = block.text or ""
            if all_occurrences:
                start = 0
                while True:
                    pos = text.find(value, start)
                    if pos < 0:
                        break
                    spans.append(Span(block_id=block.id, start=pos,
                                      end=pos + len(value)))
                    start = pos + 1
            else:
                start = text.find(value)
                if start >= 0:
                    spans.append(Span(block_id=block.id, start=start,
                                      end=start + len(value)))
    if not spans:
        return None
    return Entity(id=Entity.new_id(), etype=etype, label=label, value=value,
                  spans=spans, level="L2", confidence=0.99, origin="test",
                  decision=decision)


def _space(text: str) -> str:
    return "".join((text or "").split())


def _pdf_text(path: Path) -> str:
    with fitz.open(str(path)) as pdf:
        return "\n".join(page.get_text("text") for page in pdf)


class MergeOpsTest(unittest.TestCase):
    """F1：_merge_ops 纯几何单测（不需要 OCR / 夹具）。"""

    def test_f1_merge_replacement_into_black(self):
        _check_f1_merge_replacement_into_black()


class HiddenLayerDedupTest(unittest.TestCase):
    """Phase A1：隐藏文本层对「同 bbox + 同文本」的 OCR 重复块只写一次。"""

    def test_duplicate_ocr_blocks_written_once(self):
        print("\n■ Phase A1：隐藏文本层 OCR 重复块去重")
        workdir = Path(tempfile.mkdtemp(prefix="yinzhan_dedup_layer_"))
        try:
            path = workdir / "dedup_layer.pdf"
            blank = fitz.open()
            blank.new_page(width=320, height=220)
            blank.save(str(path))
            blank.close()
            same_a = Block(id="b0", kind="text", text="同一行正文文字",
                           bbox=(20.0, 30.0, 220.0, 48.0), page=0)
            same_b = Block(id="b1", kind="text", text="同一行正文文字",
                           bbox=(20.0, 30.0, 220.0, 48.0), page=0)
            other_pos = Block(id="b2", kind="text", text="同一行正文文字",
                              bbox=(20.0, 80.0, 220.0, 98.0), page=0)
            udm_page = Page(index=0, width=320.0, height=220.0,
                            blocks=[same_a, same_b, other_pos],
                            ref={"scanned": True})
            with fitz.open(str(path)) as pdf:
                written = _write_ai_text_layer(pdf[0], udm_page, {}, {})
                text = _space(pdf[0].get_text("text"))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        print(f"  写入块数={written}，文本层出现次数={text.count('同一行正文文字')}")
        self.assertEqual(written, 2, "同 bbox+同文本的重复块只写一次；不同位置保留")
        self.assertEqual(text.count("同一行正文文字"), 2)


class AiViewExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workdir = _TMP_ROOT / "fixtures"
        cls.workdir.mkdir(parents=True, exist_ok=True)
        registry.load_plugins()
        cls.ocr = ocr_mod.get_ocr()
        if not cls.ocr.available:
            raise unittest.SkipTest(f"OCR 不可用，无法构造扫描件：{cls.ocr.error}")
        cls.parser = PdfParser()
        cls.ctx = _ctx(cls.workdir)

        # F2/F3 共用夹具：/Rotate=270 的纯扫描页，正文里手机号出现两次
        cls.scan_path = _rotated_scan_pdf(cls.workdir / "scan_rot270.pdf", 270)
        cls.doc = cls.parser.parse(cls.scan_path, cls.ctx)
        if not cls.doc.pages or not (cls.doc.pages[0].ref or {}).get("scanned"):
            raise unittest.SkipTest("夹具未被识别为扫描页")
        if int((cls.doc.pages[0].ref or {}).get("rotation") or 0) != 270:
            raise unittest.SkipTest("夹具 /Rotate 未保留为 270")

        phone = _ent_for(cls.doc, "PHONE", "手机号", PHONE,
                         Decision(strategy="surrogate", replacement=FAKE_PHONE),
                         all_occurrences=True)
        org = _ent_for(cls.doc, "ORG", "机构名称", ORG,
                       Decision(strategy="black", black=True, replacement=None))
        if phone is None:
            raise unittest.SkipTest("OCR 未识别出虚构手机号，跳过（环境相关）")
        cls.ents = [phone] + ([org] if org else [])
        orig_text = "\n".join(b.text or "" for p in cls.doc.pages
                              for b in p.blocks)
        cls.phone_occurrences = _space(orig_text).count(PHONE)
        cls.body_chars = sum(len(b.text or "") for p in cls.doc.pages
                             for b in p.blocks)
        if cls.phone_occurrences < 2:
            raise unittest.SkipTest("OCR 未稳定识别出两处虚构手机号，跳过（环境相关）")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)

    def _ai_export(self, name: str):
        out = self.workdir / name
        pipeline.export(self.doc, self.parser, self.ents, out, {"mode": "ai"})
        return out

    def _human_export(self, name: str):
        black_ents = []
        for ent in self.ents:
            black_ents.append(Entity(
                id=ent.id, etype=ent.etype, label=ent.label,
                value=ent.value, spans=list(ent.spans), level=ent.level,
                confidence=ent.confidence, origin="test",
                decision=Decision(strategy="black", black=True,
                                  replacement=None)))
        out = self.workdir / name
        pipeline.export(self.doc, self.parser, black_ents, out, {"mode": "human"})
        return out

    # ── F2：旋转页替换文字方向 + 可读（可见假值为图片，文本层为隐藏层） ──
    def test_f2_rotated_replacement_readable(self):
        print("\n■ F2：/Rotate=270 扫描页导出后假值可见/可读")
        out = self._ai_export("ai_rot270.pdf")
        self.assertTrue(out.exists())
        with fitz.open(str(out)) as pdf:
            page = pdf[0]
            self.assertEqual(page.rotation, 270, "成品页面旋转必须保持 270")
            text = _space(page.get_text("text"))
            self.assertIn(FAKE_PHONE, text, "隐藏文本层里应能找到假值")
            self.assertEqual(text.count(FAKE_PHONE), self.phone_occurrences,
                             "每个假值在文本层出现次数应 == 原值出现次数")
        # 渲染 PNG 后 OCR 回读（图片渲染的可见假值仍要被视觉/OCR 读到）
        png = self.workdir / "ai_rot270_p1.png"
        with fitz.open(str(out)) as pdf:
            pdf[0].get_pixmap(dpi=150).save(str(png))
        from PIL import Image
        lines = self.ocr.recognize(Image.open(png).convert("RGB")) or []
        ocr_text = _space("\n".join(str(getattr(x, "text", "") or "") for x in lines))
        self.assertIn(FAKE_PHONE, ocr_text, "渲染页 OCR 应能逐字读出旋转页假值")

    # ── F3：扫描页不可见文本层（ai 有 / human 无） ──
    def test_f3_scanned_ai_text_layer(self):
        print("\n■ F3：扫描页 mode=ai 补不可见文本层，human 不写")
        out = self._ai_export("ai_textlayer.pdf")
        text = _pdf_text(out)
        print(f"  文本层字符数={len(text.strip())} / 原 OCR 文本={self.body_chars}")
        self.assertGreater(len(text.strip()), max(20, int(self.body_chars * 0.8)),
                           "ai 扫描页文本层应显著大于 0 且覆盖正文")
        self.assertIn("技术服务合同", text, "文本层应含正文关键词")
        self.assertIn(FAKE_PHONE, _space(text), "文本层应含实体假值")
        for ent in self.ents:
            self.assertNotIn(_space(ent.value), _space(text),
                             f"文本层不得残留原值：{ent.etype}")

        # verify 不受不可见文本层影响（扫描件快速复检路径）
        info = pipeline.verify(out, self.ents, self.parser, self.ctx,
                               doc=self.doc, options={})
        self.assertTrue(info.get("verified"))
        self.assertEqual(info.get("residual"), 0)

        # human 模式：同源扫描页不得写文本层
        human_out = self._human_export("human_rot270.pdf")
        human_text = _pdf_text(human_out)
        print(f"  human 文本层字符数={len(human_text.strip())}")
        self.assertEqual(len(human_text.strip()), 0,
                         "human 涂黑版不得补文本层")

    # ── Phase A1：文本层去重不变量 ──
    def test_f3_text_layer_no_duplicate_fake(self):
        print("\n■ Phase A1：假值次数 == 原值出现次数，无页尾重复片段")
        out = self._ai_export("ai_dedup.pdf")
        text = _pdf_text(out)
        flat = _space(text)
        count = flat.count(FAKE_PHONE)
        print(f"  假值出现 {count} 次 / 原值 {self.phone_occurrences} 次；"
              f"文本层 {len(text.strip())} 字 / 正文 {self.body_chars} 字")
        self.assertEqual(count, self.phone_occurrences,
                         "可见替换文字不得与隐藏文本层叠加成重复假值")
        self.assertLessEqual(len(text.strip()), self.body_chars + 40,
                             "文本层字符数应接近原件 OCR 正文，不叠加可见替换文字")
        # 页尾/全文不应有成串重复行（图片化 + 隐藏层去重后）
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        repeated = [(ln, c) for ln, c in Counter(lines).items()
                    if c > 1 and len(_space(ln)) >= 6]
        self.assertEqual(repeated[:5], [], "文本层不应出现重复行/页尾重复片段")

    def test_f3_visible_fake_uses_image(self):
        """可见假值走图片渲染：图片路径被调用，且文本层计数不变。"""
        print("\n■ Phase A1：可见替换文字图片渲染路径")
        calls = {"n": 0}
        original = pp._render_replacement_pixmap

        def wrapped(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        with mock.patch.object(pp, "_render_replacement_pixmap", wrapped):
            out = self._ai_export("ai_imagepath.pdf")
        print(f"  图片渲染调用 {calls['n']} 次")
        self.assertGreaterEqual(calls["n"], self.phone_occurrences,
                                "扫描页 AI 模式可见假值应走图片渲染")
        with fitz.open(str(out)) as pdf:
            # 源扫描页 1 张整页图；替换假值再各插 1 张小图
            self.assertGreater(len(pdf[0].get_images()), 1,
                               "成品里应能看到替换假值的图片对象")
        self.assertEqual(_space(_pdf_text(out)).count(FAKE_PHONE),
                         self.phone_occurrences,
                         "图片化后文本层假值计数不应叠加")

    def test_f3_image_fallback_available(self):
        """图片渲染不可用时回退旧文字路径，导出/verify 仍正常。"""
        print("\n■ Phase A1：图片渲染不可用 → 回退文字路径")
        with mock.patch.object(pp, "_render_replacement_pixmap",
                               lambda *a, **k: None):
            out = self._ai_export("ai_imagefallback.pdf")
        self.assertTrue(out.exists())
        self.assertIn(FAKE_PHONE, _space(_pdf_text(out)), "回退后假值仍应可读")
        info = pipeline.verify(out, self.ents, self.parser, self.ctx,
                               doc=self.doc, options={})
        self.assertTrue(info.get("verified"))
        self.assertEqual(info.get("residual"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
