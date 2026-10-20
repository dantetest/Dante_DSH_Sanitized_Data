# -*- coding: utf-8 -*-
"""生成 PIPL 合成测试夹具（全部虚构数据，可提交）。

生成到本目录：
- synthetic_id_card_front.png / synthetic_id_card_back.png   虚构身份证正/反面
- synthetic_plate.png                                        虚构车牌
- synthetic_chat.png                                        虚构聊天截图
- synthetic_door_sign.png / synthetic_road_sign.png          虚构门牌/路牌
- synthetic_invoice.png                                     虚构发票截图（含 QR/条码，依赖 zxing-cpp）
- synthetic_table.xlsx / synthetic_table.csv                 虚构 PII 表格

安全约定：
- 姓名/号码/地址/公司/卡号全部虚构，图上叠加「虚构样例」水印；
- 身份证号使用**合法校验位**但属于虚构序列（便于测试校验器，不对应真实个人）；
- 不含任何真实人像、真实证件照片；
- 可用 ``python -X utf8 app/tests/fixtures/pipl_public/generate_synthetic.py`` 重复生成
  （输出确定，仅时间戳不写盘）。

注意：本目录的 PNG/XLSX/CSV 均为**测试夹具**，不参与产品打包。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)
WATERMARK = "虚构样例 · 仅供数据隐站脱敏功能测试"

# ── 虚构数据（不对应真实个人/单位） ──
FAKE_NAME = "张三"
FAKE_NAME2 = "李四"
FAKE_PHONE = "13800138000"
FAKE_PHONE2 = "13900139000"
FAKE_ADDRESS = "北京市朝阳区示例路 1 号院 2 单元 301（虚构）"
FAKE_COMPANY = "北京示例科技有限公司（虚构）"
FAKE_BANK = "6222020200112233445"
FAKE_EMAIL = "zhangsan@example.com"
FAKE_AMOUNT = "12345.67"


def _font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _watermark(draw: ImageDraw.ImageDraw, size: tuple[int, int], size_px: int = 26,
               spacing: int = 240, color: tuple[int, int, int] = (222, 222, 222)) -> None:
    """稀疏水印：间隔大、颜色浅，避免干扰 OCR/条码检测（合成夹具要能测引擎）。"""
    font = _font(size_px)
    text = WATERMARK
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
    except Exception:  # noqa: BLE001
        tw = len(text) * size_px
    step_x = max(tw + 120, 420)
    for y in range(size[1] - 120, 10, -spacing):
        for x in range(-40, size[0], step_x):
            draw.text((x, y), text, font=font, fill=color)


def _watermark_corner(draw: ImageDraw.ImageDraw, size: tuple[int, int],
                      size_px: int = 24) -> None:
    """只在左下/右上角放一行小字，不覆盖主体（用于车牌/门牌/路牌）。"""
    draw.text((14, size[1] - size_px - 16), WATERMARK, font=_font(size_px),
              fill=(235, 235, 235))
    try:
        bbox = draw.textbbox((0, 0), WATERMARK, font=_font(size_px))
        tw = bbox[2] - bbox[0]
    except Exception:  # noqa: BLE001
        tw = len(WATERMARK) * size_px
    draw.text((size[0] - tw - 14, 8), WATERMARK, font=_font(size_px),
              fill=(235, 235, 235))


def _id_checksum(base17: str) -> str:
    """GB 11643 校验位（虚构号码也保持校验位合法，便于测试校验器）。"""
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    codes = "10X98765432"
    total = sum(int(ch) * w for ch, w in zip(base17, weights))
    return codes[total % 11]


FAKE_ID_BASE17 = "11010119900101001"          # 虚构：北京示例 + 1990-01-01 + 顺序码
FAKE_ID = FAKE_ID_BASE17 + _id_checksum(FAKE_ID_BASE17)
FAKE_ID2_BASE17 = "11010119920202002"
FAKE_ID2 = FAKE_ID2_BASE17 + _id_checksum(FAKE_ID2_BASE17)


def make_id_card_front(path: Path) -> None:
    width, height = 1011, 638
    img = Image.new("RGB", (width, height), (236, 241, 246))
    draw = ImageDraw.Draw(img)
    # 底色/边框
    draw.rectangle([0, 0, width - 1, height - 1], outline=(120, 150, 180), width=6)
    draw.text((40, 34), "中华人民共和国", font=_font(30), fill=(40, 70, 120))
    draw.text((40, 78), "居民身份证（虚构样例）", font=_font(44), fill=(30, 50, 90))
    # 虚构人像占位（灰色圆角矩形，不是真人照片）
    draw.rounded_rectangle([700, 170, 950, 540], radius=24,
                           fill=(200, 205, 210), outline=(160, 165, 170), width=3)
    draw.text((760, 330), "虚构头像位", font=_font(28), fill=(130, 135, 140))
    rows = [
        ("姓    名", FAKE_NAME),
        ("性    别", "男"),
        ("民    族", "汉"),
        ("出    生", "1990 年 1 月 1 日"),
        ("住    址", FAKE_ADDRESS),
        ("公民身份号码", FAKE_ID),
    ]
    y = 180
    for label, value in rows:
        draw.text((56, y), label, font=_font(30), fill=(60, 70, 80))
        draw.text((300, y), value, font=_font(32), fill=(20, 30, 40))
        y += 64
    _watermark(draw, (width, height), size_px=20, spacing=260)
    img.save(path)


def make_id_card_back(path: Path) -> None:
    width, height = 1011, 638
    img = Image.new("RGB", (width, height), (236, 241, 246))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(120, 150, 180), width=6)
    draw.text((40, 34), "中华人民共和国", font=_font(30), fill=(40, 70, 120))
    draw.ellipse([430, 90, 590, 250], outline=(180, 40, 40), width=6)
    draw.text((455, 150), "虚构国徽", font=_font(26), fill=(180, 40, 40))
    draw.text((66, 300), "签发机关", font=_font(30), fill=(60, 70, 80))
    draw.text((260, 300), "北京市公安局示例分局（虚构）", font=_font(32), fill=(20, 30, 40))
    draw.text((66, 390), "有效期限", font=_font(30), fill=(60, 70, 80))
    draw.text((260, 390), "2020.01.01 - 2040.01.01", font=_font(32), fill=(20, 30, 40))
    _watermark(draw, (width, height), size_px=20, spacing=300)
    img.save(path)


def make_plate(path: Path) -> None:
    width, height = 880, 280
    img = Image.new("RGB", (width, height), (30, 90, 200))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, width - 11, height - 11], outline=(240, 240, 240), width=8)
    draw.text((70, 40), "京A·12345", font=_font(150), fill=(245, 245, 245))
    draw.text((40, 226), "虚构车牌 · 仅供测试", font=_font(28), fill=(220, 230, 250))
    _watermark_corner(draw, (width, height), 20)
    img.save(path)


def make_chat(path: Path) -> None:
    width, height = 720, 1100
    img = Image.new("RGB", (width, height), (237, 237, 237))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width, 96], fill=(245, 245, 245))
    draw.text((24, 30), "示例聊群（虚构）", font=_font(34), fill=(30, 30, 30))
    messages = [
        ("left", "张三", f"我的手机号 {FAKE_PHONE}，身份证 {FAKE_ID}"),
        ("right", "李四", f"收到，银行卡 {FAKE_BANK} 对吗？"),
        ("left", "张三", f"对，地址是 {FAKE_ADDRESS}"),
        ("right", "李四", f"合同金额 ¥{FAKE_AMOUNT}，邮箱 {FAKE_EMAIL}"),
        ("left", "张三", "发票二维码在附件里，麻烦核对一下税号。"),
    ]
    y = 130
    for side, name, text in messages:
        bubble_w = 560
        lines = [text[i:i + 20] for i in range(0, len(text), 20)] or [""]
        bubble_h = 46 + 34 * len(lines)
        x0 = 24 if side == "left" else width - bubble_w - 24
        fill = (255, 255, 255) if side == "left" else (149, 236, 105)
        draw.rounded_rectangle([x0, y, x0 + bubble_w, y + bubble_h], radius=14, fill=fill)
        draw.text((x0 + 14, y + 8), f"{name}（虚构）", font=_font(22), fill=(110, 110, 110))
        for idx, line in enumerate(lines):
            draw.text((x0 + 14, y + 38 + idx * 34), line, font=_font(26), fill=(20, 20, 20))
        y += bubble_h + 26
    _watermark_corner(draw, (width, height), 18)
    img.save(path)


def make_door_sign(path: Path) -> None:
    width, height = 900, 600
    img = Image.new("RGB", (width, height), (60, 62, 66))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, width - 40, height - 40], fill=(80, 82, 86),
                   outline=(200, 200, 200), width=4)
    draw.text((120, 110), "示例路 88 号", font=_font(96), fill=(250, 250, 250))
    draw.text((150, 280), "北京示例科技有限公司（虚构）", font=_font(50), fill=(230, 230, 230))
    _watermark_corner(draw, (width, height), 22)
    img.save(path)


def make_road_sign(path: Path) -> None:
    width, height = 1200, 420
    img = Image.new("RGB", (width, height), (0, 120, 70))
    draw = ImageDraw.Draw(img)
    draw.rectangle([12, 12, width - 13, height - 13], outline=(250, 250, 250), width=8)
    draw.text((90, 70), "朝阳区  示例大道", font=_font(96), fill=(250, 250, 250))
    draw.text((90, 240), "东行  2 km", font=_font(60), fill=(240, 240, 240))
    _watermark_corner(draw, (width, height), 20)
    img.save(path)


def _qr_gray(text: str, scale: int = 8):
    try:
        import cv2
        import zxingcpp

        barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode,
                                          ec_level="30%")
        img = __import__("numpy").asarray(barcode.to_image(scale=scale))
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img
    except Exception:  # noqa: BLE001
        return None


def make_invoice(path: Path) -> None:
    width, height = 1240, 1750
    img = Image.new("RGB", (width, height), (250, 248, 242))
    draw = ImageDraw.Draw(img)
    draw.rectangle([30, 30, width - 31, height - 31], outline=(180, 60, 60), width=5)
    draw.text((360, 60), "增值税电子普通发票（虚构样例）", font=_font(52), fill=(160, 40, 40))
    rows = [
        ("发票代码", "044031900111"),
        ("发票号码", "12345678"),
        ("开票日期", "2026 年 10 月 19 日"),
        ("校验码", "12345 67890 12345 67890"),
        ("购买方名称", FAKE_COMPANY),
        ("纳税人识别号", "91110105MA00EXAMPLE"),
        ("价税合计", f"¥{FAKE_AMOUNT}"),
    ]
    y = 200
    for label, value in rows:
        draw.text((90, y), label, font=_font(34), fill=(70, 70, 70))
        draw.text((400, y), value, font=_font(34), fill=(20, 20, 20))
        y += 66
    draw.text((90, 760), "销售方名称", font=_font(34), fill=(70, 70, 70))
    draw.text((400, 760), "上海示例商贸有限公司（虚构）", font=_font(34), fill=(20, 20, 20))
    draw.text((90, 826), "纳税人识别号", font=_font(34), fill=(70, 70, 70))
    draw.text((400, 826), "91310000MA00EXAMPLE", font=_font(34), fill=(20, 20, 20))
    qr = _qr_gray("01,10,044031900111,12345678,12345.67,20261019,12345678901234567890,ABC,")
    if qr is not None:
        qimg = Image.fromarray(qr).convert("RGB")
        img.paste(qimg, (820, 120))
    _watermark(draw, (width, height), size_px=20, spacing=260)
    img.save(path)


def make_table():
    rows = [
        ["姓名", "身份证号", "手机号", "银行卡号", "邮箱", "地址", "金额"],
        [FAKE_NAME, FAKE_ID, FAKE_PHONE, FAKE_BANK, FAKE_EMAIL, FAKE_ADDRESS, FAKE_AMOUNT],
        [FAKE_NAME2, FAKE_ID2, FAKE_PHONE2, "6222020200112233446",
         "lisi@example.com", "上海市浦东新区示例路 2 号（虚构）", "6789.00"],
        ["王五（虚构）", "11010119950505003X", "13700137000",
         "6222020200112233447", "wangwu@example.com",
         "广州市天河区示例街 3 号（虚构）", "100.00"],
    ]
    csv_path = HERE / "synthetic_table.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)
    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "虚构PII表格"
        for row in rows:
            ws.append(row)
        wb.save(HERE / "synthetic_table.xlsx")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] xlsx 生成失败（缺 openpyxl）：{exc}")
    return csv_path


def main() -> int:
    HERE.mkdir(parents=True, exist_ok=True)
    make_id_card_front(HERE / "synthetic_id_card_front.png")
    make_id_card_back(HERE / "synthetic_id_card_back.png")
    make_plate(HERE / "synthetic_plate.png")
    make_chat(HERE / "synthetic_chat.png")
    make_door_sign(HERE / "synthetic_door_sign.png")
    make_road_sign(HERE / "synthetic_road_sign.png")
    make_invoice(HERE / "synthetic_invoice.png")
    make_table()
    meta = {
        "note": "全部为虚构数据，仅用于数据隐站脱敏功能测试；不含真实人像/证件。",
        "fake_id_numbers": [FAKE_ID, FAKE_ID2],
        "watermark": WATERMARK,
    }
    (HERE / "synthetic_manifest.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成合成夹具：{HERE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
