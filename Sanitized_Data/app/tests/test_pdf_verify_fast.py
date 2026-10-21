# -*- coding: utf-8 -*-
"""P2：扫描件 PDF 导出后「快速复检」回归测试（不再二次 OCR）。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_pdf_verify_fast.py

覆盖
----
1. 纯扫描 PDF 正常导出（涂黑 + 替换两种矩形）后 verify 通过；验证 verify 阶段
   OCR 次数从慢路径的每页 1–2 次降到 0 次，并打印 before/after 次数与耗时。
2. 故障注入：导出时漏掉一个实体矩形（成品该区域未被涂黑）→ verify 必须失败；
   替换矩形漏盖（成品仍保留原图墨迹）同样必须失败，防假阴性。
3. 文本层 PDF 仍走原有「重解析 + 文本残留」路径，快速通道不得启用；原文残留
   必须被 verify 拦住。

数据安全
--------
夹具全部是虚构值、运行期生成；日志/断言只打印实体类型、矩形计数与耗时，
不打印 OCR 文本或实体原值。默认写到系统临时目录，`YINZHAN_WRITE_FIXTURES=1`
时保留到 ``app/tests/fixtures/pdf_fast_verify/`` 便于人工查看。
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import fitz  # PyMuPDF  # noqa: E402

from yinzhan import pipeline, registry                    # noqa: E402
from yinzhan import ocr as ocr_mod                        # noqa: E402
from yinzhan.udm import (                                 # noqa: E402
    Decision, Entity, ParseContext, Span, VerifyError,
)

PAGES = 2
# 虚构素材：不要替换成真实姓名/号码/单位。
LINES = (
    "技术服务合同（样例）",
    "委托方：北京云杉测试科技有限公司",
    "联系人：李明 电话：13800138000",
    "地址：北京市朝阳区样例路 88 号 3 号楼 501",
    "合同金额：人民币 123,456.78 元",
    "开户行：测试银行北京分行 账号：6222020200112233445",
    "邮箱：test.user@example.com",
    "本页为虚构测试素材，不包含任何真实个人信息。",
)
# (etype, label, 原值, AI 模式策略)。OCR 认不出的候选会被测试自动跳过。
CANDIDATES = (
    ("PHONE", "手机号", "13800138000", "surrogate"),
    ("BANK", "银行账号", "6222020200112233445", "surrogate"),
    ("ORG", "机构名称", "北京云杉测试科技有限公司", "surrogate"),
    ("EMAIL", "电子邮箱", "test.user@example.com", "surrogate"),
    ("ADDRESS", "地址", "北京市朝阳区样例路", "black"),
)


# ────────────────────────── 夹具 ──────────────────────────

def _fixtures_root() -> Path:
    if os.environ.get("YINZHAN_WRITE_FIXTURES") == "1":
        root = Path(__file__).resolve().parent / "fixtures" / "pdf_fast_verify"
        root.mkdir(parents=True, exist_ok=True)
        return root
    try:
        return Path(tempfile.mkdtemp(prefix="yinzhan_fast_verify_"))
    except OSError:
        # 受限环境（mkdtemp ACL）兜底：放到测试目录下的临时子目录，结束时删除
        root = Path(__file__).resolve().parent / (".tmp_fast_verify_" + str(os.getpid()))
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root


def _text_pdf(path: Path) -> Path:
    """造一个文本层 PDF（虚构内容）。"""
    doc = fitz.open()
    for page_no in range(PAGES):
        page = doc.new_page(width=595, height=842)
        y = 92
        for line in LINES:
            page.insert_text((72, y), line, fontname="china-s", fontsize=12)
            y += 28
        page.insert_text((72, y + 16), f"第 {page_no + 1} 页 / 共 {PAGES} 页",
                         fontname="china-s", fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


def _scan_pdf(path: Path) -> Path:
    """文本页渲染成 200dpi 图片后重新装进 PDF = 无文本层的纯扫描件。"""
    src = path.with_name("_scan_source.pdf")
    _text_pdf(src)
    out = fitz.open()
    with fitz.open(str(src)) as pdf:
        for page in pdf:
            pix = page.get_pixmap(dpi=200)
            new_page = out.new_page(width=595, height=842)
            new_page.insert_image(fitz.Rect(0, 0, 595, 842), stream=pix.tobytes("png"))
    out.save(str(path))
    out.close()
    src.unlink()
    return path


def _ctx(workdir: Path) -> ParseContext:
    return ParseContext(ocr=ocr_mod.get_ocr(), workdir=str(workdir))


def _entities_for(doc, *, force_black: bool) -> tuple[list[Entity], list[tuple[str, int]]]:
    """按已知虚构值在 OCR 文本块里的位置造实体（不依赖规则包）。"""
    entities: list[Entity] = []
    found: list[tuple[str, int]] = []
    for etype, label, value, strategy in CANDIDATES:
        spans: list[Span] = []
        for page in doc.pages:
            for block in page.blocks:
                text = block.text or ""
                start = text.find(value)
                if start < 0:
                    continue
                spans.append(Span(block_id=block.id, start=start, end=start + len(value)))
        if not spans:
            continue
        if force_black or strategy == "black":
            decision = Decision(strategy="black", black=True, replacement=None)
        else:
            decision = Decision(strategy="surrogate",
                                replacement=f"虚构替换值{len(entities) + 1}号")
        entities.append(Entity(
            id=Entity.new_id(), etype=etype, label=label, value=value,
            spans=spans, level="L2", confidence=0.99, origin="test",
            decision=decision,
        ))
        found.append((etype, len(spans)))
    return entities, found


def _seal_entity() -> Entity:
    """矩形 span 的视觉实体：顺带覆盖「非文本 span」的黑框复检路径。"""
    return Entity(
        id=Entity.new_id(), etype="SEAL", label="印章", value="",
        spans=[Span(rect=(70.0, 68.0, 330.0, 100.0), page=0)],
        level="L3", confidence=0.99, origin="test",
        decision=Decision(strategy="black", black=True, replacement=None),
    )


# ────────────────────────── 工具 ──────────────────────────

@contextlib.contextmanager
def _count_ocr():
    """统计 OcrService.recognize 调用次数与耗时（不打印识别内容）。"""
    stats = {"calls": 0, "seconds": 0.0}
    original = ocr_mod.OcrService.recognize

    def wrapped(self, image, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original(self, image, *args, **kwargs)
        finally:
            stats["calls"] += 1
            stats["seconds"] += time.perf_counter() - started

    ocr_mod.OcrService.recognize = wrapped
    try:
        yield stats
    finally:
        ocr_mod.OcrService.recognize = original


@contextlib.contextmanager
def _fast_disabled():
    """临时关闭快速复检，用慢路径量 before 基线。"""
    old = os.environ.get("YINZHAN_PDF_FAST_VERIFY")
    os.environ["YINZHAN_PDF_FAST_VERIFY"] = "0"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("YINZHAN_PDF_FAST_VERIFY", None)
        else:
            os.environ["YINZHAN_PDF_FAST_VERIFY"] = old


@contextlib.contextmanager
def _drop_export_op(*, black: bool):
    """故障注入：导出时漏掉第一个指定类型的矩形（模拟定位/绘制 bug）。"""
    from yinzhan.parsers import pdf_parser as pdf_parser_mod

    original = pdf_parser_mod._collect_ops
    dropped = {"done": False}

    def broken(doc, entities, decisions, warnings):
        ops = original(doc, entities, decisions, warnings)
        for page_no in sorted(ops):
            for op in list(ops[page_no]):
                if bool(op["black"]) is black:
                    ops[page_no].remove(op)
                    dropped["done"] = True
                    return ops
        return ops

    pdf_parser_mod._collect_ops = broken
    try:
        yield dropped
    finally:
        pdf_parser_mod._collect_ops = original


# ────────────────────────── 测试 ──────────────────────────

class PdfVerifyFastTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workdir = _fixtures_root()
        cls.ocr = ocr_mod.get_ocr()
        if not cls.ocr.available:
            raise unittest.SkipTest(f"OCR 不可用，无法构造扫描件：{cls.ocr.error}")
        registry.load_plugins()
        cls.scan_path = _scan_pdf(cls.workdir / "scan_fixture.pdf")
        cls.parser = registry.find_parser(cls.scan_path)
        cls.ctx = _ctx(cls.workdir)
        # 扫描件只解析一次；下面各用例复用（OCR 最贵）
        cls.doc = cls.parser.parse(cls.scan_path, cls.ctx)
        assert cls.doc.pages and all(p.ref.get("scanned") for p in cls.doc.pages), \
            "夹具未被识别为扫描页"
        cls.black_ents, cls.black_found = _entities_for(cls.doc, force_black=True)
        cls.ai_ents, cls.ai_found = _entities_for(cls.doc, force_black=False)
        cls.black_ents.append(_seal_entity())
        if not cls.black_found or not cls.ai_found:
            raise unittest.SkipTest("OCR 未识别出任何虚构测试值，跳过（环境相关）")
        if not any(e.decision and e.decision.strategy == "surrogate"
                   for e in cls.ai_ents):
            raise unittest.SkipTest("OCR 未识别出可换名的虚构值，跳过（环境相关）")

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("YINZHAN_WRITE_FIXTURES") != "1":
            shutil.rmtree(cls.workdir, ignore_errors=True)

    # ── ① 扫描件正常导出：verify 0 次 OCR、耗时显著下降 ──
    def test_01_scan_verify_fast_reduces_ocr(self):
        out = self.workdir / "scan_black_out.pdf"
        pipeline.export(self.doc, self.parser, self.black_ents, out, {"mode": "human"})

        with _fast_disabled(), _count_ocr() as before:
            started = time.perf_counter()
            slow = pipeline.verify(out, self.black_ents, self.parser, self.ctx,
                                   doc=self.doc, options={})
            slow_ms = (time.perf_counter() - started) * 1000.0

        with _count_ocr() as after:
            started = time.perf_counter()
            fast = pipeline.verify(out, self.black_ents, self.parser, self.ctx,
                                   doc=self.doc, options={})
            fast_ms = (time.perf_counter() - started) * 1000.0

        info = fast.get("fast_verify") or {}
        print(f"\n[P2] 扫描件 verify before: {before['calls']} 次 OCR / {slow_ms:.0f}ms；"
              f"after: {after['calls']} 次 OCR / {fast_ms:.0f}ms"
              f"（{len(self.doc.pages)} 页，before {before['calls'] / len(self.doc.pages):.1f} 次/页"
              f" → after {after['calls'] / len(self.doc.pages):.1f} 次/页）")

        self.assertTrue(slow.get("verified"))
        self.assertTrue(fast.get("verified"))
        self.assertGreater(before["calls"], 0, "慢路径应至少 OCR 一次（基线）")
        self.assertEqual(after["calls"], 0, "快速复检不应再跑 OCR")
        self.assertTrue(info.get("used"), "扫描件应走快速复检")
        self.assertEqual(info.get("ocr_calls"), 0)
        self.assertGreaterEqual(info.get("black_checked", 0), len(self.doc.pages))
        self.assertLess(fast_ms, slow_ms, "快速复检耗时应低于慢路径")
        self.assertEqual(fast.get("residual"), 0)
        self.assertEqual(fast.get("pdf_hidden"), {})
        self.assertEqual(fast.get("issues"), [])

    # ── ① 补充：替换矩形（换名）也走快速通道 ──
    def test_02_scan_replacement_verify_fast(self):
        out = self.workdir / "scan_ai_out.pdf"
        pipeline.export(self.doc, self.parser, self.ai_ents, out, {"mode": "ai"})
        with _count_ocr() as stats:
            started = time.perf_counter()
            info = pipeline.verify(out, self.ai_ents, self.parser, self.ctx,
                                   doc=self.doc, options={})
            elapsed_ms = (time.perf_counter() - started) * 1000.0
        detail = info.get("fast_verify") or {}
        print(f"[P2] 扫描件（含替换写回）verify: {stats['calls']} 次 OCR / {elapsed_ms:.0f}ms，"
              f"复核矩形 black={detail.get('black_checked')} "
              f"replacement={detail.get('replacement_checked')}")
        self.assertTrue(info.get("verified"))
        self.assertEqual(stats["calls"], 0)
        self.assertTrue(detail.get("used"))
        self.assertGreater(detail.get("replacement_checked", 0), 0,
                           "换名写回必须逐矩形做覆盖检查")
        self.assertEqual(info.get("residual"), 0)

    # ── ② 故障注入：漏涂黑必须被 verify 拦住 ──
    def test_03_fault_injection_unblacked(self):
        out = self.workdir / "scan_black_broken.pdf"
        with _drop_export_op(black=True) as dropped:
            pipeline.export(self.doc, self.parser, self.black_ents, out, {"mode": "human"})
        self.assertTrue(dropped["done"], "故障注入没有命中黑框，测试无效")
        with self.assertRaises(VerifyError) as caught:
            pipeline.verify(out, self.black_ents, self.parser, self.ctx,
                            doc=self.doc, options={})
        message = str(caught.exception)
        print(f"[P2] 漏涂黑故障注入：verify 拦截 → {message[:80]}")
        self.assertIn("未涂黑", message)

    # ── ② 补充：替换矩形漏盖（仍保留原图）必须被 verify 拦住 ──
    def test_04_fault_injection_replacement_untouched(self):
        out = self.workdir / "scan_ai_broken.pdf"
        with _drop_export_op(black=False) as dropped:
            pipeline.export(self.doc, self.parser, self.ai_ents, out, {"mode": "ai"})
        self.assertTrue(dropped["done"], "故障注入没有命中替换框，测试无效")
        with self.assertRaises(VerifyError) as caught:
            pipeline.verify(out, self.ai_ents, self.parser, self.ctx,
                            doc=self.doc, options={})
        message = str(caught.exception)
        print(f"[P2] 替换漏盖故障注入：verify 拦截 → {message[:80]}")
        self.assertIn("替换区域", message)

    # ── ③ 文本层 PDF：仍走慢路径，residual 检测有效 ──
    def test_05_text_pdf_residual_still_detected(self):
        text_path = _text_pdf(self.workdir / "text_fixture.pdf")
        text_doc = self.parser.parse(text_path, self.ctx)
        self.assertFalse(any(p.ref.get("scanned") for p in text_doc.pages),
                         "文本层 PDF 不应被标记为扫描页")
        ents, found = _entities_for(text_doc, force_black=True)
        self.assertTrue(found, "文本层 PDF 应能找到虚构测试值")
        out = self.workdir / "text_out.pdf"
        pipeline.export(text_doc, self.parser, ents, out, {"mode": "human"})

        # 文本层 PDF 不允许启用快速通道
        self.assertIsNone(
            self.parser.fast_verify(out, ents, self.ctx, doc=text_doc, options={}),
            "文本层 PDF 不应走扫描件快速复检")

        calls = {"n": 0}
        original_parse = self.parser.parse

        def counting_parse(path, ctx):
            calls["n"] += 1
            return original_parse(path, ctx)

        self.parser.parse = counting_parse
        try:
            info = pipeline.verify(out, ents, self.parser, self.ctx,
                                   doc=text_doc, options={})
            bad = self.workdir / "text_source_copy.pdf"
            shutil.copyfile(text_path, bad)
            with self.assertRaises(VerifyError) as caught:
                pipeline.verify(bad, ents, self.parser, self.ctx,
                                doc=text_doc, options={})
        finally:
            del self.parser.parse

        self.assertTrue(info.get("verified"))
        self.assertNotIn("fast_verify", info)
        self.assertGreaterEqual(calls["n"], 1, "文本层 PDF 应走完整重解析")
        message = str(caught.exception)
        print(f"[P2] 文本层 PDF 残留复检：verify 拦截 → {message[:80]}")
        self.assertIn("原文残留", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
