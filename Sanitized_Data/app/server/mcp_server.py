"""数据隐站 · MCP 服务（stdio）

给 Claude Desktop / Cursor / 各类 Agent 用：在把内容交给大模型之前，
先把敏感信息在本机换掉或涂黑。

协议：JSON-RPC 2.0 over stdio（MCP 2024-11-05 / 2025-06-18 双协议协商）
启动：python app/server/mcp_server.py
stdout 只输出 JSON-RPC，日志一律走 stderr。

L2（M2-M4 server 侧）设计要点：
- 旧 4 工具（sanitize_document / redact_text / check_support / list_rules）的
  名称、参数、返回字段冻结；sanitize_document 只新增 job_id/origin/workbench。
- 双后端（hybrid）：每次工具调用先探测本机 Web 服务（GET /api/health，0.5s）。
  * 健康 → HTTP 后端：create/get/entities/mark/export/audit/render 全部走 Web
    API 的同一个 JobManager。这样 DSH/Claude 返回的 workbench_url 打开的是
    同一个 job（Web 进程知道它），审计/成品也只落一份。
  * 不可用 → 本地 MANAGER 回退（给没起 Web 服务的 Claude Desktop/Cursor
    stdio 场景），此时 workbench_available=false + 启动引导，不再给假的深链。
  * HTTP 建任务时 body 带 origin="mcp"（任务列表显示 MCP 徽标）；HTTP 中途
    连接失败返回结构化 isError（code=no_workbench + 启动引导），绝不静默
    退化成「本地 job 却给 Web 深链」。
  * 开关：YINZHAN_MCP_BACKEND=auto（默认）| local | http。
- sanitize_document 内部 = create(origin="mcp") → 等 ready → export；
  解析/检测/决策/导出/自检/审计/映射/淘汰全部复用工作台同一套逻辑
  （不再在 MCP 层 parse/export，也不再用 Path.cwd()）。
  ai 模式的「假值→原值」映射由 Web/JobManager 的 export 统一
  vault.save_mapping() 落盘，MCP 不再自行生成 mapping_id。
- 新增 11 个 sanitize_* 工具（job 化、实体复核、框选、导出、审计、还原、
  打开工作台）+ resources（JSON 快照/实体/审计、页面 PNG）+ prompts。
- 实体清单默认脱敏：只回 value_len/spans/level/etype/label/status，绝不回原值。
- 协议版本按客户端请求协商；客户端请求不受支持时回最高支持版本。
"""
from __future__ import annotations

import base64
import functools
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request  # 仅访问本机 127.0.0.1 Web API，非外呼
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 冻结版 OpenCV 初始化 OpenCL 时会把编译日志写到 stdout，破坏 MCP JSON-RPC；
# MCP 只做本地 CPU 推理，禁用 OpenCL（也避免无 GPU 机器的驱动探测）。
os.environ.setdefault("OPENCV_OPENCL_RUNTIME", "disabled")
os.environ.setdefault("OPENCV_OPENCL_DEVICE", "disabled")
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

from yinzhan import audit, pipeline, registry, vault          # noqa: E402
from yinzhan.config import PORT, RUNTIME_PATH, VERSION        # noqa: E402
from yinzhan.jobs import MANAGER, _load_rules                 # noqa: E402
from yinzhan.ocr import get_ocr                               # noqa: E402
from yinzhan.udm import Block, Document, Page, YinzhanError   # noqa: E402

SERVER_NAME = "sanitized-data"
SERVER_TITLE = "数据隐站"
# 支持的 MCP 协议版本（从新到旧）；客户端请求受支持则回显，否则回最高版本。
SUPPORTED_PROTOCOLS = ("2025-06-18", "2024-11-05")
PROTOCOL_LATEST = SUPPORTED_PROTOCOLS[0]
# 兼容旧代码/旧脚本引用的单协议常量（新实现按客户端请求协商，见 SUPPORTED_PROTOCOLS）。
PROTOCOL = "2024-11-05"
# 没有 runtime.json 时的兜底端口（config.PORT 为 None 时）。
DEFAULT_PORT = 8760
# 工具返回的预览图上限（约 2MB）；超过则等比缩小，缩不下来就干脆不回图片。
MAX_IMAGE_BYTES = 2 * 1024 * 1024
# sanitize_document 同步等任务就绪的默认上限（秒）；大扫描件可调大。
JOB_WAIT_SEC = 600.0
# sanitize_file 只等一小会：没就绪就立即返回 job_id，让调用方轮询。
FILE_WAIT_SEC = 15.0
# 后端开关：auto=有 Web 服务就优先 HTTP；local=只用本地 MANAGER；http=强制 HTTP。
BACKEND_ENV = "YINZHAN_MCP_BACKEND"
# 没有可用工作台时的统一引导（HTTP 不可用 / 本地回退都会带上）。
START_HINT = ("本机工作台未运行：请先启动「数据隐站」本地服务"
              "（start.bat / 桌面图标），启动后再打开工作台深链；"
              "当前结果仍可直接使用 output / out_path。")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ────────────────────────── 引擎懒加载 ──────────────────────────

_ENGINE_READY = False


def _ensure_engine() -> None:
    """加载解析器/检测器插件（幂等）。

    MCP 进程在 main() 里预加载；被测试/其它进程直接 import 并调工具时，
    在第一次工具调用时补加载，保证 registry.find_parser 有解析器可用。
    """
    global _ENGINE_READY
    if _ENGINE_READY:
        return
    errors = registry.load_plugins()
    for err in errors:
        log("插件加载失败:", str(err).splitlines()[0] if err else "")
    _ENGINE_READY = True


# ────────────────────────── 结果与参数小工具 ──────────────────────────

class ToolError(Exception):
    """工具内部可预期的失败：转成 MCP 工具结果里的 isError。"""

    def __init__(self, message: str, code: str = "tool_error", **extra):
        super().__init__(message)
        self.message = message
        self.code = code
        self.extra = extra


