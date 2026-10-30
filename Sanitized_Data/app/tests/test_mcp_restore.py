# -*- coding: utf-8 -*-
"""MCP 工具 restore_sanitize_file（文件级回件还原）测试。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_mcp_restore.py

本机 DSH 沙箱下系统临时目录可能不可写，用 YINZHAN_TEST_TMP 指到可写目录：

    $env:YINZHAN_TEST_TMP='D:\\DSH_DESKTOP_WORKSPACE\\.tmp_restore_test\\pytemp'
    python -X utf8 app/tests/test_mcp_restore.py

设计说明：
- 纯离线、不写仓库：数据目录/成品目录全部指到临时目录（先设环境变量，再
  import yinzhan/mcp_server，因为 config 在 import 时解析路径），测完删除；
  临时目录写法与 app/tests/test_restore.py 顶部保持一致。
- 映射用 ``vault.save_mapping`` 当场写入临时映射库；回件用 python-docx 生成
  （含拆成两个 run 的假名，验证跨 run 合并）。
- 直接调 ``mcp_server.HANDLERS["restore_sanitize_file"]``，与 stdio
  tools/call 是同一套实现（schema 契约另用 mcp_server.TOOLS 校验）。
- 安全断言：MCP 客户端可能是云端模型，默认响应只含统计/产物路径，不出现
  原值；只有 include_originals=true 才返回 fake→orig 明细。
- 强制 YINZHAN_MCP_BACKEND=local：避免探测本机可能正在运行的 Web 服务，
  保证结果确定（restore 工具本身只在本机执行，不受 hybrid 后端影响）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# ── 必须先定数据/输出目录，再 import yinzhan/mcp_server（config 在 import 时解析路径） ──
TMP_BASE = Path(os.environ.get("YINZHAN_TEST_TMP") or tempfile.gettempdir())
TMP = TMP_BASE / f"yz_mcp_restore_{os.getpid()}_{os.urandom(4).hex()}"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["YINZHAN_DATA_DIR"] = str(TMP / "data")
os.environ["YINZHAN_OUTPUT_DIR"] = str(TMP / "out")
os.environ["YINZHAN_DESKTOP"] = "1"
# 本机 Web 工作台可能正在运行：强制 local 后端，避免探测真实服务影响确定性。
os.environ["YINZHAN_MCP_BACKEND"] = "local"

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import mcp_server                                    # noqa: E402
from yinzhan import vault                            # noqa: E402
from yinzhan.config import DATA_DIR, OUTPUT_DIR      # noqa: E402

TOOL = "restore_sanitize_file"
JOB = "j_test_mcp_restore"
JOB_NAME = "合同.docx"
SEAL = "20260101-0001"
ORIG = "张伟"          # 原值（真值），绝不能出现在默认响应里
FAKE = "陈志远"        # 换名后的假值，回件里出现的就是它


def call_tool(args: dict) -> dict:
    """直接调工具实现（与 stdio tools/call 同一入口）。"""
    return mcp_server.HANDLERS[TOOL](args)


def payload(result: dict) -> dict:
    assert isinstance(result, dict) and result.get("content"), result
    try:
        return json.loads(result["content"][0]["text"])
    except Exception as exc:  # pragma: no cover - 失败时给出可读信息
        raise AssertionError(f"结果 content[0] 不是 JSON：{result!r}") from exc


def make_return_docx(path: Path) -> Path:
    """造一份「AI 交回来的」回件 docx：假名故意拆成两个 run。"""
    from docx import Document

    doc = Document()
    doc.add_paragraph("补充协议")
    p = doc.add_paragraph()
    p.add_run("甲方：陈")
    p.add_run("志远")                    # 跨 run 合并：甲方：陈志远
    doc.add_paragraph("乙方：上海某某科技有限公司")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "确认人"
    table.cell(0, 1).text = "陈志远"
    doc.save(str(path))
    return path


def docx_text(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    parts.extend(cell.text for table in doc.tables
                 for row in table.rows for cell in row.cells)
    return "\n".join(parts)


def setUpModule() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    vault.save_mapping(JOB, {"PERSON": {ORIG: FAKE}},
                       {"name": JOB_NAME, "seal": SEAL})


def tearDownModule() -> None:
    shutil.rmtree(TMP, ignore_errors=True)


class TestToolContract(unittest.TestCase):
    """工具注册与冻结 schema。"""

    def test_registered_in_tools_and_handlers(self):
        tools = {item["name"]: item for item in mcp_server.TOOLS}
        self.assertIn(TOOL, tools)
        self.assertIn(TOOL, mcp_server.HANDLERS)
        schema = tools[TOOL]["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(set(schema["required"]), {"path", "job_id"})
        self.assertEqual(set(schema["properties"]),
                         {"path", "job_id", "out_dir", "include_originals"})
        # 默认 false：不静默返回原值明细
        self.assertIs(schema["properties"]["include_originals"]["default"], False)
        # 工具名不重复（tools/list 不能有重名）
        names = [item["name"] for item in mcp_server.TOOLS]
        self.assertEqual(len(names), len(set(names)))


class TestRestoreTool(unittest.TestCase):
    def setUp(self):
        self.inbox = TMP / "inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)

    def test_restore_docx_default_hides_originals(self):
        src = make_return_docx(self.inbox / "回件.docx")
        result = call_tool({"path": str(src), "job_id": JOB})
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(len(result["content"]), 1)
        self.assertEqual(result["content"][0]["type"], "text")

        data = payload(result)
        self.assertIs(data["ok"], True)
        report = data["restore"]
        self.assertEqual(report["job_id"], JOB)
        self.assertEqual(report["job_name"], JOB_NAME)
        self.assertEqual(report["seal"], SEAL)
        self.assertEqual(report["format"], "docx")
        self.assertEqual(report["strategy"], "docx/ooxml-text")
        # 段落 + 表格各出现一次假名
        self.assertGreaterEqual(report["counts"]["replaced"], 2)
        self.assertEqual(report["counts"]["unique_replaced"], 1)
        self.assertEqual(report["by_type"], {"PERSON": 1})
        self.assertIsInstance(report["warnings"], list)
        self.assertGreaterEqual(report["elapsed_ms"], 0)

        # 产物落到本机 out 目录且真实存在
        outputs = report["outputs"]
        self.assertTrue(outputs)
        main = next(o for o in outputs if o["kind"] == "main")
        out_path = Path(main["path"])
        self.assertTrue(out_path.is_file(), out_path)
        self.assertGreater(main["bytes"], 0)
        self.assertRegex(main["sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("_还原", main["name"])

        # 默认响应不含替换明细、不含原值（MCP 客户端可能是云端模型）
        self.assertNotIn("replacements", report)
        dumped = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(ORIG, dumped)

        # 成品里确实换回了真值（含跨 run）
        restored = docx_text(out_path)
        self.assertNotIn(FAKE, restored)
        self.assertIn(f"甲方：{ORIG}", restored)
        self.assertIn(ORIG, restored)

    def test_include_originals_returns_replacements(self):
        src = make_return_docx(self.inbox / "回件2.docx")
        result = call_tool({"path": str(src), "job_id": JOB,
                            "include_originals": True})
        self.assertFalse(result.get("isError"), result)
        report = payload(result)["restore"]
        self.assertIn("replacements", report)
        replacements = report["replacements"]
        self.assertTrue(replacements)
        person = next(item for item in replacements if item["fake"] == FAKE)
        self.assertEqual(person["orig"], ORIG)
        self.assertEqual(person["etype"], "PERSON")
        self.assertGreaterEqual(person["count"], 1)
        self.assertIn("label", person)

        # 布尔参数严格解析：字符串 "false" 不能当成 True（安全默认不能被静默放行）
        again = call_tool({"path": str(src), "job_id": JOB,
                           "include_originals": "false"})
        self.assertFalse(again.get("isError"), again)
        self.assertNotIn("replacements", payload(again)["restore"])

    def test_out_dir_is_respected(self):
        src = make_return_docx(self.inbox / "回件3.docx")
        custom = TMP / "custom_out"
        result = call_tool({"path": str(src), "job_id": JOB,
                            "out_dir": str(custom)})
        self.assertFalse(result.get("isError"), result)
        main = next(o for o in payload(result)["restore"]["outputs"]
                    if o["kind"] == "main")
        self.assertEqual(Path(main["path"]).parent.resolve(), custom.resolve())
        self.assertTrue(Path(main["path"]).is_file())


class TestRestoreErrors(unittest.TestCase):
    """错误分支：一律返回结构化 isError，不崩溃、message 可读。"""

    def setUp(self):
        self.inbox = TMP / "errors"
        self.inbox.mkdir(parents=True, exist_ok=True)

    def assert_readable_error(self, result: dict, code: str | None = None) -> dict:
        self.assertTrue(result.get("isError"), result)
        data = payload(result)
        self.assertIs(data.get("ok"), False)
        message = str(data.get("error") or "").strip()
        self.assertTrue(message, data)
        self.assertNotIn("Traceback", message)
        if code is not None:
            self.assertEqual(data.get("code"), code, data)
        return data

    def test_missing_path(self):
        data = self.assert_readable_error(call_tool({"job_id": JOB}),
                                          "bad_request")
        self.assertIn("path", data["error"])

    def test_missing_job_id(self):
        src = make_return_docx(self.inbox / "回件.docx")
        data = self.assert_readable_error(call_tool({"path": str(src)}),
                                          "bad_request")
        self.assertIn("job_id", data["error"])

    def test_missing_file(self):
        data = self.assert_readable_error(
            call_tool({"path": str(self.inbox / "没有这个.docx"), "job_id": JOB}),
            "bad_request")
        self.assertIn("回件不存在", data["error"])

    def test_mapping_not_found(self):
        src = make_return_docx(self.inbox / "回件_missing.docx")
        data = self.assert_readable_error(
            call_tool({"path": str(src), "job_id": "j_not_exists"}),
            "mapping_not_found")
        self.assertIn("映射", data["error"])

    def test_unsupported_binary_format(self):
        # 未知后缀的二进制：restore.detect_format 会兜底尝试按纯文本解码，
        # 所以当前实现报 no_match（restore.py 只在读文件失败时报
        # unsupported_format）。这里锁「不崩溃 + 结构化可读错误」，不锁死 code。
        binary = self.inbox / "回件.bin"
        binary.write_bytes(b"\xff\xfe\x00\x01\x02\xff" * 4)
        result = call_tool({"path": str(binary), "job_id": JOB})
        data = self.assert_readable_error(result)
        self.assertIn(data.get("code"), ("unsupported_format", "no_match"))
        self.assertNotIn(ORIG, json.dumps(data, ensure_ascii=False))

    def test_corrupt_office_file_does_not_crash(self):
        # 后缀是 docx 但内容不是合法 zip：工具仍要返回 isError，不能掀翻协议层。
        broken = self.inbox / "坏回件.docx"
        broken.write_text("这不是一个 zip 包", "utf-8")
        self.assert_readable_error(call_tool({"path": str(broken), "job_id": JOB}))

    def test_no_match(self):
        src = self.inbox / "无关.txt"
        src.write_text("这份回件里没有任何映射中的假值。", "utf-8")
        data = self.assert_readable_error(
            call_tool({"path": str(src), "job_id": JOB}), "no_match")
        self.assertIn("假值", data["error"])
        # 没有命中的中间产物要清理掉，不留脏文件
        left = [p for p in OUTPUT_DIR.glob("无关_还原*")]
        self.assertEqual(left, [])

    def test_bad_out_dir_type(self):
        src = make_return_docx(self.inbox / "回件_类型.docx")
        data = self.assert_readable_error(
            call_tool({"path": str(src), "job_id": JOB, "out_dir": 123}),
            "bad_request")
        self.assertIn("out_dir", data["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
