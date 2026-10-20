# -*- coding: utf-8 -*-
"""PDF 隐藏数据（附件/批注/表单/JS/缩略图）P0 测试。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_pdf_hidden.py
    cd app\\server && python -X utf8 ..\\tests\\test_pdf_hidden.py

设计说明
--------
* 纯离线、不联网；夹具默认生成到临时目录（tempfile.mkdtemp()）。
  想保留夹具人工查看时设置 ``YINZHAN_WRITE_FIXTURES=1``，会写到
  ``app/tests/fixtures/pdf_hidden/``（二进制夹具不入库，按需生成）。
* 夹具里的 PII 全部是虚构值：附件/批注/表单/JS 各带唯一 marker，
  导出后扫描成品 xref 对象与解压流，断言 marker 与 PII 均不存在。
* 覆盖：解析 meta 计数、默认剥离（ai/human）、keep_pdf_hidden 行为、
  keep 不能绕过文本残留复检、解析器侧复检阻断、缩略图剥离、无隐藏数据 PDF 不回归。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import fitz  # PyMuPDF  # noqa: E402

from yinzhan import pipeline, registry                   # noqa: E402
from yinzhan.jobs import _load_rules                     # noqa: E402
from yinzhan.ocr import get_ocr                          # noqa: E402
from yinzhan.parsers import pdf_parser as pdf_parser_mod  # noqa: E402
from yinzhan.udm import ParseContext, VerifyError        # noqa: E402

SAMPLES = REPO / "samples"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "pdf_hidden"
SAMPLE_PDF = SAMPLES / "委托代理合同.pdf"

# 虚构 PII / marker（不要替换成真实数据）
BODY_PHONE = "13800138000"
ANNOT_PHONE = "13700137000"
FREE_ANNOT_PHONE = "13600136000"
WIDGET_PHONE = "13500135000"
ATTACH_PHONE = "13900139000"
MARK_ATTACH = "ATTACHSECRET-MARKER-9f3a"
MARK_ANNOT = "ANNOTSECRET-MARKER-7c1d"
MARK_WIDGET = "WIDGETSECRET-MARKER-5b2e"
MARK_JS = "JSSECRET-MARKER-3a4f"
MARK_THUMB = "THUMBSECRET-MARKER-8d6b"
ALL_MARKERS = (MARK_ATTACH, MARK_ANNOT, MARK_WIDGET, MARK_JS, MARK_THUMB)


def _fixtures_root() -> Path:
    if os.environ.get("YINZHAN_WRITE_FIXTURES") == "1":
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        return FIXTURE_DIR
    return Path(tempfile.mkdtemp(prefix="yinzhan_pdf_hidden_"))


class Fixtures:
    root: Path | None = None

    @classmethod
    def get(cls) -> Path:
        if cls.root is None:
            cls.root = _fixtures_root()
        return cls.root

    @classmethod
    def hidden(cls) -> Path:
        """主夹具：附件/批注/表单/JS/缩略图 + 可见 PII 正文。"""
        path = cls.get() / "pdf_hidden_full.pdf"
        if not path.exists():
            _build_hidden_pdf(path, sensitive_hidden=True)
        return path

    @classmethod
    def hidden_clean(cls) -> Path:
        """隐藏数据不含 PII 的夹具：用于验证 keep_pdf_hidden 放行路径。"""
        path = cls.get() / "pdf_hidden_clean.pdf"
        if not path.exists():
            _build_hidden_pdf(path, sensitive_hidden=False)
        return path

    @classmethod
    def annot_over_text(cls) -> Path:
        """批注盖在正文手机号上的夹具（验证删注释/redaction 顺序）。"""
        path = cls.get() / "pdf_annot_over_text.pdf"
        if not path.exists():
            _build_annot_over_text(path)
        return path

    @classmethod
    def hidden_with_thumb(cls) -> Path:
        path = cls.get() / "pdf_hidden_thumb.pdf"
        if not path.exists():
            _build_thumb_pdf(path)
        return path


# ────────────────────────── 夹具构造 ──────────────────────────

def _new_doc():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "委托方：张三", fontsize=12)
    page.insert_text((72, 112), f"手机号 {BODY_PHONE}，日期 2026-10-20", fontsize=12)
    page.insert_text((72, 140), "正文：双方同意按本协议执行。", fontsize=12)
    return doc, page


def _add_js_open_action(doc: fitz.Document, marker: str) -> None:
    js_xref = doc.get_new_xref()
    doc.update_object(js_xref, "<< /S /JavaScript /JS (app.alert('js')) >>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js_xref} 0 R")


def _build_hidden_pdf(path: Path, *, sensitive_hidden: bool = True) -> Path:
    """构造含附件/批注/表单/JS 的 PDF；sensitive_hidden=True 时每类都带虚构 PII。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc, page = _new_doc()

    attach_content = (
        f"{MARK_ATTACH} 附件内容 {ATTACH_PHONE}".encode("utf-8")
        if sensitive_hidden else f"{MARK_ATTACH} 内部资料 无个人信息".encode("utf-8")
    )
    doc.embfile_add("secret.txt" if sensitive_hidden else "notes.txt",
                    attach_content,
                    filename="secret.txt" if sensitive_hidden else "notes.txt")

    annot_text = (f"{MARK_ANNOT} 批注：王五 {ANNOT_PHONE}" if sensitive_hidden
                  else f"{MARK_ANNOT} 审阅：请核对第三段")
    note = page.add_text_annot((100, 200), annot_text)
    note.update()
    free_text = (f"{MARK_ANNOT}-FREETEXT 自由批注 {FREE_ANNOT_PHONE}"
                 if sensitive_hidden else f"{MARK_ANNOT}-FREETEXT 请复核")
    free = page.add_freetext_annot(fitz.Rect(100, 300, 360, 330), free_text, fontsize=10)
    free.update()

    widget = fitz.Widget()
    widget.field_name = "phone"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_value = f"{MARK_WIDGET} {WIDGET_PHONE}" if sensitive_hidden else f"{MARK_WIDGET} 同意"
    widget.rect = fitz.Rect(100, 400, 360, 428)
    widget.text_fontsize = 10
    page.add_widget(widget)

    _add_js_open_action(doc, MARK_JS)
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return path


