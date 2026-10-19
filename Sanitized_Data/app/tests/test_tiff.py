# -*- coding: utf-8 -*-
"""正射 TIF P0 测试：大图 / 位深 / 坐标中性化 / sidecar 与元数据。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_tiff.py
    cd app\\server && python -X utf8 ..\\tests\\test_tiff.py

设计说明
--------
* 纯离线、不联网；夹具默认生成到临时目录（tempfile.mkdtemp()）。
  想保留夹具人工查看时设置 ``YINZHAN_WRITE_FIXTURES=1``，会写到
  ``app/tests/fixtures/tiff/``（该目录的二进制夹具不入库，按需生成）。
* 需要可选依赖 ``rasterio``/``pyproj`` 的用例在缺失时自动 skip；
  Pillow 回退路径用 monkeypatch 模拟“没装 rasterio”的环境。
* 大图用例默认生成 20000×20000（4 亿像素）稀疏 TIFF：设置
  ``YINZHAN_TIFF_SKIP_BIG=1`` 可跳过（CI 快速档）。
* 红线对照：``samples/正射影像.tif`` 解析、识别、导出、复检必须保持
  e2e 基线（5/5、残留 0、真实 Geo 标签 0）。
"""
from __future__ import annotations

import json
import os
import random
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

from yinzhan import georef, pipeline, registry        # noqa: E402
from yinzhan.jobs import _load_rules                  # noqa: E402
from yinzhan.ocr import get_ocr                       # noqa: E402
from yinzhan.udm import ParseContext, ParseError, VerifyError  # noqa: E402

SAMPLES = REPO / "samples"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tiff"
SAMPLE_TIF = SAMPLES / "正射影像.tif"

HAS_RASTERIO = georef.has_rasterio()
BIG_W = BIG_H = 20000
SKIP_BIG = os.environ.get("YINZHAN_TIFF_SKIP_BIG", "") == "1"
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


# ────────────────────────── 夹具 ──────────────────────────

def _fixtures_root() -> Path:
    if os.environ.get("YINZHAN_WRITE_FIXTURES") == "1":
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        return FIXTURE_DIR
    return Path(tempfile.mkdtemp(prefix="yinzhan_tiff_"))


def _font(size: int):
    for candidate in FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:  # pragma: no cover
        return None


def _write_geotiff(path: Path, width: int, height: int, count: int = 3,
                   dtype: str = "uint8", *, crs: str | None = "EPSG:4490",
                   transform: tuple | None = None, with_overviews: bool = False) -> Path:
    """用 rasterio 造一张小 GeoTIFF（单页）。"""
    import rasterio
    from rasterio.transform import Affine

    tr = transform or (0.1, 0.0, 500000.0, 0.0, -0.1, 3450000.0)
    profile = dict(driver="GTiff", width=width, height=height, count=count,
                   dtype=dtype, compress="deflate")
    if crs:
        profile["crs"] = crs
    if tr is not None:
        profile["transform"] = Affine(*tr)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        arr = np.zeros((count, height, width), dtype=dtype)
        if dtype in ("uint8", "uint16"):
            arr[...] = 200 if dtype == "uint8" else 20000
        dst.write(arr)
        if with_overviews and max(width, height) >= 1024:
            from rasterio.enums import Resampling

            dst.build_overviews([2, 4, 8], Resampling.average)
    return path


def _write_multiframe(path: Path) -> Path:
    """2 帧 TIFF，首帧带 Geo 标签（Pillow 多页路径）。"""
    frame1 = Image.new("RGB", (400, 300), (240, 238, 228))
    d1 = ImageDraw.Draw(frame1)
    font = _font(26)
    if font is not None:
        d1.text((20, 20), "FRAME-1 图幅号 H51G023045", font=font, fill=(20, 20, 20))
    frame2 = Image.new("RGB", (400, 300), (210, 220, 235))
    d2 = ImageDraw.Draw(frame2)
    if font is not None:
        d2.text((20, 20), "FRAME-2 CGCS2000", font=font, fill=(20, 20, 20))
    path.parent.mkdir(parents=True, exist_ok=True)
    frame1.save(
        path, save_all=True, append_images=[frame2],
        tiffinfo={
            33550: (0.5, 0.5, 0.0),
            33922: (0.0, 0.0, 0.0, 500000.0, 3450000.0, 0.0),
            34735: (1, 1, 0, 1, 1024, 0, 1, 2),
        })
    return path


