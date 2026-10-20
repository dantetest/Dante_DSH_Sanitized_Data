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
  0 新增模型/体积；长边 >1600 时按 2×2（cap 640）/3×3（cap 960）分块检测、
  映射回原图合并去重，并在原图较大时向 ctx.warnings 写"照片较大，小脸可能漏检"；
  命中框按「整个头」非对称外扩（上 35% 发际线、左右 25% 耳廓、下 15% 下颌）。
- ``id_document``：OCR 锚点判定（身份证/护照/驾驶证/营业执照/社保卡/
  工作证/居住证/港澳居民来往内地通行证/台湾居民来往大陆通行证/
  外国人永久居留身份证/银行卡），命中后返回整证件区域；面积 ≥ 页面 25%
  且文本块不多时返回整页；文本块 ≥18 的发票/合同页直接跳过，12~17 块禁止
  整页扩张（只涂锚点邻域）。银行卡需 Luhn 校验卡号/强锚点，且永远不做整页扩张。
  note 用 `doc=id_card/passport/.../bank_card` 稳定标识类型。
- ``id_photo`` / ``id_name`` / ``id_number`` / ``id_address``：身份证关键字段
  （A 方案）。仅身份证正面触发：按 0/90/180/270 四向旋转，用「期望证件照区」
  Haar 人脸命中确定朝向，再按固定模板把头像/姓名/号码/住址框映射回原图；
  没有可识别脸/朝向/字段越界时返回 ``fallback=1`` 的整卡 ``id_document``（不漏）。
  注释格式：``side=front|back;fields=n[;fallback=1];orient=0|90|180|270``，
  ``orient`` 表示把原页面逆时针旋转多少度得到正向卡。
- ``plate``：OCR 文本框 + 中国车牌正则（含新能源 8 位、挂/学/警后缀）作为基础路径；
  全图 HSV 蓝/绿/黄底色 + 矩形拟合找**完整牌面**，OCR 仅确认归属；
  不可读时回退低置信候选（note 标 unconfirmed），返回比 OCR 框更宽的整块牌面。
- ``road_sign`` / ``door_address``：**P1 预留**，当前恒返回空（只注册 kind，
  不产生误报）；实现计划见 docs/impl/visual.md。
- ``chat_avatar`` / ``chat_name`` / ``chat_group`` / ``chat_time`` / ``chat_input``：
  聊天截图**逐项**识别（P1-A）。用 OCR 文本框 + 布局线索（状态栏时间、顶部标题、
  左右头像列、气泡排布、底部输入条）保守判定聊天截图（≥3 条信号 + ≥3 条左右
  消息行），只返回逐项框、不整屏黑；note 带 ``chat=1;role=...``。
  PDF/文档页（``fmt`` 非 image）直接跳过；无 OCR 或自定义皮肤宁少报不误报。

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
    "id_photo",
    "id_name",
    "id_number",
    "id_address",
    "id_authority",
    "id_validity",
    "plate",
    "road_sign",
    "door_address",
    "chat_avatar",
    "chat_name",
    "chat_group",
    "chat_time",
    "chat_input",
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
    """按宽/高比例外扩（车牌 0.08，通用）。"""
    x0, y0, x1, y1 = box
    dx = max(0.0, (x1 - x0)) * ratio
    dy = max(0.0, (y1 - y0)) * ratio
    return _clamp_box((x0 - dx, y0 - dy, x1 + dx, y1 + dy), width, height)


# ────────────────────── 人脸：整头外扩 + 大图分块（P0） ──────────────────────

#: 整头遮盖：Haar 框通常只到眉/颧/下巴；上含发际线、左右含耳廓、下含下颌。
#: 数值相对人脸检测框的 宽/高。
_HEAD_PAD_SIDES = 0.25
_HEAD_PAD_TOP = 0.35
_HEAD_PAD_BOTTOM = 0.15

#: 长边超过该值就分块检测（每块 cap 640/960），避免 640 全图缩放把中小脸缩没。
_FACE_TILE_TRIGGER = 1600

#: 检测被缩小时写入 ctx.warnings 的漏检提示（mediascan 会并入 doc.warnings）。
_FACE_DOWNSCALE_WARNING = "照片较大，小脸可能漏检，请人工复核/整图涂黑"


def _expand_head(box: Box, width: float, height: float) -> Box:
    """按「整个头」非对称外扩：上 35% 脸高（发际线）、左右各 25% 脸宽（耳廓）、
    下 15% 脸高（下颌）。仍返回矩形，具体绘制由上层决定。"""
    x0, y0, x1, y1 = box
    face_w = max(0.0, x1 - x0)
    face_h = max(0.0, y1 - y0)
    return _clamp_box((
        x0 - face_w * _HEAD_PAD_SIDES,      # 左耳廓
        y0 - face_h * _HEAD_PAD_TOP,        # 发际线
        x1 + face_w * _HEAD_PAD_SIDES,      # 右耳廓
        y1 + face_h * _HEAD_PAD_BOTTOM,     # 下颌
    ), float(width), float(height))



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
    """单块人脸检测的长边上限。默认 640：1000×620 名片 ≈ 0.6 倍，单页 <100ms。"""
    cap = _env_int("YINZHAN_SCAN_FACE_MAX_SIDE", 640, minimum=320, maximum=6000)
    generic = _env_int("YINZHAN_SCAN_MAX_SIDE", 3000, minimum=320, maximum=10000)
    return min(cap, generic)


def _face_tile_plan(width: int, height: int) -> tuple[str, list[tuple[int, int, int, int, float]]]:
    """人脸检测的页面分块计划。

    返回 ``(label, [(x0, y0, x1, y1, scale), ...])``：

    - 长边 ≤1600：整图一次（scale = min(1, 640/长边)）；
    - 1600 < 长边 ≤3200：2×2 分块、每块长边缩到 ≤640；
    - 长边 >3200：3×3 分块、每块长边缩到 ≤960；
    - ``YINZHAN_SCAN_FACE_TILE=0`` 关闭分块（退回整图 cap），=2/3 强制块数；
    - ``YINZHAN_SCAN_FACE_TILE_CAP`` 可覆盖每块检测长边。
    """
    mode = str(os.environ.get("YINZHAN_SCAN_FACE_TILE", "auto")).strip().lower()
    cap = _face_scan_side()
    long_edge = max(int(width), int(height))
    forced = 0
    if mode in ("0", "false", "no", "off", "single"):
        forced = 1
    elif mode in ("2", "3"):
        forced = int(mode)
    grid = forced
    if grid == 0:
        if long_edge <= _FACE_TILE_TRIGGER:
            grid = 1
        elif long_edge <= 4800:
            grid = 2
        else:
            grid = 3
    if grid <= 1:
        scale = min(1.0, cap / float(long_edge)) if long_edge else 1.0
        return f"single/scale={scale:.3f}", [(0, 0, int(width), int(height), scale)]
    tile_cap = 640 if grid == 2 else 960
    tile_cap = _env_int("YINZHAN_SCAN_FACE_TILE_CAP", tile_cap,
                        minimum=320, maximum=4000)
    try:
        overlap = float(os.environ.get("YINZHAN_SCAN_FACE_TILE_OVERLAP", "0.0") or 0.0)
    except (TypeError, ValueError):
        overlap = 0.0
    overlap = max(0.0, min(0.45, overlap))
    # 分块重叠：防止人脸正好压在块边界上被切成两半（84 号双人照实测有此类漏检）
    tile_w = width / float(max(1.0, grid - (grid - 1) * overlap))
    tile_h = height / float(max(1.0, grid - (grid - 1) * overlap))
    step_x = tile_w * (1.0 - overlap)
    step_y = tile_h * (1.0 - overlap)
    tiles: list[tuple[int, int, int, int, float]] = []
    for gy in range(grid):
        for gx in range(grid):
            x0 = int(round(gx * step_x))
            y0 = int(round(gy * step_y))
            x0 = min(max(0, x0), max(0, int(round(width - tile_w))))
            y0 = min(max(0, y0), max(0, int(round(height - tile_h))))
            x1 = min(int(width), int(round(x0 + tile_w)))
            y1 = min(int(height), int(round(y0 + tile_h)))
            tw = max(1, x1 - x0)
            th = max(1, y1 - y0)
            scale = min(1.0, tile_cap / float(max(tw, th)))
            tiles.append((x0, y0, x1, y1, scale))
    return f"{grid}x{grid}/cap{tile_cap}", tiles


