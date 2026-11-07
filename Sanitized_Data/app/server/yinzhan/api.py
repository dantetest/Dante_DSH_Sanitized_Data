"""本地 HTTP API。

只监听 127.0.0.1；不引用任何外呼库。
前端与接口同源，跨站请求一律拒绝（防 CSRF 打本地端点）。
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import audit, logging_setup, quota, registry, restore as restore_mod, updater, vault
from .config import (BUILD_CHANNEL, BUILD_ID, COMMERCIAL_URL, DATA_DIR, MAX_UPLOAD,
                     PAYWALL_ENABLED, PURCHASE_URL, UPLOAD_DIR, VERSION, WEB_DIR)
from .jobs import MANAGER
from .licensing import LicenseError
from .ocr import get_ocr
from .udm import YinzhanError
from .utils import safe_name, sha256_bytes, sha256_file

# license / quota 共用同一个 service，避免状态文件锁和计数各算各的
LICENSE = quota.SERVICE


def log_error(text: str) -> None:
    """兼容旧调用点：错误写本地文件日志（按日期翻转）并同步到 stderr。"""
    logging_setup.log_error(text)


def log_warning(text: str) -> None:
    logging_setup.log_warning(text)


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
            "packs": quota.visible_packs(packs),
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
    if not isinstance(body, dict):
        raise ApiError("请求体必须是 JSON 对象")
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
    parser = registry.find_parser(path)
    if parser is None:
        raise ApiError("暂不支持这种文件", "free_format", 400)
    try:
        packs = quota.precheck(path, parser.format_id, packs)
    except LicenseError as exc:
        raise ApiError(exc.message, exc.code, exc.status)
    try:
        job = MANAGER.create(path, strictness=strictness, pack_ids=packs,
                             id_card_mode=id_card_mode,
                             time_blur=bool(body.get("time_blur")),
                             # MCP 客户端（Claude Desktop/DSH）经本地 HTTP 建任务时标记来源；
                             # 其他调用不传则默认 api，不影响既有行为。
                             origin="mcp" if body.get("origin") == "mcp" else "api")
    except LicenseError as exc:
        raise ApiError(exc.message, exc.code, exc.status)
    return {"job_id": job.id, "name": job.name, "fmt": job.fmt,
            "origin": job.origin,
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
    mode = str(ctx.query.get("mode", "orig") or "orig").lower()
    if mode == "redacted":            # 兼容旧调用方别名
        mode = "out"
    if mode not in ("orig", "out"):
        raise ApiError("mode 只能是 orig 或 out")
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
    raw = ctx.query.get("limit", "50")
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise ApiError("limit 必须是整数")
    limit = max(1, min(500, limit))
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
    visible = quota.visible_packs(index)
    allowed = {p.get("id") for p in visible}
    detail = {k: v for k, v in detail.items() if k in allowed}
    return {"packs": visible, "detail": detail}


# ══════════════════ AI 回件还原（大模型交回的文件 → 真值） ══════════════════
# 设计见 docs/impl/restore.md：本地解密映射、实体级回写、报告含真值但不外发。

def _restore_error(exc) -> ApiError:
    return ApiError(exc.message, exc.code, exc.status)


@route("GET", r"/api/restore/mappings")
def restore_mappings(ctx):
    items = restore_mod.list_mapping_infos()
    return {"mappings": items, "count": len(items)}


@route("POST", r"/api/restore/upload")
def restore_upload(ctx):
    name = urllib.parse.unquote(ctx.headers.get("X-Filename", "回件.bin"))
    data = ctx.body
    if not data:
        raise ApiError("空文件", "bad_request", 400)
    if len(data) > MAX_UPLOAD:
        raise ApiError("文件太大，超过本机处理上限", "too_large", 413)
    rid = "r_" + os.urandom(6).hex()
    folder = restore_mod.RESTORE_DIR / rid
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe_name(name or "回件.bin")
    path.write_bytes(data)
    try:
        prepared = restore_mod.RESTORE_MANAGER.prepare(rid, path, name or path.name)
    except restore_mod.RestoreError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise _restore_error(exc)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(folder, ignore_errors=True)
        log_error("回件读取失败 %s\n%s" % (path.name, traceback.format_exc()))
        raise ApiError(f"读取回件失败：{exc}", "restore_failed", 400)
    return prepared.public()


@route("POST", r"/api/restore/run")
def restore_run(ctx):
    body = ctx.json()
    if not isinstance(body, dict):
        raise ApiError("请求体必须是 JSON 对象")
    rid = str(body.get("restore_id") or "").strip()
    job_id = str(body.get("job_id") or "").strip()
    if not rid or not job_id:
        raise ApiError("缺少 restore_id 或 job_id")
    try:
        prepared, mapping, report = restore_mod.RESTORE_MANAGER.run(rid, job_id)
    except restore_mod.RestoreError as exc:
        raise _restore_error(exc)
    except Exception as exc:  # noqa: BLE001
        log_error("回件还原失败 rid=%s job=%s\n%s" % (rid, job_id, traceback.format_exc()))
        raise ApiError(f"还原失败：{exc}", "restore_failed", 500)
    restore_mod.RESTORE_MANAGER.attach_report(prepared, report)
    restore_mod.record_audit(mapping, prepared.path, report)
    payload = report.public()
    payload.update({"restore_id": rid, "job_id": mapping.job_id,
                    "job_name": mapping.name, "seal": mapping.seal})
    payload["outputs"] = [{
        "kind": artifact.kind,
        "name": artifact.name,
        "bytes": artifact.path.stat().st_size,
        "sha256": sha256_file(artifact.path),
        "url": f"/api/restore/file?rid={urllib.parse.quote(rid)}"
               f"&kind={urllib.parse.quote(artifact.kind)}",
    } for artifact in report.artifacts]
    # 结果页左右对照预览：回件 ↔ 还原稿（PDF/图片支持）
    payload["preview"] = restore_mod.RESTORE_MANAGER.preview_meta(rid)
    return payload


def _license_call(fn):
    try:
        return fn()
    except LicenseError as exc:
        raise ApiError(exc.message, exc.code, exc.status)


@route("GET", r"/api/license/status")
def license_status(ctx):
    def _status_with_quota():
        data = LICENSE.status()
        data["quota"] = LICENSE.quota_status()
        data["quota"]["enforced"] = quota.is_enforced()
        data["purchase_url"] = PURCHASE_URL
        # L3 扫码升级：商业服务基址（浏览器购买页跨域访问）；空串表示未配置。
        data["commercial_url"] = COMMERCIAL_URL
        data["pricing_url"] = "/pricing"
        data["paywall_enabled"] = PAYWALL_ENABLED
        data["build_channel"] = BUILD_CHANNEL
        return data
    return _license_call(_status_with_quota)


@route("GET", r"/api/quota/status")
def quota_status(ctx):
    data = _license_call(LICENSE.quota_status)
    data["enforced"] = quota.is_enforced()
    return data


@route("GET", r"/api/license/machine-code")
def license_machine_code(ctx):
    info = _license_call(LICENSE.machine_info)
    return {"machine_code": info["code"], "machine_display": info["display"]}


@route("POST", r"/api/license/import")
def license_import(ctx):
    body = ctx.json()
    if not isinstance(body, dict):
        raise ApiError("授权数据格式无效")
    if "schema" in body and "payload" in body:
        content = body
    else:
        content = body.get("content") or body.get("license") or ""
    if not content:
        raise ApiError("缺少授权文件内容")
    return _license_call(lambda: LICENSE.import_license(content))


@route("POST", r"/api/trial/start")
def trial_start(ctx):
    return _license_call(LICENSE.start_trial)


@route("GET", r"/api/update/status")
def update_status(ctx):
    return updater.UPDATE.status()


@route("POST", r"/api/update/check")
def update_check(ctx):
    return updater.UPDATE.check()


@route("POST", r"/api/update/download")
def update_download(ctx):
    return updater.UPDATE.start_download()


@route("POST", r"/api/update/install")
def update_install(ctx):
    return updater.UPDATE.install()


@route("GET", r"/api/license/file")
def license_file(ctx):
    content = LICENSE.license_content()
    if not content:
        raise ApiError("本机还没有授权文件", "license_required", 404)
    return {"content": content, "filename": "license.json"}


@route("GET", r"/api/license/events")
def license_events(ctx):
    try:
        limit = int(ctx.query.get("limit", "20"))
    except Exception:
        limit = 20
    return {"events": LICENSE.events(limit)}


# ══════════════════════════ HTTP 层 ══════════════════════════

class Handler(BaseHTTPRequestHandler):
    server_version = "Yinzhan/" + VERSION
    protocol_version = "HTTP/1.1"

    # ── 工具 ──
    def _send(self, status: int, body: bytes, ctype: str,
              extra: dict | None = None, filename: str | None = None):
        self._req_status = int(status)
        self._req_bytes = len(body)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
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

    # ── 请求日志（只记运维事实：method/path/status/耗时/字节数） ──
    def _begin_request(self) -> None:
        self._req_t0 = time.perf_counter()
        self._req_status = 0
        self._req_bytes = 0
        self._req_path = urllib.parse.urlparse(self.path).path

    def _end_request(self) -> None:
        """无论成功/异常都写一行请求日志；query 在 format_request 里丢弃。"""
        try:
            if not logging_setup.requests_enabled():
                return
            elapsed_ms = (time.perf_counter() - self._req_t0) * 1000.0
            peer = self.client_address[0] if self.client_address else ""
            client_ip = logging_setup.pick_client_ip(
                peer,
                self.headers.get("X-Real-IP", "") if self.headers else "",
                self.headers.get("X-Forwarded-For", "") if self.headers else "",
            )
            logging_setup.log_request(
                self.command or "?", self._req_path,
                self._req_status, self._req_bytes, elapsed_ms, client_ip)
        except Exception:
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
        """防 CSRF + DNS rebinding：只接受同源请求。

        部署在 nginx/Caddy 反代后面时，Origin 是 https://cka.blctek.com:8443，
        Host 可能是公网域名；服务只监听 127.0.0.1，不能因此把反代请求全拒掉。
        这里用「反代头是否由 nginx 写入」区分正常反代与 DNS rebinding：
        - 浏览器直连 127.0.0.1 的恶意域名请求：Host=evil.com，但浏览器无法
          伪造 X-Forwarded-*（fetch 自定义头会触发预检，本服务不返回 CORS）；
        - 正常反代：nginx 写入 X-Forwarded-Host/For/Proto 或 X-Real-IP。
        """
        try:
            bound_host = str(self.server.server_address[0])
        except Exception:
            bound_host = "127.0.0.1"
        host_hdr = _host_only(self.headers.get("Host", ""))
        # 反代头存在即视为来自受信反向代理；直接暴露时浏览器无法带上这些头
        behind_proxy = any(
            str(self.headers.get(key) or "").strip()
            for key in ("X-Forwarded-Host", "X-Forwarded-For",
                        "X-Forwarded-Proto", "X-Real-IP"))
        allowed_hosts = {
            _host_only(item) for item in os.environ.get("YINZHAN_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        }
        # DNS rebinding 防护：只绑回环地址时，Host 非回环时必须能证明来自反代
        if bound_host in ("127.0.0.1", "localhost", "::1"):
            if (host_hdr and host_hdr not in ("127.0.0.1", "localhost", "::1", "[::1]")
                    and not behind_proxy and host_hdr not in allowed_hosts):
                log_warning("非回环 Host 被拒（无反向代理头） host=%s" % (
                    logging_setup.sanitize_field(self.headers.get("Host", ""), 200)))
                return False
        origin = self.headers.get("Origin")
        if not origin:
            return True                     # 非浏览器（curl / MCP / 本地脚本）
        origin_host = _host_only(origin)
        if not origin_host or origin_host == "null":
            # Origin: null 来自 sandboxed iframe / file:// 页面，不能当成本地脚本；
            # 拒绝它，避免跨站写接口（upload/jobs/export/update）被盲打。
            return False
        candidates = []
        for key in ("X-Forwarded-Host", "Host", "X-Real-Host", "X-Forwarded-Server"):
            raw = self.headers.get(key)
            if raw:
                candidates.append(_host_only(raw.split(",")[0].strip()))
        if origin_host in [c for c in candidates if c]:
            return True
        if origin_host in allowed_hosts:
            return True
        # 额外白名单：YINZHAN_ALLOWED_ORIGINS="https://a.com,https://b.com"
        allowed = os.environ.get("YINZHAN_ALLOWED_ORIGINS", "")
        for item in allowed.split(","):
            item = item.strip()
            if item and _host_only(item) == origin_host:
                return True
        log_warning("跨站请求被拒 origin=%s host=%s xfh=%s" % (
            logging_setup.sanitize_field(origin, 200),
            logging_setup.sanitize_field(self.headers.get("Host", ""), 200),
            logging_setup.sanitize_field(self.headers.get("X-Forwarded-Host", ""), 200)))
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
        "license": "license.html",
        "license/": "license.html",
        "pricing": "pricing.html",
        "pricing/": "pricing.html",
        # GEO / 公开页面
        "demo": "demo.html",
        "demo/": "demo.html",
        "faq": "faq.html",
        "faq/": "faq.html",
        "compare": "compare.html",
        "compare/": "compare.html",
        "support-matrix": "support_matrix.html",
        "support-matrix/": "support_matrix.html",
        "security": "security.html",
        "security/": "security.html",
        "changelog": "changelog.html",
        "changelog/": "changelog.html",
        "privacy": "privacy.html",
        "privacy/": "privacy.html",
        "eula": "eula.html",
        "eula/": "eula.html",
        # L3 扫码升级页：同源静态页，用户主动操作时才跨域访问商业服务
        "purchase": "purchase.html",
        "purchase/": "purchase.html",
        "restore": "restore.html",
        "restore/": "restore.html",
    }

    def _static(self, path: str) -> bool:
        rel = path.lstrip("/")
        updates_dir = (DATA_DIR / "updates")
        if rel == "updates" or rel.startswith("updates/"):
            sub = rel[len("updates"):].lstrip("/")
            target = (updates_dir / sub).resolve() if sub else updates_dir
            try:
                target.relative_to(updates_dir.resolve())
            except ValueError:
                self._json({"error": {"code": "forbidden", "message": "路径非法"}}, 403)
                return True
            if not target.is_file():
                return False
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/json":
                ctype += "; charset=utf-8"
            extra = {"Cache-Control": "no-store" if sub == "latest.json" else "public, max-age=86400"}
            self._send(200, target.read_bytes(), ctype, extra=extra)
            return True
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
        # CSRF 只约束 API；静态页/JS 不应因反代 Host 差异整站 403。
        if (path == "/api" or path.startswith("/api/")) and not self._same_origin():
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

        if path == "/api/restore/file" and method == "GET":
            q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            try:
                artifact = restore_mod.RESTORE_MANAGER.artifact(
                    str(q.get("rid") or ""), str(q.get("kind") or "main"))
                if not artifact.path.is_file():
                    raise ApiError("还原文件已被移动或删除", "gone", 404)
                self._send(200, artifact.path.read_bytes(),
                           "application/octet-stream", filename=artifact.name)
            except Exception as exc:
                self._err(exc)
            return

        if path == "/api/restore/render" and method == "GET":
            q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            try:
                page = int(q.get("page") or 0)
            except (TypeError, ValueError):
                page = 0
            try:
                data, pages = restore_mod.RESTORE_MANAGER.preview_png(
                    str(q.get("rid") or ""), str(q.get("side") or "input"), page)
                self._send(200, data, "image/png",
                           extra={"Cache-Control": "no-store",
                                  "X-Preview-Pages": str(pages)})
            except restore_mod.RestoreError as exc:
                self._err(_restore_error(exc))
            except Exception as exc:  # noqa: BLE001
                log_error("回件预览渲染失败 rid=%s side=%s\n%s"
                          % (q.get("rid"), q.get("side"), traceback.format_exc()))
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
        self._begin_request()
        try:
            self._dispatch("GET")
        finally:
            self._end_request()

    def do_HEAD(self):
        """下载器和反向代理常先发 HEAD 探测大小。"""
        self._head_only = True
        self._begin_request()
        try:
            self._dispatch("GET")
        finally:
            self._head_only = False
            self._end_request()

    def do_POST(self):
        self._begin_request()
        try:
            self._dispatch("POST")
        finally:
            self._end_request()

    def do_OPTIONS(self):
        self._begin_request()
        try:
            self._json({"ok": True})
        finally:
            self._end_request()

    def log_message(self, fmt, *args):
        """http.server 默认会打印含完整请求行/query 的访问日志；已结构化覆盖，静音。"""
        return

    def log_error(self, fmt, *args):
        """协议层错误（坏请求行/头）记 WARNING；清掉 query/换行，防日志注入。"""
        try:
            message = fmt % args
        except Exception:
            message = str(fmt)
        logging_setup.get_logger("http").warning(
            "%s", logging_setup.sanitize_field(message, 500, strip_query=True))


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
        # 客户端 IP 默认不记（与请求日志同口径）；开启 YINZHAN_LOG_CLIENT_IP 才带
        peer = client_address[0] if client_address else ""
        ip = logging_setup.pick_client_ip(peer) or "-"
        log_error("请求处理异常 client=%s\n%s" % (ip, traceback.format_exc()))


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