def _write_icc_tiff(path: Path) -> Path:
    """带 ICC(34675) + Geo 标签的小 TIFF。"""
    image = Image.new("RGB", (200, 120), (235, 232, 222))
    icc = b"ICC_PROFILE\x00" + bytes(range(256)) * 4
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, tiffinfo={
        33550: (0.1, 0.1, 0.0),
        33922: (0.0, 0.0, 0.0, 500000.0, 3450000.0, 0.0),
        34735: (1, 1, 0, 1, 1024, 0, 1, 2),
        34675: icc,
    }, compression="tiff_lzw")
    return path


def _write_meta_leak_tiff(path: Path) -> Path:
    """带 EXIF/XMP/描述等泄漏面标签的小 TIFF。"""
    image = Image.new("RGB", (200, 120), (235, 232, 222))
    image.save(path, tiffinfo={
        270: "项目：XX新区倾斜摄影 无人机航测",
        271: "DJI", 272: "Mavic 3E", 305: "ContextCapture 10.20",
        306: "2026:10:19 12:00:00", 315: "张三",
        33432: "Copyright XX测绘院",
        700: b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:creator>LISI</dc:creator></x:xmpmeta>',
    }, compression="tiff_lzw")
    return path


def _write_big_tiff(path: Path) -> Path:
    """4 亿像素稀疏 GeoTIFF（窗口写，磁盘 <1MB）。"""
    import rasterio
    from rasterio.transform import Affine
    from rasterio.windows import Window

    path.parent.mkdir(parents=True, exist_ok=True)
    block = 2048
    with rasterio.open(
            path, "w", driver="GTiff", width=BIG_W, height=BIG_H, count=1,
            dtype="uint8", transform=Affine(0.05, 0.0, 500000.0, 0.0, -0.05, 3450000.0),
            tiled=True, blockxsize=256, blockysize=256, compress="deflate") as dst:
        tile = np.zeros((block, block), dtype="uint8")
        for row in range(0, BIG_H, block):
            for col in range(0, BIG_W, block):
                h = min(block, BIG_H - row)
                w = min(block, BIG_W - col)
                dst.write(tile[:h, :w], 1, window=Window(col, row, w, h))
    return path


class Fixtures:
    """按需生成夹具（类级缓存，只生成一次）。"""

    root: Path | None = None

    @classmethod
    def get(cls) -> Path:
        if cls.root is None:
            cls.root = _fixtures_root()
        return cls.root

    @classmethod
    def big(cls) -> Path:
        path = cls.get() / "big_20000x20000.tif"
        if not path.exists():
            _write_big_tiff(path)
        return path

    @classmethod
    def u16(cls) -> Path:
        path = cls.get() / "u16_3band.tif"
        if not path.exists():
            _write_geotiff(path, 300, 200, 3, "uint16", with_overviews=True)
        return path

    @classmethod
    def rgbn(cls) -> Path:
        path = cls.get() / "rgba_4band.tif"
        if not path.exists():
            _write_geotiff(path, 200, 150, 4, "uint8")
        return path

    @classmethod
    def multiframe(cls) -> Path:
        path = cls.get() / "multiframe_2page.tif"
        if not path.exists():
            _write_multiframe(path)
        return path

    @classmethod
    def icc(cls) -> Path:
        path = cls.get() / "icc_geotiff.tif"
        if not path.exists():
            _write_icc_tiff(path)
        return path

    @classmethod
    def leaky(cls) -> Path:
        path = cls.get() / "meta_leak.tif"
        if not path.exists():
            _write_meta_leak_tiff(path)
        return path

    @classmethod
    def overviews(cls) -> Path:
        path = cls.get() / "overviews_1024.tif"
        if not path.exists():
            _write_geotiff(path, 1024, 768, 3, "uint8", with_overviews=True)
        return path

    @classmethod
    def plain(cls) -> Path:
        path = cls.get() / "plain_no_geo.tif"
        if not path.exists():
            _write_geotiff(path, 120, 80, 3, "uint8", crs=None, transform=None)
        return path


