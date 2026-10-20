# -*- coding: utf-8 -*-
"""数据隐站 · 回归门禁共享工具（qa 所有，测试专用）。

职责：
  * 采集"基线事实"（e2e、逐样例解析/检测/导出/元数据、规则包、注册表、
    安全静态扫描、HTTP 静态冒烟 + 真实主路径、已知边界快照）；
  * 提供回归检查复用的探针（PDF/DOCX/XLSX/图片/TIFF 元数据读取、HTTP 客户端等）。

被 ``app/tests/regression.py`` 使用；也可单独 import 做实验。
只写临时目录：所有数据目录/导出文件均放在调用方给的 work_root（默认临时目录）下。

注意：Windows 控制台默认 GBK，跑测试请用 ``python -X utf8``。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
TESTS = REPO / "app" / "tests"
SAMPLES = REPO / "samples"
BASELINE_DIR = TESTS / "baseline"
BASELINE_JSON = BASELINE_DIR / "baseline.json"
SNAPSHOT_MD = BASELINE_DIR / "SNAPSHOT.md"

PY = sys.executable
GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# 由 setup() 初始化
WORK = Path()
DATA = Path()
EXPORTS = Path()
NET = Path()
_READY = False

ANSI = re.compile(r"\x1b\[[0-9;]*m")
ALLOWED_FMTS = {"pdf", "docx", "xlsx", "text", "image", "tiff", "mesh"}
ALLOWED_LEVELS = {"L0", "L1", "L2", "L3"}
META_KEYS = {"author", "creator", "producer", "subject", "keywords", "title",
             "creationdate", "moddate", "last_modified_by", "company", "manager"}

CORE_NS = "{http://purl.org/dc/elements/1.1/}"
CP_NS = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
APP_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"

GEO_TAGS = {33550: "ModelPixelScaleTag", 33922: "ModelTiepointTag",
            34264: "ModelTransformationTag", 34735: "GeoKeyDirectoryTag",
            34736: "GeoDoubleParamsTag", 34737: "GeoAsciiParamsTag",
            42112: "GDAL_METADATA", 42113: "GDAL_NODATA"}
# RPC（有理多项式系数）标签：真实遥感定位参数，必须为 0
RPC_TAGS = {50844: "RPCCoefficientTag", 50845: "RPCTransformTag"}
# 中性化 LOCAL_CS 允许保留的物理结构标签（D3 已批准：本地任意 CRS，可 GIS 打开）
LOCAL_CRS_ALLOWED_TAGS = {34264, 34735, 34737}
LOCAL_CRS_MARKER = "sanitized_data local arbitrary"
# GeoKeyDirectory 里的"真实 CRS"键：2048 地理坐标系 / 3072 投影坐标系
REAL_CRS_KEYS = {2048: "GeographicTypeGeoKey", 3072: "ProjectedCSTypeGeoKey"}

# TIFF 展示性标签：允许保留（方向/分辨率/单位）
KEEP_TIFF_TAGS = {274, 282, 283, 296}

ANSI_RE = ANSI


# ════════════════════════ 环境 ════════════════════════
def setup(work_root: str | Path | None = None, clean: bool = False) -> Path:
    """建临时目录、写环境变量、把 app/server 加进 sys.path。

    必须在 import yinzhan.* 之前调用（config 在 import 时读环境变量）。
    """
    global WORK, DATA, EXPORTS, NET, _READY
    if work_root is None:
        import tempfile
        work_root = tempfile.mkdtemp(prefix="yz_qa_")
    WORK = Path(work_root)
    if clean and WORK.exists():
        import shutil
        shutil.rmtree(WORK, ignore_errors=True)
    DATA = WORK / "data"
    EXPORTS = WORK / "exports"
    NET = WORK / "http"
    for d in (DATA, EXPORTS, NET):
        d.mkdir(parents=True, exist_ok=True)
    os.environ["YINZHAN_DATA_DIR"] = str(DATA)
    os.environ["YINZHAN_OUTPUT_DIR"] = str(DATA / "out")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.pop("YINZHAN_DEBUG", None)
    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))
    _READY = True
    return WORK


def ensure_ready() -> None:
    if not _READY:
        setup()


# ════════════════════════ 小工具 ════════════════════════
def say(msg: str = "") -> None:
    print(msg, flush=True)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_cmd(args: list[str], cwd: Path | None = None, timeout: int = 120) -> dict:
    try:
        p = subprocess.run(args, cwd=str(cwd or REPO), capture_output=True,
                           timeout=timeout)
        return {"code": p.returncode,
                "out": p.stdout.decode("utf-8", "replace").strip(),
                "err": p.stderr.decode("utf-8", "replace").strip()}
    except Exception as exc:  # noqa: BLE001
        return {"code": None, "out": "", "err": f"{type(exc).__name__}: {exc}"}


def all_text(doc) -> str:
    parts = []
    for page in doc.pages:
        for b in page.blocks:
            if b.text:
                parts.append(b.text)
            for c in getattr(b, "cells", []) or []:
                if c.text:
                    parts.append(c.text)
    return "\n".join(parts)


# ════════════════════════ 元信息 ════════════════════════
def capture_meta() -> dict:
    head = run_cmd(["git", "-C", str(REPO), "rev-parse", "HEAD"])["out"]
    branch = run_cmd(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"])["out"]
    engine_status = run_cmd(["git", "-C", str(REPO), "status", "--porcelain", "--",
                             "app/server", "samples"])["out"]
    tests_status = run_cmd(["git", "-C", str(REPO), "status", "--porcelain", "--",
                            "app/tests"])["out"]
    try:
        import importlib.util
        gdal = importlib.util.find_spec("osgeo") is not None
        trimesh = importlib.util.find_spec("trimesh") is not None
    except Exception:
        gdal = trimesh = None  # type: ignore
    return {
        "captured_at": now_iso(),
        "git_head": head,
        "git_head_short": head[:7],
        "git_branch": branch,
        "engine_dirty_status": engine_status.splitlines(),
        "engine_clean": engine_status == "",
        "tests_dirty_status": tests_status.splitlines(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "repo": str(REPO),
        "no_gdal": not gdal,
        "no_trimesh": not trimesh,
        "e2e_command": "cd app\\server; $env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\\tests\\e2e.py",
    }


# ════════════════════════ e2e 子进程 ════════════════════════
def capture_e2e(timeout: int = 300) -> dict:
    ensure_ready()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["YINZHAN_DATA_DIR"] = str(DATA)
    env["YINZHAN_OUTPUT_DIR"] = str(DATA / "out")
    t0 = time.time()
    try:
        p = subprocess.run([PY, "-X", "utf8", str(TESTS / "e2e.py")],
                           cwd=str(SERVER), capture_output=True, timeout=timeout, env=env)
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        code = p.returncode
    except Exception as exc:  # noqa: BLE001
        out, err, code = "", f"{type(exc).__name__}: {exc}", None
    elapsed = round(time.time() - t0, 2)
    clean = ANSI.sub("", out)
    summary = [ln.strip() for ln in clean.splitlines() if "汇总" in ln or "全部通过" in ln]
    ocr_line = [ln.strip() for ln in clean.splitlines() if "OCR" in ln][:1]
    tail = [ln for ln in clean.strip().splitlines() if ln.strip()][-14:]
    return {
        "exit_code": code,
        "elapsed_s": elapsed,
        "ok": code == 0,
        "summary_lines": summary,
        "ocr_line": ocr_line,
        "stdout_tail": tail,
        "stderr_tail": ANSI.sub("", err).strip().splitlines()[-6:],
    }


# ════════════════════════ 元数据探针 ════════════════════════
def pdf_meta(path: str | Path) -> dict:
    import fitz
    path = Path(path)
    d = fitz.open(str(path))
    try:
        meta = {k: v for k, v in (d.metadata or {}).items() if v}
        try:
            xmp = d.get_xml_metadata() or ""
        except Exception:
            xmp = ""
        try:
            has_meta_xref = d.xref_get_key(d.pdf_catalog(), "Metadata")[0] != "null"
        except Exception:
            has_meta_xref = False
        page_texts = []
        for page in d:
            try:
                page_texts.append(page.get_text("text"))
            except Exception:
                page_texts.append("")
    finally:
        d.close()
    raw = path.read_bytes()
    return {"nonempty": meta, "xmp_len": len(xmp.strip()),
            "has_metadata_xref": has_meta_xref,
            "raw_has_xpacket": b"xpacket" in raw,
            "text": "\n".join(page_texts)}


def pdf_identity_clean(om: dict) -> bool:
    bad = [k for k in (om.get("nonempty") or {}) if k.lower() in META_KEYS]
    return not bad and om.get("xmp_len") == 0 and not om.get("raw_has_xpacket")


def _xml_fields(root, mapping: dict) -> dict:
    out = {}
    for name, tag in mapping.items():
        try:
            el = root.find(tag)
            out[name] = (el.text or "").strip() if el is not None else ""
        except Exception:
            out[name] = ""
    return out


def _zip_part_text(path: Path, part: str, pattern: re.Pattern | None = None) -> list[str]:
    """读 zip 内某些部件的纯文本；pattern 给定时只返回匹配 pattern 的部件名。"""
    texts: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if pattern is not None and not pattern.search(name):
                    continue
                if name == part:
                    try:
                        from lxml import etree
                        root = etree.fromstring(z.read(name))
                        texts.append("|".join(t.strip() for t in root.itertext()
                                              if t.strip()))
                    except Exception:
                        texts.append(z.read(name).decode("utf-8", "replace"))
    except Exception:
        pass
    return texts


def docx_props(path: str | Path, forbidden: list[str] | None = None) -> dict:
    """docProps 结构化字段（只看元数据部件，不看正文）。"""
    from lxml import etree
    path = Path(path)
    out: dict = {"parts": {}, "fields": {}, "app_fields": {},
                 "core_text": "", "app_text": "", "forbidden_hits": []}
    forbidden = [str(f).strip() for f in (forbidden or []) if str(f).strip()]
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for part in ("docProps/core.xml", "docProps/app.xml",
                     "docProps/custom.xml", "docProps/thumbnail.jpeg"):
            out["parts"][part] = part in names
        if out["parts"]["docProps/core.xml"]:
            try:
                root = etree.fromstring(z.read("docProps/core.xml"))
                out["fields"] = _xml_fields(root, {
                    "creator": CORE_NS + "creator",
                    "lastModifiedBy": CP_NS + "lastModifiedBy",
                    "title": CORE_NS + "title",
                    "subject": CORE_NS + "subject",
                    "description": CORE_NS + "description",
                    "keywords": CP_NS + "keywords",
                    "category": CP_NS + "category",
                    "contentStatus": CP_NS + "contentStatus",
                })
                out["core_text"] = "|".join(t.strip() for t in root.itertext() if t.strip())
            except Exception as exc:  # noqa: BLE001
                out["core_text"] = f"<parse-error {exc}>"
        if out["parts"]["docProps/app.xml"]:
            try:
                root = etree.fromstring(z.read("docProps/app.xml"))
                out["app_fields"] = _xml_fields(root, {
                    "Company": APP_NS + "Company",
                    "Manager": APP_NS + "Manager",
                    "Template": APP_NS + "Template",
                })
                out["app_text"] = "|".join(t.strip() for t in root.itertext() if t.strip())
            except Exception as exc:  # noqa: BLE001
                out["app_text"] = f"<parse-error {exc}>"
    blob = out["core_text"] + "|" + out["app_text"]
    out["forbidden_hits"] = [f for f in forbidden if f in blob]
    key_fields = ("creator", "lastModifiedBy", "title", "subject", "description",
                  "keywords", "category", "contentStatus")
    out["identity_fields_empty"] = (
        all(not out["fields"].get(k) for k in key_fields)
        and all(not out["app_fields"].get(k) for k in ("Company", "Manager")))
    return out


def xlsx_props(path: str | Path, forbidden: list[str] | None = None) -> dict:
    from lxml import etree
    from openpyxl import load_workbook
    path = Path(path)
    forbidden = [str(f).strip() for f in (forbidden or []) if str(f).strip()]
    wb = load_workbook(str(path))
    props = wb.properties
    fields = ("creator", "lastModifiedBy", "title", "subject", "description",
              "keywords", "category", "contentStatus", "identifier", "language",
              "version", "lastPrinted", "created", "modified")
    vals = {}
    for f in fields:
        try:
            v = getattr(props, f, None)
            vals[f] = "" if v is None else str(v)
        except Exception:
            vals[f] = ""
    wb.close()
    out = {"nonempty_fields": {k: v for k, v in vals.items() if v},
           "parts": {}, "app_text": "", "core_text": "", "forbidden_hits": []}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for part in ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml"):
            out["parts"][part] = part in names
        for part, key in (("docProps/core.xml", "core_text"),
                          ("docProps/app.xml", "app_text")):
            if out["parts"][part]:
                try:
                    root = etree.fromstring(z.read(part))
                    out[key] = "|".join(t.strip() for t in root.itertext() if t.strip())
                except Exception as exc:  # noqa: BLE001
                    out[key] = f"<parse-error {exc}>"
    blob = out["core_text"] + "|" + out["app_text"]
    out["forbidden_hits"] = [f for f in forbidden if f in blob]
    key_fields = ("creator", "lastModifiedBy", "title", "subject", "description",
                  "keywords", "category", "contentStatus", "identifier", "language")
    out["identity_fields_empty"] = all(not out["nonempty_fields"].get(k) for k in key_fields)
    return out


def image_meta(path: str | Path) -> dict:
    from PIL import Image
    with Image.open(path) as im:
        try:
            exif = dict(im.getexif())
        except Exception:
            exif = {}
        info_keys = [k for k in im.info.keys()
                     if str(k).lower() in ("exif", "icc_profile", "xmp", "gps", "xml")]
        return {"size": list(im.size), "format": im.format,
                "info_keys": info_keys, "exif_entries": len(exif)}


def tiff_geo(path: str | Path) -> dict:
    """TIFF 地理参考探针（区分「真实世界坐标残留」与「已批准的 LOCAL_CS 中性化」）。

    验收口径（D3 已批准）：中性化成品允许物理上保留 LOCAL_CS 结构标签
    （34264/34735/34737，这是 GIS 能打开的前提），红线是**没有真实世界坐标**：
    源 ModelPixelScale/ModelTiepoint/EPSG/GDAL_METADATA/RPC 仍出现 → 残留 FAIL。
    """
    from PIL import Image
    path = Path(path)
    with Image.open(path) as im:
        try:
            tags = dict(im.tag_v2.items())
        except Exception:
            tags = {}
        frames = int(getattr(im, "n_frames", 1) or 1)
        info_keys = [k for k in im.info.keys()
                     if str(k).lower() in ("exif", "icc_profile", "xmp", "gps", "xml")]
    present_ids = [tag for tag in GEO_TAGS if tag in tags]
    present = [GEO_TAGS[t] for t in present_ids]
    rpc = [name for tag, name in RPC_TAGS.items() if tag in tags]
    ascii_params = str(tags.get(34737) or "")
    local_crs = LOCAL_CRS_MARKER in ascii_params.lower()

    # GeoKeyDirectory 里的真实 EPSG（本地 CRS 会写 3076=9001/1024=1 等非真实值）
    real_epsg: list[str] = []
    gkd = tags.get(34735)
    if isinstance(gkd, (tuple, list)) and len(gkd) >= 4:
        try:
            n = int(gkd[3])
            vals = list(gkd[4:4 + 4 * n])
            for i in range(0, len(vals) - 3, 4):
                key, _loc, _count, val = int(vals[i]), int(vals[i + 1]), \
                    int(vals[i + 2]), int(vals[i + 3])
                if key in REAL_CRS_KEYS and val not in (0, 32767):
                    real_epsg.append(f"{REAL_CRS_KEYS[key]}={val}")
        except Exception:
            pass

    real_geo: list[str] = []
    real_geo += rpc
    real_geo += real_epsg
    if local_crs:
        # LOCAL_CS 模式：只允许 34264/34735/34737；其它 Geo 结构（33550/33922/
        # 34736/42112/42113）出现即视为真实参考残留。
        real_geo += [name for tag, name in GEO_TAGS.items()
                     if tag in tags and tag not in LOCAL_CRS_ALLOWED_TAGS]
    else:
        # 没有本地 CRS 标记：任何 Geo 结构都按"真实参考"算（保守）
        real_geo = list(dict.fromkeys(present + real_geo))

    return {
        # 向后兼容字段：仅当"已中性化且无真实残留"时为空
        "geo_tags": [] if (local_crs and not real_geo) else present,
        "raw_geo_tags": present,
        "local_crs": local_crs,
        "real_geo_residual": sorted(set(real_geo)),
        "real_epsg": real_epsg,
        "rpc_tags": rpc,
        "info_keys": info_keys,
        "frames": frames,
        "tag_count": len(tags),
        "non_display_tags": [tag for tag in tags if tag not in KEEP_TIFF_TAGS],
    }


def tiff_clean(om: dict) -> bool:
    """TIF 成品是否安全：无真实地理参考残留 + 无 EXIF/ICC/XMP/GPS。"""
    return (not (om.get("real_geo_residual") or [])
            and not (om.get("info_keys") or []))


def sidecars(path: str | Path) -> dict:
    """同名 sidecar 残留（.tfw/.prj/.aux.xml/.ovr/.kml）。"""
    path = Path(path)
    cands = [path.with_suffix(".tfw"), path.with_suffix(".prj"),
             path.with_suffix(".aux.xml"), path.with_suffix(".ovr"),
             path.with_suffix(".kml"), path.parent / (path.name + ".aux.xml"),
             path.parent / (path.name + ".ovr")]
    return {c.name: c.exists() for c in cands}


# ════════════════════════ 逐样例采集 ════════════════════════
def _forbidden_from_source(fmt: str, src: dict) -> list[str]:
    forbidden: list[str] = []
    if fmt == "pdf":
        forbidden = list((src.get("nonempty") or {}).values())
    elif fmt == "docx":
        forbidden = list((src.get("fields") or {}).values())
        forbidden += list((src.get("app_fields") or {}).values())
    elif fmt == "xlsx":
        forbidden = list((src.get("nonempty_fields") or {}).values())
    return [str(v).strip() for v in forbidden if len(str(v).strip()) >= 2]


def capture_samples() -> tuple[dict, dict]:
    """对 samples/ 全量跑 解析→检测→双导出→复检→元数据复检。

    返回 (facts, runtime)：
      facts   —— 可 JSON 序列化，用于基线/检查；
      runtime —— 实体对象、文档对象等（不落盘），供回归额外断言用。
    """
    ensure_ready()
    from yinzhan import pipeline, registry
    from yinzhan.jobs import _load_rules
    from yinzhan.ocr import get_ocr
    from yinzhan.udm import Decision, ParseContext, VerifyError

    load_errors = registry.load_plugins()
    ocr = get_ocr()
    files = sorted(p for p in SAMPLES.iterdir() if p.is_file())
    out: dict = {"plugin_load_errors": [e.splitlines()[0] for e in load_errors],
                 "ocr": {"available": bool(ocr.available), "backend": ocr.backend,
                         "error": ocr.error},
                 "files": {}}
    runtime: dict = {"entities": {}, "docs": {}, "parsers": {},
                     "exports": {}, "ocr": ocr}
    for path in files:
        rec: dict = {"file": path.name, "suffix": path.suffix.lower(), "ok": False,
                     "err": "", "types": {}, "values_sha256": {},
                     "exports": {}, "warnings": [], "source_meta": {},
                     "bytes": path.stat().st_size,
                     "sha256": sha256_file(path)}
        parser = registry.find_parser(path)
        if parser is None:
            rec["err"] = "没有匹配的解析器"
            out["files"][path.name] = rec
            continue
        fmt = parser.format_id
        rec["fmt"] = fmt
        runtime["parsers"][path.name] = parser
        ctx = ParseContext(ocr=ocr, workdir=str(DATA / "work"))
        t0 = time.time()
        try:
            doc = parser.parse(path, ctx)
        except Exception as exc:  # noqa: BLE001
            rec["err"] = f"解析失败 {type(exc).__name__}: {exc}"
            out["files"][path.name] = rec
            continue
        runtime["docs"][path.name] = doc
        rec["parse_ms"] = int((time.time() - t0) * 1000)
        rec["pages"] = len(doc.pages)
        rec["blocks"] = sum(len(p.blocks) for p in doc.pages)
        rec["doc_meta"] = {k: v for k, v in (doc.meta or {}).items()
                           if isinstance(v, (str, int, float, bool))}
        try:
            if fmt == "pdf":
                rec["source_meta"] = pdf_meta(path)
            elif fmt == "docx":
                rec["source_meta"] = docx_props(path)
            elif fmt == "xlsx":
                rec["source_meta"] = xlsx_props(path)
            elif fmt == "image":
                rec["source_meta"] = image_meta(path)
            elif fmt == "tiff":
                rec["source_meta"] = tiff_geo(path)
        except Exception as exc:  # noqa: BLE001
            rec["source_meta"] = {"error": f"{type(exc).__name__}: {exc}"}

        rules, pack_label, _ = _load_rules(fmt)
        rec["pack_label"] = pack_label
        t1 = time.time()
        try:
            det = pipeline.detect(doc, rules, "standard")
            pipeline.decide(det.entities, det.rules, pipeline.Session())
        except Exception as exc:  # noqa: BLE001
            rec["err"] = f"检测失败 {type(exc).__name__}: {exc}"
            out["files"][path.name] = rec
            continue
        runtime["entities"][path.name] = det.entities
        rec["detect_ms"] = int((time.time() - t1) * 1000)
        rec["entities"] = len(det.entities)
        for e in det.entities:
            rec["types"][e.label] = rec["types"].get(e.label, 0) + 1
            if e.value:
                # 基线只存哈希，不落敏感原值
                rec["values_sha256"].setdefault(e.label, []).append(
                    sha256_bytes(e.value.encode("utf-8"))[:12])
        rec["warnings"] = list(doc.warnings) + list(det.warnings)
        forbidden = _forbidden_from_source(fmt, rec.get("source_meta") or {})
        rec["metadata_forbidden_values_count"] = len(forbidden)

        for mode in ("ai", "human"):
            suffix = ".pdf" if fmt == "pdf" else path.suffix
            out_path = EXPORTS / f"{path.stem}_{mode}{suffix}"
            runtime["exports"][(path.name, mode)] = str(out_path)
            ex: dict = {"ok": False}
            try:
                if mode == "human":
                    for e in det.entities:
                        e.decision = Decision(strategy="black", black=True)
                res = pipeline.export(doc, parser, det.entities, out_path, {"mode": mode})
                ex["size"] = res.size
                ex["warnings"] = list(res.warnings)
                try:
                    ver = pipeline.verify(out_path, det.entities, parser, ctx)
                    ex.update({"ok": True, "verified": ver.get("verified"),
                               "residual": ver.get("residual"),
                               "checked": ver.get("checked"), "issues": []})
                except VerifyError as ve:
                    ex.update({"ok": False, "err": f"VerifyError: {ve}",
                               "residual": None})
                ex["meta_check"] = _meta_check(fmt, out_path, forbidden)
            except Exception as exc:  # noqa: BLE001
                ex["err"] = f"{type(exc).__name__}: {exc}"
            rec["exports"][mode] = ex
        checks = [e.get("ok") for e in rec["exports"].values()]
        meta_ok = all((e.get("meta_check") or {}).get("identity_cleared", False)
                      for e in rec["exports"].values()
                      if isinstance(e.get("meta_check"), dict)
                      and "error" not in (e.get("meta_check") or {}))
        rec["ok"] = bool(checks and all(checks) and meta_ok)
        out["files"][path.name] = rec
        say(f"    - {path.name}: {rec.get('fmt')} {rec.get('pages')}p "
            f"{rec.get('entities')}实体 ai/human ok={rec['ok']}")
    out["summary"] = {
        "files_total": len(out["files"]),
        "files_ok": sum(1 for r in out["files"].values() if r["ok"]),
        "exports_ok": sum(1 for r in out["files"].values() for e in r["exports"].values()
                          if e.get("ok")),
        "residual_total": sum(int(e.get("residual") or 0)
                              for r in out["files"].values() for e in r["exports"].values()),
    }
    return out, runtime


def _meta_check(fmt: str, out_path: Path, forbidden: list[str]) -> dict:
    """导出成品元数据复检（硬）。"""
    if fmt == "pdf":
        om = pdf_meta(out_path)
        bad_keys = [k for k in (om.get("nonempty") or {}) if k.lower() in META_KEYS]
        return {"out": om, "bad_meta_keys": bad_keys,
                "xmp_cleared": om.get("xmp_len") == 0,
                "identity_cleared": pdf_identity_clean(om)}
    if fmt == "docx":
        om = docx_props(out_path, forbidden)
        return {"out": om, "identity_cleared": om["identity_fields_empty"],
                "thumbnail_removed": not om["parts"].get("docProps/thumbnail.jpeg", False)}
    if fmt == "xlsx":
        om = xlsx_props(out_path, forbidden)
        return {"out": om, "identity_cleared": om["identity_fields_empty"]}
    if fmt == "image":
        om = image_meta(out_path)
        return {"out": om,
                "identity_cleared": not om["info_keys"] and om["exif_entries"] == 0}
    if fmt == "tiff":
        om = tiff_geo(out_path)
        sc = sidecars(out_path)
        return {"out": om, "sidecars": sc,
                "identity_cleared": tiff_clean(om) and not any(sc.values()),
                "local_crs": om.get("local_crs"),
                "raw_geo_tags": om.get("raw_geo_tags"),
                "real_geo_residual": om.get("real_geo_residual")}
    return {}


# ════════════════════════ 规则包 + 注册表 ════════════════════════
def _validate_rules(packs, rules_mod) -> tuple[list[str], dict, dict]:
    violations: list[str] = []
    own_ids: dict[str, list[str]] = {}
    owner: dict[str, str] = {}
    for pid, rs in packs.items():
        ids = []
        for r in rs:
            if r.pack != pid:
                continue
            ids.append(r.id)
            if r.id in owner:
                violations.append(f"规则 id 全局重复：{r.id} 同时属于 {owner[r.id]} 与 {pid}")
            owner[r.id] = pid
            if not r.entity or not r.label or not r.detect:
                violations.append(f"{pid}/{r.id}: 缺少必填字段")
            if r.level not in ALLOWED_LEVELS:
                violations.append(f"{pid}/{r.id}: 非法 level {r.level}")
            bad_scope = [s for s in r.scope
                         if s not in ALLOWED_FMTS and s not in ("all", "*")]
            if bad_scope:
                violations.append(f"{pid}/{r.id}: 非法 scope {bad_scope}")
            if not r.scope:
                violations.append(f"{pid}/{r.id}: scope 为空")
            strat = (r.replace or {}).get("strategy")
            if strat not in rules_mod.KNOWN_STRATEGIES:
                violations.append(f"{pid}/{r.id}: 非法替换策略 {strat}")
        own_ids[pid] = sorted(ids)
    return violations, own_ids, owner


def capture_rules() -> dict:
    from yinzhan import rules as rules_mod
    packs = rules_mod.load_packs()
    infos = rules_mod.packs_info()
    errors = rules_mod.load_errors()
    violations, own_ids, _ = _validate_rules(packs, rules_mod)
    compiled = {}
    for fmt in sorted(ALLOWED_FMTS):
        try:
            compiled[fmt] = len(rules_mod.compile_rules(packs, fmt))
        except Exception as exc:  # noqa: BLE001
            compiled[fmt] = f"ERROR {exc}"
    metas = {m["id"]: m for m in infos}
    pack_rec = {}
    for pid in sorted(own_ids):
        m = metas.get(pid, {})
        pack_rec[pid] = {
            "name": m.get("name", pid), "version": m.get("version", 0),
            "file": m.get("file", ""), "include": m.get("include", []),
            "own_count": len(own_ids[pid]),
            "total_count": len(packs.get(pid, [])),
            "rule_ids": own_ids[pid],
        }
    defined_total = len({rid for ids in own_ids.values() for rid in ids})
    return {
        "packs": pack_rec,
        "defined_rules_total": defined_total,
        "load_errors": errors,
        "violations": violations,
        "compiled_by_fmt": compiled,
        "KNOWN_STRATEGIES": sorted(rules_mod.KNOWN_STRATEGIES),
    }


def capture_registry() -> dict:
    from yinzhan import registry
    from yinzhan.ocr import get_ocr
    errors = registry.load_plugins()
    parsers = sorted({p.format_id for p in registry.all_parsers().values()
                      if isinstance(p, registry.Parser)})
    detectors = sorted(registry.all_detectors().keys())
    replacers = {}
    for name in ("surrogate", "black", "mask"):
        try:
            replacers[name] = registry.get_replacer(name) is not None
        except Exception:
            replacers[name] = False
    ocr = get_ocr()
    return {
        "plugin_load_errors": [e.splitlines()[0] for e in errors],
        "parsers": parsers,
        "parser_exts": sorted(k for k in registry.all_parsers().keys() if k.startswith(".")),
        "detectors": detectors,
        "replacers": replacers,
        "ocr": {"available": bool(ocr.available), "backend": ocr.backend,
                "error": ocr.error},
    }


# ════════════════════════ 安全静态扫描 ════════════════════════
OUTBOUND_PATTERNS = [
    (r"^\s*import\s+requests\b", "import requests"),
    (r"^\s*from\s+requests\b", "from requests"),
    (r"^\s*import\s+httpx\b", "import httpx"),
    (r"^\s*import\s+aiohttp\b", "import aiohttp"),
    (r"^\s*import\s+urllib\.request\b", "import urllib.request"),
    (r"^\s*from\s+urllib\.request\b", "from urllib.request"),
    (r"^\s*import\s+http\.client\b", "import http.client"),
    (r"^\s*from\s+http\.client\b", "from http.client"),
    (r"urlopen\s*\(", "urlopen("),
    (r"socket\.create_connection\s*\(", "socket.create_connection("),
]
SOCKET_CONNECT_RE = re.compile(r"\.connect\s*\(")
SQLITE_RE = re.compile(r"sqlite|sqlite3|DB_PATH")
CALL_CONTEXT_RE = re.compile(
    r"urlopen\s*\(|Request\s*\(|urlretrieve\s*\(|HTTPConnection\s*\(|"
    r"webbrowser\.open\s*\(|socket\.(?:create_connection|socket)\s*\(")
LOOPBACK_RE = re.compile(r"(127\.0\.0\.1|localhost|::1|0\.0\.0\.0)")
URL_RE = re.compile(r"https?://[^\s'\"<>)]+")
URL_ALLOWLIST = ("www.w3.org", "schemas.openxmlformats.org", "schemas.microsoft.com",
                 "purl.org", "ns.adobe.com", "xml.org", "openxmlformats.org")


def capture_security_scan() -> dict:
    ensure_ready()
    targets = sorted((SERVER / "yinzhan").rglob("*.py")) + [SERVER / "run.py"]
    for extra in ("mcp_server.py", "rehydrate.py"):
        p = SERVER / extra
        if p.is_file():
            targets.append(p)
    hits: list[dict] = []
    url_literals: list[dict] = []
    urls_called: list[dict] = []
    browser_launch: list[dict] = []
    for p in targets:
        try:
            text = p.read_text("utf-8")
        except Exception:
            continue
        rel = str(p.relative_to(REPO))
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat, label in OUTBOUND_PATTERNS:
                if re.search(pat, line) and not LOOPBACK_RE.search(line):
                    hits.append({"file": rel, "line": lineno, "pattern": label,
                                 "text": stripped[:120]})
            if (SOCKET_CONNECT_RE.search(line) and "socket" in line.lower()
                    and not SQLITE_RE.search(line) and not LOOPBACK_RE.search(line)):
                hits.append({"file": rel, "line": lineno, "pattern": ".connect(",
                             "text": stripped[:120]})
            if "webbrowser.open" in line:
                browser_launch.append({"file": rel, "line": lineno,
                                       "text": stripped[:120]})
            for u in URL_RE.findall(line):
                if any(a in u for a in URL_ALLOWLIST) or LOOPBACK_RE.search(u):
                    continue
                item = {"file": rel, "line": lineno, "url": u}
                if "{" in u:
                    item["context"] = "templated_local_url"
                    url_literals.append(item)
                    continue
                if CALL_CONTEXT_RE.search(line):
                    item["context"] = "call_context"
                    urls_called.append(item)
                else:
                    item["context"] = "documentation_or_comment"
                url_literals.append(item)
    audit_src = (SERVER / "yinzhan" / "audit.py").read_text("utf-8")
    insert_cols: list[str] = []
    m = re.search(r"INSERT INTO audit\(([^)]*)\)", audit_src)
    if m:
        insert_cols = [c.strip() for c in m.group(1).split(",")]
    value_like = [c for c in insert_cols
                  if re.search(r"value|original|plain|text|content", c, re.I)]
    config_src = (SERVER / "yinzhan" / "config.py").read_text("utf-8")
    host_match = re.search(r'^HOST\s*=\s*"([^"]+)"', config_src, re.M)
    default_host = host_match.group(1) if host_match else ""
    # 依赖清单 + 网络客户端依赖检查（离线安全红线）
    req_path = SERVER / "requirements.txt"
    req_src = req_path.read_text("utf-8", "replace") if req_path.is_file() else ""
    packages = []
    for line in req_src.splitlines():
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        m = re.match(r"([A-Za-z0-9_.\-\[\]]+)", s)
        if m:
            packages.append(m.group(1).lower())
    NETWORK_LIBS = re.compile(
        r"^(requests|httpx|aiohttp|urllib3|pycurl|websocket-client|websockets|"
        r"pysocks|httplib2|treq|grequests|python-telegram-bot|discord\.py)$", re.I)
    net_deps = [p for p in packages if NETWORK_LIBS.match(p)]
    # 可选重依赖（TIF P0 可能引入 rasterio/pyproj/tifffile）：只记录，不判回归
    optional_deps: dict[str, bool] = {}
    try:
        import importlib.util
        for name in ("rasterio", "pyproj", "tifffile", "osgeo", "zxingcpp",
                     "rapidocr", "rapidocr_onnxruntime"):
            try:
                optional_deps[name] = importlib.util.find_spec(name) is not None
            except Exception:
                optional_deps[name] = False
    except Exception:
        pass
    return {
        "outbound_hits": hits,
        "outbound_ok": not hits,
        "external_url_literals": url_literals,
        "external_urls_called": urls_called,
        "external_urls_ok": not urls_called,
        "browser_launch": browser_launch,
        "audit_insert_columns": insert_cols,
        "audit_value_like_columns": value_like,
        "audit_ok": not value_like,
        "listen_host_config": default_host,
        "requirements_packages": packages,
        "network_deps": net_deps,
        "requirements_offline_ok": not net_deps,
        "optional_deps": optional_deps,
    }


# ════════════════════════ HTTP ════════════════════════
def http_json(url: str, method: str = "GET", payload: dict | None = None,
              headers: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    hdrs = {"Content-Type": "application/json"} if data is not None else {}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status,
                    "ctype": r.headers.get("Content-Type", ""),
                    "body": r.read()}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ctype": e.headers.get("Content-Type", ""),
                "body": e.read()}
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "ctype": "", "body": b"", "err": repr(exc)}


def http_bytes(url: str, method: str = "GET", payload: bytes | None = None,
               headers: dict | None = None, timeout: int = 120) -> dict:
    hdrs = dict(headers or {})
    req = urllib.request.Request(url, data=payload, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "ctype": r.headers.get("Content-Type", ""),
                    "disposition": r.headers.get("Content-Disposition", ""),
                    "body": r.read()}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ctype": e.headers.get("Content-Type", ""),
                "body": e.read()}
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "ctype": "", "body": b"", "err": repr(exc)}


def pick_port(preferred: int = 18763) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def wait_health(url: str, timeout: float = 90) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = http_json(url + "/api/health", timeout=3)
        if r["status"] == 200:
            return True
        time.sleep(0.3)
    return False


def _wait_job(base: str, jid: str, timeout: float = 180):
    """轮询任务到 ready/error，返回 (snapshot, stage, elapsed)。"""
    t0 = time.time()
    snap: dict = {}
    stage = ""
    while time.time() - t0 < timeout:
        rg = http_json(base + f"/api/jobs/{jid}")
        try:
            snap = json.loads(rg["body"] or b"{}")
        except Exception:
            snap = {}
        stage = snap.get("stage") or ""
        if stage in ("ready", "error"):
            break
        time.sleep(0.25)
    return snap, stage, round(time.time() - t0, 2)


def _capture_id_card_modes(base: str, sample: Path) -> dict:
    """身份证档位 API 契约：默认 fields、非法值回退 fields、whole 原样。"""
    out: dict = {}
    cases = (
        ("default", {"path": str(sample)}),
        ("invalid", {"path": str(sample), "id_card_mode": "bogus"}),
        ("whole", {"path": str(sample), "id_card_mode": "whole"}),
    )
    for label, body in cases:
        r = http_json(base + "/api/jobs", "POST", body)
        try:
            jb = json.loads(r["body"] or b"{}")
        except Exception:
            jb = {}
        jid = jb.get("job_id")
        rec = {"create_status": r["status"],
               "create_id_card_mode": jb.get("id_card_mode"),
               "job_id": jid}
        if jid:
            snap, stage, poll_s = _wait_job(base, jid)
            rec.update({"stage": stage, "poll_s": poll_s,
                        "id_card_mode": snap.get("id_card_mode"),
                        "warnings": (snap.get("warnings") or [])[:3]})
        out[label] = rec
    return out


def capture_http() -> dict:
    """启动真实服务（带启动校验与端口重试），返回冒烟 + 主路径事实。

    并发注意：多名开发/CI 可能同时跑门禁，端口可能被别的实例抢走。
    这里每次启动后都校验 runtime.json 的 pid/port 必须属于本次子进程，
    对不上就换端口重试，避免"测到别人的服务/别人的数据目录"。
    """
    ensure_ready()
    last: dict = {}
    base_port = 18763 + (os.getpid() % 17)
    for attempt in range(6):
        port = pick_port(base_port + attempt * 3)
        out = _capture_http_once(port)
        out["attempt"] = attempt + 1
        runtime = out.get("runtime") or {}
        if out.get("started") and runtime.get("pid"):
            return out
        last = out
        if out.get("server_log_tail"):
            last["last_error_log"] = out["server_log_tail"][-3:]
        time.sleep(0.2)
    last.setdefault("error", "服务启动/端口校验重试全部失败")
    return last


def _capture_http_once(port: int) -> dict:
    """在指定端口启动一次服务并跑冒烟 + 主路径（校验失败由 capture_http 重试）。"""
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env.update({"YINZHAN_DATA_DIR": str(DATA), "YINZHAN_OUTPUT_DIR": str(DATA / "out"),
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    log_path = WORK / f"server_{port}.log"
    log_fh = open(log_path, "wb")
    cmd = [PY, "-X", "utf8", "run.py", "--port", str(port),
           "--no-browser", "--no-ocr"]
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=str(SERVER), env=env,
                            stdout=log_fh, stderr=subprocess.STDOUT)
    out: dict = {"command": " ".join(cmd), "port": port, "base": base,
                 "started": False, "runtime": {}, "health": {}, "static": {},
                 "packs_api": {}, "main_path": {}, "csrf": {}, "netstat": [],
                 "server_log_tail": []}
    try:
        out["started"] = wait_health(base)
        out["startup_s"] = round(time.time() - t0, 2)
        if not out["started"]:
            out["error"] = "服务 90 秒内未就绪"
            return out
        if proc.poll() is not None:
            out["started"] = False
            out["error"] = f"服务进程提前退出（exit={proc.returncode}）"
            return out
        # 必须是"本次启动的进程 + 本次端口"写的 runtime.json，否则可能是并发实例
        try:
            rt = json.loads((DATA / "runtime.json").read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            out["started"] = False
            out["runtime"] = {"error": f"{type(exc).__name__}: {exc}"}
            out["error"] = "看不到本次服务的 runtime.json（并发/竞态？）"
            return out
        rt_pid = rt.get("pid")
        rt_port = rt.get("port")
        try:
            same_pid = int(rt_pid) == int(proc.pid)
            same_port = int(rt_port) == int(port)
        except (TypeError, ValueError):
            same_pid = same_port = False
        if not (same_pid and same_port):
            out["started"] = False
            out["runtime"] = {"mismatch": True, "runtime_pid": rt_pid,
                              "runtime_port": rt_port,
                              "expected_pid": proc.pid, "expected_port": port}
            out["error"] = ("runtime.json 不属于本次启动的服务"
                            "（端口被并发实例占用，重试）")
            return out
        out["runtime"] = {"host": rt.get("host"), "port": rt.get("port"),
                          "url": rt.get("url"), "pid": rt.get("pid")}
        try:
            ns = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                                timeout=20)
            txt = ns.stdout.decode("gbk", "replace")
            out["netstat"] = [ln.strip() for ln in txt.splitlines()
                              if f":{port}" in ln and "LISTEN" in ln.upper()]
        except Exception as exc:  # noqa: BLE001
            out["netstat"] = [f"netstat 失败：{exc}"]
        r = http_json(base + "/api/health")
        try:
            body = json.loads(r["body"] or b"{}")
        except Exception:
            body = {}
        out["health"] = {
            "status": r["status"], "ctype": r["ctype"],
            "ok": body.get("ok"), "net": body.get("net"),
            "version": body.get("version"),
            "ocr": (body.get("engine") or {}).get("ocr"),
            "parsers": (body.get("engine") or {}).get("parsers"),
            "detectors": (body.get("engine") or {}).get("detectors"),
            "packs": (body.get("engine") or {}).get("packs"),
            "vault_key_len": len(str((body.get("engine") or {}).get("vault_key") or "")),
            "audit": body.get("audit"),
        }
        rp = http_json(base + "/api/packs")
        try:
            pbody = json.loads(rp["body"] or b"{}")
        except Exception:
            pbody = {}
        out["packs_api"] = {
            "status": rp["status"],
            "packs": pbody.get("packs"),
            "detail_counts": {k: len(v) for k, v in (pbody.get("detail") or {}).items()},
        }
        page_expect = {
            "/": ["text/html"],
            "/console": ["text/html"],
            "/download": ["text/html"],
            "/mcp": ["text/html"],
            "/favicon.svg": ["image/svg+xml"],
            "/favicon.ico": ["image/x-icon", "image/vnd.microsoft.icon"],
            "/apple-touch-icon.png": ["image/png"],
        }
        for path, expects in page_expect.items():
            rr = http_bytes(base + path, timeout=30)
            ctype = rr.get("ctype", "")
            out["static"][path] = {
                "status": rr["status"], "ctype": ctype, "expect_ctype": expects,
                "mime_ok": ctype.split(";")[0].strip() in expects,
                "bytes": len(rr.get("body") or b""),
            }
        rr = http_json(base + "/api/jobs", "POST",
                       {"path": str(SAMPLES / "委托代理合同.pdf")},
                       headers={"Origin": "http://evil.example.com"})
        out["csrf"] = {"status": rr["status"], "blocked": rr["status"] == 403,
                       "ctype": rr["ctype"]}

        # ── 主路径 ──
        sample = SAMPLES / "委托代理合同.pdf"
        raw = sample.read_bytes()
        up = http_bytes(base + "/api/upload", "POST", raw, headers={
            "X-Filename": urllib.parse.quote(sample.name),
            "Content-Type": "application/octet-stream"})
        upj = json.loads(up["body"] or b"{}")
        mp: dict = {"sample": sample.name, "source_bytes": len(raw),
                    "source_sha256": sha256_bytes(raw),
                    "upload": {"status": up["status"],
                               "upload_id": upj.get("upload_id"),
                               "size": upj.get("size"), "sha256": upj.get("sha256")}}
        if up["status"] != 200 or not upj.get("upload_id"):
            mp["ok"] = False
            mp["error"] = "上传失败"
            out["main_path"] = mp
            return out
        rj = http_json(base + "/api/jobs", "POST",
                       {"upload_id": upj["upload_id"], "strictness": "standard"})
        jj = json.loads(rj["body"] or b"{}")
        jid = jj.get("job_id")
        mp["job_create"] = {"status": rj["status"], "job_id": jid,
                            "name": jj.get("name"), "fmt": jj.get("fmt")}
        if not jid:
            mp["ok"] = False
            mp["error"] = "建任务失败"
            out["main_path"] = mp
            return out
        t1 = time.time()
        snap: dict = {}
        stage = ""
        while time.time() - t1 < 180:
            rg = http_json(base + f"/api/jobs/{jid}")
            try:
                snap = json.loads(rg["body"] or b"{}")
            except Exception:
                snap = {}
            stage = snap.get("stage") or ""
            if stage in ("ready", "error"):
                break
            time.sleep(0.4)
        mp["job_snapshot"] = {
            "stage": stage, "progress": snap.get("progress"),
            "pages": snap.get("pages"), "fmt": snap.get("fmt"),
            "pack": snap.get("pack"), "strictness": snap.get("strictness"),
            "counts": snap.get("counts"), "warnings": (snap.get("warnings") or [])[:8],
            "error": snap.get("error"), "poll_s": round(time.time() - t1, 2),
        }
        re_ = http_json(base + f"/api/jobs/{jid}/entities")
        try:
            eb = json.loads(re_["body"] or b"{}")
        except Exception:
            eb = {}
        ents = eb.get("entities") or []
        types: dict[str, int] = {}
        for e in ents:
            label = e.get("label") or e.get("type") or "?"
            types[label] = types.get(label, 0) + 1
        mp["job_entities"] = {"status": re_["status"], "total": len(ents),
                              "types": types, "counts": eb.get("counts"),
                              "entity_ids": [e.get("id") for e in ents[:50]]}
        # 预览 + 渲染（F8）
        rv = http_json(base + f"/api/jobs/{jid}/preview/0?mode=orig", timeout=60)
        try:
            pv = json.loads(rv["body"] or b"{}")
        except Exception:
            pv = {}
        mp["preview"] = {"status": rv["status"], "kind": pv.get("kind"),
                         "pages": pv.get("total_pages"),
                         "blocks": len(pv.get("blocks") or []),
                         "entities": len(pv.get("entities") or [])}
        rr2 = http_bytes(base + f"/api/jobs/{jid}/render/0?mode=orig", timeout=60)
        mp["render"] = {"status": rr2["status"], "ctype": rr2.get("ctype"),
                        "bytes": len(rr2.get("body") or b""),
                        "png_magic": (rr2.get("body") or b"")[:8] ==
                                     b"\x89PNG\r\n\x1a\n"}
        # 双导出 + 下载
        mp["exports"] = {}
        for mode in ("ai", "human"):
            rx = http_json(base + f"/api/jobs/{jid}/export", "POST",
                           {"mode": mode}, timeout=180)
            try:
                xb = json.loads(rx["body"] or b"{}")
            except Exception:
                xb = {}
            rec = {"status": rx["status"], "ok": rx["status"] == 200,
                   "name": xb.get("name"), "bytes": xb.get("bytes"),
                   "sha256": xb.get("sha256"), "mode": xb.get("mode"),
                   "verify": xb.get("verify"),
                   "warnings": (xb.get("warnings") or [])[:8]}
            if rx["status"] == 200:
                rd = http_bytes(base + f"/api/jobs/{jid}/file?mode={mode}", timeout=120)
                body = rd.get("body") or b""
                rec["download"] = {
                    "status": rd["status"], "ctype": rd.get("ctype"),
                    "disposition": rd.get("disposition"),
                    "bytes": len(body), "sha256": sha256_bytes(body),
                    "magic": body[:8].decode("latin-1"),
                    "pdf_magic_ok": body[:5] == b"%PDF-",
                    "nonempty": len(body) > 0,
                    "match_export_sha": bool(rec.get("sha256")) and
                                        sha256_bytes(body) == rec.get("sha256"),
                    "match_export_bytes": rec.get("bytes") == len(body),
                }
                (NET / f"download_{mode}.pdf").write_bytes(body)
            mp["exports"][mode] = rec
        # 审计
        ra = http_json(base + "/api/audit?limit=10")
        try:
            ab = json.loads(ra["body"] or b"{}")
        except Exception:
            ab = {}
        recs = ab.get("records") or []
        mp["audit"] = {
            "status": ra["status"], "records": len(recs),
            "record_keys": sorted(recs[0].keys()) if recs else [],
            "counts_has_by_type": bool(recs and
                                       (recs[0].get("counts") or {}).get("by_type")),
            "value_fields_present": [
                k for k in (recs[0].keys() if recs else [])
                if re.search(r"value|original|plain|content|text", k, re.I)],
            "stats": ab.get("stats"),
        }
        rv2 = http_json(base + "/api/audit/verify", "POST", {})
        try:
            vb = json.loads(rv2["body"] or b"{}")
        except Exception:
            vb = {}
        mp["audit_verify"] = {"status": rv2["status"], **vb}
        # 框选补漏 + 逐条确认（F8）：在导出之后做，不污染主路径
        try:
            rm = http_json(base + f"/api/jobs/{jid}/mark", "POST",
                           {"page": 0, "rect": [0.08, 0.08, 0.18, 0.12],
                            "etype": "OTHER", "propagate": False})
            mb = json.loads(rm["body"] or b"{}")
            eid = (mb.get("entity") or {}).get("id")
            mp["mark"] = {"status": rm["status"], "added": mb.get("added"),
                          "has_entity": bool(eid)}
            if eid:
                ru = http_json(base + f"/api/jobs/{jid}/entities/{eid}", "POST",
                               {"action": "accept"})
                ub = json.loads(ru["body"] or b"{}")
                mp["mark"]["accept_status"] = ru["status"]
                mp["mark"]["status_after"] = ub.get("status")
            rac = http_json(base + f"/api/jobs/{jid}/accept-all", "POST", {})
            try:
                acb = json.loads(rac["body"] or b"{}")
            except Exception:
                acb = {}
            mp["accept_all"] = {"status": rac["status"],
                                "accepted": acb.get("accepted"),
                                "total": acb.get("total")}
            # 整图涂黑（前端按钮的后端契约）：rect=[0,0,1,1] 必须生成 black 实体；
            # 预览接口带 black/boxes，用它做 API 级断言（不依赖浏览器）
            rf = http_json(base + f"/api/jobs/{jid}/mark", "POST",
                           {"page": 0, "rect": [0, 0, 1, 1], "etype": "OTHER",
                            "propagate": False})
            try:
                fb = json.loads(rf["body"] or b"{}")
            except Exception:
                fb = {}
            fid = (fb.get("entity") or {}).get("id")
            mf = {"status": rf["status"], "added": fb.get("added"),
                  "has_entity": bool(fid)}
            rp2 = http_json(base + f"/api/jobs/{jid}/preview/0?mode=orig",
                            timeout=60)
            try:
                pv2 = json.loads(rp2["body"] or b"{}")
            except Exception:
                pv2 = {}
            page_w = float(pv2.get("width") or 0)
            page_h = float(pv2.get("height") or 0)
            found = next((x for x in (pv2.get("entities") or [])
                          if x.get("id") == fid), None)
            boxes = (found or {}).get("boxes") or []
            cover = 0.0
            if boxes and page_w > 0 and page_h > 0:
                x0 = min(float(b[0]) for b in boxes)
                y0 = min(float(b[1]) for b in boxes)
                x1 = max(float(b[2]) for b in boxes)
                y1 = max(float(b[3]) for b in boxes)
                cover = round(((x1 - x0) * (y1 - y0)) / (page_w * page_h), 3)
            mf.update({"preview_status": rp2["status"],
                       "black": bool((found or {}).get("black")),
                       "coverage": cover,
                       "page": [page_w, page_h], "boxes": boxes[:4]})
            mp["mark_full"] = mf
            # 身份证档位 API 契约（默认 fields / 非法值回退 / whole）
            try:
                mp["id_card_mode_api"] = _capture_id_card_modes(base, sample)
            except Exception as exc:  # noqa: BLE001
                mp["id_card_mode_api"] = {"error": f"{type(exc).__name__}: {exc}"}
            # P1-A API：时间模糊 / 未成年人严格模式 / 组合风险
            try:
                mp["p1a_api"] = capture_p1a_api(base)
            except Exception as exc:  # noqa: BLE001
                mp["p1a_api"] = {"error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:  # noqa: BLE001
            mp["mark"] = {"error": f"{type(exc).__name__}: {exc}"}
        exp = mp["exports"]
        mp["ok"] = bool(
            stage == "ready"
            and exp.get("ai", {}).get("ok") and exp.get("human", {}).get("ok")
            and exp["ai"].get("verify", {}).get("residual") == 0
            and exp["human"].get("verify", {}).get("residual") == 0
            and exp["ai"].get("download", {}).get("pdf_magic_ok")
            and exp["human"].get("download", {}).get("pdf_magic_ok")
            and exp["ai"].get("download", {}).get("nonempty")
            and exp["human"].get("download", {}).get("nonempty")
        )
        out["main_path"] = mp
        return out
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        time.sleep(0.2)
        try:
            log_fh.close()
        except Exception:
            pass
        try:
            out["server_log_tail"] = log_path.read_text(
                "utf-8", "replace").strip().splitlines()[-25:]
        except Exception:
            pass
        try:
            (DATA / "runtime.json").unlink(missing_ok=True)
        except Exception:
            pass


def check_rehydrate(job_id: str) -> dict:
    """F5：AI 导出后映射库应可解密、并能把假值还原回原值。"""
    ensure_ready()
    try:
        from yinzhan import vault
        payload = vault.load_mapping(job_id)
        if not payload:
            return {"ok": False, "err": "映射库没有该任务记录（vault.load_mapping 返回空）"}
        mapping = payload.get("map") or {}
        pairs = [(orig, fake) for _etype, table in mapping.items()
                 for orig, fake in (table or {}).items() if fake]
        if not pairs:
            return {"ok": False, "err": "映射为空"}
        orig, fake = pairs[0]
        restored = vault.rehydrate(f"前缀{fake}后缀", payload)
        return {"ok": orig in restored and fake not in restored,
                "entities": sum(len(t or {}) for t in mapping.values()),
                "types": sorted(mapping.keys()),
                "sample_roundtrip": {"restored_ok": orig in restored}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


def rehydrate_cli_test(job_id: str, timeout: int = 120) -> dict:
    """F5：命令行还原链路（rehydrate.py --job … src dst）端到端。"""
    ensure_ready()
    try:
        from yinzhan import vault
        payload = vault.load_mapping(job_id)
        if not payload:
            return {"ok": False, "err": "无映射，无法测 CLI"}
        mapping = payload.get("map") or {}
        pairs = [(orig, fake) for _table in mapping.values()
                 for orig, fake in (_table or {}).items() if fake]
        if not pairs:
            return {"ok": False, "err": "映射为空，无法测 CLI"}
        orig, fake = pairs[0]
        d = WORK / "rehydrate_cli"
        d.mkdir(parents=True, exist_ok=True)
        src = d / "answer.txt"
        dst = d / "answer_restored.txt"
        src.write_text(f"模型回答里出现：{fake}\n第二行：{fake}\n", "utf-8")
        env = dict(os.environ)
        env.update({"YINZHAN_DATA_DIR": str(DATA), "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1"})
        p = subprocess.run([PY, "-X", "utf8", "rehydrate.py", "--job", job_id,
                            str(src), str(dst)],
                           cwd=str(SERVER), capture_output=True, timeout=timeout,
                           env=env)
        if p.returncode != 0:
            return {"ok": False,
                    "err": f"exit={p.returncode} "
                           f"{p.stderr.decode('utf-8', 'replace')[-160:]}"}
        if not dst.is_file():
            return {"ok": False, "err": "还原文件未生成"}
        text = dst.read_text("utf-8", "replace")
        return {"ok": orig in text and fake not in text,
                "changed_lines": sum(1 for a, b in zip(src.read_text("utf-8").splitlines(),
                                                       text.splitlines()) if a != b),
                "detail": "CLI 还原成功（假值→原值）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


def mcp_stdio_test(timeout: int = 180) -> dict:
    """F9：MCP stdio 协议端到端：initialize / tools/list / 4 个 tools/call。"""
    ensure_ready()
    sample = SAMPLES / "委托代理合同.pdf"
    calls = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "list_rules", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "redact_text",
                    "arguments": {"text": "手机：13817264451，身份证：310104198703124510"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "check_support", "arguments": {"path": str(sample)}}},
    ]
    payload = "\n".join(json.dumps(c, ensure_ascii=False) for c in calls) + "\n"
    env = dict(os.environ)
    env.update({"YINZHAN_DATA_DIR": str(DATA), "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1"})
    cwd = WORK / "mcp_stdio"
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run([PY, "-X", "utf8", str(SERVER / "mcp_server.py")],
                           input=payload.encode("utf-8"), capture_output=True,
                           timeout=timeout, env=env, cwd=str(cwd))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}
    stdout = p.stdout.decode("utf-8", "replace")
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    # stdout 必须只有 JSON-RPC（日志只能走 stderr）
    parsed, bad_lines = [], []
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except Exception:
            bad_lines.append(ln[:80])
    by_id = {r.get("id"): r for r in parsed if isinstance(r, dict)}
    problems = []
    if p.returncode != 0:
        problems.append(f"exit={p.returncode}")
    if bad_lines:
        problems.append(f"stdout 非 JSON：{bad_lines[:2]}")
    if len(parsed) < 5:
        problems.append(f"响应数 {len(parsed)}<5")
    init = by_id.get(1) or {}
    if not (init.get("result") or {}).get("serverInfo"):
        problems.append("initialize 无 serverInfo")
    tools = ((by_id.get(2) or {}).get("result") or {}).get("tools") or []
    names = {t.get("name") for t in tools}
    if not {"sanitize_document", "redact_text", "check_support", "list_rules"} <= names:
        problems.append(f"tools/list 缺工具：{sorted(names)}")

    def text_payload(rid: int) -> dict:
        res = (by_id.get(rid) or {}).get("result") or {}
        try:
            return json.loads(res["content"][0]["text"])
        except Exception:
            return {}

    rules = text_payload(3)
    if not rules.get("packs"):
        problems.append("list_rules 无 packs")
    red = text_payload(4)
    redacted = red.get("redacted") or ""
    if not redacted or "13817264451" in redacted or "310104198703124510" in redacted:
        problems.append("redact_text 未脱敏/泄露原值")
    support = text_payload(5)
    if support.get("supported") is not True:
        problems.append("check_support 未识别 pdf")
    return {"ok": not problems,
            "detail": "；".join(problems) if problems
            else "initialize/tools/list/3×tools-call 均正常，stdout 全 JSON-RPC",
            "responses": len(parsed)}


# ════════════════════════ P0 二波新能力夹具（PDF 隐藏数据 / EXIF 朝向 / 表格数值） ════════════════════════
NEW_FIXTURE_DIRNAME = "p0_wave2"


def _new_fixture_dir() -> Path:
    ensure_ready()
    d = WORK / NEW_FIXTURE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _raw_pdf_blob(path: Path) -> bytes:
    """成品所有 xref 对象 + 解压流的拼接，用于隐藏数据 marker 残留扫描。"""
    import fitz
    chunks: list[bytes] = []
    with fitz.open(str(path)) as pdf:
        for xref in range(1, pdf.xref_length()):
            try:
                chunks.append((pdf.xref_object(xref, compressed=False) or "")
                              .encode("utf-8", "replace"))
            except Exception:
                pass
            try:
                stream = pdf.xref_stream(xref)
            except Exception:
                stream = None
            if stream:
                chunks.append(stream)
    return b"\n".join(chunks)


def pdf_hidden_fixture_test() -> dict:
    """PDF 隐藏数据（附件/批注/表单/JS）：默认剥离 + verify 拦截未清理。"""
    ensure_ready()
    try:
        import fitz
        from yinzhan import pipeline, registry
        from yinzhan.jobs import _load_rules
        from yinzhan.ocr import get_ocr
        from yinzhan.udm import ParseContext, VerifyError

        registry.load_plugins()
        d = _new_fixture_dir()
        src = d / "hidden.pdf"
        body_phone = "13800138000"
        att_phone, annot_phone, widget_phone = ("13900139000", "13700137000",
                                                "13500135000")
        marks = {"attach": "QA-ATTACH-MARK-91f", "annot": "QA-ANNOT-MARK-72c",
                 "widget": "QA-WIDGET-MARK-53b", "js": "QA-JS-MARK-34a"}
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 90), "委托方：张三", fontsize=12)
        page.insert_text((72, 112), f"手机号 {body_phone}，日期 2026-10-20",
                         fontsize=12)
        doc.embfile_add("secret.txt",
                        f"{marks['attach']} {att_phone}".encode("utf-8"),
                        filename="secret.txt")
        note = page.add_text_annot((100, 200),
                                   f"{marks['annot']} 批注 {annot_phone}")
        note.update()
        widget = fitz.Widget()
        widget.field_name = "phone"
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_value = f"{marks['widget']} {widget_phone}"
        widget.rect = fitz.Rect(100, 400, 360, 428)
        widget.text_fontsize = 10
        page.add_widget(widget)
        js_xref = doc.get_new_xref()
        doc.update_object(js_xref, f"<< /S /JavaScript /JS ({marks['js']}) >>")
        doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js_xref} 0 R")
        doc.save(str(src), garbage=4, deflate=True)
        doc.close()

        parser = registry.find_parser(src)
        if parser is None:
            return {"ok": False, "err": "没有 PDF 解析器"}
        ctx = ParseContext(ocr=get_ocr(), workdir=str(d / "work"))
        parsed = parser.parse(src, ctx)
        meta = parsed.meta or {}
        stats = {k: meta.get(k) for k in ("embedded_files", "pdf_annotations",
                                          "pdf_forms", "pdf_js")}
        parse_ok = (int(stats.get("embedded_files") or 0) >= 1
                    and int(stats.get("pdf_annotations") or 0) >= 1
                    and int(stats.get("pdf_forms") or 0) >= 1
                    and bool(stats.get("pdf_js")))
        rules, _pack, _idx = _load_rules("pdf")
        det = pipeline.detect(parsed, rules, "standard")
        pipeline.decide(det.entities, det.rules, pipeline.Session())
        out = d / "hidden_ai.pdf"
        pipeline.export(parsed, parser, det.entities, out, {"mode": "ai"})
        ver = pipeline.verify(out, det.entities, parser, ctx)
        with fitz.open(str(out)) as pdf:
            out_stats = {
                "embedded_files": int(pdf.embfile_count()),
                "embedded_file_names": list(pdf.embfile_names() or []),
                "pdf_annotations": sum(len(list(pg.annots() or [])) for pg in pdf),
                "pdf_forms": sum(len(list(pg.widgets() or [])) for pg in pdf),
            }
        blob = _raw_pdf_blob(out).decode("utf-8", "replace")
        markers_left = [m for m in marks.values() if m in blob]
        pii_left = [v for v in (att_phone, annot_phone, widget_phone)
                    if v in blob]
        strip_ok = (out_stats["embedded_files"] == 0
                    and out_stats["embedded_file_names"] == []
                    and out_stats["pdf_annotations"] == 0
                    and out_stats["pdf_forms"] == 0
                    and not markers_left and not pii_left)
        # keep_pdf_hidden：默认导出选项下 verify 必须拦截（同一阻断机制）；
        # 另用"无 PII 的隐藏内容"验证显式 keep 放行（keep 不能绕过文本残留复检，
        # 敏感隐藏内容即使 keep 也仍会被 residual 拦住 —— 见 test_pdf_hidden）。
        kept = d / "hidden_keep.pdf"
        pipeline.export(parsed, parser, det.entities, kept,
                        {"mode": "ai", "keep_pdf_hidden": True})
        intercepted, intercept_msg = False, ""
        try:
            pipeline.verify(kept, det.entities, parser, ctx)
        except VerifyError as ve:
            intercept_msg = str(ve)
            intercepted = "隐藏数据" in intercept_msg
        # 非敏感隐藏内容：只 marker 无 PII → keep 导出 + 显式放行应通过
        plain = d / "hidden_plain.pdf"
        pdoc = fitz.open()
        ppage = pdoc.new_page(width=400, height=300)
        ppage.insert_text((60, 90), "内部资料，无个人信息", fontsize=12)
        pdoc.embfile_add("notes.txt", b"QA-PLAIN-ATTACH-MARK-11a",
                         filename="notes.txt")
        pannot = ppage.add_text_annot((80, 150), "QA-PLAIN-ANNOT-MARK-22b 请核对")
        pannot.update()
        pdoc.save(str(plain), garbage=4, deflate=True)
        pdoc.close()
        keep_passes = False
        if parser is not None:
            pparsed = parser.parse(plain, ctx)
            prules, _p2, _i2 = _load_rules("pdf")
            pdet = pipeline.detect(pparsed, prules, "standard")
            pipeline.decide(pdet.entities, pdet.rules, pipeline.Session())
            pout = d / "hidden_plain_keep.pdf"
            pipeline.export(pparsed, parser, pdet.entities, pout,
                            {"mode": "ai", "keep_pdf_hidden": True})
            try:
                pv = pipeline.verify(pout, pdet.entities, parser, ctx,
                                     options={"keep_pdf_hidden": True})
                keep_passes = pv.get("verified") is True
            except Exception:
                keep_passes = False
        ok = bool(parse_ok and strip_ok and ver.get("residual") == 0
                  and intercepted and keep_passes)
        return {"ok": ok,
                "detail": {"parse_stats": stats, "parse_ok": parse_ok,
                           "strip_ok": strip_ok, "out_stats": out_stats,
                           "markers_left": markers_left, "pii_left": pii_left,
                           "residual": ver.get("residual"),
                           "verify_intercepted": intercepted,
                           "intercept_msg": intercept_msg[:120],
                           "keep_option_passes": keep_passes}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


def exif_orientation_fixture_test() -> dict:
    """图片 EXIF Orientation=6：解析/渲染/导出均转正，且导出无 EXIF。"""
    ensure_ready()
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw
        from yinzhan import pipeline, registry
        from yinzhan.ocr import get_ocr
        from yinzhan.udm import Decision, Entity, ParseContext, Span

        registry.load_plugins()
        d = _new_fixture_dir()
        src = d / "orientation6.jpg"
        img = Image.new("RGB", (400, 200), (245, 245, 245))
        ImageDraw.Draw(img).text((30, 80), "QA ORIENT", fill=(20, 20, 20))
        exif = Image.Exif()
        exif[274] = 6          # 顺时针 90°：存储 400x200 → 展示 200x400
        img.save(str(src), exif=exif.tobytes(), quality=92)
        parser = registry.find_parser(src)
        if parser is None:
            return {"ok": False, "err": "没有图片解析器"}
        ctx = ParseContext(ocr=get_ocr(), workdir=str(d / "work"))
        doc = parser.parse(src, ctx)
        page = doc.pages[0]
        parsed_dims = (round(page.width), round(page.height))
        dims_ok = abs(page.width - 200) <= 2 and abs(page.height - 400) <= 2
        png = parser.render(doc, 0, "orig") or b""
        render_dims = None
        if png:
            with Image.open(BytesIO(png)) as im:
                render_dims = im.size
        render_ok = render_dims == (200, 400)
        ent = Entity(id=Entity.new_id(), etype="OTHER", label="测试遮盖", value="",
                     spans=[Span(rect=(0.1, 0.1, 0.6, 0.6), page=0)],
                     level="L2", confidence=1.0, status="accepted",
                     decision=Decision(strategy="black", black=True))
        out = d / "orientation6_out.jpg"
        pipeline.export(doc, parser, [ent], out, {"mode": "ai"})
        om = image_meta(out)
        with Image.open(out) as im:
            export_dims = im.size
        exif_clean = not om["info_keys"] and om["exif_entries"] == 0
        export_dims_ok = export_dims == (200, 400)
        ok = bool(dims_ok and render_ok and exif_clean and export_dims_ok)
        return {"ok": ok,
                "detail": {"parsed_dims": parsed_dims, "dims_ok": dims_ok,
                           "render_dims": render_dims, "render_ok": render_ok,
                           "export_dims": export_dims,
                           "export_dims_ok": export_dims_ok,
                           "exif_clean": exif_clean,
                           "export_meta": om}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


def table_numeric_fixture_test() -> dict:
    """表格：表头列级覆盖 + 纯数值金额导出后单元格类型为数字。"""
    ensure_ready()
    try:
        from openpyxl import Workbook, load_workbook
        from yinzhan import pipeline, registry
        from yinzhan.detectors import numeric_det
        from yinzhan.jobs import _load_rules
        from yinzhan.ocr import get_ocr
        from yinzhan.udm import ParseContext

        registry.load_plugins()
        d = _new_fixture_dir()
        src = d / "table_amounts.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "工资表"
        ws.append(["姓名", "身份证号", "手机号", "金额"])
        ws.append(["张三", "11010519491231002X", "13800138000", 1234.56])
        ws.append(["李四", "310104198703124510", "13900139000", 2345.67])
        ws.append(["合计", "", "", 3580.23])
        wb.save(str(src))
        wb.close()

        parser = registry.find_parser(src)
        ctx = ParseContext(ocr=get_ocr(), workdir=str(d / "work"))
        doc = parser.parse(src, ctx)
        rules, _p, _i = _load_rules("xlsx")
        det = pipeline.detect(doc, rules, "standard")
        pipeline.decide(det.entities, det.rules, pipeline.Session())
        table_ents = [e for e in det.entities if e.origin == "table"]
        numeric_ents = [e for e in det.entities if e.origin == "numeric_cell"]
        numeric_amounts = [e for e in numeric_ents if e.etype == "AMOUNT"]
        # 表头列级覆盖：按"单元格是否被实体 span 覆盖"断言（同值合并后
        # table 实体可能与 regex 实体合并，不能只看 origin/label）。
        cell_block: dict[str, str] = {}
        for page in doc.pages:
            for b in page.blocks:
                ref = getattr(b, "ref", None) or {}
                cell = ref.get("cell")
                if cell and getattr(b, "text", ""):
                    cell_block[str(cell)] = b.id
        covered: dict[str, list[dict]] = {}
        for e in det.entities:
            for s in e.spans:
                if s.block_id:
                    covered.setdefault(s.block_id, []).append(
                        {"etype": e.etype, "origin": e.origin,
                         "start": s.start, "end": s.end})
        # 姓名/证件/手机 三列各 2 个数据单元格必须被覆盖，且类型正确
        expect_cells = {
            "A2": {"PERSON"}, "A3": {"PERSON"},
            "B2": {"ID_CARD"}, "B3": {"ID_CARD"},
            "C2": {"PHONE"}, "C3": {"PHONE"},
        }
        missing_cells, wrong_types = [], []
        for cell, etypes in expect_cells.items():
            bid = cell_block.get(cell)
            hit = covered.get(bid or "", [])
            if not hit:
                missing_cells.append(cell)
                continue
            if not any(h["etype"] in etypes for h in hit):
                wrong_types.append(f"{cell}:{[h['etype'] for h in hit]}")
        table_provenance = len(table_ents)
        table_cover_ok = (not missing_cells and not wrong_types
                          and table_provenance >= 1)
        out = d / "table_amounts_ai.xlsx"
        pipeline.export(doc, parser, det.entities, out, {"mode": "ai"})
        ver = pipeline.verify(out, det.entities, parser, ctx)
        wb2 = load_workbook(str(out))
        ws2 = wb2.active
        writes = []
        for e in numeric_amounts:
            meta = numeric_det.get_numeric_meta(e.id) or {}
            coord = meta.get("cell") or meta.get("coordinate")
            sheet = meta.get("sheet") or ws2.title
            repl = e.decision.replacement if e.decision else None
            if not coord or repl is None:
                continue
            try:
                cell = wb2[sheet][coord]
                expected = float(str(repl).replace(",", "").replace("，", "").strip())
            except Exception:
                continue
            v = cell.value
            writes.append({
                "cell": coord, "type": type(v).__name__,
                "value_ok": (isinstance(v, (int, float))
                             and not isinstance(v, bool)
                             and abs(float(v) - expected) < 1e-6),
                "format_kept": (not meta.get("number_format")
                                or cell.number_format == meta.get("number_format")),
            })
        wb2.close()
        numeric_ok = (len(writes) >= 2
                      and all(w["value_ok"] and w["format_kept"] for w in writes))
        ok = bool(table_cover_ok and numeric_ok and ver.get("residual") == 0)
        return {"ok": ok,
                "detail": {"table_entities": len(table_ents),
                           "table_provenance": table_provenance,
                           "missing_cells": missing_cells,
                           "wrong_types": wrong_types,
                           "numeric_entities": len(numeric_ents),
                           "numeric_amounts": len(numeric_amounts),
                           "writes": writes, "residual": ver.get("residual"),
                           "table_cover_ok": table_cover_ok,
                           "numeric_ok": numeric_ok}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


# ════════════════════════ 身份证 A 方案（fields / whole）夹具与真实链路 ════════════════════════
IDCARD_PAGE = (1160, 760)
IDCARD_CARD = (30, 25, 1130, 725)
_IDCARD_FONT_PATHS = (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                      r"C:\Windows\Fonts\simsun.ttc",
                      "/System/Library/Fonts/PingFang.ttc",
                      "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


class _FakeCascade:
    """只实现 detectMultiScale 的假级联（引擎 test hook 用，按调用顺序返回预设框）。"""

    def __init__(self, dets_per_call):
        self.dets = list(dets_per_call)
        self.calls = 0

    def detectMultiScale(self, gray, **kwargs):
        self.calls += 1
        if not self.dets:
            return []
        d = self.dets[-1] if self.calls >= len(self.dets) \
            else self.dets[self.calls - 1]
        return [list(d)] if d else []


def _idc_font(size: int):
    from PIL import ImageFont
    for path in _IDCARD_FONT_PATHS:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _idc_rel(card, nx: float, ny: float) -> tuple[float, float]:
    cw = card[2] - card[0]
    ch = card[3] - card[1]
    return (card[0] + nx * cw, card[1] + ny * ch)


def _idc_face_det() -> tuple[int, int, int, int]:
    """注入级联框（scan 坐标）：落在期望证件照区中心，供确定性人脸命中。"""
    w, h = IDCARD_PAGE
    card = IDCARD_CARD
    cw, ch = card[2] - card[0], card[3] - card[1]
    scale = 640.0 / max(w, h)
    px = card[0] + 0.75 * cw
    py = card[1] + 0.45 * ch
    fw, fh = 150.0 / scale, 180.0 / scale
    return (int(round((px - fw / 2) * scale)),
            int(round((py - fh / 2) * scale)),
            int(round(fw * scale)), int(round(fh * scale)))


def _build_id_front(path: Path, photo: bool = True) -> dict:
    """合成身份证正面（虚构信息）：模板块位置与 #85 校准一致。"""
    from PIL import Image, ImageDraw
    w, h = IDCARD_PAGE
    card = IDCARD_CARD
    cw, ch = card[2] - card[0], card[3] - card[1]
    img = Image.new("RGB", (w, h), (120, 130, 125))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(card, radius=28, fill=(250, 250, 248),
                        outline=(90, 90, 90), width=4)
    photo_rect = None
    if photo:
        photo_rect = (*_idc_rel(card, 0.55, 0.15), *_idc_rel(card, 0.96, 0.78))
        d.rectangle([round(v) for v in photo_rect], fill=(205, 205, 205))
        cx = (photo_rect[0] + photo_rect[2]) / 2
        cy = (photo_rect[1] + photo_rect[3]) / 2
        d.ellipse([cx - 90, cy - 140, cx + 90, cy + 40], fill=(235, 220, 205))
        d.ellipse([cx - 26, cy - 80, cx - 6, cy - 58], fill=(60, 50, 45))
        d.ellipse([cx + 6, cy - 80, cx + 26, cy - 58], fill=(60, 50, 45))
        d.arc([cx - 40, cy - 55, cx + 40, cy + 5], 20, 160, fill=(150, 90, 90), width=5)
    lines = (
        ("姓名 张三", 0.08, 0.135, 40, "id_name"),
        ("性别 男", 0.08, 0.305, 32, "retained_gender"),
        ("民族 汉", 0.34, 0.305, 32, "retained_gender"),
        ("出生 1990年1月1日", 0.08, 0.405, 30, "retained_birth"),
        ("住址 上海市浦东新区测试路1号2栋3单元401室", 0.08, 0.545, 28, "id_address"),
        ("公民身份号码 310101199001011234", 0.08, 0.805, 30, "id_number"),
    )
    layout: dict[str, list] = {}
    for text, nx, ny, size, group in lines:
        pos = (round(card[0] + nx * cw), round(card[1] + ny * ch))
        font = _idc_font(size)
        d.text(pos, text, font=font, fill=(25, 25, 25))
        bbox = tuple(float(v) for v in d.textbbox(pos, text, font=font))
        layout.setdefault(group, []).append(bbox)
    img.save(path)
    return {"path": path, "photo_rect": photo_rect,
            "protected": layout.get("id_name", []) + layout.get("id_address", [])
                         + layout.get("id_number", [])
                         + ([photo_rect] if photo_rect else []),
            "retained": layout.get("retained_gender", [])
                        + layout.get("retained_birth", []),
            "fake_det": _idc_face_det()}


