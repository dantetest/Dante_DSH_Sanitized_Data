# -*- coding: utf-8 -*-
"""AI 回件还原（restore）测试：映射/变体/各格式/API/审计。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_restore.py

设计说明：
- 纯离线、不写仓库：数据目录/成品目录全部指到系统临时目录（先设环境变量，
  再 import yinzhan，因为 config 在 import 时解析路径）。
- 映射用 ``vault.save_mapping`` 当场写入临时映射库，测完随数据目录删除。
- OCR 用例（图片/扫描件）在 OCR 不可用时自动跳过。
- API 用例用 ``api.serve`` 起临时端口，走真实 HTTP（含上传/还原/下载/审计）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

TMP_BASE = Path(os.environ.get("YINZHAN_TEST_TMP") or tempfile.gettempdir())
TMP = TMP_BASE / f"yz_restore_{os.getpid()}_{os.urandom(4).hex()}"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["YINZHAN_DATA_DIR"] = str(TMP / "data")
os.environ["YINZHAN_OUTPUT_DIR"] = str(TMP / "out")
os.environ["YINZHAN_DESKTOP"] = "1"

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import audit, restore, vault            # noqa: E402
from yinzhan.config import DATA_DIR, OUTPUT_DIR      # noqa: E402

JOB = "j_test_restore"
JOB2 = "j_test_restore_other"
JOB3 = "j_test_variants"
JOB4 = "j_test_low_quality"
MAP = {
    "PERSON": {"张伟": "陈志远", "李娜": "王雨欣"},
    "ORG": {"北京华信科技有限公司": "上海明远数据科技有限公司"},
    "AMOUNT": {"¥12345.67": "¥10000.00", "¥1234.57": "¥1000.00"},
    "PHONE": {"13800138000": "13900001111"},
    "ID_CARD": {"11010119900307561X": "110101199003075612"},
    "USCC": {"91110108MA01ABCD3X": "91310115MA01ABCD3Y"},
    "ADDRESS": {"北京市海淀区中关村大街1号": "上海市浦东新区世纪大道100号"},
    "RECORD_NO": {"住院号：ZY20250001": "住院号：WR000011"},
    "EMAIL": {"zhangwei@example.com": "chenzhiyuan@example.cn"},
}

# 带标签前缀的结构化值：AI 回件常只抄核心号码、省略“账号:/住院号：”标签。
MAP3 = {
    "ACCOUNT": {"账号:5105 0143 6372 0000 0355": "账号:6679 6635 6060 6760 3332"},
    "RECORD_NO": {"住院号：ZY20250001": "住院号：WR000011"},
    "CASE_NO": {"[2024 ]沪建纬(蓉)民代字第593号": "[2024 ]沪建纬(蓉)民代字第81914号"},
}

FAKES = [
    "陈志远", "上海明远数据科技有限公司", "¥10000.00", "13900001111",
    "110101199003075612", "91310115MA01ABCD3Y", "上海市浦东新区世纪大道100号",
    "住院号：WR000011", "chenzhiyuan@example.cn",
]

SAMPLE_TEXT = (
    "甲方：陈志远，乙方：上海明远数据科技有限公司。\n"
    "合同金额：¥10000.00（大写：壹万元整），电话 13900001111。\n"
    "身份证 110101199003075612，统一社会信用代码 91310115MA01ABCD3Y。\n"
    "地址：上海市浦东新区世纪大道100号，住院号：WR000011。\n"
    "邮箱：chenzhiyuan@example.cn。\n"
)


def _fake_docx(path: Path) -> None:
    from docx import Document

    doc = Document()
    doc.add_paragraph("测试合同")
    p = doc.add_paragraph()
    p.add_run("甲方：陈")
    p.add_run("志远")                      # 故意拆成两个 run，验证跨 run 合并
    doc.add_paragraph("乙方：上海明远数据科技有限公司")
    doc.add_paragraph("合同金额：¥10000.00；电话 13900001111")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "身份证"
    table.cell(0, 1).text = "110101199003075612"
    table.cell(1, 0).text = "住院号"
    table.cell(1, 1).text = "住院号：WR000011"
    section = doc.sections[0]
    section.header.paragraphs[0].text = "页眉：陈志远"
    section.footer.paragraphs[0].text = "页脚：13900001111"
    doc.save(str(path))


def _fake_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "姓名"
    ws["B1"] = "陈志远"
    ws["A2"] = "机构"
    ws["B2"] = "上海明远数据科技有限公司"
    ws["A3"] = "金额"
    ws["B3"] = "¥10000.00"
    wb.save(str(path))


def _fake_pdf(path: Path) -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    lines = [
        "甲方：陈志远",
        "乙方：上海明远数据科技有限公司",
        "金额：¥10000.00；电话 13900001111",
        "地址：上海市浦东新区世纪大道100号",
    ]
    for i, line in enumerate(lines):
        page.insert_text((72, 100 + i * 26), line, fontsize=12,
                         fontname="china-s")
    doc.save(str(path))
    doc.close()


def _fake_image(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 260), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    font_path = next((p for p in (r"C:\Windows\Fonts\msyh.ttc",
                                  r"C:\Windows\Fonts\simsun.ttc") if Path(p).exists()), None)
    font = ImageFont.truetype(font_path, 30) if font_path else None
    draw.text((40, 40), "姓名：陈志远", fill=(20, 20, 20), font=font)
    draw.text((40, 100), "电话：13900001111", fill=(20, 20, 20), font=font)
    draw.text((40, 160), "金额：¥10000.00", fill=(20, 20, 20), font=font)
    image.save(path)


def _ocr_ok() -> bool:
    try:
        from yinzhan.ocr import get_ocr
        return bool(get_ocr().available)
    except Exception:
        return False


def _text(path: Path) -> str:
    return path.read_text("utf-8")


def setUpModule() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    vault.save_mapping(JOB, MAP, {"name": "测试合同.docx", "seal": "20260101-0001"})
    vault.save_mapping(JOB2, {"PERSON": {"张三": "李四"}},
                       {"name": "另一份.docx", "seal": "20260101-0002"})
    vault.save_mapping(JOB3, MAP3, {"name": "结构化变体.txt", "seal": "20260101-0003"})
    vault.save_mapping(JOB4, {"PERSON": {"公章": "叶雅"}},
                       {"name": "低质量映射.txt", "seal": "20260101-0004"})


def tearDownModule() -> None:
    shutil.rmtree(TMP, ignore_errors=True)


class TestMappingAndScan(unittest.TestCase):
    def test_mapping_info(self):
        info = restore.load_mapping_info(JOB)
        self.assertEqual(info.name, "测试合同.docx")
        self.assertEqual(info.seal, "20260101-0001")
        self.assertEqual(info.count, 11)
        self.assertEqual(info.by_type["PERSON"], 2)
        self.assertEqual(restore.type_label("USCC"), "统一社会信用代码")
        self.assertEqual(restore.type_label("PERSON"), "人名")

    def test_variants(self):
        amount = restore._variants_for("¥10000.00", "AMOUNT")
        self.assertIn("¥10000.00", amount)
        self.assertIn("￥10000.00", amount)
        self.assertIn("¥10,000.00", amount)
        self.assertIn("10000.00", amount)
        idcard = restore._variants_for("11010119900307561X", "ID_CARD")
        self.assertIn("11010119900307561x", idcard)
        # 核心号码不再塞进“同一变体”（否则替换会带错误前缀）；改由派生 Pair 处理
        record_variants = restore._variants_for("住院号：WR000011", "RECORD_NO")
        self.assertNotIn("WR000011", record_variants)
        derived = [p.fake for p in restore.load_mapping_info(JOB).derived]
        self.assertIn("WR000011", derived)

    def test_structured_whitespace_and_brackets(self):
        case_no = restore._variants_for("[2024 ]沪建纬(蓉)民代字第593号", "CASE_NO")
        self.assertIn("[2024]沪建纬(蓉)民代字第593号", case_no)   # 去空格写法
        account = restore._variants_for("账号:6679 6635 6060 6760 3332", "ACCOUNT")
        self.assertIn("66796635606067603332", account)              # 纯数字写法

    def test_labeled_value_restore(self):
        """账号/住院号只抄核心号码、案号少一个空格时，仍能正确还原且不重复前缀。"""
        info = restore.load_mapping_info(JOB3)
        self.assertEqual(info.count, 3)                 # 派生写法不增加实体数
        self.assertGreater(len(info.all_pairs), info.count)
        src = TMP / "变体回件.txt"
        src.write_text(
            "收款账号：6679 6635 6060 6760 3332\n"
            "住院号 WR000011\n"
            "文书编号 [2024]沪建纬(蓉)民代字第81914号\n", "utf-8")
        report = restore.restore_path(src, src.name, info, out_dir=OUTPUT_DIR)
        text = report.artifacts[0].path.read_text("utf-8")
        self.assertIn("收款账号：5105 0143 6372 0000 0355", text)
        self.assertIn("住院号 ZY20250001", text)
        self.assertNotIn("住院号 住院号", text)
        self.assertNotIn("账号:账号:", text)
        self.assertIn("[2024 ]沪建纬(蓉)民代字第593号", text)
        self.assertNotIn("81914", text)
        self.assertNotIn("WR000011", text)
        # 报告展示回原始实体，而不是派生写法
        joined = json.dumps(report.public(), ensure_ascii=False)
        self.assertIn("账号:6679", joined)
        self.assertIn("账号:5105", joined)

    def test_low_quality_mapping_warning(self):
        info = restore.load_mapping_info(JOB4)
        self.assertTrue(info.quality_warnings)
        self.assertIn("OCR", info.quality_warnings[0])

    def test_no_cascade(self):
        pairs = [restore.Pair(fake="BC", orig="ABC", etype="OTHER")]
        index = restore._Index(pairs)
        out, events = restore.scan_replace("BC", index)
        self.assertEqual(out, "ABC")          # 不会把还原结果再扫一遍
        self.assertEqual(len(events), 1)

    def test_longest_first(self):
        pairs = [
            restore.Pair(fake="上海明远", orig="短机构", etype="ORG"),
            restore.Pair(fake="上海明远数据科技有限公司", orig="北京华信科技有限公司",
                         etype="ORG"),
        ]
        index = restore._Index(pairs)
        out, events = restore.scan_replace("上海明远数据科技有限公司", index)
        self.assertEqual(out, "北京华信科技有限公司")
        self.assertEqual(len(events), 1)

    def test_list_mappings(self):
        items = restore.list_mapping_infos()
        ids = [x["job_id"] for x in items]
        self.assertIn(JOB, ids)
        job = next(x for x in items if x["job_id"] == JOB)
        self.assertGreaterEqual(job["pairs"], 9)


class TestFormats(unittest.TestCase):
    def setUp(self):
        self.out = OUTPUT_DIR

    def _restore(self, src: Path, name: str | None = None):
        info = restore.load_mapping_info(JOB)
        return restore.restore_path(src, name or src.name, info, out_dir=self.out)

    def test_plain_text(self):
        src = TMP / "回件.txt"
        src.write_text(SAMPLE_TEXT, "utf-8")
        report = self._restore(src)
        self.assertEqual(report.strategy, "text/plain")
        text = _text(report.artifacts[0].path)
        for fake in FAKES:
            self.assertNotIn(fake, text, fake)
        self.assertIn("张伟", text)
        self.assertIn("北京华信科技有限公司", text)
        self.assertIn("¥12345.67", text)
        self.assertIn("13800138000", text)
        self.assertIn("住院号：ZY20250001", text)
        self.assertEqual(report.replacements["陈志远"]["orig"], "张伟")

    def test_plain_text_gb18030(self):
        src = TMP / "回件_中文编码.txt"
        src.write_bytes("姓名：陈志远".encode("gb18030"))
        report = self._restore(src)
        self.assertIn("姓名：张伟", report.artifacts[0].path.read_bytes()
                      .decode("gb18030"))

    def test_csv(self):
        src = TMP / "回件.csv"
        src.write_text("字段,值\n姓名,陈志远\n机构,上海明远数据科技有限公司\n",
                       "utf-8")
        report = self._restore(src)
        text = _text(report.artifacts[0].path)
        self.assertNotIn("陈志远", text)
        self.assertIn("张伟", text)
        self.assertIn("北京华信科技有限公司", text)

    def test_json(self):
        src = TMP / "回件.json"
        src.write_text(json.dumps({"甲方": "陈志远",
                                   "nested": [{"机构": "上海明远数据科技有限公司"}],
                                   "金额": "¥10000.00"},
                                  ensure_ascii=False), "utf-8")
        report = self._restore(src)
        data = json.loads(_text(report.artifacts[0].path))
        self.assertEqual(data["甲方"], "张伟")
        self.assertEqual(data["nested"][0]["机构"], "北京华信科技有限公司")
        self.assertEqual(data["金额"], "¥12345.67")

    def test_docx(self):
        src = TMP / "回件.docx"
        _fake_docx(src)
        report = self._restore(src)
        path = report.artifacts[0].path
        self.assertTrue(path.is_file())
        from docx import Document
        doc = Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs)
        text += "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
        text += "\n".join(p.text for p in doc.sections[0].header.paragraphs)
        text += "\n".join(p.text for p in doc.sections[0].footer.paragraphs)
        for fake in ("陈志远", "上海明远数据科技有限公司", "¥10000.00",
                     "13900001111", "110101199003075612", "WR000011"):
            self.assertNotIn(fake, text, fake)
        self.assertIn("甲方：张伟", text)
        self.assertIn("页眉：张伟", text)

    def test_xlsx(self):
        src = TMP / "回件.xlsx"
        _fake_xlsx(src)
        report = self._restore(src)
        from openpyxl import load_workbook
        wb = load_workbook(str(report.artifacts[0].path))
        ws = wb.active
        values = [str(c.value) for row in ws.iter_rows() for c in row if c.value]
        joined = "|".join(values)
        self.assertNotIn("陈志远", joined)
        self.assertIn("张伟", joined)
        self.assertIn("北京华信科技有限公司", joined)
        self.assertIn("¥12345.67", joined)

    def test_pdf_text_layer(self):
        src = TMP / "回件.pdf"
        _fake_pdf(src)
        report = self._restore(src)
        import fitz
        doc = fitz.open(str(report.artifacts[0].path))
        text = "\n".join(doc[i].get_text("text") for i in range(doc.page_count))
        doc.close()
        for fake in ("陈志远", "上海明远数据科技有限公司", "¥10000.00",
                     "13900001111"):
            self.assertNotIn(fake, text, fake)
        self.assertIn("张伟", text)
        self.assertIn("北京华信科技有限公司", text)
        self.assertIn("¥12345.67", text)

    def test_xlsx_numeric_amount(self):
        """AI 常把金额写成纯数字单元格（无 ¥/元），也要按金额映射还原。"""
        from openpyxl import Workbook, load_workbook
        src = TMP / "回件_数值.xlsx"
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "金额1"
        ws["B1"] = 10000.0          # 假值 ¥10000.00 的数字写法
        ws["A2"] = "金额2"
        ws["B2"] = 1000             # 假值 ¥1000.00 的数字写法
        ws["A3"] = "合计（公式）"
        ws["B3"] = "=B1+B2"         # 公式单元格不应被文本替换破坏
        wb.save(str(src))
        report = self._restore(src)
        out = report.artifacts[0].path
        ws2 = load_workbook(str(out), data_only=False).active
        self.assertAlmostEqual(ws2["B1"].value, 12345.67, places=2)
        self.assertAlmostEqual(ws2["B2"].value, 1234.57, places=2)
        self.assertEqual(ws2["B3"].value, "=B1+B2")
        self.assertTrue(any("数值单元格" in w for w in report.warnings))

    def test_no_match(self):
        src = TMP / "无关文件.txt"
        src.write_text("这份文件里没有任何映射中的假值。", "utf-8")
        info = restore.load_mapping_info(JOB)
        with self.assertRaises(restore.RestoreError) as ctx:
            restore.restore_path(src, src.name, info, out_dir=self.out)
        self.assertEqual(ctx.exception.code, "no_match")
        left = [p for p in self.out.glob("无关文件_还原*")]
        self.assertEqual(left, [])

    def test_unknown_binary_unsupported(self):
        binary = TMP / "回件.bin"
        binary.write_bytes(b"\xff\xfe\x00\x01\x02\xff" * 8)
        self.assertEqual(restore.detect_format(binary), "unknown")
        info = restore.load_mapping_info(JOB)
        with self.assertRaises(restore.RestoreError) as ctx:
            restore.restore_path(binary, binary.name, info, out_dir=self.out)
        self.assertEqual(ctx.exception.code, "unsupported_format")

    @unittest.skipUnless(_ocr_ok(), "本机 OCR 不可用，跳过图片/扫描件用例")
    def test_image(self):
        src = TMP / "回件.png"
        _fake_image(src)
        report = self._restore(src)
        main = next(a for a in report.artifacts if a.kind == "main")
        self.assertTrue(main.path.is_file())
        texts = [a for a in report.artifacts if a.kind == "text"]
        if texts:
            content = _text(texts[0].path)
            self.assertIn("张伟", content)
            self.assertNotIn("陈志远", content)

    @unittest.skipUnless(_ocr_ok(), "本机 OCR 不可用，跳过图片/扫描件用例")
    def test_scanned_pdf(self):
        import fitz
        png = TMP / "scan.png"
        _fake_image(png)
        pdf = TMP / "扫描件.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=400)
        page.insert_image(fitz.Rect(0, 0, 595, 400), filename=str(png))
        doc.save(str(pdf))
        doc.close()
        report = self._restore(pdf)
        self.assertEqual(report.scan_pages, 1)
        main = next(a for a in report.artifacts if a.kind == "main")
        content = _text(main.path)
        self.assertIn("张伟", content)


class TestApi(unittest.TestCase):
    """起真实本地 HTTP 服务，走 /api/restore/*。"""

    @classmethod
    def setUpClass(cls):
        from yinzhan import api
        cls.httpd = api.serve("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path: str, raw: bool = False):
        with urllib.request.urlopen(self.base + path, timeout=30) as resp:
            data = resp.read()
            return data if raw else json.loads(data.decode("utf-8"))

    def _post(self, path: str, *, data: bytes | None = None, body: dict | None = None,
              headers: dict | None = None):
        payload = data if data is not None else json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=payload, method="POST",
                                     headers=headers or {})
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_restore_flow(self):
        mappings = self._get("/api/restore/mappings")
        ids = [m["job_id"] for m in mappings["mappings"]]
        self.assertIn(JOB, ids)

        src = TMP / "API回件.docx"
        _fake_docx(src)
        name = urllib.parse.quote("API回件.docx")
        prepared = self._post("/api/restore/upload", data=src.read_bytes(),
                              headers={"X-Filename": name})
        self.assertEqual(prepared["format"], "docx")
        self.assertTrue(prepared["restore_id"].startswith("r_"))
        cand_ids = [c["job_id"] for c in prepared["candidates"]]
        self.assertIn(JOB, cand_ids)

        result = self._post("/api/restore/run",
                            body={"restore_id": prepared["restore_id"],
                                  "job_id": JOB})
        self.assertEqual(result["job_id"], JOB)
        self.assertGreater(result["counts"]["replaced"], 0)
        kinds = [o["kind"] for o in result["outputs"]]
        self.assertIn("main", kinds)
        main = next(o for o in result["outputs"] if o["kind"] == "main")
        self.assertTrue(main["url"].startswith("/api/restore/file?rid="))
        blob = self._get(main["url"], raw=True)
        self.assertTrue(blob)                  # 能下载回二进制

        records = self._get("/api/audit?limit=5")["records"]
        restores = [r for r in records if r.get("mode") == "restore"]
        self.assertTrue(restores)
        self.assertEqual(restores[0]["job_id"], JOB)

    def test_pages(self):
        with urllib.request.urlopen(self.base + "/restore", timeout=10) as resp:
            html = resp.read().decode("utf-8", "replace")
        self.assertEqual(resp.status, 200)
        self.assertIn("restore", html.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