def _detect_faces(rgb: np.ndarray, ctx: ScanContext) -> list[ScanHit]:
    if cv2 is None:
        return []
    try:
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
    except Exception:
        return []
    if height < 32 or width < 32:
        return []
    # 整页灰阶快速判断（缩略图）：纯色页/黑页直接跳过，不进入分块循环
    try:
        preview_scale = min(1.0, 320.0 / float(max(height, width)))
        preview = (cv2.resize(rgb, (max(1, int(width * preview_scale)),
                                    max(1, int(height * preview_scale))),
                              interpolation=cv2.INTER_AREA)
                   if preview_scale < 1.0 else rgb)
        preview_gray = cv2.cvtColor(preview, cv2.COLOR_RGB2GRAY)
    except Exception:
        return []
    if float(preview_gray.std()) < 2.0:
        return []
    doc_like = _document_like(preview_gray)

    mode_label, plan = _face_tile_plan(width, height)
    # 只有「原图本身较大」时才提示可能漏检（小图缩到 640 检测是常态，不打扰用户）
    if max(width, height) > _FACE_TILE_TRIGGER:
        try:
            ctx.warnings.append(_FACE_DOWNSCALE_WARNING)
        except Exception:
            pass

    profile_mode = str(os.environ.get("YINZHAN_SCAN_FACE_PROFILE", "auto")).strip().lower()
    profile_allowed = profile_mode not in ("0", "false", "no", "off", "none")
    always_profile = profile_allowed and profile_mode not in ("auto",)
    auto_profile = profile_allowed and not always_profile and not doc_like

    found: list[tuple[Box, float, str]] = []
    frontal_count = 0
    try:
        min_neighbors = max(1, int(os.environ.get("YINZHAN_SCAN_FACE_MIN_NEIGHBORS", "5") or 5))
    except (TypeError, ValueError):
        min_neighbors = 5

    def _collect_pass(entries: list[tuple[str, Any, float]]) -> int:
        count = 0
        for (tx0, ty0, tx1, ty1, tile_scale) in plan:
            try:
                tile = rgb[ty0:ty1, tx0:tx1]
            except Exception:
                continue
            if tile.size == 0:
                continue
            tile_w = max(1, tx1 - tx0)
            tile_h = max(1, ty1 - ty0)
            if tile_scale < 0.999:
                try:
                    tile = cv2.resize(
                        tile,
                        (max(1, int(round(tile_w * tile_scale))),
                         max(1, int(round(tile_h * tile_scale)))),
                        interpolation=cv2.INTER_AREA)
                except Exception:
                    continue
            try:
                gray = cv2.equalizeHist(cv2.cvtColor(tile, cv2.COLOR_RGB2GRAY))
            except Exception:
                continue
            if float(gray.std()) < 2.0:
                continue
            for label, cascade, base in entries:
                variants = [(False, label)]
                if label == "profile":
                    variants = [(False, "profile"), (True, "profile-flip")]
                for flipped_source, label_note in variants:
                    try:
                        src = cv2.flip(gray, 1) if flipped_source else gray
                        dets = cascade.detectMultiScale(
                            src, scaleFactor=1.1, minNeighbors=min_neighbors,
                            minSize=(24, 24),
                        )
                    except Exception:
                        continue
                    for det in dets if dets is not None else []:
                        try:
                            x, y, fw, fh = (float(v) for v in list(det)[:4])
                        except Exception:
                            continue
                        if fw <= 0 or fh <= 0 or tile_scale <= 0:
                            continue
                        src_w = float(gray.shape[1]) if flipped_source else 0.0
                        if flipped_source:
                            x = src_w - x - fw
                        rect: Box = (tx0 + x / tile_scale, ty0 + y / tile_scale,
                                     tx0 + (x + fw) / tile_scale,
                                     ty0 + (y + fh) / tile_scale)
                        face_px = min(fw / tile_scale, fh / tile_scale)
                        found.append((rect,
                                      _face_confidence(base, face_px, label_note),
                                      label_note))
                        count += 1
        return count

    frontal_count = _collect_pass(_face_cascades(include_profile=False))
    if always_profile or (auto_profile and frontal_count == 0):
        _collect_pass([entry for entry in _face_cascades(include_profile=True)
                       if entry[0] == "profile"])

    if not found:
        return []

    # 去重：同一张脸在多块/多尺度/正脸+侧脸重复命中只留一个（先按原始框去重）
    found.sort(key=lambda item: item[1], reverse=True)
    kept: list[tuple[Box, float, str]] = []
    for rect, conf, label in found:
        if any(_iou(rect, prev[0]) >= 0.35 or _ioc(rect, prev[0]) >= 0.85
               for prev in kept):
            continue
        kept.append((rect, conf, label))
    # 再按「整头」外扩，并做一次外扩后的去重（相邻/重叠框会因外扩而连在一起）
    expanded: list[tuple[Box, float, str]] = []
    for rect, conf, label in kept:
        head = _expand_head(rect, float(width), float(height))
        if head[2] - head[0] < 8 or head[3] - head[1] < 8:
            continue
        if any(_iou(head, prev[0]) >= 0.48 or _ioc(head, prev[0]) >= 0.90
               for prev in expanded):
            continue
        expanded.append((head, conf, label))
    max_faces = _env_int("YINZHAN_SCAN_FACE_MAX", 64, minimum=1, maximum=512)
    expanded = expanded[:max_faces]
    return [
        ScanHit(kind="face", rect=box, confidence=round(conf, 3),
                note=f"engine=haar;{label};{mode_label};head")
        for box, conf, label in expanded
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
    # ── P1.5 证件类型扩展（照片/扫描件；title+字段弱锚点才触发，低误报） ──
    "work_permit": {
        "strong": (),
        "title": ("工作证", "员工证", "工牌"),
        "weak": ("工号", "部门", "职位", "职务", "有效期", "姓名"),
    },
    "residence_permit": {
        "strong": (),
        "title": ("居住证",),
        "weak": ("公民身份号码", "签发机关", "有效期限", "住址", "姓名"),
    },
    "hk_mo_permit": {
        "strong": (),
        "title": ("港澳居民来往内地通行证", "回乡证"),
        "weak": ("证件号码", "有效期限", "签发机关", "姓名"),
    },
    "tw_permit": {
        "strong": (),
        "title": ("台湾居民来往大陆通行证", "台胞证"),
        "weak": ("证件号码", "有效期限", "签发机关", "姓名"),
    },
    "foreign_pr": {
        "strong": (),
        "title": ("外国人永久居留身份证", "外国人永久居留证", "永久居留证"),
        "weak": ("证件号码", "国籍", "有效期限", "签发机关", "姓名"),
    },
    # 银行卡：弱锚点（卡组织/卡种文字）+ Luhn 校验过的卡号才触发；
    # 只涂锚点邻域/整卡，禁止 ≥25% 整页扩张（防合同里银行卡号被整页涂黑）。
    "bank_card": {
        "strong": ("银行卡号", "信用卡号", "借记卡号", "储蓄卡号"),
        "weak": ("银联", "VISA", "MASTERCARD", "信用卡", "借记卡",
                 "储蓄卡", "银行卡"),
        "title": (),
        "no_whole_page": True,
    },
}

_CARD_NUMBER_RE = re.compile(r"(?<![\dA-Za-z])(?:\d[ -]?){13,19}(?![\dA-Za-z])")


def _luhn_valid(digits: str) -> bool:
    """银行卡号 Luhn 校验（用于压误报；只对 13~19 位纯数字）。"""
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _find_bank_card_numbers(text_boxes: Sequence[tuple[str, Box]]) -> list[tuple[str, Box]]:
    """找 Luhn 校验通过的银行卡号文本框（OCR 可能带空格/短横线）。"""
    hits: list[tuple[str, Box]] = []
    for text, rect in text_boxes:
        compact = re.sub(r"[^0-9]", "", _norm_text(text))
        if _luhn_valid(compact):
            hits.append((compact, rect))
            continue
        # 一行里可能混有多个数字段（如卡号 + 有效期），逐个候选试 Luhn
        for match in _CARD_NUMBER_RE.finditer(_norm_text(text)):
            candidate = re.sub(r"[^0-9]", "", match.group(0))
            if _luhn_valid(candidate):
                hits.append((candidate, rect))
                break
    return hits

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


# ────────────────────── 身份证关键字段（A 方案，P0 后续） ──────────────────────

#: 正面字段模板（相对「正立卡片」宽/高归一化坐标；用用户 #85 转正实拍校准）。
#: 只抹**值**、不抹标签；性别/民族/出生三行必须保持可见。
_ID_FIELD_TEMPLATES: dict[str, tuple[float, float, float, float]] = {
    "id_photo": (0.55, 0.15, 0.96, 0.78),
    "id_name": (0.18, 0.12, 0.45, 0.26),
    "id_address": (0.18, 0.48, 0.93, 0.69),
    "id_number": (0.26, 0.73, 0.93, 0.90),
}
_ID_FIELD_PAD = 0.0                  # 模板区间已按实拍校准，不再整体外扩（框窄优先）
_ID_FIELD_CARD_EXPAND = 0.03         # 拟合卡略小于实物卡，映射前放大 3%（补偿锚点内缩）
_ID_CARD_ASPECT = 85.6 / 54.0        # ≈1.585（横版卡）
_ID_CARD_MARGIN_X = 0.06             # 文本锚点距卡片左边 6%
#: #85 转正实测：姓名行顶 ≈卡高 14.7%，公民身份号码标签底 ≈卡高 91%；
#: 锚点纵向跨度对应卡高 76.3%，据此反推卡片高度/上边界（旧 5%~86% 会把框整体上移）。
_ID_CARD_MARGIN_TOP = 0.147
_ID_CARD_TEXT_H = 0.763
_ID_PHOTO_REGION = (0.55, 0.15, 0.96, 0.78)   # 期望证件照区域（脸中心归一化）
_ID_FRONT_ANCHORS = ("姓名", "公民身份号码", "住址", "性别", "民族", "出生")
_ID_BACK_ANCHORS = ("签发机关", "有效期限", "居民身份证")
_ID_NUMBER_RE = re.compile(r"\d{17}[\dXx]")

#: 反面字段模板（相对「正立卡片」归一化坐标；按用户 #39 转正实拍校准）。
#: 用户要求反面也抹「签发机关」「有效期限」，标签一起遮没问题。
_ID_BACK_FIELD_TEMPLATES: dict[str, tuple[float, float, float, float]] = {
    "id_authority": (0.17, 0.68, 0.80, 0.82),
    "id_validity": (0.17, 0.84, 0.92, 0.96),
}
_ID_BACK_ROW_TOP = 0.68              # 签发机关行顶（相对卡高）
_ID_BACK_ROW_BOTTOM = 0.96           # 有效期限行底（相对卡高）
_ID_BACK_MARGIN_X = 0.17             # 两行共同左边距（相对卡宽）


def _offset_box(box: Box, dx: float, dy: float) -> Box:
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def _rot_forward_box(box: Box, k: int, width: float, height: float) -> Box:
    """原图（裁片）坐标 → 逆时针旋转 k*90° 后的坐标。

    np.rot90(crop, k) 的像素关系（裁片宽 W、高 H）：
    k=1: Xr=y,           Yr=W-x
    k=2: Xr=W-x,         Yr=H-y
    k=3: Xr=H-y,         Yr=x
    """
    x0, y0, x1, y1 = box
    k %= 4
    if k == 0:
        return (x0, y0, x1, y1)
    if k == 1:
        return (y0, width - x1, y1, width - x0)
    if k == 2:
        return (width - x1, height - y1, width - x0, height - y0)
    return (height - y1, x0, height - y0, x1)