def _build_id_noface(path: Path) -> dict:
    return _build_id_front(path, photo=False)


def _build_id_back(path: Path, rows: bool = True) -> dict:
    """合成身份证反面（虚构信息）：国徽占位 + 标题 + 签发机关/有效期限两行。

    ``rows=False`` 时只画国徽/标题，用于验证「两行读不出 → 整卡 fallback」。
    """
    from PIL import Image, ImageDraw
    w, h = IDCARD_PAGE
    card = IDCARD_CARD
    cw, ch = card[2] - card[0], card[3] - card[1]
    img = Image.new("RGB", (w, h), (120, 130, 125))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(card, radius=28, fill=(250, 250, 248),
                        outline=(90, 90, 90), width=4)
    retained: list[tuple] = []
    # 国徽占位（暗红；不是黑色，用于断言"保留"）
    ex = card[0] + 0.50 * cw
    ey = card[1] + 0.085 * ch
    r = 0.040 * cw
    d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=(150, 25, 25))
    retained.append((ex - r, ey - r, ex + r, ey + r))
    # 标题
    tpos = _idc_rel(card, 0.30, 0.145)
    tfont = _idc_font(38)
    d.text(tpos, "中华人民共和国居民身份证", font=tfont, fill=(25, 25, 25))
    retained.append(tuple(float(v) for v in d.textbbox(tpos, "中华人民共和国居民身份证",
                                                      font=tfont)))
    protected: list[tuple] = []
    if rows:
        apos = _idc_rel(card, 0.17, 0.695)
        afont = _idc_font(28)
        atext = "签发机关 上海市公安局浦东分局"
        d.text(apos, atext, font=afont, fill=(25, 25, 25))
        protected.append(tuple(float(v) for v in d.textbbox(apos, atext, font=afont)))
        vpos = _idc_rel(card, 0.17, 0.855)
        vfont = _idc_font(26)
        vtext = "有效期限 2020.01.01-2040.01.01"
        d.text(vpos, vtext, font=vfont, fill=(25, 25, 25))
        protected.append(tuple(float(v) for v in d.textbbox(vpos, vtext, font=vfont)))
    img.save(path)
    return {"path": path, "protected": protected, "retained": retained}


