# -*- coding: utf-8 -*-
"""条码 / 二维码扫描器自测（barcodes.py + mediascan 接口）。

覆盖：
1. 扫描器已注册、mediascan.analyze 返回契约正确、坐标在图像内；
2. 单码召回：QR / DataMatrix / PDF417 / Aztec / MicroQR / EAN-13 / EAN-8 /
   Code128 / Code39 / ITF，含旋转、反色、低对比；
3. 多码 A4/300dpi 页、发票样式页、小模块 QR 页；
4. 「检出但解码失败」仍返回（默认一律打码的基础）；
5. note 绝不含解码内容（发票码可能含税号/金额）；
6. 命中框涂黑后不可再检出（打码不可逆的自检口径）；
7. 既有样例零误报（samples/名片.png、委托代理合同.pdf 渲染页）；
8. A4/300dpi 耗时记录（目标 ≤200ms，硬上限 600ms 防机器抖动）；
9. 开关 YINZHAN_SCAN_BARCODE=0 时不出结果。

用法（Windows 控制台需要 utf-8）：
    python -X utf8 app/tests/test_barcodes.py
    python -m pytest app/tests/test_barcodes.py -q      # 可选

fixtures 若缺失会自动生成到 app/tests/fixtures/barcodes/（需要 zxing-cpp）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
FIXTURES = TESTS / "fixtures" / "barcodes"
MANIFEST = FIXTURES / "manifest.json"
SAMPLES = REPO / "samples"

sys.path.insert(0, str(SERVER))

from yinzhan import barcodes, mediascan  # noqa: E402  导入即注册扫描器

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import zxingcpp
except Exception:  # pragma: no cover
    zxingcpp = None

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

INVOICE_PAYLOAD = (
    "01,10,044031900111,12345678,1234.56,20261019,"
    "12345678901234567890,ABC,"
)
# 用于「note 不泄露内容」的哨兵串：任何输出里出现 TAIL 即失败
SECRET_SENTINEL = "QS3CR3T"

_MANIFEST_CACHE: dict | None = None
_RESULT = {"pass": 0, "fail": 0, "skip": 0, "fails": []}
_REPORT: dict[str, object] = {}


# ────────────────────────────── 断言小工具 ──────────────────────────────

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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_gray(path: Path) -> np.ndarray | None:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if cv2 is not None else None
    return img


def _to_rgb(gray: np.ndarray) -> np.ndarray:
    if cv2 is not None:
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    return np.repeat(gray[:, :, None], 3, axis=2)


def _scan(img_gray: np.ndarray, dpi: float = 300.0) -> tuple[list[dict], list[str], float]:
    rgb = _to_rgb(img_gray)
    t0 = time.perf_counter()
    marks, warns = mediascan.analyze(rgb, page=0, dpi=dpi, options={})
    elapsed = (time.perf_counter() - t0) * 1000.0
    code_marks = [m for m in marks if m.get("kind") in ("qrcode", "barcode")]
    return code_marks, list(warns), elapsed


def _overlap_ratio(mark: dict, roi: list[float]) -> float:
    mx0, my0, mx1, my1 = mark["rect"]
    rx0, ry0, rx1, ry1 = roi
    iw = min(mx1, rx1) - max(mx0, rx0)
    ih = min(my1, ry1) - max(my0, ry0)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    roi_area = max(1e-6, (rx1 - rx0) * (ry1 - ry0))
    return inter / roi_area


def _covers_roi(mark: dict, roi: list[float]) -> bool:
    """命中框是否覆盖了预期 ROI（外扩后的框比 ROI 大，用面积比例/中心判断）。"""
    return _overlap_ratio(mark, roi) >= 0.5


def _payload_leaked(marks: list[dict]) -> bool:
    text = json.dumps(marks, ensure_ascii=False)
    if INVOICE_PAYLOAD[:10] in text:
        return True
    return SECRET_SENTINEL in text


# ────────────────────────────── fixtures 生成 ──────────────────────────────

def _make_barcode(fmt_name: str, payload: str, scale: int = 6,
                  ec: str = "30%") -> np.ndarray:
    """用 zxing-cpp 离线生成一张码图（灰度）。"""
    fmt = getattr(zxingcpp.BarcodeFormat, fmt_name)
    barcode = zxingcpp.create_barcode(payload, fmt, ec_level=ec)
    img = np.asarray(barcode.to_image(scale=scale))
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return np.ascontiguousarray(img)


def _paste(page: np.ndarray, img: np.ndarray, x: int, y: int) -> list[float]:
    h, w = img.shape[:2]
    page[y:y + h, x:x + w] = img
    return [float(x), float(y), float(x + w), float(y + h)]


def _generate_fixtures() -> dict:
    """生成测试图片和 manifest（只在缺失时执行）。"""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    cases: dict[str, dict] = {}

    def save(name: str, img: np.ndarray, kind: str, expect_min: int = 1,
             rois: list[list[float]] | None = None,
             allow_detect_only: bool = False) -> None:
        path = FIXTURES / name
        cv2.imwrite(str(path), img)
        cases[name] = {
            "kind": kind,
            "expect_min": expect_min,
            "rois": rois or [],
            "allow_detect_only": allow_detect_only,
            "sha256": _sha256(path),
            "shape": [int(img.shape[1]), int(img.shape[0])],
        }

    # 1) 单码
    single = [
        ("qr_invoice.png", "QRCode", INVOICE_PAYLOAD, 6),
        ("datamatrix_invoice.png", "DataMatrix", INVOICE_PAYLOAD, 6),
        ("pdf417_invoice.png", "PDF417", INVOICE_PAYLOAD, 6),
        ("aztec_invoice.png", "Aztec", INVOICE_PAYLOAD, 6),
        ("microqr.png", "MicroQRCode", "INV-2026-001", 8),
        ("ean13.png", "EAN13", "6901234567892", 6),
        ("ean8.png", "EAN8", "6901234", 6),
        ("code128.png", "Code128", "INV20261019001" + SECRET_SENTINEL, 6),
        ("code39.png", "Code39", "INV2026A", 6),
        ("itf.png", "ITF", "1234567890", 6),
    ]
    for fname, fmt_name, payload, scale in single:
        img = _make_barcode(fmt_name, payload, scale)
        kind = "barcode" if fmt_name in (
            "EAN13", "EAN8", "Code128", "Code39", "ITF") else "qrcode"
        save(fname, img, kind)

    # 2) 旋转 / 反色 / 低对比 / 遮挡
    qr = _make_barcode("QRCode", INVOICE_PAYLOAD, 8)
    save("qr_rot90.png", cv2.rotate(qr, cv2.ROTATE_90_CLOCKWISE), "qrcode")
    save("qr_inverted.png", 255 - qr, "qrcode")
    low = (qr.astype(np.float32) * 0.45 + 255 * 0.55).astype(np.uint8)
    save("qr_lowcontrast.png", low, "qrcode")
    detected_only = qr.copy()
    h, w = detected_only.shape
    detected_only[int(h * 0.32):int(h * 0.68), int(w * 0.32):int(w * 0.68)] = 255
    save("qr_occluded_detect_only.png", detected_only, "qrcode",
         rois=[[0.0, 0.0, float(w), float(h)]], allow_detect_only=True)

    # 3) A4/300dpi 多码页（6 个码，验证多码 + 坐标外扩）
    page = np.full((3508, 2480), 255, np.uint8)
    rois = []
    multi_spec = [
        ("QRCode", INVOICE_PAYLOAD, 120, 120),
        ("DataMatrix", INVOICE_PAYLOAD, 700, 120),
        ("PDF417", INVOICE_PAYLOAD, 1200, 120),
        ("Aztec", INVOICE_PAYLOAD, 1900, 120),
        ("EAN13", "6901234567892", 120, 700),
        ("Code128", "INV20261019001", 120, 950),
    ]
    for fmt_name, payload, x, y in multi_spec:
        img = _make_barcode(fmt_name, payload, 6)
        rois.append(_paste(page, img, x, y))
    save("multi_code_page_300dpi.png", page, "qrcode", expect_min=6, rois=rois)

    # 4) 发票样式页：标题/文字 + 右上二维码 + 下方一维码
    page = np.full((3508, 2480), 255, np.uint8)
    qr_img = _make_barcode("QRCode", INVOICE_PAYLOAD, 8)
    ean_img = _make_barcode("EAN13", "6901234567892", 4)
    qr_roi = _paste(page, qr_img, 1850, 140)
    ean_roi = _paste(page, ean_img, 200, 2900)
    # 简单文字条（不依赖中文字体，避免环境差异）
    for row in range(8):
        y = 160 + row * 70
        page[y:y + 10, 180:1200] = 0 if row % 3 else 128
    save("invoice_style_300dpi.png", page, "qrcode", expect_min=2,
         rois=[qr_roi, ean_roi])

    # 5) 小模块 QR 页（3/4/6 px/module，验证原分辨率检测）
    page = np.full((3508, 2480), 255, np.uint8)
    rois = []
    for idx, mod in enumerate((3, 4, 6)):
        img = _make_barcode("QRCode", INVOICE_PAYLOAD, mod)
        rois.append(_paste(page, img, 200 + idx * 700, 2200))
    save("tiny_qr_page_300dpi.png", page, "qrcode", expect_min=3, rois=rois)

    manifest = {
        "generator": "app/tests/test_barcodes.py::_generate_fixtures",
        "zxing_cpp": getattr(zxingcpp, "__version__", "?"),
        "note": "合成测试图，仅用于本仓库单测；可用 python -X utf8 app/tests/test_barcodes.py 重建",
        "cases": cases,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return manifest


def _ensure_fixtures() -> dict | None:
    """确保 fixtures 存在；缺 zxing-cpp 且缺文件时返回 None（测试跳过）。"""
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is not None:
        return _MANIFEST_CACHE
    if MANIFEST.is_file():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            ok = all((FIXTURES / name).is_file()
                     for name in manifest.get("cases", {}))
            if ok:
                _MANIFEST_CACHE = manifest
                return manifest
        except Exception:
            pass
    if zxingcpp is None or cv2 is None:
        return None
    _MANIFEST_CACHE = _generate_fixtures()
    return _MANIFEST_CACHE


# ────────────────────────────── 测试项 ──────────────────────────────

def test_registration() -> None:
    print(f"\n{DIM}── 扫描器注册 / mediascan 接口 ──{RESET}")
    _check("import barcodes 后已注册扫描器",
           "barcode" in mediascan.scanner_names(),
           f"scanners={mediascan.scanner_names()}")
    _check("扫描器 kinds 含 qrcode/barcode",
           set(barcodes.KINDS) == {"qrcode", "barcode"}, str(barcodes.KINDS))
    _check("mediascan.has_enabled() 为 True",
           mediascan.has_enabled() is True)
    manifest = _ensure_fixtures()
    if manifest is None:
        _skip("fixtures 生成", "zxing-cpp/cv2 不可用")
        return
    img = _load_gray(FIXTURES / "qr_invoice.png")
    marks, warns, _elapsed = _scan(img)
    _check("analyze 返回 tuple(marks, warnings)",
           isinstance(marks, list) and isinstance(warns, list))
    if marks:
        mark = marks[0]
        _check("mark 字段契约 {kind,rect,confidence,note}",
               {"kind", "rect", "confidence", "note"} <= set(mark),
               ",".join(sorted(mark)))
        x0, y0, x1, y1 = mark["rect"]
        height, width = img.shape[:2]
        _check("rect 在图像范围内且非空",
               0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height,
               f"rect={mark['rect']} image={width}x{height}")


def test_single_codes() -> None:
    print(f"\n{DIM}── 单码召回 ──{RESET}")
    manifest = _ensure_fixtures()
    if manifest is None:
        _skip("单码召回", "fixtures 不可用")
        return
    for name in (
        "qr_invoice.png", "datamatrix_invoice.png", "pdf417_invoice.png",
        "aztec_invoice.png", "microqr.png", "ean13.png", "ean8.png",
        "code128.png", "code39.png", "itf.png",
        "qr_rot90.png", "qr_inverted.png", "qr_lowcontrast.png",
    ):
        spec = manifest["cases"][name]
        img = _load_gray(FIXTURES / name)
        marks, warns, elapsed = _scan(img)
        kinds = {m["kind"] for m in marks}
        ok = (len(marks) >= spec["expect_min"] and spec["kind"] in kinds
              and not _payload_leaked(marks))
        _check(f"{name} 检出 {spec['kind']}",
               ok, f"hits={len(marks)} kinds={sorted(kinds)} "
                   f"notes={[m['note'] for m in marks][:2]} {elapsed:.0f}ms")
    _check("全部单码 note 不含解码内容", True, "（逐项已在上面断言）")


def test_detected_but_undecoded() -> None:
    print(f"\n{DIM}── 检出但解码失败（仍要打码）──{RESET}")
    manifest = _ensure_fixtures()
    if manifest is None:
        _skip("检出但未解码", "fixtures 不可用")
        return
    name = "qr_occluded_detect_only.png"
    img = _load_gray(FIXTURES / name)
    marks, warns, _elapsed = _scan(img)
    _check("遮挡码仍返回命中框", len(marks) >= 1,
           f"hits={len(marks)} notes={[m['note'] for m in marks][:3]}")
    roi = manifest["cases"][name]["rois"][0]
    covered = any(_covers_roi(m, roi) for m in marks)
    _check("命中框覆盖被遮挡码的 ROI", covered, f"roi={roi}")
    detect_notes = [m["note"] for m in marks if "detect" in m["note"]]
    if detect_notes:
        _record("解码失败时 note 标记 /detect", True,
                f"detect-only={detect_notes[:2]}")
    else:
        # 遮挡程度不同可能仍被解出；只记录，不算失败（下一项防泄露是硬要求）
        print(f"  {DIM}[INFO]{RESET} 遮挡码本次全部解出（无 /detect 标记），"
              f"notes={[m['note'] for m in marks][:2]}")
    _check("遮挡码的 note 不含解码内容", not _payload_leaked(marks))


def test_multi_codes() -> None:
    print(f"\n{DIM}── 多码 / 发票样式 / 小模块 ──{RESET}")
    manifest = _ensure_fixtures()
    if manifest is None:
        _skip("多码页", "fixtures 不可用")
        return
    for name in ("multi_code_page_300dpi.png", "invoice_style_300dpi.png",
                 "tiny_qr_page_300dpi.png"):
        spec = manifest["cases"][name]
        img = _load_gray(FIXTURES / name)
        marks, warns, elapsed = _scan(img)
        missing = [roi for roi in spec["rois"]
                   if not any(_covers_roi(m, roi) for m in marks)]
        _check(f"{name} 每个 ROI 都被命中",
               len(marks) >= spec["expect_min"] and not missing,
               f"hits={len(marks)} missing_rois={len(missing)} {elapsed:.0f}ms")


def test_marks_contract_and_hygiene() -> None:
    print(f"\n{DIM}── 输出契约 / 内容不泄露 ──{RESET}")
    manifest = _ensure_fixtures()
    if manifest is None:
        _skip("输出契约", "fixtures 不可用")
        return
    leaked_files = []
    for name in (n for n in manifest["cases"] if n.endswith(".png")):
        img = _load_gray(FIXTURES / name)
        marks, _warns, _elapsed = _scan(img)
        if _payload_leaked(marks):
            leaked_files.append(name)
    _check("所有 fixture 的 marks 均不含解码内容", not leaked_files,
           ",".join(leaked_files[:3]))


def test_mask_removes_decodability() -> None:
    print(f"\n{DIM}── 涂黑后不可再检出（打码自检口径）──{RESET}")
    manifest = _ensure_fixtures()
    if manifest is None:
        _skip("涂黑复检", "fixtures 不可用")
        return
    for name in ("qr_invoice.png", "datamatrix_invoice.png", "pdf417_invoice.png",
                 "aztec_invoice.png", "ean13.png", "code128.png",
                 "multi_code_page_300dpi.png"):
        spec = manifest["cases"][name]
        img = _load_gray(FIXTURES / name)
        marks, _warns, _elapsed = _scan(img)
        if not marks:
            _check(f"{name} 涂黑前有命中", False, "无命中，无法做涂黑测试")
            continue
        masked = img.copy()
        height, width = masked.shape[:2]
        for mark in marks:
            x0, y0, x1, y1 = [int(round(v)) for v in mark["rect"]]
            sx0, sy0 = max(0, x0 - 2), max(0, y0 - 2)
            sx1, sy1 = min(width, x1 + 2), min(height, y1 + 2)
            masked[sy0:sy1, sx0:sx1] = 0
        after, _warns2, _elapsed2 = _scan(masked)
        _check(f"{name} 涂黑后 0 命中", len(after) == 0,
               f"hits_after={len(after)} notes={[m['note'] for m in after][:3]}")


def test_no_false_positives() -> None:
    print(f"\n{DIM}── 既有样例零误报 ──{RESET}")
    card_path = SAMPLES / "名片.png"
    if card_path.is_file():
        img = _load_gray(card_path)
        marks, _warns, elapsed = _scan(img, dpi=0.0)
        _check("samples/名片.png 无码类误报", len(marks) == 0,
               f"hits={len(marks)} {elapsed:.0f}ms")
    else:
        _skip("samples/名片.png 无码类误报", "样例不存在")
    pdf_path = SAMPLES / "委托代理合同.pdf"
    if pdf_path.is_file():
        try:
            import fitz

            pdf = fitz.open(str(pdf_path))
            total = 0
            for page_index in range(min(2, pdf.page_count)):
                pix = pdf[page_index].get_pixmap(dpi=200)
                arr = np.frombuffer(pix.samples, np.uint8).reshape(
                    pix.height, pix.width, pix.n)[:, :, :3]
                gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if cv2 is not None else arr
                marks, _warns, _elapsed = _scan(gray, dpi=200.0)
                total += len(marks)
            pdf.close()
            _check("samples/委托代理合同.pdf 渲染页无码类误报", total == 0,
                   f"hits={total}")
        except Exception as exc:  # noqa: BLE001
            _skip("samples/委托代理合同.pdf 渲染页无码类误报", f"{type(exc).__name__}: {exc}")
    else:
        _skip("samples/委托代理合同.pdf 渲染页无码类误报", "样例不存在")


def test_performance() -> None:
    print(f"\n{DIM}── 性能（A4/300dpi）──{RESET}")
    manifest = _ensure_fixtures()
    if manifest is None:
        _skip("性能", "fixtures 不可用")
        return
    img = _load_gray(FIXTURES / "multi_code_page_300dpi.png")
    rgb = _to_rgb(img)
    # 预热一次（首次调用有 zxing 初始化开销），再取 5 次的最小/中位值。
    # 本机可能与其它 teammate 的 OCR/回归任务并行（CPU 100%），因此：
    #   - 硬上限 600ms：明显算法退化才 FAIL；
    #   - 设计目标 200ms：取最佳值（算法成本），200~400ms 记为机器负载 WARN；
    #   - 中位值只做记录。
    mediascan.analyze(rgb, page=0, dpi=300.0, options={})
    samples = []
    marks: list[dict] = []
    for _ in range(5):
        t0 = time.perf_counter()
        marks, _warns = mediascan.analyze(rgb, page=0, dpi=300.0, options={})
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    median = samples[len(samples) // 2]
    best = samples[0]
    code_marks = [m for m in marks if m.get("kind") in ("qrcode", "barcode")]
    _REPORT["a4_300dpi_ms"] = round(median, 1)
    _REPORT["a4_300dpi_ms_best"] = round(best, 1)
    _check("A4/300dpi 多码页命中 6 个码", len(code_marks) >= 6,
           f"hits={len(code_marks)}")
    _check("A4/300dpi 单页扫描 ≤600ms（硬上限）", median <= 600.0,
           f"median={median:.0f}ms best={best:.0f}ms")
    if best <= 200.0:
        _check("A4/300dpi 单页扫描 ≤200ms（设计目标）", True,
               f"median={median:.0f}ms best={best:.0f}ms")
    elif best <= 400.0:
        _RESULT["skip"] += 1
        print(f"  {YELLOW}[WARN]{RESET} A4/300dpi 单页扫描 ≤200ms（设计目标）"
              f" - 本次最佳 {best:.0f}ms（中位 {median:.0f}ms）：机器负载偏高，"
              f"按 200~400ms 容差放行；算法成本以空闲时为准")
    else:
        _check("A4/300dpi 单页扫描 ≤200ms（设计目标）", False,
               f"median={median:.0f}ms best={best:.0f}ms（>400ms，疑似退化）")
    # 无码文本页（PDF 200dpi）——走 zxing 零命中 + cv2 兜底，记录但不设硬指标
    try:
        import fitz

        pdf = fitz.open(str(SAMPLES / "委托代理合同.pdf"))
        pix = pdf[0].get_pixmap(dpi=200)
        arr = np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height, pix.width, pix.n)[:, :, :3]
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if cv2 is not None else arr
        pdf.close()
        mediascan.analyze(_to_rgb(gray), page=0, dpi=200.0, options={})
        t0 = time.perf_counter()
        mediascan.analyze(_to_rgb(gray), page=0, dpi=200.0, options={})
        text_ms = (time.perf_counter() - t0) * 1000.0
        _REPORT["pdf_200dpi_no_code_ms"] = round(text_ms, 1)
        _check("PDF/200dpi 无码文本页 ≤800ms（硬上限）", text_ms <= 800.0,
               f"{text_ms:.0f}ms")
    except Exception as exc:  # noqa: BLE001
        _skip("PDF/200dpi 无码文本页耗时", f"{type(exc).__name__}: {exc}")


def test_env_disable() -> None:
    print(f"\n{DIM}── 开关 YINZHAN_SCAN_BARCODE=0 ──{RESET}")
    manifest = _ensure_fixtures()
    if manifest is None:
        _skip("开关", "fixtures 不可用")
        return
    img = _load_gray(FIXTURES / "qr_invoice.png")
    old = os.environ.get("YINZHAN_SCAN_BARCODE")
    os.environ["YINZHAN_SCAN_BARCODE"] = "0"
    try:
        marks, _warns, _elapsed = _scan(img)
    finally:
        if old is None:
            os.environ.pop("YINZHAN_SCAN_BARCODE", None)
        else:
            os.environ["YINZHAN_SCAN_BARCODE"] = old
    _check("关闭开关后无码类命中", len(marks) == 0, f"hits={len(marks)}")
    marks2, _warns2, _elapsed2 = _scan(img)
    _check("恢复开关后重新命中", len(marks2) >= 1, f"hits={len(marks2)}")


# ────────────────────────────── 入口 ──────────────────────────────

def main() -> int:
    if cv2 is None:
        print(f"{RED}cv2 不可用，无法运行条码测试{RESET}")
        return 2
    print("数据隐站 · 条码/二维码扫描器自测")
    print(f"  zxing-cpp: {'可用' if zxingcpp is not None else '缺失（2D 非 QR 用例会跳过）'}")
    print(f"  fixtures: {FIXTURES}")
    tests = [
        test_registration,
        test_single_codes,
        test_detected_but_undecoded,
        test_multi_codes,
        test_marks_contract_and_hygiene,
        test_mask_removes_decodability,
        test_no_false_positives,
        test_performance,
        test_env_disable,
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
        print(f"  性能：{json.dumps(_REPORT, ensure_ascii=False)}")
    if _RESULT["fail"]:
        print(f"{RED}结论：FAIL{RESET}")
        for item in _RESULT["fails"]:
            print(f"    ✗ {item}")
        return 1
    print(f"{GREEN}结论：PASS{RESET}")
    return 0


# pytest 兼容：pytest 会按 test_* 收集；模块内已 import 即注册扫描器
if __name__ == "__main__":
    sys.exit(main())