# ────────────────────────── 公共工具 ──────────────────────────

def _ctx() -> ParseContext:
    return ParseContext(ocr=get_ocr(), workdir=str(Fixtures.get()))


def _parser():
    registry.load_plugins()
    return registry.find_parser(SAMPLE_TIF)


def _detect(doc):
    rules, _pack, _index = _load_rules(doc.fmt)
    det = pipeline.detect(doc, rules, "standard")
    pipeline.decide(det.entities, det.rules, pipeline.Session())
    return det


def _tag_ids(path: Path) -> set[int]:
    return set(georef.scan_tiff(path).first_tags)


def _geo_tag_ids(path: Path) -> list[int]:
    tags = _tag_ids(path)
    return [t for t in sorted(georef.GEO_TAG_NAMES) if t in tags]


# ────────────────────────── 1. 地理数学 / 结构扫描 ──────────────────────────

class TestGeorefMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SAMPLE_TIF.is_file():
            raise unittest.SkipTest("缺少 samples/正射影像.tif")
        cls.structure = georef.scan_tiff(SAMPLE_TIF)
        cls.ref = georef.read_georef(SAMPLE_TIF, cls.structure)

    def test_scan_sample(self):
        st = self.structure
        self.assertEqual(st.frames, 1)
        self.assertEqual((st.width, st.height), (800, 600))
        self.assertIn(33550, st.first_tags)
        self.assertIn(33922, st.first_tags)
        self.assertIn(34735, st.first_tags)
        self.assertEqual(st.error, "")

    def test_read_real_georef(self):
        self.assertEqual(self.ref.kind, "real")
        self.assertIsNotNone(self.ref.transform)
        self.assertAlmostEqual(self.ref.transform[0], 0.1, places=9)
        if HAS_RASTERIO:
            self.assertEqual(self.ref.epsg, 4490)

    def test_make_params(self):
        params = georef.make_params(self.ref, seed=42)
        self.assertEqual(params["method"], "translate+rotate")
        self.assertEqual(georef.validate_params(params), [])
        self.assertAlmostEqual(params["scale"], 1.0)
        self.assertLessEqual(abs(params["theta_deg"]), 3.0)
        self.assertGreaterEqual(abs(params["dx"]), 200.0)
        self.assertNotEqual(abs(params["theta_deg"]), 0.0)
        # 同一 seed 必须可复现
        self.assertEqual(params, georef.make_params(self.ref, seed=42))
        # JSON 可序列化（vault 要求）
        json.loads(georef.params_to_json(params))
        # 成品 geotransform 必须已不再是源 transform
        self.assertNotEqual(
            [round(float(v), 9) for v in params["dst_transform"]],
            [round(float(v), 9) for v in params["src_transform"]])

    def test_roundtrip_pixel(self):
        params = georef.make_params(self.ref, seed=7)
        rng = random.Random(3)
        for _ in range(10):
            px, py = rng.uniform(0, 800), rng.uniform(0, 600)
            lx, ly = georef.pixel_to_local(params, px, py)
            bx, by = georef.local_to_pixel(params, lx, ly)
            self.assertAlmostEqual(bx, px, places=6)
            self.assertAlmostEqual(by, py, places=6)

    def test_roundtrip_world(self):
        params = georef.make_params(self.ref, seed=9)
        wx, wy = georef.affine_apply(params["src_transform"], 321.0, 123.0)
        lx, ly = georef.world_to_local(params, wx, wy)
        bx, by = georef.local_to_world(params, lx, ly)
        self.assertAlmostEqual(bx, wx, places=6)
        self.assertAlmostEqual(by, wy, places=6)

    def test_dst_transform_consistency(self):
        params = georef.make_params(self.ref, seed=11)
        for px, py in ((0, 0), (123.4, 456.7), (800, 600)):
            expect = georef.pixel_to_local(params, px, py)
            got = georef.affine_apply(params["dst_transform"], px, py)
            # params 里 transform 保留 9 位小数，允许亚微米级误差
            self.assertAlmostEqual(got[0], expect[0], delta=1e-4)
            self.assertAlmostEqual(got[1], expect[1], delta=1e-4)

    def test_strip_params_not_invertible(self):
        params = georef.strip_params(self.ref)
        self.assertEqual(params["method"], "strip")
        self.assertFalse(params["invertible"])
        self.assertEqual(georef.validate_params(params), [])
        with self.assertRaises(ValueError):
            georef.pixel_to_local(params, 0.0, 0.0)