def _rot_back_box(box: Box, k: int, width: float, height: float) -> Box:
    """逆时针旋转 k*90° 后的坐标 → 原图（裁片）坐标（上式的逆）。"""
    x0, y0, x1, y1 = box
    k %= 4
    if k == 0:
        return (x0, y0, x1, y1)
    if k == 1:
        return (width - y1, x0, width - y0, x1)
    if k == 2:
        return (width - x1, height - y1, width - x0, height - y0)
    return (y0, height - x1, y1, height - x0)


def _expand_card_rect(box: Box, width: int, height: int, ratio: float = 0.10) -> Box:
    """整卡命中框外扩（宁可多遮不可漏）；字段框不受影响。"""
    x0, y0, x1, y1 = box
    dx = (x1 - x0) * ratio
    dy = (y1 - y0) * ratio
    return _clamp_box((x0 - dx, y0 - dy, x1 + dx, y1 + dy),
                      float(width), float(height))


def _expand_card_rect_local(box: Box, ratio: float) -> Box:
    """四边等比外扩（用于模板映射前放大拟合卡，不夹取页面边界）。"""
    x0, y0, x1, y1 = box
    dx = (x1 - x0) * ratio
    dy = (y1 - y0) * ratio
    return (x0 - dx, y0 - dy, x1 + dx, y1 + dy)


