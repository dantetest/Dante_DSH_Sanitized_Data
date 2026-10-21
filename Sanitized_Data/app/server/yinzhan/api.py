"""本地 HTTP API。

只监听 127.0.0.1；不引用任何外呼库。
前端与接口同源，跨站请求一律拒绝（防 CSRF 打本地端点）。
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import audit, registry, vault
from .config import BUILD_ID, DATA_DIR, MAX_UPLOAD, UPLOAD_DIR, VERSION, WEB_DIR
from .jobs import MANAGER
from .ocr import get_ocr
from .udm import YinzhanError
from .utils import safe_name, sha256_bytes

LOG_PATH = DATA_DIR / "server.log"
LOG_MAX_BYTES = 2 * 1024 * 1024


def log_error(text: str) -> None:
    """错误同时写 data/server.log 和标准错误，方便用户把日志发回来。"""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text.rstrip()}\n"
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            LOG_PATH.unlink(missing_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass


def _is_client_gone(exc: BaseException) -> bool:
    """浏览器关页/刷新导致的断连（WinError 10054）不是服务端错误。"""
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return True
    return getattr(exc, "winerror", None) == 10054


def _host_only(value: str) -> str:
    """从 Origin/Host/X-Forwarded-Host 里取出主机名：小写、去协议、去端口。"""
    if not value:
        return ""
    v = str(value).strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if "@" in v:                      # user:pass@host
        v = v.rsplit("@", 1)[1]
    if v.startswith("["):             # IPv6 字面量
        return v.split("]", 1)[0] + "]"
    return v.split(":", 1)[0]


ROUTES: list[tuple[str, re.Pattern, str]] = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn.__name__))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, message: str, code: str = "bad_request", status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


# ══════════════════════════ 处理函数 ══════════════════════════

@route("GET", r"/api/health")
def health(ctx):
    ocr = get_ocr()
    packs = []
    try:
        from .jobs import _load_rules
        _, _, packs = _load_rules("pdf")
    except Exception:
        pass
    try:
        from . import mediascan
        scanners = mediascan.scanner_names()
        scan_enabled = mediascan.has_enabled()
    except Exception:
        scanners, scan_enabled = [], False
    return {
        "ok": True, "version": VERSION, "build": BUILD_ID, "net": "blocked",
        "engine": {
            "ocr": bool(ocr.available),
            "ocr_error": ocr.error,
            "parsers": sorted({p.format_id for p in registry.all_parsers().values()
                               if isinstance(p, registry.Parser)}),
            "detectors": sorted(registry.all_detectors().keys()),
            "scanners": scanners,
            "scan_enabled": scan_enabled,
            "packs": packs,
            "vault_key": vault.key_fingerprint(),
            # 预览与 human 导出使用同一套涂黑矩形（v1.1.2 起）；部署自检用
            "preview_parity": "export-parity/v1",
        },
        "audit": audit.stats(),
    }


@route("POST", r"/api/upload")
def upload(ctx):
    name = urllib.parse.unquote(ctx.headers.get("X-Filename", "upload.bin"))
    data = ctx.body
    if not data:
        raise ApiError("空文件")
    if len(data) > MAX_UPLOAD:
        raise ApiError("文件太大，请改用「按路径打开」")
    uid = "u_" + os.urandom(6).hex()
    path = UPLOAD_DIR / f"{uid}_{safe_name(name)}"
    path.write_bytes(data)
    return {"upload_id": uid, "name": name, "size": len(data),
            "sha256": sha256_bytes(data), "path": str(path)}


@route("POST", r"/api/jobs")
def create_job(ctx):
    body = ctx.json()
    strictness = body.get("strictness") or "standard"
    packs = body.get("packs") or None
    # 证件脱敏档位：fields=只遮头像/姓名/身份证号/住址（默认）；whole=整卡涂黑
    id_card_mode = body.get("id_card_mode") or "fields"
    if id_card_mode not in ("fields", "whole"):
        id_card_mode = "fields"
    path: Path | None = None
    if body.get("upload_id"):
        uid = body["upload_id"]
        for p in UPLOAD_DIR.glob(f"{uid}_*"):
            path = p
            break
        if path is None:
            raise ApiError("上传的文件不见了")
    elif body.get("path"):
        path = Path(body["path"])
        if not path.exists():
            raise ApiError("路径不存在")
    else:
        raise ApiError("缺少 upload_id 或 path")
    job = MANAGER.create(path, strictness=strictness, pack_ids=packs,
                         id_card_mode=id_card_mode,
                         time_blur=bool(body.get("time_blur")))
    return {"job_id": job.id, "name": job.name, "fmt": job.fmt,
            "id_card_mode": job.id_card_mode, "time_blur": job.time_blur}


@route("GET", r"/api/jobs")
def list_jobs(ctx):
    return {"jobs": MANAGER.list()}


@route("GET", r"/api/jobs/(?P<jid>[^/]+)")
def get_job(ctx, jid):
    return MANAGER.get(jid).snapshot()


@route("GET", r"/api/jobs/(?P<jid>[^/]+)/entities")
def get_entities(ctx, jid):
    job = MANAGER.get(jid)
    with job.lock:
        return {"entities": [e.to_dict() for e in job.entities],
                "counts": job.counts()}


@route("GET", r"/api/jobs/(?P<jid>[^/]+)/preview/(?P<page>\d+)")
def preview(ctx, jid, page):
    mode = ctx.query.get("mode", "orig")
    return MANAGER.preview(jid, int(page), mode)


@route("POST", r"/api/jobs/(?P<jid>[^/]+)/mark")
def mark(ctx, jid):
    return MANAGER.mark(jid, ctx.json())


@route("POST", r"/api/jobs/(?P<jid>[^/]+)/entities/(?P<eid>[^/]+)")
def update_entity(ctx, jid, eid):
    return MANAGER.update_entity(jid, eid, ctx.json())


@route("POST", r"/api/jobs/(?P<jid>[^/]+)/accept-all")
def accept_all(ctx, jid):
    return MANAGER.accept_all(jid)


@route("POST", r"/api/jobs/(?P<jid>[^/]+)/cancel")
def cancel_job(ctx, jid):
    """扫描件 OCR 很慢，用户必须能中途喊停。"""
    return MANAGER.cancel(jid)


@route("POST", r"/api/jobs/(?P<jid>[^/]+)/minor-strict")
def minor_strict(ctx, jid):
    """P1-A：未成年人严格模式（人工确认后启用）。"""
    return MANAGER.minor_strict(jid)


@route("POST", r"/api/jobs/(?P<jid>[^/]+)/export")
def export(ctx, jid):
    body = ctx.json()
    options = {
        "allow_unprocessed_media": bool(body.get("allow_unprocessed_media")),
        # P0：PDF 附件/批注/表单默认剥离；高级用户可显式保留（写审计/告警）
        "keep_pdf_hidden": bool(body.get("keep_pdf_hidden")),
        # P1-B：Office 内嵌图片自动脱敏（默认开）；strict 时无命中图置空白+剥离隐藏对象
        "redact_office_media": bool(body.get("redact_office_media", True)),
        "strict_office_media": bool(body.get("strict_office_media", False)),
    }
    return MANAGER.export(jid, body.get("mode", "ai"), options=options)


@route("GET", r"/api/audit")
def audit_list(ctx):
    limit = int(ctx.query.get("limit", "50"))
    return {"records": audit.recent(limit), "stats": audit.stats()}


@route("POST", r"/api/audit/verify")
def audit_verify(ctx):
    return audit.verify_chain()


@route("GET", r"/api/packs")
def packs(ctx):
    from .jobs import _load_rules
    rules, label, index = _load_rules("pdf")
    detail = {}
    for r in rules:
        detail.setdefault(r.pack, []).append(r.to_dict())
    return {"packs": index, "detail": detail}


# ══════════════════════════ HTTP 层 ══════════════════════════

class Handler(BaseHTTPRequestHandler):
    server_version = "Yinzhan/" + VERSION
    protocol_version = "HTTP/1.1"

    # ── 工具 ──
    def _send(self, status: int, body: bytes, ctype: str,
              extra: dict | None = None, filename: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if filename:
            quoted = urllib.parse.quote(filename)
            self.send_header("Content-Disposition",
                             f"attachment; filename*=UTF-8''{quoted}")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if getattr(self, "_head_only", False):
            return                      # HEAD 只要响应头
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _err(self, exc: Exception):
        if isinstance(exc, ApiError):
            self._json({"error": {"code": exc.code, "message": exc.message}}, exc.status)
        elif isinstance(exc, YinzhanError):
            code = getattr(exc, "code", "internal")
            status = 409 if code in ("verify_failed",) else 400
            self._json({"error": {"code": code, "message": str(exc)}}, status)
        else:
            self._json({"error": {"code": "internal", "message": str(exc)}}, 500)

    def _same_origin(self) -> bool:
        """防 CSRF：只接受「同源」的写请求。

        部署在 nginx/Caddy 反代后面时，Origin 是 https://cka.blctek.com:8443，
        但 Host 可能被改写成 cka.blctek.com 或 127.0.0.1:8760，
        直接字符串比较会把正常请求全拦掉。所以这里只比主机名，
        并优先信任反向代理传来的 X-Forwarded-Host。
        """
        origin = self.headers.get("Origin")
        if not origin:
            return True                     # 非浏览器（curl / MCP / 本地脚本）
        origin_host = _host_only(origin)
        if not origin_host or origin_host == "null":
            return True
        candidates = []
        for key in ("X-Forwarded-Host", "Host", "X-Real-Host", "X-Forwarded-Server"):
            raw = self.headers.get(key)
            if raw:
                candidates.append(_host_only(raw.split(",")[0].strip()))
        if origin_host in [c for c in candidates if c]:
            return True
        # 额外白名单：YINZHAN_ALLOWED_ORIGINS="https://a.com,https://b.com"
        allowed = os.environ.get("YINZHAN_ALLOWED_ORIGINS", "")
        for item in allowed.split(","):
            item = item.strip()
            if item and _host_only(item) == origin_host:
                return True
        log_error("跨站请求被拒 origin=%s host=%s xfh=%s" % (
            origin, self.headers.get("Host"), self.headers.get("X-Forwarded-Host")))
        return False

    # ── 请求体 ──
    def _read_body(self) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return b""
        if n > MAX_UPLOAD:
            raise ApiError("请求体过大")
        return self.rfile.read(n)

    # ── 静态文件 ──
    # 站点结构：/ 首页 · /console 控制台 · /download 下载 · /mcp MCP 接入
    PAGE_ALIASES = {
        "": "home.html",
        "index.html": "home.html",
        "home": "home.html",
        "console": "index.html",
        "console/": "index.html",
        "app": "index.html",
        "app/": "index.html",
        "download": "download.html",
        "download/": "download.html",
        "mcp": "mcp.html",
        "mcp/": "mcp.html",
    }

    def _static(self, path: str) -> bool:
        rel = path.lstrip("/")
        if rel in self.PAGE_ALIASES:
            target = WEB_DIR / self.PAGE_ALIASES[rel]
        else:
            target = (WEB_DIR / rel).resolve()
            try:
                target.relative_to(WEB_DIR.resolve())
            except ValueError:
                self._json({"error": {"code": "forbidden", "message": "路径非法"}}, 403)
                return True
        if not target.is_file():
            return False
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        # 安装包体积大，允许浏览器缓存
        extra = {}
        if rel.startswith("downloads/"):
            extra["Cache-Control"] = "public, max-age=86400"
        self._send(200, data, ctype, extra=extra)
        return True

    # ── 分发 ──
    def _dispatch(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not self._same_origin():
            self._json({"error": {"code": "forbidden", "message": "跨站请求被拒绝"}}, 403)
            return

        # 文件下载 / 渲染单独处理
        m = re.match(r"^/api/jobs/(?P<jid>[^/]+)/render/(?P<page>\d+)$", path)
        if m and method == "GET":
            mode = urllib.parse.parse_qs(parsed.query).get("mode", ["orig"])[0]
            try:
                data = MANAGER.render(m["jid"], int(m["page"]), mode)
                self._send(200, data, "image/png")
            except Exception as exc:
                self._err(exc)
            return

        m = re.match(r"^/api/jobs/(?P<jid>[^/]+)/file$", path)
        if m and method == "GET":
            mode = urllib.parse.parse_qs(parsed.query).get("mode", ["ai"])[0]
            try:
                job = MANAGER.get(m["jid"])
                rec = job.exports.get(mode)
                if not rec:
                    raise ApiError("还没有导出过这个版本", "not_exported", 404)
                p = Path(rec["out"])
                if not p.exists():
                    raise ApiError("成品文件已被移动或删除", "gone", 404)
                self._send(200, p.read_bytes(), "application/octet-stream",
                           filename=rec["name"])
            except Exception as exc:
                self._err(exc)
            return

        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        for m_method, pattern, fname in ROUTES:
            if m_method != method:
                continue
            mm = pattern.match(path)
            if not mm:
                continue
            ctx = Ctx(self, query, mm.groupdict())
            fn = globals().get(fname)
            try:
                result = fn(ctx, **mm.groupdict())
                if isinstance(result, tuple):
                    data, ctype = result
                    self._send(200, data, ctype)
                else:
                    self._json(result)
            except Exception as exc:
                if _is_client_gone(exc):
                    return  # 浏览器先挂了，正常现象，不吵
                if not isinstance(exc, (ApiError, YinzhanError)):
                    log_error("接口 %s 处理失败\n%s" % (path, traceback.format_exc()))
                try:
                    self._err(exc)
                except Exception as send_exc:
                    if not _is_client_gone(send_exc):
                        log_error("回写错误失败：%r" % (send_exc,))
            return

        if method == "GET" and self._static(path):
            return
        self._json({"error": {"code": "not_found", "message": f"没有这个接口：{path}"}}, 404)

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        """下载器和反向代理常先发 HEAD 探测大小。"""
        self._head_only = True
        try:
            self._dispatch("GET")
        finally:
            self._head_only = False

    def do_POST(self):
        self._dispatch("POST")

    def do_OPTIONS(self):
        self._json({"ok": True})

    def log_message(self, fmt, *args):
        if os.environ.get("YINZHAN_DEBUG"):
            super().log_message(fmt, *args)


class QuietServer(ThreadingHTTPServer):
    """浏览器刷新/关页会触发 WinError 10054，socketserver 默认会打一整页堆栈。
    那不是故障，这里静音；其它异常写日志。"""

    daemon_threads = True
    # POSIX 上用 SO_REUSEADDR 便于 TIME_WAIT 后快速重启；Windows 的
    # SO_REUSEADDR 会允许与已监听端口重复绑定（端口冲突时误以为绑定成功），
    # 必须关闭，端口选择由 app.pick_port 先做 connect 探测。
    allow_reuse_address = (os.name != "nt")

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if exc is not None and _is_client_gone(exc):
            return
        log_error("请求处理异常 %s\n%s" % (client_address, traceback.format_exc()))


class Ctx:
    def __init__(self, handler: Handler, query: dict, params: dict):
        self.handler = handler
        self.query = query
        self.params = params
        self.headers = handler.headers
        self._body: bytes | None = None

    @property
    def body(self) -> bytes:
        if self._body is None:
            self._body = self.handler._read_body()
        return self._body

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            raise ApiError("请求体不是合法 JSON")


def serve(host: str, port: int) -> ThreadingHTTPServer:
    httpd = QuietServer((host, port), Handler)
    return httpd