class TestScanTiff(unittest.TestCase):
    @unittest.skipUnless(HAS_RASTERIO, "需要 rasterio 生成夹具")
    def test_overview_ifd_not_counted_as_page(self):
        st = georef.scan_tiff(Fixtures.overviews())
        self.assertEqual(st.frames, 1, "内部金字塔 IFD 不应计为多帧")
        self.assertGreaterEqual(st.overview_frames, 1)

    def test_multiframe_counted(self):
        st = georef.scan_tiff(Fixtures.multiframe())
        self.assertEqual(st.frames, 2)

    def test_corrupt_file_returns_error(self):
        path = Fixtures.get() / "corrupt.tif"
        path.write_bytes(b"this is not a tiff at all")
        st = georef.scan_tiff(path)
        self.assertNotEqual(st.error, "")
        self.assertEqual(st.frames, 0)


# ────────────────────────── 2. 样例解析 / 检测 ──────────────────────────

class TestSampleParse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SAMPLE_TIF.is_file():
            raise unittest.SkipTest("缺少 samples/正射影像.tif")
        registry.load_plugins()
        cls.parser = registry.find_parser(SAMPLE_TIF)
        cls.doc = cls.parser.parse(SAMPLE_TIF, _ctx())
        cls.det = _detect(cls.doc)

    def test_parser(self):
        self.assertEqual(self.parser.format_id, "tiff")

    def test_parse_engine_and_geo(self):
        self.assertEqual(self.doc.meta.get("format"), "TIFF")
        self.assertEqual(self.doc.meta.get("frames"), 1)
        self.assertTrue(self.doc.pages)
        names = self.doc.meta.get("geo_tags") or []
        for expect in ("ModelPixelScaleTag", "ModelTiepointTag", "GeoKeyDirectoryTag"):
            self.assertIn(expect, names)
        gt = (self.doc.extra.get("geo_transform") or {})
        self.assertTrue(gt, "解析应把源地理参考放进 doc.extra['geo_transform']")
        self.assertTrue(str(gt.get("crs") or "").startswith("EPSG:") if HAS_RASTERIO else True)

    def test_detect_entities(self):
        self.assertTrue(self.det.entities, "样例正射影像应识别出图面文字")
        types = {e.etype for e in self.det.entities}
        self.assertTrue(types & {"SHEET_NO", "CONTROL_POINT", "CRS"}, types)

    def test_noise_guard(self):
        """只有 Geo 标签、没有文字时也必须有可清理目标（jobs 的导出闸门）。"""
        doc = self.parser.parse(SAMPLE_TIF, _ctx())
        for page in doc.pages:
            page.blocks = []
        self.assertTrue(pipeline.has_cleanup_targets(doc))


# ────────────────────────── 3. 导出：中性化 / strip / 元数据 ──────────────────────────