def _inside_ratio(inner: Box, width: float, height: float) -> float:
    """框落在 [0,0,width,height] 内的面积占比（0~1）。"""
    x0, y0 = max(0.0, inner[0]), max(0.0, inner[1])
    x1, y1 = min(float(width), inner[2]), min(float(height), inner[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area = max(1e-6, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return inter / area


def _fitted_card_box(anchor_rot: Sequence[Box]) -> Box | None:
    """用「正立卡片」坐标系里的文本锚点并集，反推横版卡片矩形。

    依据 #85 转正实测：姓名行顶 ≈ 卡高 14.7%、公民身份号码标签底 ≈ 卡高 91%，
    即文本锚点纵向跨度约卡高 76.3%；宽度按身份证 1.585 横版比例反推，
    左边界由「最左锚点距卡左 6%」得到。旧假设 5%~86% 会让卡片整体上移，
    #85 上表现为住址框上移盖住性别/民族/出生行。
    """
    if not anchor_rot:
        return None
    x0 = min(b[0] for b in anchor_rot)
    y0 = min(b[1] for b in anchor_rot)
    x1 = max(b[2] for b in anchor_rot)
    y1 = max(b[3] for b in anchor_rot)
    ah = max(1.0, y1 - y0)
    if ah < 6:
        return None
    card_h = ah / _ID_CARD_TEXT_H
    card_w = card_h * _ID_CARD_ASPECT
    if card_w < max(8.0, x1 - x0):      # 保证装得下横向锚点
        card_w = max(8.0, x1 - x0) / 0.90
        card_h = card_w / _ID_CARD_ASPECT
    card_x0 = x0 - _ID_CARD_MARGIN_X * card_w
    card_y0 = y0 - _ID_CARD_MARGIN_TOP * card_h
    return (card_x0, card_y0, card_x0 + card_w, card_y0 + card_h)


def _text_upright_votes(anchor_page: Sequence[Box], crop_box: Box,
                        crop_w: float, crop_h: float) -> list[int]:
    """四向旋转投票：文本框变成「横排」最多的 k 即为文本朝向。"""
    votes: list[int] = []
    for k in range(4):
        horizontal = 0
        for rect in anchor_page:
            local = _offset_box(rect, -crop_box[0], -crop_box[1])
            mapped = _rot_forward_box(local, k, crop_w, crop_h)
            w = mapped[2] - mapped[0]
            h = mapped[3] - mapped[1]
            if w >= h * 1.1:
                horizontal += 1
        votes.append(horizontal)
    return votes


def _choose_text_orientation(anchor_page: Sequence[Box], crop_box: Box,
                             crop_w: float, crop_h: float) -> int:
    votes = _text_upright_votes(anchor_page, crop_box, crop_w, crop_h)
    best_k = 0
    best_vote = -1
    for k, vote in enumerate(votes):
        if vote > best_vote:
            best_k, best_vote = k, vote
    return best_k


def _expand_anchor_crop(anchor_page: Sequence[Box], width: int, height: int,
                        ratio: float = 0.25) -> Box:
    """锚点并集外扩一圈作为「证件裁片」，保证能包含头像区/卡片边缘。"""
    x0 = min(b[0] for b in anchor_page)
    y0 = min(b[1] for b in anchor_page)
    x1 = max(b[2] for b in anchor_page)
    y1 = max(b[3] for b in anchor_page)
    pad_x = max(12.0, (x1 - x0) * ratio)
    pad_y = max(12.0, (y1 - y0) * ratio)
    return _clamp_box((x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y),
                      float(width), float(height))


def _detect_card_face(crop: np.ndarray, k: int, anchor_page: Sequence[Box],
                      crop_box: Box, width: int, height: int):
    """在旋转 k 后的裁片里找「证件照区域」的人脸。

    返回 ``(score, rot_image, rot_w, rot_h, card_rot)``；找不到返回 ``None``。
    """
    if cv2 is None:
        return None
    try:
        rot = np.rot90(crop, k)
    except Exception:
        return None
    rot_h, rot_w = int(rot.shape[0]), int(rot.shape[1])
    if rot_w < 40 or rot_h < 24:
        return None
    local_anchor = [_offset_box(r, -crop_box[0], -crop_box[1]) for r in anchor_page]
    anchor_rot = [_rot_forward_box(r, k, float(crop.shape[1]), float(crop.shape[0]))
                  for r in local_anchor]
    card_rot = _fitted_card_box(anchor_rot)
    if card_rot is None:
        return None
    if card_rot[2] - card_rot[0] <= (card_rot[3] - card_rot[1]) * 1.15:
        return None            # 正向身份证必须是横版
    scale = min(1.0, 640.0 / float(max(rot_w, rot_h)))
    if scale < 1.0:
        scan = cv2.resize(rot, (max(1, int(round(rot_w * scale))),
                                max(1, int(round(rot_h * scale)))),
                          interpolation=cv2.INTER_AREA)
    else:
        scan = rot
    try:
        gray = cv2.equalizeHist(cv2.cvtColor(scan, cv2.COLOR_RGB2GRAY))
    except Exception:
        return None
    card_w = max(1.0, card_rot[2] - card_rot[0])
    card_h = max(1.0, card_rot[3] - card_rot[1])
    best = None
    for _label, cascade, base_conf in _face_cascades(include_profile=False):
        if cascade is None:
            continue
        try:
            dets = cascade.detectMultiScale(gray, scaleFactor=1.1,
                                            minNeighbors=5, minSize=(24, 24))
        except Exception:
            continue
        for det in dets if dets is not None else []:
            try:
                fx, fy, fw, fh = (float(v) for v in list(det)[:4])
            except Exception:
                continue
            if fw <= 0 or fh <= 0 or scale <= 0:
                continue
            # 映射回旋转后裁片坐标，再换算到「卡片归一化」坐标
            cx = (fx + fw / 2.0) / scale
            cy = (fy + fh / 2.0) / scale
            nx = (cx - card_rot[0]) / card_w
            ny = (cy - card_rot[1]) / card_h
            tol = 0.06
            if not (_ID_PHOTO_REGION[0] - tol <= nx <= _ID_PHOTO_REGION[2] + 0.03
                    and _ID_PHOTO_REGION[1] - tol <= ny <= _ID_PHOTO_REGION[3] + tol):
                continue
            face_w = fw / scale
            if face_w < card_w * 0.06:       # 小得离谱的框多半是纹理误报
                continue
            score = (fw / scale) * (fh / scale)
            if best is None or score > best[0]:
                best = (score, base_conf)
    if best is None:
        return None
    return best[0], rot, rot_w, rot_h, card_rot


def _refine_id_fields(fields: dict[str, Box],
                      text_boxes: Sequence[tuple[str, Box]],
                      card_page_box: Box,
                      template_boxes: dict[str, Box] | None = None) -> dict[str, Box]:
    """用同字段 OCR 文本框微调字段框，但**必须裁回模板值区间**。

    校准后模板只覆盖「值」；OCR 框常把标签/邻行也框进来，若直接并集会遮到
    性别/民族/出生行，因此并集后按模板框硬裁剪（宁可框窄一点，漏了可用整卡档兜底）。
    """
    card_area = max(1.0, (card_page_box[2] - card_page_box[0])
                    * (card_page_box[3] - card_page_box[1]))
    name_box = fields.get("id_name")
    number_box = fields.get("id_number")
    address_box = fields.get("id_address")

    def _near(rect: Box, field: Box, expand: float) -> bool:
        fx0 = field[0] - (field[2] - field[0]) * expand
        fy0 = field[1] - (field[3] - field[1]) * expand
        fx1 = field[2] + (field[2] - field[0]) * expand
        fy1 = field[3] + (field[3] - field[1]) * expand
        cx = (rect[0] + rect[2]) / 2.0
        cy = (rect[1] + rect[3]) / 2.0
        return fx0 <= cx <= fx1 and fy0 <= cy <= fy1

    for text, rect in text_boxes:
        norm = _norm_text(text)
        if not norm:
            continue
        rect_area = max(1e-6, (rect[2] - rect[0]) * (rect[3] - rect[1]))
        if rect_area > card_area * 0.45:     # 整页/整卡大框不参与扩框
            continue
        if name_box is not None and "姓名" in norm and _near(rect, name_box, 0.45):
            name_box = (min(name_box[0], rect[0]), min(name_box[1], rect[1]),
                        max(name_box[2], rect[2]), max(name_box[3], rect[3]))
            fields["id_name"] = name_box
        elif number_box is not None and (
                _ID_NUMBER_RE.search(norm) or "身份号码" in norm
                or "公民身份" in norm) and _near(rect, number_box, 0.55):
            number_box = (min(number_box[0], rect[0]), min(number_box[1], rect[1]),
                          max(number_box[2], rect[2]), max(number_box[3], rect[3]))
            fields["id_number"] = number_box
        elif address_box is not None and _near(rect, address_box, 0.45) and any(
                key in norm for key in ("住址", "省", "市", "区", "路", "号", "村",
                                        "街", "栋", "楼")):
            address_box = (min(address_box[0], rect[0]), min(address_box[1], rect[1]),
                           max(address_box[2], rect[2]), max(address_box[3], rect[3]))
            fields["id_address"] = address_box
    # 先裁回模板值区间（模板是遮「值」的边界，标签/邻行不得被并集带进来）
    if template_boxes:
        for kind, box in list(fields.items()):
            template = template_boxes.get(kind)
            if template is None:
                continue
            clipped = (max(box[0], template[0]), max(box[1], template[1]),
                       min(box[2], template[2]), min(box[3], template[3]))
            if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                fields[kind] = clipped
    # 再确保不扩到卡片外（卡片外扩 8% 容差）
    pad_x = (card_page_box[2] - card_page_box[0]) * 0.08
    pad_y = (card_page_box[3] - card_page_box[1]) * 0.08
    allowed = (card_page_box[0] - pad_x, card_page_box[1] - pad_y,
               card_page_box[2] + pad_x, card_page_box[3] + pad_y)
    for kind, box in list(fields.items()):
        clipped = (max(box[0], allowed[0]), max(box[1], allowed[1]),
                   min(box[2], allowed[2]), min(box[3], allowed[3]))
        if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
            fields[kind] = clipped
    return fields


def _detect_id_card_front(rgb: np.ndarray, text_boxes: Sequence[tuple[str, Box]],
                          width: int, height: int):
    """正面字段级检测：四向旋转 + 期望证件照区人脸选朝向 + 模板映射。

    返回 ``(orient, fields, card_page_box)`` 或 ``None``（需要整卡回退）。
    """
    anchor_rects = [rect for _text, rect in
                    _find_anchor_boxes(text_boxes, _ID_FRONT_ANCHORS)]
    if len(anchor_rects) < 2:
        return None
    # 正面文字锚点只覆盖卡片左半（右侧是照片），四向旋转前无法预知照片方向；
    # 直接以整页为裁片做四向旋转（长边缩到 640 再跑 Haar），保证旋转后照片区完整。
    crop_box = (0.0, 0.0, float(width), float(height))
    cx0, cy0 = 0, 0
    crop = rgb
    if crop.size == 0 or crop.shape[0] < 24 or crop.shape[1] < 40:
        return None
    crop_w = float(crop.shape[1])
    crop_h = float(crop.shape[0])
    best = None
    for k in range(4):
        found = _detect_card_face(crop, k, anchor_rects, crop_box, width, height)
        if found is None:
            continue
        score, _rot, rot_w, rot_h, card_rot = found
        if best is None or score > best[0]:
            best = (score, k, rot_w, rot_h, card_rot)
    if best is None:
        return None
    _score, k, rot_w, rot_h, card_rot = best
    if _ID_FIELD_CARD_EXPAND:
        card_rot = _expand_card_rect_local(card_rot, _ID_FIELD_CARD_EXPAND)
    # 模板框：旋转后坐标系（正立卡片）→ 裁片坐标 → 页面坐标
    cw = card_rot[2] - card_rot[0]
    ch = card_rot[3] - card_rot[1]
    pad_x = _ID_FIELD_PAD * cw
    pad_y = _ID_FIELD_PAD * ch
    fields: dict[str, Box] = {}
    template_page: dict[str, Box] = {}
    for kind, (nx0, ny0, nx1, ny1) in _ID_FIELD_TEMPLATES.items():
        rot_box = (card_rot[0] + nx0 * cw - pad_x, card_rot[1] + ny0 * ch - pad_y,
                   card_rot[0] + nx1 * cw + pad_x, card_rot[1] + ny1 * ch + pad_y)
        local = _rot_back_box(rot_box, k, crop_w, crop_h)
        if _inside_ratio(local, crop_w, crop_h) < 0.97:
            return None                  # 字段框落出卡片/裁片 → 不猜，整卡回退
        page_box = _clamp_box(_offset_box(local, cx0, cy0),
                              float(width), float(height))
        fields[kind] = page_box
        template_page[kind] = page_box
    card_local = _rot_back_box(card_rot, k, crop_w, crop_h)
    card_page = _clamp_box(_offset_box(card_local, cx0, cy0),
                           float(width), float(height))
    if card_page[2] - card_page[0] < 40 or card_page[3] - card_page[1] < 24:
        return None
    fields = _refine_id_fields(fields, text_boxes, card_page, template_page)
    return k * 90, fields, card_page


def _choose_card_orientation(text_boxes: Sequence[tuple[str, Box]],
                             anchor_page: Sequence[Box], crop_box: Box,
                             crop_w: float, crop_h: float) -> int:
    """文本朝向投票；横排候选有多个（0/180 或 90/270 歧义）时用证件标题位置消歧。

    反面卡片顶部有「中华人民共和国居民身份证」标题：标题在卡片上半区的朝向才是正向。
    """
    votes = _text_upright_votes(anchor_page, crop_box, crop_w, crop_h)
    best_vote = max(votes) if votes else 0
    candidates = [k for k, vote in enumerate(votes) if vote == best_vote]
    if len(candidates) <= 1:
        return candidates[0] if candidates else 0
    title_boxes = [rect for _text, rect in
                   _find_anchor_boxes(text_boxes, ("居民身份证", "中华人民共和国"))]
    if not title_boxes:
        return candidates[0]
    best = None
    for k in candidates:
        local = [_offset_box(r, -crop_box[0], -crop_box[1]) for r in anchor_page]
        anchor_rot = [_rot_forward_box(r, k, crop_w, crop_h) for r in local]
        card = _fitted_card_box(anchor_rot)
        if card is None:
            continue
        title_local = [_offset_box(r, -crop_box[0], -crop_box[1])
                       for r in title_boxes]
        title_rot = [_rot_forward_box(r, k, crop_w, crop_h)
                     for r in title_local]
        cy = sum((b[1] + b[3]) / 2.0 for b in title_rot) / len(title_rot)
        ny = (cy - card[1]) / max(1.0, card[3] - card[1])
        if best is None or ny < best[0]:
            best = (ny, k)
    return best[1] if best else candidates[0]


def _detect_id_card_fallback(rgb: np.ndarray, text_boxes: Sequence[tuple[str, Box]],
                             anchor_rects: Sequence[Box], width: int, height: int,
                             side: str = "front"):
    """无脸/无法确定朝向时的整卡回退：文本朝向投票估卡片框，返回 (orient, box)。"""
    if not anchor_rects:
        return 0, (0.0, 0.0, float(width), float(height))
    crop_box = _expand_anchor_crop(anchor_rects, width, height, ratio=0.28)
    cx0, cy0, cx1, cy1 = (int(round(v)) for v in crop_box)
    crop = rgb[cy0:cy1, cx0:cx1]
    if crop.size == 0:
        return 0, crop_box
    crop_w = float(crop.shape[1])
    crop_h = float(crop.shape[0])
    k = _choose_card_orientation(text_boxes, anchor_rects, crop_box, crop_w, crop_h)
    local_anchor = [_offset_box(r, -cx0, -cy0) for r in anchor_rects]
    anchor_rot = [_rot_forward_box(r, k, crop_w, crop_h) for r in local_anchor]
    card_rot = _fitted_card_box(anchor_rot)
    if card_rot is None:
        return k * 90, _clamp_box(crop_box, float(width), float(height))
    local = _rot_back_box(card_rot, k, crop_w, crop_h)
    page = _clamp_box(_offset_box(local, cx0, cy0), float(width), float(height))
    return k * 90, page


def _fitted_back_card_box(authority_rot: Box, validity_rot: Box) -> Box | None:
    """用反面「签发机关/有效期限」两行锚点反推横版卡片矩形（按 #39 校准）。

    #39 转正实测：签发机关行顶 ≈ 卡高 68%、有效期限行底 ≈ 卡高 96%，
    两行左边距 ≈ 卡宽 17%。据此直接解卡片高度/左边界。
    """
    row_y0 = min(authority_rot[1], validity_rot[1])
    row_y1 = max(authority_rot[3], validity_rot[3])
    row_h = row_y1 - row_y0
    if row_h < 4:
        return None
    card_h = row_h / (_ID_BACK_ROW_BOTTOM - _ID_BACK_ROW_TOP)
    card_w = card_h * _ID_CARD_ASPECT
    if card_w < 30 or card_h < 18:
        return None
    row_x0 = min(authority_rot[0], validity_rot[0])
    card_x0 = row_x0 - _ID_BACK_MARGIN_X * card_w
    card_y0 = row_y0 - _ID_BACK_ROW_TOP * card_h
    return (card_x0, card_y0, card_x0 + card_w, card_y0 + card_h)


def _detect_id_card_back(rgb: np.ndarray, text_boxes: Sequence[tuple[str, Box]],
                         width: int, height: int):
    """反面字段级检测：定朝向 → 拟合卡 → 签发机关/有效期限模板映射。

    返回 ``(orient, fields, card_page_box)`` 或 ``None``（无法定向/拟合 → 整卡回退）。
    """
    authority_boxes = _find_anchor_boxes(text_boxes, ("签发机关",))
    validity_boxes = _find_anchor_boxes(text_boxes, ("有效期限",))
    if not authority_boxes or not validity_boxes:
        return None                    # 两行都读不出 → 不猜，整卡 fallback
    authority = authority_boxes[0][1]
    validity = validity_boxes[0][1]
    back_anchor_rects = [rect for _text, rect in
                         _find_anchor_boxes(text_boxes, _ID_BACK_ANCHORS)]
    title_boxes = [rect for _text, rect in
                   _find_anchor_boxes(text_boxes, ("居民身份证", "中华人民共和国"))]
    page_w = float(width)
    page_h = float(height)
    votes = _text_upright_votes(back_anchor_rects, (0.0, 0.0, page_w, page_h),
                                page_w, page_h)
    best_vote = max(votes) if votes else 0
    candidates = [k for k, vote in enumerate(votes) if vote == best_vote]
    best = None
    for k in candidates:
        auth_rot = _rot_forward_box(authority, k, page_w, page_h)
        valid_rot = _rot_forward_box(validity, k, page_w, page_h)
        card_rot = _fitted_back_card_box(auth_rot, valid_rot)
        if card_rot is None:
            continue
        card_w = max(1.0, card_rot[2] - card_rot[0])
        card_h = max(1.0, card_rot[3] - card_rot[1])
        row_left = (min(auth_rot[0], valid_rot[0]) - card_rot[0]) / card_w
        title_ratio = 0.5
        if title_boxes:
            title_rot = [_rot_forward_box(r, k, page_w, page_h) for r in title_boxes]
            cy = sum((b[1] + b[3]) / 2.0 for b in title_rot) / len(title_rot)
            title_ratio = (cy - card_rot[1]) / card_h
        # 正向：两行靠卡左（≈0.17）、标题靠上（≈0.1~0.3）
        score = abs(row_left - _ID_BACK_MARGIN_X) + abs(title_ratio - 0.20)
        if best is None or score < best[0]:
            best = (score, k, card_rot)
    if best is None:
        return None
    _score, k, card_rot = best
    cw = card_rot[2] - card_rot[0]
    ch = card_rot[3] - card_rot[1]
    fields: dict[str, Box] = {}
    for kind, (nx0, ny0, nx1, ny1) in _ID_BACK_FIELD_TEMPLATES.items():
        rot_box = (card_rot[0] + nx0 * cw, card_rot[1] + ny0 * ch,
                   card_rot[0] + nx1 * cw, card_rot[1] + ny1 * ch)
        local = _rot_back_box(rot_box, k, page_w, page_h)
        local = _clamp_box(local, page_w, page_h)
        if (local[2] - local[0]) < 20 or (local[3] - local[1]) < 8:
            return None
        fields[kind] = local
    card_local = _rot_back_box(card_rot, k, page_w, page_h)
    card_page = _clamp_box(card_local, page_w, page_h)
    if (card_page[2] - card_page[0]) < 40 or (card_page[3] - card_page[1]) < 24:
        return None
    fields = _refine_back_fields(fields, text_boxes, card_page)
    return k * 90, fields, card_page


def _refine_back_fields(fields: dict[str, Box],
                        text_boxes: Sequence[tuple[str, Box]],
                        card_page_box: Box) -> dict[str, Box]:
    """反面两行允许连标签一起遮：用同关键词 OCR 行框扩到整行，再裁进卡片。"""
    card_area = max(1.0, (card_page_box[2] - card_page_box[0])
                    * (card_page_box[3] - card_page_box[1]))
    keywords = {"id_authority": ("签发机关",),
                "id_validity": ("有效期限",)}
    for kind, keys in keywords.items():
        box = fields.get(kind)
        if box is None:
            continue
        for text, rect in text_boxes:
            norm = _norm_text(text)
            if not norm or not any(key in norm for key in keys):
                continue
            rect_area = max(1e-6, (rect[2] - rect[0]) * (rect[3] - rect[1]))
            if rect_area > card_area * 0.6:
                continue
            # 只并集「与本行在同一带」的框（中心落在字段框纵向附近）
            cy = (rect[1] + rect[3]) / 2.0
            if not (box[1] - (box[3] - box[1]) * 0.8 <= cy
                    <= box[3] + (box[3] - box[1]) * 0.8):
                continue
            box = (min(box[0], rect[0]), min(box[1], rect[1]),
                   max(box[2], rect[2]), max(box[3], rect[3]))
        fields[kind] = box
    pad_x = (card_page_box[2] - card_page_box[0]) * 0.05
    pad_y = (card_page_box[3] - card_page_box[1]) * 0.05
    allowed = (card_page_box[0] - pad_x, card_page_box[1] - pad_y,
               card_page_box[2] + pad_x, card_page_box[3] + pad_y)
    for kind, box in list(fields.items()):
        clipped = (max(box[0], allowed[0]), max(box[1], allowed[1]),
                   min(box[2], allowed[2]), min(box[3], allowed[3]))
        if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
            fields[kind] = clipped
    return fields


def _detect_id_document(rgb: np.ndarray, ctx: ScanContext,
                        text_boxes: Sequence[tuple[str, Box]]) -> list[ScanHit]:
    if not text_boxes:
        return []
    width, height = int(rgb.shape[1]), int(rgb.shape[0])
    if width < 32 or height < 32:
        return []
    # 文本块数量是「证件版式 vs 发票/合同正文」的关键信号：
    # - 证件照片 OCR 通常 4~12 块；
    # - 发票/合同文本页 20+ 块，里面的「名称/住所/统一社会信用代码」常被误当证件。
    block_count = len(text_boxes)
    if block_count >= 18:
        # 纯文本长文（发票/合同/函件）：不做证件判定，避免整页误涂
        return []
    text_rich = block_count >= 12
    best: tuple[float, str, list[Box], str, list[Box]] | None = None
    # conf, type, regions, note, anchors

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
        bank_number_boxes: list[Box] = []
        if doc_type == "bank_card":
            # 银行卡：强锚点（银行卡号/信用卡号…）单独可触发；
            # 或「卡组织/卡种文字 + Luhn 校验通过的卡号」组合触发（压低合同/对账单误报）。
            number_hits = _find_bank_card_numbers(text_boxes)
            if n_strong >= 1:
                triggered = True
                conf = 0.84
            elif n_weak >= 1 and number_hits:
                triggered = True
                conf = 0.78
            if triggered:
                bank_number_boxes = [rect for _digits, rect in number_hits]
        if spec.get("title"):
            # 营业执照：必须命中「标题样」的短文本框（「营业执照（副本）」），
            # 且至少再有一个字段锚点；正文里出现「营业执照复印件」不算。
            # 发票/合同等长文本页（>14 块）里的同名文字一律不算证件。
            short_title = [(text, rect) for text, rect in title if len(text) <= 14]
            if text_rich or block_count > 14:
                short_title = []
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
        anchor_rects.extend(bank_number_boxes)
        region = _id_region(anchor_rects, text_boxes, width, height)
        area = max(0.0, region[2] - region[0]) * max(0.0, region[3] - region[1])
        page_area = float(width) * float(height) or 1.0
        # 只有「证件版式」的小文本页才允许 25% 整页扩张；发票/合同文本页
        # （text_rich）即使面积够大也最多涂锚点邻域，避免整页误涂。
        # 银行卡（no_whole_page）永远不做整页扩张，只涂锚点邻域/整卡。
        whole_page = (not spec.get("no_whole_page")
                      and not text_rich) and block_count <= 12 \
            and area >= 0.25 * page_area
        if whole_page:
            region = (0.0, 0.0, float(width), float(height))
            conf = min(0.98, conf + 0.02)
        elif text_rich:
            conf = min(conf, 0.72)
        note = (f"engine=ocr;doc={doc_type};n={n_strong + n_medium + n_weak}"
                f"{';whole_page' if whole_page else ''}"
                f"{';textpage_region' if text_rich else ''}")
        cand = (conf, doc_type, [region], note, anchor_rects)
        if best is None or cand[0] > best[0]:
            best = cand

    if best is None:
        return []
    conf, doc_type, regions, note, anchor_rects = best
    if doc_type != "id_card":
        # 护照/驾驶证/营业执照/社保卡：保持原有整卡命中（字段级只做身份证）
        return [ScanHit(kind="id_document", rect=regions[0],
                        confidence=round(conf, 3), note=note)]

    # ── 身份证：正/反面分类 + 正面字段级定位（A 方案） ──
    whole_page = "whole_page" in note          # 卡几乎占满整页（用户实拍常见）
    page_rect = (0.0, 0.0, float(width), float(height))
    front_hits = _find_anchor_boxes(text_boxes, _ID_FRONT_ANCHORS)
    back_hits = _find_anchor_boxes(text_boxes, _ID_BACK_ANCHORS)
    side = "back" if len(back_hits) > len(front_hits) else "front"
    if side == "back":
        back = _detect_id_card_back(rgb, text_boxes, width, height)
        if back is None:
            # 无法定向/拟合失败 → 不猜，整卡 fallback=1（保证不漏）
            orient, card_box = _detect_id_card_fallback(
                rgb, text_boxes, anchor_rects, width, height, side="back")
            doc_rect = page_rect if whole_page else _expand_card_rect(
                card_box, width, height, ratio=0.10)
            return [ScanHit(
                kind="id_document", rect=doc_rect,
                confidence=round(min(conf, 0.88), 3),
                note=(f"engine=ocr;doc=id_card;side=back;fields=0;fallback=1;"
                      f"orient={orient}"))]
        orient, fields, card_page = back
        field_note = f"engine=ocr;doc=id_card;side=back;fields={len(fields)};orient={orient}"
        hits: list[ScanHit] = []
        for kind in ("id_authority", "id_validity"):
            box = fields.get(kind)
            if box is None:
                continue
            hits.append(ScanHit(kind=kind, rect=box, confidence=0.88,
                                note=field_note))
        doc_rect = page_rect if whole_page else _expand_card_rect(
            card_page, width, height, ratio=0.10)
        hits.append(ScanHit(kind="id_document", rect=doc_rect,
                            confidence=round(min(0.97, conf), 3),
                            note=field_note))
        return hits

    front = _detect_id_card_front(rgb, text_boxes, width, height)
    if front is None:
        # 无脸/无法确定朝向/字段框越界：不猜，整卡 fallback=1（保证不漏）
        orient, card_box = _detect_id_card_fallback(
            rgb, text_boxes, anchor_rects, width, height, side="front")
        doc_rect = page_rect if whole_page else _expand_card_rect(
            card_box, width, height, ratio=0.10)
        return [ScanHit(
            kind="id_document", rect=doc_rect,
            confidence=round(min(conf, 0.88), 3),
            note=(f"engine=ocr;doc=id_card;side=front;fields=0;fallback=1;"
                  f"orient={orient}"))]
    orient, fields, card_page = front
    field_note = f"engine=ocr;doc=id_card;side=front;fields={len(fields)};orient={orient}"
    hits: list[ScanHit] = []
    for kind in ("id_photo", "id_name", "id_number", "id_address"):
        box = fields.get(kind)
        if box is None:
            continue
        field_conf = 0.90 if kind in ("id_photo", "id_name", "id_number") else 0.86
        hits.append(ScanHit(kind=kind, rect=box, confidence=field_conf,
                            note=field_note))
    doc_rect = page_rect if whole_page else _expand_card_rect(
        card_page, width, height, ratio=0.10)
    hits.append(ScanHit(kind="id_document", rect=doc_rect,
                        confidence=round(min(0.97, conf), 3),
                        note=field_note))
    return hits


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


#: 车牌底色（HSV 下限/上限 + 字符极性）：蓝/绿牌白字，黄牌黑字。
_PLATE_COLOR_SPECS: tuple[tuple[str, tuple[int, int, int], tuple[int, int, int], str], ...] = (
    ("blue", (100, 80, 40), (135, 255, 255), "white"),
    ("green", (45, 60, 40), (95, 255, 255), "white"),
    ("yellow", (15, 80, 80), (40, 255, 255), "black"),
)


def _plate_color_candidates(rgb: np.ndarray) -> list[dict]:
    """全图 HSV 找「完整牌面」候选（蓝/绿/黄底 + 白/黑字），返回原图坐标。

    这是车牌的主检测路径：即使 OCR 读不出字，只要牌面完整也能给出矩形；
    OCR 文本只用于确认归属（见 ``_detect_plates``）。
    每个候选：``{"rect", "kind", "ratio", "quality", "area_frac", "aspect"}``。
    """
    if cv2 is None or rgb is None or getattr(rgb, "size", 0) == 0:
        return []
    try:
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
    except Exception:
        return []
    cap = _env_int("YINZHAN_SCAN_PLATE_MAX_SIDE", 2000, minimum=320, maximum=6000)
    generic = _env_int("YINZHAN_SCAN_MAX_SIDE", 3000, minimum=320, maximum=10000)
    cap = min(cap, generic)
    scale = min(1.0, cap / float(max(height, width))) if max(height, width) else 1.0
    if scale < 1.0:
        try:
            work = cv2.resize(rgb, (max(1, int(width * scale)),
                                    max(1, int(height * scale))),
                              interpolation=cv2.INTER_AREA)
        except Exception:
            return []
    else:
        work = rgb
    try:
        hsv = cv2.cvtColor(work, cv2.COLOR_RGB2HSV)
    except Exception:
        return []
    page_area = float(work.shape[0] * work.shape[1]) or 1.0
    close_kernel = np.ones((5, 5), np.uint8)
    open_kernel = np.ones((3, 3), np.uint8)
    out: list[dict] = []
    for kind, low, high, polarity in _PLATE_COLOR_SPECS:
        try:
            mask = cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
        except Exception:
            continue
        for contour in contours:
            try:
                area = float(cv2.contourArea(contour))
            except Exception:
                continue
            if area < 500:
                continue
            try:
                (rw, rh) = cv2.minAreaRect(contour)[1]
            except Exception:
                continue
            if rw < 40 or rh < 10:
                continue
            long_side, short_side = max(rw, rh), min(rw, rh)
            aspect = long_side / float(max(1.0, short_side))
            if not 2.0 <= aspect <= 4.8:
                continue
            rect_fill = area / float(max(1.0, rw * rh))
            if rect_fill < 0.62:
                continue
            try:
                bx, by, bw, bh = cv2.boundingRect(contour)
            except Exception:
                continue
            if bw <= 0 or bh <= 0:
                continue
            area_frac = (bw * bh) / page_area
            if not (0.0006 <= area_frac <= 0.30):
                continue
            # 轴对齐长宽比：牌面在画面里必须横放（允许轻微透视/旋转），
            # 过滤水面/界面色带等竖向大色块。
            bbox_aspect = bw / float(max(1, bh))
            if not 1.7 <= bbox_aspect <= 5.0:
                continue
            crop = hsv[by:by + bh, bx:bx + bw]
            if crop.size == 0:
                continue
            if polarity == "white":
                polarity_mask = (crop[:, :, 1] < 80) & (crop[:, :, 2] > 130)
            else:
                polarity_mask = crop[:, :, 2] < 95
            ratio = float(polarity_mask.mean())
            if ratio < (0.045 if polarity == "white" else 0.035):
                continue
            # 字符笔画感：真实牌面有多个白/黑连通块（过滤纯色块/路牌底色）
            char_like = 0
            try:
                count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
                    polarity_mask.astype(np.uint8), 8)
                for idx in range(1, count):
                    _x, _y, cw, ch, ca = (int(v) for v in stats[idx][:5])
                    if ch < max(3, int(bh * 0.10)) or cw < 2 or ca < 12:
                        continue
                    if ch > bh * 0.90:
                        continue
                    char_like += 1
            except Exception:
                char_like = 0
            if char_like < 3:
                continue
            quality = (min(1.0, rect_fill) * 0.40
                       + min(1.0, ratio / 0.18) * 0.30
                       + min(1.0, char_like / 8.0) * 0.30)
            out.append({
                "rect": (bx / scale, by / scale,
                         (bx + bw) / scale, (by + bh) / scale),
                "kind": kind,
                "ratio": round(ratio, 4),
                "quality": round(quality, 4),
                "area_frac": round(area_frac, 5),
                "aspect": round(aspect, 3),
                "bbox_aspect": round(bbox_aspect, 3),
                "touches_border": bool(bx <= 1 or by <= 1
                                       or bx + bw >= work.shape[1] - 1
                                       or by + bh >= work.shape[0] - 1),
            })
    if not out:
        return []
    # 去重：同一张牌被多通道/多轮廓命中，保留质量高、面积大的那个
    out.sort(key=lambda item: (item["quality"], item["area_frac"]), reverse=True)
    kept: list[dict] = []
    for cand in out:
        if any(_iou(cand["rect"], prev["rect"]) >= 0.30
               or _ioc(cand["rect"], prev["rect"]) >= 0.80 for prev in kept):
            continue
        kept.append(cand)
    return kept[:6]


def _plate_candidate_for_box(box: Box, candidates: Sequence[dict]) -> int | None:
    """OCR 车牌框落在哪个颜色候选里（中心包含/重叠），返回候选下标。"""
    tcx = (box[0] + box[2]) / 2.0
    tcy = (box[1] + box[3]) / 2.0
    text_area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    best_index, best_score = None, 0.0
    for index, cand in enumerate(candidates):
        rect = cand["rect"]
        center_inside = rect[0] <= tcx <= rect[2] and rect[1] <= tcy <= rect[3]
        overlap = _iou(box, rect)
        if not center_inside and overlap < 0.20:
            continue
        cand_area = max(1.0, (rect[2] - rect[0]) * (rect[3] - rect[1]))
        area_ratio = cand_area / text_area
        if area_ratio < 0.9 or area_ratio > 8.0:
            continue
        score = overlap + (0.5 if center_inside else 0.0)
        if score > best_score:
            best_index, best_score = index, score
    return best_index


def _detect_plates(rgb: np.ndarray, ctx: ScanContext,
                   text_boxes: Sequence[tuple[str, Box]]) -> list[ScanHit]:
    """车牌检测：颜色牌面为主、OCR 文本确认归属。

    1. 全图颜色分割找完整牌面（蓝/绿/黄底 + 白/黑字 + 矩形拟合）；OCR 文本命中
       则与色块取并集（比 OCR 框更宽，盖住整个牌面），置信度 0.95；
    2. OCR 未读出的完整牌面作为候选回退（低置信 0.55，note 标 unconfirmed），
       由上层（jobs 默认 pending）让用户确认；
    3. 纯 OCR 命中（颜色分割失败/异常底色）仍保留原有文本路径。
    """
    width, height = int(rgb.shape[1]), int(rgb.shape[0])
    if width < 32 or height < 32:
        return []
    text_hits = _plate_hits_from_text(text_boxes, float(width), float(height))
    color_enabled = _env_bool("YINZHAN_SCAN_PLATE_COLOR", True) and cv2 is not None
    candidates = _plate_color_candidates(rgb) if color_enabled else []
    hits: list[tuple[Box, float, str, str]] = []
    used: set[int] = set()
    for box, conf, value, engine in text_hits:
        index = _plate_candidate_for_box(box, candidates)
        if index is not None:
            cand = candidates[index]
            rect = cand["rect"]
            merged = (min(box[0], rect[0]), min(box[1], rect[1]),
                      max(box[2], rect[2]), max(box[3], rect[3]))
            merged = _expand_box(merged, 0.025, float(width), float(height))
            hits.append((merged, 0.95, value,
                         f"color+ocr/{cand['kind']}"))
            used.add(index)
        else:
            hits.append((box, conf, value, engine))
    # 不可读回退：没有 OCR 确认的完整牌面，只取质量最高的一个候选，低置信 + note
    if candidates and _env_bool("YINZHAN_SCAN_PLATE_UNCONFIRMED", True):
        unused = [(idx, cand) for idx, cand in enumerate(candidates)
                  if idx not in used]
        if unused:
            idx, cand = max(unused, key=lambda item: (item[1]["quality"],
                                                      item[1]["area_frac"]))
            strict = (cand["quality"] >= 0.72
                      and 2.3 <= cand["aspect"] <= 4.4
                      and cand["area_frac"] <= 0.25
                      and cand["ratio"] >= 0.06
                      and not cand.get("touches_border"))
            if strict:
                hits.append((_expand_box(cand["rect"], 0.02,
                                         float(width), float(height)),
                             0.55, "", f"color/{cand['kind']};unconfirmed"))
    if not hits:
        return []
    # 去重：已确认的完整牌面优先；同牌 OCR 框与色块框只留大的那个
    hits.sort(key=lambda item: (bool(item[2]),
                                (item[0][2] - item[0][0]) * (item[0][3] - item[0][1])),
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


# ────────────────────────────── 聊天截图逐项识别（P1-A） ──────────────────────────────

#: 聊天截图逐项命中 kind（与 Lead 的 jobs._VISUAL_SPEC 对齐，由 Lead 接）
CHAT_KINDS: tuple[str, ...] = (
    "chat_avatar", "chat_name", "chat_group", "chat_time", "chat_input",
)

_CHAT_TIME_RE = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)")
_CHAT_DATE_RE = re.compile(
    r"(?:前天|昨天|今天|星期[一二三四五六日天]|"
    r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日)")
_CHAT_INPUT_WORDS = ("发送", "按住", "说话", "表情", "语音", "聊天信息")


def _chat_text_matches_time(text: str) -> bool:
    value = _norm_text(text)
    return bool(value) and bool(_CHAT_TIME_RE.search(value))


def _chat_text_matches_date(text: str) -> bool:
    value = _norm_text(text)
    return bool(value) and bool(_CHAT_DATE_RE.search(value))


def _chat_color_distance(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a, dtype=np.float32)
                                - np.asarray(b, dtype=np.float32)))


