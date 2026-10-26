# -*- coding: utf-8 -*-
"""非画面敏感数据（GPS/EXIF/地理坐标）进「隐去清单」回归。

2026-10-26 用户反馈：工作台右侧「隐去清单」只显示画面里的框，TIFF 正射影像的
GeoTIFF 地理坐标、照片的 EXIF/GPS 这些“看不见的脱敏”没有列出来，用户无法确认
到底隐去了什么。修复后这些元数据项会作为实体进入清单（value 已用 * 打码、无 spans），
导出时随元数据一并剥离。

本测试锁定：
1. EXIF/GPS/XMP 打码条目生成，display 不出现原始数字；
2. GeoTIFF 正射影像生成「GPS/地理坐标」实体，原始原点坐标不出现；
3. 元数据实体不影响导出画面（无涂黑）、导出后不残留原始 EPSG；
4. 照片任务能列出 EXIF 项，导出后 EXIF 为空。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="yz_meta_ent_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import registry                              # noqa: E402
from yinzhan.jobs import MANAGER                          # noqa: E402
from yinzhan.parsers.image_parser import (                # noqa: E402
    _mask_digits, _meta_masked_items,
)


class FakeExif(dict):
    def __init__(self, gps: dict | None = None):
        super().__init__()
        self._gps = gps or {}

    def get_ifd(self, tag):
        return self._gps if int(tag) == 0x8825 else {}


class FakeImage:
    def __init__(self, exif: dict, info: dict | None = None):
        self._exif = exif
        self.info = info or {}

    def getexif(self):
        return self._exif


class MaskedItemsTest(unittest.TestCase):
    def test_mask_digits(self):
        self.assertEqual(_mask_digits("N 60.123456°"), "N **.******°")

    def test_plain_image_has_no_items(self):
        from PIL import Image
        self.assertEqual(_meta_masked_items(Image.new("RGB", (8, 8), "red")), [])

    def test_exif_gps_items_are_masked(self):
        exif = FakeExif(gps={1: "N", 2: (39.0, 54.0, 30.0), 3: "E",
                             4: (116.0, 23.0, 15.0), 6: 123.4})
        exif.update({271: "DJI", 272: "FC6310", 305: "v01.00",
                     36867: "2026:10:25 12:00:00"})
        image = FakeImage(exif, info={"xmp": b"<x/>", "icc_profile": b"icc"})
        items = _meta_masked_items(image)
        types = {it["type"] for it in items}
        self.assertEqual(types, {"GPS", "EXIF", "META"})
        for it in items:
            self.assertNotRegex(it["display"], r"\d",
                                f"展示文本不得含原始数字：{it}")
        gps = next(it for it in items if it["type"] == "GPS")
        self.assertIn("GPS 坐标", gps["display"])
        self.assertIn("N ", gps["display"])


def _wait_ready(job, timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while job.stage not in ("ready", "failed", "error") and time.time() < deadline:
        time.sleep(0.2)
    if job.stage != "ready":
        raise AssertionError(f"任务未就绪：{job.stage} {job.error}")


def _make_geotiff(path: Path) -> None:
    import numpy as np
    import rasterio

    data = np.zeros((3, 64, 64), dtype=np.uint8)
    data[0] = 120
    data[1] = 160
    data[2] = 90
    with rasterio.open(
            path, "w", driver="GTiff", width=64, height=64, count=3,
            dtype="uint8", crs="EPSG:32617",
            transform=rasterio.transform.from_origin(500000.0, 4000000.0, 0.5, 0.5),
            tiled=False) as ds:
        ds.write(data)


class TiffGeoEntityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        cls.path = TMP / "ortho_geo.tif"
        _make_geotiff(cls.path)
        cls.job = MANAGER.create(cls.path, strictness="standard", pack_ids=None,
                                 id_card_mode="fields", time_blur=False, origin="api")
        _wait_ready(cls.job)

    def _meta_ents(self):
        return [e for e in self.job.entities if e.origin == "metadata"]

    def test_geo_entity_listed_with_masked_value(self):
        metas = self._meta_ents()
        self.assertEqual(len(metas), 1, [(e.etype, e.value) for e in self.job.entities])
        ent = metas[0]
        self.assertIn(ent.etype, ("GEO", "GPS"))
        self.assertIn("GPS", ent.label)
        self.assertIn("*", ent.value, "坐标必须打码")
        self.assertNotIn("500000", ent.value, "原始投影 X 坐标不得出现")
        self.assertNotIn("4000000", ent.value, "原始投影 Y 坐标不得出现")
        self.assertEqual(ent.spans, [], "元数据实体不落在画面上")
        self.assertEqual(ent.status, "accepted")
        # 工作台行展示：左 = 已打码的值，右 = 已移除
        d = ent.to_dict()
        self.assertIn("*", d["value"])
        self.assertEqual(d["surrogate"], "已移除")
        self.assertTrue(any("非画面敏感数据" in w for w in self.job.warnings))

    def test_export_keeps_pixels_and_strips_geo(self):
        import numpy as np
        import rasterio

        out = TMP / "ortho_geo_ai.tif"
        info = MANAGER._export_to(self.job, "ai", out, do_verify=True)
        self.assertTrue(out.is_file())
        self.assertTrue((info.get("verify") or {}).get("verified", True))
        self.assertTrue(any("非画面敏感数据" in w for w in info.get("warnings") or []),
                        info.get("warnings"))
        with rasterio.open(self.path) as src, rasterio.open(out) as dst:
            a = src.read()
            b = dst.read()
            self.assertTrue(np.array_equal(a, b), "元数据实体不应在画面上涂黑")
            self.assertFalse(dst.crs and "32617" in str(dst.crs),
                             f"成品不得保留原始 EPSG：{dst.crs}")


class ImageExifEntityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        from PIL import Image
        cls.path = TMP / "photo_exif.jpg"
        image = Image.new("RGB", (64, 64), (200, 100, 50))
        exif = Image.Exif()
        exif[271] = "DJI"
        exif[272] = "FC6310"
        exif[305] = "v01.00"
        exif[306] = "2026:10:25 12:00:00"
        image.save(cls.path, format="JPEG", exif=exif)
        cls.job = MANAGER.create(cls.path, strictness="standard", pack_ids=None,
                                 id_card_mode="fields", time_blur=False, origin="api")
        _wait_ready(cls.job)

    def test_exif_entity_listed_masked(self):
        metas = [e for e in self.job.entities if e.origin == "metadata"]
        exifs = [e for e in metas if e.etype == "EXIF"]
        self.assertTrue(exifs, [(e.etype, e.value) for e in self.job.entities])
        value = exifs[0].value
        self.assertIn("*", value)
        self.assertNotRegex(value, r"\d", f"EXIF 展示不得含原始数字：{value}")

    def test_export_strips_exif(self):
        from PIL import Image
        out = TMP / "photo_exif_ai.jpg"
        MANAGER._export_to(self.job, "ai", out, do_verify=True)
        self.assertTrue(out.is_file())
        with Image.open(out) as img:
            self.assertEqual(len(dict(img.getexif())), 0, "成品不得残留 EXIF")


if __name__ == "__main__":
    unittest.main(verbosity=2)
