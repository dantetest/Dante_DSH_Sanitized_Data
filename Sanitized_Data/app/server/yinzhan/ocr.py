"""OCR 服务：离线中文识别。

后端优先级：
  1. `rapidocr` (3.x) —— 自带 PP-OCRv6 small 模型，离线可用。
     实测比 v4 快约 3 倍、识别质量持平或更好。
  2. `rapidocr_onnxruntime` (1.x, PP-OCRv4) —— 老后端，兜底用。

坐标约定：recognize() 返回的 box 永远是「传入图片」的坐标系，
即使内部做了缩放 / 纠偏，也会反算回去。

性能与精度实测（6 核 CPU，A4 扫描页）：
    v4 @ 最长边 2339px → 5.4 秒/页
    v4 @ 最长边 1600px → 3.5 秒/页
    v6 @ 最长边 1600px → 1.9 秒/页，识别行数反而略多
超过约 1800px 会掉进检测模型的慢路径，所以统一先缩到 MAX_OCR_SIDE。

纠偏（deskew）：扫描件里 33% 的页有 0.75°~3° 倾斜，但实测对 PP-OCR 的
识别结果没有提升 —— 它的检测器会按多边形把每行拉正。所以默认关闭，
留着开关给别的引擎用。
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

try:  # cv2 是 OCR 依赖，正常都在
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

# 超过这个最长边就先缩小再 OCR；可用环境变量覆盖
MAX_OCR_SIDE = int(os.environ.get("YINZHAN_OCR_MAX_SIDE", "1600") or 1600)

# 扫描件预处理开关
DESKEW = os.environ.get("YINZHAN_OCR_DESKEW", "0") not in ("0", "false", "no")
CLAHE_ON = os.environ.get("YINZHAN_OCR_CLAHE", "1") not in ("0", "false", "no")
DESKEW_MIN = 0.3
DESKEW_LIMIT = 6.0
LOW_CONTRAST = 28.0


@dataclass
class OcrLine:
    text: str
    box: tuple[float, float, float, float]   # x0, y0, x1, y1
    score: float


# ────────────────────────── 预处理 ──────────────────────────

def _estimate_skew(gray: np.ndarray) -> float:
    if cv2 is None:
        return 0.0
    h, w = gray.shape[:2]
    if h < 200 or w < 200:
        return 0.0
    scale = 900.0 / max(h, w)
    small = (cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
             if scale < 1.0 else gray)
    try:
        _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        lines = cv2.morphologyEx(bw, cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3)))
    except Exception:
        return 0.0
    h2, w2 = lines.shape[:2]

    def score(ang: float) -> float:
        m = cv2.getRotationMatrix2D((w2 / 2.0, h2 / 2.0), ang, 1.0)
        rot = cv2.warpAffine(lines, m, (w2, h2), flags=cv2.INTER_NEAREST, borderValue=0)
        return float(rot.sum(axis=1, dtype=np.float64).var())

    best, best_s = 0.0, score(0.0)
    for ang in np.arange(-3.0, 3.001, 0.5):
        s = score(float(ang))
        if s > best_s:
            best, best_s = float(ang), s
    for ang in np.arange(best - 0.5, best + 0.501, 0.125):
        s = score(float(ang))
        if s > best_s:
            best, best_s = float(ang), s
    return best


def _deskew(arr: np.ndarray):
    if cv2 is None or not DESKEW:
        return arr, None
    try:
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        ang = _estimate_skew(gray)
        if abs(ang) < DESKEW_MIN or abs(ang) > DESKEW_LIMIT:
            return arr, None
        h, w = arr.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, 1.0)
        rot = cv2.warpAffine(arr, m, (w, h), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        return rot, m
    except Exception:
        return arr, None


def _enhance(arr: np.ndarray) -> np.ndarray:
    if cv2 is None or not CLAHE_ON:
        return arr
    try:
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        if float(gray.std()) >= LOW_CONTRAST:
            return arr
        lab = cv2.cvtColor(arr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
        return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
    except Exception:
        return arr


def _invert(m):
    if m is None or cv2 is None:
        return None
    try:
        return cv2.invertAffineTransform(m)
    except Exception:
        return None


def _apply2(m, x: float, y: float) -> tuple[float, float]:
    return (m[0][0] * x + m[0][1] * y + m[0][2],
            m[1][0] * x + m[1][1] * y + m[1][2])


def _resize_bgr(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    if cv2 is not None:
        return cv2.resize(arr, (w, h), interpolation=cv2.INTER_AREA)
    from PIL import Image
    rgb = Image.fromarray(arr[:, :, ::-1]).resize((w, h), Image.LANCZOS)
    return np.array(rgb)[:, :, ::-1].copy()


# ────────────────────────── 服务 ──────────────────────────

class OcrService:
    _inst: "OcrService | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._engine: Any = None
        self._backend: str = ""
        self._error: str | None = None
        self._ready = False
        self._init_lock = threading.Lock()
        self._run_lock = threading.Lock()   # engine 不是线程安全的，串行化

    @classmethod
    def instance(cls) -> "OcrService":
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    @property
    def available(self) -> bool:
        self._ensure()
        return self._ready

    @property
    def backend(self) -> str:
        self._ensure()
        return self._backend

    @property
    def error(self) -> str | None:
        self._ensure()
        return self._error

    def _ensure(self) -> None:
        if self._ready or self._error:
            return
        with self._init_lock:
            if self._ready or self._error:
                return
            # 老包会往 stderr 刷一堆 INFO，先压掉
            for name in ("RapidOCR", "rapidocr", "rapidocr_onnxruntime"):
                logging.getLogger(name).setLevel(logging.WARNING)
            errs = []
            try:
                logging.disable(logging.INFO)   # 初始化期间的模型加载日志也别刷屏
                from rapidocr import RapidOCR as RapidOCR6  # type: ignore
                self._engine = RapidOCR6()
                self._backend = "ppocrv6"
                self._ready = True
                return
            except Exception as exc:
                errs.append(f"rapidocr(3.x): {type(exc).__name__}: {exc}")
            finally:
                logging.disable(logging.NOTSET)
            try:
                logging.disable(logging.INFO)
                from rapidocr_onnxruntime import RapidOCR as RapidOCR4  # type: ignore
                self._engine = RapidOCR4()
                self._backend = "ppocrv4"
                self._ready = True
                return
            except Exception as exc:
                errs.append(f"rapidocr_onnxruntime: {type(exc).__name__}: {exc}")
            finally:
                logging.disable(logging.NOTSET)
            self._error = " | ".join(errs) or "没有可用的 OCR 后端"

    # ── 引擎调用，统一成 [(text, score, quad), ...] ──
    def _run_engine(self, arr: np.ndarray) -> list[tuple[str, float, Any]]:
        if self._backend == "ppocrv6":
            out = self._engine(arr)
            txts = list(getattr(out, "txts", []) or [])
            scores = list(getattr(out, "scores", []) or [])
            boxes = getattr(out, "boxes", None)
            res = []
            for i, t in enumerate(txts):
                sc = float(scores[i]) if i < len(scores) else 0.0
                quad = boxes[i] if boxes is not None and i < len(boxes) else None
                res.append((str(t), sc, quad))
            return res
        out = self._engine(arr)
        raw = out[0] if isinstance(out, tuple) else out
        res = []
        for item in (raw or []):
            try:
                res.append((str(item[1]), float(item[2]), item[0]))
            except Exception:
                continue
        return res

    def recognize(self, image: Any, min_score: float = 0.35,
                  deadline: float | None = None, should_stop=None) -> list[OcrLine]:
        """image: PIL.Image 或 numpy.ndarray(BGR)。返回原图坐标系的行。"""
        self._ensure()
        if not self._ready:
            return []
        if should_stop is not None and should_stop():
            return []
        rotate_m = None
        try:
            arr = self._to_bgr(image)
            if DESKEW:
                arr, rotate_m = _deskew(arr)
            if CLAHE_ON:
                arr = _enhance(arr)
            orig_h, orig_w = arr.shape[:2]
            scale = 1.0
            longest = max(orig_w, orig_h)
            if MAX_OCR_SIDE and longest > MAX_OCR_SIDE:
                scale = MAX_OCR_SIDE / float(longest)
                new_w = max(1, int(round(orig_w * scale)))
                new_h = max(1, int(round(orig_h * scale)))
                arr = _resize_bgr(arr, new_w, new_h)
            if should_stop is not None and should_stop():
                return []
            with self._run_lock:
                raw = self._run_engine(arr)
        except Exception:
            return []
        if not raw:
            return []
        inv = 1.0 if scale == 1.0 else (1.0 / scale)
        inv_m = _invert(rotate_m)
        out: list[OcrLine] = []
        for text, score, quad in raw:
            if not text or score < min_score or quad is None:
                continue
            try:
                pts = [(float(p[0]) * inv, float(p[1]) * inv) for p in quad]
            except Exception:
                continue
            if inv_m is not None:
                pts = [_apply2(inv_m, x, y) for x, y in pts]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            out.append(OcrLine(text=text,
                               box=(min(xs), min(ys), max(xs), max(ys)),
                               score=score))
        out.sort(key=lambda l: (round(l.box[1] / 8), l.box[0]))
        return out

    @staticmethod
    def _to_bgr(image: Any) -> np.ndarray:
        if isinstance(image, np.ndarray):
            return image
        arr = np.array(image.convert("RGB"))
        return arr[:, :, ::-1].copy()


def get_ocr() -> OcrService:
    return OcrService.instance()
