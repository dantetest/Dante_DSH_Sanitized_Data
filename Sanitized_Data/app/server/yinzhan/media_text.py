# -*- coding: utf-8 -*-
"""内嵌图片「纯文字规则」脱敏 helper（P1.5）。

Office/PDF 解析器在做完视觉/码类扫描后，可以调用本模块把图片里 OCR 出来的
姓名/身份证/手机/地址/邮箱/金额等**文字类敏感信息**按规则检测并涂黑，再返回
重编码后的图片字节（不携带 EXIF/ICC）。

约束：
- 只处理位图（PNG/JPEG/BMP/WEBP）；EMF/WMF/SVG 等由调用方计入 unsupported；
- 只做「破坏性黑色覆盖」，不换名/不生成假值；
- 单图处理有像素预算，超预算返回 None（调用方保留原图 + warning）；
- 运行时导入 `parsers.image_parser` 与 `pipeline`，避免模块级循环依赖。
"""
from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from . import pipeline, rules as rules_mod, registry
from .ocr import get_ocr
from .udm import Decision, ParseContext

MAX_PIXELS = 12_000_000
MAX_ENTITIES = 500
SUPPORTED_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def _safe_temp_dir(workdir: Path | None) -> Path:
    """建一个确定可写的唯一临时目录（用完即删）。

    不用 ``tempfile.mkdtemp``：部分受限环境（DSH Windows 沙箱 / 杀软 /
    组策略）里按 0o700 创建的目录会被 ACL 拒绝写入，导致 OCR 静默跳过；
    这里用默认权限建唯一子目录，并依次尝试调用方 workdir、系统 TEMP、
    产品数据目录。
    """
    candidates: list[Path] = []
    if workdir is not None:
        candidates.append(Path(workdir))
    try:
        candidates.append(Path(tempfile.gettempdir()))
    except Exception:                                  # noqa: BLE001
        pass
    try:
        from . import paths as _paths
        candidates.append(Path(_paths.data_root()) / "work")
    except Exception:                                  # noqa: BLE001
        pass
    last_exc: OSError | None = None
    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            tmp = base / f"yz_media_text_{uuid.uuid4().hex[:12]}"
            tmp.mkdir()
            probe = tmp / ".write_probe"
            probe.write_bytes(b"x")
            probe.unlink()
            return tmp
        except OSError as exc:
            last_exc = exc
            continue
    raise OSError(f"内嵌图文字 OCR 临时目录不可写：{last_exc}")


def redact_image_text(data: bytes, suffix: str = ".png",
                      workdir: Path | None = None) -> tuple[bytes | None, int, list[str]]:
    """对单张图片做 OCR + 规则检测 + 黑色覆盖。

    返回 ``(new_bytes|None, entity_count, warnings)``；没有命中返回
    ``(None, 0, [])``，由调用方决定保留原图。
    """
    suffix = (suffix or ".png").lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return None, 0, [f"内嵌图片文字 OCR 跳过（不支持格式 {suffix}）"]

    from PIL import Image

    warnings: list[str] = []
    try:
        with Image.open(__import__("io").BytesIO(data)) as im:
            if im.width * im.height > MAX_PIXELS:
                return None, 0, [f"内嵌图片过大（{im.width}x{im.height}），文字 OCR 跳过"]
    except Exception as exc:
        return None, 0, [f"内嵌图片无法解码：{exc}"]

    try:
        from .parsers.image_parser import ImageParser
    except Exception as exc:
        return None, 0, [f"内嵌图文字 OCR 不可用：{exc}"]

    try:
        tmp = _safe_temp_dir(workdir)
    except OSError as exc:
        return None, 0, warnings + [f"内嵌图片文字 OCR 临时目录不可用：{exc}"]
    src = tmp / f"embedded{suffix}"
    out = tmp / f"embedded_redacted{suffix}"
    try:
        src.write_bytes(data)
        parser = ImageParser()
        ctx = ParseContext(ocr=get_ocr(), workdir=str(tmp))
        doc = parser.parse(src, ctx)
        if not any(page.blocks for page in doc.pages):
            return None, 0, warnings
        packs = rules_mod.load_packs()
        compiled = rules_mod.compile_rules(packs, "image")
        det = pipeline.detect(doc, compiled, "standard")
        entities = [e for e in det.entities if e.value and len(e.value.strip()) >= 2]
        if not entities:
            return None, 0, warnings
        if len(entities) > MAX_ENTITIES:
            entities = entities[:MAX_ENTITIES]
            warnings.append(f"内嵌图片命中过多，仅处理前 {MAX_ENTITIES} 处")
        session = pipeline.Session()
        pipeline.decide(entities, det.rules, session)
        for ent in entities:
            ent.status = "accepted"
            ent.decision = Decision(strategy="black", black=True, replacement=None)
        pipeline.export(doc, parser, entities, out, {"mode": "human"})
        if not out.is_file():
            return None, 0, warnings + ["内嵌图片文字脱敏导出失败"]
        return out.read_bytes(), len(entities), warnings
    except Exception as exc:
        return None, 0, warnings + [f"内嵌图片文字 OCR 失败：{exc}"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