def _chat_page_background(rgb: np.ndarray):
    """取页面左右边距（避开顶部状态栏/底部输入条）的中位色作为聊天背景估计。"""
    try:
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
        if height < 8 or width < 8:
            return (255.0, 255.0, 255.0), 0.0
        y0 = max(0, int(0.10 * height))
        y1 = max(y0 + 1, int(0.90 * height))
        strip = max(1, int(0.01 * width))
        samples = np.concatenate([
            rgb[y0:y1, 0:strip, :].reshape(-1, 3),
            rgb[y0:y1, width - strip:width, :].reshape(-1, 3),
        ]).astype(np.float32)
        return tuple(np.median(samples, axis=0)), float(samples.std())
    except Exception:
        return (255.0, 255.0, 255.0), 0.0


def _chat_cluster_rows(text_boxes: Sequence[tuple[str, Box]],
                       width: int, height: int) -> tuple[list[dict], float]:
    """把 OCR 文本框按纵向聚成「消息行」，并返回中位字高。"""
    items = []
    heights = []
    for text, rect in text_boxes:
        value = _norm_text(text)
        if not value or len(value) > 80:
            continue
        x0, y0, x1, y1 = _clamp_box(rect, float(width), float(height))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        items.append({"text": value, "rect": (x0, y0, x1, y1),
                      "cy": (y0 + y1) / 2.0})
        heights.append(y1 - y0)
    if len(items) < 3:
        return [], 0.0
    median_h = float(np.median(heights))
    items.sort(key=lambda item: item["cy"])
    rows: list[dict] = []
    for item in items:
        placed = False
        for row in reversed(rows):
            overlap = (min(row["rect"][3], item["rect"][3])
                       - max(row["rect"][1], item["rect"][1]))
            if overlap >= min(median_h * 0.35,
                              (item["rect"][3] - item["rect"][1]) * 0.5):
                row["texts"].append(item["text"])
                row["rect"] = (min(row["rect"][0], item["rect"][0]),
                               min(row["rect"][1], item["rect"][1]),
                               max(row["rect"][2], item["rect"][2]),
                               max(row["rect"][3], item["rect"][3]))
                row["cy"] = (row["rect"][1] + row["rect"][3]) / 2.0
                placed = True
                break
        if not placed:
            rows.append({"rect": item["rect"], "texts": [item["text"]],
                         "cy": item["cy"]})
    # 行过滤：排除状态栏/标题/输入条，以及表格那种整宽长行
    message_rows: list[dict] = []
    for row in rows:
        x0, y0, x1, y1 = row["rect"]
        row_w = x1 - x0
        row_h = y1 - y0
        if not (0.085 * height <= row["cy"] <= 0.90 * height):
            continue
        if row_w > 0.78 * width or row_h > 0.20 * height:
            continue
        if row_w < 0.03 * width:
            continue
        cx = (x0 + x1) / 2.0
        if cx < 0.48 * width:
            side = "left"
        elif cx > 0.52 * width:
            side = "right"
        else:
            continue
        row["side"] = side
        row["row_w"] = row_w
        message_rows.append(row)
    return message_rows, median_h


