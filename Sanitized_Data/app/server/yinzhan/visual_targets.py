"""页面图像目标扫描器（P0）：人脸 / 证件整图 / 车牌。

由 :mod:`yinzhan.mediascan` 统一调度：本模块 **import 即注册**（见文件末尾），
解析器（image/tiff/pdf）在拿到页面 RGB 图后调用 ``mediascan.analyze()``，
命中框写进 ``Document.visual_marks``，再由 jobs 层转成 Entity 涂黑。

本文件只做「找」：不画图、不改文档、不碰规则包、不写磁盘、不联网。

坐标约定
--------
``scan()`` 收到的 ``image_rgb`` 是 HxWx3、uint8、**RGB** 顺序；返回的
``ScanHit.rect`` 一律是**同一张图的像素坐标** ``(x0, y0, x1, y1)``。
解析器负责把 OCR 文本框换算到同一坐标系后放进 ``ctx.options["text_boxes"]``
（结构 ``[{"text": str, "rect": (x0,y0,x1,y1)}, ...]``，可选
``ctx.options["text_boxes_scale"]`` 做整体缩放）。

P0 范围
-------
- ``face``：OpenCV 自带 Haar 级联（frontalface_default + profileface），
  0 新增模型/体积；长边缩到 **≈640** 检测（默认 ``YINZHAN_SCAN_FACE_MAX_SIDE=640``），
  框外扩 12%，同图去重；侧脸级联只在「正脸 0 命中且非白底文档页」时才加载/运行。
- ``id_document``：OCR 锚点判定（身份证/护照/驾驶证/营业执照/社保卡），
  命中后返回整证件区域；面积 ≥ 页面 25% 时返回整页（决策已批准）。
- ``plate``：优先 OCR 文本框 + 中国车牌正则（含新能源 8 位、挂/学/警后缀），
  扩边后只在 OCR 框附近的局部窗口用颜色（蓝/绿）底色修正；
  没有可读车牌文字时不做全图猜测（避免蓝底路牌误报、也控制大图耗时）。
- ``road_sign`` / ``door_address``：**P1 预留**，当前恒返回空（只注册 kind，
  不产生误报）；实现计划见 docs/impl/visual.md。

测试钩子：``_FACE_CASCADES_OVERRIDE`` 仅供 ``app/tests/test_visual.py`` 注入
假的级联对象，生产代码不要设置。
"""
from __future__ import annotations

import os
import re
import threading
import unicodedata
from typing import Any, Iterable, Sequence

import numpy as np

try:  # cv2 是 rapidocr 的依赖，正常都在；缺失时文本类检测仍可用
    import cv2
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

from . import mediascan
from .mediascan import ScanContext, ScanHit

Box = tuple[float, float, float, float]

#: 本扫描器可能返回的 kind（与 jobs._VISUAL_SPEC 对齐）
KINDS: tuple[str, ...] = (
    "face",
    "id_document",
    "plate",
    "road_sign",
    "door_address",
)

SCANNER_NAME = "visual_targets"


# ────────────────────────────── 小工具 ──────────────────────────────

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "none")


