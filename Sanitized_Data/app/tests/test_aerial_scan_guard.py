# -*- coding: utf-8 -*-
"""无人机/超大照片视觉检测降误报回归（2026-10-25 QA DR-01/DR-02 修复）。

覆盖：
1. `_cross_check_large_photo_faces`：超大照片丢弃无 YuNet 佐证的 Haar 大框，
   保留 YuNet/小框/与 YuNet 重叠的 Haar 框；
2. `_large_photo_face_cross_check`：只在 image/tiff + 超大图 + YuNet 可用时启用；
3. barcodes `_filter_unverified_code_hits`：≥12MP 大图上过滤未解码的小码候选，
   已解码/大码/普通尺寸页面保留；
4. image_parser：EXIF 含 DJI 机型时给出「无人机航拍影像」warning + meta 标记。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="yz_aerial_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import registry               # noqa: E402
from yinzhan import visual_targets as vt    # noqa: E402
from yinzhan import barcodes                # noqa: E402
from yinzhan import mediascan               # noqa: E402
from yinzhan.parsers.image_parser import ImageParser  # noqa: E402
from yinzhan.udm import ParseContext        # noqa: E402


class FaceCrossCheckTest(unittest.TestCase):
    def test_cross_check_drops_large_single_engine(self):
        found = [
            ((10, 10, 40, 40), 0.75, "yunet"),        # 小框单引擎 → 保留
            ((12, 12, 45, 45), 0.80, "frontal"),      # 与 YuNet 重叠 → 保留
            ((500, 500, 540, 540), 0.70, "frontal"),  # 小框单引擎 → 保留
            ((100, 100, 300, 300), 0.92, "frontal"),  # Haar 大框纹理误报 → 丢
            ((700, 700, 820, 820), 0.90, "yunet"),    # YuNet 大框单引擎 → 丢
        ]
        kept = vt._cross_check_large_photo_faces(found)
        boxes = [item[0] for item in kept]
        self.assertEqual(len(kept), 3, kept)
        self.assertIn((10, 10, 40, 40), boxes)
        self.assertIn((12, 12, 45, 45), boxes)
        self.assertIn((500, 500, 540, 540), boxes)
        self.assertNotIn((100, 100, 300, 300), boxes)
        self.assertNotIn((700, 700, 820, 820), boxes)

    def test_drone_scene_face_filter(self):
        found = [
            ((10, 10, 40, 40), 0.80, "yunet"),      # 原始 30px 小框 → 保留
            ((100, 100, 280, 280), 0.92, "frontal"),  # 180px 大框 → 丢
            ((500, 500, 580, 580), 0.87, "frontal"),  # 80px 大框 → 丢
            ((700, 700, 730, 730), 0.55, "yunet"),  # 小框但低置信 → 丢
        ]
        kept = vt._filter_drone_scene_faces(found)
        self.assertEqual([item[0] for item in kept], [(10, 10, 40, 40)])

    def test_drone_scene_face_filter_uses_gsd_scale(self):
        found = [((0, 0, 20, 20), 0.80, "yunet"),
                 ((0, 0, 100, 100), 0.80, "frontal")]
        # GSD 0.004m/px + 预览放大倍数 5（原图/扫描图）→ 0.6m 人头在扫描图约 30px
        kept = vt._filter_drone_scene_faces(found, gsd_m=0.004, pixel_scale=5.0)
        self.assertEqual([item[0] for item in kept], [(0, 0, 20, 20)])

    def test_drone_scene_detection_helper(self):
        from types import SimpleNamespace
        self.assertTrue(vt._drone_scene(SimpleNamespace(options={"drone": True})))
        self.assertFalse(vt._drone_scene(SimpleNamespace(options={"fmt": "image"})))

    def test_gate_conditions(self):
        self.assertTrue(vt._large_photo_face_cross_check(True, 5472, True))
        self.assertFalse(vt._large_photo_face_cross_check(True, 1200, True),
                         "普通尺寸照片不启用（避免误伤既有行为）")
        self.assertFalse(vt._large_photo_face_cross_check(False, 5472, True),
                         "非 photo/tiff 格式不启用")
        self.assertFalse(vt._large_photo_face_cross_check(True, 5472, False),
                         "YuNet 不可用时不启用")
        old = os.environ.get("YINZHAN_SCAN_FACE_CROSS_CHECK")
        try:
            os.environ["YINZHAN_SCAN_FACE_CROSS_CHECK"] = "0"
            self.assertFalse(vt._large_photo_face_cross_check(True, 5472, True))
        finally:
            if old is None:
                os.environ.pop("YINZHAN_SCAN_FACE_CROSS_CHECK", None)
            else:
                os.environ["YINZHAN_SCAN_FACE_CROSS_CHECK"] = old


class UnverifiedCodeFilterTest(unittest.TestCase):
    @staticmethod
    def _hit(rect, conf, kind="qrcode"):
        return mediascan.ScanHit(kind, rect, conf, "test")

    def test_large_image_drops_tiny_unverified(self):
        hits = [
            self._hit((100, 100, 170, 170), 0.50),      # 4900 px² 未解码小框 → 丢
            self._hit((200, 200, 215, 215), 0.50),      # 225 px² → 丢
            self._hit((300, 300, 500, 500), 0.50),      # 40000 px² 大码 → 留
            self._hit((600, 600, 660, 660), 0.95),      # 已解码 → 留
        ]
        kept = barcodes._filter_unverified_code_hits(hits, 5472, 3648)  # ~20MP
        self.assertEqual(len(kept), 2, kept)
        self.assertIn((300, 300, 500, 500), [h.rect for h in kept])
        self.assertIn((600, 600, 660, 660), [h.rect for h in kept])

    def test_normal_page_keeps_all(self):
        hits = [self._hit((100, 100, 170, 170), 0.50)]
        kept = barcodes._filter_unverified_code_hits(hits, 2480, 3508)  # A4 300dpi
        self.assertEqual(kept, hits, "普通尺寸页面行为不变")

    def test_keep_unverified_env(self):
        old = os.environ.get("YINZHAN_BARCODE_KEEP_UNVERIFIED")
        try:
            os.environ["YINZHAN_BARCODE_KEEP_UNVERIFIED"] = "1"
            hits = [self._hit((100, 100, 170, 170), 0.50)]
            self.assertEqual(barcodes._filter_unverified_code_hits(hits, 5472, 3648),
                             hits)
        finally:
            if old is None:
                os.environ.pop("YINZHAN_BARCODE_KEEP_UNVERIFIED", None)
            else:
                os.environ["YINZHAN_BARCODE_KEEP_UNVERIFIED"] = old


class DronePhotoWarningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        from PIL import Image
        cls.drone = TMP / "drone.jpg"
        exif = Image.Exif()
        exif[271] = "DJI"
        exif[272] = "FC6310"
        Image.new("RGB", (320, 240), (120, 120, 120)).save(
            cls.drone, quality=85, exif=exif)
        cls.normal = TMP / "normal.jpg"
        Image.new("RGB", (320, 240), (120, 120, 120)).save(cls.normal, quality=85)

    def test_drone_photo_warning(self):
        doc = ImageParser().parse(self.drone, ParseContext())
        self.assertTrue(doc.meta.get("drone_photo"))
        self.assertTrue(any("无人机航拍影像" in w for w in doc.warnings),
                        doc.warnings)

    def test_normal_photo_no_drone_warning(self):
        doc = ImageParser().parse(self.normal, ParseContext())
        self.assertFalse(any("无人机航拍影像" in w for w in doc.warnings),
                         doc.warnings)


class MetadataOnlyImageExportTest(unittest.TestCase):
    """只有 EXIF/GPS、没有识别实体的图片也必须能导出「仅清理」版（DR-05 回归）。"""

    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        from PIL import Image
        cls.path = TMP / "gps_only.jpg"
        exif = Image.Exif()
        exif[271] = "DJI"
        exif[272] = "FC6310"
        Image.new("RGB", (320, 240), (100, 130, 110)).save(
            cls.path, quality=85, exif=exif)

    def test_cleanup_target_detected(self):
        from yinzhan import pipeline
        doc = ImageParser().parse(self.path, ParseContext())
        self.assertTrue(doc.meta.get("exif"), "应记录 EXIF 存在")
        self.assertTrue(pipeline.has_cleanup_targets(doc),
                        "无实体但含 EXIF/GPS 时应允许仅清理导出")

    def test_metadata_only_export(self):
        from yinzhan.jobs import MANAGER
        job = MANAGER.create(self.path, strictness="standard", pack_ids=None,
                             id_card_mode="fields", time_blur=False, origin="api")
        import time
        deadline = time.time() + 120
        while job.stage not in ("ready", "failed", "error") and time.time() < deadline:
            time.sleep(0.2)
        self.assertEqual(job.stage, "ready", job.error)
        for mode in ("ai", "human"):
            out = TMP / f"gps_only_{mode}.jpg"
            out.unlink(missing_ok=True)
            res = MANAGER._export_to(job, mode, out, do_verify=True)
            self.assertTrue(out.is_file())
            self.assertEqual(res["verify"].get("residual"), 0)
            from PIL import Image
            with Image.open(out) as img:
                self.assertEqual(dict(img.getexif()), {}, f"{mode} 成品仍有 EXIF")
                self.assertFalse(img.info.get("xmp"), f"{mode} 成品仍有 XMP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