def _chat_has_alternation(rows: Sequence[dict]) -> bool:
    transitions = 0
    for prev, cur in zip(rows, rows[1:]):
        if prev["side"] != cur["side"]:
            transitions += 1
    left = sum(1 for row in rows if row["side"] == "left")
    right = sum(1 for row in rows if row["side"] == "right")
    return left >= 1 and right >= 1 and (transitions >= 1 or (left >= 2 and right >= 2))


def _chat_detect_avatars(rgb: np.ndarray, rows: Sequence[dict],
                         width: int, height: int) -> list[Box]:
    """在左右边距内找与消息行纵向对齐的彩色/高方差小方块（头像）。"""
    if cv2 is None or not rows:
        return []
    try:
        height_i, width_i = int(height), int(width)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        rgb_f = rgb.astype(np.int16)
        maximum = rgb_f.max(axis=2)
        minimum = rgb_f.min(axis=2)
        saturation = maximum - minimum
        bg, _bg_std = _chat_page_background(rgb)
        color_dist = np.linalg.norm(rgb_f - np.asarray(bg, dtype=np.int16),
                                    axis=2)
        mask = ((saturation > 45) | (color_dist > 55)).astype(np.uint8)
        margin_right = min(width_i, int(round(width * 0.18)))
        margin_left = margin_right
        keep = np.zeros_like(mask)
        keep[:, :margin_left] = mask[:, :margin_left]
        keep[:, width_i - margin_right:] = mask[:, width_i - margin_right:]
        keep[int(0.10 * height_i):int(0.90 * height_i), :] = keep[
            int(0.10 * height_i):int(0.90 * height_i), :]
        keep[:int(0.10 * height_i), :] = 0
        keep[int(0.90 * height_i):, :] = 0
        keep = cv2.morphologyEx(keep, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            keep, 8)
    except Exception:
        return []
    median_row_h = float(np.median([row["rect"][3] - row["rect"][1]
                                    for row in rows])) if rows else 20.0
    min_side = max(12.0, 0.025 * min(width, height))
    max_side = max(min_side + 4.0, 0.16 * width)
    boxes: list[Box] = []
    for index in range(1, count):
        x, y, bw, bh, area = (int(v) for v in stats[index][:5])
        side = (bw + bh) / 2.0
        if not (min_side <= side <= max_side):
            continue
        if not (0.55 <= bw / float(max(1, bh)) <= 1.80):
            continue
        center_x = x + bw / 2.0
        # 头像必须贴着屏幕左/右边缘（排除气泡边缘被误当头像）
        if not (center_x <= 0.10 * width or center_x >= 0.90 * width):
            continue
        fill = area / float(max(1, bw * bh))
        if fill < 0.35:
            continue
        cy = y + bh / 2.0
        near_row = any(abs(cy - row["cy"]) <= max(median_row_h * 2.2,
                                                  0.04 * height)
                       for row in rows)
        if not near_row:
            continue
        try:
            patch = rgb[max(0, y):min(height_i, y + bh),
                        max(0, x):min(width_i, x + bw)]
            patch_gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
            if float(patch_gray.std()) < 5.0 and float(
                    patch.reshape(-1, 3).std(axis=0).max()) < 10.0:
                continue
        except Exception:
            continue
        boxes.append((max(0.0, x - 3.0), max(0.0, y - 3.0),
                      min(float(width), x + bw + 3.0),
                      min(float(height), y + bh + 3.0)))
    # 去重
    kept: list[Box] = []
    for box in sorted(boxes, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])):
        if any(_iou(box, prev) > 0.35 for prev in kept):
            continue
        kept.append(box)
    return kept