def _overlap_frac(inner, outer) -> float:
    """inner 矩形被 outer 覆盖的面积占比（0~1）。"""
    x0 = max(inner[0], outer[0])
    y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2])
    y1 = min(inner[3], outer[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = max(1e-6, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return inter / area


def _black_ratio(img, rect, inset: float = 0.12) -> float:
    """矩形区域内黑色像素占比（用于 human 导出后的遮/未遮断言）。"""
    x0, y0, x1, y1 = rect
    dx = (x1 - x0) * inset
    dy = (y1 - y0) * inset
    box = (round(x0 + dx), round(y0 + dy), round(x1 - dx), round(y1 - dy))
    if box[2] <= box[0] or box[3] <= box[1]:
        return 0.0
    crop = img.crop(box)
    pixels = list(crop.getdata())
    if not pixels:
        return 0.0
    black = sum(1 for p in pixels
                if p[0] < 45 and p[1] < 45 and p[2] < 45)
    return round(black / len(pixels), 3)


def id_card_chain_test() -> dict:
    """身份证 A 方案真链路：jobs(id_card_mode) → 实体档位 → human 导出 → verify。

    合成虚构身份证正面/反面夹具；通过引擎 test hook ``_FACE_CASCADES_OVERRIDE``
    注入确定性人脸命中（仓库无真人证件照片，不可用真实 Haar 正样本）。
    """
    ensure_ready()
    try:
        import time as _time
        from PIL import Image
        from yinzhan import registry, visual_targets
        from yinzhan.jobs import MANAGER

        registry.load_plugins()
        d = WORK / "fixtures_idcard"
        d.mkdir(parents=True, exist_ok=True)
        front = _build_id_front(d / "idfront.png")
        back = _build_id_back(d / "idback.png")
        back_nofields = _build_id_back(d / "idback_nofields.png", rows=False)
        noface = _build_id_noface(d / "idfront_noface.png")

        def _override(dets):
            visual_targets._FACE_CASCADES_OVERRIDE = (
                [("frontal", _FakeCascade(dets), 0.92)] if dets is not None else None)

        def _run(path, mode: str, dets):
            _override(dets)
            job = MANAGER.create(Path(path), id_card_mode=mode)
            t0 = _time.time()
            while _time.time() - t0 < 120:
                if getattr(job, "stage", "") in ("ready", "error"):
                    break
                _time.sleep(0.1)
            return job

        def _ent_rect(e):
            for s in (e.spans or []):
                if s.rect:
                    return tuple(float(v) for v in s.rect)
            return None

        def _export_human(job):
            # 导出/verify 阶段：字段已被涂黑，用"无脸"注入让重解析走整卡回退，
            # 避免假级联把黑块又"检测"成字段（真实产品黑块不会命中 Haar）。
            _override([[]])
            return MANAGER.export(job.id, "human")

        def _types(ents):
            out: dict[str, int] = {}
            for e in ents:
                out[e.etype] = out.get(e.etype, 0) + 1
            return out

        old = visual_targets._FACE_CASCADES_OVERRIDE
        res: dict = {}
        try:
            # 1) fields 档正面
            job = _run(front["path"], "fields", [front["fake_det"]])
            ents = list(job.entities)
            rects = {e.etype: _ent_rect(e) for e in ents
                     if _ent_rect(e) is not None}
            f = {"stage": job.stage, "types": _types(ents),
                 "warnings": list(job.warnings)[:6], "has_doc": "ID_DOCUMENT" in rects,
                 "field_kinds": [k for k in ("ID_PHOTO", "ID_NAME", "ID_NUMBER",
                                             "ID_ADDRESS") if k in rects]}
            try:
                rec = _export_human(job)
                out = Path(rec["out"])
                img = Image.open(out).convert("RGB")
                ratios = {kind: _black_ratio(img, rects[kind]) for kind in f["field_kinds"]}
                f["protected_ratios"] = ratios
                f["retained_ratios"] = [_black_ratio(img, r) for r in front["retained"]]
                field_rects = [r for k, r in rects.items() if k.startswith("ID_")]
                f["retained_max_overlap"] = [
                    round(max((_overlap_frac(r, pr) for pr in field_rects),
                              default=0.0), 3)
                    for r in front["retained"]]
                f["verify"] = dict(rec.get("verify") or {})
                f["export_ok"] = out.is_file() and out.stat().st_size > 0
                f["out_name"] = out.name
            except Exception as exc:  # noqa: BLE001
                f["export_error"] = f"{type(exc).__name__}: {exc}"
            res["fields_front"] = f

            # 2) whole 档正面（同一夹具）
            job = _run(front["path"], "whole", [front["fake_det"]])
            ents = list(job.entities)
            rects = {e.etype: _ent_rect(e) for e in ents if _ent_rect(e) is not None}
            w = {"stage": job.stage, "types": _types(ents),
                 "warnings": list(job.warnings)[:6],
                 "has_doc": "ID_DOCUMENT" in rects}
            try:
                rec = _export_human(job)
                out = Path(rec["out"])
                img = Image.open(out).convert("RGB")
                thumb = img.resize((160, max(1, int(160 * img.height / img.width))))
                w["full_black_ratio"] = _black_ratio(thumb, (0, 0, *thumb.size), inset=0.02)
                w["verify"] = dict(rec.get("verify") or {})
                w["export_ok"] = out.is_file() and out.stat().st_size > 0
            except Exception as exc:  # noqa: BLE001
                w["export_error"] = f"{type(exc).__name__}: {exc}"
            res["whole_front"] = w

            # 3) 反面：fields 遮签发机关/有效期限（国徽/标题保留）；whole 整卡；
            #    「两行读不出」→ 整卡 fallback + 提示
            job = _run(back["path"], "fields", None)
            ents = list(job.entities)
            rects = {e.etype: _ent_rect(e) for e in ents if _ent_rect(e) is not None}
            bf = {"stage": job.stage, "types": _types(ents),
                  "warnings": list(job.warnings)[:6],
                  "has_doc": "ID_DOCUMENT" in rects}
            try:
                rec = _export_human(job)
                out = Path(rec["out"])
                img = Image.open(out).convert("RGB")
                bf["protected_ratios"] = {
                    etype: _black_ratio(img, rects[etype])
                    for etype in ("ID_AUTHORITY", "ID_VALIDITY") if etype in rects}
                bf["retained_ratios"] = [_black_ratio(img, r) for r in back["retained"]]
                top_rects = [rects[t] for t in ("ID_AUTHORITY", "ID_VALIDITY")
                             if t in rects]
                bf["retained_max_overlap"] = [
                    round(max((_overlap_frac(r, pr) for pr in top_rects),
                              default=0.0), 3)
                    for r in back["retained"]]
                bf["verify"] = dict(rec.get("verify") or {})
                bf["export_ok"] = out.is_file() and out.stat().st_size > 0
            except Exception as exc:  # noqa: BLE001
                bf["export_error"] = f"{type(exc).__name__}: {exc}"
            res["back_fields"] = bf
            job = _run(back["path"], "whole", None)
            ents = list(job.entities)
            rects = {e.etype: _ent_rect(e) for e in ents if _ent_rect(e) is not None}
            bw = {"stage": job.stage, "types": _types(ents),
                  "has_doc": "ID_DOCUMENT" in rects}
            try:
                rec = _export_human(job)
                out = Path(rec["out"])
                img = Image.open(out).convert("RGB")
                thumb = img.resize((160, max(1, int(160 * img.height / img.width))))
                bw["full_black_ratio"] = _black_ratio(thumb, (0, 0, *thumb.size),
                                                      inset=0.02)
                bw["verify"] = dict(rec.get("verify") or {})
            except Exception as exc:  # noqa: BLE001
                bw["export_error"] = f"{type(exc).__name__}: {exc}"
            res["back_whole"] = bw

            # 3b) 反面「两行读不出」→ fields 回退整卡 + 提示
            job = _run(back_nofields["path"], "fields", None)
            ents = list(job.entities)
            bnf = {"stage": job.stage, "types": _types(ents),
                   "warnings": list(job.warnings)[:6],
                   "has_doc": any(e.etype == "ID_DOCUMENT" for e in ents),
                   "id_types": [e.etype for e in ents
                                if e.etype.startswith("ID_")
                                and e.etype != "ID_DOCUMENT"]}
            res["back_nofields_fields"] = bnf

            # 4) 无脸/无法定向的正面 → fields 回退整卡 + 提示
            job = _run(noface["path"], "fields", [])
            ents = list(job.entities)
            res["noface_fields"] = {
                "stage": job.stage, "types": _types(ents),
                "has_doc": any(e.etype == "ID_DOCUMENT" for e in ents),
                "warnings": list(job.warnings)[:6],
            }
        finally:
            visual_targets._FACE_CASCADES_OVERRIDE = old

        ff = res.get("fields_front") or {}
        wf = res.get("whole_front") or {}
        bf = res.get("back_fields") or {}
        bw = res.get("back_whole") or {}
        bnf = res.get("back_nofields_fields") or {}
        nf = res.get("noface_fields") or {}
        protected = ff.get("protected_ratios") or {}
        retained = ff.get("retained_ratios") or []
        retained_ov = ff.get("retained_max_overlap") or []
        bf_prot = bf.get("protected_ratios") or {}
        bf_ret = bf.get("retained_ratios") or []
        bf_ov = bf.get("retained_max_overlap") or []
        ok = bool(
            ff.get("stage") == "ready"
            and set(ff.get("field_kinds") or []) == {"ID_PHOTO", "ID_NAME",
                                                     "ID_NUMBER", "ID_ADDRESS"}
            and not ff.get("has_doc")
            and protected and all(v >= 0.6 for v in protected.values())
            and retained and all(v <= 0.5 for v in retained)
            and retained_ov and all(v <= 0.2 for v in retained_ov)
            and (ff.get("verify") or {}).get("residual") == 0
            and wf.get("has_doc") and (wf.get("full_black_ratio") or 0) >= 0.98
            and (wf.get("verify") or {}).get("residual") == 0
            and bf.get("types") == {"ID_AUTHORITY": 1, "ID_VALIDITY": 1}
            and not bf.get("has_doc")
            and set(bf_prot) == {"ID_AUTHORITY", "ID_VALIDITY"}
            and all(v >= 0.6 for v in bf_prot.values())
            and bf_ret and all(v <= 0.5 for v in bf_ret)
            and bf_ov and all(v <= 0.2 for v in bf_ov)
            and (bf.get("verify") or {}).get("residual") == 0
            and bw.get("has_doc") and (bw.get("full_black_ratio") or 0) >= 0.98
            and (bw.get("verify") or {}).get("residual") == 0
            and bnf.get("has_doc") and not bnf.get("id_types")
            and any("反面字段定位失败" in str(x) for x in (bnf.get("warnings") or []))
            and nf.get("has_doc")
            and any("整卡" in str(x) for x in (nf.get("warnings") or []))
        )
        return {"ok": ok, **res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


# ════════════════════════ P1-A 第一波（聊天/招牌/未成年/时间模糊/组合风险） ════════════════════════
def _valid_id_number(birth: str = "20150101", seq: str = "123",
                     region: str = "310101") -> str:
    """生成虚构但校验位合法的 18 位身份证号（仅测试用）。"""
    body = f"{region}{birth}{seq}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    total = sum(int(c) * w for c, w in zip(body, weights))
    return body + "10X98765432"[total % 11]


def _wait_job_inproc(job, timeout: float = 180) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if getattr(job, "stage", "") in ("ready", "error"):
            return
        time.sleep(0.1)


def _entity_rects(ents) -> list[tuple[str, tuple]]:
    out: list[tuple[str, tuple]] = []
    for e in ents:
        for sp in (e.spans or []):
            if sp.rect:
                out.append((e.etype, tuple(float(v) for v in sp.rect)))
                break
    return out


def chat_chain_test() -> dict:
    """P1-A：聊天截图逐项识别 → jobs 实体 CHAT_* → human 导出遮盖 → verify。"""
    ensure_ready()
    try:
        from PIL import Image
        from yinzhan import registry
        from yinzhan.jobs import MANAGER
        import test_visual as tv

        registry.load_plugins()
        root = WORK / "fixtures_chat_p1a"
        root.mkdir(parents=True, exist_ok=True)
        fixtures: dict = {}
        tv._add_chat_fixtures(root, fixtures)
        expected = {
            "chat_single": {"CHAT_AVATAR", "CHAT_NAME", "CHAT_TIME", "CHAT_INPUT"},
            "chat_group": {"CHAT_GROUP", "CHAT_AVATAR", "CHAT_INPUT"},
            "chat_dark": {"CHAT_AVATAR", "CHAT_NAME", "CHAT_TIME", "CHAT_INPUT"},
            "chat_landscape": {"CHAT_AVATAR", "CHAT_NAME", "CHAT_INPUT"},
            "chat_long": {"CHAT_AVATAR", "CHAT_NAME", "CHAT_TIME", "CHAT_INPUT"},
        }
        results: dict = {}
        for name, need in expected.items():
            item = fixtures.get(name) or {}
            path = item.get("path")
            rec: dict = {"ok": False}
            if not path or not Path(path).is_file():
                rec["err"] = "fixture 缺失"
                results[name] = rec
                continue
            job = MANAGER.create(Path(path))
            _wait_job_inproc(job)
            ents = [e for e in list(job.entities) if e.etype.startswith("CHAT_")]
            types = {e.etype for e in ents}
            rec.update({"stage": job.stage, "types": sorted(types),
                        "missing": sorted(need - types)})
            try:
                exp = MANAGER.export(job.id, "human")
                out = Path(exp["out"])
                img = Image.open(out).convert("RGB")
                ratios = [_black_ratio(img, r) for _t, r in _entity_rects(ents)]
                rec["black_ratios"] = ratios
                rec["verify"] = dict(exp.get("verify") or {})
                rec["ok"] = (rec["stage"] == "ready" and not rec["missing"]
                             and bool(ratios) and all(v >= 0.6 for v in ratios)
                             and (rec["verify"] or {}).get("residual") == 0)
            except Exception as exc:  # noqa: BLE001
                rec["export_error"] = f"{type(exc).__name__}: {exc}"
            results[name] = rec

        negatives: dict = {}
        for name in ("invoice_screenshot", "contract_page", "blank_page",
                     "business_mention"):
            item = fixtures.get(name) or {}
            path = item.get("path")
            if not path or not Path(path).is_file():
                continue
            job = MANAGER.create(Path(path))
            _wait_job_inproc(job)
            chat = [e.etype for e in job.entities if e.etype.startswith("CHAT_")]
            negatives[name] = {"chat_entities": chat, "ok": not chat}
        card = SAMPLES / "名片.png"
        if card.is_file():
            job = MANAGER.create(card)
            _wait_job_inproc(job)
            chat = [e.etype for e in job.entities if e.etype.startswith("CHAT_")]
            negatives["sample_名片.png"] = {"chat_entities": chat, "ok": not chat}
        ok = (bool(results) and all(r.get("ok") for r in results.values())
              and all(v.get("ok") for v in negatives.values()))
        return {"ok": bool(ok), "cases": results, "negatives": negatives}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


SIGN_ETYPE = {"door_address": "DOOR_ADDRESS", "road_sign": "ROAD_SIGN",
              "shop_sign": "SHOP_SIGN", "org_plaque": "ORG_PLAQUE"}


def signs_chain_test() -> dict:
    """P1-A：招牌/门牌/路牌/铭牌扫描器 → jobs 实体 → human 导出遮盖 → verify。"""
    ensure_ready()
    try:
        from PIL import Image
        from yinzhan import registry
        from yinzhan.jobs import MANAGER

        registry.load_plugins()
        fx = TESTS / "fixtures" / "signs"
        manifest = fx / "manifest.json"
        if not manifest.is_file():
            return {"ok": False, "err": "fixtures/signs/manifest.json 缺失"}
        cases = json.loads(manifest.read_text("utf-8")).get("cases") or []
        results: dict = {}
        for case in cases:
            path = fx / str(case.get("file"))
            kind = str(case.get("kind") or "")
            etype = SIGN_ETYPE.get(kind, "")
            if not etype:
                # manifest 里也列了负样本（kind=null），负样本由 negatives 单独断言
                continue
            rec: dict = {"ok": False, "expect": etype}
            if not path.is_file():
                rec["err"] = "fixture 缺失"
                results[kind] = rec
                continue
            job = MANAGER.create(path)
            _wait_job_inproc(job)
            sign_ents = [e for e in job.entities if e.etype in SIGN_ETYPE.values()]
            types = {e.etype for e in sign_ents}
            rec.update({"stage": job.stage, "types": sorted(types),
                        "hit": etype in types})
            try:
                exp = MANAGER.export(job.id, "human")
                out = Path(exp["out"])
                img = Image.open(out).convert("RGB")
                ratios = [_black_ratio(img, r) for _t, r in _entity_rects(sign_ents)]
                rec["black_ratios"] = ratios
                rec["verify"] = dict(exp.get("verify") or {})
                rec["ok"] = bool(rec["hit"] and ratios
                                 and all(v >= 0.6 for v in ratios)
                                 and (rec["verify"] or {}).get("residual") == 0)
            except Exception as exc:  # noqa: BLE001
                rec["export_error"] = f"{type(exc).__name__}: {exc}"
            results[kind] = rec
        negatives: dict = {}
        for name in ("neg_document.png", "neg_card.png", "neg_scenery.png"):
            path = fx / name
            if not path.is_file():
                continue
            job = MANAGER.create(path)
            _wait_job_inproc(job)
            sig = [e.etype for e in job.entities if e.etype in SIGN_ETYPE.values()]
            negatives[name] = {"sign_entities": sig, "ok": not sig}
        ok = (len(results) == 4 and all(r.get("ok") for r in results.values())
              and negatives and all(v.get("ok") for v in negatives.values()))
        return {"ok": bool(ok), "cases": results, "negatives": negatives}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


# ── 未成年 / 时间模糊 / 组合风险：HTTP 真实链路（jobs API） ──
def _job_via_api(base: str, body: dict) -> dict:
    r = http_json(base + "/api/jobs", "POST", body)
    try:
        jb = json.loads(r["body"] or b"{}")
    except Exception:
        jb = {}
    out = {"create_status": r["status"], **jb}
    jid = jb.get("job_id")
    if jid:
        snap, stage, poll_s = _wait_job(base, jid)
        out.update({"stage": stage, "poll_s": poll_s, "snapshot": snap})
    return out


def _export_text_via_api(base: str, jid: str) -> tuple[str, dict]:
    rx = http_json(base + f"/api/jobs/{jid}/export", "POST", {"mode": "ai"},
                   timeout=120)
    try:
        rec = json.loads(rx["body"] or b"{}")
    except Exception:
        rec = {}
    if rx["status"] != 200:
        return "", rec
    rd = http_bytes(base + f"/api/jobs/{jid}/file?mode=ai", timeout=120)
    return (rd.get("body") or b"").decode("utf-8", "replace"), rec


def capture_p1a_api(base: str) -> dict:
    """P1-A API 契约：时间模糊（默认关/开启）、未成年人推断+严格模式、组合风险。"""
    d = WORK / "p1a_api"
    d.mkdir(parents=True, exist_ok=True)
    minor_id = _valid_id_number()
    time_doc = d / "p1a_time.txt"
    time_doc.write_text(
        "会议时间：2026-10-20 14:35:22\n"
        "地点：上海市浦东新区测试路1号\n"
        "联系人：张伟 13800138000\n", "utf-8")
    minor_doc = d / "p1a_minor.txt"
    minor_doc.write_text(
        f"学生姓名：小明\n身份证号：{minor_id}\n监护人电话：13800138000\n",
        "utf-8")
    out: dict = {"fixtures": {"minor_id": minor_id}}
    # 1) 时间模糊默认关
    j = _job_via_api(base, {"path": str(time_doc)})
    jid = j.get("job_id") or ""
    text_default, _rec = _export_text_via_api(base, jid) if jid else ("", {})
    out["time_default"] = {
        "stage": j.get("stage"),
        "time_blur": (j.get("snapshot") or {}).get("time_blur"),
        "text_has_exact_time": "14:35" in text_default,
        "text": text_default[:200],
    }
    # 2) 时间模糊开启
    j2 = _job_via_api(base, {"path": str(time_doc), "time_blur": True})
    jid2 = j2.get("job_id") or ""
    text_blur = ""
    rec_blur: dict = {}
    if jid2:
        text_blur, rec_blur = _export_text_via_api(base, jid2)
    try:
        eb = json.loads((http_json(base + f"/api/jobs/{jid2}/entities")
                         .get("body") or b"{}"))
    except Exception:
        eb = {}
    time_ents = [e for e in (eb.get("entities") or []) if e.get("type") == "TIME"]
    snap2 = j2.get("snapshot") or {}
    out["time_blur"] = {
        "stage": j2.get("stage"),
        "time_blur": snap2.get("time_blur"),
        "time_entities": len(time_ents),
        "surrogates": [e.get("surrogate") for e in time_ents][:3],
        "no_exact_time": ("14:35" not in text_blur
                          and "14：35" not in text_blur),
        "date_kept": ("2026-10-20" in text_blur
                      or "2026年10月20日" in text_blur),
        "period_ok": any(p in text_blur for p in
                         ("凌晨", "上午", "中午", "下午", "晚上")),
        "verify": rec_blur.get("verify"),
        "combination_risk": snap2.get("combination_risk"),
        "text": text_blur[:300],
    }
    # 3) 未成年人推断 + 严格模式
    jm = _job_via_api(base, {"path": str(minor_doc)})
    jidm = jm.get("job_id") or ""
    snapm = jm.get("snapshot") or {}
    strict: dict = {"status": None}
    if jidm:
        rs = http_json(base + f"/api/jobs/{jidm}/minor-strict", "POST", {})
        try:
            sb = json.loads(rs["body"] or b"{}")
        except Exception:
            sb = {}
        text_strict, rec_strict = _export_text_via_api(base, jidm)
        strict = {"status": rs["status"],
                  "minor_strict": sb.get("minor_strict"),
                  "id_card_mode": sb.get("id_card_mode"),
                  "minor_detected": sb.get("minor_detected"),
                  "snapshot_minor_strict": sb.get("minor_strict"),
                  "snapshot_minor_detected": sb.get("minor_detected"),
                  "snapshot_id_card_mode": sb.get("id_card_mode"),
                  "export_no_id": minor_id not in text_strict,
                  "verify": rec_strict.get("verify"),
                  "warnings": (sb.get("warnings") or [])[-3:]}
    out["minor"] = {
        "stage": jm.get("stage"),
        "minor_detected": snapm.get("minor_detected"),
        "warnings": (snapm.get("warnings") or [])[:5],
        "strict": strict,
    }
    # 4) 非未成年任务启用严格模式 → 400
    if jid:
        r400 = http_json(base + f"/api/jobs/{jid}/minor-strict", "POST", {})
        out["minor_strict_reject"] = {
            "status": r400["status"],
            "body": (r400["body"] or b"")[:200].decode("utf-8", "replace"),
        }
    return out


# ════════════════════════ P1-B（Office 内嵌图/隐藏对象、CSV/TSV、PDF 内嵌图） ════════════════════════
def _p1b_job(path: Path):
    from yinzhan.jobs import MANAGER
    job = MANAGER.create(Path(path))
    _wait_job_inproc(job)
    return job


def _p1b_media(job) -> dict:
    return dict(((getattr(job, "doc", None).meta or {}).get("embedded_media") or {})
                if getattr(job, "doc", None) else {})


def office_media_chain_test() -> dict:
    """P1-B：Office 内嵌图片自动脱敏、隐藏对象确认语义、strict 剥离、正常图不变、审计。"""
    ensure_ready()
    try:
        import test_office_media as tom
        from yinzhan import audit, registry
        from yinzhan.jobs import MANAGER
        from yinzhan.udm import ParseContext, YinzhanError

        registry.load_plugins()
        ctx = ParseContext(ocr=None, workdir=str(WORK))

        def _blocked(fn):
            try:
                fn()
                return {"code": "", "msg": "", "blocked": False}
            except YinzhanError as exc:
                return {"code": getattr(exc, "code", ""), "msg": str(exc),
                        "blocked": True}
            except Exception as exc:  # noqa: BLE001
                return {"code": type(exc).__name__, "msg": str(exc),
                        "blocked": False}

        def _rescan_office(path: Path, prefix: str) -> dict:
            parts = tom._zip_media_parts(path)
            marks, means = 0, []
            for part in parts:
                if not part.startswith(prefix):
                    continue
                try:
                    data = tom._part_bytes(path, part)
                except Exception:
                    continue
                marks += len(tom._scan_marks(data))
                means.append(round(tom._image_mean(data), 1))
            return {"media_parts": sorted(p for p in parts if p.startswith(prefix)),
                    "residual_marks": marks, "means": means}

        res: dict = {}
        # ── DOCX 含敏感图 + 隐藏对象 ──
        dx = tom.Fixtures.docx_hidden()
        j = _p1b_job(dx)
        m = _p1b_media(j)
        blocked = _blocked(lambda: MANAGER.export(j.id, "human"))
        docx = {"image_count": int(m.get("image_count") or 0),
                "hidden_count": int(m.get("count") or 0),
                "hidden_objects": (m.get("hidden_objects") or [])[:5],
                "blocked": blocked}
        try:
            rec = MANAGER.export(j.id, "human",
                                 options={"allow_unprocessed_media": True})
            out = Path(rec["out"])
            scan = _rescan_office(out, "word/media/")
            re_doc = registry.find_parser(out).parse(out, ctx)
            rm = (re_doc.meta or {}).get("embedded_media") or {}
            docx.update({"allow_ok": True, "verify": rec.get("verify"),
                         "rescan": scan,
                         "hidden_after": int(rm.get("count") or 0),
                         "out": str(out)})
        except Exception as exc:  # noqa: BLE001
            docx.update({"allow_ok": False, "allow_err": repr(exc)})
        try:
            rec = MANAGER.export(j.id, "human",
                                 options={"allow_unprocessed_media": True,
                                          "strict_office_media": True})
            out = Path(rec["out"])
            re_doc = registry.find_parser(out).parse(out, ctx)
            rm = (re_doc.meta or {}).get("embedded_media") or {}
            scan = _rescan_office(out, "word/media/")
            docx["strict"] = {"hidden_after": int(rm.get("count") or 0),
                              "rescan_marks": scan["residual_marks"],
                              "verify": rec.get("verify")}
        except Exception as exc:  # noqa: BLE001
            docx["strict"] = {"err": repr(exc)}
        res["docx_hidden"] = docx
        # ── DOCX 正常图（无命中）：不弹确认、字节不变 ──
        dc = tom.Fixtures.docx_clean()
        jc = _p1b_job(dc)
        mc = _p1b_media(jc)
        docx_clean = {"image_count": int(mc.get("image_count") or 0),
                      "hidden_count": int(mc.get("count") or 0)}
        try:
            rec = MANAGER.export(jc.id, "human")
            out = Path(rec["out"])
            same = []
            for part in tom._zip_media_parts(dc):
                if not part.startswith("word/media/"):
                    continue
                try:
                    same.append(tom._part_bytes(dc, part)
                                == tom._part_bytes(out, part))
                except Exception:
                    same.append(False)
            docx_clean.update({"export_ok": True,
                               "verify": rec.get("verify"),
                               "parts_unchanged": same,
                               "blocked_expected_false": True})
        except Exception as exc:  # noqa: BLE001
            docx_clean.update({"export_ok": False, "err": repr(exc)})
        res["docx_clean"] = docx_clean
        # ── XLSX 含敏感图 + 隐藏对象 ──
        xh = tom.Fixtures.xlsx_hidden()
        jx = _p1b_job(xh)
        mx = _p1b_media(jx)
        xlsx = {"image_count": int(mx.get("image_count") or 0),
                "hidden_count": int(mx.get("count") or 0)}
        try:
            rec = MANAGER.export(jx.id, "human",
                                 options={"allow_unprocessed_media": True})
            out = Path(rec["out"])
            scan = _rescan_office(out, "xl/media/")
            xlsx.update({"allow_ok": True, "verify": rec.get("verify"),
                         "rescan": scan})
        except Exception as exc:  # noqa: BLE001
            xlsx.update({"allow_ok": False, "err": repr(exc)})
        res["xlsx_hidden"] = xlsx
        # ── 审计 counts.office_media ──
        office_audit = None
        try:
            for record in audit.recent(20):
                counts = record.get("counts") or {}
                if "office_media" in counts:
                    office_audit = counts.get("office_media")
                    break
        except Exception:
            office_audit = None
        res["audit_office_media"] = office_audit
        d = res.get("docx_hidden") or {}
        c = res.get("docx_clean") or {}
        x = res.get("xlsx_hidden") or {}
        ok = bool(
            d.get("image_count", 0) >= 2 and d.get("hidden_count", 0) >= 2
            and d.get("blocked", {}).get("blocked") is True
            and "隐藏对象" in d.get("blocked", {}).get("msg", "")
            and d.get("allow_ok") and d.get("rescan", {}).get("residual_marks") == 0
            and (d.get("verify") or {}).get("verified") is True
            and (d.get("strict") or {}).get("hidden_after") == 0
            and (d.get("strict") or {}).get("rescan_marks") == 0
            and (d.get("strict") or {}).get("verify", {}).get("verified") is True
            and c.get("hidden_count") == 0 and c.get("export_ok") is True
            and c.get("parts_unchanged") and all(c.get("parts_unchanged"))
            and (c.get("verify") or {}).get("verified") is True
            and x.get("image_count", 0) >= 2 and x.get("hidden_count", 0) >= 2
            and x.get("allow_ok") and x.get("rescan", {}).get("residual_marks") == 0
            and (x.get("verify") or {}).get("verified") is True
            and office_audit is not None)
        return {"ok": ok, **res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


def csv_chain_test() -> dict:
    """P1-B：CSV/TSV 列级+多级/合并表头、导出行列不变、无表头 0 命中。"""
    ensure_ready()
    try:
        import csv as _csv
        from yinzhan import registry
        from yinzhan.jobs import MANAGER

        registry.load_plugins()
        fx = TESTS / "fixtures" / "csv_table"
        expected = json.loads((fx / "expected.json").read_text("utf-8"))
        names = ("client_list.csv", "client_list.tsv", "two_level.csv",
                 "merged_header.csv", "vertical_multilevel.csv",
                 "no_header.csv")

        def _shape(path: Path):
            delim = "\t" if path.suffix.lower() == ".tsv" else ","
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                    rows = list(_csv.reader(fh, delimiter=delim))
            except Exception:
                return None
            return (len(rows), max((len(r) for r in rows), default=0))

        results: dict = {}
        for name in names:
            p = fx / name
            if not p.is_file():
                continue
            job = _p1b_job(p)
            table_ents = [e for e in job.entities if e.origin == "table"]

            def _norm_v(value) -> str:
                return "".join(str(value or "").split())

            values = {_norm_v(e.value) for e in job.entities if e.value}
            exp = expected.get(name) or {}
            missing = [(rid, v) for rid, vals in exp.items() for v in vals
                       if _norm_v(v) not in values]
            rec = {"stage": job.stage, "table_entities": len(table_ents),
                   "entities": len(job.entities),
                   "missing_count": len(missing), "missing": missing[:4]}
            if name == "no_header.csv":
                rec["ok"] = (job.stage == "ready" and not table_ents)
            else:
                try:
                    exp_rec = MANAGER.export(job.id, "ai")
                    outp = Path(exp_rec["out"])
                    src_shape, out_shape = _shape(p), _shape(outp)
                    rec.update({"verify": exp_rec.get("verify"),
                                "shape_src": src_shape, "shape_out": out_shape,
                                "suffix_kept": outp.suffix == p.suffix})
                    rec["ok"] = bool(
                        job.stage == "ready" and len(table_ents) >= 1
                        and not missing and src_shape == out_shape
                        and outp.suffix == p.suffix
                        and (exp_rec.get("verify") or {}).get("residual") == 0)
                except Exception as exc:  # noqa: BLE001
                    rec.update({"ok": False, "err": repr(exc)})
            results[name] = rec
        ok = bool(results) and all(r.get("ok") for r in results.values())
        return {"ok": ok, "cases": results}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


def _pdf_black_ratio(exported: Path, rect, page: int) -> float:
    """渲染导出 PDF 页面，统计实体矩形内黑像素比例。"""
    try:
        import fitz
        import numpy as np
        with fitz.open(str(exported)) as pdf:
            pg = pdf[page]
            pix = pg.get_pixmap(dpi=72)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)[:, :, :3]
            sx = pix.width / float(pg.rect.width or 1)
            sy = pix.height / float(pg.rect.height or 1)
            x0 = max(0, int(rect[0] * sx))
            y0 = max(0, int(rect[1] * sy))
            x1 = min(pix.width, int(rect[2] * sx))
            y1 = min(pix.height, int(rect[3] * sy))
            if x1 <= x0 or y1 <= y0:
                return 0.0
            crop = arr[y0:y1, x0:x1]
            if crop.size == 0:
                return 0.0
            black = np.all(crop < 45, axis=2)
            return round(float(black.mean()), 3)
    except Exception:
        return -1.0


def pdf_embedded_chain_test() -> dict:
    """P1-B：PDF 文本层内嵌图 → EMBEDDED_IMAGE → human 涂黑 → 重解析 0 残留。"""
    ensure_ready()
    try:
        from yinzhan import registry
        from yinzhan.jobs import MANAGER
        from yinzhan.udm import ParseContext

        registry.load_plugins()
        fx = TESTS / "fixtures" / "pdf_embedded"
        manifest = json.loads((fx / "manifest.json").read_text("utf-8"))
        cases = manifest.get("cases") or {}
        results: dict = {}
        for name, spec in cases.items():
            p = fx / str(spec.get("file"))
            if not p.is_file():
                continue
            job = _p1b_job(p)
            emb = [e for e in job.entities if e.etype == "EMBEDDED_IMAGE"]
            rec = {"stage": job.stage, "embedded": len(emb)}
            if name == "sensitive":
                try:
                    exp = MANAGER.export(job.id, "human")
                    outp = Path(exp["out"])
                    ctx = ParseContext(ocr=None, workdir=str(WORK))
                    re_doc = registry.find_parser(outp).parse(outp, ctx)
                    re_emb = [m for m in (re_doc.visual_marks or [])
                              if getattr(m, "kind", "") == "embedded_image"]
                    ratios = []
                    for e in emb:
                        for sp in e.spans:
                            if sp.rect:
                                ratios.append(_pdf_black_ratio(
                                    outp, sp.rect, sp.page or 0))
                                break
                    rec.update({"verify": exp.get("verify"),
                                "reparse_embedded": len(re_emb),
                                "black_ratios": ratios})
                    rec["ok"] = bool(
                        len(emb) >= 2 and len(re_emb) == 0
                        and ratios and all(v >= 0.9 for v in ratios)
                        and (exp.get("verify") or {}).get("residual") == 0)
                except Exception as exc:  # noqa: BLE001
                    rec.update({"ok": False, "err": repr(exc)})
            else:
                rec["ok"] = (job.stage == "ready" and not emb)
            results[name] = rec
        ok = bool(results) and all(r.get("ok") for r in results.values())
        return {"ok": ok, "cases": results}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


# ════════════════════════ P1.5（内嵌图文字 OCR / 证件类型 / CSV 编码 / 护照号） ════════════════════════
def _p15_ctx():
    from yinzhan.ocr import get_ocr
    from yinzhan.udm import ParseContext
    return ParseContext(ocr=get_ocr(), workdir=str(WORK))


def _p15_job(path: Path):
    from yinzhan.jobs import MANAGER
    job = MANAGER.create(Path(path))
    _wait_job_inproc(job)
    return job


def _p15_media_marks(path: Path) -> int:
    """重扫 zip 内图片的视觉/码类命中数（test_office_media 的 helper）。"""
    import test_office_media as tom
    marks = 0
    for part in tom._zip_media_parts(path):
        try:
            marks += len(tom._scan_marks(tom._part_bytes(path, part)))
        except Exception:
            continue
    return marks


def office_text_ocr_chain_test() -> dict:
    """P1.5：Office 内嵌图片文字 OCR 自动脱敏 / 预算跳过 / 开关关闭时仍做视觉脱敏。"""
    ensure_ready()
    try:
        import test_office_media as tom
        from yinzhan import audit, registry
        from yinzhan.jobs import MANAGER

        registry.load_plugins()

        def _extra(job) -> dict:
            doc = getattr(job, "doc", None)
            return dict(((getattr(doc, "extra", None) or {}).get("office_media") or {}))

        old = os.environ.get("YINZHAN_OFFICE_TEXT_OCR")
        res: dict = {}
        try:
            os.environ["YINZHAN_OFFICE_TEXT_OCR"] = "1"
            src = tom.Fixtures.docx_text()
            job = _p15_job(src)
            e = _extra(job)
            on = {"stage": job.stage,
                  "text_hits": int(e.get("text_hits") or 0),
                  "text_images_redacted": int(e.get("text_images_redacted") or 0),
                  "image_count": int(e.get("image_count") or 0)}
            rec = MANAGER.export(job.id, "human")
            out = Path(rec["out"])
            e = _extra(job)          # 文字 OCR 统计在导出阶段写入 doc.extra
            on["text_hits"] = int(e.get("text_hits") or 0)
            on["text_images_redacted"] = int(e.get("text_images_redacted") or 0)
            on["text_verify_hits"] = int(e.get("text_verify_hits") or 0)
            on["image_count"] = int(e.get("image_count") or 0)
            re_doc = registry.find_parser(out).parse(out, _p15_ctx())
            ee = dict(((re_doc.extra or {}).get("office_media") or {}))
            on.update({"verify": rec.get("verify"),
                       "out_text_hits": int(ee.get("text_hits") or 0)})
            src_parts = tom._zip_media_parts(src)
            out_parts = tom._zip_media_parts(out)
            changed = unchanged = 0
            for part in src_parts:
                if part not in out_parts:
                    continue
                try:
                    same = tom._part_bytes(src, part) == tom._part_bytes(out, part)
                except Exception:
                    continue
                changed += 0 if same else 1
                unchanged += 1 if same else 0
            on.update({"changed_media": changed, "unchanged_media": unchanged})
            # 预算跳过：max_images=0 时不处理文字，但不应崩溃
            os.environ["YINZHAN_OFFICE_TEXT_MAX_IMAGES"] = "0"
            jb = _p15_job(tom.Fixtures.docx_text())
            eb = _extra(jb)
            budget = {"text_hits": int(eb.get("text_hits") or 0),
                      "warnings": [w for w in (getattr(jb.doc, "warnings", None) or [])
                                   if "文字" in str(w) or "预算" in str(w)][:4]}
            os.environ.pop("YINZHAN_OFFICE_TEXT_MAX_IMAGES", None)
            # 开关关闭：文字不处理，但视觉/码类仍脱敏
            os.environ["YINZHAN_OFFICE_TEXT_OCR"] = "0"
            j2 = _p15_job(tom.Fixtures.docx_qr_text())
            e2 = _extra(j2)
            off = {"stage": j2.stage,
                   "text_hits": int(e2.get("text_hits") or 0),
                   "image_count": int(e2.get("image_count") or 0)}
            rec2 = MANAGER.export(j2.id, "human")
            out2 = Path(rec2["out"])
            off.update({"visual_rescan": _p15_media_marks(out2),
                        "verify": rec2.get("verify")})
            res.update({"on": on, "budget": budget, "off": off})
        finally:
            os.environ.pop("YINZHAN_OFFICE_TEXT_MAX_IMAGES", None)
            if old is None:
                os.environ.pop("YINZHAN_OFFICE_TEXT_OCR", None)
            else:
                os.environ["YINZHAN_OFFICE_TEXT_OCR"] = old
        audit_text = None
        try:
            for record in audit.recent(40):
                c = (record.get("counts") or {}).get("office_media")
                if isinstance(c, dict) and "text_hits" in c:
                    audit_text = c
                    break
        except Exception:
            audit_text = None
        res["audit_text"] = audit_text
        on = res.get("on") or {}
        off = res.get("off") or {}
        bg = res.get("budget") or {}
        ok = bool(on.get("text_hits", 0) >= 1
                  and on.get("text_images_redacted", 0) >= 1
                  and on.get("text_verify_hits") == 0
                  and on.get("out_text_hits") == 0
                  and on.get("changed_media", 0) >= 1
                  and on.get("unchanged_media", 0) >= 1
                  and (on.get("verify") or {}).get("verified") is True
                  and bg.get("text_hits") == 0 and bg.get("warnings")
                  and off.get("text_hits") == 0
                  and off.get("visual_rescan") == 0
                  and (off.get("verify") or {}).get("verified") is True
                  and audit_text is not None)
        return {"ok": ok, **res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


def pdf_embedded_text_chain_test() -> dict:
    """P1.5：PDF 文本层内嵌「纯文字图」→ EMBEDDED_IMAGE(note text=N) → 涂黑/复检/开关。"""
    ensure_ready()
    try:
        import test_pdf_embedded as tpe
        from yinzhan import registry
        from yinzhan.jobs import MANAGER
        from yinzhan.udm import ParseContext

        registry.load_plugins()
        path = TESTS / "fixtures" / "pdf_embedded" / "text_sensitive.pdf"
        if not path.is_file():
            return {"ok": False, "err": "text_sensitive.pdf 缺失"}
        old = os.environ.get("YINZHAN_PDF_EMBEDDED_TEXT_OCR")
        res: dict = {}
        try:
            os.environ.pop("YINZHAN_PDF_EMBEDDED_TEXT_OCR", None)
            job = _p15_job(path)
            marks = [m for m in (getattr(job.doc, "visual_marks", None) or [])
                     if getattr(m, "kind", "") == "embedded_image"]
            text_counts = [int(mm.group(1)) for m in marks
                           for mm in [re.search(r"text=(\d+)", str(m.note or ""))]
                           if mm]
            emb = [e for e in job.entities if e.etype == "EMBEDDED_IMAGE"]
            exp = MANAGER.export(job.id, "human")
            out = Path(exp["out"])
            ratios = []
            for e in emb:
                for sp in e.spans:
                    if sp.rect:
                        ratios.append(_pdf_black_ratio(out, sp.rect, sp.page or 0))
                        break
            re_doc = registry.find_parser(out).parse(
                out, ParseContext(ocr=None, workdir=str(WORK)))
            re_emb = sum(1 for m in (re_doc.visual_marks or [])
                         if getattr(m, "kind", "") == "embedded_image")
            on = {"marks": len(marks), "text_counts": text_counts,
                  "embedded": len(emb), "black_ratios": ratios,
                  "reparse_embedded": re_emb,
                  "verify": exp.get("verify")}
            os.environ["YINZHAN_PDF_EMBEDDED_TEXT_OCR"] = "0"
            job2 = _p15_job(path)
            off = {"marks": sum(1 for m in (getattr(job2.doc, "visual_marks", None) or [])
                                if getattr(m, "kind", "") == "embedded_image")}
            res.update({"on": on, "off": off})
        finally:
            if old is None:
                os.environ.pop("YINZHAN_PDF_EMBEDDED_TEXT_OCR", None)
            else:
                os.environ["YINZHAN_PDF_EMBEDDED_TEXT_OCR"] = old
        on = res.get("on") or {}
        off = res.get("off") or {}
        ok = bool(on.get("marks", 0) >= 1
                  and on.get("text_counts") and min(on["text_counts"]) >= 2
                  and on.get("embedded", 0) >= 1
                  and on.get("black_ratios")
                  and all(v >= 0.9 for v in on.get("black_ratios") or [])
                  and on.get("reparse_embedded") == 0
                  and (on.get("verify") or {}).get("residual") == 0
                  and off.get("marks") == 0)
        return {"ok": ok, **res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


P15_DOC_LABELS = {
    "work_permit": "工作证", "residence_permit": "居住证",
    "hk_mo_permit": "港澳通行证", "tw_permit": "台胞证",
    "foreign_pr": "外国人永居证", "bank_card": "银行卡",
}


def id_doc_types_chain_test() -> dict:
    """P1.5：6 类非身份证证件经 jobs 出 ID_DOCUMENT 且 label 正确；银行卡不整页；负样本 0。"""
    ensure_ready()
    try:
        from PIL import Image
        import test_visual as tv
        from yinzhan.jobs import MANAGER

        root = WORK / "fixtures_p15_docs"
        root.mkdir(parents=True, exist_ok=True)
        fixtures: dict = {}
        tv._add_id_type_fixtures(root, fixtures)
        results: dict = {}
        for name, want_label in P15_DOC_LABELS.items():
            fixture = fixtures.get(name) or {}
            path = fixture.get("path")
            rec: dict = {"ok": False, "want": want_label}
            if not path or not Path(path).is_file():
                rec["err"] = "fixture 缺失"
                results[name] = rec
                continue
            job = _p15_job(path)
            docs = [e for e in job.entities if e.etype == "ID_DOCUMENT"]
            labels = [e.label for e in docs]
            rec.update({"stage": job.stage, "labels": labels,
                        "hit": want_label in labels})
            if docs:
                rect = None
                for sp in docs[0].spans:
                    if sp.rect:
                        rect = tuple(float(v) for v in sp.rect)
                        break
                page = job.doc.pages[docs[0].spans[0].page or 0] if job.doc.pages else None
                if rect and page and page.width and page.height:
                    rec["area_ratio"] = round(
                        (rect[2] - rect[0]) * (rect[3] - rect[1])
                        / (page.width * page.height), 3)
                try:
                    exp = MANAGER.export(job.id, "human")
                    out = Path(exp["out"])
                    img = Image.open(out).convert("RGB")
                    rec["black"] = _black_ratio(img, rect) if rect else 0.0
                    rec["verify"] = exp.get("verify")
                    rec["ok"] = bool(rec["hit"] and (rec.get("black") or 0) >= 0.6
                                     and (exp.get("verify") or {}).get("residual") == 0)
                    if name == "bank_card":
                        rec["ok"] = bool(rec["ok"]
                                         and float(rec.get("area_ratio") or 1.0) < 0.90)
                except Exception as exc:  # noqa: BLE001
                    rec["export_error"] = repr(exc)
            results[name] = rec
        negatives: dict = {}
        neg_paths = {
            "invoice": TESTS / "fixtures" / "pipl_public" / "synthetic_invoice.png",
            "contract": TESTS / "fixtures" / "signs" / "neg_document.png",
        }
        for name, path in neg_paths.items():
            if not Path(path).is_file():
                continue
            job = _p15_job(path)
            ids = [e.etype for e in job.entities if e.etype.startswith("ID_")]
            negatives[name] = {"id_entities": ids, "ok": not ids}
        ok = (len(results) == 6 and all(r.get("ok") for r in results.values())
              and negatives and all(v.get("ok") for v in negatives.values()))
        return {"ok": bool(ok), "cases": results, "negatives": negatives}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


def _detect_text_entities(text: str):
    from yinzhan import pipeline
    from yinzhan.jobs import _load_rules
    from yinzhan.udm import Block, Document, Page
    doc = Document(doc_id="p15", path="", fmt="text")
    doc.pages.append(Page(index=0, width=595, height=842,
                          blocks=[Block(id="b0", kind="text", text=text, page=0)]))
    rules, _pack, _idx = _load_rules("text")
    det = pipeline.detect(doc, rules, "standard")
    pipeline.decide(det.entities, det.rules, pipeline.Session())
    return det


def passport_rule_chain_test() -> dict:
    """P1.5：护照号规则（near 护照才命中；surrogate 假值；普通编号/法人不误报）。"""
    ensure_ready()
    try:
        from yinzhan import registry
        registry.load_plugins()
        yes = _detect_text_entities("护照号码：E12345678\n姓名：张三")
        yes_ents = [e for e in yes.entities if e.etype == "PASSPORT"]
        fake_ok = False
        if yes_ents:
            e = yes_ents[0]
            repl = getattr(e.decision, "replacement", None) if e.decision else None
            fake_ok = bool(e.value == "E12345678" and repl
                           and repl != e.value
                           and not getattr(e.decision, "black", False))
        no_near = _detect_text_entities("编号：E12345678")
        legal = _detect_text_entities(
            "法定代表人：王五\n统一社会信用代码：91310000MA1FL1XX0G")
        no_near_ok = not [e for e in no_near.entities if e.etype == "PASSPORT"]
        legal_ok = not [e for e in legal.entities if e.etype == "PASSPORT"]
        return {"ok": bool(yes_ents and fake_ok and no_near_ok and legal_ok),
                "yes": [(e.etype, e.value,
                         getattr(e.decision, "replacement", None) if e.decision
                         else None) for e in yes_ents[:3]],
                "no_near_passport": [e.value for e in no_near.entities
                                     if e.etype == "PASSPORT"],
                "legal_passport": [e.value for e in legal.entities
                                   if e.etype == "PASSPORT"]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


CSV_P15_CONTENT = ("姓名,手机号,备注\n张伟,13817264451,普通备注\n"
                   "李四,13901827733,第二条\n")


def csv_encoding_chain_test() -> dict:
    """P1.5：CSV/TSV 原编码/BOM/换行保留、未知编码 warning 回退、residual/行列不变。"""
    ensure_ready()
    try:
        import csv as _csv
        from yinzhan.jobs import MANAGER

        d = WORK / "p15_csv"
        d.mkdir(parents=True, exist_ok=True)
        content = CSV_P15_CONTENT
        variants = {
            "gbk": (content.encode("gb18030"), "gb18030", False),
            "sig": (b"\xef\xbb\xbf" + content.encode("utf-8"),
                    "utf-8-sig", True),
            "utf16": (content.encode("utf-16"), "utf-16", True),
            "crlf_gbk": (content.replace("\n", "\r\n").encode("gb18030"),
                         "gb18030", False),
        }
        expected_rows = [row for row in _csv.reader(content.splitlines())]

        def _rows(raw: bytes, encoding: str):
            try:
                text = raw.decode(encoding)
            except Exception:
                return None
            return [row for row in _csv.reader(text.splitlines())]

        results: dict = {}
        for label, (raw, encoding, expect_bom) in variants.items():
            p = d / f"{label}.csv"
            p.write_bytes(raw)
            job = _p15_job(p)
            meta = dict(getattr(job.doc, "meta", None) or {})
            rec = {"stage": job.stage,
                   "encoding": meta.get("encoding"),
                   "encoding_output": meta.get("encoding_output"),
                   "bom": meta.get("bom"), "newline": meta.get("newline")}
            try:
                exp = MANAGER.export(job.id, "ai")
                out = Path(exp["out"])
                out_raw = out.read_bytes()
                has_bom = (out_raw.startswith(b"\xef\xbb\xbf")
                           or out_raw.startswith(b"\xff\xfe")
                           or out_raw.startswith(b"\xfe\xff"))
                rows = _rows(out_raw, rec.get("encoding_output") or encoding)
                rec.update({"verify": exp.get("verify"),
                            "has_bom": has_bom,
                            "rows_ok": rows is not None
                            and [len(r) for r in rows]
                            == [len(r) for r in expected_rows]})
                good = (job.stage == "ready"
                        and rec.get("encoding") == encoding
                        and rec.get("encoding_output") == encoding
                        and bool(rec.get("bom")) is expect_bom
                        and rec.get("has_bom") is expect_bom
                        and rec.get("rows_ok")
                        and (exp.get("verify") or {}).get("residual") == 0)
                if label == "crlf_gbk":
                    good = good and rec.get("newline") == "CRLF" \
                        and b"\r\n" in out_raw
                rec["ok"] = bool(good)
            except Exception as exc:  # noqa: BLE001
                rec.update({"ok": False, "err": repr(exc)})
            results[label] = rec
        # 未知编码 → warning 回退 UTF-8
        bad = d / "unknown.csv"
        bad.write_bytes(b"\xff\xff\xff\xff")
        job = _p15_job(bad)
        meta = dict(getattr(job.doc, "meta", None) or {})
        unknown = {"stage": job.stage, "encoding": meta.get("encoding"),
                   "encoding_source": meta.get("encoding_source")}
        parse_warns = [str(w) for w in (getattr(job.doc, "warnings", None) or [])]
        unknown["parse_warnings"] = parse_warns[:4]
        warn: list = []
        try:
            exp = MANAGER.export(job.id, "ai")
            warn = list(exp.get("warnings") or [])
            unknown["export_ok"] = True
        except Exception as exc:  # noqa: BLE001
            unknown["export_ok"] = False
            unknown["export_err"] = repr(exc)
        fallback_warn = any(("回退 UTF-8" in str(w)) or ("未保留原编码" in str(w))
                            or ("替换字符" in str(w))
                            for w in (warn + parse_warns))
        unknown["fallback_warn"] = fallback_warn
        unknown["ok"] = bool(meta.get("encoding_source") == "fallback"
                             and (meta.get("encoding_output") or "").startswith("utf-8")
                             and fallback_warn)
        results["unknown"] = unknown
        ok = all(r.get("ok") for r in results.values())
        return {"ok": bool(ok), "cases": results}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


# ════════════ P0 修复（PLY 二进制完整性 / TIF Orientation） ════════════
def mesh_ply_chain_test() -> dict:
    """P0：PLY ascii/little/big 三子格式 jobs→导出→独立重读；篡改/截断阻断且不留成品。"""
    ensure_ready()
    try:
        import struct
        import test_mesh as tm
        from yinzhan import registry
        from yinzhan.jobs import MANAGER, OUTPUT_DIR
        from yinzhan.parsers import mesh_parser as mesh_mod
        from yinzhan.udm import ParseContext

        registry.load_plugins()

        def _ctx() -> ParseContext:
            return ParseContext(ocr=None, workdir=str(WORK))

        results: dict = {}
        for label, fixture in (("ascii", tm.Fixtures.ply_ascii),
                               ("le", tm.Fixtures.ply_le),
                               ("be", tm.Fixtures.ply_be)):
            src = fixture()
            rec: dict = {"ok": False}
            job = _p1b_job(src)
            rec["stage"] = job.stage
            parsed_text = " ".join(b.text for page in job.doc.pages
                                   for b in page.blocks)
            rec["parsed_has_phone"] = tm.SECRET_PHONE in parsed_text
            rec["entity_values"] = [e.value for e in job.entities
                                    if e.value][:4]
            try:
                exp = MANAGER.export(job.id, "human")
                out = Path(exp["out"])
                source = tm._read_ply(src)
                output = tm._read_ply(out)
                rec.update({"format": output.get("format"),
                            "verify": exp.get("verify"),
                            "header_has_phone": tm.SECRET_PHONE in tm._header_text(out),
                            "faces_ok": [tuple(f) for f in output.get("faces") or []]
                            == [tuple(f) for f in tm.FACES]})
                if str(source.get("format", "")).startswith("binary"):
                    rec["data_len_src"] = len(source.get("data") or b"")
                    rec["data_len_out"] = len(output.get("data") or b"")
                center = tm._source_center(source.get("verts") or [])
                bad = 0
                for sv, ov in zip(source.get("verts") or [], output.get("verts") or []):
                    for axis in range(3):
                        if abs(ov[axis] - (sv[axis] - center[axis])) > 1e-4:
                            bad += 1
                rec["vertex_mismatch"] = bad
                rec["verts_n"] = len(output.get("verts") or [])
                reparsed = registry.find_parser(out).parse(out, _ctx())
                rec["reparse_vertices"] = (reparsed.meta or {}).get("vertices")
                good = bool(job.stage == "ready" and rec["parsed_has_phone"]
                            and len(job.entities) >= 1
                            and not rec["header_has_phone"]
                            and rec["faces_ok"] and bad == 0
                            and rec["verts_n"] == len(tm.VERTS)
                            and rec["reparse_vertices"] == len(tm.VERTS))
                if rec.get("format", "").startswith("binary"):
                    good = good and rec["data_len_src"] == rec["data_len_out"]
                rec["ok"] = bool(good)
            except Exception as exc:  # noqa: BLE001
                rec["err"] = repr(exc)
            results[label] = rec

        # 截断源 → 导出失败且不留成品（不再复制源文件）
        guard: dict = {}
        try:
            before = set(p.name for p in OUTPUT_DIR.glob("*")) if OUTPUT_DIR.exists() else set()
            jt = _p1b_job(tm.Fixtures.ply_truncated())
            try:
                MANAGER.export(jt.id, "human")
                guard = {"raised": False}
            except Exception as exc:  # noqa: BLE001
                guard = {"raised": True, "type": type(exc).__name__,
                         "msg": str(exc)[:200]}
            after = set(p.name for p in OUTPUT_DIR.glob("*")) if OUTPUT_DIR.exists() else set()
            guard["new_outputs"] = sorted(after - before)
            guard["ok"] = bool(guard.get("raised")
                               and "PLY" in guard.get("msg", "")
                               and not guard.get("new_outputs"))
        except Exception as exc:  # noqa: BLE001
            guard = {"ok": False, "err": repr(exc)}

        # 篡改成品字节 → 自检 ExportError 且删除损坏成品
        tamper: dict = {}
        try:
            src = tm.Fixtures.ply_le()
            _doc0, _res0, out, _vals = tm._export(src)
            header = mesh_mod._read_ply_header(src)
            header_bytes, _hits = mesh_mod._ply_build_header(header, [], binary=True)
            corrupted = tm.Fixtures.get() / "regression_corrupted.ply"
            data = bytearray(out.read_bytes())
            data[-1] = (data[-1] + 1) % 256
            corrupted.write_bytes(data)
            try:
                mesh_mod._ply_verify_output(src, corrupted, header,
                                            header_bytes, "<", center=None,
                                            warnings=[])
                tamper = {"raised": False}
            except Exception as exc:  # noqa: BLE001
                tamper = {"raised": True, "type": type(exc).__name__,
                          "msg": str(exc)[:160]}
            tamper["corrupted_exists"] = corrupted.exists()
            tamper["ok"] = bool(tamper.get("raised")
                                and "PLY 成品复检失败" in tamper.get("msg", "")
                                and not tamper["corrupted_exists"])
        except Exception as exc:  # noqa: BLE001
            tamper = {"ok": False, "err": repr(exc)}

        ok = (len(results) == 3 and all(r.get("ok") for r in results.values())
              and guard.get("ok") and tamper.get("ok"))
        return {"ok": bool(ok), "cases": results, "truncated_guard": guard,
                "tamper": tamper}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


def tiff_orientation_chain_test() -> dict:
    """P0：TIFF Orientation=6 → Pillow 路径 parse/render/export 转正且成品无 274；rasterio 已知限制。"""
    ensure_ready()
    try:
        import test_tiff as tt
        from PIL import Image, ImageOps
        from yinzhan import georef, registry
        from yinzhan.jobs import MANAGER
        from yinzhan.parsers import tiff_parser
        from yinzhan.udm import ParseContext

        registry.load_plugins()
        src = tt.Fixtures.orientation()
        old = georef.HAS_RASTERIO
        res: dict = {}
        try:
            georef.HAS_RASTERIO = False
            job = _p1b_job(src)
            meta = dict(getattr(job.doc, "meta", None) or {})
            page = job.doc.pages[0]
            pillow = {"engine": meta.get("engine"),
                      "orientation": meta.get("orientation"),
                      "applied": meta.get("orientation_applied"),
                      "page_size": [page.width, page.height],
                      "warnings": [w for w in (job.doc.warnings or [])
                                   if "Orientation" in str(w)][:3]}
            from yinzhan import pipeline as _pipeline
            from yinzhan.parsers import tiff_parser as _tp
            png = job.parser.render(job.doc, 0, "orig")
            render_size = None
            if png:
                import io as _io
                with Image.open(_io.BytesIO(png)) as rendered:
                    render_size = list(rendered.size)
            out = WORK / "p0_orientation6_out.tif"
            exp = _pipeline.export(job.doc, job.parser, list(job.entities),
                                   out, {"mode": "human"})
            exp = {"out": str(out), "warnings": exp.warnings, "verify": {}}
            with Image.open(src) as image:
                expected = ImageOps.exif_transpose(image).convert("RGB")
            with Image.open(out) as exported:
                exported_rgb = exported.convert("RGB")
                pixels_ok = (exported_rgb.size == expected.size
                             and list(exported_rgb.getdata()) == list(expected.getdata())
                             if exported_rgb.size == expected.size else False)
                out_size = list(exported_rgb.size)
            with Image.open(out) as exported2:
                exif_274 = exported2.getexif().get(274)
            pillow.update({"render_size": render_size,
                           "out_size": out_size,
                           "expected_size": list(expected.size),
                           "pixels_ok": bool(pixels_ok),
                           "out_orientation_tag": exif_274,
                           "keeptags_no_274": 274 not in tiff_parser.KEEP_TAGS,
                           "verify": exp.get("verify")})
            pillow["ok"] = bool(pillow.get("engine") == "pillow"
                                and pillow.get("orientation") == 6
                                and pillow.get("applied") is True
                                and pillow.get("page_size") == [60.0, 120.0]
                                and render_size == [60, 120]
                                and any("Orientation=6" in w and "转正" in w
                                        for w in pillow.get("warnings") or [])
                                and pixels_ok and not exif_274
                                and 274 not in tiff_parser.KEEP_TAGS)
            # 无 Orientation 的 TIF：正常尺寸、成品无 274
            plain = WORK / "p0_plain.tif"
            Image.new("RGB", (48, 32), (200, 120, 40)).save(plain)
            jp = _p1b_job(plain)
            from yinzhan import pipeline as _pipeline2
            outp = WORK / "p0_plain_out.tif"
            _pipeline2.export(jp.doc, jp.parser, list(jp.entities), outp,
                              {"mode": "human"})
            ep = {"out": str(outp), "verify": {}}
            with Image.open(outp) as im2:
                plain_rec = {"size": list(im2.size),
                             "tag": im2.getexif().get(274),
                             "verify": ep.get("verify")}
            plain_rec["ok"] = bool(plain_rec["size"] == [48, 32]
                                   and not plain_rec["tag"])
            res["plain"] = plain_rec
            res["pillow"] = pillow
        finally:
            georef.HAS_RASTERIO = old
        # rasterio 路径（真实环境有 rasterio）：不转正像素但成品无 274（已知限制）
        raster = {"has_rasterio": bool(georef.has_rasterio())}
        if raster["has_rasterio"]:
            try:
                job2 = _p1b_job(src)
                meta2 = dict(getattr(job2.doc, "meta", None) or {})
                raster.update({"engine": meta2.get("engine"),
                               "orientation": meta2.get("orientation"),
                               "warnings": [w for w in (job2.doc.warnings or [])
                                            if "Orientation" in str(w)][:3]})
                from yinzhan import pipeline as _pipeline3
                out2 = WORK / "p0_rasterio_out.tif"
                _pipeline3.export(job2.doc, job2.parser, list(job2.entities),
                                  out2, {"mode": "human"})
                with Image.open(out2) as im3:
                    raster["out_tag"] = im3.getexif().get(274)
                raster["ok"] = bool(
                    raster.get("engine") == "rasterio"
                    and any("rasterio 路径暂不转正" in str(w)
                            or "不转正像素" in str(w)
                            for w in raster.get("warnings") or [])
                    and not raster.get("out_tag"))
            except Exception as exc:  # noqa: BLE001
                raster.update({"ok": False, "err": repr(exc)})
        else:
            raster["ok"] = True   # 无 rasterio 时该已知限制不适用
        res["rasterio"] = raster
        return {"ok": bool((res.get("pillow") or {}).get("ok")
                           and (res.get("plain") or {}).get("ok")
                           and raster.get("ok")),
                **res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": f"{type(exc).__name__}: {exc}"}


# ════════════════════════ 已知边界软记录 ════════════════════════
def capture_known_limits() -> dict:
    ensure_ready()

    def read(p: Path) -> str:
        try:
            return p.read_text("utf-8")
        except Exception:
            return ""

    pdf_src = read(SERVER / "yinzhan" / "parsers" / "pdf_parser.py")
    docx_src = read(SERVER / "yinzhan" / "parsers" / "docx_parser.py")
    xlsx_src = read(SERVER / "yinzhan" / "parsers" / "xlsx_parser.py")
    app_src = read(SERVER / "yinzhan" / "app.py")
    jobs_src = read(SERVER / "yinzhan" / "jobs.py")
    mcp_src = read(SERVER / "mcp_server.py")
    m = re.search(r"MIN_TEXT_CHARS\s*=\s*(\d+)", pdf_src)
    embedded = []
    for src, name in ((docx_src, "docx_parser"), (xlsx_src, "xlsx_parser")):
        for line in src.splitlines():
            s = line.strip()
            if "内嵌" in s or ("图片" in s and ("告警" in s or "warn" in s.lower())):
                embedded.append(f"{name}: {s[:140]}")
                break
    georef_src = read(SERVER / "yinzhan" / "georef.py")
    tiff_local_mode = LOCAL_CRS_MARKER in georef_src.lower()
    image_src = read(SERVER / "yinzhan" / "parsers" / "image_parser.py")
    visual_src = read(SERVER / "yinzhan" / "visual_targets.py")
    appjs_src = read(REPO / "app" / "web" / "app.js")
    hidden_keys = [k for k in ("embedded_files", "pdf_annotations",
                               "pdf_forms", "pdf_js") if k in pdf_src]
    return {
        "_note": "以下为现状快照，只做软记录；后续 P0 允许改变，不得当成硬断言。",
        "pdf_hidden_strip": {
            "meta_keys": hidden_keys,
            "default": "strip（导出默认剥离附件/批注/表单/JS/缩略图）",
            "keep_option": "keep_pdf_hidden" if "keep_pdf_hidden" in pdf_src else "",
            "verify_intercepts": "HIDDEN_META_KEYS" in read(
                SERVER / "yinzhan" / "pipeline.py")
            or "pdf_hidden" in read(SERVER / "yinzhan" / "pipeline.py"),
        },
        "image_exif_orientation": {
            "applied": "_apply_orientation" in image_src,
            "exif_transpose": "exif_transpose" in image_src,
        },
        "frontend_full_image_black": {
            "backend_mark_normalized_rect": True,
            "frontend_button": ("整图" in appjs_src
                                or "fullBlack" in appjs_src
                                or "rect: [0, 0, 1, 1]" in appjs_src
                                or "rect:[0,0,1,1]" in appjs_src),
        },
        # 身份证 A 方案（默认关键字段 / 可选整卡）
        "id_card_modes": {
            "default": "fields",
            "optional": ["fields", "whole"],
            "front_field_kinds": ["id_photo", "id_name", "id_number", "id_address"],
            "back_field_kinds": ["id_authority", "id_validity"],
            "field_kinds": ["id_photo", "id_name", "id_number", "id_address",
                            "id_authority", "id_validity"],
            "jobs_impl": "id_card_mode" in jobs_src,
            "visual_targets_fields": all(k in visual_src for k in
                                         ("id_photo", "id_name", "id_number",
                                          "id_address", "id_authority",
                                          "id_validity")),
            "ocr_text_entities_dropped_in_card":
                "_drop_text_entities_in_id_cards" in jobs_src,
            "frontend_selector": ("id_card_mode" in appjs_src
                                  or "idCardMode" in appjs_src
                                  or "整卡" in appjs_src),
        },
        "known_gaps_not_fixed": {
            "side_face_low_head_miss": "用户测试集 83 双人低头侧脸仍漏（Haar 模型上限，"
                                       "已写 docs/impl/visual.md，P1 YuNet 建议；本轮不修）",
        },
        # P1-A 第一波语义与已知限制（只做软记录）
        "p1a_features": {
            "chat_kinds": ["chat_avatar", "chat_name", "chat_group",
                           "chat_time", "chat_input"],
            "chat_only_image_fmt": "fmt == image（PDF/TIFF 不跑聊天逐项）",
            "sign_kinds": ["road_sign", "door_address", "shop_sign", "org_plaque"],
            "minor_inference": "仅由身份证号生日推断 <14（无身份证号不判定）",
            "time_blur": "默认 False；开启后仅模糊「完整日期+时刻」的时刻，日期保留",
            "combination_risk": "≥2 类准标识符提示 level/categories，只提示不自动改",
            "known_limits": [
                "聊天逐项依赖 OCR 可读文字+微信式左右气泡布局（非聊天截图/无 OCR 不承诺）",
                "招牌扫描对近白纸面/无文字场景白纸门控跳过（宁可不报，不承诺）",
                "未成年人推断只认身份证号生日，不估计人脸年龄",
                "时间模糊默认关闭，需调用方显式开启",
                "组合风险只提示，不自动整图涂黑",
            ],
        },
        # P1-B 语义与已知限制（只做软记录）
        "p1b_features": {
            "office_media": {
                "redact_images_default": True,
                "confirmation_now_for": "隐藏对象（图表/文本框/OLE 等）",
                "strict_option": "strict_office_media",
                "allow_option": "allow_unprocessed_media",
            },
            "delimited": {"formats": ["csv", "tsv"],
                          "column_level": True,
                          "multilevel_header": True,
                          "export_keeps_delim": True},
            "pdf_embedded_image": {"min_area_ratio": 0.05,
                                   "text_layer_pages_only": True,
                                   "reparse_black_threshold": 0.95},
            "known_limits": [
                "Office 内嵌图里的纯文字（无 face/id/plate/qr/barcode/chat/sign 目标）不自动处理（P1.5）",
                "strict_office_media 会改变文档外观（无命中图置白/剥离隐藏对象）",
                "PDF 内嵌图只处理文本层页且图片面积≥5% 页面；异常格式降级为跳过",
            ],
        },
        # P1.5 语义与已知限制（只做软记录）
        "p15_features": {
            "office_text_ocr": {
                "switch": "YINZHAN_OFFICE_TEXT_OCR",
                "stats_keys": ["text_hits", "text_images_redacted",
                               "text_skipped", "text_verify_hits"],
                "budget_env": ["YINZHAN_OFFICE_TEXT_MAX_IMAGES",
                               "YINZHAN_OFFICE_TEXT_MAX_SECONDS"],
            },
            "pdf_embedded_text_ocr": {
                "switch": "YINZHAN_PDF_EMBEDDED_TEXT_OCR",
                "note_format": "engine=embedded_image;text=N;area=",
            },
            "id_doc_types": ["work_permit", "residence_permit", "hk_mo_permit",
                             "tw_permit", "foreign_pr", "bank_card"],
            "csv_encoding_preserve": ["encoding", "encoding_output", "bom",
                                      "newline", "encoding_source"],
            "passport_rule": "legal-cn cn.passport_no（near 护照；PASSPORT；surrogate）",
            "known_limits": [
                "Office 内嵌图文字 OCR 有预算（默认 30 张/60s），超预算保留原图+warning",
                "PDF 内嵌图文字检测仅文本层页且面积≥5%；开关可关",
                "证件类型扩展只按 OCR 锚点判型；银行卡不做整页扩张",
                "CSV/TSV 未知编码回退 UTF-8 并 warning（不静默）",
            ],
        },
        # P0 修复语义（只做软记录）
        "p0_fixes": {
            "ply_binary_self_check": {
                "formats": ["ascii", "binary_little_endian",
                            "binary_big_endian"],
                "verify": "header/element 长度、数据段长度、顶点-center 平移、面逐字节",
                "on_fail": "ExportError + 删除成品（不再复制源文件）",
            },
            "tiff_orientation": {
                "pillow_path": "parse/render/export 转正，KEEP_TAGS 不含 274",
                "rasterio_path": "不转正像素 + warning（已知限制），成品无 274",
            },
            "known_limits": [
                "PLY ASCII 行式数据段长度可变化（顶点/面仍完整重读）",
                "rasterio 大图路径不转正 Orientation 像素（预览/OCR 按原始朝向）",
            ],
        },
        "pdf_text_layer_pages_skipped_ocr": {
            "min_text_chars": int(m.group(1)) if m else None,
            "ocr_only_when_text_short": "MIN_TEXT_CHARS" in pdf_src,
        },
        "office_embedded_media": {
            "docx_has_warning": "图片" in docx_src,
            "xlsx_has_warning": "图片" in xlsx_src,
            "warning_lines": embedded,
        },
        "no_ocr_flag_wired": bool(re.search(r"args\.no_ocr", app_src)),
        "export_requires_entities": "没有需要隐去的内容" in jobs_src,
        "mcp_saves_mapping": "save_mapping" in mcp_src,
        "verify_has_visual_hook": "visual" in read(
            SERVER / "yinzhan" / "pipeline.py").split("def verify")[-1],
        # 兼容旧基线字段（v1 早期采集）：verify 是否只查已识别文字
        "verify_only_checks_identified_text": "visual_marks" not in read(
            SERVER / "yinzhan" / "pipeline.py").split("def verify")[-1],
        # TIF 中性化口径（D3 已批准）：真实世界坐标 0 即可，LOCAL_CS 结构允许
        "tiff_neutralization": {
            "mode": "LOCAL_CS local arbitrary CRS（D3 批准，可 GIS 打开）"
                    if tiff_local_mode else "strip / 其他模式",
            "physical_local_geo_structure_allowed": True,
            "red_line": "无真实世界坐标：ModelPixelScale/ModelTiepoint/EPSG/"
                        "GDAL_METADATA/RPC/ICC/EXIF/GPS 残留即 FAIL；"
                        "34264/34735/34737 本地结构不算残留",
        },
    }


# ════════════════════════ 基线汇总 ════════════════════════
def build_required(samples: dict) -> dict:
    """从基线生成"必须至少命中"的最低要求；OCR 类样例允许 ±1 抖动。"""
    req = {}
    for name, r in samples["files"].items():
        if not r.get("ok"):
            req[name] = {"fmt": r.get("fmt"), "required_types": {}, "tolerance": 0}
            continue
        tol = 1 if r.get("fmt") in ("image", "tiff") else 0
        must = {label: max(1, count - tol) for label, count in r["types"].items()}
        req[name] = {"fmt": r.get("fmt"), "required_types": must,
                     "baseline_types": r["types"], "tolerance": tol,
                     "baseline_entities": r.get("entities"),
                     "baseline_pages": r.get("pages"),
                     "baseline_blocks": r.get("blocks"),
                     "sha256": r.get("sha256"), "bytes": r.get("bytes")}
    return req


def capture_all(with_http: bool = True) -> dict:
    """完整采集一次基线。"""
    base: dict = {"schema": "sanitized-data-baseline/v1"}
    base["meta"] = capture_meta()
    e2e = capture_e2e()
    base["e2e"] = e2e
    samples, _runtime = capture_samples()
    base["samples"] = samples
    base["required"] = {"samples": build_required(samples)}
    base["rules"] = capture_rules()
    base["registry"] = capture_registry()
    base["security"] = capture_security_scan()
    if with_http:
        http = capture_http()
        base["http"] = http
        base["health"] = {
            "status": (http.get("health") or {}).get("status"),
            "net": (http.get("health") or {}).get("net"),
            "ok": (http.get("health") or {}).get("ok"),
            "version": (http.get("health") or {}).get("version"),
            "engine": {
                "ocr": (http.get("health") or {}).get("ocr"),
                "parsers": (http.get("health") or {}).get("parsers"),
                "detectors": (http.get("health") or {}).get("detectors"),
                "packs": (http.get("health") or {}).get("packs"),
            },
            "audit": (http.get("health") or {}).get("audit"),
            "runtime": http.get("runtime") or {},
        }
        base["perf_soft"] = {
            "e2e_elapsed_s": e2e.get("elapsed_s"),
            "sample_parse_ms": {n: r.get("parse_ms")
                                for n, r in samples["files"].items()},
            "sample_detect_ms": {n: r.get("detect_ms")
                                 for n, r in samples["files"].items()},
            "alert_ratio": 1.5,
        }
        base["known_limits"] = capture_known_limits()
        base["rehydrate_soft"] = check_rehydrate(
            ((http.get("main_path") or {}).get("job_create") or {}).get("job_id") or "")
    return base


# ════════════════════════ 基线写盘 ════════════════════════
def write_baseline(base: dict) -> tuple[Path, Path]:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    base["captured_at"] = base.get("captured_at") or now_iso()
    BASELINE_JSON.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n",
                             "utf-8")
    write_snapshot(base, SNAPSHOT_MD)
    return BASELINE_JSON, SNAPSHOT_MD


def load_baseline() -> dict:
    if not BASELINE_JSON.is_file():
        raise SystemExit(f"基线不存在：{BASELINE_JSON}\n"
                         f"先运行：python -X utf8 app/tests/regression.py --update-baseline")
    return json.loads(BASELINE_JSON.read_text("utf-8"))


def write_snapshot(base: dict, path: Path) -> None:
    e2e = base["e2e"]
    samples = base["samples"]
    rules = base["rules"]
    reg = base["registry"]
    http = base.get("http") or {}
    sec = base["security"]
    lines: list[str] = []
    A = lines.append
    A("# 数据隐站 · 基线快照（BASELINE SNAPSHOT）")
    A("")
    A(f"- 冻结时间：{base['meta']['captured_at']}")
    A(f"- git HEAD：`{base['meta']['git_head_short']}`"
      f"（{base['meta']['git_head']}）  分支 `{base['meta']['git_branch']}`")
    A(f"- 引擎源码改动：{'无（干净）' if base['meta']['engine_clean'] else '有未提交改动，基线需复核'}")
    A(f"- Python：{base['meta']['python']}  · 平台：{base['meta']['platform']}")
    A(f"- 无 GDAL：{base['meta']['no_gdal']}  · 无 trimesh：{base['meta']['no_trimesh']}")
    A("")
    A("> **本文件是冻结基线的可读摘要；机器可读版本见 [baseline.json](baseline.json)。**")
    A("> 判定原则：**新增识别/新增规则导致多识别不算回归**；"
      "原有必须命中的变少、导出失败、残留>0、元数据回来了、服务外呼，一律算回归。")
    A("")
    A("## 0. 如何复跑")
    A("")
    A("```powershell")
    A("python -X utf8 app/tests/regression.py")
    A("python -X utf8 app/tests/regression.py --update-baseline   # 仅在确认是改进后重录")
    A("```")
    A("")
    A("## 1. 硬断言（HARD，红了就是回归，必须修）")
    A("")
    A("| # | 硬断言 | 基线事实 |")
    A("|---|---|---|")
    A(f"| H1 | 基线样例全部解析成功 | "
      f"{samples['summary']['files_ok']}/{samples['summary']['files_total']} |")
    A(f"| H2 | 两种导出全部成功、residual=0、成品存在非空、元数据已清 | "
      f"exports_ok={samples['summary']['exports_ok']}/"
      f"{samples['summary']['files_total'] * 2}，"
      f"residual_total={samples['summary']['residual_total']} |")
    A(f"| H3 | e2e 退出码 0 | exit={e2e['exit_code']}，{e2e['elapsed_s']}s |")
    A(f"| H4 | 规则包完整、无 load_errors、id 全局唯一、必填字段/scope/策略合法 | "
      f"packs={len(rules['packs'])}，defined={rules['defined_rules_total']}，"
      f"load_errors={len(rules['load_errors'])}，violations={len(rules['violations'])} |")
    A(f"| H5 | 解析器/检测器全部注册成功 | "
      f"parsers={','.join(reg['parsers'])}；detectors={','.join(reg['detectors'])} |")
    A(f"| H6 | 服务只监听 127.0.0.1 | runtime.host="
      f"{(http.get('runtime') or {}).get('host')}；代码 HOST={sec.get('listen_host_config')} |")
    A(f"| H7 | health.net == blocked | {(base.get('health') or {}).get('net')} |")
    A(f"| H8 | 静态站点 4 页面 + 3 图标 200 且 MIME 正确 | "
      f"{sum(1 for v in (http.get('static') or {}).values() if v['status'] == 200 and v['mime_ok'])}/7 |")
    A(f"| H9 | 真实 HTTP 主路径（上传→任务→ready→双导出→下载）通过 | "
      f"{(http.get('main_path') or {}).get('ok')} |")
    A(f"| H10 | 引擎无出站网络（源码静态扫描） | "
      f"outbound_hits={len(sec['outbound_hits'])}，"
      f"url_literals={len(sec['external_url_literals'])}（调用上下文 "
      f"{len(sec['external_urls_called'])}） |")
    A(f"| H11 | 审计只记类型数量不记原值 | insert 列="
      f"{','.join(sec['audit_insert_columns'])} |")
    A("| H12 | Office/图片/TIF/PDF 元数据清理 | 见 §3 元数据矩阵 |")
    A("| H13 | 映射库（AI 导出）可解密还原 | 见 §5.3 |")
    A("")
    A("## 2. 软参考（SOFT，只记录/报警，不算回归）")
    A("")
    A("| # | 软参考项 | 基线值 | 说明 |")
    A("|---|---|---|---|")
    A(f"| S1 | 性能：e2e 总耗时 / OCR 页耗时 | e2e {e2e['elapsed_s']}s | "
      f"超过基线 50% 报警（red-lines F12）|")
    A("| S2 | 已知边界现状 | 见 §6 | 后续 P0 会改，**不得断言永远如此** |")
    A(f"| S3 | 新增命中 | 基线实体总数 "
      f"{sum(r.get('entities', 0) for r in samples['files'].values())} | "
      f"新增类型/数量只提示，不判失败 |")
    A("")
    A("## 3. e2e / 样例基线（HARD 依据）")
    A("")
    A("| 样例 | fmt | 页 | 块 | 实体 | 换名 residual/size | 涂黑 residual/size |")
    A("|---|---|---|---|---|---|---|")
    for name, r in samples["files"].items():
        ei = r.get("exports", {}).get("ai", {})
        eh = r.get("exports", {}).get("human", {})
        A(f"| {name} | {r.get('fmt')} | {r.get('pages')} | {r.get('blocks')} | "
          f"{r.get('entities')} | {ei.get('residual')} / {ei.get('size')} | "
          f"{eh.get('residual')} / {eh.get('size')} |")
    A("")
    A("### 3.1 必须至少命中的实体（HARD 下限；新增类型/数量不算回归）")
    A("")
    A("| 样例 | 最低类型与数量 | OCR 容差 | 样例 sha256（漂移检测） |")
    A("|---|---|---|---|")
    for name, req in base["required"]["samples"].items():
        must = "、".join(f"{k}×{v}" for k, v in req["required_types"].items())
        sha = (req.get("sha256") or "")[:16]
        A(f"| {name} | {must} | ±{req['tolerance']} | `{sha}` |")
    A("")
    A("### 3.2 元数据清理矩阵（HARD）")
    A("")
    A("| 样例 | 源元数据（证明有对象） | 导出后（身份字段清零） |")
    A("|---|---|---|")
    for name, r in samples["files"].items():
        fmt = r.get("fmt")
        sm = r.get("source_meta") or {}
        ai = r.get("exports", {}).get("ai", {}).get("meta_check", {}) or {}
        if fmt == "pdf":
            src = f"metadata={list((sm.get('nonempty') or {}).keys())}，xmp={sm.get('xmp_len')}"
            out = ai.get("out") or {}
            A(f"| {name} | {src} | metadata={list((out.get('nonempty') or {}).keys())}，"
              f"xmp={out.get('xmp_len')}，身份字段清空={ai.get('identity_cleared')} |")
        elif fmt == "docx":
            out = ai.get("out") or {}
            A(f"| {name} | fields={ {k: v for k, v in (sm.get('fields') or {}).items() if v} } | "
              f"fields={ {k: v for k, v in (out.get('fields') or {}).items() if v} }，"
              f"身份字段清空={ai.get('identity_cleared')}，"
              f"缩略图移除={ai.get('thumbnail_removed')}，"
              f"源值残留={out.get('forbidden_hits')} |")
        elif fmt == "xlsx":
            out = ai.get("out") or {}
            A(f"| {name} | props={list((sm.get('nonempty_fields') or {}).keys())} | "
              f"props={list((out.get('nonempty_fields') or {}).keys())}，"
              f"身份字段清空={ai.get('identity_cleared')}，"
              f"源值残留={out.get('forbidden_hits')} |")
        elif fmt == "image":
            out = ai.get("out") or {}
            A(f"| {name} | info={sm.get('info_keys')}，exif={sm.get('exif_entries')} | "
              f"info={out.get('info_keys')}，exif={out.get('exif_entries')}，"
              f"清空={ai.get('identity_cleared')} |")
        elif fmt == "tiff":
            out = ai.get("out") or {}
            A(f"| {name} | geo={sm.get('geo_tags')} | "
              f"真实地理参考残留={out.get('real_geo_residual')}，"
              f"local_crs={out.get('local_crs')}，"
              f"raw={out.get('raw_geo_tags')}，sidecars={ai.get('sidecars')}，"
              f"清空={ai.get('identity_cleared')} |")
    A("")
    A("> 说明：DOCX/XLSX 的部分时间戳（created/modified）由 python-docx/openpyxl "
      "重保存时保留，属现状软记录；身份字段（作者/公司/标题等）必须清零，源值不得残留。")
    A("")
    A("### 3.3 本轮新增实体（人工确认，非误报）")
    A("")
    A("- `对账单.xlsx`：13 → 19 实体，+6 均为预期新增 —— **table-cn 列级 2**"
      "（客户名称列补 1 个人名、账号列整列覆盖 +1 个「表格账号列」）"
      " + **pipl numeric_cell 4**（纯数值金额列写回 4 个「金额（数值）」AMOUNT）。")
    A("- 其余 4 个样例实体类型/数量与上一基线完全一致（无减少、无新增）。")
    A("- 定性：这是新规则包的**预期覆盖增强**，不是误报；回归仍按"
      "「原有必须命中的不变少」判定，新增实体只做软提示。")
    A("")
    A("## 4. 规则包基线（HARD）")
    A("")
    A("| 包 | 名称 | 版本 | include | 自有规则 | 含 include 总数 |")
    A("|---|---|---|---|---|---|")
    for pid, p in rules["packs"].items():
        A(f"| {pid} | {p['name']} | {p['version']} | "
          f"{','.join(p['include']) or '-'} | {p['own_count']} | {p['total_count']} |")
    A("")
    A(f"合计自有规则 **{rules['defined_rules_total']}** 条；"
      f"load_errors={len(rules['load_errors'])}；violations={len(rules['violations'])}。")
    A("")
    A("按格式编译条数：" + "、".join(f"{k}={v}" for k, v in rules["compiled_by_fmt"].items()))
    A("")
    A("各包规则 id 清单见 `baseline.json` → `rules.packs.*.rule_ids`。"
      "回归要求：包 id 不缺、每包自有规则数不减少、编译条数不减少（新增不算回归）。")
    A("")
    A("## 5. 服务与安全基线（HARD）")
    A("")
    A(f"- 注册：parsers={reg['parsers']}；detectors={reg['detectors']}；"
      f"replacers={reg['replacers']}")
    A(f"- OCR：available={reg['ocr']['available']}，backend={reg['ocr']['backend']}，"
      f"error={reg['ocr']['error']}")
    A(f"- `/api/health`：status={(base.get('health') or {}).get('status')}，"
      f"net=**{(base.get('health') or {}).get('net')}**，"
      f"version={(base.get('health') or {}).get('version')}，"
      f"ocr={((base.get('health') or {}).get('engine') or {}).get('ocr')}")
    A(f"- runtime.json：host={(http.get('runtime') or {}).get('host')}，"
      f"port={(http.get('runtime') or {}).get('port')}；netstat 监听行="
      f"{http.get('netstat')}")
    A(f"- 外呼静态扫描：hits={sec['outbound_hits']}，URL 字面量上下文 "
      f"{[(u['url'], u['context']) for u in sec['external_url_literals']]}；"
      f"本地浏览器拉起={sec.get('browser_launch')}")
    A(f"- 审计 INSERT 列：{sec['audit_insert_columns']}；疑似原值列={sec['audit_value_like_columns']}")
    A(f"- 依赖：requirements={sec.get('requirements_packages')}；"
      f"网络客户端依赖={sec.get('network_deps') or '无'}")
    A(f"- 可选重依赖实测：{sec.get('optional_deps')}（TIF P0 可能引入 rasterio/pyproj，"
      f"只记录不判回归）")
    A("")
    A("### 5.1 静态站点冒烟")
    A("")
    A("| 路径 | 状态 | Content-Type | 期望 MIME |")
    A("|---|---|---|---|")
    for route_path, v in (http.get("static") or {}).items():
        expect = v.get("expect_ctype")
        expect_s = "/".join(expect) if isinstance(expect, list) else str(expect)
        A(f"| `{route_path}` | {v['status']} | {v['ctype']} | {expect_s} |")
    A("")
    A("### 5.2 真实 HTTP 主路径（上传 → 任务 → ready → 双导出 → 下载）")
    A("")
    mp = http.get("main_path") or {}
    A(f"- 样例：{mp.get('sample')}（{mp.get('source_bytes')} bytes）")
    A(f"- 上传：status={(mp.get('upload') or {}).get('status')}；"
      f"任务：{(mp.get('job_create') or {}).get('job_id')}；"
      f"stage={(mp.get('job_snapshot') or {}).get('stage')}")
    A(f"- 实体类型：{(mp.get('job_entities') or {}).get('types')}")
    A(f"- 预览：{mp.get('preview')}；渲染：{mp.get('render')}")
    for mode in ("ai", "human"):
        ex = (mp.get("exports") or {}).get(mode) or {}
        dl = ex.get("download") or {}
        A(f"- {mode}：status={ex.get('status')}，bytes={ex.get('bytes')}，"
          f"verify={ex.get('verify')}，下载 status={dl.get('status')}，"
          f"magic={dl.get('magic')}，非空={dl.get('nonempty')}")
    A(f"- 审计：records={(mp.get('audit') or {}).get('records')}，"
      f"verify={mp.get('audit_verify')}")
    A(f"- 框选/逐条确认：mark={mp.get('mark')}，accept-all={mp.get('accept_all')}")
    A(f"- **主路径结论：{'PASS' if mp.get('ok') else 'FAIL'}**")
    A("")
    A("### 5.3 映射库与还原（HARD）")
    A("")
    A(f"- {base.get('rehydrate_soft')}")
    A("")
    A("## 6. 已知边界快照（SOFT，后续 P0 会修改，不要当硬断言）")
    A("")
    kl = base.get("known_limits") or {}
    A(f"- PDF 文本层页不 OCR：只对文本 < `{kl.get('pdf_text_layer_pages_skipped_ocr', {}).get('min_text_chars')}` "
      f"字的页走 OCR 分支")
    A(f"- Office 内嵌图片：仅告警、不自动脱敏（{kl.get('office_embedded_media')}）")
    A(f"- `--no-ocr` 启动参数是否接线：{kl.get('no_ocr_flag_wired')}（False=现状未接线）")
    A(f"- 无实体时不可导出（仅清理场景受限）：{kl.get('export_requires_entities')}")
    A(f"- MCP 路径是否保存映射（S6）：{kl.get('mcp_saves_mapping')}（False=现状缺口，后续修复不算回归）")
    A(f"- PDF 隐藏数据（附件/批注/表单/JS）：默认剥离（meta 键 "
      f"{kl.get('pdf_hidden_strip', {}).get('meta_keys')}），"
      f"verify 拦截未清理={kl.get('pdf_hidden_strip', {}).get('verify_intercepts')}，"
      f"保留开关={kl.get('pdf_hidden_strip', {}).get('keep_option')}")
    A(f"- 图片 EXIF Orientation 转正：{kl.get('image_exif_orientation')}")
    A(f"- 前端整图涂黑（rect=[0,0,1,1] → black 实体）："
      f"{kl.get('frontend_full_image_black')}")
    idm = kl.get("id_card_modes") or {}
    A(f"- **身份证 A 方案**：默认 `{idm.get('default')}`；"
      f"正面字段={idm.get('front_field_kinds')}，反面字段={idm.get('back_field_kinds')}；"
      f"可选 `{idm.get('optional')}` 整卡涂黑；"
      f"jobs id_card_mode={idm.get('jobs_impl')}，"
      f"证件内 OCR 文字实体让位={idm.get('ocr_text_entities_dropped_in_card')}，"
      f"前端选择器={idm.get('frontend_selector')}")
    A("- **边界（产品语义）**：身份证反面在 `fields` 档遮签发机关+有效期限两行，"
      "国徽/标题/其余卡面保留；两面字段都读不出时整卡回退并提示切「整卡涂黑」。")
    p1a = kl.get("p1a_features") or {}
    A(f"- **P1-A 第一波**：聊天逐项 kinds={p1a.get('chat_kinds')}"
      f"（{p1a.get('chat_only_image_fmt')}）；招牌 kinds={p1a.get('sign_kinds')}；"
      f"未成年人={p1a.get('minor_inference')}；"
      f"时间模糊={p1a.get('time_blur')}；组合风险={p1a.get('combination_risk')}")
    for _lim in (p1a.get("known_limits") or []):
        A(f"  - 已知限制：{_lim}")
    p1b = kl.get("p1b_features") or {}
    om = p1b.get("office_media") or {}
    A(f"- **P1-B 第一波**：Office 内嵌图片默认自动脱敏={om.get('redact_images_default')}"
      f"（需确认的是{om.get('confirmation_now_for')}；strict={om.get('strict_option')}）；"
      f"CSV/TSV={p1b.get('delimited')}；PDF 内嵌图={p1b.get('pdf_embedded_image')}")
    for _lim in (p1b.get("known_limits") or []):
        A(f"  - 已知限制：{_lim}")
    p15 = kl.get("p15_features") or {}
    A(f"- **P1.5**：Office 内嵌图文字 OCR={p15.get('office_text_ocr')}；"
      f"PDF 文字内嵌图={p15.get('pdf_embedded_text_ocr')}；"
      f"证件类型={p15.get('id_doc_types')}；"
      f"CSV 编码保留={p15.get('csv_encoding_preserve')}；"
      f"护照规则={p15.get('passport_rule')}")
    for _lim in (p15.get("known_limits") or []):
        A(f"  - 已知限制：{_lim}")
    p0 = kl.get("p0_fixes") or {}
    A(f"- **P0 修复**：PLY 二进制结构自检={p0.get('ply_binary_self_check')}；"
      f"TIF Orientation={p0.get('tiff_orientation')}")
    for _lim in (p0.get("known_limits") or []):
        A(f"  - 已知限制：{_lim}")
    gaps = kl.get("known_gaps_not_fixed") or {}
    if gaps:
        A("- **已知不修（本轮确认，不当作回归）**：")
        for k, v in gaps.items():
            A(f"  - `{k}`：{v}")
    A("- **正射 TIF 中性化（D3 已批准）**：成品物理上仍保留 LOCAL_CS 本地任意 CRS 的 "
      "GeoTIFF 结构标签（34264/34735/34737，GIS 可打开）。红线是"
      "**没有真实世界坐标**：源 ModelPixelScale / ModelTiepoint / EPSG / "
      "GDAL_METADATA / RPC / ICC / EXIF / GPS 一旦残留即 FAIL；"
      "LOCAL_CS 结构本身不算残留（详见 `docs/impl/tiff.md §10`）。")
    A("")
    A("## 7. 采集环境说明")
    A("")
    A("- 采集使用临时数据目录（YINZHAN_DATA_DIR=临时目录），仓库 `data/` 不被基线污染。")
    A("- e2e 采集命令同仓库约定；结构化明细由同一引擎在进程内按 e2e 相同步骤复跑得到。")
    A("- 语义：e2e 只做 detect/decide/export/verify；HTTP 主路径会额外走 jobs 的表单人名、"
      "同值扩散、签署区/视觉实体补充，所以两者实体数可能不同，均属正常。")
    A("")
    reason = (base.get("meta") or {}).get("update_reason")
    if reason:
        A("## 8. 本次基线更新理由（人工填写）")
        A("")
        A(f"> {reason}")
        A("")
    history = (base.get("meta") or {}).get("update_history") or []
    if history:
        A("## 9. 基线更新记录（最近 10 次）")
        A("")
        A("| 时间 | HEAD | engine_clean | 理由 |")
        A("|---|---|---|---|")
        for item in history[-10:]:
            A(f"| {item.get('at')} | {item.get('head')} | "
              f"{item.get('engine_clean')} | {item.get('reason')} |")
        A("")
    path.write_text("\n".join(lines) + "\n", "utf-8")
