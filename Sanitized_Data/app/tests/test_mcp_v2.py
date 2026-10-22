# -*- coding: utf-8 -*-
"""MCP L2（M2-M4 server 侧）测试：协议协商 / 旧工具契约 / Job 化 / 新工具 /
resources / prompts / 导出失败语义 / 审计 / 还原 / 工作台深链 / hybrid 后端。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_mcp_v2.py

设计说明：
- 纯离线、不写仓库：数据目录/成品目录全部指到系统临时目录（先设环境变量，
  再 import yinzhan/mcp_server，因为 config 在 import 时解析路径）。
- 不依赖真实 OCR 全量：正文用临时 txt（解析/检测/导出都很快），PNG 预览用
  一页由 PyMuPDF 生成的纯文本 PDF 验证 image content block。
- 直接调 mcp_server.handle() / HANDLERS，与 stdio 进程使用的是同一套实现。
- hybrid 后端：
  * 本地模式用例在模块导入前强制 YINZHAN_MCP_BACKEND=local（保证不探测本机
    可能存在的真实 Web 服务、结果确定）；
  * HTTP 模式用例（TestHttpBackend）临时切 http，用 mock 替换
    mcp_server._http_json / _http_bytes 模拟本机 Web API（建任务/快照/实体/
    导出/审计/渲染），覆盖「同一个 job 的深链、HTTP 失败结构化错误、
    media_confirmation_required/media_count、verify_failed」等分支；
  * 真实端口的联调（起 app/server/run.py 后 MCP 走 HTTP）由 QA/Lead 在
    DSH 联调阶段验证，不属于本离线用例。
- 其余 mock 仅用于「内嵌媒体二次确认」「出口自检失败」「vault.load_mapping」
  和「页面渲染」这类真实夹具很难稳定构造的失败/二进制分支。
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

# ── 必须先定数据/输出目录，再 import yinzhan（config 在 import 时解析路径） ──
_TMP = Path(tempfile.mkdtemp(prefix="yinzhan_mcp_v2_"))
os.environ["YINZHAN_DATA_DIR"] = str(_TMP / "data")
os.environ["YINZHAN_OUTPUT_DIR"] = str(_TMP / "data" / "out")
# 本地模式用例强制 local 后端（不探测本机可能存在的 Web 服务，保证确定性）；
# HTTP 模式用例在 TestHttpBackend 里临时改 http + mock 本机 API。
os.environ["YINZHAN_MCP_BACKEND"] = "local"

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import mcp_server                                    # noqa: E402
from yinzhan import vault                            # noqa: E402
from yinzhan.config import RUNTIME_PATH              # noqa: E402
from yinzhan.jobs import MANAGER                     # noqa: E402
from yinzhan.udm import Entity, Span, YinzhanError   # noqa: E402

# 合成敏感数据（全部虚构）：用于断言工具结果/资源里绝不出现原值。
PHONE = "13817264451"
ID_NO = "310104198703124510"
PERSON = "张伟"
SECRETS = (PHONE, ID_NO, PERSON)
TEXT = f"联系人：{PERSON}，手机号 {PHONE}，身份证号 {ID_NO}。"

_OLD_TOOLS = ("sanitize_document", "redact_text", "check_support", "list_rules")
_NEW_TOOLS = (
    "sanitize_file", "get_sanitize_job", "list_sanitize_jobs",
    "get_sanitize_entities", "update_sanitize_entity", "mark_sanitize_region",
    "accept_all_sanitize_entities", "export_sanitize_job",
    "get_sanitize_audit", "rehydrate_sanitize_text", "open_sanitize_workbench",
)

try:
    import fitz          # PyMuPDF：生成一页纯文本 PDF 夹具；缺失时跳过图片用例
    _HAS_FITZ = True
except Exception:        # pragma: no cover
    fitz = None          # type: ignore
    _HAS_FITZ = False


# ────────────────────────────── 小工具 ──────────────────────────────

def tool(name: str, args: dict) -> dict:
    """直接调用工具实现（与 stdio tools/call 同一入口）。"""
    return mcp_server.HANDLERS[name](args)


def payload(result: dict) -> dict:
    """从 MCP 工具结果里取 JSON 文本（旧 4 工具也是这个形态）。"""
    assert isinstance(result, dict) and result.get("content"), result
    try:
        return json.loads(result["content"][0]["text"])
    except Exception as exc:  # pragma: no cover - 失败时给出可读信息
        raise AssertionError(f"结果 content[0] 不是 JSON：{result!r}") from exc


def rpc(method: str, params: dict | None = None, msg_id: int = 1) -> dict:
    return mcp_server.handle({"jsonrpc": "2.0", "id": msg_id,
                              "method": method, "params": params or {}})


def fixture_text(name: str = "doc.txt", text: str = TEXT) -> Path:
    path = _TMP / "fixtures" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


def make_pdf(path: Path, lines: list[str]) -> Path:
    assert fitz is not None
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    for index, line in enumerate(lines):
        page.insert_text((72, 100 + index * 26), line, fontsize=12)
    pdf.save(str(path))
    pdf.close()
    return path


def new_job(name: str = "doc.txt", text: str = TEXT) -> dict:
    """sanitize_file 创建任务并等到 ready，返回工具 payload。"""
    result = tool("sanitize_file", {"path": str(fixture_text(name, text))})
    assert not result.get("isError"), result
    data = payload(result)
    assert data.get("stage") == "ready", data
    return data


def visible_text(payload_obj) -> str:
    return json.dumps(payload_obj, ensure_ascii=False)


# ────────────────────────────── 协议协商 / 工具列表 ──────────────────────────────

class TestProtocol(unittest.TestCase):
    def test_initialize_echoes_supported_versions(self):
        for version in ("2024-11-05", "2025-06-18"):
            resp = rpc("initialize", {
                "protocolVersion": version,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            })
            self.assertNotIn("error", resp, resp)
            result = resp["result"]
            self.assertEqual(result["protocolVersion"], version)
            for capability in ("tools", "resources", "prompts"):
                self.assertIn(capability, result["capabilities"])
            self.assertEqual(result["serverInfo"]["name"], "sanitized-data")
            self.assertTrue(result["serverInfo"]["version"])

    def test_initialize_unknown_version_returns_highest(self):
        for request in ({"protocolVersion": "2099-01-01"}, {},
                        {"protocolVersion": ""}):
            resp = rpc("initialize", request)
            self.assertEqual(resp["result"]["protocolVersion"],
                             mcp_server.PROTOCOL_LATEST)
        self.assertEqual(mcp_server.PROTOCOL_LATEST, "2025-06-18")

    def test_notifications_and_ping(self):
        self.assertIsNone(mcp_server.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(mcp_server.handle(
            {"jsonrpc": "2.0", "method": "notifications/cancelled",
             "params": {"requestId": 1}}))
        resp = rpc("ping")
        self.assertEqual(resp["result"], {})

    def test_unknown_method_returns_protocol_error(self):
        resp = rpc("no/such-method")
        self.assertEqual(resp["error"]["code"], -32601)

    def test_tools_list_has_old_and_new_tools(self):
        resp = rpc("tools/list")
        tools = resp["result"]["tools"]
        names = [item["name"] for item in tools]
        self.assertEqual(len(names), len(set(names)), "工具名不能重复")
        for name in _OLD_TOOLS + _NEW_TOOLS:
            self.assertIn(name, names)
        self.assertEqual(set(_OLD_TOOLS) - set(names), set())
        # 旧 4 工具的参数契约冻结（名称/参数不删不改）
        schema = {item["name"]: item["inputSchema"] for item in tools}
        expected = {
            "sanitize_document": (
                {"path"},
                {"path", "mode", "strictness", "allow_unprocessed_media"}),
            "redact_text": ({"text"}, {"text", "strictness"}),
            "check_support": ({"path"}, {"path"}),
            "list_rules": (set(), set()),
        }
        for name, (required, properties) in expected.items():
            self.assertEqual(set(schema[name].get("required") or []), required,
                             name)
            self.assertEqual(set((schema[name].get("properties") or {})),
                             properties, name)
        # sanitize_document 旧枚举值不能变
        props = schema["sanitize_document"]["properties"]
        self.assertEqual(props["mode"]["enum"], ["ai", "human"])
        self.assertEqual(props["strictness"]["enum"],
                         ["loose", "standard", "strict"])


# ────────────────────────────── sanitize_document（旧工具 + Job 化） ──────────────────────────────

class TestSanitizeDocument(unittest.TestCase):
    def test_text_document_keeps_old_fields_and_adds_job_fields(self):
        result = tool("sanitize_document", {"path": str(fixture_text())})
        self.assertFalse(result.get("isError"), result)
        data = payload(result)

        old_fields = {"ok", "output", "mode", "pack", "entities", "by_type",
                      "verify", "mapping_id", "warnings"}
        self.assertTrue(old_fields <= set(data), sorted(data))
        self.assertIs(data["ok"], True)
        self.assertEqual(data["mode"], "ai")
        self.assertIsInstance(data["pack"], str)
        self.assertIsInstance(data["entities"], int)
        self.assertGreaterEqual(data["entities"], 1)
        self.assertIsInstance(data["by_type"], dict)
        self.assertTrue(data["by_type"])
        # verify 内部字段也必须保持旧形态：只允许 verified/residual
        self.assertEqual(set(data["verify"]), {"verified", "residual"})
        self.assertIs(data["verify"]["verified"], True)
        self.assertEqual(data["verify"]["residual"], 0)
        self.assertIsInstance(data["warnings"], list)
        output = Path(data["output"])
        self.assertTrue(output.is_file(), output)
        self.assertGreater(output.stat().st_size, 0)
        # 成品统一落 data/out（不再用 cwd/sanitized_output）
        self.assertEqual(output.parent.resolve(),
                         Path(os.environ["YINZHAN_OUTPUT_DIR"]).resolve())

        # 新增字段（只加不改）
        self.assertTrue(str(data["job_id"]).startswith("j_"))
        self.assertEqual(data["origin"], "mcp")
        # 本测试进程强制 local 后端：没有运行中的 Web 服务 → 不给假深链，
        # 明确 workbench_available=false + 启动引导（HTTP 模式见 TestHttpBackend）。
        self.assertIs(data["workbench_available"], False)
        self.assertIsNone(data["workbench_url"])
        self.assertIn("启动", data["workbench_hint"])
        self.assertEqual(data["backend"], "local")
        # ai 模式映射由 MANAGER.export 以 job.id 落盘
        self.assertEqual(data["mapping_id"], data["job_id"])
        self.assertIsNotNone(vault.load_mapping(data["job_id"]))

        # 文本格式不可渲染：只回 1 个 text block，不硬塞图片
        self.assertEqual(len(result["content"]), 1)

    def test_human_mode_returns_output_and_no_mapping(self):
        result = tool("sanitize_document",
                      {"path": str(fixture_text()), "mode": "human"})
        self.assertFalse(result.get("isError"), result)
        data = payload(result)
        self.assertEqual(data["mode"], "human")
        self.assertIsNone(data["mapping_id"])
        output = Path(data["output"])
        self.assertTrue(output.is_file(), output)
        self.assertIn("已隐去", output.name)

    def test_media_confirmation_required_then_explicit_retry(self):
        original_export = MANAGER.export
        calls: list[dict] = []

        def fake_export(job_id, mode, options=None):
            options = dict(options or {})
            calls.append(options)
            if not options.get("allow_unprocessed_media"):
                error = YinzhanError("检测到 2 个无法自动脱敏的隐藏对象，要继续吗？")
                error.code = "media_confirmation_required"
                error.media_count = 2
                raise error
            return original_export(job_id, mode, options=options)

        with mock.patch.object(mcp_server.MANAGER, "export",
                               side_effect=fake_export):
            first = tool("sanitize_document", {"path": str(fixture_text())})
            self.assertTrue(first.get("isError"), first)
            first_data = payload(first)
            self.assertEqual(first_data["code"], "media_confirmation_required")
            self.assertEqual(first_data["media_count"], 2)
            self.assertIs(calls[0].get("allow_unprocessed_media"), False)

            second = tool("sanitize_document", {
                "path": str(fixture_text()), "allow_unprocessed_media": True})
            self.assertFalse(second.get("isError"), second)
            self.assertIs(calls[1].get("allow_unprocessed_media"), True)

    def test_verify_failure_returns_iserror_and_deletes_product(self):
        product = _TMP / "fixtures" / "fake_verify_failed.txt"
        product.write_text("should be deleted", "utf-8")
        record = {
            "out": str(product), "name": product.name, "seal_no": "20260101-0001",
            "sha256": "0" * 64, "mode": "ai",
            "verify": {"verified": False, "residual": None,
                       "issues": ["成品无法重新解析"]},
            "warnings": [],
        }
        with mock.patch.object(mcp_server.MANAGER, "export",
                               return_value=dict(record)):
            result = tool("sanitize_document", {"path": str(fixture_text())})
        self.assertTrue(result.get("isError"), result)
        data = payload(result)
        self.assertEqual(data["code"], "verify_failed")
        self.assertFalse(product.exists(), "自检失败的成品必须删除")

    def test_missing_path_is_error(self):
        result = tool("sanitize_document", {"path": str(_TMP / "nope.txt")})
        self.assertTrue(result.get("isError"))
        self.assertEqual(payload(result)["code"], "bad_request")

    @unittest.skipUnless(_HAS_FITZ, "需要 PyMuPDF 生成 PDF 夹具")
    def test_pdf_success_returns_preview_image_block(self):
        pdf = make_pdf(_TMP / "fixtures" / "preview.pdf",
                       [f"联系人：{PERSON}", f"手机号 {PHONE}"])
        result = tool("sanitize_document", {"path": str(pdf), "mode": "ai"})
        self.assertFalse(result.get("isError"), result)
        data = payload(result)
        self.assertIs(data["ok"], True)
        self.assertEqual(data["verify"]["residual"], 0)
        self.assertGreaterEqual(len(result["content"]), 2,
                                "可渲染格式应附带一张预览图")
        image = result["content"][1]
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["mimeType"], "image/png")
        raw = base64.b64decode(image["data"])
        self.assertTrue(raw.startswith(b"\x89PNG"))
        self.assertLessEqual(len(raw), mcp_server.MAX_IMAGE_BYTES)


# ────────────────────────────── 工具图片 / 预览图上限 ──────────────────────────────

class TestImageContent(unittest.TestCase):
    def test_cap_image_bytes_shrinks_large_png(self):
        import io
        import os

        from PIL import Image

        # 随机噪声 PNG 压缩率最低，能稳定超过 2MB
        side = 1200
        noise = os.urandom(side * side * 3)
        source = Image.frombytes("RGB", (side, side), noise)
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        raw = buffer.getvalue()
        self.assertGreater(len(raw), mcp_server.MAX_IMAGE_BYTES)
        capped = mcp_server._cap_image_bytes(raw)
        self.assertIsNotNone(capped)
        self.assertLessEqual(len(capped), mcp_server.MAX_IMAGE_BYTES)
        self.assertTrue(capped.startswith(b"\x89PNG"))

    def test_non_png_normalized_to_png(self):
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), (10, 200, 10)).save(buffer, format="JPEG")
        png = mcp_server._to_png_bytes(buffer.getvalue())
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))


# ────────────────────────────── 新工具：Job / 实体 / 框选 / 导出 / 审计 ──────────────────────────────

class TestJobsTools(unittest.TestCase):
    def test_sanitize_file_then_get_and_list(self):
        data = new_job()
        job_id = data["job_id"]
        self.assertTrue(job_id.startswith("j_"))
        self.assertEqual(data["origin"], "mcp")
        self.assertGreaterEqual(data["counts"]["total"], 1)
        self.assertIs(data["workbench_available"], False)
        self.assertIsNone(data["workbench_url"])
        self.assertIn("启动", data["workbench_hint"])

        snap = payload(tool("get_sanitize_job", {"job_id": job_id}))
        self.assertEqual(snap["id"], job_id)
        self.assertEqual(snap["origin"], "mcp")
        self.assertEqual(snap["stage"], "ready")
        self.assertIn("pending", snap)
        self.assertIn("exports", snap)

        listed = payload(tool("list_sanitize_jobs", {"limit": 10}))
        matched = [j for j in listed["jobs"] if j["id"] == job_id]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["origin"], "mcp")

        bad = tool("get_sanitize_job", {"job_id": "j_does_not_exist"})
        self.assertTrue(bad.get("isError"))
        self.assertEqual(payload(bad)["code"], "job_not_found")

    def test_entities_are_masked_views(self):
        job_id = new_job()["job_id"]
        data = payload(tool("get_sanitize_entities", {"job_id": job_id}))
        self.assertGreaterEqual(data["counts"]["total"], 1)
        self.assertEqual(data["total"], len(data["entities"]))
        required = {"id", "label", "etype", "status", "value_len", "spans",
                    "level"}
        for entity in data["entities"]:
            self.assertTrue(required <= set(entity), entity.keys())
            self.assertNotIn("value", entity, "实体清单不得返回原值")
            self.assertIsInstance(entity["value_len"], int)
        dumped = visible_text(data)
        for secret in SECRETS:
            self.assertNotIn(secret, dumped, f"实体清单泄露原值：{secret}")

        pending = payload(tool("get_sanitize_entities",
                               {"job_id": job_id, "status": "pending"}))
        self.assertTrue(all(e["status"] == "pending" for e in pending["entities"]))

        limited = payload(tool("get_sanitize_entities",
                               {"job_id": job_id, "limit": 1}))
        self.assertEqual(len(limited["entities"]), 1)
        if limited["total"] > 1:
            self.assertTrue(limited["truncated"])

    def test_mark_update_and_accept_all(self):
        job_id = new_job()["job_id"]
        job = MANAGER.get(job_id)
        before = payload(tool("get_sanitize_job",
                              {"job_id": job_id}))["counts"]["total"]

        marked = payload(tool("mark_sanitize_region", {
            "job_id": job_id, "page": 0,
            "rect": [0.10, 0.10, 0.40, 0.20], "label": "人工框选测试"}))
        self.assertEqual(marked["added"], 1)
        self.assertTrue(str(marked["entity_id"]).startswith("e_"))
        self.assertEqual(marked["counts"]["total"], before + 1)
        entity_id = marked["entity_id"]

        accepted = payload(tool("update_sanitize_entity", {
            "job_id": job_id, "entity_id": entity_id, "action": "accept"}))
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["action"], "accept")

        rejected = payload(tool("update_sanitize_entity", {
            "job_id": job_id, "entity_id": entity_id, "action": "reject"}))
        self.assertEqual(rejected["status"], "rejected")

        replaced = payload(tool("update_sanitize_entity", {
            "job_id": job_id, "entity_id": entity_id, "action": "replace",
            "replacement": "[人工替换]"}))
        self.assertEqual(replaced["status"], "accepted")
        # 更新结果不返回原值
        for secret in SECRETS:
            self.assertNotIn(secret, visible_text(replaced))

        # replace 必须显式给 replacement
        missing = tool("update_sanitize_entity", {
            "job_id": job_id, "entity_id": entity_id, "action": "replace"})
        self.assertTrue(missing.get("isError"))
        self.assertEqual(payload(missing)["code"], "bad_request")

        # 构造一个待确认实体，验证 accept_all 真正生效
        with job.lock:
            job.entities.append(Entity(
                id="e_pending_for_accept_all", etype="OTHER", label="待确认测试",
                value="同值", spans=[Span(rect=(1.0, 1.0, 2.0, 2.0), page=0)],
                level="L2", status="pending"))
        accepted_all = payload(tool("accept_all_sanitize_entities",
                                    {"job_id": job_id}))
        self.assertGreaterEqual(accepted_all["accepted"], 1)
        self.assertEqual(accepted_all["pending"], 0)

        # 非法 rect / 越界页码
        bad_rect = tool("mark_sanitize_region", {
            "job_id": job_id, "page": 0, "rect": [2.0, 0.0, 3.0, 1.0]})
        self.assertTrue(bad_rect.get("isError"))
        self.assertEqual(payload(bad_rect)["code"], "bad_request")

    def test_export_success_returns_contract_fields(self):
        job_id = new_job()["job_id"]
        data = payload(tool("export_sanitize_job",
                            {"job_id": job_id, "mode": "ai"}))
        self.assertIs(data["ok"], True)
        out = Path(data["out_path"])
        self.assertTrue(out.is_file(), out)
        self.assertGreater(out.stat().st_size, 0)
        self.assertRegex(data["sha256"] or "", r"^[0-9a-f]{64}$")
        self.assertTrue(data["seal_no"])
        self.assertIs(data["verify"].get("verified"), True)
        self.assertIsInstance(data["warnings"], list)
        self.assertEqual(data["mapping_id"], job_id)
        self.assertEqual(data["origin"], "mcp")
        # local 后端：没有 Web 服务时不给假深链
        self.assertIs(data["workbench_available"], False)
        self.assertIsNone(data["workbench_url"])
        self.assertIn("启动", data["workbench_hint"])
        # 成品统一落 data/out
        self.assertEqual(out.parent.resolve(),
                         Path(os.environ["YINZHAN_OUTPUT_DIR"]).resolve())

        human = payload(tool("export_sanitize_job",
                             {"job_id": job_id, "mode": "human"}))
        self.assertTrue(Path(human["out_path"]).is_file())
        self.assertIsNone(human["mapping_id"])

    def test_export_invalid_job_and_nothing_to_export(self):
        invalid = tool("export_sanitize_job", {"job_id": "j_not_exists"})
        self.assertTrue(invalid.get("isError"))
        self.assertEqual(payload(invalid)["code"], "job_not_found")

        clean = new_job(name="clean.txt", text="今天天气不错，适合散步。")
        if clean["counts"]["total"] == 0:
            result = tool("export_sanitize_job", {"job_id": clean["job_id"]})
            self.assertTrue(result.get("isError"))
            self.assertEqual(payload(result)["code"], "nothing_to_export")

    def test_audit_job_filter_and_chain_verify(self):
        job_id = new_job()["job_id"]
        payload(tool("export_sanitize_job", {"job_id": job_id}))
        data = payload(tool("get_sanitize_audit", {"job_id": job_id}))
        self.assertGreaterEqual(data["count"], 1)
        self.assertTrue(all(r.get("job_id") == job_id for r in data["records"]))
        self.assertIs(data["verify_chain"]["ok"], True)
        self.assertEqual(data["verify_chain"]["bad"], 0)
        self.assertGreaterEqual(data["stats"]["records"], 1)
        # 审计不含原值
        dumped = visible_text(data)
        for secret in SECRETS:
            self.assertNotIn(secret, dumped)

        no_chain = payload(tool("get_sanitize_audit",
                                {"job_id": job_id, "verify_chain": False}))
        self.assertNotIn("verify_chain", no_chain)

    def test_rehydrate_uses_mapping(self):
        mapping = {"map": {"PERSON": {PERSON: "陈志远"}}}
        with mock.patch.object(mcp_server.vault, "load_mapping",
                               return_value=mapping):
            data = payload(tool("rehydrate_sanitize_text", {
                "mapping_id": "m_demo", "text": "联系人：陈志远"}))
        self.assertEqual(data["mapping_id"], "m_demo")
        self.assertIn(PERSON, data["rehydrated"])
        self.assertNotIn("陈志远", data["rehydrated"])
        self.assertIs(data["changed"], True)

        with mock.patch.object(mcp_server.vault, "load_mapping",
                               return_value=None):
            missing = tool("rehydrate_sanitize_text", {
                "mapping_id": "m_missing", "text": "陈志远"})
        self.assertTrue(missing.get("isError"))
        self.assertEqual(payload(missing)["code"], "mapping_not_found")

        no_id = tool("rehydrate_sanitize_text", {"text": "陈志远"})
        self.assertTrue(no_id.get("isError"))
        self.assertEqual(payload(no_id)["code"], "bad_request")

    def test_local_mode_workbench_is_unavailable(self):
        job_id = new_job()["job_id"]
        # 本机无 Web 服务（测试强制 local）：open 工具应明确 no_workbench + 启动引导，
        # 而不是返回一个打开必然 404 的深链。
        result = tool("open_sanitize_workbench", {"job_id": job_id})
        self.assertTrue(result.get("isError"), result)
        data = payload(result)
        self.assertEqual(data["code"], "no_workbench")
        self.assertIs(data["workbench_available"], False)
        self.assertIn("启动", data["workbench_hint"])
        # runtime.json 的端口只在「服务真的健康」时才会被用作可用深链；
        # HTTP 模式的深链端口断言见 TestHttpBackend.test_http_workbench_deeplink。


# ────────────────────────────── HTTP 后端（hybrid） ──────────────────────────────

class _FakeWebApi:
    """内存版本机 Web API：mock mcp_server._http_json / _http_bytes 的响应。

    真实部署里 MCP stdio 是独立进程，HTTP 模式下 job 只存在于 Web 进程；
    这里用内存实现验证 MCP 侧确实按 Web API 协议走、且不再触碰本地 MANAGER。
    """

    PNG = b"\x89PNG\r\n\x1a\n" + b"FAKE_HTTP_PAGE"

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.entities: dict[str, list[dict]] = {}
        self.audit_records: list[dict] = []
        self.creates: list[dict] = []
        self.export_bodies: list[dict] = []
        self.fail_health = False
        self.fail_create = False
        self.export_error: dict | None = None
        self.export_seq = 0

    # ── 内部 ──
    @staticmethod
    def _raise(status: int, code: str, message: str):
        body = json.dumps({"error": {"code": code, "message": message}},
                          ensure_ascii=False).encode("utf-8")
        raise mcp_server.HttpError(status, body, message)

    def _snapshot(self, job_id) -> dict:
        snap = self.jobs.get(job_id)
        if snap is None:
            self._raise(400, "internal", f"任务不存在：{job_id}")
        return snap

    def _update(self, job_id: str, entity_id: str, body: dict) -> dict:
        self._snapshot(job_id)
        action = str(body.get("action") or "")
        target = next((e for e in self.entities.get(job_id, [])
                       if e.get("id") == entity_id), None)
        if target is None:
            self._raise(400, "internal", "实体不存在")
        target["status"] = {"reject": "rejected"}.get(action, "accepted")
        return dict(target)

    def _export(self, job_id: str, body: dict) -> dict:
        snap = self._snapshot(job_id)
        self.export_bodies.append(dict(body))
        if self.export_error:
            self._raise(int(self.export_error.get("status", 409)),
                        str(self.export_error.get("code")),
                        str(self.export_error.get("message")))
        self.export_seq += 1
        out = self.out_dir / f"AI视图_web_换名_20261023-{self.export_seq:04d}.txt"
        out.write_text("FAKE-WEB-EXPORT", "utf-8")
        record = {
            "out": str(out), "name": out.name,
            "seal_no": f"20261023-{self.export_seq:04d}",
            "sha256": "a" * 64, "mode": body.get("mode", "ai"),
            "verify": {"verified": True, "residual": 0, "issues": [],
                       "checked": 2, "media": {"count": 0},
                       "pdf_hidden": {}},
            "warnings": [], "bytes": out.stat().st_size,
        }
        snap["exports"][str(record["mode"])] = record
        self.audit_records.append({
            "seq": len(self.audit_records) + 1,
            "ts": "2026-10-23T00:00:00+08:00", "job_id": job_id,
            "doc_name": snap.get("name"), "doc_hash": "b" * 64,
            "fmt": snap.get("fmt"), "counts": {}, "rule_pack": "common",
            "mode": record["mode"], "out_hash": record["sha256"],
            "out_name": record["name"], "prev_sig": "", "sig": "c" * 16,
        })
        return record

    # ── mock 入口 ──
    def json(self, method, path, payload=None, timeout=None):
        parsed = urllib.parse.urlparse(path)
        route = parsed.path
        if method == "GET" and route == "/api/health":
            if self.fail_health:
                raise mcp_server.HttpError(0, b"", "connection refused")
            return {"ok": True, "version": "1.1.3"}
        if method == "POST" and route == "/api/jobs":
            if self.fail_create:
                raise mcp_server.HttpError(0, b"", "connection refused")
            body = dict(payload or {})
            self.creates.append(body)
            job_id = "j_web0001"
            self.jobs[job_id] = {
                "id": job_id, "name": Path(str(body.get("path"))).name,
                "fmt": "text", "stage": "ready", "progress": 1.0,
                "note": "", "cancelled": False, "error": None,
                "warnings": [], "pages": 1,
                "counts": {"total": 3, "pending": 1, "done": 2,
                           "rejected": 0,
                           "by_type": {"人名": 1, "手机号": 1, "身份证号": 1}},
                "created_at": "2026-10-23T00:00:00+08:00", "pack": "common",
                "strictness": "standard", "origin": "mcp",
                "id_card_mode": "fields", "time_blur": False,
                "minor_detected": False, "minor_strict": False,
                "combination_risk": {}, "exports": {},
            }
            self.entities[job_id] = [
                {"id": "e_web1", "type": "PERSON", "label": "人名",
                 "value": PERSON, "surrogate": "陈志远", "level": "L2",
                 "confidence": 0.99, "origin": "regex", "status": "accepted",
                 "spans": [{"block_id": "b0", "start": 3, "end": 5}]},
                {"id": "e_web2", "type": "PHONE", "label": "手机号",
                 "value": PHONE, "surrogate": None, "level": "L2",
                 "confidence": 0.98, "origin": "regex", "status": "pending",
                 "spans": [{"block_id": "b0", "start": 8, "end": 19}]},
            ]
            return {"job_id": job_id, "name": self.jobs[job_id]["name"],
                    "fmt": "text", "origin": "mcp"}
        if method == "GET" and route == "/api/jobs":
            return {"jobs": [dict(s) for s in self.jobs.values()]}
        if method == "GET" and route == "/api/audit":
            return {"records": list(reversed(self.audit_records)),
                    "stats": {"records": len(self.audit_records),
                              "last": None}}
        if method == "POST" and route == "/api/audit/verify":
            return {"total": len(self.audit_records), "bad": 0, "ok": True}

        match = re.match(r"^/api/jobs/(?P<jid>[^/]+)$", route)
        if match and method == "GET":
            return dict(self._snapshot(match["jid"]))
        match = re.match(r"^/api/jobs/(?P<jid>[^/]+)/entities$", route)
        if match and method == "GET":
            snap = self._snapshot(match["jid"])
            return {"entities": [dict(e) for e in
                                 self.entities.get(match["jid"], [])],
                    "counts": dict(snap["counts"])}
        match = re.match(r"^/api/jobs/(?P<jid>[^/]+)/entities/(?P<eid>[^/]+)$",
                         route)
        if match and method == "POST":
            return self._update(match["jid"], match["eid"], dict(payload or {}))
        match = re.match(r"^/api/jobs/(?P<jid>[^/]+)/mark$", route)
        if match and method == "POST":
            self._snapshot(match["jid"])
            return {"added": 1, "propagated": 0,
                    "entity": {"id": "e_web_mark", "type": "OTHER",
                               "label": (payload or {}).get("label")
                               or "其他敏感", "value": "",
                               "status": "accepted", "spans": []}}
        match = re.match(r"^/api/jobs/(?P<jid>[^/]+)/accept-all$", route)
        if match and method == "POST":
            snap = self._snapshot(match["jid"])
            snap["counts"]["pending"] = 0
            return {"accepted": 1, "total": snap["counts"]["total"]}
        match = re.match(r"^/api/jobs/(?P<jid>[^/]+)/export$", route)
        if match and method == "POST":
            return self._export(match["jid"], dict(payload or {}))
        self._raise(404, "not_found", f"没有这个接口：{route}")
        return {}

    def bytes(self, method, path, timeout=None):
        parsed = urllib.parse.urlparse(path)
        match = re.match(r"^/api/jobs/(?P<jid>[^/]+)/render/(?P<page>\d+)$",
                         parsed.path)
        if match and method == "GET":
            self._snapshot(match["jid"])
            return self.PNG
        self._raise(404, "not_found", f"没有这个接口：{parsed.path}")
        return b""


class TestHttpBackend(unittest.TestCase):
    """hybrid：Web 服务健康时 MCP 必须走 HTTP API（同一个 job），而不是本地 MANAGER。"""

    def setUp(self):
        self._saved_mode = os.environ.get("YINZHAN_MCP_BACKEND")
        os.environ["YINZHAN_MCP_BACKEND"] = "http"
        RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_PATH.write_text(json.dumps({"port": 9123}), "utf-8")
        self.web = _FakeWebApi(_TMP / "http_out")
        self._patches = [
            mock.patch.object(mcp_server, "_http_json",
                              side_effect=self.web.json),
            mock.patch.object(mcp_server, "_http_bytes",
                              side_effect=self.web.bytes),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()
        RUNTIME_PATH.unlink(missing_ok=True)
        if self._saved_mode is None:
            os.environ.pop("YINZHAN_MCP_BACKEND", None)
        else:
            os.environ["YINZHAN_MCP_BACKEND"] = self._saved_mode

    def _new_http_job(self, name: str = "http.txt") -> dict:
        data = payload(tool("sanitize_file",
                            {"path": str(fixture_text(name))}))
        self.assertEqual(data["job_id"], "j_web0001")
        return data

    def test_http_backend_create_origin_and_deeplink(self):
        path = fixture_text("http.txt")
        data = payload(tool("sanitize_file", {"path": str(path)}))
        self.assertEqual(data["job_id"], "j_web0001")
        # 建任务请求带 origin=mcp（Web 任务列表 MCP 徽标）
        self.assertEqual(self.web.creates[-1]["origin"], "mcp")
        self.assertEqual(self.web.creates[-1]["path"], str(path))
        self.assertIs(data["workbench_available"], True)
        self.assertEqual(data["backend"], "http")
        self.assertEqual(
            data["workbench_url"],
            "http://127.0.0.1:9123/console?job=j_web0001")
        # MCP 进程本地 MANAGER 不应再产生同名 job（唯一事实源在 Web 进程）
        self.assertNotIn("j_web0001", MANAGER._jobs)

        opened = payload(tool("open_sanitize_workbench",
                              {"job_id": "j_web0001"}))
        self.assertEqual(opened["url"],
                         "http://127.0.0.1:9123/console?job=j_web0001")
        self.assertIs(opened["workbench_available"], True)

        snap = payload(tool("get_sanitize_job", {"job_id": "j_web0001"}))
        self.assertEqual(snap["id"], "j_web0001")
        self.assertEqual(snap["origin"], "mcp")
        self.assertIs(snap["workbench_available"], True)
        listed = payload(tool("list_sanitize_jobs", {"limit": 10}))
        self.assertEqual(listed["jobs"][0]["id"], "j_web0001")
        self.assertIn("/console?job=", listed["jobs"][0]["workbench_url"])

    def test_http_sanitize_document_contract_and_preview_image(self):
        result = tool("sanitize_document",
                      {"path": str(fixture_text("http-doc.txt"))})
        self.assertFalse(result.get("isError"), result)
        data = payload(result)
        old_fields = {"ok", "output", "mode", "pack", "entities", "by_type",
                      "verify", "mapping_id", "warnings"}
        self.assertTrue(old_fields <= set(data), sorted(data))
        self.assertIs(data["ok"], True)
        self.assertEqual(data["verify"], {"verified": True, "residual": 0})
        self.assertEqual(data["job_id"], "j_web0001")
        self.assertEqual(data["mapping_id"], "j_web0001")
        self.assertEqual(data["origin"], "mcp")
        self.assertIs(data["workbench_available"], True)
        self.assertEqual(data["workbench_url"],
                         "http://127.0.0.1:9123/console?job=j_web0001")
        out = Path(data["output"])
        self.assertTrue(out.is_file(), out)
        self.assertEqual(out.parent.resolve(), self.web.out_dir.resolve())
        # 实体清单来自 Web API，但结果里绝不能出现原值
        dumped = visible_text(data)
        for secret in SECRETS:
            self.assertNotIn(secret, dumped)
        # 图片走 Web 渲染接口（render?mode=out）
        self.assertEqual(len(result["content"]), 2)
        image = result["content"][1]
        self.assertEqual(image["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(image["data"]), _FakeWebApi.PNG)

    def test_http_tools_flow_entities_masked_audit_resources(self):
        job_id = self._new_http_job()["job_id"]

        entities = payload(tool("get_sanitize_entities",
                                {"job_id": job_id}))
        dumped = visible_text(entities)
        for secret in SECRETS:
            self.assertNotIn(secret, dumped, f"HTTP 实体清单泄露原值：{secret}")
        self.assertTrue(any(e["value_len"] > 0
                            for e in entities["entities"]))
        self.assertTrue(all("value" not in e for e in entities["entities"]))

        updated = payload(tool("update_sanitize_entity", {
            "job_id": job_id, "entity_id": "e_web2", "action": "accept"}))
        self.assertEqual(updated["status"], "accepted")

        marked = payload(tool("mark_sanitize_region", {
            "job_id": job_id, "page": 0, "rect": [0.1, 0.1, 0.4, 0.2]}))
        self.assertEqual(marked["entity_id"], "e_web_mark")

        accepted = payload(tool("accept_all_sanitize_entities",
                                {"job_id": job_id}))
        self.assertGreaterEqual(accepted["accepted"], 1)

        exported = payload(tool("export_sanitize_job",
                                {"job_id": job_id, "mode": "ai"}))
        self.assertTrue(Path(exported["out_path"]).is_file())
        self.assertEqual(exported["mapping_id"], job_id)
        self.assertIs(exported["verify"]["verified"], True)
        self.assertIs(exported["workbench_available"], True)
        self.assertEqual(self.web.export_bodies[-1]["mode"], "ai")

        audit_data = payload(tool("get_sanitize_audit",
                                  {"job_id": job_id}))
        self.assertGreaterEqual(audit_data["count"], 1)
        self.assertIs(audit_data["verify_chain"]["ok"], True)
        self.assertTrue(all(r.get("job_id") == job_id
                            for r in audit_data["records"]))

        # resources 也优先走 HTTP（list + JSON + 页面 PNG）
        resources = rpc("resources/list")["result"]["resources"]
        self.assertIn(f"sanitize://jobs/{job_id}",
                      {item["uri"] for item in resources})
        entities_res = rpc("resources/read", {
            "uri": f"sanitize://jobs/{job_id}/entities"})
        entities_data = json.loads(
            entities_res["result"]["contents"][0]["text"])
        for secret in SECRETS:
            self.assertNotIn(secret, visible_text(entities_data))
        page = rpc("resources/read", {
            "uri": f"sanitize://jobs/{job_id}/pages/0?mode=out"})
        content = page["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(content["blob"]), _FakeWebApi.PNG)

    def test_http_media_confirmation_error_keeps_code_and_media_count(self):
        self._new_http_job()
        self.web.export_error = {
            "status": 409, "code": "media_confirmation_required",
            "message": ("检测到 2 个无法自动脱敏的隐藏对象（图表/文本框/OLE 等），"
                        "继续导出会保留它们。要继续吗？"),
        }
        result = tool("export_sanitize_job",
                      {"job_id": "j_web0001", "mode": "ai"})
        self.assertTrue(result.get("isError"), result)
        data = payload(result)
        self.assertEqual(data["code"], "media_confirmation_required")
        self.assertEqual(data["media_count"], 2)
        self.assertEqual(data["http_status"], 409)

    def test_http_verify_failure_is_error(self):
        self._new_http_job()
        self.web.export_error = {
            "status": 409, "code": "verify_failed",
            "message": "仍有 2 处原文残留",
        }
        result = tool("export_sanitize_job", {"job_id": "j_web0001"})
        self.assertTrue(result.get("isError"), result)
        self.assertEqual(payload(result)["code"], "verify_failed")

    def test_http_unavailable_returns_no_workbench_not_local_fallback(self):
        # 探活失败（强制 http）：结构化 isError + 启动引导，绝不退化成本地 job
        before_ids = {j["id"] for j in MANAGER.list(100)}
        self.web.fail_health = True
        result = tool("sanitize_file", {"path": str(fixture_text("down.txt"))})
        self.assertTrue(result.get("isError"), result)
        data = payload(result)
        self.assertEqual(data["code"], "no_workbench")
        self.assertIs(data["workbench_available"], False)
        self.assertIn("启动", data["workbench_hint"])
        self.assertEqual(data["backend"], "http")
        self.assertEqual(self.web.creates, [], "探活失败时不应再请求建任务")
        self.assertEqual({j["id"] for j in MANAGER.list(100)}, before_ids,
                         "强制 http 模式不得静默创建本地 job")

        # 健康但建任务中途断连：仍是结构化 isError（no_workbench）
        self.web.fail_health = False
        self.web.fail_create = True
        result = tool("sanitize_document",
                      {"path": str(fixture_text("down2.txt"))})
        self.assertTrue(result.get("isError"), result)
        data = payload(result)
        self.assertEqual(data["code"], "no_workbench")
        self.assertIn("启动", data["workbench_hint"])


# ────────────────────────────── resources ──────────────────────────────

class TestResources(unittest.TestCase):
    def setUp(self):
        self.job_id = new_job()["job_id"]

    def test_list_and_templates(self):
        listed = rpc("resources/list")["result"]["resources"]
        uris = {item["uri"] for item in listed}
        self.assertIn("sanitize://jobs", uris)
        self.assertIn(f"sanitize://jobs/{self.job_id}", uris)
        self.assertIn(f"sanitize://jobs/{self.job_id}/entities", uris)
        self.assertIn(f"sanitize://jobs/{self.job_id}/audit", uris)
        for item in listed:
            self.assertIn("mimeType", item)

        templates = rpc("resources/templates/list")["result"]["resourceTemplates"]
        self.assertEqual(len(templates), len(mcp_server.RESOURCE_TEMPLATES))
        patterns = {item["uriTemplate"] for item in templates}
        self.assertIn("sanitize://jobs/{job_id}", patterns)
        self.assertIn("sanitize://jobs/{job_id}/entities", patterns)
        self.assertIn("sanitize://jobs/{job_id}/audit", patterns)
        self.assertIn("sanitize://jobs/{job_id}/pages/{page}", patterns)

    def test_read_json_resources(self):
        jobs = rpc("resources/read", {"uri": "sanitize://jobs"})
        self.assertNotIn("error", jobs)
        jobs_data = json.loads(jobs["result"]["contents"][0]["text"])
        self.assertIn("jobs", jobs_data)
        self.assertTrue(jobs_data["jobs"])
        self.assertEqual(jobs["result"]["contents"][0]["mimeType"],
                         "application/json")

        snapshot = rpc("resources/read", {
            "uri": f"sanitize://jobs/{self.job_id}"})
        snapshot_data = json.loads(snapshot["result"]["contents"][0]["text"])
        self.assertEqual(snapshot_data["id"], self.job_id)
        self.assertEqual(snapshot_data["origin"], "mcp")

        entities = rpc("resources/read", {
            "uri": f"sanitize://jobs/{self.job_id}/entities"})
        entities_data = json.loads(entities["result"]["contents"][0]["text"])
        self.assertEqual(entities_data["job_id"], self.job_id)
        dumped = visible_text(entities_data)
        for secret in SECRETS:
            self.assertNotIn(secret, dumped)

        audit = rpc("resources/read", {
            "uri": f"sanitize://jobs/{self.job_id}/audit"})
        audit_data = json.loads(audit["result"]["contents"][0]["text"])
        self.assertEqual(audit_data["job_id"], self.job_id)
        self.assertIs(audit_data["verify_chain"]["ok"], True)

    def test_read_page_returns_png_blob(self):
        fake_png = b"\x89PNG\r\n\x1a\n" + b"MCP-L2-TEST"
        with mock.patch.object(mcp_server.MANAGER, "render",
                               return_value=fake_png) as render:
            data = rpc("resources/read", {
                "uri": f"sanitize://jobs/{self.job_id}/pages/0"})
        self.assertNotIn("error", data)
        content = data["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(content["blob"]), fake_png)
        render.assert_called_once_with(self.job_id, 0, "out")

        with mock.patch.object(mcp_server.MANAGER, "render",
                               return_value=fake_png):
            data = rpc("resources/read", {
                "uri": f"sanitize://jobs/{self.job_id}/pages/0?mode=orig"})
        content = data["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "image/png")

    def test_read_page_normalizes_non_png_to_png(self):
        """图片格式的成品可能渲染成 JPEG；资源仍必须按 image/png 返回。"""
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (32, 24), (200, 30, 30)).save(buffer, format="JPEG")
        with mock.patch.object(mcp_server.MANAGER, "render",
                               return_value=buffer.getvalue()):
            data = rpc("resources/read", {
                "uri": f"sanitize://jobs/{self.job_id}/pages/0?mode=out"})
        self.assertNotIn("error", data)
        content = data["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(content["blob"]).startswith(b"\x89PNG"))

    def test_read_unknown_resource_errors(self):
        missing = rpc("resources/read", {"uri": "sanitize://jobs/j_not_exists"})
        self.assertEqual(missing["error"]["code"], -32002)

        bad_scheme = rpc("resources/read", {"uri": "http://example.com/x"})
        self.assertEqual(bad_scheme["error"]["code"], -32002)

        bad_page = rpc("resources/read", {
            "uri": f"sanitize://jobs/{self.job_id}/pages/xx"})
        self.assertEqual(bad_page["error"]["code"], -32602)

        bad_mode = rpc("resources/read", {
            "uri": f"sanitize://jobs/{self.job_id}/pages/0?mode=raw"})
        self.assertEqual(bad_mode["error"]["code"], -32602)


# ────────────────────────────── prompts ──────────────────────────────

class TestPrompts(unittest.TestCase):
    def test_list_and_get(self):
        listed = rpc("prompts/list")["result"]["prompts"]
        names = {item["name"] for item in listed}
        self.assertEqual(names, {"review-before-export", "workbench-fallback"})
        for item in listed:
            self.assertTrue(item.get("description"))
            self.assertIn("arguments", item)

        job_id = new_job()["job_id"]
        review = rpc("prompts/get", {
            "name": "review-before-export", "arguments": {"job_id": job_id}})
        self.assertNotIn("error", review)
        message = review["result"]["messages"][0]
        self.assertEqual(message["role"], "user")
        self.assertEqual(message["content"]["type"], "text")
        text = message["content"]["text"]
        self.assertIn(job_id, text)
        self.assertIn("export_sanitize_job", text)
        self.assertIn("media_confirmation_required", text)

        fallback = rpc("prompts/get", {"name": "workbench-fallback"})
        self.assertIn("/console", fallback["result"]["messages"][0]["content"]["text"])

        unknown = rpc("prompts/get", {"name": "no-such-prompt"})
        self.assertEqual(unknown["error"]["code"], -32602)


# ────────────────────────────── handle() 的 tools/call 路径 ──────────────────────────────

class TestToolsCallViaHandle(unittest.TestCase):
    def test_redact_text_through_handle(self):
        resp = rpc("tools/call", {
            "name": "redact_text", "arguments": {"text": TEXT}})
        self.assertNotIn("error", resp)
        data = json.loads(resp["result"]["content"][0]["text"])
        redacted = data["redacted"]
        for secret in SECRETS:
            self.assertNotIn(secret, redacted)
        self.assertNotIn("13817264451", redacted)
        self.assertGreaterEqual(data["entities"], 2)

    def test_unknown_tool_returns_tool_error(self):
        resp = rpc("tools/call", {"name": "no_such_tool", "arguments": {}})
        self.assertTrue(resp["result"]["isError"])
        data = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(data["code"], "unknown_tool")

    def test_sanitize_file_through_handle(self):
        resp = rpc("tools/call", {
            "name": "sanitize_file",
            "arguments": {"path": str(fixture_text())}})
        self.assertNotIn("error", resp)
        self.assertFalse(resp["result"]["isError"])
        data = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(data["stage"], "ready")
        self.assertTrue(data["job_id"].startswith("j_"))


def tearDownModule() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
