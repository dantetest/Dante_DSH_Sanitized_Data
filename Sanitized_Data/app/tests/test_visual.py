# -*- coding: utf-8 -*-
"""图像 P0 扫描器测试（visual_targets：人脸 / 证件整图与身份证字段 / 车牌）。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_visual.py
    cd app\\server && python -X utf8 ..\\tests\\test_visual.py

设计说明：
- 纯离线、无网络、不写仓库：夹具默认生成到临时目录（tempfile.mkdtemp()）。
  想人工看一眼夹具时，加环境变量 ``YINZHAN_WRITE_FIXTURES=1``，会写到
  ``app/tests/fixtures/visual/``（该目录不入库二进制，由本脚本按需生成）。
- 人脸正样本：仓库内没有可再分发的真人照片，用「注入假级联」验证
  分块缩放映射、整头外扩、去重、置信度换算、文档页跳过侧脸等逻辑；
  真实照片召回/误报测试集见 docs/impl/visual.md §9（用户测试集只读实测）。
- 身份证关键字段（A 方案）：合成正面/反面卡（虚构姓名/号码/地址 + 暗红头像块，
  0/90/180/270 四向）+ 注入「最大暗块」假级联，验证朝向/模板映射/整卡回退；
  无脸卡走 fallback=1。
- 负样本：真实 ``samples/名片.png``（跑真 OCR 取文本框）、``samples/正射影像.tif``
  首帧、纯白页，断言不新增 face / plate / id_document 误报（红线要求）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import numpy as np                                    # noqa: E402
from PIL import Image, ImageDraw, ImageFont           # noqa: E402

from yinzhan import mediascan                         # noqa: E402
from yinzhan import visual_targets                    # noqa: E402
from yinzhan.mediascan import ScanContext, ScanHit   # noqa: E402

SAMPLES = REPO / "samples"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "visual"

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def _font(size: int):
    for candidate in _FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:             # pragma: no cover
        return None


def _draw_text(draw: ImageDraw.ImageDraw, xy, text: str, size: int,
               fill=(25, 25, 25)) -> None:
    font = _font(size)
    if font is None:              # pragma: no cover
        return
    draw.text(xy, text, font=font, fill=fill)


def _new_canvas(width: int, height: int, color=(252, 250, 245)) -> Image.Image:
    return Image.new("RGB", (width, height), color)


def _fixtures_root() -> Path:
    if os.environ.get("YINZHAN_WRITE_FIXTURES") == "1":
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        return FIXTURE_DIR
    return Path(tempfile.mkdtemp(prefix="yinzhan_visual_"))


def build_fixtures() -> dict:
    """生成合成夹具；返回 {name: {"path","size","text_boxes","expect"}}。"""
    root = _fixtures_root()
    out: dict = {}

    # 1) 蓝牌照片：浅灰背景 + 蓝底白字车牌
    img = _new_canvas(1200, 800, (205, 205, 200))
    draw = ImageDraw.Draw(img)
    plate = (300, 500, 740, 640)
    draw.rectangle(plate, fill=(0, 70, 180))
    _draw_text(draw, (330, 520), "沪A12345", 74, (255, 255, 255))
    path = root / "plate_blue.png"
    img.save(path)
    out["plate_blue"] = {
        "path": path, "size": img.size, "plate_rect": plate,
        "text_boxes": [{"text": "沪A12345", "rect": (335, 525, 700, 620)}],
    }

    # 2) 新能源绿牌（8 位）
    img = _new_canvas(1200, 800, (210, 212, 208))
    draw = ImageDraw.Draw(img)
    plate = (250, 420, 730, 560)
    draw.rectangle(plate, fill=(30, 120, 60))
    _draw_text(draw, (272, 440), "粤BD12345", 70, (255, 255, 255))
    path = root / "plate_green.png"
    img.save(path)
    out["plate_green"] = {
        "path": path, "size": img.size, "plate_rect": plate,
        "text_boxes": [{"text": "粤BD12345", "rect": (275, 445, 700, 545)}],
    }

    # 3) 黄牌（黑字黄底），验证极性反转
    img = _new_canvas(1200, 800, (208, 208, 198))
    draw = ImageDraw.Draw(img)
    plate = (300, 430, 720, 570)
    draw.rectangle(plate, fill=(240, 200, 20))
    _draw_text(draw, (330, 450), "京C88888", 70, (20, 20, 20))
    path = root / "plate_yellow.png"
    img.save(path)
    out["plate_yellow"] = {
        "path": path, "size": img.size, "plate_rect": plate,
        "text_boxes": [{"text": "京C88888", "rect": (335, 455, 690, 550)}],
    }

    # 4) 路牌照片（蓝底白字路名）——不得被当成车牌
    img = _new_canvas(1200, 800, (190, 190, 186))
    draw = ImageDraw.Draw(img)
    draw.rectangle((260, 240, 960, 520), fill=(0, 80, 180))
    _draw_text(draw, (320, 330), "中山北路", 96, (255, 255, 255))
    path = root / "roadsign.png"
    img.save(path)
    out["roadsign"] = {
        "path": path, "size": img.size,
        "text_boxes": [{"text": "中山北路", "rect": (320, 330, 900, 440)}],
    }

    # 4) 身份证照片（卡片区域 < 页面 25%）
    img = _new_canvas(2200, 1600, (250, 248, 242))
    draw = ImageDraw.Draw(img)
    card = (200, 200, 1000, 600)
    draw.rectangle(card, outline=(180, 170, 150), width=3)
    _draw_text(draw, (230, 230), "中华人民共和国居民身份证", 36)
    _draw_text(draw, (230, 320), "姓名 张三", 30)
    _draw_text(draw, (230, 380), "住址 北京市朝阳区建国路88号", 26)
    _draw_text(draw, (230, 480), "公民身份号码", 32)
    _draw_text(draw, (520, 480), "11010519491231002X", 34)
    path = root / "idcard.png"
    img.save(path)
    out["idcard"] = {
        "path": path, "size": img.size, "card_rect": card,
        "text_boxes": [
            {"text": "中华人民共和国居民身份证", "rect": (225, 225, 800, 270)},
            {"text": "姓名 张三", "rect": (228, 315, 480, 355)},
            {"text": "住址 北京市朝阳区建国路88号", "rect": (228, 375, 900, 410)},
            {"text": "公民身份号码", "rect": (228, 475, 510, 520)},
            {"text": "11010519491231002X", "rect": (515, 475, 900, 520)},
        ],
    }

    # 5) 整页证件（锚点铺满页面 → 应返回整页）
    img = _new_canvas(600, 420, (248, 246, 240))
    draw = ImageDraw.Draw(img)
    _draw_text(draw, (20, 20), "中华人民共和国居民身份证", 26)
    _draw_text(draw, (20, 340), "公民身份号码 11010519491231002X", 22)
    path = root / "idcard_whole.png"
    img.save(path)
    out["idcard_whole"] = {
        "path": path, "size": img.size,
        "text_boxes": [
            {"text": "中华人民共和国居民身份证", "rect": (18, 18, 590, 60)},
            {"text": "公民身份号码 11010519491231002X", "rect": (18, 330, 590, 400)},
        ],
    }

    # 6) 护照资料页（标题 + MRZ → 整页）
    img = _new_canvas(1200, 1600, (248, 248, 240))
    draw = ImageDraw.Draw(img)
    _draw_text(draw, (120, 80), "中华人民共和国护照", 40)
    _draw_text(draw, (120, 200), "护照号码 E12345678", 28)
    _draw_text(draw, (80, 1400), "P<CHNWANG<<WU<<<<<<<<<<<<<<<<<<<<<<<<", 22)
    path = root / "passport.png"
    img.save(path)
    out["passport"] = {
        "path": path, "size": img.size,
        "text_boxes": [
            {"text": "中华人民共和国护照", "rect": (115, 75, 700, 130)},
            {"text": "护照号码 E12345678", "rect": (115, 195, 700, 240)},
            {"text": "P<CHNWANG<<WU<<<<<<<<<<<<<<<<<<<<<<<<",
             "rect": (78, 1395, 1100, 1440)},
        ],
    }

    # 7) 营业执照（标题 + 统一社会信用代码 + 字段）
    img = _new_canvas(1400, 1000, (246, 242, 228))
    draw = ImageDraw.Draw(img)
    _draw_text(draw, (80, 60), "营业执照（副本）", 46)
    _draw_text(draw, (80, 180), "统一社会信用代码 91350100M000100Y43", 26)
    _draw_text(draw, (80, 250), "名称 星辰科技有限公司", 30)
    _draw_text(draw, (80, 320), "法定代表人 李四", 30)
    _draw_text(draw, (80, 390), "注册资本 人民币贰佰万元整", 28)
    path = root / "business_license.png"
    img.save(path)
    out["business_license"] = {
        "path": path, "size": img.size,
        "text_boxes": [
            {"text": "营业执照（副本）", "rect": (75, 55, 520, 120)},
            {"text": "统一社会信用代码 91350100M000100Y43",
             "rect": (75, 175, 800, 215)},
            {"text": "名称 星辰科技有限公司", "rect": (75, 245, 620, 290)},
            {"text": "法定代表人 李四", "rect": (75, 315, 520, 360)},
            {"text": "注册资本 人民币贰佰万元整", "rect": (75, 385, 700, 430)},
        ],
    }

    # 8) 合同页（含身份证号/统一社会信用代码/有效期限，但没有任何证件名）
    img = _new_canvas(1200, 1600, (252, 251, 248))
    draw = ImageDraw.Draw(img)
    _draw_text(draw, (80, 70), "委 托 代 理 合 同", 34)
    _draw_text(draw, (80, 180), "身份证号码：310104198703124510", 22)
    _draw_text(draw, (80, 230), "统一社会信用代码：91350100M000100Y43", 22)
    _draw_text(draw, (80, 280), "法定代表人：张伟", 22)
    _draw_text(draw, (80, 330), "有效期限：2025年3月12日", 22)
    path = root / "contract_page.png"
    img.save(path)
    out["contract_page"] = {
        "path": path, "size": img.size,
        "text_boxes": [
            {"text": "委 托 代 理 合 同", "rect": (75, 65, 500, 120)},
            {"text": "身份证号码：310104198703124510", "rect": (75, 175, 700, 215)},
            {"text": "统一社会信用代码：91350100M000100Y43",
             "rect": (75, 225, 800, 265)},
            {"text": "法定代表人：张伟", "rect": (75, 275, 500, 315)},
            {"text": "有效期限：2025年3月12日", "rect": (75, 325, 560, 365)},
        ],
    }

    # 9) 正文提到「营业执照」的长句 + 单独的统一社会信用代码（不得触发）
    img = _new_canvas(1200, 1600, (252, 251, 248))
    draw = ImageDraw.Draw(img)
    _draw_text(draw, (80, 80),
               "请乙方提供营业执照复印件并加盖公章后寄回", 22)
    _draw_text(draw, (80, 140), "统一社会信用代码：91350100M000100Y43", 22)
    path = root / "business_mention.png"
    img.save(path)
    out["business_mention"] = {
        "path": path, "size": img.size,
        "text_boxes": [
            {"text": "请乙方提供营业执照复印件并加盖公章后寄回",
             "rect": (75, 75, 1000, 115)},
            {"text": "统一社会信用代码：91350100M000100Y43",
             "rect": (75, 135, 800, 175)},
        ],
    }

    # 10) 纯白页（空白文档页）
    img = _new_canvas(1654, 2339, (255, 255, 255))
    path = root / "blank_page.png"
    img.save(path)
    out["blank_page"] = {"path": path, "size": img.size, "text_boxes": []}

    # 11) 身份证正面字段级夹具：0/90/180/270 四向 + 无脸回退
    _add_id_field_fixtures(root, out)

    return out


def _add_id_field_fixtures(root: Path, out: dict) -> None:
    """生成身份证正面/反面/无脸合成夹具（虚构信息，不入库）。

    正面布局按用户 #85 转正实拍校准：标签在左、值落在模板值区间；
    性别/民族、出生两行画在模板间隙里，用于断言导出掩膜不得覆盖保留行。
    """
    width, height = 1400, 900
    background = (120, 130, 125)          # 中性灰：既非暗块、也非纯白
    card = (150, 110, 1250, 804)          # 1100×694，≈1.585 横版身份证比例
    card_w = float(card[2] - card[0])
    card_h = float(card[3] - card[1])

    def _rel_box(nx0: float, ny0: float, nx1: float, ny1: float):
        return (card[0] + nx0 * card_w, card[1] + ny0 * card_h,
                card[0] + nx1 * card_w, card[1] + ny1 * card_h)

    # (text, nx, ny, font_size, group)；group 用于统计字段期望区/保留行区域
    front_items = (
        ("姓名", 0.08, 0.145, 34, None),
        ("张三", 0.20, 0.135, 44, "id_name"),
        ("性别", 0.08, 0.305, 32, None),
        ("男", 0.20, 0.305, 34, "retained_gender"),
        ("民族", 0.32, 0.305, 32, None),
        ("汉", 0.44, 0.305, 34, "retained_gender"),
        ("出生", 0.08, 0.405, 32, None),
        ("1990年1月1日", 0.20, 0.405, 30, "retained_birth"),
        ("住址", 0.08, 0.535, 32, None),
        ("上海市浦东新区测试路1号", 0.20, 0.535, 28, "id_address"),
        ("2栋3单元401室", 0.22, 0.615, 28, "id_address"),
        ("公民身份号码", 0.08, 0.805, 30, None),
        ("310101199001011234", 0.30, 0.795, 32, "id_number"),
    )
    back_items = (
        ("中华人民共和国居民身份证", 0.30, 0.10, 34, None),
        ("签发机关 上海市公安局", 0.17, 0.695, 28, "id_authority"),
        ("有效期限 2020.01.01-2040.01.01", 0.17, 0.855, 26, "id_validity"),
    )
    back_fallback_items = (
        ("中华人民共和国居民身份证", 0.30, 0.10, 34, None),
    )

    def _union(rects):
        return (min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects))

    def _render(items, photo: bool):
        img = _new_canvas(width, height, background)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(card, radius=30, fill=(250, 250, 248),
                               outline=(90, 90, 90), width=4)
        photo_rect = None
        if photo:
            photo_rect = _rel_box(0.55, 0.15, 0.96, 0.78)
            draw.rectangle([round(v) for v in photo_rect], fill=(150, 25, 25))
        text_boxes = []
        groups: dict[str, list] = {}
        for text, nx, ny, size, group in items:
            pos = (round(card[0] + nx * card_w), round(card[1] + ny * card_h))
            font = _font(size)
            _draw_text(draw, pos, text, size)
            bbox = tuple(float(v) for v in draw.textbbox(pos, text, font=font))
            text_boxes.append({"text": text, "rect": bbox})
            if group:
                groups.setdefault(group, []).append(bbox)
        value_rects = {key: _union(rects) for key, rects in groups.items()
                       if key in ("id_name", "id_address", "id_number",
                                  "id_authority", "id_validity")}
        retained = [_union(groups[key]) for key in
                    ("retained_gender", "retained_birth") if key in groups]
        return img, text_boxes, photo_rect, value_rects, retained

    def _emit(prefix: str, img, text_boxes, photo_rect, value_rects,
              retained, rotations=(0, 90, 180, 270)) -> None:
        base = np.array(img)
        for rotation in rotations:
            k = rotation // 90
            page = np.rot90(base, k).copy()
            page_h, page_w = page.shape[:2]

            def _map(box):
                return tuple(visual_targets._rot_forward_box(
                    box, k, float(width), float(height)))

            boxes = [{"text": item["text"], "rect": _map(item["rect"])}
                     for item in text_boxes]
            path = root / f"{prefix}_{rotation}.png"
            Image.fromarray(page).save(path)
            # 未旋转的原始图像 → 需要逆时针旋转 ((4-k)%4)*90° 才能转正
            expected_orient = ((4 - k) % 4) * 90
            out[f"{prefix}_{rotation}"] = {
                "path": path, "size": (page_w, page_h), "text_boxes": boxes,
                "card_rect": _map(card),
                "photo_rect": _map(photo_rect) if photo_rect else None,
                "value_rects": {key: _map(rect)
                                for key, rect in value_rects.items()},
                "retained_rects": [_map(rect) for rect in retained],
                "orient_expected": expected_orient,
            }

    front_img, front_boxes, front_photo, front_values, front_retained = _render(
        front_items, True)
    _emit("idfront", front_img, front_boxes, front_photo, front_values,
          front_retained)
    noface_img, noface_boxes, _, noface_values, noface_retained = _render(
        front_items, False)
    _emit("idfront_noface", noface_img, noface_boxes, None, noface_values,
          noface_retained, rotations=(0,))
    back_img, back_boxes, _, back_values, _ = _render(back_items, False)
    _emit("idback", back_img, back_boxes, None, back_values, [],
          rotations=(0, 90, 180, 270))
    fallback_img, fallback_boxes, _, fallback_values, _ = _render(
        back_fallback_items, False)
    _emit("idback_fallback", fallback_img, fallback_boxes, None,
          fallback_values, [], rotations=(0,))



# ────────────────────────────── 测试用假级联 ──────────────────────────────

class _FakeCascade:
    """只实现 detectMultiScale 的假级联（按调用顺序返回预设框）。"""

    def __init__(self, boxes_per_call):
        self._calls = list(boxes_per_call)
        self.calls = 0

    def detectMultiScale(self, image, scaleFactor=1.1, minNeighbors=5,
                         minSize=(24, 24)):
        index = min(self.calls, len(self._calls) - 1) if self._calls else 0
        self.calls += 1
        boxes = self._calls[index] if self._calls else []
        return np.array(boxes, dtype=np.int32) if boxes else np.array([])


class _MarkerFaceCascade:
    """测试用假级联：把「图像里最大的暗色实心块」当证件照头像。

    合成身份证夹具用暗红实心块表示头像；反向测试（不画头像块）时只剩文字
    笔画/描边等细碎暗块，本假级联不会返回，从而走 fallback 整卡分支。
    """

    def __init__(self, max_gray: int = 110, min_area_frac: float = 0.01):
        self.max_gray = max_gray
        self.min_area_frac = min_area_frac
        self.calls = 0

    def detectMultiScale(self, image, scaleFactor=1.1, minNeighbors=5,
                         minSize=(24, 24)):
        import cv2 as _cv2
        self.calls += 1
        if image is None:
            return np.empty((0, 4), np.int32)
        gray = image if image.ndim == 2 else _cv2.cvtColor(
            image, _cv2.COLOR_RGB2GRAY)
        mask = (gray < self.max_gray).astype(np.uint8)
        count, _labels, stats, _centroids = _cv2.connectedComponentsWithStats(
            mask, 8)
        height, width = gray.shape
        best = None
        for index in range(1, count):
            x, y, bw, bh, area = (int(v) for v in stats[index][:5])
            if bw < 8 or bh < 8 or area < self.min_area_frac * width * height:
                continue
            fill = area / max(1.0, bw * bh)
            if fill < 0.60:            # 描边/文字笔画不算头像块
                continue
            if best is None or area > best[4]:
                best = (x, y, bw, bh, area)
        if best is None:
            return np.empty((0, 4), np.int32)
        return np.array([[best[0], best[1], best[2], best[3]]], dtype=np.int32)


def _ctx(width: int, height: int, text_boxes=None, options=None) -> ScanContext:
    opts = {"text_boxes": text_boxes or []}
    if options:
        opts.update(options)
    return ScanContext(page=0, dpi=0.0, image_size=(width, height), options=opts)


def _image(path) -> np.ndarray:
    with Image.open(path) as img:
        return np.array(img.convert("RGB"))


def _hits_by_kind(hits, kind: str):
    return [hit for hit in hits if hit.kind == kind]


def _intersection_area(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _mask_coverage(target, masks, samples: int = 40) -> float:
    """像素采样估计：target 区域被 masks（描黑框）覆盖的面积比例。"""
    x0, y0, x1, y1 = target
    if x1 <= x0 or y1 <= y0 or not masks:
        return 0.0
    covered = 0
    total = 0
    for iy in range(samples):
        py = y0 + (iy + 0.5) * (y1 - y0) / samples
        for ix in range(samples):
            px = x0 + (ix + 0.5) * (x1 - x0) / samples
            total += 1
            if any(m[0] <= px <= m[2] and m[1] <= py <= m[3] for m in masks):
                covered += 1
    return covered / float(total or 1)


# ══════════════════════════════ 测试类 ══════════════════════════════

FIXTURES: dict = {}


def setUpModule() -> None:
    global FIXTURES
    FIXTURES = build_fixtures()
    print(f"\n[test_visual] fixtures -> {FIXTURES['plate_blue']['path'].parent}")


class TestRegistration(unittest.TestCase):
    def test_scanner_registered(self):
        self.assertIn(visual_targets.SCANNER_NAME, mediascan.scanner_names())
        self.assertIn("face", visual_targets.KINDS)
        self.assertIn("id_document", visual_targets.KINDS)
        self.assertIn("id_photo", visual_targets.KINDS)
        self.assertIn("id_name", visual_targets.KINDS)
        self.assertIn("id_number", visual_targets.KINDS)
        self.assertIn("id_address", visual_targets.KINDS)
        self.assertIn("id_authority", visual_targets.KINDS)
        self.assertIn("id_validity", visual_targets.KINDS)
        self.assertIn("plate", visual_targets.KINDS)

    def test_no_cv2_still_importable(self):
        # 模块顶层对 cv2 缺失有兜底（这里只断言常量/接口存在，不真的卸载 cv2）
        self.assertTrue(callable(visual_targets.scan))


class TestFaceLogic(unittest.TestCase):
    """注入假级联，验证缩放/外扩/去重/置信度/文档页跳过。"""

    def setUp(self):
        self._saved_override = visual_targets._FACE_CASCADES_OVERRIDE
        self._env_backup = {key: os.environ.get(key) for key in
                            ("YINZHAN_SCAN_FACE_PROFILE", "YINZHAN_SCAN_FACE",
                             "YINZHAN_SCAN_FACE_MAX_SIDE", "YINZHAN_SCAN_FACE_TILE",
                             "YINZHAN_SCAN_FACE_TILE_CAP",
                             "YINZHAN_SCAN_FACE_TILE_OVERLAP",
                             "YINZHAN_SCAN_FACE_MIN_NEIGHBORS")}
        os.environ["YINZHAN_SCAN_FACE_PROFILE"] = "0"
        os.environ["YINZHAN_SCAN_FACE"] = "1"
        os.environ["YINZHAN_SCAN_FACE_TILE"] = "0"      # 单位几何测试用整图单次
        os.environ["YINZHAN_SCAN_FACE_MAX_SIDE"] = "800"
        os.environ.pop("YINZHAN_SCAN_FACE_TILE_CAP", None)
        os.environ.pop("YINZHAN_SCAN_FACE_TILE_OVERLAP", None)
        os.environ.pop("YINZHAN_SCAN_FACE_MIN_NEIGHBORS", None)

    def tearDown(self):
        visual_targets._FACE_CASCADES_OVERRIDE = self._saved_override
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _noise_image(self, width=800, height=600) -> np.ndarray:
        rng = np.random.default_rng(7)
        return rng.integers(0, 255, (height, width, 3), dtype=np.uint8)

    def test_blank_image_is_skipped(self):
        fake = _FakeCascade([[(10, 10, 100, 100)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        blank = np.full((600, 800, 3), 250, np.uint8)
        ctx = _ctx(800, 600)
        self.assertEqual(visual_targets._detect_faces(blank, ctx), [])
        self.assertEqual(fake.calls, 0)

    def test_no_face_returns_empty(self):
        fake = _FakeCascade([[]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        ctx = _ctx(800, 600)
        self.assertEqual(visual_targets._detect_faces(self._noise_image(), ctx), [])

    def test_head_expansion_asymmetric(self):
        # 整头外扩：上 35% 脸高（发际线）、左右 25% 脸宽（耳廓）、下 15% 脸高（下颌）
        os.environ["YINZHAN_SCAN_FACE_MAX_SIDE"] = "800"
        fake = _FakeCascade([[(100, 100, 80, 80)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        ctx = _ctx(800, 600)
        hits = visual_targets._detect_faces(self._noise_image(), ctx)
        self.assertEqual(len(hits), 1)
        rect = hits[0].rect
        # (100,100,180,180) → 左-20/上-28/右+20/下+12 = (80,72,200,192)
        self.assertAlmostEqual(rect[0], 80.0, delta=0.8)
        self.assertAlmostEqual(rect[1], 72.0, delta=0.8)
        self.assertAlmostEqual(rect[2], 200.0, delta=0.8)
        self.assertAlmostEqual(rect[3], 192.0, delta=0.8)
        self.assertIn("engine=haar", hits[0].note)
        self.assertIn("head", hits[0].note)

    def test_downscale_mapping(self):
        # 关掉分块，3200x1600 长边缩到 1600（scale=0.5）验证坐标映射 + 整头外扩
        os.environ["YINZHAN_SCAN_FACE_MAX_SIDE"] = "1600"
        os.environ["YINZHAN_SCAN_FACE_TILE"] = "0"
        fake = _FakeCascade([[(100, 100, 80, 80)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        image = self._noise_image(3200, 1600)
        ctx = _ctx(3200, 1600)
        hits = visual_targets._detect_faces(image, ctx)
        self.assertEqual(len(hits), 1)
        rect = hits[0].rect
        # 原图脸框 (200,200,360,360) → 整头 (160,144,400,384)
        self.assertAlmostEqual(rect[0], 160.0, delta=1.5)
        self.assertAlmostEqual(rect[1], 144.0, delta=1.5)
        self.assertAlmostEqual(rect[2], 400.0, delta=1.5)
        self.assertAlmostEqual(rect[3], 384.0, delta=1.5)
        self.assertIn("scale=0.500", hits[0].note)

    def test_face_tile_plan_grids(self):
        # 小图单次；1600<长边≤4800 用 2×2；更大用 3×3
        os.environ["YINZHAN_SCAN_FACE_TILE"] = "auto"
        mode, plan = visual_targets._face_tile_plan(1200, 800)
        self.assertTrue(mode.startswith("single"))
        self.assertEqual(len(plan), 1)
        mode, plan = visual_targets._face_tile_plan(3200, 2000)
        self.assertTrue(mode.startswith("2x2"))
        self.assertEqual(len(plan), 4)
        mode, plan = visual_targets._face_tile_plan(6000, 4000)
        self.assertTrue(mode.startswith("3x3"))
        self.assertEqual(len(plan), 9)
        # env 可强制退回整图
        os.environ["YINZHAN_SCAN_FACE_TILE"] = "0"
        mode, plan = visual_targets._face_tile_plan(6000, 4000)
        self.assertTrue(mode.startswith("single"))
        self.assertEqual(len(plan), 1)

    def test_tile_mapping_and_downscale_warning(self):
        # 3200x1600 → 2×2 块，每块 cap640（scale=0.4）；第 2 块里返回假框 →
        # 映射回原图应为 (1850,250,2050,450)，整头外扩后 (1800,180,2100,480)
        os.environ["YINZHAN_SCAN_FACE_TILE"] = "2"
        os.environ["YINZHAN_SCAN_FACE_TILE_CAP"] = "640"
        os.environ["YINZHAN_SCAN_FACE_TILE_OVERLAP"] = "0.0"
        fake = _FakeCascade([[], [(100, 100, 80, 80)], [], []])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        ctx = _ctx(3200, 1600)
        hits = visual_targets._detect_faces(self._noise_image(3200, 1600), ctx)
        self.assertEqual(len(hits), 1)
        rect = hits[0].rect
        self.assertAlmostEqual(rect[0], 1800.0, delta=2.0)
        self.assertAlmostEqual(rect[1], 180.0, delta=2.0)
        self.assertAlmostEqual(rect[2], 2100.0, delta=2.0)
        self.assertAlmostEqual(rect[3], 480.0, delta=2.0)
        self.assertIn("2x2", hits[0].note)
        self.assertIn("照片较大，小脸可能漏检，请人工复核/整图涂黑", ctx.warnings)

    def test_small_image_no_downscale_warning(self):
        # 长边 ≤1600 的普通图片即使缩到 640 检测，也不应刷"照片较大"提示
        fake = _FakeCascade([[(100, 100, 80, 80)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        ctx = _ctx(800, 600)
        visual_targets._detect_faces(self._noise_image(), ctx)
        self.assertEqual([w for w in ctx.warnings if "照片较大" in w], [])

    def test_duplicate_faces_are_merged(self):
        fake = _FakeCascade([[(100, 100, 80, 80), (104, 104, 80, 80)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        ctx = _ctx(800, 600)
        hits = visual_targets._detect_faces(self._noise_image(), ctx)
        self.assertEqual(len(hits), 1)

    def test_confidence_scales_with_face_size(self):
        big = _FakeCascade([[(100, 100, 120, 120)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", big, 0.92)]
        big_hits = visual_targets._detect_faces(self._noise_image(), _ctx(800, 600))
        small = _FakeCascade([[(100, 100, 20, 20)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", small, 0.92)]
        small_hits = visual_targets._detect_faces(self._noise_image(), _ctx(800, 600))
        self.assertEqual(len(big_hits), 1)
        self.assertEqual(len(small_hits), 1)
        self.assertGreater(big_hits[0].confidence, small_hits[0].confidence)
        self.assertGreaterEqual(small_hits[0].confidence, 0.35)

    def test_profile_skipped_on_document_page_in_auto(self):
        frontal = _FakeCascade([[]])
        profile = _FakeCascade([[(200, 200, 120, 120)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [
            ("frontal", frontal, 0.92), ("profile", profile, 0.78)]
        os.environ["YINZHAN_SCAN_FACE_PROFILE"] = "auto"
        # 白底、带稀疏黑条 —— 像文档页
        doc = np.full((1200, 900, 3), 255, np.uint8)
        doc[300:320, 100:800] = 20
        doc[500:520, 100:700] = 20
        hits = visual_targets._detect_faces(doc, _ctx(900, 1200))
        self.assertEqual(profile.calls, 0)
        self.assertEqual(len(hits), 0)

    def test_profile_runs_when_forced(self):
        frontal = _FakeCascade([[]])
        profile = _FakeCascade([[(200, 200, 120, 120)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [
            ("frontal", frontal, 0.92), ("profile", profile, 0.78)]
        os.environ["YINZHAN_SCAN_FACE_PROFILE"] = "always"
        doc = np.full((1200, 900, 3), 255, np.uint8)
        doc[300:320, 100:800] = 20
        os.environ["YINZHAN_SCAN_FACE_PROFILE"] = "always"
        hits = visual_targets._detect_faces(doc, _ctx(900, 1200))
        self.assertEqual(profile.calls, 2)       # 正常 + 镜像各一次
        self.assertGreaterEqual(len(hits), 1)


class TestIdDocument(unittest.TestCase):
    def _run(self, name: str):
        fixture = FIXTURES[name]
        image = _image(fixture["path"])
        width, height = fixture["size"]
        ctx = _ctx(width, height, fixture["text_boxes"])
        boxes = visual_targets.iter_text_boxes(ctx)
        return visual_targets._detect_id_document(image, ctx, boxes)

    def test_idcard_region(self):
        hits = self._run("idcard")
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.kind, "id_document")
        self.assertGreaterEqual(hit.confidence, 0.85)
        self.assertIn("doc=id_card", hit.note)
        self.assertNotIn("whole_page", hit.note)
        width, height = FIXTURES["idcard"]["size"]
        # 区域应覆盖身份证号码锚点，且明显小于整页
        self.assertLessEqual(hit.rect[2] - hit.rect[0], width * 0.95)
        x0, y0, x1, y1 = hit.rect
        self.assertTrue(x0 <= 225 and y0 <= 475 and x1 >= 900 and y1 >= 520)

    def test_idcard_whole_page(self):
        hits = self._run("idcard_whole")
        self.assertEqual(len(hits), 1)
        width, height = FIXTURES["idcard_whole"]["size"]
        rect = hits[0].rect
        self.assertAlmostEqual(rect[0], 0.0, delta=1.0)
        self.assertAlmostEqual(rect[1], 0.0, delta=1.0)
        self.assertAlmostEqual(rect[2], float(width), delta=1.0)
        self.assertAlmostEqual(rect[3], float(height), delta=1.0)
        # 新 note 格式：身份证带 side/fields/orient；无脸时 fallback=1
        self.assertIn("doc=id_card", hits[0].note)
        self.assertIn("side=front", hits[0].note)
        self.assertIn("fields=0", hits[0].note)
        self.assertIn("fallback=1", hits[0].note)
        self.assertIn("orient=", hits[0].note)

    def test_passport_mrz(self):
        hits = self._run("passport")
        self.assertEqual(len(hits), 1)
        self.assertIn("doc=passport", hits[0].note)
        self.assertGreaterEqual(hits[0].confidence, 0.88)

    def test_business_license(self):
        hits = self._run("business_license")
        self.assertEqual(len(hits), 1)
        self.assertIn("doc=business_license", hits[0].note)
        self.assertGreaterEqual(hits[0].confidence, 0.85)

    def test_contract_page_no_id_document(self):
        hits = self._run("contract_page")
        self.assertEqual(hits, [], f"合同页误判证件: {[h.note for h in hits]}")

    def test_business_mention_no_id_document(self):
        hits = self._run("business_mention")
        self.assertEqual(hits, [], f"正文提及营业执照误判: {[h.note for h in hits]}")

    def test_no_text_boxes_no_id_document(self):
        fixture = FIXTURES["idcard"]
        image = _image(fixture["path"])
        ctx = _ctx(*fixture["size"])
        self.assertEqual(
            visual_targets._detect_id_document(image, ctx, []), [])


class TestIdCardFields(unittest.TestCase):
    """A 方案：身份证关键字段定位（合成卡 0/90/180/270 + 反面 + 无脸回退）。"""

    def setUp(self):
        self._saved_override = visual_targets._FACE_CASCADES_OVERRIDE
        visual_targets._FACE_CASCADES_OVERRIDE = [
            ("frontal", _MarkerFaceCascade(), 0.92)]
        self._env_backup = {key: os.environ.get(key)
                            for key in ("YINZHAN_SCAN_FACE_PROFILE",)}
        os.environ["YINZHAN_SCAN_FACE_PROFILE"] = "0"

    def tearDown(self):
        visual_targets._FACE_CASCADES_OVERRIDE = self._saved_override
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _run(self, name: str):
        fixture = FIXTURES[name]
        image = _image(fixture["path"])
        ctx = _ctx(*fixture["size"], fixture["text_boxes"])
        boxes = visual_targets.iter_text_boxes(ctx)
        return visual_targets._detect_id_document(image, ctx, boxes)

    def test_front_fields_all_orientations(self):
        for rotation in (0, 90, 180, 270):
            name = f"idfront_{rotation}"
            fixture = FIXTURES[name]
            with self.subTest(rotation=rotation):
                hits = self._run(name)
                fields = {hit.kind: hit for hit in hits if hit.kind in
                          ("id_photo", "id_name", "id_number", "id_address")}
                self.assertEqual(
                    set(fields),
                    {"id_photo", "id_name", "id_number", "id_address"},
                    [hit.note for hit in hits])
                docs = [hit for hit in hits if hit.kind == "id_document"]
                self.assertEqual(len(docs), 1)
                for hit in hits:
                    self.assertIn("side=front", hit.note)
                    self.assertIn("fields=4", hit.note)
                    self.assertNotIn("fallback=1", hit.note)
                    self.assertIn(f"orient={fixture['orient_expected']}", hit.note)
                # 字段框都在卡片范围内（卡片外扩 8% 容差）
                cx0, cy0, cx1, cy1 = fixture["card_rect"]
                dx, dy = (cx1 - cx0) * 0.08, (cy1 - cy0) * 0.08
                card = (cx0 - dx, cy0 - dy, cx1 + dx, cy1 + dy)
                for kind, hit in fields.items():
                    self.assertGreaterEqual(
                        visual_targets._ioc(hit.rect, card), 0.95, kind)
                # 字段覆盖语义区域：头像块/姓名值/号码值/住址值（遮盖率 ≥85%）
                semantic = {
                    "id_photo": fixture["photo_rect"],
                    "id_name": fixture["value_rects"]["id_name"],
                    "id_number": fixture["value_rects"]["id_number"],
                    "id_address": fixture["value_rects"]["id_address"],
                }
                for kind, expected in semantic.items():
                    self.assertIsNotNone(expected, kind)
                    area = (expected[2] - expected[0]) * (expected[3] - expected[1])
                    covered = _intersection_area(fields[kind].rect, expected) / area
                    self.assertGreaterEqual(covered, 0.85, kind)
                # 性别/民族/出生三行必须保留可见：导出掩膜（4 个字段框）不得
                # 完全覆盖保留行（像素采样覆盖率 <50%，即至少一半可见）
                mask_boxes = [hit.rect for hit in fields.values()]
                for index, retained in enumerate(fixture["retained_rects"]):
                    coverage = _mask_coverage(retained, mask_boxes)
                    self.assertLess(
                        coverage, 0.5,
                        f"rotation={rotation} 保留行{index}被字段框覆盖 "
                        f"{coverage:.0%}：{retained} masks={mask_boxes}")

    def test_back_fields_all_orientations(self):
        for rotation in (0, 90, 180, 270):
            name = f"idback_{rotation}"
            fixture = FIXTURES[name]
            with self.subTest(rotation=rotation):
                hits = self._run(name)
                fields = {hit.kind: hit for hit in hits
                          if hit.kind in ("id_authority", "id_validity")}
                self.assertEqual(set(fields), {"id_authority", "id_validity"},
                                 [hit.note for hit in hits])
                docs = [hit for hit in hits if hit.kind == "id_document"]
                self.assertEqual(len(docs), 1)
                for hit in hits:
                    self.assertIn("side=back", hit.note)
                    self.assertIn("fields=2", hit.note)
                    self.assertNotIn("fallback=1", hit.note)
                    self.assertIn(f"orient={fixture['orient_expected']}", hit.note)
                # 行框在卡片范围内（外扩 8% 容差）
                cx0, cy0, cx1, cy1 = fixture["card_rect"]
                dx, dy = (cx1 - cx0) * 0.08, (cy1 - cy0) * 0.08
                card = (cx0 - dx, cy0 - dy, cx1 + dx, cy1 + dy)
                for kind, hit in fields.items():
                    self.assertGreaterEqual(
                        visual_targets._ioc(hit.rect, card), 0.95, kind)
                # 覆盖语义区：签发机关行 / 有效期限行（标签+值一起遮）
                for kind in ("id_authority", "id_validity"):
                    expected = fixture["value_rects"][kind]
                    area = (expected[2] - expected[0]) * (expected[3] - expected[1])
                    covered = _intersection_area(fields[kind].rect, expected) / area
                    self.assertGreaterEqual(covered, 0.85, kind)

    def test_back_falls_back_when_rows_unreadable(self):
        # 只有标题、读不出签发机关/有效期限 → 无法拟合，回退整卡 fallback=1
        hits = self._run("idback_fallback_0")
        self.assertEqual(len(hits), 1, [hit.note for hit in hits])
        note = hits[0].note
        self.assertEqual(hits[0].kind, "id_document")
        self.assertIn("side=back", note)
        self.assertIn("fields=0", note)
        self.assertIn("fallback=1", note)
        self.assertIn("orient=", note)
        self.assertNotIn("id_authority", [hit.kind for hit in hits])

    def test_no_face_falls_back_to_whole_card(self):
        hits = self._run("idfront_noface_0")
        self.assertEqual(len(hits), 1, [hit.note for hit in hits])
        note = hits[0].note
        self.assertEqual(hits[0].kind, "id_document")
        self.assertIn("side=front", note)
        self.assertIn("fields=0", note)
        self.assertIn("fallback=1", note)
        self.assertIn("orient=", note)
        self.assertNotIn("id_photo", [hit.kind for hit in hits])

    def test_rotation_mapping_roundtrip(self):
        box = (150.0, 110.0, 1250.0, 804.0)
        width, height = 1400, 900
        for k in range(4):
            page_box = visual_targets._rot_forward_box(
                box, k, float(width), float(height))
            back = visual_targets._rot_back_box(
                page_box, k, float(width), float(height))
            for a, b in zip(box, back):
                self.assertAlmostEqual(a, b, delta=1e-6)

    def test_fields_kept_in_prune_nested(self):
        # 字段框在整卡框内也不能被 _prune_nested 当重复删掉（jobs 按模式过滤）
        hits = [
            ScanHit(kind="id_document", rect=(0.0, 0.0, 100.0, 100.0),
                    confidence=0.9, note="engine=ocr;doc=id_card;fields=1"),
            ScanHit(kind="id_photo", rect=(10.0, 10.0, 40.0, 40.0),
                    confidence=0.9, note="engine=ocr;doc=id_card;fields=1"),
        ]
        pruned = visual_targets._prune_nested(hits)
        self.assertEqual([hit.kind for hit in pruned],
                         ["id_document", "id_photo"])


class TestPlate(unittest.TestCase):
    def setUp(self):
        self._env_backup = {key: os.environ.get(key) for key in
                            ("YINZHAN_SCAN_PLATE_UNCONFIRMED",
                             "YINZHAN_SCAN_PLATE_COLOR",
                             "YINZHAN_SCAN_PLATE_MAX_SIDE")}
        os.environ.pop("YINZHAN_SCAN_PLATE_UNCONFIRMED", None)
        os.environ.pop("YINZHAN_SCAN_PLATE_COLOR", None)
        os.environ.pop("YINZHAN_SCAN_PLATE_MAX_SIDE", None)

    def tearDown(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _detect(self, name: str, text_boxes=None):
        fixture = FIXTURES[name]
        image = _image(fixture["path"])
        width, height = fixture["size"]
        raw = text_boxes if text_boxes is not None else fixture["text_boxes"]
        ctx = _ctx(width, height, raw)
        boxes = visual_targets.iter_text_boxes(ctx)
        return visual_targets._detect_plates(image, ctx, boxes)

    def test_blue_plate(self):
        hits = self._detect("plate_blue")
        self.assertEqual(len(hits), 1, [h.note for h in hits])
        hit = hits[0]
        self.assertEqual(hit.kind, "plate")
        self.assertGreaterEqual(hit.confidence, 0.9)
        self.assertIn("plate=沪A12345", hit.note)
        self.assertIn("color+ocr", hit.note)
        # 命中框应覆盖物理车牌区域的大部分
        px0, py0, px1, py1 = FIXTURES["plate_blue"]["plate_rect"]
        ix = max(0.0, min(hit.rect[2], px1) - max(hit.rect[0], px0))
        iy = max(0.0, min(hit.rect[3], py1) - max(hit.rect[1], py0))
        inter = ix * iy
        self.assertGreater(inter / ((px1 - px0) * (py1 - py0)), 0.55)

    def test_plate_color_rect_is_wider_than_ocr_box(self):
        # 完整牌面（色块）应比 OCR 文本框更宽，能盖住整个牌照而不是只盖文字
        hits = self._detect("plate_blue")
        self.assertEqual(len(hits), 1)
        hit_width = hits[0].rect[2] - hits[0].rect[0]
        ocr_box = FIXTURES["plate_blue"]["text_boxes"][0]["rect"]
        ocr_width = ocr_box[2] - ocr_box[0]
        self.assertGreater(hit_width, ocr_width * 1.05)

    def test_blue_plate_color_only_fallback(self):
        # 没有 OCR 文本（车牌不可读）→ 颜色牌面回退，低置信 + unconfirmed 标注
        hits = self._detect("plate_blue", [])
        self.assertEqual(len(hits), 1, [h.note for h in hits])
        hit = hits[0]
        self.assertAlmostEqual(hit.confidence, 0.55, places=2)
        self.assertIn("unconfirmed", hit.note)
        self.assertIn("color/blue", hit.note)

    def test_yellow_plate_black_text(self):
        hits = self._detect("plate_yellow")
        self.assertEqual(len(hits), 1, [h.note for h in hits])
        self.assertIn("plate=京C88888", hits[0].note)
        self.assertIn("yellow", hits[0].note)
        # 黄牌黑字也要能纯颜色检出
        fallback = self._detect("plate_yellow", [])
        self.assertEqual(len(fallback), 1)
        self.assertIn("color/yellow", fallback[0].note)

    def test_green_new_energy_plate(self):
        hits = self._detect("plate_green")
        self.assertEqual(len(hits), 1, [h.note for h in hits])
        self.assertIn("plate=粤BD12345", hits[0].note)

    def test_ocr_noise_tolerance(self):
        for text in ("沪A·12345", "沪a12345", "沪A 12345", "沪Ａ12345"):
            with self.subTest(text=text):
                hits = self._detect(
                    "plate_blue",
                    [{"text": text, "rect": (335, 525, 700, 620)}])
                self.assertEqual(len(hits), 1, f"{text} 未命中")

    def test_plate_suffix_variants(self):
        for text in ("沪A1234挂", "苏E1234学", "京A0001警"):
            with self.subTest(text=text):
                hits = self._detect(
                    "plate_blue",
                    [{"text": text, "rect": (335, 525, 700, 620)}])
                self.assertEqual(len(hits), 1, f"{text} 未命中")

    def test_contract_numbers_are_not_plates(self):
        fixture = FIXTURES["contract_page"]
        hits = self._detect("contract_page", fixture["text_boxes"])
        self.assertEqual(hits, [], [h.note for h in hits])

    def test_road_sign_not_plate(self):
        fixture = FIXTURES["roadsign"]
        image = _image(fixture["path"])
        width, height = fixture["size"]
        boxes = fixture["text_boxes"]
        ctx = _ctx(width, height, boxes)
        self.assertEqual(visual_targets._detect_plates(image, ctx, boxes), [])
        # 纯颜色候选（蓝底白字路牌）也应按字/比例门槛被拒绝
        ctx2 = _ctx(width, height)
        self.assertEqual(visual_targets._detect_plates(image, ctx2, []), [])

    def test_unconfirmed_can_be_disabled(self):
        os.environ["YINZHAN_SCAN_PLATE_UNCONFIRMED"] = "0"
        hits = self._detect("plate_blue", [])
        self.assertEqual(hits, [], [h.note for h in hits])

    def test_color_engine_can_be_disabled(self):
        os.environ["YINZHAN_SCAN_PLATE_COLOR"] = "0"
        # 关掉颜色后只剩 OCR 文本路径，框仍应在（但不含 color+ocr 标记）
        hits = self._detect("plate_blue")
        self.assertEqual(len(hits), 1, [h.note for h in hits])
        self.assertNotIn("color+ocr", hits[0].note)


class TestPublicAnalyze(unittest.TestCase):
    """走 mediascan.analyze 公共入口（含坐标裁剪、同 kind 合并）。"""

    def _analyze(self, name: str):
        fixture = FIXTURES[name]
        image = _image(fixture["path"])
        marks, warnings = mediascan.analyze(
            image, page=0, dpi=0.0,
            options={"text_boxes": fixture["text_boxes"]})
        return marks, warnings

    def test_plate_through_analyze(self):
        marks, _warnings = self._analyze("plate_blue")
        plates = [m for m in marks if m["kind"] == "plate"]
        self.assertEqual(len(plates), 1, marks)
        x0, y0, x1, y1 = plates[0]["rect"]
        self.assertLess(x0, x1)
        self.assertLess(y0, y1)
        self.assertGreater(plates[0]["confidence"], 0.9)
        self.assertEqual(plates[0]["page"], 0)

    def test_id_document_through_analyze(self):
        marks, _warnings = self._analyze("idcard")
        docs = [m for m in marks if m["kind"] == "id_document"]
        self.assertEqual(len(docs), 1, marks)

    def test_nested_face_pruned_by_id_document(self):
        # 证件里再检出人脸时，人脸框被证件区域覆盖 → 只保留证件
        fixture = FIXTURES["idcard"]
        image = _image(fixture["path"])
        ctx = _ctx(*fixture["size"], fixture["text_boxes"])
        os.environ["YINZHAN_SCAN_FACE_MAX_SIDE"] = "1600"
        os.environ["YINZHAN_SCAN_FACE_TILE"] = "0"
        fake = _FakeCascade([[(260, 300, 90, 90)]])
        saved = visual_targets._FACE_CASCADES_OVERRIDE
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        try:
            hits = visual_targets.scan(image, ctx)
        finally:
            visual_targets._FACE_CASCADES_OVERRIDE = saved
            os.environ.pop("YINZHAN_SCAN_FACE_MAX_SIDE", None)
            os.environ.pop("YINZHAN_SCAN_FACE_TILE", None)
        kinds = [hit.kind for hit in hits]
        self.assertIn("id_document", kinds)
        self.assertNotIn("face", kinds)


class TestSamplesNegative(unittest.TestCase):
    """真实既有样例：不得新增 face / plate / id_document 误报（红线）。"""

    def _analyze_file(self, path: Path, text_boxes=None):
        image = _image(path)
        marks, warnings = mediascan.analyze(
            image, page=0, dpi=0.0,
            options={"text_boxes": text_boxes or []})
        ours = [m for m in marks
                if m["kind"] in ("face", "plate", "id_document")]
        return marks, ours, warnings

    def test_business_card_no_visual_entities(self):
        card = SAMPLES / "名片.png"
        self.assertTrue(card.is_file(), "缺少 samples/名片.png")
        from yinzhan.ocr import get_ocr
        image = _image(card)
        with Image.open(card) as img:
            lines = get_ocr().recognize(img.convert("RGB")) or []
        text_boxes = [{"text": line.text, "rect": tuple(line.box)} for line in lines]
        self.assertTrue(text_boxes, "OCR 不可用，负样本测试无法构造文本框")
        marks, ours, _warnings = self._analyze_file(card, text_boxes)
        self.assertEqual(ours, [], f"名片.png 新增视觉误报: {ours}")
        self.assertEqual([m for m in marks if m["kind"] == "id_document"], [])

    def test_orthophoto_no_visual_entities(self):
        tif = SAMPLES / "正射影像.tif"
        self.assertTrue(tif.is_file(), "缺少 samples/正射影像.tif")
        with Image.open(tif) as img:
            frame = np.array(img.convert("RGB"))
        marks, warnings = mediascan.analyze(frame, page=0, dpi=0.0, options={})
        ours = [m for m in marks if m["kind"] in ("face", "plate", "id_document")]
        self.assertEqual(ours, [], f"正射影像新增视觉误报: {ours}")

    def test_blank_page_fast_and_clean(self):
        blank = np.full((2339, 1654, 3), 255, np.uint8)
        t0 = time.perf_counter()
        marks, _warnings = mediascan.analyze(blank, page=0, dpi=150.0, options={})
        elapsed = time.perf_counter() - t0
        ours = [m for m in marks if m["kind"] in ("face", "plate", "id_document")]
        self.assertEqual(ours, [])
        self.assertLess(elapsed, 2.0, f"空白页扫描过慢: {elapsed:.2f}s")


class TestRobustness(unittest.TestCase):
    def test_malformed_text_boxes(self):
        fixture = FIXTURES["plate_blue"]
        image = _image(fixture["path"])
        dirty = [None, 1, {"text": 123, "rect": "bad"},
                 {"text": "沪A12345", "rect": [0, 0, 0, 0]},
                 {"text": "沪A12345", "rect": (335, 525, 700, 620)}]
        ctx = _ctx(*fixture["size"], dirty)
        hits = visual_targets.scan(image, ctx)
        self.assertTrue(any(hit.kind == "plate" for hit in hits))

    def test_no_options(self):
        fixture = FIXTURES["blank_page"]
        image = _image(fixture["path"])
        ctx = ScanContext(page=0, dpi=0.0, image_size=fixture["size"],
                          options={})
        self.assertEqual(visual_targets.scan(image, ctx), [])

    def test_scan_deterministic(self):
        fixture = FIXTURES["idcard"]
        image = _image(fixture["path"])
        ctx = _ctx(*fixture["size"], fixture["text_boxes"])
        first = [(h.kind, tuple(round(v, 2) for v in h.rect),
                  round(h.confidence, 3)) for h in visual_targets.scan(image, ctx)]
        second = [(h.kind, tuple(round(v, 2) for v in h.rect),
                   round(h.confidence, 3)) for h in visual_targets.scan(image, ctx)]
        self.assertEqual(first, second)


class TestPerformance(unittest.TestCase):
    """软性耗时观测：只做宽松上限，不作为精度断言。跑两份取较快值。"""

    def test_business_card_scan_time(self):
        card = SAMPLES / "名片.png"
        if not card.is_file():
            self.skipTest("缺少 samples/名片.png")
        image = _image(card)
        t0 = time.perf_counter()
        mediascan.analyze(image, page=0, dpi=0.0, options={})
        first = time.perf_counter() - t0
        t0 = time.perf_counter()
        mediascan.analyze(image, page=0, dpi=0.0, options={})
        second = time.perf_counter() - t0
        print(f"\n[perf] 名片.png 1000x620 扫描：首次 {first*1000:.0f}ms，"
              f"热态 {second*1000:.0f}ms")
        self.assertLess(second, 2.5, f"名片扫描过慢: {second:.2f}s")

    def test_1600px_scan_time(self):
        rng = np.random.default_rng(3)
        image = rng.integers(0, 255, (1200, 1600, 3), dtype=np.uint8)
        t0 = time.perf_counter()
        marks, _warnings = mediascan.analyze(image, page=0, dpi=0.0, options={})
        elapsed = time.perf_counter() - t0
        print(f"\n[perf] 1600x1200 扫描：{elapsed*1000:.0f}ms，marks={len(marks)}")
        self.assertLess(elapsed, 3.0, f"1600px 扫描过慢: {elapsed:.2f}s")


def main() -> int:
    print("数据隐站 · 图像 P0 扫描器测试（visual_targets）")
    print("-" * 64)
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("-" * 64)
    if result.wasSuccessful():
        print(f"通过：{result.testsRun} 项")
        return 0
    print(f"失败：failures={len(result.failures)} errors={len(result.errors)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
