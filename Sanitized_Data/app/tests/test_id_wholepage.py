# -*- coding: utf-8 -*-
"""P2 误报回归：整页证件过判（QA real-testset v1.1.0 §7.4，R07/R11）。

场景：手机竖拍照片（页面 1024×1821 / 1080×1920 一类，长宽比远离身份证 1.585），
身份证卡片几乎占满画面。旧行为：``whole_page=True`` 时 ``id_document`` 固定为
整页（面积比 1.0），成品整页涂黑；即使正面 4 字段 / 反面 2 行都已准确定位。

期望（本文件断言）：
1. 字段级命中可靠（正面 4 字段、反面 2 行）时，不再产生面积≈1.0 的 ``id_document``：
   返回字段/卡片几何拟合出的整卡框（明显小于整页），字段 hits 原样保留；
   拟合不出合理整卡框时允许**不返回** ``id_document``，只留字段 hits。
2. 字段读不出/无法定向（fallback）时仍回退整页/整卡，宁可多遮不可漏。
3. 页面长宽比≈身份证（紧裁卡面/卡面扫描）时保留整页兜底：此时整页≈整卡，
   也是既有 ``whole`` 档「整页黑覆盖≈100%」功能红线的夹具形态（见 qa_common）。

纯合成虚构图（无真实个人信息、无 OCR/网络依赖）：卡片 + 暗色头像块用 PIL 画，
文本框显式构造；用「最大暗块」假级联确定身份证正面朝向，保证可复现。

运行：
    python -X utf8 app/tests/test_id_wholepage.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import visual_targets                      # noqa: E402
from yinzhan.mediascan import ScanContext               # noqa: E402

#: 合成底图（横版）；逆时针 90° 后得到 900×1600 的竖拍页面（长宽比 0.5625）。
_BASE_SIZE = (1600, 900)
_PAGE_SIZE = (900, 1600)
#: 卡片几乎占满底图（旋转后面积比 ≈0.86），模拟用户把证件拍满画面。
_CARD = (50.0, 50.0, 1550.0, 872.0)
#: 头像块：落在身份证正面模板的证件照区域（相对底图卡片）。
_PHOTO = (0.55, 0.15, 0.96, 0.78)
_BG = (120, 130, 125)
_CARD_FILL = (250, 250, 248)

#: 正面字段（相对底图卡片归一化；只用于构造 OCR 文本框，不绘制文字）。
_FRONT_ITEMS = {
    "title": ("中华人民共和国居民身份证", (0.30, 0.04, 0.90, 0.09)),
    "id_name": ("姓名 张三", (0.08, 0.135, 0.40, 0.19)),
    "id_address": ("住址 上海市浦东新区测试路1号", (0.08, 0.545, 0.70, 0.61)),
    "id_number": ("公民身份号码 310101199001011234", (0.08, 0.805, 0.66, 0.87)),
}
#: 反面字段（签发机关/有效期限两行 + 标题）。
_BACK_ITEMS = {
    "title": ("中华人民共和国居民身份证", (0.30, 0.08, 0.90, 0.14)),
    "id_authority": ("签发机关 上海市公安局", (0.17, 0.695, 0.75, 0.75)),
    "id_validity": ("有效期限 2020.01.01-2040.01.01",
                    (0.17, 0.855, 0.85, 0.91)),
}


class _MarkerFaceCascade:
    """测试用假级联：返回图像里「最大的暗色实心块」当作证件照头像。

    合成卡片只画一个暗色头像块；卡片描边/背景为细线或浅色，不会命中。
    与 ``app/tests/test_visual.py`` 的同名夹具思路一致，这里内联一份，
    避免新测试依赖其它测试模块的私有实现。
    """

    def __init__(self, max_gray: int = 110, min_area_frac: float = 0.01):
        self.max_gray = max_gray
        self.min_area_frac = min_area_frac
        self.calls = 0

    def detectMultiScale(self, image, scaleFactor=1.1, minNeighbors=5,
                         minSize=(24, 24)):
        import cv2

        self.calls += 1
        if image is None:
            return np.empty((0, 4), np.int32)
        gray = image if image.ndim == 2 else cv2.cvtColor(
            image, cv2.COLOR_RGB2GRAY)
        mask = (gray < self.max_gray).astype(np.uint8)
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask, 8)
        height, width = gray.shape
        best = None
        for index in range(1, count):
            x, y, bw, bh, area = (int(v) for v in stats[index][:5])
            if bw < 8 or bh < 8 or area < self.min_area_frac * width * height:
                continue
            if area / max(1.0, bw * bh) < 0.60:      # 描边/文字笔画不算头像
                continue
            if best is None or area > best[4]:
                best = (x, y, bw, bh, area)
        if best is None:
            return np.empty((0, 4), np.int32)
        return np.array([[best[0], best[1], best[2], best[3]]], dtype=np.int32)


def _rel_box(nx0: float, ny0: float, nx1: float, ny1: float):
    x0, y0, x1, y1 = _CARD
    card_w, card_h = x1 - x0, y1 - y0
    return (x0 + nx0 * card_w, y0 + ny0 * card_h,
            x0 + nx1 * card_w, y0 + ny1 * card_h)


def _scene(items: dict, *, photo: bool = True, rotate: bool = True):
    """生成 ``(rgb, text_boxes)``：底图卡片 + 可选头像块，按需转成竖拍页面。"""
    img = Image.new("RGB", _BASE_SIZE, _BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(_CARD, radius=24, fill=_CARD_FILL,
                           outline=(90, 90, 90), width=4)
    if photo:
        photo_box = _rel_box(*_PHOTO)
        draw.rectangle([round(v) for v in photo_box], fill=(150, 25, 25))
    boxes = [(text, _rel_box(*rel)) for text, rel in items.values()]
    if not rotate:
        return np.asarray(img), boxes
    array = np.rot90(np.asarray(img), 1).copy()
    page_w, page_h = float(array.shape[1]), float(array.shape[0])
    mapped = [(text, visual_targets._rot_forward_box(
        rect, 1, float(_BASE_SIZE[0]), float(_BASE_SIZE[1])))
        for text, rect in boxes]
    # 兜底检查：夹进页面，避免夹具尺寸改动后坐标越界。
    mapped = [(text, (max(0.0, min(page_w, r[0])), max(0.0, min(page_h, r[1])),
                      max(0.0, min(page_w, r[2])), max(0.0, min(page_h, r[3]))))
              for text, r in mapped]
    return array, mapped


def _flat_page(width: int, height: int, boxes):
    """纯背景页 + 显式文本框（fallback 场景：读不出字段/没有脸）。"""
    image = np.full((height, width, 3), _BG, np.uint8)
    return image, list(boxes)


def _ctx(width: int, height: int, boxes) -> ScanContext:
    return ScanContext(
        page=0, dpi=0.0, image_size=(width, height),
        options={"text_boxes": [{"text": text, "rect": rect}
                                for text, rect in boxes]})


def _area_ratio(rect, width: int, height: int) -> float:
    area = max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])
    return area / float(width * height)


def _run(image, boxes):
    height, width = int(image.shape[0]), int(image.shape[1])
    ctx = _ctx(width, height, boxes)
    text_boxes = visual_targets.iter_text_boxes(ctx)
    return visual_targets._detect_id_document(image, ctx, text_boxes), width, height


class TestWholePageSuppression(unittest.TestCase):
    """字段级命中可靠时，手机竖拍页面不再整页扩张。"""

    FRONT_KINDS = ("id_photo", "id_name", "id_number", "id_address")
    BACK_KINDS = ("id_authority", "id_validity")

    def setUp(self):
        self._saved_override = visual_targets._FACE_CASCADES_OVERRIDE
        visual_targets._FACE_CASCADES_OVERRIDE = [
            ("frontal", _MarkerFaceCascade(), 0.92)]

    def tearDown(self):
        visual_targets._FACE_CASCADES_OVERRIDE = self._saved_override

    def _assert_card_box(self, hits, kinds, side: str, width: int, height: int):
        fields = {hit.kind: hit for hit in hits if hit.kind in kinds}
        self.assertEqual(set(fields), set(kinds), [hit.note for hit in hits])
        docs = [hit for hit in hits if hit.kind == "id_document"]
        self.assertEqual(len(docs), 1, [hit.note for hit in hits])
        doc = docs[0]
        ratio = _area_ratio(doc.rect, width, height)
        self.assertLess(ratio, 0.95,
                        f"{side} 整页证件未抑制：id_document 面积比 {ratio:.3f}")
        self.assertGreater(ratio, 0.05, f"{side} 整卡框异常小：{doc.rect}")
        # 字段命中仍保留，且落在整卡框内（不因抑制整页而丢字段）
        for kind, hit in fields.items():
            self.assertGreaterEqual(
                visual_targets._ioc(hit.rect, doc.rect), 0.99, kind)
        for hit in hits:
            self.assertIn(f"side={side}", hit.note)
            self.assertIn(f"fields={len(kinds)}", hit.note)
            self.assertNotIn("fallback=1", hit.note)

    def test_front_fields_no_whole_page(self):
        image, boxes = _scene(_FRONT_ITEMS)
        hits, width, height = _run(image, boxes)
        self._assert_card_box(hits, self.FRONT_KINDS, "front", width, height)
        # id_document 不得再覆盖整页
        doc = [h for h in hits if h.kind == "id_document"][0]
        self.assertFalse(doc.rect[2] - doc.rect[0] >= width * 0.99
                         and doc.rect[3] - doc.rect[1] >= height * 0.99)

    def test_back_fields_no_whole_page(self):
        image, boxes = _scene(_BACK_ITEMS)
        hits, width, height = _run(image, boxes)
        self._assert_card_box(hits, self.BACK_KINDS, "back", width, height)

    def test_implausible_card_box_returns_fields_only(self):
        """拟合不出合理整卡框时不返回 id_document（由字段级命中兜底）。"""
        page = (0.0, 0.0, 1024.0, 1821.0)
        # 整页 / 过小 / 长宽比离谱（细长条）都应判为不可信
        for box in (page, (10.0, 10.0, 30.0, 22.0),
                    (100.0, 800.0, 900.0, 860.0)):
            with self.subTest(box=box):
                self.assertIsNone(
                    visual_targets._id_document_box(True, box, 1024, 1821))


class TestFallbackStillWholeCard(unittest.TestCase):
    """字段读不出/无法定向：仍回退整页/整卡（宁可多遮，不可漏）。"""

    def setUp(self):
        self._saved_override = visual_targets._FACE_CASCADES_OVERRIDE
        visual_targets._FACE_CASCADES_OVERRIDE = None       # 不注入人脸

    def tearDown(self):
        visual_targets._FACE_CASCADES_OVERRIDE = self._saved_override

    def test_front_no_face_falls_back_to_page(self):
        # 手机竖拍页：标题/号码分列上下 → 面积触发 whole_page；没有脸 → fallback
        width, height = 900, 1600
        boxes = [
            ("中华人民共和国居民身份证", (50.0, 40.0, 850.0, 120.0)),
            ("公民身份号码 310101199001011234", (50.0, 1460.0, 850.0, 1550.0)),
        ]
        image, boxes = _flat_page(width, height, boxes)
        hits, width, height = _run(image, boxes)
        self.assertEqual(len(hits), 1, [hit.note for hit in hits])
        hit = hits[0]
        self.assertEqual(hit.kind, "id_document")
        self.assertIn("side=front", hit.note)
        self.assertIn("fields=0", hit.note)
        self.assertIn("fallback=1", hit.note)
        self.assertGreaterEqual(_area_ratio(hit.rect, width, height), 0.99,
                                f"无脸 fallback 未回退整页：{hit.rect}")

    def test_back_unreadable_rows_falls_back_to_page(self):
        # 只读出标题+签发机关（有效期限读不出）→ 反面无法拟合 → 整页 fallback
        width, height = 900, 1600
        boxes = [
            ("中华人民共和国居民身份证", (50.0, 40.0, 850.0, 120.0)),
            ("签发机关 上海市公安局", (50.0, 1460.0, 850.0, 1550.0)),
        ]
        image, boxes = _flat_page(width, height, boxes)
        hits, width, height = _run(image, boxes)
        self.assertEqual(len(hits), 1, [hit.note for hit in hits])
        hit = hits[0]
        self.assertEqual(hit.kind, "id_document")
        self.assertIn("side=back", hit.note)
        self.assertIn("fields=0", hit.note)
        self.assertIn("fallback=1", hit.note)
        self.assertGreaterEqual(_area_ratio(hit.rect, width, height), 0.99,
                                f"反面 fallback 未回退整页：{hit.rect}")


class TestCardShapedPageGuard(unittest.TestCase):
    """页面本身≈证件比例（紧裁卡面/卡面扫描）时保留整页兜底。

    ``qa_common.id_card_chain_test`` 的合成卡页面 1160×760（1.526≈1.585）即此类；
    既有 ``whole`` 档功能红线要求「整页黑覆盖率≈100%」，因此此形态仍返回整页。
    """

    def test_page_aspect_helper(self):
        self.assertTrue(visual_targets._page_is_card_shaped(1160, 760))
        self.assertTrue(visual_targets._page_is_card_shaped(760, 1160))
        self.assertFalse(visual_targets._page_is_card_shaped(1024, 1821))
        self.assertFalse(visual_targets._page_is_card_shaped(1080, 1920))
        self.assertFalse(visual_targets._page_is_card_shaped(1400, 1600))

    def test_card_shaped_page_keeps_page_box(self):
        card_box = (73.0, 162.0, 889.0, 677.0)       # qa_common 反面合成卡实测框
        self.assertEqual(
            visual_targets._id_document_box(True, card_box, 1160, 760),
            (0.0, 0.0, 1160.0, 760.0))

    def test_normal_page_uses_card_box(self):
        # 同一张卡放在手机竖拍页（1024×1821）上：应返回整卡框而不是整页
        card_box = (73.0, 162.0, 889.0, 677.0)
        page = (0.0, 0.0, 1024.0, 1821.0)
        box = visual_targets._id_document_box(True, card_box, 1024, 1821)
        self.assertIsNotNone(box)
        self.assertLess(_area_ratio(box, 1024, 1821), 0.95)
        self.assertNotEqual(box, page)


def main() -> int:
    print("数据隐站 · 整页证件过判回归（id_document / 身份证字段）")
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
