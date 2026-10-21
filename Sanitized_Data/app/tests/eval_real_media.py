# -*- coding: utf-8 -*-
"""真实媒体评测脚本（用户测试集 + 公开照片 + 合成夹具）。

用途：对给定目录内的图片 / PDF / TIFF / Office 文件走**真实引擎**
（parse → detect → decide → 视觉标记），输出每文件的实体/视觉命中/耗时/告警，
生成 JSON + CSV + Markdown 报告到临时目录；**不做导出**（避免在磁盘上产生含
个人信息的成品），也**不复制输入文件**。

安全约定：
- 输入目录只读；报告写到 ``%TEMP%``（可用 ``--out`` 指定）；
- 报告**不记录实体原值、不记录 OCR 文本**，只记录类型/标签/长度/决策是否脱敏；
- 不联网（公开素材由 ``fixtures/pipl_public/MANIFEST.md`` 记录、离线下载后使用）；
- 用户测试集仅在本地跑，不提交、不复制进仓库。

用法（Windows 控制台要 ``-X utf8``）：

    # 默认用户测试集（不存在时退回 fixtures/pipl_public）
    python -X utf8 app/tests/eval_real_media.py

    # 指定目录（可多次；支持混合目录）
    python -X utf8 app/tests/eval_real_media.py ^
        --dir "C:\\Users\\Dante\\Desktop\\测试集" ^
        --dir "%TEMP%\\pipl_public_testset" ^
        --dir app/tests/fixtures/pipl_public

常用参数：
    --no-ocr          跳过 OCR（快；图片的人脸/证件/车牌/二维码仍会扫描）
    --strictness      loose|standard|strict（默认 standard，与产品一致）
    --out             报告输出目录（默认 %TEMP%\\yz_eval_<时间戳>）
    --limit N         每个目录最多 N 个文件（调试用）
    --include EXT     额外扩展名（逗号分隔）
    --json-only       只写 JSON（不写 CSV/Markdown）
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# ────────────────────────────── 路径与环境（必须在 import yinzhan 前） ──────────────────────────────

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
FIXTURES = TESTS / "fixtures" / "pipl_public"
DEFAULT_USER_DIR = Path(r"C:\Users\Dante\Desktop\测试集")
DEFAULT_PUBLIC_DIR = Path(os.environ.get("TEMP", ".")) / "pipl_public_testset"

ENGINE_FMTS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp",
    ".tif", ".tiff", ".docx", ".xlsx",
    ".txt", ".csv",
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="真实媒体评测：parse → detect → decide → 视觉标记（只读、不导出）")
    ap.add_argument("--dir", action="append", default=None,
                    help="要评测的目录（可多次）；默认用户测试集")
    ap.add_argument("--out", default="", help="报告输出目录（默认 %%TEMP%%\\yz_eval_<ts>）")
    ap.add_argument("--no-ocr", action="store_true", help="跳过 OCR")
    ap.add_argument("--strictness", default="standard",
                    choices=("loose", "standard", "strict"))
    ap.add_argument("--limit", type=int, default=0, help="每个目录最多文件数（调试）")
    ap.add_argument("--include", default="", help="额外扩展名，逗号分隔，如 .heic,.gif")
    ap.add_argument("--json-only", action="store_true", help="只写 JSON 报告")
    ap.add_argument("--quiet", action="store_true", help="只打印汇总，不逐文件")
    return ap.parse_args(argv)


def _prepare_env(args: argparse.Namespace) -> tuple[list[Path], Path, Path]:
    """设置隔离的数据目录（不写仓库 data/），返回 (输入目录, 报告目录, 临时 data 根)。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else Path(
        os.environ.get("TEMP", ".")) / f"yz_eval_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    data_root = out_dir / "_engine_data"
    os.environ["YINZHAN_DATA_DIR"] = str(data_root)
    os.environ["YINZHAN_OUTPUT_DIR"] = str(data_root / "out")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    dirs: list[Path] = []
    for raw in (args.dir or []):
        p = Path(raw).expanduser()
        if p.is_dir():
            dirs.append(p)
        else:
            print(f"[WARN] 目录不存在，跳过：{p}")
    if not dirs:
        if DEFAULT_USER_DIR.is_dir():
            dirs = [DEFAULT_USER_DIR]
        elif FIXTURES.is_dir():
            dirs = [FIXTURES]
        else:
            dirs = [DEFAULT_PUBLIC_DIR]
    extra = {e.strip().lower() for e in args.include.split(",") if e.strip()}
    return dirs, out_dir, data_root


def _load_engine():
    """import 引擎（在环境变量设置之后）。"""
    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))
    import importlib

    mods = {}
    for name in ("pipeline", "registry", "jobs", "udm", "ocr", "mediascan"):
        mods[name] = importlib.import_module(f"yinzhan.{name}")
    # 触发插件/扫描器注册
    mods["registry"].load_plugins()
    return mods


# ────────────────────────────── 文件枚举 ──────────────────────────────