class ProtocolError(Exception):
    """JSON-RPC 协议层错误（resources/read 找不到资源等）。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = int(code)
        self.message = message


def _text_block(payload) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, indent=2)
    return {"type": "text", "text": text}


def _text_result(payload) -> dict:
    return {"content": [_text_block(payload)], "isError": False}


def _error(msg: str, code: str | None = None, **extra) -> dict:
    """统一错误结果：content 里放 JSON，Agent 能拿到 code/结构化字段。"""
    payload: dict = {"ok": False, "error": str(msg)}
    if code:
        payload["code"] = str(code)
    payload.update(extra)
    return {"content": [_text_block(payload)], "isError": True}


def _as_bool(value, default: bool = False) -> bool:
    """严格布尔解析：不把字符串 "false" 当成 True（安全默认不能被静默放行）。"""
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "y"):
        return True
    if text in ("0", "false", "no", "off", "n", ""):
        return False
    return bool(default)


def _clamp_int(value, default: int, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(lo, min(hi, number))


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return value if value > 0 else default


def _code_for(exc: BaseException) -> str:
    """把引擎异常映射成稳定的工具错误码。"""
    code = getattr(exc, "code", None)
    if code and code != "internal":
        return str(code)
    message = str(exc)
    if "不存在" in message:
        return "job_not_found"
    if "还没准备好" in message or "还没解析完" in message or "未就绪" in message:
        return "not_ready"
    if "没有需要隐去" in message:
        return "nothing_to_export"
    if "不支持" in message:
        return "unsupported_format"
    if "越界" in message:
        return "bad_request"
    return "internal"


def _tool_guard(fn):
    """工具统一兜底：加载引擎、可预期失败转 isError、未知异常记 stderr。"""

    @functools.wraps(fn)
    def run(args=None):
        _ensure_engine()
        try:
            return fn(args or {})
        except ToolError as exc:
            return _error(exc.message, exc.code, **exc.extra)
        except YinzhanError as exc:
            return _error(str(exc), _code_for(exc))
        except Exception as exc:  # noqa: BLE001
            log("tool error:", traceback.format_exc())
            return _error(f"{type(exc).__name__}: {exc}", "internal")

    return run


# ────────────────────── 工作台深链 / runtime 端口 ──────────────────────

def _runtime_port() -> int:
    """读 data/runtime.json 的真实监听端口；缺失时退回 config.PORT / 8760。"""
    try:
        raw = json.loads(RUNTIME_PATH.read_text("utf-8"))
        port = int(raw.get("port"))
        if 0 < port < 65536:
            return port
    except Exception:
        pass
    try:
        if PORT:
            return int(PORT)
    except Exception:
        pass
    return DEFAULT_PORT


def _workbench_url(job_id: str | None = None) -> str:
    url = f"http://127.0.0.1:{_runtime_port()}/console"
    if job_id:
        url += "?job=" + urllib.parse.quote(str(job_id), safe="")
    return url


# ────────────────────── Web API 后端（hybrid） ──────────────────────

class HttpError(Exception):
    """本机 Web API 调用失败；status=0 表示连接层失败（服务没起/超时/断连）。"""

    def __init__(self, status: int, body: bytes = b"", message: str = ""):
        self.status = int(status)
        self.body = body or b""
        self.message = message or self.body[:300].decode("utf-8", "replace")
        super().__init__(self.message)


class BackendUnavailable(ToolError):
    """HTTP 后端不可用（强制 http 但探活失败，或调用中途断连）。"""

    def __init__(self, message: str | None = None, **extra):
        super().__init__(
            message or "本机数据隐站 Web 服务不可用（HTTP 后端连接失败）",
            "no_workbench",
            workbench_available=False,
            workbench_hint=START_HINT,
            backend="http",
            **extra)


def _backend_mode() -> str:
    mode = str(os.environ.get(BACKEND_ENV, "") or "").strip().lower()
    return mode if mode in ("auto", "local", "http") else "auto"


def _api_base() -> str:
    return f"http://127.0.0.1:{_runtime_port()}"


def _http_open(method: str, path: str, payload=None, timeout: float = 5.0):
    """只访问 127.0.0.1 的本机 Web API；返回 (status, body, content_type)。"""
    request = urllib.request.Request(
        _api_base() + path,
        data=(json.dumps(payload, ensure_ascii=False).encode("utf-8")
              if payload is not None else None),
        headers=({"Content-Type": "application/json; charset=utf-8"}
                 if payload is not None else {}),
        method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # 127.0.0.1
            return int(response.status), response.read(), \
                response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        raise HttpError(int(exc.code), body, f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HttpError(0, b"", f"{type(exc).__name__}: {exc}")


def _http_json(method: str, path: str, payload=None, timeout: float = 5.0):
    status, body, _ctype = _http_open(method, path, payload, timeout)
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except Exception:  # noqa: BLE001
        raise HttpError(status, body, "Web API 返回的不是 JSON")


def _http_bytes(method: str, path: str, timeout: float = 60.0) -> bytes:
    _status, body, _ctype = _http_open(method, path, None, timeout)
    return body


def _http_ready(timeout: float = 0.5) -> bool:
    """探测本机 Web 服务健康状态。

    auto 模式下没有 data/runtime.json 就不探（无服务环境每次 0.5s 白等，
    也会让单测/回归变得不确定）；强制 http 时无条件探活。
    """
    mode = _backend_mode()
    if mode == "local":
        return False
    if mode == "auto":
        try:
            if not RUNTIME_PATH.is_file():
                return False
        except Exception:  # noqa: BLE001
            return False
    try:
        data = _http_json("GET", "/api/health", timeout=timeout)
    except Exception:  # noqa: BLE001
        return False
    return isinstance(data, dict) and (data.get("ok") is True
                                       or bool(data.get("version")))


def _map_api_code(code: str, message: str) -> str:
    """把 Web API 的 error.code/message 映射成 MCP 工具错误码。"""
    code = (code or "").strip().lower()
    if code and code not in ("internal", "api_error"):
        return code
    if "任务不存在" in message:
        return "job_not_found"
    if "没有需要隐去" in message:
        return "nothing_to_export"
    if "还没准备好" in message or "还没解析完" in message:
        return "not_ready"
    if "路径不存在" in message or "缺少" in message:
        return "bad_request"
    if "不支持" in message:
        return "unsupported_format"
    return code or "api_error"


def _api_error_to_tool(status: int, body: bytes) -> ToolError:
    """Web API 错误响应 → 结构化工具错误（带 code/media_count 等）。"""
    code, message = "api_error", ""
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:  # noqa: BLE001
        data = {}
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or code)
        message = str(error.get("message") or "")
    if not message:
        message = (body[:300].decode("utf-8", "replace")
                   or f"Web API 请求失败（HTTP {status}）")
    mapped = _map_api_code(code, message)
    extra: dict = {"http_status": int(status)}
    if mapped == "media_confirmation_required":
        # api.py 的错误体不含 media_count（只改了 create 的 origin 透传），
        # 从固定文案「检测到 N 个…」里解析；拿不到就不带该字段。
        match = re.search(r"检测到\s*(\d+)\s*个", message)
        if match:
            extra["media_count"] = int(match.group(1))
    return ToolError(message, mapped, **extra)


def _resolve_backend(allow_local: bool = True):
    """每次工具调用探测一次：Web 健康→HTTP；否则本地 MANAGER 回退。

    - YINZHAN_MCP_BACKEND=local：直接本地；
    - http：强制 HTTP，探活失败返回 isError（no_workbench），不静默退化；
    - auto（默认）：无可用 Web 服务时本地回退，并在结果里带启动引导。
    """
    mode = _backend_mode()
    if mode == "local":
        return LocalBackend()
    if _http_ready():
        return HttpBackend()
    if mode == "http" or not allow_local:
        raise BackendUnavailable()
    return LocalBackend()


def _workbench_fields(job_id: str | None, backend) -> dict:
    """把「深链是否可用」明确写进工具结果，避免返回指向 Web 的假链接。"""
    if getattr(backend, "kind", "local") == "http":
        return {"workbench_available": True,
                "workbench_url": _workbench_url(job_id),
                "backend": "http"}
    return {"workbench_available": False,
            "workbench_url": None,
            "workbench_hint": START_HINT,
            "backend": "local"}


# ────────────────────────── Job 小工具 ──────────────────────────

def _local_job(job_id) -> object:
    jid = str(job_id or "").strip()
    if not jid:
        raise ToolError("缺少 job_id", "bad_request")
    try:
        return MANAGER.get(jid)
    except YinzhanError as exc:
        raise ToolError(str(exc), "job_not_found")


def _require_job_id(args: dict) -> str:
    jid = str(args.get("job_id") or "").strip()
    if not jid:
        raise ToolError("缺少 job_id", "bad_request")
    return jid


def _wait_for(job, timeout: float) -> str:
    """等 Job 进入 ready/error；超时返回当前 stage（调用方决定轮询）。"""
    deadline = time.time() + max(0.0, float(timeout))
    while True:
        with job.lock:
            stage = job.stage
        if stage in ("ready", "error"):
            return stage
        if time.time() >= deadline:
            return stage
        time.sleep(0.05)


def _normalize_create_args(args: dict) -> dict:
    """校验/归一化建任务参数（本地与 HTTP 后端共用同一套语义）。"""
    raw = (args.get("path") or "").strip().strip('"')
    if not raw:
        raise ToolError("缺少 path", "bad_request")
    src = Path(raw).expanduser()
    if not src.is_file():
        raise ToolError(f"文件不存在：{src}", "bad_request")
    strictness = str(args.get("strictness") or "standard")
    if strictness not in ("loose", "standard", "strict"):
        strictness = "standard"
    id_card_mode = str(args.get("id_card_mode") or "fields")
    if id_card_mode not in ("fields", "whole"):
        id_card_mode = "fields"
    packs = args.get("packs")
    if isinstance(packs, str):
        packs = [p.strip() for p in packs.split(",") if p.strip()]
    if isinstance(packs, (list, tuple)):
        packs = [str(p) for p in packs if str(p).strip()] or None
    else:
        packs = None
    return {
        "path": str(src),
        "strictness": strictness,
        "packs": packs,
        "id_card_mode": id_card_mode,
        "time_blur": _as_bool(args.get("time_blur")),
    }


def _job_snapshot(job) -> dict:
    """Job.snapshot()（含 origin）追加 pending，便于 Agent 展示。"""
    snap = job.snapshot()
    snap["pending"] = (snap.get("counts") or {}).get("pending", 0)
    return snap


def _require_verified(record: dict, job_id: str) -> dict:
    """出口自检必须显式 verified=True；否则删成品并返回 isError。"""
    verify = record.get("verify")
    if not isinstance(verify, dict):
        verify = {}
    if verify.get("verified") is not True:
        out = record.get("out")
        if out:
            try:
                Path(out).unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                log("删除自检未通过的成品失败:", exc)
        raise ToolError("出口自检未通过，成品已删除", "verify_failed",
                        job_id=job_id, verify=verify)
    return verify


def _entity_view(entity) -> dict:
    """实体清单的脱敏视图：绝不返回 value 原文，只给摘要/位置/状态。"""
    decision = entity.decision
    return {
        "id": entity.id,
        "label": entity.label,
        "etype": entity.etype,
        "level": entity.level,
        "status": entity.status,
        "origin": entity.origin,
        "confidence": round(float(entity.confidence or 0.0), 4),
        "value_len": len(entity.value or ""),
        "spans": [span.to_dict() for span in (entity.spans or [])],
        "strategy": decision.strategy if decision else None,
        "black": bool(decision and decision.black),
    }


# ────────────────────── 预览图（工具 content block） ──────────────────────

def _to_png_bytes(data: bytes) -> bytes | None:
    """统一成 PNG（部分图片格式的成品渲染出来是 JPEG，不能伪标 image/png）。"""
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            frame = image.copy()
        if frame.mode not in ("RGB", "RGBA", "L"):
            frame = frame.convert("RGB")
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001
        log("页面图转 PNG 失败:", exc)
        return None


def _cap_image_bytes(data: bytes, limit: int = MAX_IMAGE_BYTES) -> bytes | None:
    """把过大的 PNG 等比缩小；缩不到 limit 以内就返回 None（宁可不回图）。"""
    if not data:
        return None
    data = _to_png_bytes(data) or b""
    if not data:
        return None
    if len(data) <= limit:
        return data
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            frame = image.copy()
        if frame.mode not in ("RGB", "RGBA", "L"):
            frame = frame.convert("RGB")
        for scale in (0.8, 0.6, 0.45, 0.3, 0.2):
            width = max(1, int(frame.width * scale))
            height = max(1, int(frame.height * scale))
            buffer = io.BytesIO()
            frame.resize((width, height)).save(buffer, format="PNG", optimize=True)
            small = buffer.getvalue()
            if len(small) <= limit:
                return small
    except Exception as exc:  # noqa: BLE001
        log("预览图压缩失败:", exc)
    return None


def _image_block(backend, job_id: str) -> dict | None:
    """工具返回的预览图（优先出稿预览 mode=out）。

    本机或 HTTP 后端都可能拿不到图（flow 格式/尚未导出/服务断开），
    一律只记日志、忽略，不影响工具本身的成功结果。
    """
    try:
        data = backend.render(job_id, 0, "out")
        data = _cap_image_bytes(data or b"")
        if not data:
            return None
        return {"type": "image",
                "data": base64.b64encode(data).decode("ascii"),
                "mimeType": "image/png"}
    except Exception as exc:  # noqa: BLE001
        log("预览图生成失败（不影响工具结果）:", exc)
        return None


def _mask_remote_entity(entity: dict) -> dict:
    """Web API 的实体 dict（含原值）→ MCP 脱敏视图。

    Web 侧 e.to_dict() 会带 value 原文；MCP 返回给模型前必须统一抹掉。
    """
    if not isinstance(entity, dict):
        return {}
    try:
        confidence = round(float(entity.get("confidence") or 0.0), 4)
    except (TypeError, ValueError):
        confidence = 0.0
    spans = entity.get("spans")
    return {
        "id": entity.get("id"),
        "label": entity.get("label"),
        "etype": entity.get("type") or entity.get("etype") or "",
        "level": entity.get("level") or "",
        "status": entity.get("status") or "",
        "origin": entity.get("origin") or "",
        "confidence": confidence,
        "value_len": len(str(entity.get("value") or "")),
        "spans": spans if isinstance(spans, list) else [],
        # Web API 不回传 decision 细节：避免 reject/keep 决策把原值带出来。
        "strategy": None,
        "black": False,
    }


def _job_path(job_id: str) -> str:
    return "/api/jobs/" + urllib.parse.quote(str(job_id), safe="")


# ────────────────────────── 后端实现 ──────────────────────────

class LocalBackend:
    """没有可用 Web 服务时的本地回退后端（Claude Desktop/Cursor 直连 stdio）。"""

    kind = "local"

    # ── 创建 / 读取 ──
    def create(self, args: dict) -> str:
        params = _normalize_create_args(args)
        try:
            job = MANAGER.create(
                Path(params["path"]), strictness=params["strictness"],
                pack_ids=params["packs"], id_card_mode=params["id_card_mode"],
                time_blur=params["time_blur"], origin="mcp")
        except YinzhanError as exc:
            raise ToolError(str(exc), _code_for(exc))
        return job.id

    def wait_ready(self, job_id: str, timeout: float) -> tuple[str, dict]:
        job = _local_job(job_id)
        return _wait_for(job, timeout), _job_snapshot(job)

    def snapshot(self, job_id: str) -> dict:
        return _job_snapshot(_local_job(job_id))

    def list_jobs(self, limit: int) -> list[dict]:
        return MANAGER.list(_clamp_int(limit, 20, 1, 100))

    def entities(self, job_id: str) -> tuple[dict, list[dict]]:
        job = _local_job(job_id)
        with job.lock:
            return job.counts(), [_entity_view(e) for e in list(job.entities)]

    # ── 写操作 ──
    def update(self, job_id: str, entity_id: str, payload: dict) -> dict:
        return MANAGER.update_entity(job_id, entity_id, payload)

    def mark(self, job_id: str, payload: dict) -> dict:
        return MANAGER.mark(job_id, payload)

    def accept_all(self, job_id: str) -> dict:
        return MANAGER.accept_all(job_id)

    def export(self, job_id: str, mode: str, options: dict) -> dict:
        try:
            return MANAGER.export(job_id, mode, options=options)
        except YinzhanError as exc:
            code = _code_for(exc)
            extra: dict = {"job_id": job_id}
            if code == "media_confirmation_required":
                extra["media_count"] = int(getattr(exc, "media_count", 0) or 0)
            raise ToolError(str(exc), code, **extra)

    # ── 审计 / 渲染 ──
    def audit(self, limit: int) -> dict:
        limit = _clamp_int(limit, 20, 1, 500)
        return {"records": audit.recent(limit), "stats": audit.stats()}

    def audit_verify(self) -> dict:
        return audit.verify_chain()

    def render(self, job_id: str, page: int, mode: str) -> bytes:
        return MANAGER.render(job_id, int(page), mode)


class HttpBackend:
    """本机 Web API 后端：MCP 只做客户端，Job 的唯一事实源是 Web 进程。

    这样 workbench_url 指向的 Web 工作台认识同一个 job；审计/成品/映射
    也只有一份，不会出现「MCP 本地 job + Web 深链 404」。
    """

    kind = "http"

    @staticmethod
    def _connection_lost(exc: HttpError) -> BackendUnavailable:
        return BackendUnavailable(f"本机 Web 服务连接失败：{exc.message}")

    def _json(self, method: str, path: str, payload=None,
              timeout: float = 5.0) -> dict:
        try:
            return _http_json(method, path, payload, timeout)
        except HttpError as exc:
            if exc.status == 0:
                raise self._connection_lost(exc)
            raise _api_error_to_tool(exc.status, exc.body)

    def _bytes(self, path: str, timeout: float = 60.0) -> bytes:
        try:
            return _http_bytes("GET", path, timeout=timeout)
        except HttpError as exc:
            if exc.status == 0:
                raise self._connection_lost(exc)
            raise _api_error_to_tool(exc.status, exc.body)

    # ── 创建 / 读取 ──
    def create(self, args: dict) -> str:
        body = dict(_normalize_create_args(args))
        body["origin"] = "mcp"       # 任务列表显示 MCP 徽标
        data = self._json("POST", "/api/jobs", body, timeout=15.0)
        job_id = str(data.get("job_id") or "").strip()
        if not job_id:
            raise ToolError("Web API 未返回 job_id", "api_error")
        return job_id

    def wait_ready(self, job_id: str, timeout: float) -> tuple[str, dict]:
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            snap = self.snapshot(job_id)
            stage = str(snap.get("stage") or "")
            if stage in ("ready", "error"):
                return stage, snap
            if time.time() >= deadline:
                return stage, snap
            time.sleep(0.1)

    def snapshot(self, job_id: str) -> dict:
        return self._json("GET", _job_path(job_id))

    def list_jobs(self, limit: int) -> list[dict]:
        data = self._json("GET", "/api/jobs")
        jobs = list(data.get("jobs") or [])
        return jobs[: _clamp_int(limit, 20, 1, 100)]

    def entities(self, job_id: str) -> tuple[dict, list[dict]]:
        data = self._json("GET", f"{_job_path(job_id)}/entities",
                          timeout=15.0)
        counts = data.get("counts") or {}
        return counts, [_mask_remote_entity(e)
                        for e in (data.get("entities") or [])]

    # ── 写操作 ──
    def update(self, job_id: str, entity_id: str, payload: dict) -> dict:
        path = f"{_job_path(job_id)}/entities/{urllib.parse.quote(str(entity_id), safe='')}"
        return self._json("POST", path, payload, timeout=15.0)

    def mark(self, job_id: str, payload: dict) -> dict:
        return self._json("POST", f"{_job_path(job_id)}/mark", payload,
                          timeout=15.0)

    def accept_all(self, job_id: str) -> dict:
        return self._json("POST", f"{_job_path(job_id)}/accept-all", {},
                          timeout=15.0)

    def export(self, job_id: str, mode: str, options: dict) -> dict:
        body = {"mode": mode}
        body.update(options or {})
        # 导出会跑自检（扫描件要重解析/渲染），超时要比普通查询大得多。
        return self._json("POST", f"{_job_path(job_id)}/export", body,
                          timeout=_env_float("YINZHAN_MCP_EXPORT_TIMEOUT",
                                             JOB_WAIT_SEC))

    # ── 审计 / 渲染 ──
    def audit(self, limit: int) -> dict:
        limit = _clamp_int(limit, 20, 1, 500)
        return self._json("GET", f"/api/audit?limit={limit}")

    def audit_verify(self) -> dict:
        return self._json("POST", "/api/audit/verify", {})

    def render(self, job_id: str, page: int, mode: str) -> bytes:
        path = (f"{_job_path(job_id)}/render/{int(page)}"
                f"?mode={urllib.parse.quote(str(mode), safe='')}")
        return self._bytes(path, timeout=60.0)


# ────────────────────────── 工具定义 ──────────────────────────

TOOLS = [
    # ── 旧 4 工具：名称/参数/返回字段冻结（只允许新增字段） ──
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
    # ── 新增 11 个工具：job 化 / 实体复核 / 框选 / 导出 / 审计 / 还原 / 深链 ──
    {
        "name": "sanitize_file",
        "description": ("创建一个 MCP 脱敏任务（Job），返回 job_id/stage/workbench_url。"
                        "文件大时会先返回 parsing，用 get_sanitize_job 轮询到 ready；"
                        "之后可用 get_sanitize_entities 复核、export_sanitize_job 导出。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "待处理文件的绝对路径"},
                "mode": {"type": "string", "enum": ["ai", "human"], "default": "ai",
                         "description": "仅记录默认导出意图；导出时以导出工具参数为准"},
                "strictness": {"type": "string",
                               "enum": ["loose", "standard", "strict"],
                               "default": "standard"},
                "id_card_mode": {"type": "string", "enum": ["fields", "whole"],
                                 "default": "fields",
                                 "description": "证件档位：fields=只遮关键字段；whole=整卡涂黑"},
                "time_blur": {"type": "boolean", "default": False,
                              "description": "精确时间模糊化（只把时刻换成时段）"},
                "packs": {"type": "array", "items": {"type": "string"},
                          "description": "指定规则包 id 列表（缺省=全部适用包）"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_sanitize_job",
        "description": "读取任务快照（stage/progress/counts/pending/warnings/exports/origin）。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "list_sanitize_jobs",
        "description": "列出最近的脱敏任务（含 origin、stage、counts），用于恢复会话上下文。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1,
                                     "maximum": 100, "default": 20}},
        },
    },
    {
        "name": "get_sanitize_entities",
        "description": ("读取任务实体清单的**脱敏视图**（不返回原值）："
                        "id/label/etype/status/value_len/spans/level。"
                        "用 status=pending 只看待确认项。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "status": {"type": "string",
                           "enum": ["all", "pending", "accepted", "rejected"],
                           "default": "all"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000,
                          "default": 500},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "update_sanitize_entity",
        "description": ("更新单个实体决策：accept=接受（按默认策略脱敏）、"
                        "reject=保留原值（不再脱敏）、replace=用显式 replacement 替换。"
                        "返回更新后的计数，不返回原值。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "entity_id": {"type": "string"},
                "action": {"type": "string",
                           "enum": ["accept", "reject", "replace"]},
                "replacement": {"type": "string",
                                "description": "action=replace 时必填（显式替换值）"},
            },
            "required": ["job_id", "entity_id", "action"],
        },
    },
    {
        "name": "mark_sanitize_region",
        "description": ("人工框选补漏：在指定页面上按归一化矩形 0-1 标记一个敏感区域，"
                        "返回新实体 id。用于规则/OCR 漏检的签章、批注、表格区域等。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "page": {"type": "integer", "minimum": 0,
                         "description": "页码（0 基）"},
                "rect": {"type": "array", "items": {"type": "number"},
                         "minItems": 4, "maxItems": 4,
                         "description": "[x0,y0,x1,y1]，归一化 0-1，相对页面宽高"},
                "label": {"type": "string", "description": "可选展示名"},
                "etype": {"type": "string",
                          "description": "可选实体类型（如 SIGNATURE/SECRET/OTHER）"},
            },
            "required": ["job_id", "page", "rect"],
        },
    },
    {
        "name": "accept_all_sanitize_entities",
        "description": "一次性接受所有待确认实体（按默认策略脱敏），返回接受数量。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "export_sanitize_job",
        "description": ("导出任务成品：ai=换名可还原版，human=涂黑不可还原版。"
                        "返回 out_path/sha256/seal_no/verify/warnings。"
                        "内嵌媒体需确认时返回 code=media_confirmation_required，"
                        "用户明确同意后才可传 allow_unprocessed_media=true 重试。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "mode": {"type": "string", "enum": ["ai", "human"],
                         "default": "ai"},
                "allow_unprocessed_media": {
                    "type": "boolean", "default": False,
                    "description": "用户已确认可保留未脱敏内嵌媒体（默认 false，不静默放行）"},
                "keep_pdf_hidden": {
                    "type": "boolean", "default": False,
                    "description": "保留 PDF 附件/批注/表单等隐藏数据（默认剥离）"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "get_sanitize_audit",
        "description": ("读取审计记录（只含类型/数量/哈希，无原值）并做 HMAC 链校验；"
                        "可按 job_id 过滤。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                          "default": 20},
                "verify_chain": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "rehydrate_sanitize_text",
        "description": ("本机还原：把模型回答里的假值换回原值。只在本机显式调用，"
                        "返回文本会含原始敏感信息，请只交给本机用户。"
                        "需要 job_id 或 mapping_id（二者其一）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "mapping_id": {"type": "string"},
                "text": {"type": "string", "description": "含假值、待还原的文本"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "open_sanitize_workbench",
        "description": ("返回浏览器工作台深链（http://127.0.0.1:<端口>/console?job=<id>）。"
                        "框选补漏/整图涂黑/证件档位/时间模糊等细修建议到工作台完成，"
                        "同一 job 无缝衔接。"),
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
        },
    },
]


# ────────────────────────── 旧 4 工具实现 ──────────────────────────

@_tool_guard
def tool_sanitize_document(args: dict) -> dict:
    """旧工具：返回字段不变；HTTP 可用时走 Web 的同一个 Job，否则本地 JobManager。"""
    mode = str(args.get("mode") or "ai")
    if mode not in ("ai", "human"):
        raise ToolError("mode 只能是 ai 或 human", "bad_request")
    allow_media = _as_bool(args.get("allow_unprocessed_media"))

    backend = _resolve_backend()
    job_id = backend.create(args)
    stage, snap = backend.wait_ready(
        job_id, _env_float("YINZHAN_MCP_JOB_WAIT", JOB_WAIT_SEC))
    workbench = _workbench_fields(job_id, backend)
    if stage == "error":
        raise ToolError(
            snap.get("error") or "处理失败", "job_failed", job_id=job_id,
            stage="error",
            warnings=list(snap.get("warnings") or [])[:10], **workbench)
    if stage != "ready":
        raise ToolError(
            "任务处理超时，可稍后用 get_sanitize_job 轮询", "timeout",
            job_id=job_id, stage=stage, **workbench)

    record = backend.export(job_id, mode, {"allow_unprocessed_media": allow_media})
    verify = _require_verified(record, job_id)

    snap = backend.snapshot(job_id)
    counts, entity_views = backend.entities(job_id)
    active = [e for e in entity_views if e.get("status") != "rejected"]
    by_type: dict[str, int] = {}
    for entity in active:
        label = str(entity.get("label") or entity.get("etype") or "")
        by_type[label] = by_type.get(label, 0) + 1
    entity_count = len(active)

    # 旧字段顺序/命名保持：ok/output/mode/pack/entities/by_type/verify/
    # mapping_id/warnings；新增字段只加 job_id/origin/workbench*。
    payload = {
        "ok": True,
        "output": str(record.get("out") or ""),
        "mode": mode,
        "pack": snap.get("pack") or "",
        "entities": entity_count,
        "by_type": by_type,
        "verify": {"verified": verify.get("verified"),
                   "residual": verify.get("residual")},
        # ai 模式的映射由 Web/JobManager 的 export 以 job.id 落盘，故
        # mapping_id = job_id；human 涂黑版不可还原，没有映射。
        "mapping_id": job_id if mode == "ai" else None,
        "warnings": list(record.get("warnings") or [])[:5],
        "job_id": job_id,
        "origin": snap.get("origin") or "mcp",
        **workbench,
    }
    content = [_text_block(payload)]
    image = _image_block(backend, job_id)
    if image:
        content.append(image)
    return {"content": content, "isError": False}


@_tool_guard
def tool_redact_text(args: dict) -> dict:
    text = args.get("text") or ""
    if not text.strip():
        raise ToolError("text 为空", "bad_request")
    return _text_result(_redact_text(text, args.get("strictness") or "standard"))


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


@_tool_guard
def tool_check_support(args: dict) -> dict:
    raw = (args.get("path") or "").strip().strip('"')
    if not raw:
        raise ToolError("缺少 path", "bad_request")
    src = Path(raw).expanduser()
    parser = registry.find_parser(src)
    if parser is None:
        return _text_result({"supported": False, "path": str(src)})
    return _text_result({"supported": True, "path": str(src),
                         "format": parser.format_id})


@_tool_guard
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


# ────────────────────────── 新增 11 个工具实现 ──────────────────────────

@_tool_guard
def tool_sanitize_file(args: dict) -> dict:
    backend = _resolve_backend()
    job_id = backend.create(args)
    stage, snap = backend.wait_ready(
        job_id, _env_float("YINZHAN_MCP_FILE_WAIT", FILE_WAIT_SEC))
    workbench = _workbench_fields(job_id, backend)
    if stage == "error":
        raise ToolError(
            snap.get("error") or "处理失败", "job_failed", job_id=job_id,
            stage="error", warnings=(snap.get("warnings") or [])[:10],
            **workbench)
    payload = {
        "job_id": job_id,
        "stage": stage,
        "name": snap.get("name"),
        "fmt": snap.get("fmt"),
        "pages": snap.get("pages"),
        "progress": snap.get("progress"),
        "counts": snap.get("counts"),
        "pending": (snap.get("counts") or {}).get("pending", 0),
        "warnings": (snap.get("warnings") or [])[:10],
        "origin": snap.get("origin") or "mcp",
        "mode": str(args.get("mode") or "ai"),
        **workbench,
    }
    if stage != "ready":
        payload["note"] = "任务仍在处理中，可用 get_sanitize_job 轮询"
    return _text_result(payload)


@_tool_guard
def tool_get_sanitize_job(args: dict) -> dict:
    job_id = _require_job_id(args)
    backend = _resolve_backend()
    snap = backend.snapshot(job_id)
    snap["pending"] = (snap.get("counts") or {}).get("pending", 0)
    snap.update(_workbench_fields(job_id, backend))
    return _text_result(snap)


@_tool_guard
def tool_list_sanitize_jobs(args: dict) -> dict:
    limit = _clamp_int(args.get("limit"), 20, 1, 100)
    backend = _resolve_backend()
    jobs = backend.list_jobs(limit)
    workbench = _workbench_fields(None, backend)
    if backend.kind == "http":
        for item in jobs:
            item["workbench_url"] = _workbench_url(item.get("id"))
    return _text_result({"jobs": jobs, "count": len(jobs), "limit": limit,
                         **workbench})


@_tool_guard
def tool_get_sanitize_entities(args: dict) -> dict:
    job_id = _require_job_id(args)
    status = str(args.get("status") or "all").strip().lower()
    if status not in ("all", "pending", "accepted", "rejected"):
        status = "all"
    limit = _clamp_int(args.get("limit"), 500, 1, 2000)
    backend = _resolve_backend()
    counts, entities = backend.entities(job_id)
    if status != "all":
        entities = [e for e in entities if e.get("status") == status]
    views = entities[:limit]
    return _text_result({
        "job_id": job_id,
        "status": status,
        "counts": counts,
        "pending": counts.get("pending", 0),
        "total": len(entities),
        "returned": len(views),
        "truncated": len(entities) > limit,
        "entities": views,
        **_workbench_fields(job_id, backend),
    })


@_tool_guard
def tool_update_sanitize_entity(args: dict) -> dict:
    job_id = _require_job_id(args)
    entity_id = str(args.get("entity_id") or "").strip()
    if not entity_id:
        raise ToolError("缺少 entity_id", "bad_request")
    action = str(args.get("action") or "").strip().lower()
    if action not in ("accept", "reject", "replace"):
        raise ToolError("action 只能是 accept/reject/replace", "bad_request")
    update: dict = {"action": action}
    if action == "replace":
        replacement = args.get("replacement")
        if not isinstance(replacement, str) or not replacement.strip():
            raise ToolError("action=replace 时必须显式传 replacement",
                            "bad_request")
        update["replacement"] = replacement
    backend = _resolve_backend()
    try:
        result = backend.update(job_id, entity_id, update)
    except YinzhanError as exc:
        raise ToolError(str(exc), "entity_not_found"
                        if "实体不存在" in str(exc) else _code_for(exc))
    counts = (backend.snapshot(job_id).get("counts") or {})
    return _text_result({
        "job_id": job_id,
        "entity_id": entity_id,
        "action": action,
        "status": result.get("status"),
        "counts": counts,
        "pending": counts.get("pending", 0),
        **_workbench_fields(job_id, backend),
    })


@_tool_guard
def tool_mark_sanitize_region(args: dict) -> dict:
    job_id = _require_job_id(args)
    try:
        page = int(args.get("page", 0))
    except (TypeError, ValueError):
        raise ToolError("page 必须是整数", "bad_request")
    if page < 0:
        raise ToolError("page 不能为负数", "bad_request")
    rect = args.get("rect")
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise ToolError("rect 必须是 [x0,y0,x1,y1] 四个数字", "bad_request")
    try:
        values = [float(v) for v in rect]
    except (TypeError, ValueError):
        raise ToolError("rect 必须是数字", "bad_request")
    if any(v != v for v in values):           # NaN
        raise ToolError("rect 含非法数值", "bad_request")
    if any(v < -0.0001 or v > 1.0001 for v in values):
        raise ToolError("rect 必须是 0-1 归一化坐标（相对页面宽高）",
                        "bad_request")
    if values[2] <= values[0] or values[3] <= values[1]:
        raise ToolError("rect 的 x1/y1 必须大于 x0/y0", "bad_request")
    payload: dict = {
        "page": page,
        "rect": [max(0.0, min(1.0, v)) for v in values],
    }
    label = str(args.get("label") or "").strip()
    if label:
        payload["label"] = label
    etype = str(args.get("etype") or "").strip().upper()
    if etype:
        payload["etype"] = etype
    backend = _resolve_backend()
    try:
        result = backend.mark(job_id, payload)
    except YinzhanError as exc:
        raise ToolError(str(exc), _code_for(exc))
    counts = (backend.snapshot(job_id).get("counts") or {})
    entity = result.get("entity") or {}
    return _text_result({
        "job_id": job_id,
        "added": result.get("added", 1),
        "propagated": result.get("propagated", 0),
        "entity_id": entity.get("id"),
        "counts": counts,
        **_workbench_fields(job_id, backend),
    })


@_tool_guard
def tool_accept_all_sanitize_entities(args: dict) -> dict:
    job_id = _require_job_id(args)
    backend = _resolve_backend()
    result = backend.accept_all(job_id)
    counts = (backend.snapshot(job_id).get("counts") or {})
    return _text_result({
        "job_id": job_id,
        "accepted": result.get("accepted", 0),
        "total": result.get("total", 0),
        "counts": counts,
        "pending": counts.get("pending", 0),
        **_workbench_fields(job_id, backend),
    })


@_tool_guard
def tool_export_sanitize_job(args: dict) -> dict:
    job_id = _require_job_id(args)
    mode = str(args.get("mode") or "ai")
    if mode not in ("ai", "human"):
        raise ToolError("mode 只能是 ai 或 human", "bad_request")
    options = {
        "allow_unprocessed_media": _as_bool(args.get("allow_unprocessed_media")),
        "keep_pdf_hidden": _as_bool(args.get("keep_pdf_hidden")),
    }
    backend = _resolve_backend()
    record = backend.export(job_id, mode, options)
    verify = _require_verified(record, job_id)
    return _text_result({
        "ok": True,
        "job_id": job_id,
        "mode": mode,
        "out_path": str(record.get("out") or ""),
        "out_name": record.get("name"),
        "sha256": record.get("sha256"),
        "seal_no": record.get("seal_no"),
        "bytes": record.get("bytes"),
        "verify": verify,
        "warnings": list(record.get("warnings") or []),
        "mapping_id": job_id if mode == "ai" else None,
        "origin": "mcp",
        **_workbench_fields(job_id, backend),
    })


@_tool_guard
def tool_get_sanitize_audit(args: dict) -> dict:
    limit = _clamp_int(args.get("limit"), 20, 1, 500)
    job_filter = str(args.get("job_id") or "").strip() or None
    backend = _resolve_backend()
    # 有 job 过滤时多取一些，避免被其它任务的新记录挤出窗口。
    data = backend.audit(max(limit, 500) if job_filter else limit)
    records = list(data.get("records") or [])
    if job_filter:
        records = [r for r in records if r.get("job_id") == job_filter][:limit]
    payload: dict = {
        "records": records,
        "count": len(records),
        "job_id": job_filter,
        "stats": data.get("stats") or {},
    }
    if _as_bool(args.get("verify_chain"), default=True):
        payload["verify_chain"] = backend.audit_verify()
    return _text_result(payload)


@_tool_guard
def tool_rehydrate_sanitize_text(args: dict) -> dict:
    mapping_id = str(args.get("mapping_id") or "").strip()
    job_id = str(args.get("job_id") or "").strip()
    mid = mapping_id or job_id
    if not mid:
        raise ToolError("需要 job_id 或 mapping_id", "bad_request")
    text = args.get("text")
    if not isinstance(text, str) or not text:
        raise ToolError("text 为空", "bad_request")
    try:
        mapping = vault.load_mapping(mid)
    except Exception as exc:  # noqa: BLE001
        log("读取映射失败:", exc)
        raise ToolError("读取映射失败（映射可能已损坏或被删除）", "mapping_error")
    if not mapping:
        raise ToolError(f"找不到映射：{mid}（涂黑版没有映射，或已被删除）",
                        "mapping_not_found")
    restored = vault.rehydrate(text, mapping)
    return _text_result({
        "mapping_id": mid,
        "rehydrated": restored,
        "changed": restored != text,
        "length": len(restored),
    })


@_tool_guard
def tool_open_sanitize_workbench(args: dict) -> dict:
    job_id = str(args.get("job_id") or "").strip() or None
    backend = _resolve_backend()
    if backend.kind != "http":
        # 没有运行中的 Web 服务时，深链必然 404：明确报错 + 启动引导，不给假链接。
        raise ToolError(
            "本机工作台未运行，无法打开深链。请先启动「数据隐站」"
            "（start.bat / 桌面图标）后重试。",
            "no_workbench", workbench_available=False,
            workbench_hint=START_HINT, job_id=job_id, backend="local")
    if job_id:
        backend.snapshot(job_id)      # 校验任务存在；不存在直接返回 isError
    try:
        runtime_seen = RUNTIME_PATH.is_file()
    except Exception:
        runtime_seen = False
    return _text_result({
        "url": _workbench_url(job_id),
        "job_id": job_id,
        "port": _runtime_port(),
        "backend": "http",
        "workbench_available": True,
        "runtime_seen": runtime_seen,
    })


HANDLERS = {
    # 旧 4 工具
    "sanitize_document": tool_sanitize_document,
    "redact_text": tool_redact_text,
    "check_support": tool_check_support,
    "list_rules": tool_list_rules,
    # 新增 11 工具
    "sanitize_file": tool_sanitize_file,
    "get_sanitize_job": tool_get_sanitize_job,
    "list_sanitize_jobs": tool_list_sanitize_jobs,
    "get_sanitize_entities": tool_get_sanitize_entities,
    "update_sanitize_entity": tool_update_sanitize_entity,
    "mark_sanitize_region": tool_mark_sanitize_region,
    "accept_all_sanitize_entities": tool_accept_all_sanitize_entities,
    "export_sanitize_job": tool_export_sanitize_job,
    "get_sanitize_audit": tool_get_sanitize_audit,
    "rehydrate_sanitize_text": tool_rehydrate_sanitize_text,
    "open_sanitize_workbench": tool_open_sanitize_workbench,
}


# ────────────────────────── resources ──────────────────────────

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "sanitize://jobs/{job_id}",
        "name": "sanitize-job-snapshot",
        "title": "任务快照",
        "description": "单个脱敏任务的 stage/progress/counts/warnings/exports（JSON）",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "sanitize://jobs/{job_id}/entities",
        "name": "sanitize-job-entities",
        "title": "实体清单（脱敏视图）",
        "description": "实体 id/label/etype/status/value_len/spans/level，不返回原值（JSON）",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "sanitize://jobs/{job_id}/audit",
        "name": "sanitize-job-audit",
        "title": "审计记录 + 链校验",
        "description": "该任务的审计记录（无原值）与 HMAC 链校验结果（JSON）",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "sanitize://jobs/{job_id}/pages/{page}",
        "name": "sanitize-job-page",
        "title": "页面预览图",
        "description": ("页面 PNG；?mode=out（默认，可外发版）|orig（原图，仅本机显式读取）。"
                        "页码 0 基。"),
        "mimeType": "image/png",
    },
]

RESOURCES_JOBS = {
    "uri": "sanitize://jobs",
    "name": "sanitize-jobs",
    "title": "最近脱敏任务",
    "description": "最近任务列表（JSON，含 origin/stage/counts/workbench_url）",
    "mimeType": "application/json",
}


def _list_resources(backend) -> list[dict]:
    resources = [dict(RESOURCES_JOBS)]
    try:
        for snap in backend.list_jobs(10):
            jid = snap.get("id")
            if not jid:
                continue
            label = f"{snap.get('name') or jid}（{snap.get('stage') or '?'}）"
            resources.append({
                "uri": f"sanitize://jobs/{jid}",
                "name": f"sanitize-job-{jid}",
                "title": f"任务快照：{label}",
                "description": "任务快照（JSON）",
                "mimeType": "application/json",
            })
            resources.append({
                "uri": f"sanitize://jobs/{jid}/entities",
                "name": f"sanitize-entities-{jid}",
                "title": f"实体清单：{label}",
                "description": "实体脱敏视图（JSON）",
                "mimeType": "application/json",
            })
            resources.append({
                "uri": f"sanitize://jobs/{jid}/audit",
                "name": f"sanitize-audit-{jid}",
                "title": f"审计记录：{label}",
                "description": "审计记录 + 链校验（JSON）",
                "mimeType": "application/json",
            })
    except Exception as exc:  # noqa: BLE001
        log("resources/list 动态任务失败:", exc)
    return resources


def _json_resource(uri: str, payload) -> dict:
    return {"contents": [{
        "uri": uri,
        "mimeType": "application/json",
        "text": json.dumps(payload, ensure_ascii=False),
    }]}


def _read_resource(uri: str, backend=None) -> dict:
    """resources/read：JSON 资源返回 text，页面图返回 blob(base64)。

    backend 由调用方按 hybrid 规则解析（HTTP 优先 / 本地回退）后传入。
    """
    if backend is None:
        backend = _resolve_backend()
    uri = str(uri or "").strip()
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "sanitize":
        raise ProtocolError(-32002, f"未知资源：{uri}")
    if parsed.netloc.lower() != "jobs":
        raise ProtocolError(-32002, f"未知资源：{uri}")
    segments = [urllib.parse.unquote(s) for s in parsed.path.split("/") if s]
    query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    if not segments:
        jobs = backend.list_jobs(30)
        return _json_resource(uri, {"jobs": jobs, "count": len(jobs)})

    job_id = segments[0]
    try:
        snap = backend.snapshot(job_id)
    except ToolError as exc:
        if exc.code == "job_not_found":
            raise ProtocolError(-32002, f"任务不存在：{job_id}")
        raise

    if len(segments) == 1:
        snap = dict(snap)
        snap["pending"] = (snap.get("counts") or {}).get("pending", 0)
        return _json_resource(uri, snap)

    kind = segments[1].lower()
    if kind == "entities":
        counts, entities = backend.entities(job_id)
        return _json_resource(uri, {
            "job_id": job_id,
            "counts": counts,
            "entities": entities[:1000],
        })
    if kind == "audit":
        data = backend.audit(500)
        records = [r for r in (data.get("records") or [])
                   if r.get("job_id") == job_id]
        return _json_resource(uri, {
            "job_id": job_id,
            "records": records,
            "count": len(records),
            "verify_chain": backend.audit_verify(),
            "stats": data.get("stats") or {},
        })
    if kind == "pages":
        if len(segments) < 3:
            raise ProtocolError(-32602, "页面资源需要 /pages/{page}")
        try:
            page = int(segments[2])
        except (TypeError, ValueError):
            raise ProtocolError(-32602, f"页码非法：{segments[2]}")
        mode = str(query.get("mode") or "out").lower()
        if mode not in ("orig", "out"):
            raise ProtocolError(-32602, "mode 只能是 orig 或 out")
        try:
            data = backend.render(job_id, page, mode)
        except ToolError as exc:
            raise ProtocolError(-32603, f"页面渲染失败：{exc.message}")
        except Exception as exc:  # noqa: BLE001
            raise ProtocolError(-32603, f"页面渲染失败：{exc}")
        data = _cap_image_bytes(data or b"")
        if not data:
            raise ProtocolError(-32603, f"页面渲染失败：第 {page} 页")
        return {"contents": [{
            "uri": uri,
            "mimeType": "image/png",
            "blob": base64.b64encode(data).decode("ascii"),
        }]}
    raise ProtocolError(-32002, f"未知资源：{uri}")


# ────────────────────────── prompts ──────────────────────────

PROMPTS = [
    {
        "name": "review-before-export",
        "title": "导出前复核",
        "description": ("引导模型在导出前先查看任务/待确认实体、逐条确认或补框选，"
                        "确认后才导出，并如实汇报自检/审计结果。"),
        "arguments": [{
            "name": "job_id",
            "description": "脱敏任务 id（可省略，省略时先创建/列出任务）",
            "required": False,
        }],
    },
    {
        "name": "workbench-fallback",
        "title": "改用浏览器工作台复核",
        "description": ("复杂细修（框选补漏/整图涂黑/证件档位/时间模糊/未成年人严格模式）"
                        "改用浏览器工作台，同一 job 深链衔接。"),
        "arguments": [{
            "name": "job_id",
            "description": "脱敏任务 id（可省略）",
            "required": False,
        }],
    },
]


def _get_prompt(params: dict) -> dict:
    name = str(params.get("name") or "").strip()
    prompt = next((p for p in PROMPTS if p["name"] == name), None)
    if prompt is None:
        raise ProtocolError(-32602, f"未知提示词：{name}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    job_id = str(arguments.get("job_id") or "").strip()
    url = _workbench_url(job_id or None)
    try:
        workbench_available = _http_ready()
    except Exception:  # noqa: BLE001
        workbench_available = False
    if workbench_available:
        workbench_line = f"工作台：{url}（可用，与 MCP 是同一个 job）"
    else:
        workbench_line = (f"工作台：{url}（当前未运行；请先启动「数据隐站」"
                          f"（start.bat / 桌面图标）后再打开）")
    task = job_id or "（未指定；先调 sanitize_file 或 list_sanitize_jobs 拿 job_id）"

    if name == "review-before-export":
        text = (
            "请先完成脱敏导出前复核，再导出。\n"
            f"任务：{task}\n{workbench_line}\n\n"
            "步骤：\n"
            "1. 调 get_sanitize_job 确认 stage=ready；未就绪就轮询，"
            "或先用 sanitize_file 创建任务。\n"
            "2. 调 get_sanitize_entities(status=\"pending\") 看待确认项"
            "（返回的是脱敏摘要，不含原文）；需要时用 mark_sanitize_region "
            "补框选漏检区域。\n"
            "3. 逐条用 update_sanitize_entity（accept/reject/replace）明确处理；"
            "replace 必须提供显式 replacement。\n"
            "4. 用户明确确认后才调 export_sanitize_job；human=涂黑不可还原，"
            "必须二次确认。\n"
            "5. 若返回 media_confirmation_required，把 code/media_count 如实告知用户，"
            "等用户确认后才可传 allow_unprocessed_media=true，不要静默放行。\n"
            "6. 汇报 verify/sha256/seal_no/warnings 与 workbench_url，"
            "不要声称代替用户完成不可逆确认。"
        )
    else:
        text = (
            "需要细修时请改用本机浏览器工作台（同一 job，无感衔接）。\n"
            f"任务：{task}\n{workbench_line}\n\n"
            "适用：框选补漏、整图涂黑、证件档位（fields/whole）、精确时间模糊、"
            "未成年人严格模式、PDF 隐藏数据取舍等复杂操作。\n"
            "步骤：\n"
            "1. 调 open_sanitize_workbench 拿到深链（或直接用上面的 URL；"
            "若工具返回 no_workbench，说明本机工作台没起）。\n"
            "2. 告诉用户打开该链接；工作台里看到的与 MCP 是同一个 job，"
            "操作会同步写入审计。\n"
            "3. 如果链接打不开，提示用户先启动「数据隐站」本地服务"
            "（start.bat / 桌面图标）再打开；这不影响对话里继续用 MCP 工具。\n"
            "4. 用户改完回到对话后，用 get_sanitize_job 刷新状态，"
            "再按需要 export_sanitize_job。\n"
            "注意：不要在对话里伪造已完成的人工确认；工作台操作由用户完成。"
        )
    return {
        "description": prompt["description"],
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


# ────────────────────────── JSON-RPC ──────────────────────────

def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}


def _initialize_result(params: dict) -> dict:
    requested = str(params.get("protocolVersion") or "").strip()
    version = requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL_LATEST
    return {
        "protocolVersion": version,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
        },
        "serverInfo": {"name": SERVER_NAME, "version": VERSION,
                       "title": SERVER_TITLE},
        "instructions": ("本机脱敏工具：文件先脱敏再交给模型。"
                         "旧工具 sanitize_document 可直接用；需要复核/审计/框选时"
                         "用 sanitize_file + get_sanitize_entities + "
                         "export_sanitize_job。本机 Web 工作台运行时，MCP 自动走"
                         "同一套 HTTP Job（workbench_url 可打开同一个 job）；"
                         "未运行时返回 workbench_available=false 并提示先启动数据隐站。"),
    }


def _handle_tools_call(msg_id, params: dict):
    name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    fn = HANDLERS.get(name)
    if fn is None:
        return _ok(msg_id, _error(f"未知工具：{name}", "unknown_tool"))
    try:
        return _ok(msg_id, fn(arguments))
    except Exception as exc:  # noqa: BLE001  兜底：工具异常不掀翻协议层
        log("tool call error:", traceback.format_exc())
        return _ok(msg_id, _error(f"{type(exc).__name__}: {exc}", "internal"))


def handle(msg: dict):
    """返回响应字典；通知类消息返回 None。"""
    if not isinstance(msg, dict):
        return _err(None, -32600, "无效请求")
    method = str(msg.get("method") or "")
    msg_id = msg.get("id")
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        return _ok(msg_id, _initialize_result(params))
    if method == "notifications/initialized":
        return None
    if method.startswith("notifications/"):
        return None
    if method == "ping":
        return _ok(msg_id, {})

    try:
        if method == "tools/list":
            return _ok(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            return _handle_tools_call(msg_id, params)
        if method == "resources/list":
            try:
                backend = _resolve_backend()
            except ToolError as exc:
                return _err(msg_id, -32603, f"{exc.code}: {exc.message}")
            return _ok(msg_id, {"resources": _list_resources(backend)})
        if method == "resources/templates/list":
            return _ok(msg_id, {"resourceTemplates": RESOURCE_TEMPLATES})
        if method == "resources/read":
            uri = str(params.get("uri") or "")
            if not uri:
                return _err(msg_id, -32602, "缺少 uri")
            try:
                backend = _resolve_backend()
            except ToolError as exc:
                return _err(msg_id, -32603, f"{exc.code}: {exc.message}")
            return _ok(msg_id, _read_resource(uri, backend))
        if method == "prompts/list":
            return _ok(msg_id, {"prompts": PROMPTS})
        if method == "prompts/get":
            return _ok(msg_id, _get_prompt(params))
    except ProtocolError as exc:
        return _err(msg_id, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001
        log("protocol error:", traceback.format_exc())
        return _err(msg_id, -32603, f"内部错误：{exc}")

    if msg_id is None:
        return None
    return _err(msg_id, -32601, f"未实现的方法：{method}")


def main() -> int:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        _ensure_engine()
        get_ocr()
    except Exception as exc:  # noqa: BLE001  预加载失败不致命，工具调用时会再试
        log("预加载失败（继续启动）:", exc)
    try:
        backend_kind = "http" if _http_ready() else "local"
    except Exception:  # noqa: BLE001
        backend_kind = "local"
    log(f"[{SERVER_NAME}] MCP 服务已启动（stdio），"
        f"协议：{' / '.join(SUPPORTED_PROTOCOLS)}，"
        f"工具 {len(TOOLS)} 个，资源模板 {len(RESOURCE_TEMPLATES)} 个，"
        f"提示词 {len(PROMPTS)} 个，"
        f"后端：{backend_kind}（{_backend_mode()}）")

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:  # noqa: BLE001
                sys.stdout.write(json.dumps(
                    _err(None, -32700, "JSON 解析失败"),
                    ensure_ascii=False) + "\n")
                sys.stdout.flush()
                continue
            try:
                resp = handle(msg)
            except Exception:  # noqa: BLE001  单条消息不能带崩整个 stdio 循环
                log("handle 未捕获异常:", traceback.format_exc())
                resp = _err(msg.get("id") if isinstance(msg, dict) else None,
                            -32603, "内部错误")
            if resp is None:
                continue
            try:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                break
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