def _build_annot_over_text(path: Path) -> Path:
    """批注正好盖在正文手机号上。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((60, 120), f"持卡人手机号 {BODY_PHONE}", fontsize=14)
    annot = page.add_freetext_annot(fitz.Rect(55, 100, 340, 135),
                                    f"批注 {ANNOT_PHONE} 覆盖正文", fontsize=11)
    annot.update()
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return path


def _build_thumb_pdf(path: Path) -> Path:
    """页面带 /Thumb 缩略图（可能保留未脱敏画面）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc, page = _new_doc()
    page.insert_text((72, 170), MARK_THUMB, fontsize=11)
    pix = page.get_pixmap(dpi=18)
    thumb_xref = doc.get_new_xref()
    doc.update_object(thumb_xref, "<<>>")
    doc.update_stream(thumb_xref, pix.tobytes("png"))
    doc.xref_set_key(page.xref, "Thumb", f"{thumb_xref} 0 R")
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return path


# ────────────────────────── 扫描工具 ──────────────────────────

def _ctx() -> ParseContext:
    return ParseContext(ocr=get_ocr(), workdir=str(Fixtures.get()))


def _parser():
    registry.load_plugins()
    return registry.find_parser(Fixtures.hidden())


def _detect(doc):
    rules, _pack, _index = _load_rules(doc.fmt)
    det = pipeline.detect(doc, rules, "standard")
    pipeline.decide(det.entities, det.rules, pipeline.Session())
    return det


def _raw_blob(path: Path) -> bytes:
    """把成品所有 xref 对象 + 解压后的流拼起来，用于 marker 残留扫描。"""
    chunks: list[bytes] = []
    with fitz.open(str(path)) as pdf:
        for xref in range(1, pdf.xref_length()):
            try:
                chunks.append((pdf.xref_object(xref, compressed=False) or "").encode("utf-8", "replace"))
            except Exception:
                pass
            try:
                stream = pdf.xref_stream(xref)
                if stream:
                    chunks.append(stream)
            except Exception:
                pass
    return b"\n".join(chunks)


def _assert_markers_absent(testcase: unittest.TestCase, path: Path, markers=ALL_MARKERS) -> None:
    blob = _raw_blob(path)
    text = blob.decode("utf-8", "replace")
    for marker in markers:
        testcase.assertNotIn(marker, text, f"成品仍残留隐藏数据 marker：{marker}")


