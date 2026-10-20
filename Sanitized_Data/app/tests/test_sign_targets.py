# -*- coding: utf-8 -*-
"""P1-A 门牌/路牌/店招/单位铭牌扫描器自测（sign_targets）。

覆盖：
1. 注册/接口：import 即注册、kinds 正确、无 OCR 文本框返回空；
2. 正样本（PIL 合成、**真实 OCR**）：门牌/路牌/店招/单位铭牌 4 类，
   经 `ImageParser.parse`（真实 OCR + mediascan.analyze）断言 kind/ROI/note；
3. 负样本（0 新增误报）：samples/名片.png、合成发票、合同正文渲染页、
   合成文档/名片/风景 → 均不得出现 sort 四类 kind；
4. 单元防护：无 OCR / 纸面门控 / “地址：”标签 / 过小字 / 重复框去重；
5. 性能：单页扫描（不含 OCR）耗时记录。

用法（Windows 控制台要 -X utf8）：
    python -X utf8 app/tests/test_sign_targets.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# ── 隔离数据目录：必须在 import yinzhan.* 之前 ──
_WORK = Path(tempfile.mkdtemp(prefix="yz_sign_test_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(_WORK / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(_WORK / "out"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures" / "signs"
SAMPLES = REPO / "samples"
PIPL_FIXTURES = TESTS / "fixtures" / "pipl_public"

if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import mediascan, sign_targets  # noqa: E402  导入即注册
from yinzhan.mediascan import ScanContext  # noqa: E402
from yinzhan.ocr import get_ocr  # noqa: E402
from yinzhan.parsers.image_parser import ImageParser  # noqa: E402
from yinzhan.udm import ParseContext  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

_RESULT = {"pass": 0, "fail": 0, "skip": 0, "fails": []}
_REPORT: dict[str, object] = {}


# ────────────────────────────── 断言工具 ──────────────────────────────

def _record(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        _RESULT["pass"] += 1
        print(f"  {GREEN}[PASS]{RESET} {name}" + (f" - {detail}" if detail else ""))
    else:
        _RESULT["fail"] += 1
        _RESULT["fails"].append(f"{name}: {detail}")
        print(f"  {RED}[FAIL]{RESET} {name}" + (f" - {detail}" if detail else ""))
    return ok


def _skip(name: str, reason: str) -> None:
    _RESULT["skip"] += 1
    print(f"  {YELLOW}[SKIP]{RESET} {name} - {reason}")


def _check(name: str, cond: bool, detail: str = "") -> None:
    _record(name, bool(cond), detail)


def _rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def _scan_boxes(rgb: np.ndarray, boxes: list[dict] | None) -> list[dict]:
    options = {"text_boxes": boxes or [], "fmt": "test"}
    ctx = ScanContext(page=0, dpi=0.0,
                      image_size=(int(rgb.shape[1]), int(rgb.shape[0])),
                      options=options, warnings=[])
    hits = sign_targets.scan(rgb, ctx)
    return [{"kind": h.kind, "rect": tuple(h.rect), "confidence": h.confidence,
             "note": h.note} for h in hits]


def _sign_marks(doc) -> list:
    return [m for m in doc.visual_marks if m.kind in sign_targets.KINDS]


def _parse_with_ocr(path: Path):
    parser = ImageParser()
    ctx = ParseContext(ocr=get_ocr(), workdir=str(_WORK))
    return parser.parse(path, ctx)


def _overlap_ratio(rect, roi) -> float:
    x0, y0 = max(rect[0], roi[0]), max(rect[1], roi[1])
    x1, y1 = min(rect[2], roi[2]), min(rect[3], roi[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area = max(1.0, (rect[2] - rect[0]) * (rect[3] - rect[1]))
    return inter / area


def _center_inside(rect, roi) -> bool:
    cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
    return roi[0] <= cx <= roi[2] and roi[1] <= cy <= roi[3]


# ────────────────────────────── 测试项 ──────────────────────────────

def test_registration() -> None:
    print(f"\n{DIM}── 注册 / 接口 ──{RESET}")
    _check("scanner 已注册", "sign_targets" in mediascan.scanner_names(),
           str(mediascan.scanner_names()))
    _check("KINDS 与任务一致",
           set(sign_targets.KINDS) == {"road_sign", "door_address",
                                       "shop_sign", "org_plaque"},
           str(sign_targets.KINDS))
    rgb = np.full((600, 800, 3), 90, dtype=np.uint8)
    _check("无 OCR 文本框 → 返回空（不承诺）", _scan_boxes(rgb, []) == [],
           "no text_boxes")
    marks, _warn = mediascan.analyze(rgb, page=0, dpi=0.0, options={})
    _check("mediascan.analyze 无文本框也不产生招牌误报",
           not [m for m in marks if m.get("kind") in sign_targets.KINDS],
           str([m.get("kind") for m in marks]))


def test_positives_real_ocr() -> None:
    print(f"\n{DIM}── 正样本（合成图 + 真实 OCR + mediascan）──{RESET}")
    manifest_path = FIXTURES / "manifest.json"
    if not manifest_path.is_file():
        _skip("正样本", "缺少 fixtures/signs/manifest.json")
        return
    import json

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = [c for c in manifest.get("cases", []) if c.get("kind")]
    if not cases:
        _skip("正样本", "manifest 里没有 kind 用例")
        return
    for case in cases:
        path = FIXTURES / case["file"]
        if not path.is_file():
            _skip(case["file"], "文件不存在")
            continue
        t0 = time.perf_counter()
        try:
            doc = _parse_with_ocr(path)
        except Exception as exc:  # noqa: BLE001
            _check(f"{case['file']} 解析", False, f"{type(exc).__name__}: {exc}")
            continue
        elapsed = time.perf_counter() - t0
        marks = _sign_marks(doc)
        kinds = [m.kind for m in marks]
        expected = case["kind"]
        roi = case["roi"]
        hit = next((m for m in marks if m.kind == expected
                    and (_center_inside(tuple(m.rect), roi)
                         or _overlap_ratio(tuple(m.rect), roi) >= 0.5)), None)
        _check(f"{case['file']} 检出 {expected}", hit is not None,
               f"kinds={kinds} {elapsed * 1000:.0f}ms")
        if hit is not None:
            rect = tuple(hit.rect)
            _check(f"{case['file']} 框在牌面 ROI 内/重叠", _center_inside(rect, roi)
                   or _overlap_ratio(rect, roi) >= 0.5,
                   f"rect={tuple(round(v) for v in rect)} roi={roi}")
            _check(f"{case['file']} 框在图像范围内",
                   0 <= rect[0] < rect[2] and 0 <= rect[1] < rect[3],
                   f"rect={rect}")
            _check(f"{case['file']} note 格式 engine=ocr;kind=...",
                   hit.note.startswith(f"engine=ocr;kind={expected};"),
                   hit.note)
            _check(f"{case['file']} note 不含识别值",
                   not any(ch in hit.note for ch in "示例路大道便利店科技"),
                   hit.note)
        _REPORT[case["file"]] = {"kind": kinds, "ms": round(elapsed * 1000, 1)}


def test_negatives_real_ocr() -> None:
    print(f"\n{DIM}── 负样本（0 新增误报）──{RESET}")
    cases = [
        (SAMPLES / "名片.png", "名片（既有样例）"),
        (PIPL_FIXTURES / "synthetic_invoice.png", "发票样式（合成）"),
        (FIXTURES / "neg_document.png", "合同正文样式（合成）"),
        (FIXTURES / "neg_card.png", "名片样式（合成）"),
        (FIXTURES / "neg_scenery.png", "风景（无文字）"),
    ]
    for path, label in cases:
        if not path.is_file():
            _skip(label, f"缺少 {path.name}")
            continue
        t0 = time.perf_counter()
        try:
            doc = _parse_with_ocr(path)
        except Exception as exc:  # noqa: BLE001
            _check(f"{label} 解析", False, f"{type(exc).__name__}: {exc}")
            continue
        elapsed = time.perf_counter() - t0
        marks = _sign_marks(doc)
        _check(f"{label} 0 招牌命中", not marks,
               f"hits={[(m.kind, tuple(round(v) for v in m.rect)) for m in marks]} "
               f"{elapsed * 1000:.0f}ms")


def test_contract_page_no_fp() -> None:
    print(f"\n{DIM}── 合同正文页（PDF 文本层）0 新增误报 ──{RESET}")
    pdf_path = SAMPLES / "委托代理合同.pdf"
    if not pdf_path.is_file():
        _skip("合同正文页", "缺少 samples/委托代理合同.pdf")
        return
    try:
        import fitz

        pdf = fitz.open(str(pdf_path))
        page = pdf[0]
        pix = page.get_pixmap(dpi=150)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)[:, :, :3]
        scale = 150.0 / 72.0
        boxes = []
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
            if block[6] != 0 or not str(text).strip():
                continue
            boxes.append({"text": str(text),
                          "rect": (x0 * scale, y0 * scale, x1 * scale, y1 * scale)})
        pdf.close()
        ctx = ScanContext(page=0, dpi=150.0, image_size=(pix.width, pix.height),
                          options={"text_boxes": boxes, "fmt": "pdf"}, warnings=[])
        hits = [{"kind": h.kind, "rect": tuple(h.rect)}
                for h in sign_targets.scan(rgb, ctx)]
        _check("合同页 0 招牌命中", not hits,
               f"hits={[(h['kind'], tuple(round(v) for v in h['rect'])) for h in hits]}")
    except Exception as exc:  # noqa: BLE001
        _check("合同页 0 招牌命中", False, f"{type(exc).__name__}: {exc}")


def test_unit_guards() -> None:
    print(f"\n{DIM}── 单元防护（规则门槛）──{RESET}")
    gray = np.full((700, 1000, 3), 90, dtype=np.uint8)
    big = (100.0, 300.0, 620.0, 410.0)
    # 1) 大字门牌 → door_address
    hits = _scan_boxes(gray, [{"text": "示例路88号", "rect": big}])
    _check("灰色背景 + 大字门牌 → door_address",
           len(hits) == 1 and hits[0]["kind"] == "door_address",
           str(hits))
    # 2) “地址：”标签 → 不命中
    hits = _scan_boxes(gray, [{"text": "地址：示例路88号", "rect": big}])
    _check("“地址：”标签不当作门牌", hits == [], str(hits))
    # 3) 小字（正文大小）→ 不命中
    small = (100.0, 300.0, 620.0, 322.0)
    hits = _scan_boxes(gray, [{"text": "示例路88号", "rect": small}])
    _check("正文大小的小字不命中", hits == [], str(hits))
    # 4) 白纸页门控：白色页面上的大字也不报
    white = np.full((700, 1000, 3), 250, dtype=np.uint8)
    hits = _scan_boxes(white, [{"text": "示例路88号", "rect": big}])
    _check("白纸/名片页门控 → 不报", hits == [], str(hits))
    # 5) 同区域重复框 → 去重为 1
    hits = _scan_boxes(gray, [
        {"text": "示例路88号", "rect": big},
        {"text": "示例路88号", "rect": (110.0, 305.0, 610.0, 400.0)},
    ])
    _check("同区域重复框去重", len(hits) == 1,
           f"n={len(hits)} hits={[(h['kind'], h['note']) for h in hits]}")
    # 6) 邻近碎片合并：路名 + 方向/距离
    road_boxes = [
        {"text": "示例大道", "rect": (80.0, 120.0, 680.0, 300.0)},
        {"text": "东行 2 km", "rect": (95.0, 340.0, 520.0, 440.0)},
    ]
    hits = _scan_boxes(gray, road_boxes)
    road = [h for h in hits if h["kind"] == "road_sign"]
    _check("相邻“路名+方向”合并为 road_sign", len(road) == 1,
           str([(h["kind"], h["note"]) for h in hits]))
    if road:
        rect = road[0]["rect"]
        _check("合并框覆盖两行文本",
               rect[1] <= 120 and rect[3] >= 440,
               f"rect={tuple(round(v) for v in rect)}")
    # 7) 高速编号 + 出口：应归 road_sign（不是门牌）
    highway = [{"text": "G15沈海高速 出口", "rect": (80.0, 120.0, 760.0, 300.0)}]
    hits = _scan_boxes(gray, highway)
    _check("高速编号+出口 → road_sign",
           len(hits) == 1 and hits[0]["kind"] == "road_sign",
           str([(h["kind"], h["note"]) for h in hits]))
    # 8) 性能：单页规则扫描（不含 OCR）
    boxes = [
        {"text": "示例便利店", "rect": (100.0, 160.0, 840.0, 350.0)},
        {"text": "24小时营业", "rect": (130.0, 345.0, 520.0, 435.0)},
    ]
    t0 = time.perf_counter()
    for _ in range(5):
        _scan_boxes(gray, boxes)
    per = (time.perf_counter() - t0) / 5 * 1000
    _REPORT["rule_scan_ms"] = round(per, 2)
    _check("规则扫描 <50ms/页", per < 50.0, f"{per:.1f}ms")


# ────────────────────────────── 入口 ──────────────────────────────

def main() -> int:
    print("=" * 70)
    print("数据隐站 · P1-A 招牌扫描器自测（sign_targets）")
    print(f"fixtures: {FIXTURES}")
    print("=" * 70)
    tests = [
        test_registration,
        test_positives_real_ocr,
        test_negatives_real_ocr,
        test_contract_page_no_fp,
        test_unit_guards,
    ]
    started = time.time()
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback

            _record(fn.__name__, False, f"{type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)
    elapsed = time.time() - started
    print()
    print(f"  PASS {_RESULT['pass']} · FAIL {_RESULT['fail']} · SKIP {_RESULT['skip']}"
          f" · {elapsed:.1f}s")
    if _REPORT:
        import json

        print(f"  实测：{json.dumps(_REPORT, ensure_ascii=False)}")
    if _RESULT["fail"]:
        print(f"{RED}结论：FAIL{RESET}")
        for item in _RESULT["fails"]:
            print(f"    ✗ {item}")
        return 1
    print(f"{GREEN}结论：PASS{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
