# -*- coding: utf-8 -*-
"""生成 P1-B「PDF 文本层内嵌图片」测试夹具（全部虚构，可提交）。

产物（本目录）：
- `sensitive.pdf`     文本层 + 内嵌二维码（≈6.5% 页）+ 虚构证件图（≈8.5%）+ 正常风景图（≈12%）
- `normal_only.pdf`   文本层 + 正常风景图（无敏感目标，负样本）
- `tiny_qr_only.pdf`  文本层 + 小二维码（≈1.4% 页，低于 5% 门槛，应跳过）
- `text_only.pdf`     纯文本层，无图片（性能/回归基线）
- `scanned_qr.pdf`    无文本层、整页二维码图片（扫描页：不走内嵌图片分支）
- `qr.png` / `landscape.png`  合成素材（二维码由 zxing-cpp 生成，风景由 PIL 绘制）
- `manifest.json`     页面尺寸与内嵌图片期望矩形（PDF 页坐标）

安全约定：文本/公司/证件均为虚构（证件图复用
`app/tests/fixtures/pipl_public/synthetic_id_card_front.png`，全虚构带水印）。
用法：python -X utf8 app/tests/fixtures/pdf_embedded/generate_pdfs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[4]
PIPL_ID = REPO / "app" / "tests" / "fixtures" / "pipl_public" / "synthetic_id_card_front.png"
PAGE_W, PAGE_H = 595.0, 842.0


def make_qr_png() -> Path:
    try:
        import zxingcpp

        barcode = zxingcpp.create_barcode(
            "01,10,044031900111,12345678,1234.56,20261019,ABC,",
            zxingcpp.BarcodeFormat.QRCode, ec_level="30%")
        img = np.asarray(barcode.to_image(scale=6))
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    except Exception as exc:  # pragma: no cover - 依赖缺失时给明确提示
        raise SystemExit(f"需要 zxing-cpp 生成二维码 fixture：{exc}")
    path = HERE / "qr.png"
    cv2.imwrite(str(path), img)
    return path


def make_landscape_png() -> Path:
    W, H = 600, 400
    img = Image.new("RGB", (W, H), (135, 190, 235))
    draw = ImageDraw.Draw(img)
    draw.polygon([(0, H), (0, 260), (180, 150), (320, 250), (460, 170), (W, 240), (W, H)],
                 fill=(70, 110, 80))
    draw.ellipse([430, 40, 520, 130], fill=(250, 230, 150))
    path = HERE / "landscape.png"
    img.save(path)
    return path


def _text_page(pdf: fitz.Document) -> fitz.Page:
    page = pdf.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_text((60, 80), "示例咨询合同（虚构样例，文本层）",
                     fontname="china-s", fontsize=16)
    page.insert_text((60, 112), "甲方：北京示例科技有限公司", fontname="china-s", fontsize=11)
    page.insert_text((60, 138), "乙方：上海示例咨询有限公司", fontname="china-s", fontsize=11)
    page.insert_text((60, 164), "合同金额：人民币壹拾贰万元整（120000.00）",
                     fontname="china-s", fontsize=11)
    return page


def build_sensitive(qr_png: Path, landscape_png: Path) -> dict:
    pdf = fitz.open()
    page = _text_page(pdf)
    qr_rect = fitz.Rect(60, 220, 240, 400)          # 6.5% 页
    id_rect = fitz.Rect(280, 220, 540, 387)         # 8.5% 页
    img_rect = fitz.Rect(60, 460, 360, 660)         # 12% 页（正常配图）
    page.insert_image(qr_rect, filename=str(qr_png))
    page.insert_image(id_rect, filename=str(PIPL_ID))
    page.insert_image(img_rect, filename=str(landscape_png))
    path = HERE / "sensitive.pdf"
    pdf.save(str(path), garbage=3, deflate=True)
    pdf.close()
    return {
        "file": "sensitive.pdf",
        "page_size": [PAGE_W, PAGE_H],
        "sensitive": [
            {"kind": "qrcode", "rect": list(qr_rect), "area_ratio": 0.065},
            {"kind": "id_document", "rect": list(id_rect), "area_ratio": 0.085},
        ],
        "normal": [{"rect": list(img_rect), "area_ratio": 0.120}],
    }


def build_normal_only(landscape_png: Path) -> dict:
    pdf = fitz.open()
    page = _text_page(pdf)
    img_rect = fitz.Rect(60, 220, 360, 420)
    page.insert_image(img_rect, filename=str(landscape_png))
    path = HERE / "normal_only.pdf"
    pdf.save(str(path), garbage=3, deflate=True)
    pdf.close()
    return {"file": "normal_only.pdf", "image_rect": list(img_rect)}


def build_tiny_qr(qr_png: Path) -> dict:
    pdf = fitz.open()
    page = _text_page(pdf)
    rect = fitz.Rect(60, 220, 130, 290)             # ≈1.4% 页，低于 5% 门槛
    page.insert_image(rect, filename=str(qr_png))
    path = HERE / "tiny_qr_only.pdf"
    pdf.save(str(path), garbage=3, deflate=True)
    pdf.close()
    return {"file": "tiny_qr_only.pdf", "image_rect": list(rect)}


def build_text_only() -> dict:
    pdf = fitz.open()
    _text_page(pdf)
    path = HERE / "text_only.pdf"
    pdf.save(str(path), garbage=3, deflate=True)
    pdf.close()
    return {"file": "text_only.pdf"}


def build_scanned_qr(qr_png: Path) -> dict:
    pdf = fitz.open()
    page = pdf.new_page(width=PAGE_W, height=PAGE_H)
    rect = fitz.Rect(120, 120, 480, 480)
    page.insert_image(rect, filename=str(qr_png))   # 无文本层 → 扫描页
    path = HERE / "scanned_qr.pdf"
    pdf.save(str(path), garbage=3, deflate=True)
    pdf.close()
    return {"file": "scanned_qr.pdf", "image_rect": list(rect)}



def _valid_id_number(first17: str) -> str:
    """生成带合法校验位的虚构 18 位身份证号（规则 cn.idcard 会校验）。"""
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    mapping = "10X98765432"
    total = sum(int(ch) * w for ch, w in zip(first17, weights))
    return first17 + mapping[total % 11]


def make_text_image_png() -> Path:
    """P1.5 纯文字图片：ASCII 手机号/邮箱/身份证号（虚构，可被规则检测）。"""
    from PIL import ImageFont

    font_candidates = (r"C:\\Windows\\Fonts\\msyh.ttc", r"C:\\Windows\\Fonts\\arial.ttf",
                       r"C:\\Windows\\Fonts\\simsun.ttc",
                       "/System/Library/Fonts/PingFang.ttc",
                       "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    font = None
    for candidate in font_candidates:
        try:
            if Path(candidate).is_file():
                font = ImageFont.truetype(candidate, 34)
                break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    img = Image.new("RGB", (760, 480), (250, 250, 248))
    draw = ImageDraw.Draw(img)
    fake_id = _valid_id_number("11010119900307447")
    lines = [
        "Contact Info (fictional)",
        "Phone: 13800138000",
        "Email: test@example.com",
        f"ID: {fake_id}",
        "Address: Example Road 88",
    ]
    y = 40
    for index, line in enumerate(lines):
        draw.text((40, y), line, font=font, fill=(20, 20, 20))
        y += 84
    path = HERE / "text_img.png"
    img.save(path)
    return path


def build_text_sensitive(text_png: Path) -> dict:
    """文本层 + 内嵌纯文字敏感图片（手机/邮箱/身份证号）。"""
    pdf = fitz.open()
    page = _text_page(pdf)
    rect = fitz.Rect(60, 220, 420, 447)      # ≈16.3% 页
    page.insert_image(rect, filename=str(text_png))
    path = HERE / "text_sensitive.pdf"
    pdf.save(str(path), garbage=3, deflate=True)
    pdf.close()
    return {"file": "text_sensitive.pdf", "image_rect": list(rect),
            "expected_text_min": 2, "expected_id": _valid_id_number("11010119900307447")}


def main() -> int:
    HERE.mkdir(parents=True, exist_ok=True)
    qr_png = make_qr_png()
    landscape_png = make_landscape_png()
    text_png = make_text_image_png()
    manifest = {
        "generator": "app/tests/fixtures/pdf_embedded/generate_pdfs.py",
        "note": "全部虚构；图片为 PIL/zxing-cpp 合成，PDF 用 PyMuPDF 生成，可提交。",
        "cases": {
            "sensitive": build_sensitive(qr_png, landscape_png),
            "normal_only": build_normal_only(landscape_png),
            "tiny_qr_only": build_tiny_qr(qr_png),
            "text_only": build_text_only(),
            "scanned_qr": build_scanned_qr(qr_png),
            "text_sensitive": build_text_sensitive(text_png),
        },
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成 PDF 内嵌图片 fixtures：{HERE}")
    for name, spec in manifest["cases"].items():
        print(f"  {name}: {spec['file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
