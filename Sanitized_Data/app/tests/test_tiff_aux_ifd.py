# -*- coding: utf-8 -*-
"""TIFF IFD 分类回归（2026-10-25 正射影像实测）。

真实 OAM/Pix4D 正射影像的第 2 个 IFD 常是 NewSubfileType=4（透明蒙版）或
=1（内部金字塔 overview），不是第二帧。旧逻辑只认 bit0，把 mask 当帧 →
frames=2 → 跳过 rasterio 单帧路径 → 标准正射影像无法处理（并误报 rasterio 未安装）。

本测试用手工构造的最小 TIFF（1×1 像素、可指定每页 NewSubfileType）锁定：
1. `_ifd_kind` 分类：0/2→frame，1→overview，4/5→mask；
2. `scan_tiff`：mask/overview IFD 不计入 frames；
3. 真正多帧（两个 subfiletype=0 页）仍算 frames=2；
4. 带 mask IFD 的 TIFF 走 rasterio 解析路径（不再误判多帧）。
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="yz_tiff_aux_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import georef, registry          # noqa: E402
from yinzhan.parsers.tiff_parser import TiffParser  # noqa: E402
from yinzhan.udm import ParseContext          # noqa: E402


def _entry(tag: int, typ: int, count: int, value: int) -> bytes:
    if typ == 3:      # SHORT：值内联在 4 字节字段的低 2 字节
        field = struct.pack("<HH", value & 0xFFFF, 0)
    else:             # LONG
        field = struct.pack("<I", value & 0xFFFFFFFF)
    return struct.pack("<HHI", tag, typ, count) + field


def build_tiff(path: Path, subfiletypes: list[int]) -> None:
    """构造最小灰度 TIFF：每页 1×1 像素、未压缩，指定 NewSubfileType。"""
    pages = len(subfiletypes)
    base_entries = [
        (256, 4, 1, 1),      # ImageWidth
        (257, 4, 1, 1),      # ImageLength
        (258, 3, 1, 8),      # BitsPerSample
        (259, 3, 1, 1),      # Compression = none
        (262, 3, 1, 1),      # Photometric = BlackIsZero
        (273, 4, 1, 0),      # StripOffsets（占位，后面回填）
        (277, 3, 1, 1),      # SamplesPerPixel
        (278, 4, 1, 1),      # RowsPerStrip
        (279, 4, 1, 1),      # StripByteCounts
    ]
    # 每页条目数（带 NewSubfileType 则 +1）
    counts = [len(base_entries) + (1 if st is not None else 0) for st in subfiletypes]
    ifd_sizes = [2 + 12 * n + 4 for n in counts]
    ifd_offsets = [8]
    for size in ifd_sizes[:-1]:
        ifd_offsets.append(ifd_offsets[-1] + size)
    data_offsets = []
    pos = ifd_offsets[-1] + ifd_sizes[-1]
    for _ in range(pages):
        data_offsets.append(pos)
        pos += 1
    body = bytearray()
    body += b"II" + struct.pack("<H", 42) + struct.pack("<I", ifd_offsets[0])
    for idx, st in enumerate(subfiletypes):
        entries = list(base_entries)
        entries[5] = (273, 4, 1, data_offsets[idx])
        if st is not None:
            entries.append((254, 4, 1, int(st)))
        entries.sort(key=lambda item: item[0])
        next_off = ifd_offsets[idx + 1] if idx + 1 < pages else 0
        body += struct.pack("<H", len(entries))
        for tag, typ, count, value in entries:
            body += _entry(tag, typ, count, value)
        body += struct.pack("<I", next_off)
    body += b"\x00" * pages
    path.write_bytes(bytes(body))


class IfdKindTest(unittest.TestCase):
    def test_kind_mapping(self):
        self.assertEqual(georef._ifd_kind(0), "frame")
        self.assertEqual(georef._ifd_kind(2), "frame")     # 多页文档页
        self.assertEqual(georef._ifd_kind(1), "overview")  # 内部金字塔
        self.assertEqual(georef._ifd_kind(4), "mask")      # 透明蒙版
        self.assertEqual(georef._ifd_kind(5), "mask")      # mask+overview
        self.assertEqual(georef._ifd_kind(8), "frame")


class ScanTiffTest(unittest.TestCase):
    def test_mask_ifd_not_counted_as_frame(self):
        path = TMP / "with_mask.tif"
        build_tiff(path, [0, 4])
        st = georef.scan_tiff(path)
        self.assertEqual(st.error, "")
        self.assertEqual(st.frames, 1, "mask IFD 不是第二帧")
        self.assertEqual(st.mask_frames, 1)

    def test_overview_ifd_not_counted_as_frame(self):
        path = TMP / "with_overview.tif"
        build_tiff(path, [0, 1])
        st = georef.scan_tiff(path)
        self.assertEqual(st.frames, 1)
        self.assertEqual(st.overview_frames, 1)

    def test_true_multiframe_still_counted(self):
        path = TMP / "two_frames.tif"
        build_tiff(path, [0, 0])
        st = georef.scan_tiff(path)
        self.assertEqual(st.frames, 2, "真正多帧 TIFF 仍应按 2 帧处理")

    def test_mask_tiff_uses_rasterio_path(self):
        registry.load_plugins()
        if not georef.has_rasterio():
            self.skipTest("未安装 rasterio")
        path = TMP / "mask_parse.tif"
        build_tiff(path, [0, 4])
        doc = TiffParser().parse(path, ParseContext())
        self.assertEqual(doc.meta.get("engine"), "rasterio",
                         f"带 mask IFD 的单帧 TIFF 应走 rasterio 路径：{doc.meta}")
        self.assertEqual(doc.meta.get("frames"), 1)


class YCbCrJpegTiffTest(unittest.TestCase):
    """JPEG 压缩的 YCbCr GeoTIFF（正射影像常见格式）回归。

    旧代码 `src.compression.value` 对字符串压缩名（"YCbCr JPEG"）崩溃；
    YCbCr 像素被当 RGB 直接写出会偏色 → 新增 _ycbcr_to_rgb 转换。
    """

    @classmethod
    def setUpClass(cls):
        import numpy as np
        from PIL import Image
        cls.path = TMP / "ycbcr_pil.tif"
        y = np.full((128, 128), 76, np.uint8)
        cb = np.full((128, 128), 85, np.uint8)
        cr = np.full((128, 128), 255, np.uint8)
        img = Image.merge("YCbCr", [Image.fromarray(y), Image.fromarray(cb),
                                    Image.fromarray(cr)])
        img.save(cls.path, format="TIFF", compression="jpeg")

    def test_compression_helper_handles_string(self):
        import rasterio
        from yinzhan.parsers.tiff_parser import _src_compression
        with rasterio.open(self.path) as src:
            text = _src_compression(src)
        self.assertIn("jpeg", text, f"JPEG 压缩名应可读：{text}")

    def test_is_ycbcr_source_detection(self):
        from types import SimpleNamespace
        from yinzhan.parsers.tiff_parser import _is_ycbcr_source

        def fake(names):
            return SimpleNamespace(colorinterp=[SimpleNamespace(name=n) for n in names])

        self.assertTrue(_is_ycbcr_source(fake(["ycbcr", "cb", "cr"])))
        self.assertFalse(_is_ycbcr_source(fake(["red", "green", "blue"])))
        self.assertFalse(_is_ycbcr_source(fake(["gray"])))

    def test_ycbcr_math(self):
        import numpy as np
        from yinzhan.parsers.tiff_parser import _ycbcr_to_rgb
        data = np.zeros((3, 1, 1), dtype=np.uint8)
        data[0, 0, 0] = 76      # Y
        data[1, 0, 0] = 85      # Cb
        data[2, 0, 0] = 255     # Cr
        rgb = _ycbcr_to_rgb(data)
        self.assertEqual(rgb.shape, data.shape)
        self.assertGreater(int(rgb[0, 0, 0]), 200, f"红色 R 分量异常：{rgb[:, 0, 0]}")
        self.assertLess(int(rgb[1, 0, 0]), 60, f"红色 G 分量异常：{rgb[:, 0, 0]}")
        self.assertLess(int(rgb[2, 0, 0]), 60, f"红色 B 分量异常：{rgb[:, 0, 0]}")

    def test_parse_and_render_does_not_crash(self):
        registry.load_plugins()
        from io import BytesIO
        from PIL import Image
        doc = TiffParser().parse(self.path, ParseContext())
        self.assertEqual(doc.meta.get("engine"), "rasterio")
        self.assertIn("jpeg", str(doc.meta.get("compression")))
        png = TiffParser().render(doc, 0, "orig")
        self.assertTrue(png)
        with Image.open(BytesIO(png)) as im:
            self.assertEqual(im.mode, "RGB")

if __name__ == "__main__":
    unittest.main(verbosity=2)

