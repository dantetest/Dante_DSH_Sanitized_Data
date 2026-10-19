"""条形码 / 二维码页面扫描器（mediascan 插件）。

定位：给「OCR 读不出字」的码类目标一个统一入口，供 ``image_parser`` /
``tiff_parser`` / ``pdf_parser`` 在页面图像上调用，并由 Verify 复检复用。

设计（见 docs/impl/barcode.md）：
- **主引擎 zxing-cpp**：离线、无模型、无外呼，Apache-2.0；一次读全主流 1D/2D 码，
  自带旋转 / 缩小 / 反色尝试，并能返回「检出但解码失败」的框。
- **兜底 cv2**：``QRCodeDetectorAruco``（QR 检测）+ ``cv2.barcode.BarcodeDetector``
  （1D）。只在主引擎不可用或本页零命中时跑，避免常态翻倍耗时。
- **默认一律打码**：本模块只负责给框，不按解码内容判断敏感性；解码失败但检测到
  （``valid=False`` / cv2 detect-only）也返回。是否打码由上层统一决定。
- **note 只写码制/引擎/是否仅检测**，绝不写解码内容——发票类二维码可能含税号、
  金额、校验码，明文不允许进入 warning/审计/日志。
- **静区外扩**：QR 按 Version 精确算 4 模块；其它 2D 约 6%；1D 每侧 ≥10 模块，
  防止重编码/缩放后边缘像素仍可被读出。
- **性能**：A4/300dpi 主引擎约 150–180ms；大图自动分块，分块数有上限。

环境变量（均可选）：
    YINZHAN_BARCODE_ENGINE=auto|zxing|cv2   （默认 auto）
    YINZHAN_BARCODE_CV2_MAX_EDGE=2200       兜底 cv2 的工作图最长边
    YINZHAN_BARCODE_TILE_EDGE=4000          超过该长边则分块扫描
    YINZHAN_BARCODE_TILE_OVERLAP=0.12       分块重叠比例
    YINZHAN_BARCODE_MAX_TILES=16            单页最多分块数（超出只扫前 N 块并告警）
    YINZHAN_BARCODE_UPSCALE=0               主引擎零命中时是否对小图 2x 放大重试
    YINZHAN_BARCODE_DEBUG=0                 是否把扫描耗时写进 warning
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Iterable

import numpy as np

from . import mediascan

try:  # 主引擎：zxing-cpp（requirements 已声明；缺失时自动退 cv2）
    import zxingcpp
except Exception:  # pragma: no cover - 环境缺依赖时走 cv2 兜底
    zxingcpp = None  # type: ignore[assignment]

try:  # 兜底与图像工具：cv2 是 rapidocr 的依赖，正常都在
    import cv2
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]


SCANNER_NAME = "barcode"
KINDS = ("qrcode", "barcode")

# 单页命中上限：真有这么多码的页面极少，限制它避免异常图拖爆内存/时间
_MAX_HITS = 64
# QR 静区 4 模块；Micro QR 静区 2 模块（规范）
_QR_QUIET_MODULES = 4.0
_MICRO_QR_QUIET_MODULES = 2.0
# 1D 静区按 10 模块/侧估算（EAN/UPC 左 11 右 7，取保守值）
_LINEAR_QUIET_MODULES = 10.0


# ────────────────────────────── 环境变量 ──────────────────────────────

def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip().lower() if value is not None and value.strip() else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def zxing_available() -> bool:
    """主引擎是否可用（测试/诊断用）。"""
    return zxingcpp is not None


def cv2_available() -> bool:
    """兜底引擎是否可用（测试/诊断用）。"""
    return cv2 is not None


# ────────────────────────────── 图像准备 ──────────────────────────────

def _as_gray(image_rgb: Any) -> np.ndarray | None:
    """把调用方给的 RGB(A)/灰度图转成连续 uint8 灰度图；失败返回 None。"""
    if image_rgb is None:
        return None
    try:
        arr = np.asarray(image_rgb)
    except Exception:
        return None
    if arr.ndim == 3 and arr.shape[2] >= 3:
        if cv2 is not None:
            try:
                # mediascan 约定传入 RGB
                gray = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2GRAY)
            except Exception:
                gray = arr[:, :, :3].mean(axis=2)
        else:
            gray = arr[:, :, :3].mean(axis=2)
    elif arr.ndim == 2:
        gray = arr
    else:
        return None
    if gray.dtype != np.uint8:
        try:
            gray = np.clip(gray, 0, 255).astype(np.uint8)
        except Exception:
            return None
    gray = np.ascontiguousarray(gray)
    if gray.ndim != 2 or gray.shape[0] < 8 or gray.shape[1] < 8:
        return None
    return gray


def _downscale(gray: np.ndarray, max_edge: int) -> tuple[np.ndarray, float]:
    """按最长边缩到 max_edge 以内，返回 (图, 缩放比)。"""
    height, width = gray.shape[:2]
    long_edge = max(height, width)
    if max_edge <= 0 or long_edge <= max_edge:
        return gray, 1.0
    scale = max_edge / float(long_edge)
    if cv2 is not None:
        out = cv2.resize(gray, (max(1, int(round(width * scale))),
                                max(1, int(round(height * scale)))),
                         interpolation=cv2.INTER_AREA)
    else:  # 极简降级：整数步长抽稀
        step = max(1, int(round(1.0 / scale)))
        out = gray[::step, ::step]
        scale = 1.0 / float(step)
    return np.ascontiguousarray(out), scale


# ────────────────────────────── 几何：quad → 静区外扩后的 bbox ──────────────────────────────

def _quad_points(position: Any) -> list[tuple[float, float]] | None:
    """zxing 的 Position 或 cv2 的 4×2 数组 → 4 个角点。"""
    if position is None:
        return None
    points: list[tuple[float, float]] = []
    for attr in ("top_left", "top_right", "bottom_right", "bottom_left"):
        pt = getattr(position, attr, None)
        if pt is not None:
            try:
                points.append((float(pt.x), float(pt.y)))
            except Exception:
                points = []
                break
    if len(points) == 4:
        return points
    try:  # cv2: shape (4,2) 或 (1,4,2)
        arr = np.asarray(position, dtype=np.float64).reshape(-1, 2)
        if arr.shape[0] >= 4:
            return [(float(x), float(y)) for x, y in arr[:4]]
    except Exception:
        return None
    return None


def _bbox(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _edge_min(points: list[tuple[float, float]]) -> float:
    """四边形最短边长（模块宽度估算是按边来算最稳）。"""
    total = len(points)
    edges = []
    for idx in range(total):
        x0, y0 = points[idx]
        x1, y1 = points[(idx + 1) % total]
        edges.append(math.hypot(x1 - x0, y1 - y0))
    return min(edges) if edges else 0.0


def _module_count(barcode: Any, kind: str) -> float:
    """从 zxing 的 extra/格式推断模块数；失败返回 0（调用方用兜底值）。"""
    fmt = str(getattr(barcode, "format", "") or "")
    extra = getattr(barcode, "extra", None)
    version = None
    if isinstance(extra, dict):
        version = extra.get("Version")
    try:
        version_int = int(version) if version is not None else 0
    except (TypeError, ValueError):
        version_int = 0
    if kind != "qrcode":
        return 0.0
    if fmt == "QR Code" and version_int >= 1:
        return float(21 + 4 * (version_int - 1))
    if "Micro QR" in fmt and version_int >= 1:
        return float(11 + 2 * (version_int - 1))
    return 0.0


def _extend_matrix_rect(points: list[tuple[float, float]], barcode: Any,
                        width: int, height: int) -> tuple[float, float, float, float]:
    """2D 码：按模块宽度外扩静区（QR 4 模块 / Micro QR 2 模块 / 其它 6%）。"""
    x0, y0, x1, y1 = _bbox(points)
    rect_w, rect_h = max(0.0, x1 - x0), max(0.0, y1 - y0)
    fmt = str(getattr(barcode, "format", "") or "")
    edge = _edge_min(points)
    modules = _module_count(barcode, "qrcode")
    if modules <= 0:
        # 不知道版本时按「典型 29 模块（QR v3 一侧）」保守估算
        modules = 29.0
    module_px = (edge / modules) if (edge > 0 and modules > 0) else 0.0
    if "Micro QR" in fmt:
        pad = _MICRO_QR_QUIET_MODULES * module_px
    elif fmt == "QR Code":
        pad = _QR_QUIET_MODULES * module_px
    else:  # Data Matrix / PDF417 / Aztec 等：按短边 6% 兜底（覆盖各自静区）
        pad = max(3.0, 0.06 * min(rect_w, rect_h) if min(rect_w, rect_h) > 0 else 3.0)
    pad = max(pad, 2.0)
    # 安全上限：不要把相邻内容整片盖掉
    pad = min(pad, 0.30 * max(rect_w, rect_h, 1.0))
    return _clamp_rect((x0 - pad, y0 - pad, x1 + pad, y1 + pad), width, height)


def _linear_module_count(barcode: Any) -> float:
    """1D 码总模块数估算（只用长度做几何，不保留解码内容）。"""
    fmt = str(getattr(barcode, "format", "") or "")
    try:
        text_len = len(getattr(barcode, "text", "") or "")
    except Exception:
        text_len = 0
    if fmt.startswith("EAN-13") or fmt.startswith("UPC-A"):
        return 95.0
    if fmt.startswith("EAN-8"):
        return 67.0
    if fmt.startswith("UPC-E"):
        return 51.0
    if "Code 128" in fmt:
        return float(max(60, 11 * text_len + 35))
    if "Code 39" in fmt:
        return float(max(30, 13 * text_len + 26))
    if "Code 93" in fmt:
        return float(max(30, 9 * text_len + 30))
    if "ITF" in fmt:
        return float(max(30, 9 * text_len + 20))
    return 80.0


def _extend_linear_rect(points: list[tuple[float, float]], barcode: Any,
                        width: int, height: int) -> tuple[float, float, float, float]:
    """1D 码：左右各外扩 ≥10 模块（静区），上下留少量保险。"""
    x0, y0, x1, y1 = _bbox(points)
    rect_w, rect_h = max(0.0, x1 - x0), max(0.0, y1 - y0)
    modules = _linear_module_count(barcode)
    module_px = (rect_w / modules) if (rect_w > 0 and modules > 0) else 0.0
    pad_x = max(2.0, _LINEAR_QUIET_MODULES * module_px)
    pad_x = min(pad_x, 0.25 * max(rect_w, 1.0))
    pad_y = max(2.0, 0.15 * rect_h)
    return _clamp_rect((x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y), width, height)


def _clamp_rect(rect: tuple[float, float, float, float],
                width: int, height: int) -> tuple[float, float, float, float]:
    x0 = max(0.0, min(float(width), rect[0]))
    y0 = max(0.0, min(float(height), rect[1]))
    x1 = max(0.0, min(float(width), rect[2]))
    y1 = max(0.0, min(float(height), rect[3]))
    return (x0, y0, x1, y1)


def _is_matrix_format(fmt: Any) -> bool:
    """zxing 码制 → 2D（注册为 qrcode 类）还是 1D。"""
    if zxingcpp is None:
        return False
    matrix_names = (
        "QRCode", "MicroQRCode", "RMQRCode", "DataMatrix",
        "PDF417", "MicroPDF417", "CompactPDF417",
        "Aztec", "AztecCode", "AztecRune", "MaxiCode",
    )
    try:
        return fmt in {getattr(zxingcpp.BarcodeFormat, name)
                       for name in matrix_names
                       if hasattr(zxingcpp.BarcodeFormat, name)}
    except Exception:
        return False


# ────────────────────────────── 命中构造 ──────────────────────────────

def _hit(kind: str, rect: tuple[float, float, float, float],
         confidence: float, note: str) -> mediascan.ScanHit | None:
    x0, y0, x1, y1 = rect
    if x1 - x0 < 2.0 or y1 - y0 < 2.0:
        return None
    return mediascan.ScanHit(kind=kind, rect=rect,
                             confidence=float(max(0.05, min(1.0, confidence))),
                             note=note)


def _merge_level(conf: float, default: float) -> float:
    try:
        return max(float(conf), float(default))
    except (TypeError, ValueError):
        return float(default)


def _dedupe(hits: list[mediascan.ScanHit]) -> list[mediascan.ScanHit]:
    """同一位置重复命中（多尺度/双引擎）合并；只并同 kind。返回按面积降序。"""
    merged: list[mediascan.ScanHit] = []
    for hit in hits:
        placed = False
        for item in merged:
            if item.kind != hit.kind:
                continue
            inter_w = min(item.rect[2], hit.rect[2]) - max(item.rect[0], hit.rect[0])
            inter_h = min(item.rect[3], hit.rect[3]) - max(item.rect[1], hit.rect[1])
            if inter_w <= 0 or inter_h <= 0:
                continue
            inter = inter_w * inter_h
            area_a = max(0.0, item.rect[2] - item.rect[0]) * max(0.0, item.rect[3] - item.rect[1])
            area_b = max(0.0, hit.rect[2] - hit.rect[0]) * max(0.0, hit.rect[3] - hit.rect[1])
            union = area_a + area_b - inter
            if union > 0 and inter / union >= 0.30:
                # 取并集（宁多涂不可漏），保留更高置信度，note 合并去重
                item.rect = (min(item.rect[0], hit.rect[0]), min(item.rect[1], hit.rect[1]),
                             max(item.rect[2], hit.rect[2]), max(item.rect[3], hit.rect[3]))
                item.confidence = max(item.confidence, hit.confidence)
                if hit.note and hit.note not in item.note:
                    item.note = (item.note + " " + hit.note).strip()
                placed = True
                break
        if not placed:
            merged.append(mediascan.ScanHit(hit.kind, hit.rect, hit.confidence, hit.note))
    merged.sort(key=lambda h: -((h.rect[2] - h.rect[0]) * (h.rect[3] - h.rect[1])))
    return merged[: _MAX_HITS]


# ────────────────────────────── 主引擎：zxing-cpp ──────────────────────────────

def _zxing_scan(gray: np.ndarray, width: int, height: int) -> list[mediascan.ScanHit]:
    """zxing 扫一张（子）图；坐标是子图坐标。"""
    if zxingcpp is None:
        return []
    try:
        found = zxingcpp.read_barcodes(
            gray,
            try_rotate=True,
            try_downscale=True,
            try_invert=True,
            return_errors=True,
        )
    except Exception as exc:  # 引擎异常→交给上层兜底；不带 payload
        raise RuntimeError(f"zxing 解码失败：{type(exc).__name__}") from None
    hits: list[mediascan.ScanHit] = []
    for barcode in found or []:
        try:
            points = _quad_points(getattr(barcode, "position", None))
            if not points:
                continue
            kind = "qrcode" if _is_matrix_format(getattr(barcode, "format", None)) else "barcode"
            valid = bool(getattr(barcode, "valid", True))
            fmt = str(getattr(barcode, "format", "") or "unknown")
            if kind == "qrcode":
                rect = _extend_matrix_rect(points, barcode, width, height)
            else:
                rect = _extend_linear_rect(points, barcode, width, height)
            note = f"{fmt}/zxing" + ("" if valid else "/detect")
            hit = _hit(kind, rect, _merge_level(0.95 if valid else 0.50, 0.5), note)
            if hit is not None:
                hits.append(hit)
        except Exception:
            continue
    return hits


def _tile_plan(width: int, height: int, edge: int, overlap: float,
               max_tiles: int) -> tuple[list[tuple[int, int, int, int]], bool]:
    """大图分块计划；返回 (tiles, truncated)。重叠用相对 tile 边长的比例。"""
    long_edge = max(width, height)
    if edge <= 0 or long_edge <= edge:
        return [(0, 0, width, height)], False
    step = max(1, int(edge * (1.0 - max(0.0, min(0.45, overlap)))))
    xs = list(range(0, max(1, width - edge + 1), step)) or [0]
    ys = list(range(0, max(1, height - edge + 1), step)) or [0]
    if xs[-1] + edge < width:
        xs.append(max(0, width - edge))
    if ys[-1] + edge < height:
        ys.append(max(0, height - edge))
    tiles = [(x, y, min(width, x + edge), min(height, y + edge)) for y in ys for x in xs]
    if len(tiles) > max_tiles:
        return tiles[:max_tiles], True
    return tiles, False


# ────────────────────────────── 兜底：cv2 ──────────────────────────────

def _cv2_scan(gray: np.ndarray, width: int, height: int) -> list[mediascan.ScanHit]:
    """cv2 兜底：QR（Aruco→普通）+ 1D；只在主引擎没结果时调用。"""
    if cv2 is None:
        return []
    max_edge = max(400, _env_int("YINZHAN_BARCODE_CV2_MAX_EDGE", 2200))
    work, scale = _downscale(gray, max_edge)
    work_w, work_h = work.shape[1], work.shape[0]
    hits: list[mediascan.ScanHit] = []

    def _scale_rect(rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        if scale == 1.0:
            return rect
        inv = 1.0 / scale
        return (rect[0] * inv, rect[1] * inv, rect[2] * inv, rect[3] * inv)

    # QR：优先 Aruco（低分辨率更稳），失败再普通检测器；都只做 detect（不依赖解码）
    qr_points: list[Any] = []
    for factory in ("QRCodeDetectorAruco", "QRCodeDetector"):
        detector_cls = getattr(cv2, factory, None)
        if detector_cls is None:
            continue
        try:
            detector = detector_cls()
            ok, points = detector.detectMulti(work)
        except Exception:
            continue
        if ok and points is not None:
            qr_points = list(points)
            break
    for points in qr_points:
        corners = _quad_points(points)
        if not corners:
            continue
        x0, y0, x1, y1 = _bbox(corners)
        pad = max(3.0, 0.08 * min(x1 - x0, y1 - y0) if min(x1 - x0, y1 - y0) > 0 else 3.0)
        rect = _scale_rect(_clamp_rect((x0 - pad, y0 - pad, x1 + pad, y1 + pad),
                                       work_w, work_h))
        hit = _hit("qrcode", rect, 0.50, "QR Code/cv2/detect")
        if hit is not None:
            hits.append(hit)

    # 1D：BarcodeDetector 检测+解码；再补一次 detect-only（解码失败也要框）
    linear_points: list[tuple[Any, str]] = []
    barcode_cls = getattr(getattr(cv2, "barcode", None), "BarcodeDetector", None)
    if barcode_cls is not None:
        try:
            detector = barcode_cls()
            ok, texts, types, points = detector.detectAndDecodeWithType(work)
            if ok and points is not None:
                for idx, point in enumerate(points):
                    label = str(types[idx]) if idx < len(types or []) else "1D"
                    linear_points.append((point, label or "1D"))
        except Exception:
            pass
        if not linear_points:
            try:
                detector = barcode_cls()
                result = detector.detect(work)
                if isinstance(result, tuple) and len(result) >= 2 and result[0]:
                    for point in result[1] or []:
                        linear_points.append((point, "1D"))
            except Exception:
                pass
    for point, label in linear_points:
        corners = _quad_points(point)
        if not corners:
            continue
        x0, y0, x1, y1 = _bbox(corners)
        pad_x = max(3.0, 0.10 * max(1.0, x1 - x0))
        pad_y = max(2.0, 0.20 * max(1.0, y1 - y0))
        rect = _scale_rect(_clamp_rect((x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y),
                                       work_w, work_h))
        hit = _hit("barcode", rect, 0.45, f"{label}/cv2")
        if hit is not None:
            hits.append(hit)

    return hits


# ────────────────────────────── 扫描器主入口 ──────────────────────────────

def scan_page(image_rgb: Any, ctx: mediascan.ScanContext) -> list[mediascan.ScanHit]:
    """mediascan 扫描器入口：输入页面 RGB 图，返回码类命中框（图像像素坐标）。"""
    t0 = time.perf_counter()
    gray = _as_gray(image_rgb)
    if gray is None:
        return []
    height, width = gray.shape[:2]
    engine = _env_str("YINZHAN_BARCODE_ENGINE", "auto")
    hits: list[mediascan.ScanHit] = []
    zxing_ok = zxingcpp is not None and engine in ("auto", "zxing")
    cv2_ok = cv2 is not None and engine in ("auto", "cv2")

    if zxing_ok:
        edge = max(512, _env_int("YINZHAN_BARCODE_TILE_EDGE", 4000))
        overlap = _env_float("YINZHAN_BARCODE_TILE_OVERLAP", 0.12)
        max_tiles = max(1, _env_int("YINZHAN_BARCODE_MAX_TILES", 16))
        tiles, truncated = _tile_plan(width, height, edge, overlap, max_tiles)
        if truncated:
            ctx.warnings.append(
                f"条码扫描：页面 {width}×{height} 分块数超过上限 {max_tiles}，"
                "仅扫描前若干块，可能漏检")
        for (tx0, ty0, tx1, ty1) in tiles:
            tile = gray[ty0:ty1, tx0:tx1]
            tile_h, tile_w = tile.shape[:2]
            try:
                tile_hits = _zxing_scan(tile, tile_w, tile_h)
            except Exception as exc:  # 单块失败不影响其它块
                ctx.warnings.append(f"条码扫描：{exc}")
                tile_hits = []
            if not tile_hits and _env_flag("YINZHAN_BARCODE_UPSCALE", False) \
                    and tile_w * tile_h <= 4_000_000:
                # 小图零命中时可选 2x 放大重试（默认关，避免倍增耗时）
                up = cv2.resize(tile, (tile_w * 2, tile_h * 2),
                                interpolation=cv2.INTER_CUBIC) if cv2 is not None else None
                if up is not None:
                    try:
                        for hit in _zxing_scan(up, up.shape[1], up.shape[0]):
                            hit.rect = (hit.rect[0] / 2.0, hit.rect[1] / 2.0,
                                        hit.rect[2] / 2.0, hit.rect[3] / 2.0)
                            tile_hits.append(hit)
                    except Exception:
                        pass
            for hit in tile_hits:
                hit.rect = (hit.rect[0] + tx0, hit.rect[1] + ty0,
                            hit.rect[2] + tx0, hit.rect[3] + ty0)
                hits.append(hit)

    if not hits and cv2_ok:
        try:
            hits.extend(_cv2_scan(gray, width, height))
        except Exception as exc:
            ctx.warnings.append(f"条码扫描：cv2 兜底失败（{type(exc).__name__}）")

    hits = _dedupe(hits)
    if _env_flag("YINZHAN_BARCODE_DEBUG", False):
        elapsed = (time.perf_counter() - t0) * 1000.0
        engines = "zxing" if zxing_ok else ""
        if not zxing_ok or (not hits and cv2_ok):
            engines = (engines + "+cv2").strip("+")
        ctx.warnings.append(
            f"条码扫描 {width}×{height} dpi={ctx.dpi:.0f} {elapsed:.0f}ms "
            f"命中 {len(hits)}（{engines or '无引擎'}）")
    return hits


# 注册到 mediascan：import 本模块即生效（registry.load_plugins 会 import）
mediascan.register_scanner(SCANNER_NAME, scan_page, kinds=KINDS, priority=60)


__all__ = [
    "SCANNER_NAME", "KINDS", "scan_page",
    "zxing_available", "cv2_available",
]
