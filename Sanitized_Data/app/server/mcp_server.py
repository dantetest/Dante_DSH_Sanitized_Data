"""数据隐站 · MCP 服务（stdio）

给 Claude Desktop / Cursor / 各类 Agent 用：在把内容交给大模型之前，
先把敏感信息在本机换掉或涂黑。

协议：JSON-RPC 2.0 over stdio（MCP 2024-11-05）
启动：python app/server/mcp_server.py
stdout 只输出 JSON-RPC，日志一律走 stderr。
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yinzhan import registry                      # noqa: E402
from yinzhan.config import VERSION, WORK_DIR       # noqa: E402
from yinzhan.jobs import _load_rules               # noqa: E402
from yinzhan.ocr import get_ocr                    # noqa: E402
from yinzhan.udm import (                          # noqa: E402
    Block, Document, Page, ParseContext, Span,
)
from yinzhan import pipeline                       # noqa: E402

PROTOCOL = "2024-11-05"
SERVER_NAME = "sanitized-data"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ────────────────────────── 工具定义 ──────────────────────────

TOOLS = [
    {
        "name": "sanitize_document",
        "description": ("把本地文件在本机脱敏，返回可外发副本的路径。"
                        "支持 PDF / DOCX / XLSX / 图片 / TIF / TXT。"
                        "ai 模式生成换名版（可还原），human 模式生成涂黑版（不可还原）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "待处理文件的绝对路径"},
                "mode": {"type": "string", "enum": ["ai", "human"], "default": "ai",
                         "description": "ai=换名可还原（喂模型）；human=涂黑不可还原（给人看）"},
                "strictness": {"type": "string", "enum": ["loose", "standard", "strict"],
                               "default": "standard"},
                "allow_unprocessed_media": {
                    "type": "boolean", "default": False,
                    "description": ("文件内含未脱敏的内嵌图片时是否仍然导出。"
                                    "默认 false（安全默认，会拒绝并提示）；"
                                    "用户明确同意后再传 true。"),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "redact_text",
        "description": ("把一段文本里的敏感信息在本机替换掉，返回脱敏后的文本。"
                        "适合在把聊天内容/日志/报错信息发给大模型之前先过一遍。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待脱敏文本"},
                "strictness": {"type": "string", "enum": ["loose", "standard", "strict"],
                               "default": "standard"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "check_support",
        "description": "检查某个文件格式是否支持，以及会被哪套规则包处理。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_rules",
        "description": "列出本机可用的脱敏规则包与规则条目。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ────────────────────────── 工具实现 ──────────────────────────

def _text_result(payload) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": "错误：" + msg}], "isError": True}


def tool_sanitize_document(args: dict) -> dict:
    raw = (args.get("path") or "").strip().strip('"')
    if not raw:
        return _error("缺少 path")
    src = Path(raw).expanduser()
    if not src.is_file():
        return _error(f"文件不存在：{src}")
    parser = registry.find_parser(src)
    if parser is None:
        return _error(f"暂不支持这种格式：{src.suffix or '无扩展名'}")

    mode = args.get("mode") or "ai"
    strictness = args.get("strictness") or "standard"
    ctx = ParseContext(ocr=get_ocr(), workdir=str(WORK_DIR))
    doc = parser.parse(src, ctx)
    rules, pack, _ = _load_rules(doc.fmt)
    det = pipeline.detect(doc, rules, strictness)
    # 换名版要可还原：会话映射必须保留下来（S6 红线：MCP 路径也要存映射）
    session = pipeline.Session()
    pipeline.decide(det.entities, det.rules, session)
    # 金额一致性：同一勾稽组统一缩放、合计按分项重算
    try:
        from yinzhan.replacers import amount_consistency

        plan = amount_consistency.build_amount_plan(det.entities, det.rules, session)
        if plan:
            amount_consistency.apply_plan(det.entities, plan, session)
    except Exception:
        pass
    pipeline.propagate_values(doc, det.entities)

    from yinzhan.jobs import _embedded_media_info, _low_confidence_entities, _visual_entities

    sig = pipeline.find_signature_blocks(doc)
    ents = list(det.entities)
    ents.extend(_visual_entities(doc, sig))
    ents.extend(_low_confidence_entities(doc, ents))

    # P0 安全默认：Office 里的内嵌图片当前不会自动脱敏，检测到就拒绝导出，
    # 需调用方显式传 allow_unprocessed_media=true 二次确认。
    allow_media = bool(args.get("allow_unprocessed_media"))
    media = _embedded_media_info(doc)
    media_count = int(media.get("count") or 0)
    if media_count and not allow_media:
        return _error(
            f"检测到 {media_count} 张未脱敏的内嵌图片（DOCX/XLSX 内的图片/图表），"
            "当前版本不会自动处理这些图片。如确认要导出，请传 "
            "allow_unprocessed_media=true。")

    if mode == "human":
        from yinzhan.udm import Decision
        for e in ents:
            e.decision = Decision(strategy="black", black=True, replacement=None)

    out_dir = Path.cwd() / "sanitized_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "换名" if mode == "ai" else "已隐去"
    out = out_dir / f"{src.stem}_{suffix}{src.suffix}"
    res = pipeline.export(doc, parser, ents, out, {"mode": mode})

    counts: dict[str, int] = {}
    for e in ents:
        if e.status == "rejected" or not e.decision:
            continue
        counts[e.label] = counts.get(e.label, 0) + 1

    # 出口自检
    verified, residual = None, None
    try:
        v = pipeline.verify(out, [e for e in ents if e.status != "rejected"],
                            parser, ctx, doc=doc,
                            options={"allow_unprocessed_media": allow_media})
        verified, residual = v.get("verified"), v.get("residual")
    except Exception as exc:
        verified, residual = False, str(exc)

    # 换名版映射落盘（加密），供 rehydrate 还原；MCP 没有 job_id，用 mcp_ 前缀
    mapping_id = None
    if mode == "ai" and verified:
        try:
            from yinzhan import vault
            from yinzhan.utils import now

            mapping_id = "mcp_" + now().strftime("%Y%m%d%H%M%S%f")
            extra = {"name": src.name, "source": str(src), "transport": "mcp"}
            geo_params = (doc.extra or {}).get("geo_params")
            if geo_params:
                extra["geo"] = geo_params
            vault.save_mapping(mapping_id, session.to_dict(), extra)
        except Exception as exc:
            log("保存映射失败：", exc)

    return _text_result({
        "ok": True,
        "output": str(out),
        "mode": mode,
        "pack": pack,
        "entities": len([e for e in ents if e.status != "rejected"]),
        "by_type": counts,
        "verify": {"verified": verified, "residual": residual},
        "mapping_id": mapping_id,
        "warnings": list(getattr(res, "warnings", []) or [])[:5],
    })


def _redact_text(text: str, strictness: str) -> dict:
    doc = Document(doc_id="text", path="", fmt="text")
    block = Block(id="b0", kind="text", text=text, page=0)
    doc.pages.append(Page(index=0, width=595, height=842, blocks=[block]))
    rules, pack, _ = _load_rules("text")
    det = pipeline.detect(doc, rules, strictness)
    pipeline.decide(det.entities, det.rules, pipeline.Session())

    # 把决策应用到文本上（按位置倒序替换，避免偏移错乱）
    edits = []
    for e in det.entities:
        if e.status == "rejected" or not e.decision:
            continue
        for s in e.spans:
            if s.block_id != "b0" or s.start < 0:
                continue
            if e.decision.black:
                rep = "█" * (s.end - s.start)
            else:
                rep = e.decision.replacement or ("*" * (s.end - s.start))
            edits.append((s.start, s.end, rep))
    edits.sort(key=lambda x: x[0], reverse=True)
    out = text
    for start, end, rep in edits:
        out = out[:start] + rep + out[end:]

    counts: dict[str, int] = {}
    for e in det.entities:
        if e.status == "rejected" or not e.decision:
            continue
        counts[e.label] = counts.get(e.label, 0) + 1
    return {"redacted": out, "entities": len(edits), "by_type": counts, "pack": pack}


def tool_redact_text(args: dict) -> dict:
    text = args.get("text") or ""
    if not text.strip():
        return _error("text 为空")
    return _text_result(_redact_text(text, args.get("strictness") or "standard"))


def tool_check_support(args: dict) -> dict:
    raw = (args.get("path") or "").strip().strip('"')
    if not raw:
        return _error("缺少 path")
    src = Path(raw).expanduser()
    parser = registry.find_parser(src)
    if parser is None:
        return _text_result({"supported": False, "path": str(src)})
    return _text_result({"supported": True, "path": str(src),
                         "format": parser.format_id})


def tool_list_rules(args: dict) -> dict:
    rules, label, index = _load_rules("pdf")
    by_pack: dict[str, list] = {}
    for r in rules:
        by_pack.setdefault(r.pack or "default", []).append(r)
    packs = [{
        "pack": k,
        "rules": [{"id": r.id, "entity": r.entity, "label": r.label, "level": r.level}
                  for r in v],
    } for k, v in by_pack.items()]
    return _text_result({"default_pack": label, "packs": packs})


HANDLERS = {
    "sanitize_document": tool_sanitize_document,
    "redact_text": tool_redact_text,
    "check_support": tool_check_support,
    "list_rules": tool_list_rules,
}


# ────────────────────────── JSON-RPC ──────────────────────────

def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg: dict):
    """返回响应字典；通知类消息返回 None。"""
    method = msg.get("method") or ""
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if fn is None:
            return _ok(msg_id, _error(f"未知工具：{name}"))
        try:
            return _ok(msg_id, fn(args))
        except Exception as exc:
            log("tool error:", traceback.format_exc())
            return _ok(msg_id, _error(f"{type(exc).__name__}: {exc}"))
    return _err(msg_id, -32601, f"未实现的方法：{method}")


def main() -> int:
    errors = registry.load_plugins()
    for e in errors:
        log("插件加载失败:", e.splitlines()[0])
    get_ocr()
    log(f"[{SERVER_NAME}] MCP 服务已启动（stdio），工具：{', '.join(HANDLERS)}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            sys.stdout.write(json.dumps(_err(None, -32700, "JSON 解析失败")) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
