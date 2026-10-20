# -*- coding: utf-8 -*-
"""P0 修复：二进制 PLY 几何损坏回归测试（ascii / binary_little_endian / binary_big_endian）。

背景
----
旧实现把命中的 PLY header comment 替换成变长文本（`█` 是 3 字节），二进制 PLY
数据段整体错位；`pipeline.verify` 只重读 header 文本，仍报 verified=True。
本测试锁定：
* 二进制 PLY header 命中文本必须删除/等长处理，数据段字节长度与面数据不偏移；
* 三种 PLY 子格式导出后能被独立重读，顶点重定位正确、面数据一致；
* parser 导出后结构自检能拦截“数据段不一致/源文件已损坏”，不再降级复制源文件；
* 成品 header 不再含被识别出的敏感文本。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_mesh.py

夹具为虚构（PII 全假），默认生成到临时目录；设置 ``YINZHAN_WRITE_FIXTURES=1``
可写到 ``app/tests/fixtures/mesh/``（二进制夹具不入库）。
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
import zipfile  # noqa: F401  (保持与其它测试一致的导入习惯)
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import numpy as np                                          # noqa: E402
from PIL import Image  # noqa: F401,E402  (部分辅助断言用)

from yinzhan import pipeline, registry                      # noqa: E402
from yinzhan.jobs import _load_rules                        # noqa: E402
from yinzhan.ocr import get_ocr                             # noqa: E402
from yinzhan.parsers import mesh_parser as mesh_mod         # noqa: E402
from yinzhan.udm import (                                   # noqa: E402
    ExportError, ParseContext,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "mesh"
SECRET_PHONE = "13800138000"
SECRET_LINE = f"comment 项目负责人 张伟 联系电话 {SECRET_PHONE} 保密"
#: 大坐标（模拟工程坐标）用于验证顶点重定位
VERTS = [
    (10000.0, 20000.0, 30000.0), (10002.0, 20000.0, 30000.0),
    (10002.0, 20002.0, 30000.0), (10000.0, 20002.0, 30000.0),
    (10000.0, 20000.0, 30002.0), (10002.0, 20000.0, 30002.0),
    (10002.0, 20002.0, 30002.0), (10000.0, 20002.0, 30002.0),
]
FACES = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
         (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]


# ────────────────────────── 夹具 ──────────────────────────

def _fixtures_root() -> Path:
    if os.environ.get("YINZHAN_WRITE_FIXTURES") == "1":
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        return FIXTURE_DIR
    return Path(tempfile.mkdtemp(prefix="yinzhan_mesh_"))


def _ply_header(fmt: str) -> str:
    return (
        "ply\n"
        f"format {fmt} 1.0\n"
        f"{SECRET_LINE}\n"
        "obj_info 单位 米\n"
        f"element vertex {len(VERTS)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        f"element face {len(FACES)}\n"
        "property list uchar int vertex_indices\n"
        "end_header\n"
    )


def _write_ply_ascii(path: Path) -> Path:
    lines = [_ply_header("ascii")]
    lines.extend(f"{v[0]} {v[1]} {v[2]}\n" for v in VERTS)
    lines.extend(f"3 {f[0]} {f[1]} {f[2]}\n" for f in FACES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _write_ply_binary(path: Path, endian: str) -> Path:
    fmt = "binary_little_endian" if endian == "<" else "binary_big_endian"
    chunks = [_ply_header(fmt).encode("utf-8")]
    chunks.extend(struct.pack(endian + "fff", *v) for v in VERTS)
    chunks.extend(struct.pack(endian + "Biii", 3, *f) for f in FACES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(chunks))
    return path


def _write_ply_truncated(path: Path) -> Path:
    """二进制 PLY：header 声明 8 顶点，数据只给 2 顶点（源文件已损坏）。"""
    fmt = "binary_little_endian"
    chunks = [_ply_header(fmt).encode("utf-8")]
    chunks.extend(struct.pack("<fff", *v) for v in VERTS[:2])
    chunks.append(struct.pack("<Biii", 3, *FACES[0]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(chunks))
    return path


class Fixtures:
    root: Path | None = None

    @classmethod
    def get(cls) -> Path:
        if cls.root is None:
            cls.root = _fixtures_root()
        return cls.root

    @classmethod
    def ply_ascii(cls) -> Path:
        path = cls.get() / "secret_ascii.ply"
        if not path.exists():
            _write_ply_ascii(path)
        return path

    @classmethod
    def ply_le(cls) -> Path:
        path = cls.get() / "secret_le.ply"
        if not path.exists():
            _write_ply_binary(path, "<")
        return path

    @classmethod
    def ply_be(cls) -> Path:
        path = cls.get() / "secret_be.ply"
        if not path.exists():
            _write_ply_binary(path, ">")
        return path

    @classmethod
    def ply_truncated(cls) -> Path:
        path = cls.get() / "truncated_le.ply"
        if not path.exists():
            _write_ply_truncated(path)
        return path


# ────────────────────────── 独立 PLY 读取（不借道被测导出代码） ──────────────────────────

def _read_ply(path: Path) -> dict:
    header = mesh_mod._read_ply_header(path)
    data = path.read_bytes()
    fmt = str(header["format"])
    tail = data[int(header["data_offset"]):]
    if fmt == "ascii":
        lines = tail.decode("utf-8", "replace").splitlines()
        vertex_count = int(mesh_mod._ply_vertex_element(header)["count"])
        verts = [tuple(float(x) for x in line.split()[:3])
                 for line in lines[:vertex_count]]
        faces = []
        for line in lines[vertex_count:]:
            parts = line.split()
            if parts:
                faces.append(tuple(int(x) for x in parts[1:]))
        return {"format": fmt, "header": header, "data": tail,
                "verts": verts, "faces": faces}
    endian = "<" if "little" in fmt else ">"
    layout = mesh_mod._ply_binary_vertex_layout(header, endian)
    start, count, dtype = layout
    verts = [struct.unpack_from(endian + "fff", tail,
                                start + index * dtype.itemsize)
             for index in range(count)]
    pos = start + count * dtype.itemsize
    faces = []
    for _index in range(len(FACES)):
        if pos >= len(tail):
            break
        n = tail[pos]
        pos += 1
        idx = struct.unpack_from(endian + "i" * n, tail, pos)
        pos += 4 * n
        faces.append(tuple(int(x) for x in idx[:n]))
    return {"format": fmt, "header": header, "data": tail,
            "verts": verts, "faces": faces}


def _header_text(path: Path) -> str:
    header = mesh_mod._read_ply_header(path)
    return b"".join(header["raw_lines"]).decode("utf-8", "replace")


def _ctx() -> ParseContext:
    return ParseContext(ocr=get_ocr(), workdir=str(Fixtures.get()))


def _parser(path: Path):
    registry.load_plugins()
    return registry.find_parser(path)


def _detect(doc):
    rules, _pack, _index = _load_rules(doc.fmt)
    det = pipeline.detect(doc, rules, "standard")
    pipeline.decide(det.entities, det.rules, pipeline.Session())
    return det


def _source_center(verts: list[tuple]) -> list[float]:
    return [sum(v[axis] for v in verts) / len(verts) for axis in range(3)]


def _export(path: Path, parser=None):
    parser = parser or _parser(path)
    doc = parser.parse(path, _ctx())
    det = _detect(doc)
    values = [entity.value for entity in det.entities]
    out = Fixtures.get() / (path.stem + "_out.ply")
    res = pipeline.export(doc, parser, det.entities, out, {"mode": "human"})
    return doc, res, out, values


# ────────────────────────── 测试 ──────────────────────────

class TestPlyThreeFormats(unittest.TestCase):
    """三种 PLY 子格式：导出后独立重读，顶点/面一致且不损坏。"""

    @classmethod
    def setUpClass(cls):
        registry.load_plugins()

    def _case(self, suffix: str, path: Path):
        with self.subTest(fmt=suffix):
            source = _read_ply(path)
            src_header_len = int(source["header"]["data_offset"])
            doc, res, out, _values = _export(path)
            self.assertTrue(out.is_file(), f"{suffix} 没有导出成品")
            self.assertIn(SECRET_PHONE, " ".join(
                block.text for block in doc.pages[0].blocks),
                "解析阶段应能从 comment 读到虚构手机号")
            output = _read_ply(out)
            out_header_len = int(output["header"]["data_offset"])
            # 数据段长度不变（二进制是硬约束；ASCII 允许行文本变短，但面数据必须一致）
            if source["format"].startswith("binary"):
                self.assertEqual(len(source["data"]), len(output["data"]),
                                 f"{suffix} 数据段长度变化（几何错位）")
                self.assertNotEqual(src_header_len, out_header_len,
                                    "敏感 comment 应被删除，header 长度应变化")
            else:
                # ASCII 行式数据长度可变化；关键是顶点/面数据仍可完整重读
                self.assertGreater(len(output["data"]), 0, "ASCII 数据段为空")
            self.assertEqual(output["faces"], [tuple(f) for f in FACES],
                             f"{suffix} 面数据不一致")
            self.assertEqual(len(output["verts"]), len(VERTS),
                             f"{suffix} 顶点数变化")
            center = _source_center(source["verts"])
            for src_v, out_v in zip(source["verts"], output["verts"]):
                expected = tuple(src_v[i] - center[i] for i in range(3))
                for axis in range(3):
                    self.assertAlmostEqual(out_v[axis], expected[axis], places=4,
                                           msg=f"{suffix} 顶点未正确重定位")
            # header 不再含被规则命中的手机号；obj_info 也保留（未命中不删）
            self.assertNotIn(SECRET_PHONE, _header_text(out))
            self.assertIn("obj_info 单位 米", _header_text(out))
            # 成品可被 parser 重新解析
            reparsed = _parser(out).parse(out, _ctx())
            self.assertEqual(reparsed.meta.get("vertices"), len(VERTS))
            self.assertTrue(any("PLY" in w and "复检" in w for w in res.warnings)
                            or any("ASCII 重写" in w or "二进制" in w for w in res.warnings),
                            f"{suffix} 缺少导出/复检 warning：{res.warnings}")

    def test_ascii(self):
        self._case("ascii", Fixtures.ply_ascii())

    def test_binary_little_endian(self):
        self._case("le", Fixtures.ply_le())

    def test_binary_big_endian(self):
        self._case("be", Fixtures.ply_be())

    def test_output_headers_have_no_secret_for_all(self):
        for path in (Fixtures.ply_ascii(), Fixtures.ply_le(), Fixtures.ply_be()):
            with self.subTest(path=path.name):
                _doc, _res, out, _values = _export(path)
                self.assertNotIn(SECRET_PHONE, _header_text(out))


class TestPlySelfCheck(unittest.TestCase):
    """解析器侧结构自检：损坏数据不能静默交付。"""

    @classmethod
    def setUpClass(cls):
        registry.load_plugins()

    def test_truncated_binary_is_blocked(self):
        parser = _parser(Fixtures.ply_truncated())
        doc = parser.parse(Fixtures.ply_truncated(), _ctx())
        with self.assertRaises(ExportError) as caught:
            pipeline.export(doc, parser, [], Fixtures.get() / "truncated_out.ply",
                            {"mode": "human"})
        text = str(caught.exception)
        self.assertIn("PLY", text)
        self.assertTrue("损坏" in text or "数据段" in text or "复检" in text, text)
        self.assertFalse((Fixtures.get() / "truncated_out.ply").exists(),
                         "被自检拦截时不应留下成品")

    def test_binary_data_mismatch_detected(self):
        _doc, _res, out, _values = _export(Fixtures.ply_le())
        source = Fixtures.ply_le()
        header = mesh_mod._read_ply_header(source)
        header_bytes, _hits = mesh_mod._ply_build_header(header, [], binary=True)
        # 复制成品并篡改面数据一个字节 → 自检必须报“数据段不一致”
        corrupted = Fixtures.get() / "corrupted_faces.ply"
        data = bytearray(out.read_bytes())
        data[-1] = (data[-1] + 1) % 256
        corrupted.write_bytes(data)
        with self.assertRaises(ExportError) as caught:
            mesh_mod._ply_verify_output(source, corrupted, header, header_bytes,
                                        "<", center=None, warnings=[])
        self.assertIn("PLY 成品复检失败", str(caught.exception))
        self.assertFalse(corrupted.exists(), "自检失败应删除损坏成品")


class TestPlyNoSecretLeak(unittest.TestCase):
    """导出失败也不能把带敏感注释的源文件原样复制出去。"""

    def test_export_blocks_instead_of_copy_on_failure(self):
        parser = _parser(Fixtures.ply_truncated())
        doc = parser.parse(Fixtures.ply_truncated(), _ctx())
        out = Fixtures.get() / "truncated_copy_guard.ply"
        with self.assertRaises(ExportError):
            pipeline.export(doc, parser, [], out, {"mode": "human"})
        self.assertFalse(out.exists())


# ────────────────────────── main ──────────────────────────

def main() -> int:
    print("数据隐站 · PLY 二进制完整性 P0 回归")
    print("-" * 64)
    print(f"fixtures={FIXTURE_DIR if os.environ.get('YINZHAN_WRITE_FIXTURES') == '1' else 'tempdir'}")
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
