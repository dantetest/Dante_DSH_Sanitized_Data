"""P1-A 门牌 / 路牌 / 店招 / 单位铭牌扫描器（OCR 锚点）。

由 :mod:`yinzhan.mediascan` 统一调度：本模块 **import 即注册**（见文件末尾）。
解析器把 OCR 文本框放进 ``ctx.options["text_boxes"]``（像素坐标，结构
``[{"text": str, "rect": (x0,y0,x1,y1)}, ...]``，可选 ``text_boxes_scale``），
本扫描器只做「从 OCR 文本里找招牌类目标」，不画图、不落盘、不联网。

范围（P1，保守）
----------------
- **只承诺 OCR 可读的招牌/门牌/路牌**：没有 OCR 文本框（纯图像/未开 OCR）直接返回空；
- 白纸类页面（名片、发票、合同正文、文档扫描页）**整页跳过**：这些页面 90% 以上
  是近白低饱和像素，招牌类目标不应在纸面正文里命中（避免把地址/公司名当门牌）；
- 四个 kind：``door_address`` / ``road_sign`` / ``shop_sign`` / ``org_plaque``；
- 命中必须同时满足：锚点词 + 文本短且无句读 + 字高显著（相对页面/同页文本）；
- 同一区域合并、去重；rect 外扩 6px 并 clamp 到页面内；
- ``note`` 只写 ``engine=ocr;kind=...;reason=...;anchors=N``，不写识别值。

已知限制
--------
- 近白背景上的白色招牌（纸面/白墙）会被白纸门控跳过（宁可不报，不误报）；
- 手写、艺术字、竖排招牌、低分辨率/强反光照片不承诺；
- OCR 把招牌文字拆碎时，只有能形成锚点/相邻合并的才返回。
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
from typing import Any, Iterable

import numpy as np

try:  # cv2 是 rapidocr 的依赖，正常都在；缺失时退化为 numpy 纸面判断
    import cv2
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

from . import mediascan
from .mediascan import ScanContext, ScanHit

Box = tuple[float, float, float, float]

KINDS: tuple[str, ...] = ("road_sign", "door_address", "shop_sign", "org_plaque")
SCANNER_NAME = "sign_targets"

#: 命中框外扩（任务要求 4–6px；统一取 6px，图片坐标）
_PAD = 6.0

#: 白纸页门控：近白且低饱和像素占比超过该值视为文档/名片/发票/纸面
_PAPER_RATIO = 0.55
_PAPER_VALUE_MIN = 228
_PAPER_SAT_MAX = 42

#: 文本长度限制
_MAX_TEXT_LEN = 36

#: 句读/标签：正文、表单、名片字段一律不当作招牌
_PUNCT_RE = re.compile(r"[。；;，,、！!？?：:]")
_LABEL_PREFIXES = (
    "地址", "住址", "住所", "通讯地址", "联系地址", "注册地址", "经营场所",
    "生产地址", "送达地址", "名称", "单位名称", "公司名称", "纳税人", "税号",
    "统一社会信用代码", "开户行", "账号", "电话", "手机", "邮箱", "联系人",
    "甲方", "乙方", "委托人", "受托人", "购买方", "销售方", "发票", "日期",
    "编号", "身份证", "护照", "案号", "合同编号", "合同金额", "金额", "备注",
)

_ROAD_SUFFIX = ("路", "街", "大街", "大道", "巷", "弄", "道", "高速", "立交", "桥", "环线")
_DIRECTION = ("东行", "西行", "南行", "北行", "方向", "入口", "出口", "前方",
              "左转", "右转", "直行", "公里", "km", "KM", "米", "停车", "P")
_ADDR_NUM_RE = re.compile(r"\d{1,5}\s*(号|栋|幢|单元|室|楼|层|座|院|弄|区|排|组)")
_SHOP_TERMS = ("便利店", "超市", "药房", "药店", "餐厅", "餐饮", "咖啡", "酒店",
               "宾馆", "网吧", "美发", "理发", "洗衣", "维修", "门诊", "诊所",
               "商场", "市场", "银行", "营业", "招牌", "便民", "小吃", "火锅",
               "烧烤", "面包", "水果", "菜场", "市场", "店", "馆", "营业时间")
_LEGAL_SUFFIX = ("有限公司", "有限责任公司", "股份有限公司", "集团")
_ORG_TERMS = ("公司", "单位", "委员会", "学校", "学院", "医院", "事务所",
              "研究院", "研究所", "设计院", "工作室", "协会", "基金会",
              "中心", "局", "厂", "站")
_ORG_STRONG = ("有限公司", "有限责任公司", "股份有限公司", "集团", "委员会",
               "学校", "学院", "医院", "事务所", "研究院", "研究所", "设计院",
               "工作室", "协会", "基金会", "单位")

_REASON_DOOR_ADDR = "addr_no"
_REASON_DOOR_UNIT = "unit_room"
_REASON_DOOR_NUM = "house_no"
_REASON_ROAD_NAME = "road_name"
_REASON_ROAD_DIR = "road_direction"
_REASON_ROAD_DIST = "road_distance"
_REASON_SHOP_TERM = "shop_term"
_REASON_SHOP_HOURS = "shop_hours"
_REASON_ORG_LEGAL = "org_legal"
_REASON_ORG_UNIT = "org_unit"


# ────────────────────────────── 环境/小工具 ──────────────────────────────

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "none")


def _norm_text(text: Any) -> str:
    try:
        value = unicodedata.normalize("NFKC", str(text or ""))
    except Exception:  # noqa: BLE001
        value = str(text or "")
    return re.sub(r"\s+", "", value)


def _as_rect(value: Any) -> Box | None:
    try:
        if isinstance(value, dict):
            value = (value.get("x0"), value.get("y0"), value.get("x1"), value.get("y1"))
        x0, y0, x1, y1 = (float(v) for v in value[:4])
    except Exception:  # noqa: BLE001
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return (x0, y0, x1, y1)


def iter_text_boxes(ctx: ScanContext | None) -> list[tuple[str, Box]]:
    """解析 ``ctx.options["text_boxes"]``（兼容 dict/元组/带属性对象）。"""
    if ctx is None:
        return []
    options = getattr(ctx, "options", None) or {}
    raw = options.get("text_boxes") or []
    try:
        scale = float(options.get("text_boxes_scale") or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    width, height = getattr(ctx, "image_size", (0, 0)) or (0, 0)
    out: list[tuple[str, Box]] = []
    for item in raw:
        try:
            if isinstance(item, dict):
                text = item.get("text") or item.get("value") or ""
                rect = _as_rect(item.get("rect") or item)
            elif isinstance(item, (tuple, list)):
                first, second = item[0], item[1]
                if isinstance(first, str):
                    text, rect = first, _as_rect(second)
                else:
                    rect, text = _as_rect(first), second
            else:
                text = getattr(item, "text", "")
                rect = _as_rect(getattr(item, "rect", None))
        except Exception:  # noqa: BLE001
            continue
        if rect is None:
            continue
        if scale != 1.0:
            rect = (rect[0] * scale, rect[1] * scale, rect[2] * scale, rect[3] * scale)
        if width and height:
            rect = (max(0.0, min(float(width), rect[0])),
                    max(0.0, min(float(height), rect[1])),
                    max(0.0, min(float(width), rect[2])),
                    max(0.0, min(float(height), rect[3])))
        text = _norm_text(text)
        if not text:
            continue
        out.append((text, rect))
    return out


def _paper_like_page(rgb: Any) -> bool:
    """白纸页判断：近白 + 低饱和像素占比高（名片/发票/合同/文档扫描页）。"""
    if rgb is None or not _env_bool("YINZHAN_SIGN_PAPER_GATE", True):
        return False
    try:
        arr = np.asarray(rgb)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return False
        h, w = arr.shape[:2]
        step = max(1, int(max(h, w) / 320))
        small = arr[::step, ::step, :3]
        if cv2 is not None:
            hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
            mask = (hsv[:, :, 2] >= _PAPER_VALUE_MIN) & (hsv[:, :, 1] <= _PAPER_SAT_MAX)
        else:
            mx = small.max(axis=2).astype(np.int16)
            mn = small.min(axis=2).astype(np.int16)
            mask = (mn >= _PAPER_VALUE_MIN) & ((mx - mn) <= _PAPER_SAT_MAX)
        return bool(mask.mean() >= _PAPER_RATIO)
    except Exception:  # noqa: BLE001
        return False


def _box_height(rect: Box) -> float:
    return max(0.0, rect[3] - rect[1])


def _box_width(rect: Box) -> float:
    return max(0.0, rect[2] - rect[0])


def _median(values: Iterable[float]) -> float:
    vals = sorted(v for v in values if v > 0)
    if not vals:
        return 0.0
    return vals[len(vals) // 2]


def _clamp_rect(rect: Box, width: float, height: float) -> Box:
    return (max(0.0, min(width, rect[0])), max(0.0, min(height, rect[1])),
            max(0.0, min(width, rect[2])), max(0.0, min(height, rect[3])))


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


def _ioc(inner: Box, outer: Box) -> float:
    x0, y0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x1, y1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = max(0.0, inner[2] - inner[0]) * max(0.0, inner[3] - inner[1])
    return ((x1 - x0) * (y1 - y0)) / area if area > 0 else 0.0


# ────────────────────────────── 文本/尺寸过滤 ──────────────────────────────

def _is_label_like(text: str) -> bool:
    if not text:
        return True
    if text.startswith(_LABEL_PREFIXES):
        return True
    head = text[:12]
    for label in _LABEL_PREFIXES:
        if head.startswith(label + "：") or head.startswith(label + ":"):
            return True
    return False


def _text_ok(text: str) -> bool:
    if not text or len(text) > _MAX_TEXT_LEN:
        return False
    if _PUNCT_RE.search(text):
        return False
    if _is_label_like(text):
        return False
    # 纯数字/符号/单个字符不当作招牌
    if not re.search(r"[\u4e00-\u9fa5A-Za-z]", text):
        return False
    return True


def _size_ok(rect: Box, page_w: float, page_h: float,
             n_boxes: int, median_h: float) -> bool:
    h = _box_height(rect)
    w = _box_width(rect)
    if page_h <= 0 or page_w <= 0:
        return False
    if h < 14.0:
        return False
    if h > 0.45 * page_h:
        return False
    if w < max(24.0, 0.02 * page_w):
        return False
    # 基础字高门槛：招牌文字应明显大于正文
    if h < max(18.0, 0.040 * page_h):
        return False
    if n_boxes >= 9:                       # 文档/密集文本页：更严格
        if h < max(24.0, 0.060 * page_h):
            return False
        if median_h and h < 1.25 * median_h:
            return False
    elif n_boxes >= 4:                     # 稀疏多行：要求比同页中位字高大
        if median_h and h < 1.15 * median_h:
            return False
    return True


# ────────────────────────────── 分类（kind/reason） ──────────────────────────────

def _classify(text: str) -> tuple[str, str, bool] | None:
    """返回 (kind, reason, strong) 或 None。只做规则，不做尺寸判断。"""
    if not _text_ok(text):
        return None
    has_digit = bool(re.search(r"\d", text))
    has_road = any(k in text for k in _ROAD_SUFFIX)
    has_dir = any(k in text for k in _DIRECTION)
    addr_num = _ADDR_NUM_RE.search(text)
    # 1) 门牌/门址：路名+数字、门牌号、单元/室
    if "门牌" in text or "门址" in text or "楼栋" in text:
        return "door_address", _REASON_DOOR_UNIT, True
    # 高速/立交/桥/环线/出入口：带编号也是路牌（G15 沈海高速 出口）
    if has_road and any(k in text for k in ("高速", "立交", "桥", "环线", "入口", "出口")) \
            and len(text) <= 20:
        return "road_sign", _REASON_ROAD_DIR, True
    if addr_num and (has_road or "门" in text or re.match(r"^\d", text)):
        return "door_address", _REASON_DOOR_UNIT, True
    if addr_num:
        return "door_address", _REASON_DOOR_NUM, True
    if has_road and has_digit:
        return "door_address", _REASON_DOOR_ADDR, True
    # 2) 路牌：路名 + 距离/方向；或独立路名
    if has_road and any(k in text for k in ("公里", "km", "KM")):
        return "road_sign", _REASON_ROAD_DIST, True
    if has_road and has_dir:
        return "road_sign", _REASON_ROAD_DIR, True
    if has_road and not has_digit and len(text) <= 16:
        return "road_sign", _REASON_ROAD_NAME, True
    # 3) 店招：店铺类词（有法律实体后缀时归 org）
    has_legal = any(k in text for k in _LEGAL_SUFFIX)
    has_shop = any(k in text for k in _SHOP_TERMS)
    if has_shop and not has_legal:
        reason = _REASON_SHOP_HOURS if ("营业" in text or "24" in text
                                        or "小时" in text) else _REASON_SHOP_TERM
        return "shop_sign", reason, True
    # 4) 单位铭牌
    if has_legal:
        return "org_plaque", _REASON_ORG_LEGAL, True
    if any(k in text for k in _ORG_STRONG):
        return "org_plaque", _REASON_ORG_UNIT, True
    if any(k in text for k in _ORG_TERMS) and len(text) <= 20:
        return "org_plaque", _REASON_ORG_UNIT, False
    return None


def _is_fragment(text: str, cand_h: float, box_h: float) -> bool:
    """相邻框是否可以并入招牌（短、无句读、不过小/过大）。"""
    if not _text_ok(text):
        # 数字/单元等碎片允许（例：88号、2单元）
        if not re.fullmatch(r"[0-9\u4e00-\u9fa5]{1,10}(号|栋|幢|单元|室|楼|层|座|院|弄)?",
                            text or ""):
            return False
    if box_h < 0.4 * cand_h or box_h > 2.0 * cand_h:
        return False
    return True


# ────────────────────────────── 候选处理 ──────────────────────────────

class _Candidate:
    __slots__ = ("kind", "reason", "strong", "rect", "anchors", "confidence")

    def __init__(self, kind: str, reason: str, strong: bool, rect: Box,
                 anchors: int, confidence: float) -> None:
        self.kind = kind
        self.reason = reason
        self.strong = strong
        self.rect = rect
        self.anchors = anchors
        self.confidence = confidence


def _build_candidate(kind: str, reason: str, strong: bool, rect: Box,
                     boxes: list[tuple[str, Box]], page_w: float, page_h: float
                     ) -> _Candidate:
    """把命中框与相邻可合并框并起来，再外扩 6px。"""
    h = _box_height(rect)
    x0, y0, x1, y1 = rect
    pad_x = 1.8 * h
    pad_y = 1.0 * h
    union = [x0, y0, x1, y1]
    anchors = 1
    for text, box in boxes:
        if box == rect:
            continue
        bh = _box_height(box)
        if not _is_fragment(text, h, bh):
            continue
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        if not (x0 - pad_x <= cx <= x1 + pad_x and y0 - pad_y <= cy <= y1 + pad_y):
            continue
        union = [min(union[0], box[0]), min(union[1], box[1]),
                 max(union[2], box[2]), max(union[3], box[3])]
        anchors += 1
    rect2 = (union[0] - _PAD, union[1] - _PAD, union[2] + _PAD, union[3] + _PAD)
    rect2 = _clamp_rect(rect2, page_w, page_h)
    confidence = 0.86 if strong else 0.76
    if anchors >= 2:
        confidence += 0.04
    return _Candidate(kind, reason, strong, rect2, anchors, min(0.95, confidence))


def _dedupe(candidates: list[_Candidate]) -> list[_Candidate]:
    """同 kind 重叠合并；不同 kind 高度重叠时保留更具体/置信度高的一个。"""
    priority = {"door_address": 4, "road_sign": 3, "shop_sign": 2, "org_plaque": 1}
    candidates = sorted(
        candidates,
        key=lambda c: (c.confidence, priority.get(c.kind, 0)),
        reverse=True)
    kept: list[_Candidate] = []
    for cand in candidates:
        hit = None
        for item in kept:
            overlap = _iou(item.rect, cand.rect)
            if item.kind == cand.kind and (
                    overlap >= 0.5 or _ioc(cand.rect, item.rect) >= 0.75
                    or _ioc(item.rect, cand.rect) >= 0.75):
                hit = item
                break
            if item.kind != cand.kind and (
                    overlap >= 0.6 or _ioc(cand.rect, item.rect) >= 0.85
                    or _ioc(item.rect, cand.rect) >= 0.85):
                # 不同 kind 视为同一块招牌：保留更高优先级 / 更高置信度
                hit = item
                break
        if hit is None:
            kept.append(cand)
        else:
            hit.rect = (min(hit.rect[0], cand.rect[0]), min(hit.rect[1], cand.rect[1]),
                        max(hit.rect[2], cand.rect[2]), max(hit.rect[3], cand.rect[3]))
            hit.confidence = max(hit.confidence, cand.confidence)
            hit.anchors = max(hit.anchors, cand.anchors)
    return kept


def scan(image_rgb: Any, ctx: ScanContext) -> list[ScanHit]:
    """mediascan 扫描器入口：一张页面图 → 招牌类命中框（像素坐标）。"""
    t0 = time.perf_counter()
    boxes = iter_text_boxes(ctx)
    if not boxes:
        return []                                  # 无 OCR 文本框：不承诺
    if image_rgb is None:
        return []
    try:
        height, width = int(image_rgb.shape[0]), int(image_rgb.shape[1])
    except Exception:  # noqa: BLE001
        width, height = getattr(ctx, "image_size", (0, 0)) or (0, 0)
    if width < 32 or height < 32:
        return []
    if _paper_like_page(image_rgb):
        return []                                  # 纸面/名片/发票/文档页：不报招牌

    heights = [_box_height(box) for _text, box in boxes]
    median_h = _median(heights)
    n_boxes = len(boxes)
    candidates: list[_Candidate] = []
    for text, rect in boxes:
        if not _size_ok(rect, float(width), float(height), n_boxes, median_h):
            continue
        classified = _classify(text)
        if classified is None:
            continue
        kind, reason, strong = classified
        cand = _build_candidate(kind, reason, strong, rect, boxes,
                                float(width), float(height))
        area_ratio = ((cand.rect[2] - cand.rect[0]) * (cand.rect[3] - cand.rect[1])
                      / float(max(1, width * height)))
        if area_ratio > 0.60:                      # 整页框不当作招牌
            continue
        candidates.append(cand)

    # 门牌拆成「路名」+「88号」两框时，把相邻的 road 候选并入 door
    doors = [c for c in candidates if c.kind == "door_address"]
    others: list[_Candidate] = []
    for cand in candidates:
        if cand.kind == "road_sign":
            merged = False
            for door in doors:
                h = _box_height(door.rect)
                cx = (cand.rect[0] + cand.rect[2]) / 2.0
                cy = (cand.rect[1] + cand.rect[3]) / 2.0
                if (door.rect[0] - 2.0 * h <= cx <= door.rect[2] + 2.0 * h
                        and door.rect[1] - 1.5 * h <= cy <= door.rect[3] + 1.5 * h):
                    door.rect = (min(door.rect[0], cand.rect[0]),
                                 min(door.rect[1], cand.rect[1]),
                                 max(door.rect[2], cand.rect[2]),
                                 max(door.rect[3], cand.rect[3]))
                    door.anchors += 1
                    door.confidence = min(0.95, door.confidence + 0.03)
                    merged = True
                    break
            if merged:
                continue
        others.append(cand)
    candidates = _dedupe(doors + others)

    hits: list[ScanHit] = []
    for cand in candidates:
        note = (f"engine=ocr;kind={cand.kind};reason={cand.reason};"
                f"anchors={max(1, cand.anchors)}")
        hits.append(ScanHit(kind=cand.kind, rect=cand.rect,
                            confidence=round(cand.confidence, 3), note=note))
    if _env_bool("YINZHAN_SIGN_DEBUG", False):
        elapsed = (time.perf_counter() - t0) * 1000.0
        try:
            ctx.warnings.append(
                f"sign_targets {width}×{height} {elapsed:.0f}ms 文本框 {n_boxes} "
                f"命中 {len(hits)}（kind={','.join(h.kind for h in hits) or '无'}）")
        except Exception:  # noqa: BLE001
            pass
    return hits


mediascan.register_scanner(SCANNER_NAME, scan, kinds=KINDS, priority=55)

__all__ = ["KINDS", "SCANNER_NAME", "scan", "iter_text_boxes"]