def _env_int(name: str, default: int, *, minimum: int = 1,
             maximum: int = 100000) -> int:
    try:
        value = int(str(os.environ.get(name, "") or default).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _ioc(inner: Box, outer: Box) -> float:
    """交集面积 / inner 面积（判断是否被整块盖住）。"""
    x0, y0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x1, y1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area = max(0.0, inner[2] - inner[0]) * max(0.0, inner[3] - inner[1])
    return inter / area if area > 0 else 0.0


def _iou(a: Box, b: Box) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _clamp_box(box: Box, width: float, height: float) -> Box:
    return (max(0.0, min(width, box[0])), max(0.0, min(height, box[1])),
            max(0.0, min(width, box[2])), max(0.0, min(height, box[3])))


def _expand_box(box: Box, ratio: float, width: float, height: float) -> Box:
    """按宽/高比例外扩（人脸 0.12=12%，车牌 0.08）。"""
    x0, y0, x1, y1 = box
    dx = max(0.0, (x1 - x0)) * ratio
    dy = max(0.0, (y1 - y0)) * ratio
    return _clamp_box((x0 - dx, y0 - dy, x1 + dx, y1 + dy), width, height)


def _norm_text(value: Any) -> str:
    try:
        text = unicodedata.normalize("NFKC", str(value or ""))
    except Exception:
        text = str(value or "")
    return text.strip()


def _as_rect(value: Any) -> Box | None:
    try:
        x0, y0, x1, y1 = (float(v) for v in list(value)[:4])
    except Exception:
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if not all(np.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    return (x0, y0, x1, y1)


def iter_text_boxes(ctx: ScanContext) -> list[tuple[str, Box]]:
    """从 ctx.options 取 OCR 文本框，统一成 ``[(text, rect_px), ...]``。

    兼容三种写法：dict ``{"text","rect"}``、``(text, rect)`` / ``(rect, text)``
    二元组、以及带 ``.text/.rect`` 属性的对象；坐标先按
    ``text_boxes_scale`` 缩放，再被裁进图像边界。
    """
    options = getattr(ctx, "options", None) or {}
    raw = options.get("text_boxes") or options.get("ocr_boxes") or []
    try:
        scale = float(options.get("text_boxes_scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    width, height = getattr(ctx, "image_size", (0, 0)) or (0, 0)
    out: list[tuple[str, Box]] = []
    for item in raw:
        text: Any = ""
        rect: Any = None
        try:
            if isinstance(item, dict):
                text, rect = item.get("text", ""), item.get("rect")
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                first, second = item[0], item[1]
                if isinstance(first, str):
                    text, rect = first, second
                else:
                    rect, text = first, second
            else:
                text = getattr(item, "text", "")
                rect = getattr(item, "rect", None)
        except Exception:
            continue
        text = _norm_text(text)
        box = _as_rect(rect)
        if not text or box is None:
            continue
        if scale != 1.0:
            box = (box[0] * scale, box[1] * scale, box[2] * scale, box[3] * scale)
        x0 = max(0.0, box[0])
        y0 = max(0.0, box[1])
        x1 = min(float(width), box[2]) if width else box[2]
        y1 = min(float(height), box[3]) if height else box[3]
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        out.append((text, (x0, y0, x1, y1)))
    return out


def _pick_text_boxes(rgb: np.ndarray, ctx: ScanContext):
    """返回 (width, height, text_boxes)。图像尺寸优先取真实 ndarray。"""
    try:
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
    except Exception:
        width, height = getattr(ctx, "image_size", (0, 0)) or (0, 0)
    return width, height, iter_text_boxes(ctx)


# ────────────────────────────── 人脸（Haar，P0） ──────────────────────────────

_FACE_CASCADE_FILES = {
    "frontal": "haarcascade_frontalface_default.xml",
    "profile": "haarcascade_profileface.xml",
}

_FACE_LOCK = threading.RLock()
_CASCADE_TL = threading.local()

#: 测试用注入：``[(label, fake_cascade, base_confidence), ...]``
_FACE_CASCADES_OVERRIDE: list | None = None


def _cascade(kind: str):
    """线程内缓存 CascadeClassifier（多任务并发解析时不共享检测器对象）。"""
    if cv2 is None:
        return None
    cache = getattr(_CASCADE_TL, "cascades", None)
    if cache is None:
        cache = {}
        _CASCADE_TL.cascades = cache
    if kind not in cache:
        try:
            path = os.path.join(cv2.data.haarcascades, _FACE_CASCADE_FILES[kind])
            cache[kind] = cv2.CascadeClassifier(path)
        except Exception:
            cache[kind] = None
    cascade = cache[kind]
    if cascade is None:
        return None
    try:
        if getattr(cascade, "empty", lambda: False)():
            return None
    except Exception:
        pass
    return cascade


def _face_cascades(include_profile: bool = True) -> list[tuple[str, Any, float]]:
    if _FACE_CASCADES_OVERRIDE is not None:
        entries = list(_FACE_CASCADES_OVERRIDE)
        if include_profile:
            return entries
        return [item for item in entries if item[0] != "profile"]
    out: list[tuple[str, Any, float]] = []
    frontal = _cascade("frontal")
    if frontal is not None:
        out.append(("frontal", frontal, 0.92))
    if include_profile:
        profile = _cascade("profile")
        if profile is not None:
            out.append(("profile", profile, 0.78))
    return out


def _document_like(gray: np.ndarray) -> bool:
    """白底文档页判断：用于跳过代价高的侧脸级联（文档扫描页基本用不上）。"""
    try:
        white = float((gray > 235).mean())
        return white >= 0.72 and float(gray.std()) < 90.0
    except Exception:
        return False


def _face_confidence(base: float, face_px: float, label: str) -> float:
    """置信度按「检测框在原图中的最小边长」换算：越小越不可信。"""
    conf = float(base)
    if face_px < 32:
        conf *= 0.72
    elif face_px < 48:
        conf *= 0.85
    elif face_px < 64:
        conf *= 0.95
    if label.startswith("profile"):
        conf *= 0.96
    return max(0.35, min(0.98, conf))


def _face_scan_side() -> int:
    """人脸检测的长边上限。默认 640：1000×620 名片 ≈ 0.6 倍，单页 <100ms。"""
    cap = _env_int("YINZHAN_SCAN_FACE_MAX_SIDE", 640, minimum=320, maximum=6000)
    generic = _env_int("YINZHAN_SCAN_MAX_SIDE", 3000, minimum=320, maximum=10000)
    return min(cap, generic)


def _detect_faces(rgb: np.ndarray, ctx: ScanContext) -> list[ScanHit]:
    if cv2 is None:
        return []
    try:
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
    except Exception:
        return []
    if height < 32 or width < 32:
        return []
    cap = _face_scan_side()
    long_edge = max(height, width)
    scale = min(1.0, cap / float(long_edge))
    if scale < 1.0:
        scan = cv2.resize(
            rgb,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        scan = rgb
    try:
        raw_gray = cv2.cvtColor(scan, cv2.COLOR_RGB2GRAY)
    except Exception:
        return []
    if float(raw_gray.std()) < 2.0:      # 纯色页/黑页：没有可检测的内容
        return []
    doc_like = _document_like(raw_gray)
    gray = cv2.equalizeHist(raw_gray)
    flipped = cv2.flip(gray, 1) if gray is not None else None

    profile_mode = str(os.environ.get("YINZHAN_SCAN_FACE_PROFILE", "auto")).strip().lower()
    profile_allowed = profile_mode not in ("0", "false", "no", "off", "none")
    # auto：先跑正脸；正脸 0 命中且不是白底文档页时才加载侧脸级联
    # （侧脸级联 XML 828KB，加载 + 多跑两遍在文档页上是纯浪费）。
    always_profile = profile_allowed and profile_mode not in ("auto",)
    auto_profile = profile_allowed and not always_profile and not doc_like

    found: list[tuple[Box, float, str]] = []

    def _collect(label: str, cascade: Any, base: float,
                 flipped_source: bool = False) -> None:
        try:
            dets = cascade.detectMultiScale(
                flipped if flipped_source else gray,
                scaleFactor=1.1, minNeighbors=5, minSize=(24, 24),
            )
        except Exception:
            return
        for det in dets if dets is not None else []:
            try:
                x, y, fw, fh = (float(v) for v in list(det)[:4])
            except Exception:
                continue
            if fw <= 0 or fh <= 0:
                continue
            src_w = float(flipped.shape[1]) if flipped_source else float(gray.shape[1])
            x_scan = (src_w - x - fw) if flipped_source else x
            rect: Box = (x_scan / scale, y / scale,
                         (x_scan + fw) / scale, (y + fh) / scale)
            face_px = min(fw / scale, fh / scale)
            label_note = "profile-flip" if flipped_source else label
            found.append((rect, _face_confidence(base, face_px, label_note),
                          label_note))

    # 第一轮：只跑正脸级联（最常用、也最便宜）
    for label, cascade, base in _face_cascades(include_profile=False):
        _collect(label, cascade, base, flipped_source=False)

    # 第二轮：需要时再加载侧脸级联（auto：正脸 0 命中且非文档页）
    if always_profile or (auto_profile and not found):
        for label, cascade, base in _face_cascades(include_profile=True):
            if label != "profile":
                continue
            _collect("profile", cascade, base, flipped_source=False)
            if flipped is not None:
                _collect("profile", cascade, base, flipped_source=True)

    if not found:
        return []
    # 去重：置信度高者优先；同脸多尺度/正脸+侧脸重复命中只留一个
    found.sort(key=lambda item: item[1], reverse=True)
    kept: list[tuple[Box, float, str]] = []
    for rect, conf, label in found:
        box = _expand_box(rect, 0.12, float(width), float(height))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            continue
        if any(_iou(box, prev[0]) >= 0.35 or _ioc(box, prev[0]) >= 0.85
               for prev in kept):
            continue
        kept.append((box, conf, label))
    return [
        ScanHit(kind="face", rect=box, confidence=round(conf, 3),
                note=f"engine=haar;{label};scale={scale:.3f}")
        for box, conf, label in kept
    ]


# ────────────────────────────── 证件整图（OCR 锚点，P0） ──────────────────────────────

# 判定原则：宁可不报，不可把合同正文误判成证件（红线条：不得破坏既有样例）。
# - 只有「证件名/专属字段」这类强锚点才允许单独触发；
# - 「统一社会信用代码/有效期限/银行/身份证号码」这类在合同里也常见的词只能做
#   弱证据，必须和证件名一起出现；
# - 营业执照要求先命中标题「营业执照」。
_ID_DOC_SPECS: dict[str, dict[str, Any]] = {
    "id_card": {
        "strong": ("居民身份证", "公民身份号码"),
        "weak": ("签发机关", "有效期限", "住址", "出生", "民族", "性别"),
        "title": (),
    },
    "passport": {
        "strong": ("中华人民共和国护照", "护照号码"),
        "weak": ("签发日期", "出生日期", "签发机关"),
        "title": (),
    },
    "driver_license": {
        "strong": ("机动车驾驶证", "准驾车型"),
        "weak": ("证号", "有效期限", "初次领证日期"),
        "title": (),
    },
    "business_license": {
        "strong": ("营业执照编号",),
        "medium": ("统一社会信用代码",),
        "weak": ("注册资本", "法定代表人", "经营范围", "成立日期",
                 "营业期限", "名称", "住所"),
        "title": ("营业执照",),
    },
    "social_security": {
        "strong": ("社会保障卡", "医保卡", "社会保障号码"),
        "weak": ("卡号", "有效期"),
        "title": (),
    },
}

_MRZ_RE = re.compile(r"P<CHN[A-Z0-9<]{8,40}")
_MRZ_LOOSE_RE = re.compile(r"^[A-Z0-9<]{30,44}$")


def _find_anchor_boxes(text_boxes: Sequence[tuple[str, Box]],
                       anchors: Iterable[str]) -> list[tuple[str, Box]]:
    """返回 ``[(命中的文本框原文, rect)]``；原文用于判断「标题样短文本」。"""
    normalized = [(_norm_text(text).replace(" ", ""), rect)
                  for text, rect in text_boxes]
    hits: list[tuple[str, Box]] = []
    for anchor in anchors:
        needle = _norm_text(anchor).replace(" ", "")
        if not needle:
            continue
        for text, rect in normalized:
            if needle in text:
                hits.append((text, rect))
    return hits


def _find_mrz(text_boxes: Sequence[tuple[str, Box]]) -> list[tuple[str, Box]]:
    hits: list[tuple[str, Box]] = []
    for text, rect in text_boxes:
        compact = re.sub(r"\s+", "", _norm_text(text).upper())
        if not compact:
            continue
        if _MRZ_RE.search(compact) or (
                _MRZ_LOOSE_RE.match(compact) and "<" in compact
                and any(ch.isdigit() for ch in compact)):
            hits.append(("MRZ", rect))
    return hits


def _id_region(anchor_boxes: Sequence[Box], all_boxes: Sequence[tuple[str, Box]],
               width: float, height: float) -> Box:
    """锚点并集 + 邻域字段扩张，得到证件区域（像素坐标）。"""
    if not anchor_boxes:
        return (0.0, 0.0, float(width), float(height))
    x0 = min(b[0] for b in anchor_boxes)
    y0 = min(b[1] for b in anchor_boxes)
    x1 = max(b[2] for b in anchor_boxes)
    y1 = max(b[3] for b in anchor_boxes)
    # 先纳入落在锚点并集附近的字段框（姓名/住址/注册资本等与锚点常分块）
    try:
        pad_x = max(12.0, (x1 - x0) * 0.30)
        pad_y = max(12.0, (y1 - y0) * 0.35)
        ex0, ey0 = x0 - pad_x, y0 - pad_y
        ex1, ey1 = x1 + pad_x, y1 + pad_y
        for _text, rect in all_boxes:
            cx = (rect[0] + rect[2]) / 2.0
            cy = (rect[1] + rect[3]) / 2.0
            if ex0 <= cx <= ex1 and ey0 <= cy <= ey1:
                x0, y0 = min(x0, rect[0]), min(y0, rect[1])
                x1, y1 = max(x1, rect[2]), max(y1, rect[3])
    except Exception:
        pass
    pad = max(4.0, 0.025 * min(float(width), float(height)))
    return _clamp_box((x0 - pad, y0 - pad, x1 + pad, y1 + pad),
                      float(width), float(height))


def _detect_id_document(rgb: np.ndarray, ctx: ScanContext,
                        text_boxes: Sequence[tuple[str, Box]]) -> list[ScanHit]:
    if not text_boxes:
        return []
    width, height = int(rgb.shape[1]), int(rgb.shape[0])
    if width < 32 or height < 32:
        return []
    best: tuple[float, str, list[Box], str] | None = None   # conf,type,anchors,note

    for doc_type, spec in _ID_DOC_SPECS.items():
        strong = _find_anchor_boxes(text_boxes, spec.get("strong", ()))
        medium = _find_anchor_boxes(text_boxes, spec.get("medium", ()))
        weak = _find_anchor_boxes(text_boxes, spec.get("weak", ()))
        title = _find_anchor_boxes(text_boxes, spec.get("title", ()))
        if doc_type == "passport":
            mrz = _find_mrz(text_boxes)
            if mrz:
                strong = list(strong) + mrz
        n_strong, n_medium, n_weak = len(strong), len(medium), len(weak)
        triggered = False
        conf = 0.0
        if spec.get("title"):
            # 营业执照：必须命中「标题样」的短文本框（「营业执照（副本）」），
            # 且至少再有一个字段锚点；正文里出现「营业执照复印件」不算。
            short_title = [(text, rect) for text, rect in title if len(text) <= 14]
            if short_title and (n_medium + n_weak) >= 1:
                triggered = True
                title = short_title
                conf = 0.86 + min(0.08, 0.02 * (n_strong + n_medium + n_weak))
        if not triggered:
            if n_strong >= 1:
                triggered = True
                conf = 0.90 + min(0.05, 0.025 * (n_strong - 1))
            elif n_medium >= 2:
                triggered = True
                conf = 0.80 + 0.03 * min(2, n_weak)
            # 其它组合（例如合同头部的「统一社会信用代码」+「法定代表人」）一律不触发：
            # 这类词在合同/对账单里太常见，宁可漏报证件，也不能把正文整页涂黑。
        if not triggered:
            continue
        anchor_rects = [rect for _name, rect in
                        (list(strong) + list(medium) + list(weak) + list(title))]
        region = _id_region(anchor_rects, text_boxes, width, height)
        area = max(0.0, region[2] - region[0]) * max(0.0, region[3] - region[1])
        page_area = float(width) * float(height) or 1.0
        whole_page = area >= 0.25 * page_area
        if whole_page:
            region = (0.0, 0.0, float(width), float(height))
            conf = min(0.98, conf + 0.02)
        note = (f"engine=ocr;doc={doc_type};n={n_strong + n_medium + n_weak}"
                f"{';whole_page' if whole_page else ''}")
        cand = (conf, doc_type, [region], note)
        if best is None or cand[0] > best[0]:
            best = cand

    if best is None:
        return []
    conf, doc_type, regions, note = best
    return [ScanHit(kind="id_document", rect=regions[0],
                    confidence=round(conf, 3), note=note)]


# ────────────────────────────── 车牌（OCR + 颜色修正，P0） ──────────────────────────────

_PROVINCES = "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领"
_PLATE_ALNUM = "A-HJ-NP-Z0-9"      # 号牌字符集：不含 I / O

#: 依次匹配：新能源 8 位（省+字母+6）→ 警车 8 位（省+字母+字母+3数字+警）→
#: 普通 7 位（省+字母+5）→ 后缀号牌 7 位（省+字母+4位+挂/学/警/港/澳）
_PLATE_RE = re.compile(
    rf"(?<![A-Z0-9])("
    rf"[{_PROVINCES}][A-HJ-NP-Z][{_PLATE_ALNUM}]{{6}}"
    rf"|[{_PROVINCES}][A-HJ-NP-Z][A-HJ-NP-Z][A-HJ-NP-Z0-9]{{3}}警"
    rf"|[{_PROVINCES}][A-HJ-NP-Z][{_PLATE_ALNUM}]{{5}}"
    rf"|[{_PROVINCES}][A-HJ-NP-Z][{_PLATE_ALNUM}]{{4}}[挂学警港澳]"
    rf")(?![A-Z0-9])"
)

_PLATE_PREFIX_RE = re.compile(r"^(?:机动车号牌|号牌号码|车牌号码|车牌号|车牌|牌照)")


def _normalize_plate_text(text: str) -> str:
    """NFKC + 去分隔符 + 大小写归一 + OCR 易混字符纠正（I→1, O→0）。"""
    try:
        value = unicodedata.normalize("NFKC", str(text or "")).upper()
    except Exception:
        value = str(text or "").upper()
    value = re.sub(r"[\s·•.\-_—~:：]+", "", value)
    value = value.replace("I", "1").replace("O", "0")
    value = _PLATE_PREFIX_RE.sub("", value)
    return value


def _plate_hits_from_text(text_boxes: Sequence[tuple[str, Box]],
                          width: float, height: float) -> list[tuple[Box, float, str, str]]:
    hits: list[tuple[Box, float, str, str]] = []
    for text, rect in text_boxes:
        compact = _normalize_plate_text(text)
        if not compact or len(compact) > 12:
            continue
        match = _PLATE_RE.search(compact)
        if not match:
            continue
        value = match.group(1)
        box = _expand_box(rect, 0.08, width, height)
        hits.append((box, 0.95, value, "ocr"))
    return hits


def _plate_color_candidates(rgb: np.ndarray) -> list[Box]:
    """蓝色/绿色车牌候选框（像素坐标，基于传入图）。只做候选，不直接算命中。"""
    if cv2 is None or rgb.size == 0:
        return []
    try:
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    except Exception:
        return []
    masks = []
    # 蓝牌：色相 100~135；绿牌（新能源渐变绿）：45~95
    masks.append(cv2.inRange(hsv, (100, 80, 40), (135, 255, 255)))
    masks.append(cv2.inRange(hsv, (45, 60, 40), (95, 255, 255)))
    h, w = rgb.shape[:2]
    out: list[Box] = []
    for mask in masks:
        try:
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                    np.ones((5, 5), np.uint8))
            count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
        except Exception:
            continue
        for index in range(1, count):
            x, y, bw, bh, area = (int(v) for v in stats[index][:5])
            if bw < 40 or bh < 12 or area < 800:
                continue
            aspect = bw / float(bh)
            if not 2.0 <= aspect <= 4.6:
                continue
            if bw > w * 0.98 or bh > h * 0.60:      # 整页底色块不算车牌
                continue
            crop = rgb[y:y + bh, x:x + bw]
            if crop.size == 0:
                continue
            try:
                crop_hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
                white_ratio = float(((crop_hsv[:, :, 1] < 70)
                                     & (crop_hsv[:, :, 2] > 140)).mean())
            except Exception:
                continue
            if white_ratio < 0.04:                 # 车牌上要有白字
                continue
            out.append((float(x), float(y), float(x + bw), float(y + bh)))
    return out


def _detect_plates(rgb: np.ndarray, ctx: ScanContext,
                   text_boxes: Sequence[tuple[str, Box]]) -> list[ScanHit]:
    width, height = int(rgb.shape[1]), int(rgb.shape[0])
    if width < 32 or height < 32:
        return []
    # 1) OCR 文本命中（主路径，置信度高）
    text_hits = _plate_hits_from_text(text_boxes, float(width), float(height))
    # 2) 颜色修正：只在已读出的车牌 OCR 框附近开一个局部窗口找车牌底色，
    #    不做全图 HSV 扫描（12MP 照片全图扫描要几十毫秒且容易把蓝底路牌算进来）。
    color_hits: list[tuple[Box, float, str, str]] = []
    if _env_bool("YINZHAN_SCAN_PLATE_COLOR", True) and text_hits and cv2 is not None:
        height, width = rgb.shape[:2]
        for box, _conf, value, _engine in text_hits:
            span_x = max(8.0, box[2] - box[0])
            span_y = max(8.0, box[3] - box[1])
            wx0 = int(max(0.0, box[0] - span_x * 0.8))
            wy0 = int(max(0.0, box[1] - span_y * 1.2))
            wx1 = int(min(float(width), box[2] + span_x * 0.8))
            wy1 = int(min(float(height), box[3] + span_y * 1.2))
            if wx1 - wx0 < 24 or wy1 - wy0 < 12:
                continue
            crop = rgb[wy0:wy1, wx0:wx1]
            try:
                candidates = _plate_color_candidates(crop)
            except Exception:
                candidates = []
            tcx = (box[0] + box[2]) / 2.0
            tcy = (box[1] + box[3]) / 2.0
            for cand in candidates:
                box_cand = (cand[0] + wx0, cand[1] + wy0,
                            cand[2] + wx0, cand[3] + wy0)
                if not (box_cand[0] <= tcx <= box_cand[2]
                        and box_cand[1] <= tcy <= box_cand[3]):
                    continue
                cand_area = ((box_cand[2] - box_cand[0])
                             * (box_cand[3] - box_cand[1]))
                text_area = max(1.0, span_x * span_y)
                if cand_area <= 8.0 * text_area:   # 色块不能大到像路牌/背景
                    refined = _expand_box(box_cand, 0.02, float(width), float(height))
                    color_hits.append((refined, 0.93, value, "color+ocr"))
                break

    hits = list(text_hits) + color_hits
    if not hits:
        return []
    # 去重：同一车牌既被 OCR 命中又被颜色框修正时，保留面积更大的修正框
    hits.sort(key=lambda item: ((item[0][2] - item[0][0]) * (item[0][3] - item[0][1])),
              reverse=True)
    kept: list[tuple[Box, float, str, str]] = []
    for box, conf, value, engine in hits:
        if any(_iou(box, prev[0]) >= 0.30 or _ioc(box, prev[0]) >= 0.80
               for prev in kept):
            continue
        kept.append((box, conf, value, engine))
    return [
        ScanHit(kind="plate", rect=box, confidence=round(conf, 3),
                note=(f"engine={engine}" + (f";plate={value}" if value else "")))
        for box, conf, value, engine in kept
    ]


# ────────────────────────────── 路牌 / 门址（P1 预留） ──────────────────────────────

def _detect_road_sign(rgb: np.ndarray, ctx: ScanContext,
                      text_boxes: Sequence[tuple[str, Box]]) -> list[ScanHit]:
    """P1 预留：恒返回空。实现计划见 docs/impl/visual.md（路名模式 + 色块整块涂黑）。"""
    return []


def _detect_door_address(rgb: np.ndarray, ctx: ScanContext,
                         text_boxes: Sequence[tuple[str, Box]]) -> list[ScanHit]:
    """P1 预留：恒返回空。实现计划见 docs/impl/visual.md（无行政区门牌 + 跨行合并）。"""
    return []


# ────────────────────────────── 总入口 ──────────────────────────────

def _prune_nested(hits: list[ScanHit]) -> list[ScanHit]:
    """证件整图命中后，落在其中的脸/车牌框不再单独上报（避免重复实体）。"""
    docs = [hit for hit in hits if hit.kind == "id_document"]
    if not docs:
        return hits
    out: list[ScanHit] = []
    for hit in hits:
        if hit.kind == "id_document":
            out.append(hit)
            continue
        if any(_ioc(hit.rect, doc.rect) >= 0.85 for doc in docs):
            continue
        out.append(hit)
    return out


def scan(image_rgb: Any, ctx: ScanContext) -> list[ScanHit]:
    """mediascan 扫描器入口：一张页面图 → 目标命中列表（像素坐标）。"""
    if image_rgb is None or not isinstance(image_rgb, np.ndarray):
        return []
    if image_rgb.ndim != 3 or image_rgb.shape[2] < 3:
        return []
    hits: list[ScanHit] = []
    text_boxes: list[tuple[str, Box]] = []

    if _env_bool("YINZHAN_SCAN_FACE", True):
        try:
            hits.extend(_detect_faces(image_rgb, ctx))
        except Exception as exc:      # 单类失败不能带崩其它目标
            ctx.warnings.append(f"visual_targets:face 失败：{exc}")

    try:
        _width, _height, text_boxes = _pick_text_boxes(image_rgb, ctx)
    except Exception:
        text_boxes = []
        ctx.warnings.append("visual_targets: OCR 文本框不可读")

    if text_boxes and _env_bool("YINZHAN_SCAN_ID_DOC", True):
        try:
            hits.extend(_detect_id_document(image_rgb, ctx, text_boxes))
        except Exception as exc:
            ctx.warnings.append(f"visual_targets:id_document 失败：{exc}")

    if _env_bool("YINZHAN_SCAN_PLATE", True):
        try:
            hits.extend(_detect_plates(image_rgb, ctx, text_boxes))
        except Exception as exc:
            ctx.warnings.append(f"visual_targets:plate 失败：{exc}")

    if text_boxes:
        try:
            hits.extend(_detect_road_sign(image_rgb, ctx, text_boxes))
            hits.extend(_detect_door_address(image_rgb, ctx, text_boxes))
        except Exception as exc:
            ctx.warnings.append(f"visual_targets:road/door 失败：{exc}")

    return _prune_nested(hits)


# import 即注册（mediascan 按 name 幂等替换；重复 import 不会重复注册）
def _warmup_face() -> None:
    """预热 Haar 级联。

    首次 ``detectMultiScale`` 在部分 OpenCV 构建上要 300~500ms 建并行池/缓冲区
    （第二次起 15~20ms）。放在模块 import 期（``registry.load_plugins()``）做，
    把一次性的初始化成本从「每个文件的解析耗时」里挪走；失败不影响正常使用。
    """
    if cv2 is None:
        return
    try:
        cascade = _cascade("frontal")
        if cascade is None:
            return
        warm = np.full((96, 128), 128, np.uint8)
        cascade.detectMultiScale(warm, scaleFactor=1.1, minNeighbors=5,
                                 minSize=(24, 24))
    except Exception:      # pragma: no cover - 预热失败只影响首帧速度
        return


if _env_bool("YINZHAN_SCAN_PRELOAD", True):
    _warmup_face()

mediascan.register_scanner(SCANNER_NAME, scan, kinds=KINDS, priority=60)

__all__ = [
    "KINDS", "SCANNER_NAME", "scan", "iter_text_boxes",
    "_detect_faces", "_detect_id_document", "_detect_plates",
    "_detect_road_sign", "_detect_door_address",
    "_FACE_CASCADES_OVERRIDE",
]