def _output_hidden_stats(path: Path) -> dict:
    with fitz.open(str(path)) as pdf:
        return {
            "embedded_files": int(pdf.embfile_count()),
            "embedded_file_names": list(pdf.embfile_names() or []),
            "pdf_annotations": sum(len(list(page.annots() or [])) for page in pdf),
            "pdf_forms": sum(len(list(page.widgets() or [])) for page in pdf),
            "pdf_js": bool(pdf_parser_mod._pdf_has_js(pdf)),
            "is_form_pdf": bool(pdf.is_form_pdf),
        }


# ────────────────────────── 1. 解析统计 ──────────────────────────

class TestParseHiddenMeta(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        cls.parser = registry.find_parser(Fixtures.hidden())
        cls.doc = cls.parser.parse(Fixtures.hidden(), _ctx())

    def test_meta_fields(self):
        meta = self.doc.meta
        self.assertEqual(meta.get("embedded_files"), 1)
        self.assertEqual(meta.get("embedded_file_names"), ["secret.txt"])
        self.assertGreaterEqual(int(meta.get("pdf_annotations") or 0), 2)
        self.assertEqual(int(meta.get("pdf_forms") or 0), 1)
        self.assertTrue(meta.get("pdf_js"))
        types = meta.get("pdf_annotation_types") or {}
        self.assertIn("Text", types)
        self.assertIn("FreeText", types)

    def test_warning(self):
        joined = " ".join(self.doc.warnings)
        self.assertIn("PDF 隐藏数据", joined)
        self.assertIn("keep_pdf_hidden", joined)
        for word in ("附件", "批注", "表单", "JavaScript"):
            self.assertIn(word, joined)

    def test_meta_json_serializable(self):
        # doc.meta 会进 API/审计相关代码，必须可 JSON 序列化
        json.dumps({k: v for k, v in self.doc.meta.items()}, ensure_ascii=False)

    def test_no_content_leak_in_meta(self):
        """统计里不记批注/附件内容，只记名称与数量/类型。"""
        blob = json.dumps(self.doc.meta, ensure_ascii=False)
        for marker in (MARK_ANNOT, MARK_WIDGET, MARK_JS):
            self.assertNotIn(marker, blob)


# ────────────────────────── 2. 默认剥离（ai/human） ──────────────────────────

class TestDefaultStrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        cls.parser = registry.find_parser(Fixtures.hidden())
        cls.doc = cls.parser.parse(Fixtures.hidden(), _ctx())
        cls.det = _detect(cls.doc)
        cls.outputs: dict[str, Path] = {}
        for mode in ("ai", "human"):
            out = Fixtures.get() / f"pdf_hidden_out_{mode}.pdf"
            pipeline.export(cls.doc, cls.parser, cls.det.entities, out, {"mode": mode})
            cls.outputs[mode] = out

    def test_detect_visible_pii(self):
        values = {e.value for e in self.det.entities}
        self.assertIn(BODY_PHONE, values)

    def test_output_hidden_is_zero(self):
        for mode, path in self.outputs.items():
            stats = _output_hidden_stats(path)
            self.assertEqual(stats["embedded_files"], 0, f"{mode} 附件未清")
            self.assertEqual(stats["embedded_file_names"], [], f"{mode} 附件名未清")
            self.assertEqual(stats["pdf_annotations"], 0, f"{mode} 批注未清")
            self.assertEqual(stats["pdf_forms"], 0, f"{mode} 表单控件未清")
            self.assertFalse(stats["pdf_js"], f"{mode} JavaScript 未清")
            self.assertFalse(stats["is_form_pdf"], f"{mode} AcroForm 未清")

    def test_output_markers_gone(self):
        for mode, path in self.outputs.items():
            _assert_markers_absent(self, path)

    def test_output_pii_gone_from_text(self):
        for mode, path in self.outputs.items():
            with fitz.open(str(path)) as pdf:
                text = "".join(page.get_text() for page in pdf)
            for pii in (BODY_PHONE, ANNOT_PHONE, FREE_ANNOT_PHONE, WIDGET_PHONE, ATTACH_PHONE):
                self.assertNotIn(pii, text, f"{mode} 成品文本仍含 {pii}")

    def test_pipeline_verify_passes(self):
        for mode, path in self.outputs.items():
            try:
                ver = pipeline.verify(path, self.det.entities, self.parser, _ctx())
            except VerifyError as exc:
                self.fail(f"{mode} verify 失败：{exc}")
            self.assertTrue(ver.get("verified"))
            self.assertEqual(ver.get("residual"), 0)
            self.assertEqual(ver.get("pdf_hidden"), {})

    def test_export_warning(self):
        out = Fixtures.get() / "pdf_hidden_out_warn.pdf"
        res = pipeline.export(self.doc, self.parser, self.det.entities, out,
                              {"mode": "human"})
        joined = " ".join(res.warnings)
        self.assertIn("已剥离 PDF 隐藏数据", joined)
        self.assertIn("附件 1 个", joined)
        self.assertIn("批注 2 条", joined)
        self.assertIn("表单字段 1 个", joined)

    def test_doc_extra_hidden(self):
        info = (self.doc.extra or {}).get("pdf_hidden") or {}
        self.assertEqual(info.get("kept"), False)
        self.assertEqual((info.get("removed") or {}).get("embedded_files"), 1)
        self.assertEqual((info.get("removed") or {}).get("pdf_forms"), 1)


# ────────────────────────── 3. keep_pdf_hidden 行为 ──────────────────────────

class TestKeepOption(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        cls.parser = registry.find_parser(Fixtures.hidden_clean())
        cls.doc = cls.parser.parse(Fixtures.hidden_clean(), _ctx())
        cls.det = _detect(cls.doc)

    def _export_keep(self) -> tuple[Path, object]:
        out = Fixtures.get() / "pdf_hidden_keep.pdf"
        res = pipeline.export(self.doc, self.parser, self.det.entities, out,
                              {"mode": "human", "keep_pdf_hidden": True})
        return out, res

    def test_keep_retains_hidden_data(self):
        out, res = self._export_keep()
        stats = _output_hidden_stats(out)
        self.assertEqual(stats["embedded_files"], 1)
        self.assertGreaterEqual(stats["pdf_annotations"], 1)
        self.assertEqual(stats["pdf_forms"], 1)
        self.assertTrue(stats["pdf_js"])
        joined = " ".join(res.warnings)
        self.assertIn("keep_pdf_hidden=True", joined)
        self.assertIn("保留", joined)

    def test_keep_verify_requires_option(self):
        out, _res = self._export_keep()
        with self.assertRaises(VerifyError) as caught:
            pipeline.verify(out, self.det.entities, self.parser, _ctx())
        self.assertIn("PDF 隐藏数据未清理", str(caught.exception))
        ver = pipeline.verify(out, self.det.entities, self.parser, _ctx(),
                              options={"keep_pdf_hidden": True})
        self.assertTrue(ver.get("verified"))
        self.assertEqual(int((ver.get("pdf_hidden") or {}).get("embedded_files") or 0), 1)

    def test_keep_cannot_bypass_text_residual(self):
        """保留敏感隐藏数据时，若原文同时出现在 PDF 文本层，verify 仍必须拦截。"""
        parser = registry.find_parser(Fixtures.hidden())
        doc = parser.parse(Fixtures.hidden(), _ctx())
        det = _detect(doc)
        out = Fixtures.get() / "pdf_hidden_keep_sensitive.pdf"
        pipeline.export(doc, parser, det.entities, out,
                        {"mode": "human", "keep_pdf_hidden": True})
        with self.assertRaises(VerifyError):
            pipeline.verify(out, det.entities, parser, _ctx(),
                            options={"keep_pdf_hidden": True})


# ────────────────────────── 4. 解析器侧复检 / 缩略图 / 顺序 ──────────────────────────

class TestSelfCheckAndThumb(unittest.TestCase):
    def setUp(self):
        registry.load_plugins()

    def test_self_check_blocks_when_strip_fails(self):
        """模拟清理失效：导出后复检必须 ExportError，绝不静默放行。"""
        parser = registry.find_parser(Fixtures.hidden())
        doc = parser.parse(Fixtures.hidden(), _ctx())
        det = _detect(doc)
        original = pdf_parser_mod._strip_pdf_hidden
        pdf_parser_mod._strip_pdf_hidden = lambda pdf, warnings: {  # type: ignore[assignment]
            "embedded_files": 0, "pdf_annotations": 0, "pdf_forms": 0,
            "pdf_js": 0, "thumbnails": 0}
        try:
            out = Fixtures.get() / "pdf_hidden_selfcheck.pdf"
            with self.assertRaises(Exception) as caught:
                pipeline.export(doc, parser, det.entities, out, {"mode": "human"})
            self.assertIn("复检失败", str(caught.exception))
        finally:
            pdf_parser_mod._strip_pdf_hidden = original  # type: ignore[assignment]

    def test_thumbnail_stripped(self):
        parser = registry.find_parser(Fixtures.hidden_with_thumb())
        doc = parser.parse(Fixtures.hidden_with_thumb(), _ctx())
        out = Fixtures.get() / "pdf_hidden_thumb_out.pdf"
        pipeline.export(doc, parser, [], out, {"mode": "human"})
        with fitz.open(str(out)) as pdf:
            page = pdf[0]
            typ, _value = pdf.xref_get_key(page.xref, "Thumb")
            self.assertIn(typ, ("null", None), f"页面缩略图未删除：{typ}")
        _assert_markers_absent(self, out, (MARK_THUMB,))

    def test_annotation_over_text_removed_and_redacted(self):
        parser = registry.find_parser(Fixtures.annot_over_text())
        doc = parser.parse(Fixtures.annot_over_text(), _ctx())
        det = _detect(doc)
        out = Fixtures.get() / "pdf_hidden_annot_over_text.pdf"
        pipeline.export(doc, parser, det.entities, out, {"mode": "human"})
        stats = _output_hidden_stats(out)
        self.assertEqual(stats["pdf_annotations"], 0)
        with fitz.open(str(out)) as pdf:
            text = "".join(page.get_text() for page in pdf)
        self.assertNotIn(BODY_PHONE, text)
        _assert_markers_absent(self, out, (MARK_ANNOT,))


# ────────────────────────── 5. 既有 PDF 不回归 ──────────────────────────

class TestExistingPdfUnaffected(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SAMPLE_PDF.is_file():
            raise unittest.SkipTest("缺少 samples/委托代理合同.pdf")
        registry.load_plugins()
        cls.parser = registry.find_parser(SAMPLE_PDF)
        cls.doc = cls.parser.parse(SAMPLE_PDF, _ctx())
        cls.det = _detect(cls.doc)

    def test_no_hidden_data(self):
        meta = self.doc.meta
        self.assertEqual(int(meta.get("embedded_files") or 0), 0)
        self.assertEqual(int(meta.get("pdf_annotations") or 0), 0)
        self.assertEqual(int(meta.get("pdf_forms") or 0), 0)
        self.assertFalse(meta.get("pdf_js"))
        joined = " ".join(self.doc.warnings)
        self.assertNotIn("PDF 隐藏数据", joined)

    def test_export_still_ok(self):
        out = Fixtures.get() / "sample_contract_export.pdf"
        res = pipeline.export(self.doc, self.parser, self.det.entities, out, {"mode": "human"})
        self.assertGreater(res.size, 0)
        ver = pipeline.verify(out, self.det.entities, self.parser, _ctx())
        self.assertTrue(ver.get("verified"))
        self.assertEqual(ver.get("residual"), 0)
        self.assertEqual(ver.get("pdf_hidden"), {})


# ────────────────────────── main ──────────────────────────

def main() -> int:
    print("数据隐站 · PDF 隐藏数据 P0 测试（附件/批注/表单/JS/缩略图）")
    print("-" * 64)
    print(f"PyMuPDF={fitz.__doc__.splitlines()[0] if fitz.__doc__ else '?'} "
          f"fixtures={FIXTURE_DIR if os.environ.get('YINZHAN_WRITE_FIXTURES') == '1' else 'tempdir'}")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("-" * 64)
    if result.wasSuccessful():
        print(f"通过：{result.testsRun} 项（skipped={len(result.skipped)}）")
        return 0
    print(f"失败：failures={len(result.failures)} errors={len(result.errors)}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover
        import traceback

        traceback.print_exc()
        sys.exit(3)