def _chat_bubble_rows(rgb: np.ndarray, rows: Sequence[dict],
                      width: int, height: int) -> list[dict]:
    """判断消息行是否被「气泡底色」包裹（主色占多数且与页面背景有对比）。"""
    bg, _std = _chat_page_background(rgb)
    out: list[dict] = []
    for row in rows:
        x0, y0, x1, y1 = row["rect"]
        pad_x = max(5, int((y1 - y0) * 1.2))
        pad_y = max(3, int((y1 - y0) * 0.5))
        cx0 = max(0, int(x0 - pad_x)); cy0 = max(0, int(y0 - pad_y))
        cx1 = min(int(width), int(x1 + pad_x)); cy1 = min(int(height),
                                                          int(y1 + pad_y))
        if cx1 - cx0 < 8 or cy1 - cy0 < 6:
            continue
        patch = rgb[cy0:cy1, cx0:cx1]
        try:
            quantized = (patch.reshape(-1, 3) // 24).astype(np.int32)
            codes = quantized[:, 0] * 121 + quantized[:, 1] * 11 + quantized[:, 2]
            values, counts = np.unique(codes, return_counts=True)
            dominant = values[int(np.argmax(counts))]
            frac = float(counts.max()) / float(len(codes))
            dom_rgb = (
                (dominant // 121) * 24 + 12,
                ((dominant // 11) % 11) * 24 + 12,
                (dominant % 11) * 24 + 12,
            )
        except Exception:
            continue
        if frac < 0.42:
            continue
        if _chat_color_distance(dom_rgb, bg) < 14.0:
            continue
        out.append(row)
    return out


def _chat_detect_input(rgb: np.ndarray, text_boxes: Sequence[tuple[str, Box]],
                       width: int, height: int) -> Box | None:
    """底部输入条：关键词文本 + 底部整行低方差条带。"""
    if cv2 is None:
        return None
    height_i, width_i = int(height), int(width)
    bottom_band = int(0.84 * height_i)
    keyword_boxes: list[Box] = []
    for text, rect in text_boxes:
        value = _norm_text(text)
        if not value or not any(word in value for word in _CHAT_INPUT_WORDS):
            continue
        if rect[1] >= 0.85 * height:
            keyword_boxes.append(_clamp_box(rect, float(width), float(height)))
    run = None
    try:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        run_start = None
        for y in range(bottom_band, height_i):
            row_std = float(gray[y].std())
            if row_std < 14.0:
                if run_start is None:
                    run_start = y
            else:
                if run_start is not None and y - run_start >= max(
                        6, int(0.012 * height_i)):
                    run = (run_start, y)
                run_start = None
        if run_start is not None and height_i - run_start >= max(
                6, int(0.012 * height_i)):
            run = (run_start, height_i)
    except Exception:
        run = None
    candidates = []
    if keyword_boxes:
        y0 = min(box[1] for box in keyword_boxes) - 0.012 * height
        candidates.append((max(0.82 * height, y0), float(height)))
    if run is not None:
        candidates.append((float(run[0]), float(height)))
    for y0, y1 in candidates:
        y0 = max(0.80 * height, min(y0, height))
        if (y1 - y0) < 0.025 * height:
            continue
        if (y1 - y0) > 0.22 * height:
            continue
        return (0.0, float(y0), float(width), float(y1))
    return None


def _detect_chat(rgb: np.ndarray, ctx: ScanContext,
                 text_boxes: Sequence[tuple[str, Box]]) -> list[ScanHit]:
    """识别手机聊天截图并返回逐项可涂区域（不整屏黑）。

    保守判定：至少 3 条布局信号 + ≥3 条左右消息行 + 左右都有 + 有头像/气泡。
    只在 fmt 缺省或 fmt=image 时启用（PDF/文档页直接跳过）。
    """
    if not text_boxes or cv2 is None:
        return []
    options = getattr(ctx, "options", None) or {}
    fmt = str(options.get("fmt") or "").strip().lower()
    if fmt and fmt != "image":
        return []
    try:
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
    except Exception:
        return []
    if width < 120 or height < 120:
        return []
    block_count = len(text_boxes)
    if not (5 <= block_count <= 160):
        return []
    ratio = width / float(height)
    if not (0.18 <= ratio <= 4.5):
        return []
    rows, median_h = _chat_cluster_rows(text_boxes, width, height)
    if len(rows) < 3 or not _chat_has_alternation(rows):
        return []
    # 长文本/整宽行占多数 → 表格/文档，不是聊天
    long_texts = sum(1 for text, _rect in text_boxes
                     if len(_norm_text(text)) > 60)
    if long_texts > max(1, block_count // 3):
        return []

    top_band = 0.09 * height
    title_candidates = []
    status_time_boxes: list[Box] = []
    for text, rect in text_boxes:
        value = _norm_text(text)
        if not value:
            continue
        x0, y0, x1, y1 = rect
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        if y1 <= top_band + 4 and _chat_text_matches_time(value):
            status_time_boxes.append(rect)
        if (0.015 * height <= cy <= 0.105 * height
                and abs(cx - 0.5 * width) <= 0.22 * width
                and len(value) <= 20
                and not _chat_text_matches_time(value)):
            title_candidates.append((abs(cx - 0.5 * width), value, rect))
    title = min(title_candidates, key=lambda item: item[0]) if title_candidates else None
    avatars = _chat_detect_avatars(rgb, rows, width, height)
    bubble_rows = _chat_bubble_rows(rgb, rows, width, height)
    input_box = _chat_detect_input(rgb, text_boxes, width, height)

    signals = {
        "status_time": bool(status_time_boxes),
        "title": title is not None,
        "avatars": len(avatars) >= 1,
        "bubbles": len(bubble_rows) >= 2,
        "input": input_box is not None,
    }
    score = sum(1 for value in signals.values() if value)
    # 保守门槛：≥3 条布局信号 + 左右消息行 + 至少 1 个头像。
    # 气泡色块在浅色/自定义主题下可能因背景对比不足识别不到（实测浅色聊天图
    # 真实 OCR 下 avatars/rows 都成立但 bubbles=[]），此时允许用「底部输入条」
    # 兜底，避免整类浅色聊天截图漏检；仍要求头像 + 行交替，防普通截图误报。
    if score < 3 or not signals["avatars"]:
        return []
    if not signals["bubbles"] and not signals["input"]:
        return []

    hits: list[ScanHit] = []
    page_area = float(width) * float(height) or 1.0

    def _emit(kind: str, box: Box, conf: float, note: str) -> None:
        rect = _clamp_box(_expand_box(box, 0.02, float(width), float(height)),
                          float(width), float(height))
        area = max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])
        if area <= 0 or area > page_area * 0.35:      # 聊天逐项框，绝不能是整屏
            return
        hits.append(ScanHit(kind=kind, rect=rect, confidence=round(conf, 3),
                            note=f"engine=layout;chat=1;{note}"))

    # 头像
    for box in avatars:
        side = "left" if (box[0] + box[2]) / 2.0 < width * 0.5 else "right"
        _emit("chat_avatar", box, 0.72, f"role=avatar;side={side}")

    # 顶部标题：单聊=联系人昵称，群聊=群名称
    group_cue = False
    if title is not None:
        title_text = title[1]
        group_cue = ("群" in title_text
                     or bool(re.search(r"[（(]\d+[）)]", title_text))
                     or "成员" in title_text or "公告" in title_text)
        _emit("chat_group" if group_cue else "chat_name",
              title[2], 0.82,
              "role=group;src=title" if group_cue else "role=name;src=title")

    # 群聊里的发送者昵称：左气泡行上方的小字
    if group_cue:
        left_rows = [row for row in rows if row["side"] == "left"]
        for row in left_rows:
            row_top = row["rect"][1]
            for text, rect in text_boxes:
                value = _norm_text(text)
                if not value or len(value) > 12:
                    continue
                if _chat_text_matches_time(value) or _chat_text_matches_date(value):
                    continue
                cy = (rect[1] + rect[3]) / 2.0
                if not (row_top - max(median_h * 2.6, 0.05 * height) <= cy
                        < row_top):
                    continue
                if abs(rect[0] - row["rect"][0]) > 0.10 * width:
                    continue
                _emit("chat_name", rect, 0.70, "role=name;src=sender")
                break

    # 时间：状态栏时间 + 居中消息时间戳/日期
    for rect in status_time_boxes[:2]:
        _emit("chat_time", rect, 0.78, "role=time;src=status")
    for text, rect in text_boxes:
        value = _norm_text(text)
        if not value:
            continue
        cx = (rect[0] + rect[2]) / 2.0
        cy = (rect[1] + rect[3]) / 2.0
        if not (0.085 * height <= cy <= 0.90 * height):
            continue
        if abs(cx - 0.5 * width) <= 0.16 * width and (
                _chat_text_matches_time(value) or _chat_text_matches_date(value)):
            _emit("chat_time", rect, 0.75, "role=time;src=msg")

    # 底部输入条整块
    if input_box is not None:
        _emit("chat_input", input_box, 0.75, "role=input;src=bar")

    # 同类去重
    deduped: list[ScanHit] = []
    for hit in hits:
        if any(hit.kind == prev.kind and _iou(hit.rect, prev.rect) > 0.45
               for prev in deduped):
            continue
        deduped.append(hit)
    return deduped


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

#: 身份证关键字段 kind：落在整卡框里也必须保留（A 方案 fields 模式要用）
_ID_FIELD_KINDS = ("id_photo", "id_name", "id_number", "id_address",
                   "id_authority", "id_validity")


def _prune_nested(hits: list[ScanHit]) -> list[ScanHit]:
    """证件整图命中后，落在其中的脸/车牌框不再单独上报（避免重复实体）。

    注意：身份证字段 kind（id_photo/id_name/id_number/id_address）虽然在整卡框内，
    但 A 方案 fields 模式必须用它们，不能在这里被当成重复删掉；由 jobs 按 mode 过滤。
    """
    docs = [hit for hit in hits if hit.kind == "id_document"]
    if not docs:
        return hits
    out: list[ScanHit] = []
    for hit in hits:
        if hit.kind == "id_document" or hit.kind in _ID_FIELD_KINDS:
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

    if text_boxes and _env_bool("YINZHAN_SCAN_CHAT", True):
        try:
            hits.extend(_detect_chat(image_rgb, ctx, text_boxes))
        except Exception as exc:      # 单类失败不能带崩其它目标
            ctx.warnings.append(f"visual_targets:chat 失败：{exc}")

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
    "_detect_chat", "CHAT_KINDS",
    "_detect_road_sign", "_detect_door_address",
    "_detect_id_card_front", "_detect_id_card_back", "_detect_id_card_fallback",
    "_ID_FIELD_TEMPLATES", "_ID_BACK_FIELD_TEMPLATES",
    "_rot_forward_box", "_rot_back_box",
    "_fitted_card_box",
    "_FACE_CASCADES_OVERRIDE",
]
