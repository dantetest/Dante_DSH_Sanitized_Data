# -*- coding: utf-8 -*-
"""图像 P0 扫描器测试（visual_targets：人脸 / 证件整图 / 车牌）。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_visual.py
    cd app\\server && python -X utf8 ..\\tests\\test_visual.py

设计说明：
- 纯离线、无网络、不写仓库：夹具默认生成到临时目录（tempfile.mkdtemp()）。
  想人工看一眼夹具时，加环境变量 ``YINZHAN_WRITE_FIXTURES=1``，会写到
  ``app/tests/fixtures/visual/``（该目录不入库二进制，由本脚本按需生成）。
- 人脸正样本：仓库内没有可再分发的真人照片，P0 用「注入假级联」验证
  缩放下采样、外扩 12%、去重、置信度换算、文档页跳过侧脸等逻辑；
  真实照片召回/误报测试集待用户提供（见 docs/impl/visual.md §6）。
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
from yinzhan.mediascan import ScanContext             # noqa: E402

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

    # 3) 路牌照片（蓝底白字路名）——不得被当成车牌
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

    return out


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
                             "YINZHAN_SCAN_FACE_MAX_SIDE")}
        os.environ["YINZHAN_SCAN_FACE_PROFILE"] = "0"
        os.environ["YINZHAN_SCAN_FACE"] = "1"
        os.environ.pop("YINZHAN_SCAN_FACE_MAX_SIDE", None)

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

    def test_expand_12_percent(self):
        # 关掉下采样（图长边 800 <= cap 800），验证外扩 12% 的纯几何逻辑
        os.environ["YINZHAN_SCAN_FACE_MAX_SIDE"] = "800"
        fake = _FakeCascade([[(100, 100, 80, 80)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        ctx = _ctx(800, 600)
        hits = visual_targets._detect_faces(self._noise_image(), ctx)
        self.assertEqual(len(hits), 1)
        rect = hits[0].rect
        # 800x600 不缩放；框 (100,100,180,180) 外扩 12% → (90.4,90.4,189.6,189.6)
        self.assertAlmostEqual(rect[0], 90.4, delta=0.8)
        self.assertAlmostEqual(rect[1], 90.4, delta=0.8)
        self.assertAlmostEqual(rect[2], 189.6, delta=0.8)
        self.assertAlmostEqual(rect[3], 189.6, delta=0.8)
        self.assertIn("engine=haar", hits[0].note)

    def test_downscale_mapping(self):
        # 3200x1600 长边缩到 1600（scale=0.5），假框在缩放图系返回 (100,100,80,80)
        os.environ["YINZHAN_SCAN_FACE_MAX_SIDE"] = "1600"
        fake = _FakeCascade([[(100, 100, 80, 80)]])
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        image = self._noise_image(3200, 1600)
        ctx = _ctx(3200, 1600)
        hits = visual_targets._detect_faces(image, ctx)
        self.assertEqual(len(hits), 1)
        rect = hits[0].rect
        # 原图系 (200,200,360,360) → 外扩 12% → (180.8,180.8,379.2,379.2)
        self.assertAlmostEqual(rect[0], 180.8, delta=1.5)
        self.assertAlmostEqual(rect[1], 180.8, delta=1.5)
        self.assertAlmostEqual(rect[2], 379.2, delta=1.5)
        self.assertAlmostEqual(rect[3], 379.2, delta=1.5)
        self.assertIn("scale=0.500", hits[0].note)

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
        self.assertIn("whole_page", hits[0].note)

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


class TestPlate(unittest.TestCase):
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
        # 命中框应覆盖物理车牌区域的大部分
        px0, py0, px1, py1 = FIXTURES["plate_blue"]["plate_rect"]
        ix = max(0.0, min(hit.rect[2], px1) - max(hit.rect[0], px0))
        iy = max(0.0, min(hit.rect[3], py1) - max(hit.rect[1], py0))
        inter = ix * iy
        self.assertGreater(inter / ((px1 - px0) * (py1 - py0)), 0.55)

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
        boxes = [
            {"text": "统一社会信用代码：91350100M000100Y43",
             "rect": (75, 225, 800, 265)},
            {"text": "身份证号码：310104198703124510", "rect": (75, 175, 700, 215)},
            {"text": "案号：（2025）沪0104民初18732号", "rect": (75, 300, 700, 340)},
        ]
        hits = self._detect("plate_blue", boxes)
        self.assertEqual(hits, [], [h.note for h in hits])

    def test_road_sign_not_plate(self):
        fixture = FIXTURES["roadsign"]
        image = _image(fixture["path"])
        width, height = fixture["size"]
        boxes = fixture["text_boxes"]
        ctx = _ctx(width, height, boxes)
        self.assertEqual(visual_targets._detect_plates(image, ctx, boxes), [])

    def test_color_candidate_unverified_off_by_default(self):
        # 只有颜色候选、没有车牌文字 → 默认不开 unverified，不应命中
        fixture = FIXTURES["roadsign"]
        image = _image(fixture["path"])
        ctx = _ctx(*fixture["size"])
        hits = visual_targets._detect_plates(image, ctx, [])
        self.assertEqual(hits, [])


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
        fake = _FakeCascade([[(260, 300, 90, 90)]])
        saved = visual_targets._FACE_CASCADES_OVERRIDE
        visual_targets._FACE_CASCADES_OVERRIDE = [("frontal", fake, 0.92)]
        try:
            hits = visual_targets.scan(image, ctx)
        finally:
            visual_targets._FACE_CASCADES_OVERRIDE = saved
            os.environ.pop("YINZHAN_SCAN_FACE_MAX_SIDE", None)
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