def _iter_files(dirs: list[Path], extra_exts: set[str], limit: int):
    seen = set()
    for root in dirs:
        count = 0
        try:
            files = sorted(p for p in root.rglob("*") if p.is_file())
        except OSError:
            continue
        for path in files:
            suffix = path.suffix.lower()
            if suffix not in ENGINE_FMTS and suffix not in extra_exts:
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            yield root, path
            count += 1
            if limit and count >= limit:
                break


# ────────────────────────────── 小工具（报告不写原值/文本） ──────────────────────────────

def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iou(a, b) -> float:
    try:
        x0, y0 = max(a[0], b[0]), max(a[1], b[1])
        x1, y1 = min(a[2], b[2]), min(a[3], b[3])
        if x1 <= x0 or y1 <= y0:
            return 0.0
        inter = (x1 - x0) * (y1 - y0)
        area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _center(rect) -> tuple[float, float]:
    return ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)


def _inside(rect, box) -> bool:
    cx, cy = _center(rect)
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _rect_area(rect) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _norm_text(text: str) -> str:
    try:
        return (text or "").replace(" ", "").replace("\u3000", "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _text_blocks(doc):
    out = []
    for page in doc.pages:
        for block in page.blocks:
            if block.kind == "text" and block.text and block.bbox:
                out.append((page.index, block.text, tuple(block.bbox)))
    return out


def _sanitize_warnings(warnings: list[str], entities, doc) -> list[str]:
    """warning 里若混入实体原值/OCR 文本，替换为 ***（评测报告不落原文）。"""
    secrets: list[str] = []
    for entity in entities or []:
        value = (getattr(entity, "value", "") or "").strip()
        if len(value) >= 2:
            secrets.append(value)
    for _page, text, _bbox in _text_blocks(doc):
        compact = _norm_text(text)
        if 2 <= len(compact) <= 40:
            secrets.append(compact)
    secrets = sorted(set(secrets), key=len, reverse=True)[:200]
    out = []
    for warn in warnings or []:
        text = str(warn)
        for secret in secrets:
            if secret and secret in text:
                text = text.replace(secret, "***")
        out.append(text)
    return out


# ────────────────────────────── 视觉/码类与专项判定 ──────────────────────────────

_VISUAL_KINDS = (
    "face", "id_document", "plate", "road_sign", "door_address",
    "qrcode", "barcode", "seal", "signature",
)
_ID_ANCHORS_STRONG = ("居民身份证", "公民身份号码", "中华人民共和国护照", "护照号码",
                      "机动车驾驶证", "社会保障卡", "营业执照")
_ID_ANCHORS_WEAK = ("签发机关", "有效期限", "住址", "出生", "民族", "性别",
                    "准驾车型", "注册资本", "经营范围", "统一社会信用代码")
_INVOICE_KEYWORDS = ("发票", "价税合计", "发票代码", "发票号码", "校验码",
                     "购买方", "销售方", "税号", "纳税人识别号")
_PII_KEYWORDS = ("身份证", "手机号", "电话", "银行卡", "账号", "住址", "姓名",
                 "金额", "合同", "甲方", "乙方", "公司")


def _visual_stats(doc) -> dict:
    counts = {kind: 0 for kind in _VISUAL_KINDS}
    for mark in getattr(doc, "visual_marks", None) or []:
        kind = str(getattr(mark, "kind", "") or "")
        if kind in counts:
            counts[kind] += 1
        else:
            counts.setdefault(kind, 0)
            counts[kind] += 1
    counts = {k: v for k, v in counts.items() if v}
    return counts


def _entity_stats(entities) -> dict:
    by_type: dict[str, int] = {}
    by_label: dict[str, int] = {}
    status: dict[str, int] = {}
    decisions: dict[str, int] = {}
    black_by_type: dict[str, int] = {}
    for entity in entities or []:
        etype = str(getattr(entity, "etype", "") or "?")
        by_type[etype] = by_type.get(etype, 0) + 1
        label = str(getattr(entity, "label", "") or "?")
        by_label[label] = by_label.get(label, 0) + 1
        text_status = str(getattr(entity, "status", "accepted") or "accepted")
        status[text_status] = status.get(text_status, 0) + 1
        decision = getattr(entity, "decision", None)
        strategy = str(getattr(decision, "strategy", "") or "none") if decision else "none"
        black = bool(getattr(decision, "black", False)) if decision else False
        if black and strategy not in ("black", "drop"):
            strategy = "black"
        decisions[strategy] = decisions.get(strategy, 0) + 1
        if black:
            black_by_type[etype] = black_by_type.get(etype, 0) + 1
    return {"by_type": by_type, "by_label": by_label, "status": status,
            "decisions": decisions, "black_by_type": black_by_type}


def _visual_entity_stats(entities) -> dict:
    """视觉 marks 有多少落成了实体、其中多少是默认涂黑（打码可用率）。"""
    visual_types = {"FACE", "ID_DOCUMENT", "PLATE", "ROAD_SIGN", "DOOR_ADDRESS",
                    "QRCODE", "BARCODE", "SEAL", "SIGNATURE", "SIG_BLOCK"}
    total = sum(1 for e in entities if getattr(e, "etype", "") in visual_types)
    black = sum(1 for e in entities
                if getattr(e, "etype", "") in visual_types
                and getattr(getattr(e, "decision", None), "black", False))
    accepted = sum(1 for e in entities
                   if getattr(e, "etype", "") in visual_types
                   and getattr(e, "status", "accepted") != "rejected")
    return {"visual_entities": total, "visual_accepted": accepted,
            "visual_black": black}


def _text_keywords(blocks, needles) -> dict:
    """文本块命中关键词统计（只返回计数，不返回原文）。"""
    hits = 0
    blocks_hit = 0
    for _page, text, _bbox in blocks:
        compact = _norm_text(text)
        if any(needle in compact for needle in needles):
            hits += 1
            blocks_hit += 1
    return {"blocks": blocks_hit, "texts": hits}


def _id_card_assessment(doc, entities, marks, blocks, image_rgb_size) -> dict:
    """身份证照片专项：是否命中整卡、是否被 black、旋转/漏检提示（仅提示，不下结论）。"""
    marks = [m for m in marks if getattr(m, "kind", "") == "id_document"]
    id_entities = [e for e in entities if getattr(e, "etype", "") == "ID_DOCUMENT"]
    black = [e for e in id_entities
             if getattr(getattr(e, "decision", None), "black", False)]
    width, height = image_rgb_size
    page_area = float(max(1, width * height))
    best_area = 0.0
    best_text_inside = 0.0
    for mark in marks:
        rect = tuple(getattr(mark, "rect", (0, 0, 0, 0)))
        best_area = max(best_area, _rect_area(rect) / page_area)
        if blocks:
            inside = sum(1 for _p, _t, box in blocks if _inside(box, rect))
            best_text_inside = max(best_text_inside, inside / float(len(blocks)))
    strong = _text_keywords(blocks, _ID_ANCHORS_STRONG)
    weak = _text_keywords(blocks, _ID_ANCHORS_WEAK)
    vertical_blocks = sum(1 for _p, text, box in blocks
                          if (box[3] - box[1]) > 1.25 * max(1.0, box[2] - box[0])
                          and len(_norm_text(text)) >= 2)
    hints = []
    whole_card = bool(black) and (best_area >= 0.15) and (
        best_text_inside >= 0.8 or best_area >= 0.40)
    if marks and black and whole_card:
        hints.append("证件区域已被 black 覆盖（按面积/文字块包含率判定，需人工复核）")
    elif marks and black:
        hints.append("证件已检出并 black，但覆盖面积/文字包含率偏低，需人工复核是否整卡")
    elif marks and not black:
        hints.append("证件仅标记为待确认/未涂黑，需人工处理")
    elif not marks and (strong["blocks"] or vertical_blocks >= 3):
        hints.append("疑似证件但未检出 id_document（OCR 有证件锚点/竖排文字），"
                     "可能因旋转 90° 或版式异常漏检，需人工框选")
    elif not marks and not blocks:
        hints.append("OCR 0 块且未检出证件：疑似图片旋转 90°/低质量，需人工复核")
    return {
        "id_marks": len(marks),
        "id_entities": len(id_entities),
        "id_black": len(black),
        "id_area_ratio": round(best_area, 3),
        "id_text_inside_ratio": round(best_text_inside, 3),
        "whole_card_covered": whole_card,
        "anchor_strong_blocks": strong["blocks"],
        "anchor_weak_blocks": weak["blocks"],
        "vertical_text_blocks": vertical_blocks,
        "hint": "；".join(hints),
    }


def _invoice_assessment(doc, entities, marks, blocks) -> dict:
    """发票/凭证专项：二维码是否检出并涂黑、票面关键词/实体类型（不记录内容）。"""
    qr_marks = [m for m in marks if getattr(m, "kind", "") == "qrcode"]
    bar_marks = [m for m in marks if getattr(m, "kind", "") == "barcode"]
    qr_ents = [e for e in entities if getattr(e, "etype", "") == "QRCODE"]
    qr_black = [e for e in qr_ents
                if getattr(getattr(e, "decision", None), "black", False)]
    bar_ents = [e for e in entities if getattr(e, "etype", "") == "BARCODE"]
    bar_black = [e for e in bar_ents
                 if getattr(getattr(e, "decision", None), "black", False)]
    invoice_kw = _text_keywords(blocks, _INVOICE_KEYWORDS)
    pii_kw = _text_keywords(blocks, _PII_KEYWORDS)
    by_label: dict[str, int] = {}
    for entity in entities:
        label = str(getattr(entity, "label", "") or "?")
        by_label[label] = by_label.get(label, 0) + 1
    hints = []
    if invoice_kw["blocks"] and not qr_marks:
        hints.append("票面关键词命中但未检出二维码，需人工确认票面二维码是否被裁掉/模糊")
    if qr_marks and not qr_black:
        hints.append("二维码已检出但未进入 black 实体（待确认），需人工处理")
    return {
        "invoice_like": bool(invoice_kw["blocks"]),
        "invoice_keyword_blocks": invoice_kw["blocks"],
        "pii_keyword_blocks": pii_kw["blocks"],
        "qrcode_marks": len(qr_marks),
        "qrcode_entities": len(qr_ents),
        "qrcode_black": len(qr_black),
        "barcode_marks": len(bar_marks),
        "barcode_entities": len(bar_ents),
        "barcode_black": len(bar_black),
        "entity_types": by_label,
        "hint": "；".join(hints),
    }


def _exif_orientation(path: Path) -> int:
    try:
        from PIL import Image

        with Image.open(path) as image:
            exif = image.getexif() or {}
            return int(exif.get(274, 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _mark_details(marks, *, fmt: str, image_size: tuple[int, int],
                  page_sizes: list[tuple[float, float]] | None = None) -> list[dict]:
    """视觉/码类 marks 明细（不含原文）＋「整页误涂/证件误判」候选标记。

    规则：
    - invalid-large：仅检测未解码（/detect）或低置信（≤0.60）且框面积 ≥15% 页面；
    - whole-page-id：id_document 框覆盖 ≥90% 页面（可能是发票/合同被误判成证件）；
    - suspicious = 任一命中，供人工复核。
    """
    width, height = image_size
    page_sizes = page_sizes or [(float(width), float(height))]
    out: list[dict] = []
    for mark in marks or []:
        try:
            rect = tuple(float(v) for v in getattr(mark, "rect", (0, 0, 0, 0)))
            note = str(getattr(mark, "note", "") or "")
            confidence = float(getattr(mark, "confidence", 0.9) or 0.0)
            page = int(getattr(mark, "page", 0) or 0)
            pw, ph = (page_sizes[page] if 0 <= page < len(page_sizes)
                      else (float(width), float(height)))
            page_area = max(1.0, float(pw) * float(ph))
            area_ratio = round(_rect_area(rect) / page_area, 3)
            invalid_large = bool(
                ("/detect" in note or confidence <= 0.60) and area_ratio >= 0.15)
            whole_page_id = bool(
                str(getattr(mark, "kind", "")) == "id_document" and area_ratio >= 0.90)
            out.append({
                "kind": str(getattr(mark, "kind", "") or ""),
                "note": note,
                "confidence": round(confidence, 3),
                "page": page,
                "rect": [round(v, 1) for v in rect],
                "area_ratio": area_ratio,
                "invalid_large": invalid_large,
                "whole_page_id": whole_page_id,
                "suspicious": invalid_large or whole_page_id,
            })
        except Exception:  # noqa: BLE001
            continue
    return out


# ────────────────────────────── 单文件评测（复刻 jobs._run 的检测链） ──────────────────────────────

def _eval_one(path: Path, engine, ocr, strictness: str) -> dict:
    registry = engine["registry"]
    pipeline = engine["pipeline"]
    jobs = engine["jobs"]
    udm = engine["udm"]
    ParseContext = udm.ParseContext
    Decision = udm.Decision
    Span = udm.Span
    Entity = udm.Entity

    row: dict = {
        "path": str(path), "name": path.name, "fmt": "", "size": 0,
        "pages": 0, "image_size": [0, 0],
        "blocks": 0, "ocr_blocks": 0, "text_chars": 0,
        "parse_ms": 0, "rules_ms": 0, "detect_ms": 0,
        "decide_ms": 0, "visual_ms": 0, "total_ms": 0,
        "entity_total": 0, "visual_entity": 0, "visual_black": 0,
        "entities": {}, "decisions": {}, "visual_marks": {},
        "id_card": {}, "invoice": {}, "warnings": [],
        "error": "", "exif_orientation": 0,
    }
    t_all = time.perf_counter()
    try:
        row["size"] = path.stat().st_size
        parser = registry.find_parser(path)
        if parser is None:
            row["error"] = "没有匹配的解析器"
            return row
        row["fmt"] = parser.format_id
        row["exif_orientation"] = _exif_orientation(path) if parser.format_id in (
            "image", "tiff") else 0

        t0 = time.perf_counter()
        doc = parser.parse(path, ParseContext(ocr=ocr, workdir=str(path.parent)))
        row["parse_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        row["pages"] = len(doc.pages)
        if doc.pages:
            row["image_size"] = [int(doc.pages[0].width), int(doc.pages[0].height)]
        blocks = _text_blocks(doc)
        row["blocks"] = sum(len(p.blocks) for p in doc.pages)
        row["ocr_blocks"] = sum(
            1 for page in doc.pages for block in page.blocks
            if block.kind == "text" and bool((block.ref or {}).get("ocr")))
        row["text_chars"] = sum(len(_norm_text(t)) for _p, t, _b in blocks)
        warnings: list[str] = list(doc.warnings or [])

        t0 = time.perf_counter()
        rules, _pack_label, _packs = jobs._load_rules(doc.fmt)
        row["rules_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        t0 = time.perf_counter()
        det = pipeline.detect(doc, rules, strictness)
        row["detect_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        warnings.extend(det.warnings or [])
        entities = list(det.entities)

        t0 = time.perf_counter()
        try:
            form_ents = pipeline.find_form_names(doc, entities)
            form_ents += pipeline.find_party_names(doc, entities + form_ents)
            entities.extend(form_ents)
            if form_ents:
                warnings.append(f"表单/并列人名补充 {len(form_ents)} 个")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"表单人名补充跳过：{type(exc).__name__}")
        session = pipeline.Session() if hasattr(pipeline, "Session") else None
        rules_by_id = dict(det.rules)
        for entity in entities:
            if entity.id not in rules_by_id and getattr(entity, "origin", "") == "form":
                form_rule = getattr(pipeline, "_FORM_RULE", None)
                if form_rule is not None:
                    rules_by_id[entity.id] = form_rule
        pipeline.decide(entities, rules_by_id, session)
        # 金额一致性（与 jobs._run 同步；失败不影响评测）
        try:
            from yinzhan.replacers import amount_consistency

            plan = amount_consistency.build_amount_plan(entities, rules_by_id, session)
            if plan:
                amount_consistency.apply_plan(entities, plan, session)
                warnings.append(f"金额一致性缩放 {len(plan)} 处")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"金额一致性处理跳过：{type(exc).__name__}")
        try:
            propagated = pipeline.propagate_values(doc, entities)
            if propagated:
                warnings.append(f"同值扩散补了 {propagated} 处")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"同值扩散跳过：{type(exc).__name__}")
        row["decide_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        t0 = time.perf_counter()
        try:
            sig_blocks = pipeline.find_signature_blocks(doc)
        except Exception:  # noqa: BLE001
            sig_blocks = []
        for page_index, rect in sig_blocks:
            entities.append(Entity(
                id=Entity.new_id(), etype="SIG_BLOCK", label="签署区", value="",
                spans=[Span(rect=rect, page=page_index)],
                level="L3", confidence=0.95, origin="visual", status="accepted",
                decision=Decision(strategy="black", black=True, replacement=None),
            ))
        try:
            visual_ents = jobs._visual_entities(doc, sig_blocks)
            entities.extend(visual_ents)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"视觉实体转换跳过：{type(exc).__name__}")
        try:
            low_conf = jobs._low_confidence_entities(doc, entities)
            entities.extend(low_conf)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"低置信度手写补充跳过：{type(exc).__name__}")
        row["visual_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        marks = list(getattr(doc, "visual_marks", None) or [])
        entity_stats = _entity_stats(entities)
        row["entity_total"] = len(entities)
        row["entities"] = entity_stats["by_type"]
        row["decisions"] = entity_stats["decisions"]
        row["visual_entity_stats"] = _visual_entity_stats(entities)
        row["visual_entity"] = row["visual_entity_stats"]["visual_entities"]
        row["visual_black"] = row["visual_entity_stats"]["visual_black"]
        row["visual_marks"] = _visual_stats(doc)
        page_sizes = [(float(p.width), float(p.height)) for p in doc.pages]
        row["visual_mark_details"] = _mark_details(
            marks, fmt=row.get("fmt"),
            image_size=tuple(row.get("image_size") or (0, 0)),
            page_sizes=page_sizes)
        row["suspicious_marks"] = [
            m for m in row["visual_mark_details"] if m.get("suspicious")
        ]
        row["invalid_large_marks"] = [
            m for m in row["visual_mark_details"] if m.get("invalid_large")
        ]
        row["whole_page_id_marks"] = [
            m for m in row["visual_mark_details"] if m.get("whole_page_id")
        ]
        # 身份证/发票专项判定只对图像类有意义（文本/表格文件不参与）
        if row.get("fmt") in ("image", "tiff"):
            row["id_card"] = _id_card_assessment(
                doc, entities, marks, blocks, tuple(row["image_size"]))
        else:
            row["id_card"] = {}
        row["invoice"] = _invoice_assessment(doc, entities, marks, blocks)
        # 整页证件误判：票面/合同页（有发票关键词或文字块很多）被 id_document 整页覆盖
        row["id_fp_on_document"] = bool(
            row.get("whole_page_id_marks")
            and (bool(row["invoice"].get("invoice_like"))
                 or _to_int(row.get("blocks")) >= 20))
        row["warnings"] = _sanitize_warnings(warnings, entities, doc)
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        row["total_ms"] = round((time.perf_counter() - t_all) * 1000.0, 1)
    return row


# ────────────────────────────── 汇总与报告 ──────────────────────────────

def _aggregate(rows: list[dict], dirs: list[Path], engine, args) -> dict:
    fmt_counts: dict[str, int] = {}
    entity_totals: dict[str, int] = {}
    visual_totals: dict[str, int] = {}
    decision_totals: dict[str, int] = {}
    files_ok = 0
    files_error = 0
    files_zero_entities = 0
    total_ms = 0.0
    suspicious_total = 0
    whole_page_id_total = 0
    invalid_large_total = 0
    files_whole_page_id = 0
    files_id_fp = 0
    id_rows = []
    invoice_rows = []
    photo_rows = []
    for row in rows:
        if row.get("error"):
            files_error += 1
        else:
            files_ok += 1
        fmt_counts[row.get("fmt") or "?"] = fmt_counts.get(row.get("fmt") or "?", 0) + 1
        for key, value in (row.get("entities") or {}).items():
            entity_totals[key] = entity_totals.get(key, 0) + _to_int(value)
        for key, value in (row.get("visual_marks") or {}).items():
            visual_totals[key] = visual_totals.get(key, 0) + _to_int(value)
        for key, value in (row.get("decisions") or {}).items():
            decision_totals[key] = decision_totals.get(key, 0) + _to_int(value)
        total_ms += float(row.get("total_ms") or 0.0)
        suspicious_total += len(row.get("suspicious_marks") or [])
        whole_page_id_total += len(row.get("whole_page_id_marks") or [])
        invalid_large_total += len(row.get("invalid_large_marks") or [])
        if row.get("whole_page_id_marks"):
            files_whole_page_id += 1
        if row.get("id_fp_on_document"):
            files_id_fp += 1
        if not row.get("error") and _to_int(row.get("entity_total")) == 0:
            files_zero_entities += 1
        if _to_int(row.get("id_card", {}).get("id_marks")) or \
                _to_int(row.get("id_card", {}).get("anchor_strong_blocks")):
            id_rows.append(row)
        inv = row.get("invoice") or {}
        if bool(inv.get("invoice_like")) or _to_int(inv.get("qrcode_marks")):
            invoice_rows.append(row)
        if row.get("fmt") in ("image", "tiff"):
            photo_rows.append(row)
    timings = sorted(float(r.get("total_ms") or 0.0) for r in rows if not r.get("error"))
    return {
        "files": len(rows),
        "files_ok": files_ok,
        "files_error": files_error,
        "files_zero_entities": files_zero_entities,
        "formats": fmt_counts,
        "entity_totals": entity_totals,
        "visual_totals": visual_totals,
        "decision_totals": decision_totals,
        "suspicious_marks": suspicious_total,
        "whole_page_id_marks": whole_page_id_total,
        "files_with_whole_page_id": files_whole_page_id,
        "files_id_fp_on_document": files_id_fp,
        "invalid_large_marks": invalid_large_total,
        "total_ms": round(total_ms, 1),
        "per_file_ms": {
            "median": round(timings[len(timings) // 2], 1) if timings else 0,
            "max": round(timings[-1], 1) if timings else 0,
            "min": round(timings[0], 1) if timings else 0,
        },
        "id_candidates": len(id_rows),
        "invoice_candidates": len(invoice_rows),
        "image_candidates": len(photo_rows),
        "engine": {
            "ocr": not args.no_ocr,
            "strictness": args.strictness,
            "scanners": list(getattr(engine["mediascan"], "scanner_names", lambda: [])()),
            "rule_packs": [p.get("id") for p in (engine["jobs"]._load_rules(
                "pdf", None)[2] or [])],
        },
        "dirs": [str(d) for d in dirs],
    }


def _sanitize_row_for_csv(row: dict) -> dict:
    def _json(value) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:  # noqa: BLE001
            return "{}"

    return {
        "name": row.get("name", ""),
        "fmt": row.get("fmt", ""),
        "size": row.get("size", 0),
        "pages": row.get("pages", 0),
        "blocks": row.get("blocks", 0),
        "text_chars": row.get("text_chars", 0),
        "entity_total": row.get("entity_total", 0),
        "entities": _json(row.get("entities", {})),
        "decisions": _json(row.get("decisions", {})),
        "visual_marks": _json(row.get("visual_marks", {})),
        "visual_entity": row.get("visual_entity", 0),
        "visual_black": row.get("visual_black", 0),
        "suspicious_marks": len(row.get("suspicious_marks") or []),
        "whole_page_id": len(row.get("whole_page_id_marks") or []),
        "id_fp_on_document": bool(row.get("id_fp_on_document")),
        "invalid_large_marks": len(row.get("invalid_large_marks") or []),
        "mark_notes": _json([m.get("note", "") for m in
                             (row.get("visual_mark_details") or [])]),
        "id_marks": _to_int(row.get("id_card", {}).get("id_marks")),
        "id_black": _to_int(row.get("id_card", {}).get("id_black")),
        "id_whole_card": bool(row.get("id_card", {}).get("whole_card_covered")),
        "qr_marks": _to_int(row.get("invoice", {}).get("qrcode_marks")),
        "qr_black": _to_int(row.get("invoice", {}).get("qrcode_black")),
        "face_marks": _to_int(row.get("visual_marks", {}).get("face")),
        "parse_ms": row.get("parse_ms", 0),
        "detect_ms": row.get("detect_ms", 0),
        "total_ms": row.get("total_ms", 0),
        "warnings": len(row.get("warnings") or []),
        "error": row.get("error", ""),
    }


def _fmt_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))
    out = ["| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |",
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)) + " |")
    return out


def _write_reports(rows: list[dict], summary: dict, out_dir: Path,
                   args: argparse.Namespace) -> dict[str, Path]:
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo": str(REPO),
        "git_head": _git_head(),
        "engine_snapshot": summary.get("engine_snapshot") or _engine_snapshot(),
        "argv": sys.argv[1:],
        "dirs": summary.get("dirs"),
        "engine": summary.get("engine"),
        "notes": [
            "报告不包含实体原值与 OCR 文本；用户测试集只读，未复制进仓库。",
            "未做导出；评测只跑 parse→detect→decide→视觉标记。",
        ],
    }
    payload = {"meta": meta, "summary": summary, "files": rows}
    json_path = out_dir / "report.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    paths = {"json": json_path}
    if args.json_only:
        return paths
    csv_path = out_dir / "report.csv"
    fields = list(_sanitize_row_for_csv(rows[0]).keys()) if rows else [
        "name", "fmt", "entity_total", "total_ms", "error"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(_sanitize_row_for_csv(row))
    paths["csv"] = csv_path

    md = ["# 真实媒体评测报告", "",
          f"- 生成时间：{meta['generated_at']}",
          f"- 目录：{', '.join(meta.get('dirs') or [])}",
          f"- 引擎快照：HEAD `{meta.get('engine_snapshot', {}).get('git_head', '')}`"
          f" · 工作区"
          f"{'有未提交改动' if meta.get('engine_snapshot', {}).get('git_dirty') else '干净'}",
          f"- 引擎：{json.dumps(meta.get('engine'), ensure_ascii=False)}",
          f"- 文件：{summary['files']}（成功 {summary['files_ok']}，"
          f"错误 {summary['files_error']}，零实体 {summary['files_zero_entities']}）", "",
          "## 汇总", "",
          f"- 实体类型合计：`{json.dumps(summary['entity_totals'], ensure_ascii=False)}`",
          f"- 视觉 marks 合计：`{json.dumps(summary['visual_totals'], ensure_ascii=False)}`",
          f"- 决策分布：`{json.dumps(summary['decision_totals'], ensure_ascii=False)}`",
          f"- 疑似整页误涂：{summary.get('suspicious_marks', 0)} 处"
          f"（其中整页证件误判 {summary.get('whole_page_id_marks', 0)} 处、"
          f"无效码大框 {summary.get('invalid_large_marks', 0)} 处；需人工复核）",
          f"- 整页证件误判落在票面/合同页：{summary.get('files_id_fp_on_document', 0)} 个文件"
          "（可能把整页发票/合同涂黑，需优先修）",
          f"- 单文件耗时：中位 {summary['per_file_ms']['median']}ms / "
          f"最大 {summary['per_file_ms']['max']}ms / 合计 {summary['total_ms']}ms", "",
          "## 逐文件", ""]
    headers = ["文件", "类型", "页", "实体", "视觉", "QR(marks/black)", "人脸", "证件",
               "耗时ms", "告警", "备注"]
    table_rows = []
    for row in rows:
        id_card = row.get("id_card") or {}
        invoice = row.get("invoice") or {}
        note = row.get("error", "")
        if not note and row.get("suspicious_marks"):
            note = f"疑似整页误涂 {len(row['suspicious_marks'])} 处"
        if not note and id_card.get("hint"):
            note = id_card["hint"]
        elif not note and invoice.get("hint"):
            note = invoice["hint"]
        table_rows.append([
            row.get("name", ""), row.get("fmt", ""), row.get("pages", 0),
            row.get("entity_total", 0),
            json.dumps(row.get("visual_marks", {}), ensure_ascii=False),
            f"{_to_int(invoice.get('qrcode_marks'))}/{_to_int(invoice.get('qrcode_black'))}",
            _to_int((row.get("visual_marks") or {}).get("face")),
            f"{_to_int(id_card.get('id_marks'))}/{_to_int(id_card.get('id_black'))}",
            row.get("total_ms", 0),
            len(row.get("warnings") or []),
            note,
        ])
    md.extend(_fmt_table(headers, table_rows))
    md_path = out_dir / "summary.md"
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    paths["md"] = md_path
    return paths


def _git_head() -> str:
    try:
        import subprocess

        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(REPO), capture_output=True, text=True,
                             timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    try:  # 退化：直接读 .git（worktree/受限环境可能失败）
        head = REPO / ".git" / "HEAD"
        if head.is_file():
            text = head.read_text(encoding="utf-8").strip()
            if text.startswith("ref:"):
                ref = REPO / ".git" / text.split(":", 1)[1].strip()
                if ref.is_file():
                    return ref.read_text(encoding="utf-8").strip()[:12]
            return text[:12]
    except Exception:  # noqa: BLE001
        pass
    return ""


_ENGINE_SNAPSHOT_FILES = (
    "yinzhan/pipeline.py", "yinzhan/jobs.py", "yinzhan/registry.py",
    "yinzhan/mediascan.py", "yinzhan/barcodes.py", "yinzhan/visual_targets.py",
    "yinzhan/udm.py", "yinzhan/rules.py", "yinzhan/ocr.py",
    "yinzhan/parsers/image_parser.py", "yinzhan/parsers/pdf_parser.py",
    "yinzhan/parsers/tiff_parser.py", "yinzhan/parsers/docx_parser.py",
    "yinzhan/parsers/xlsx_parser.py",
    # 替换器/内嵌图文字 helper 也会直接影响成品内容（R04 地址泄漏根因所在），
    # 必须纳入指纹，否则引擎快照无法覆盖这类回归。
    "yinzhan/replacers/surrogate.py", "yinzhan/media_text.py",
)


def _engine_snapshot() -> dict:
    """记录评测时的引擎文件指纹 + git 状态，保证数字可追溯（并行开发期尤其重要）。"""
    import hashlib
    import subprocess

    files = []
    for rel in _ENGINE_SNAPSHOT_FILES:
        path = SERVER / rel
        if not path.is_file():
            continue
        try:
            files.append({
                "path": rel,
                "size": path.stat().st_size,
                "sha256_16": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
                "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                    timespec="seconds"),
            })
        except Exception:  # noqa: BLE001
            continue
    rules = []
    try:
        rules_dir = SERVER / "yinzhan" / "rules"
        for path in sorted(rules_dir.glob("*.yaml")):
            rules.append({"name": path.name,
                          "sha256_16": hashlib.sha256(path.read_bytes()).hexdigest()[:16]})
    except Exception:  # noqa: BLE001
        pass
    git_dirty = None
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=str(REPO),
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            git_dirty = bool(out.stdout.strip())
            dirty_paths = [line[3:].strip() for line in out.stdout.splitlines()
                           if line.strip()]
            git_dirty_paths = dirty_paths[:20]
        else:
            git_dirty_paths = []
    except Exception:  # noqa: BLE001
        git_dirty_paths = []
    return {"git_head": _git_head(), "git_dirty": git_dirty,
            "git_dirty_paths": git_dirty_paths, "engine_files": files, "rules": rules}


def _print_console(rows: list[dict], summary: dict) -> None:
    print("=" * 78)
    print("真实媒体评测（parse → detect → decide → 视觉标记；不导出、不写仓库）")
    print("=" * 78)
    for row in rows:
        flags = []
        if row.get("error"):
            flags.append(f"ERROR {row['error']}")
        inv = row.get("invoice") or {}
        idc = row.get("id_card") or {}
        if _to_int(inv.get("qrcode_marks")):
            flags.append(f"QR {inv['qrcode_marks']}/black {inv['qrcode_black']}")
        if _to_int((row.get("visual_marks") or {}).get("face")):
            flags.append(f"face {row['visual_marks']['face']}")
        if row.get("suspicious_marks"):
            flags.append(f"疑似整页误涂 {len(row['suspicious_marks'])} 处")
        if row.get("whole_page_id_marks"):
            flags.append(f"整页证件误判 {len(row['whole_page_id_marks'])} 处")
        if row.get("id_fp_on_document"):
            flags.append("票面/合同整页证件误判（需优先修）")
        if idc.get("hint"):
            flags.append(idc["hint"])
        print(f"  {row.get('name','')} [{row.get('fmt','')}, {row.get('pages',0)}p] "
              f"实体 {row.get('entity_total',0)} · 视觉 "
              f"{json.dumps(row.get('visual_marks', {}), ensure_ascii=False)} · "
              f"{row.get('total_ms',0)}ms" + (f" · {' | '.join(flags)}" if flags else ""))
    print("-" * 78)
    print(f"文件 {summary['files']}（错误 {summary['files_error']}，"
          f"零实体 {summary['files_zero_entities']}）· "
          f"实体类型 {json.dumps(summary['entity_totals'], ensure_ascii=False)}")
    print(f"视觉 marks {json.dumps(summary['visual_totals'], ensure_ascii=False)} · "
          f"决策 {json.dumps(summary['decision_totals'], ensure_ascii=False)}")
    print(f"耗时：中位 {summary['per_file_ms']['median']}ms / "
          f"最大 {summary['per_file_ms']['max']}ms / 合计 {summary['total_ms']}ms")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dirs, out_dir, _data_root = _prepare_env(args)
    print(f"评测目录：{[str(d) for d in dirs]}")
    print(f"报告目录：{out_dir}")
    engine = _load_engine()
    engine_snapshot = _engine_snapshot()
    print(f"引擎快照：HEAD {engine_snapshot.get('git_head')} · "
          f"工作区{'有未提交改动' if engine_snapshot.get('git_dirty') else '干净'}")
    ocr = None
    if not args.no_ocr:
        try:
            ocr = engine["ocr"].get_ocr()
            if not getattr(ocr, "available", True):
                print("[WARN] OCR 不可用，图片文字不会被识别")
                ocr = None
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] OCR 初始化失败：{type(exc).__name__}，继续无 OCR 评测")
            ocr = None
    extra_exts = {e.strip().lower() for e in args.include.split(",") if e.strip()}
    rows: list[dict] = []
    started = time.time()
    for root, path in _iter_files(dirs, extra_exts, args.limit):
        row = _eval_one(path, engine, ocr, args.strictness)
        row["source_root"] = str(root)
        rows.append(row)
        if not args.quiet:
            status = "ERROR " + row["error"] if row.get("error") else "ok"
            print(f"  [{len(rows):02d}] {path.name} → {status} "
                  f"实体 {row.get('entity_total', 0)} "
                  f"视觉 {json.dumps(row.get('visual_marks', {}), ensure_ascii=False)} "
                  f"{row.get('total_ms', 0)}ms")
    summary = _aggregate(rows, dirs, engine, args)
    summary["engine_snapshot"] = engine_snapshot
    summary["elapsed_s"] = round(time.time() - started, 1)
    paths = _write_reports(rows, summary, out_dir, args)
    if not args.quiet:
        _print_console(rows, summary)
    print("报告：")
    for kind, p in paths.items():
        print(f"  {kind}: {p}")
    return 0 if rows else 2


if __name__ == "__main__":
    sys.exit(main())