class TestExportNeutralized(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SAMPLE_TIF.is_file():
            raise unittest.SkipTest("缺少 samples/正射影像.tif")
        if not HAS_RASTERIO:
            raise unittest.SkipTest("中性化写 LOCAL_CS 需要 rasterio")
        registry.load_plugins()
        cls.parser = registry.find_parser(SAMPLE_TIF)
        cls.doc = cls.parser.parse(SAMPLE_TIF, _ctx())
        cls.det = _detect(cls.doc)
        cls.out = Fixtures.get() / "sample_neutralized.tif"
        cls.res = pipeline.export(cls.doc, cls.parser, cls.det.entities, cls.out,
                                  {"mode": "ai", "geo_seed": 20261020})
        cls.params = cls.doc.extra.get("geo_params") or {}

    def test_output_exists(self):
        self.assertTrue(self.out.is_file())
        self.assertGreater(self.res.size, 0)

    def test_no_real_georef(self):
        """成品不能带原始 CRS/tiepoint：用 rasterio 判定为本地任意 CRS。"""
        if not HAS_RASTERIO:
            self.skipTest("需要 rasterio")
        import rasterio

        with rasterio.open(self.out) as src:
            self.assertIsNotNone(src.crs)
            self.assertIsNone(src.crs.to_epsg(), "成品 CRS 不应再是真实 EPSG")
            wkt = src.crs.to_wkt().upper()
            self.assertTrue(wkt.startswith("LOCAL_CS") or "ENGCRS" in wkt, wkt[:80])

    def test_original_transform_replaced(self):
        """成品 geotransform 必须等于记录的中性化参数（不是源 tiepoint）。"""
        if not HAS_RASTERIO:
            self.skipTest("需要 rasterio")
        import rasterio

        with rasterio.open(self.out) as src:
            got = (src.transform.a, src.transform.b, src.transform.c,
                   src.transform.d, src.transform.e, src.transform.f)
        expect = tuple(float(v) for v in self.params["dst_transform"])
        for i in range(6):
            self.assertAlmostEqual(got[i], expect[i], places=6)
        src_tr = tuple(float(v) for v in (self.doc.extra["geo_transform"]["transform"]))
        self.assertNotEqual(tuple(round(v, 9) for v in got),
                            tuple(round(v, 9) for v in src_tr))

    def test_metadata_clean(self):
        tags = _tag_ids(self.out)
        for leak_tag, name in georef.META_LEAK_TAGS.items():
            self.assertNotIn(leak_tag, tags, f"成品仍含 {name}")
        # 结构性 Geo 标签允许存在，但必须没有 RPC / 原始 GeoDouble 之外的残留
        self.assertNotIn(50844, tags)

    def test_pipeline_verify(self):
        try:
            ver = pipeline.verify(self.out, self.det.entities, self.parser, _ctx())
        except VerifyError as exc:
            self.fail(f"pipeline.verify 失败：{exc}")
        self.assertTrue(ver.get("verified"))
        self.assertEqual(ver.get("residual"), 0)

    def test_params_reversible(self):
        self.assertEqual(georef.validate_params(self.params), [])
        self.assertTrue(self.params.get("invertible"))
        self.assertEqual(self.params["method"], "translate+rotate")
        # 成品局部坐标 → 原始像素坐标 可还原
        for px, py in ((0, 0), (400, 300), (799, 599)):
            lx, ly = georef.pixel_to_local(self.params, px, py)
            bx, by = georef.local_to_pixel(self.params, lx, ly)
            self.assertAlmostEqual(bx, px, places=6)
            self.assertAlmostEqual(by, py, places=6)

    def test_sidecars_cleaned(self):
        out = Fixtures.get() / "sample_sidecars.tif"
        for suffix in (".tfw", ".prj", ".aux.xml", ".ovr", ".kml", ".rpc", ".rpb", ".imd", ".xml"):
            out.with_suffix(suffix).write_text("fake", encoding="utf-8")
        pipeline.export(self.doc, self.parser, self.det.entities, out,
                        {"mode": "human", "geo_seed": 3})
        self.assertEqual(georef.read_linked_sidecars(out), [])

    def test_strip_mode(self):
        out = Fixtures.get() / "sample_strip.tif"
        pipeline.export(self.doc, self.parser, self.det.entities, out,
                        {"mode": "human", "geo_mode": "strip"})
        self.assertEqual(_geo_tag_ids(out), [], "strip 模式成品不应再有任何 Geo 标签")
        self.assertEqual(georef.verify_exported_geo(out, expect_neutralized=False), [])
        if HAS_RASTERIO:
            import rasterio

            with rasterio.open(out) as src:
                self.assertIsNone(src.crs)


class TestExportMetadata(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_RASTERIO:
            raise unittest.SkipTest("需要 rasterio 写中性化成品")
        registry.load_plugins()
        cls.parser = registry.find_parser(SAMPLE_TIF)

    def _doc(self, path: Path):
        return self.parser.parse(path, _ctx())

    def test_icc_stripped(self):
        doc = self._doc(Fixtures.icc())
        out = Fixtures.get() / "icc_out.tif"
        pipeline.export(doc, self.parser, [], out, {"mode": "human", "geo_seed": 1})
        self.assertNotIn(34675, _tag_ids(out), "ICC profile 必须剥离")

    def test_exif_xmp_stripped(self):
        doc = self._doc(Fixtures.leaky())
        out = Fixtures.get() / "leaky_out.tif"
        pipeline.export(doc, self.parser, [], out, {"mode": "human", "geo_seed": 1})
        tags = _tag_ids(out)
        for leak_tag, name in georef.META_LEAK_TAGS.items():
            self.assertNotIn(leak_tag, tags, f"成品仍含 {name}")

    @unittest.skipUnless(HAS_RASTERIO, "需要 rasterio 生成 16bit 夹具")
    def test_16bit_3band_preserved(self):
        doc = self._doc(Fixtures.u16())
        self.assertEqual(doc.meta.get("dtype"), "uint16")
        self.assertEqual(doc.meta.get("bands"), 3)
        out = Fixtures.get() / "u16_out.tif"
        pipeline.export(doc, self.parser, [], out, {"mode": "human", "geo_seed": 2})
        import rasterio

        with rasterio.open(out) as src:
            self.assertEqual(src.dtypes[0], "uint16")
            self.assertEqual(src.count, 3)
            self.assertIsNone(src.crs.to_epsg() if src.crs else None)
        self.assertEqual(georef.verify_exported_geo(
            out, expect_neutralized=True, params=doc.extra.get("geo_params")), [])

    @unittest.skipUnless(HAS_RASTERIO, "需要 rasterio 生成 4 波段夹具")
    def test_4band_preserved(self):
        doc = self._doc(Fixtures.rgbn())
        self.assertEqual(doc.meta.get("bands"), 4)
        out = Fixtures.get() / "rgba_out.tif"
        pipeline.export(doc, self.parser, [], out, {"mode": "human", "geo_seed": 4})
        import rasterio

        with rasterio.open(out) as src:
            self.assertEqual(src.count, 4)


class TestFallbacks(unittest.TestCase):
    def setUp(self):
        registry.load_plugins()
        self.parser = registry.find_parser(SAMPLE_TIF)

    def test_pillow_fallback_parse_and_export(self):
        """模拟没装 rasterio：解析/导出走 Pillow，明确 warning 且剥离 Geo。"""
        old = georef.HAS_RASTERIO
        georef.HAS_RASTERIO = False
        try:
            doc = self.parser.parse(SAMPLE_TIF, _ctx())
            self.assertEqual(doc.meta.get("engine"), "pillow")
            self.assertTrue(doc.meta.get("geo_tags"))
            out = Fixtures.get() / "pillow_fallback.tif"
            res = pipeline.export(doc, self.parser, [], out, {"mode": "human"})
            self.assertTrue(any("未做坐标中性化" in w for w in res.warnings),
                            res.warnings)
            self.assertEqual(_geo_tag_ids(out), [])
        finally:
            georef.HAS_RASTERIO = old

    def test_pillow_fallback_refuses_huge(self):
        """没装 rasterio 时，超大影像必须明确中文报错，不能静默失败。"""
        if SKIP_BIG:
            self.skipTest("YINZHAN_TIFF_SKIP_BIG=1")
        big = Fixtures.big()
        old = georef.HAS_RASTERIO
        georef.HAS_RASTERIO = False
        try:
            with self.assertRaises(ParseError) as caught:
                self.parser.parse(big, _ctx())
            self.assertIn("rasterio", str(caught.exception))
        finally:
            georef.HAS_RASTERIO = old

    def test_photo_style_no_georef(self):
        """无地理参考的普通 TIFF：不报错、不做中性化、只剥离。"""
        with Image.open(SAMPLE_TIF) as src:
            plain_img = src.convert("RGB").resize((320, 240))
        path = Fixtures.get() / "photo_no_geo.tif"
        plain_img.save(path)
        doc = self.parser.parse(path, _ctx())
        self.assertFalse(doc.meta.get("geo_tags"))
        out = Fixtures.get() / "photo_no_geo_out.tif"
        pipeline.export(doc, self.parser, [], out, {"mode": "human"})
        self.assertEqual(_geo_tag_ids(out), [])


class TestMultiFrame(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        cls.parser = registry.find_parser(Fixtures.multiframe())
        cls.doc = cls.parser.parse(Fixtures.multiframe(), _ctx())

    def test_parse_frames(self):
        self.assertEqual(self.doc.meta.get("frames"), 2)
        self.assertEqual(len(self.doc.pages), 2)

    def test_export_strips_geo_and_warns(self):
        out = Fixtures.get() / "multiframe_out.tif"
        res = pipeline.export(self.doc, self.parser, [], out, {"mode": "human"})
        self.assertTrue(any("未做坐标中性化" in w for w in res.warnings), res.warnings)
        st = georef.scan_tiff(out)
        self.assertEqual(st.frames, 2)
        self.assertEqual(_geo_tag_ids(out), [])
        self.assertEqual(georef.verify_exported_geo(out, expect_neutralized=False), [])


# ────────────────────────── 4. 大图 ──────────────────────────

class TestBigImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_RASTERIO:
            raise unittest.SkipTest("大图路线需要 rasterio")
        if SKIP_BIG:
            raise unittest.SkipTest("YINZHAN_TIFF_SKIP_BIG=1")
        registry.load_plugins()
        cls.parser = registry.find_parser(Fixtures.big())
        cls.big = Fixtures.big()

    def test_parse_big_without_pillow_limit(self):
        t0 = time.time()
        doc = self.parser.parse(self.big, _ctx())
        elapsed = time.time() - t0
        self.assertEqual(doc.meta.get("engine"), "rasterio")
        self.assertEqual(doc.meta.get("size"), [BIG_W, BIG_H])
        self.assertFalse(doc.meta.get("full_read"))
        self.assertEqual(doc.meta.get("frames"), 1)
        self.assertTrue(any("降采样预览" in w or "扫描上限" in w for w in doc.warnings),
                        doc.warnings)
        # 4 亿像素在普通机器上应在 30s 内解析完（含 OCR 预览）
        self.assertLess(elapsed, 60.0)

    def test_export_big_neutralized(self):
        doc = self.parser.parse(self.big, _ctx())
        out = Fixtures.get() / "big_neutralized.tif"
        t0 = time.time()
        res = pipeline.export(doc, self.parser, [], out, {"mode": "human", "geo_seed": 10})
        elapsed = time.time() - t0
        self.assertTrue(out.is_file())
        self.assertLess(elapsed, 180.0)
        st = georef.scan_tiff(out)
        self.assertEqual(st.frames, 1)
        self.assertEqual((st.width, st.height), (BIG_W, BIG_H))
        self.assertGreaterEqual(st.overview_frames, 1, "大图应生成内部金字塔")
        self.assertEqual(georef.verify_exported_geo(
            out, expect_neutralized=True, params=doc.extra.get("geo_params")), [])
        # 参数可还原
        params = doc.extra.get("geo_params") or {}
        lx, ly = georef.pixel_to_local(params, 12345.0, 6789.0)
        bx, by = georef.local_to_pixel(params, lx, ly)
        self.assertAlmostEqual(bx, 12345.0, places=6)
        self.assertAlmostEqual(by, 6789.0, places=6)


# ────────────────────────── main ──────────────────────────

def main() -> int:
    print("数据隐站 · 正射 TIF P0 测试（大图/位深/中性化/sidecar）")
    print("-" * 64)
    print(f"rasterio={HAS_RASTERIO} pyproj={georef.has_pyproj()} "
          f"fixtures={FIXTURE_DIR if os.environ.get('YINZHAN_WRITE_FIXTURES') == '1' else 'tempdir'}")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("-" * 64)
    if result.wasSuccessful():
        print(f"通过：{result.testsRun} 项（skipped={len(result.skipped)}）")
        return 0
    print(f"失败：failures={len(result.failures)} errors={len(result.errors)}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover
        import traceback

        traceback.print_exc()
        sys.exit(3)
