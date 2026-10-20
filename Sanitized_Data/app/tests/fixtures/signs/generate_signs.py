# -*- coding: utf-8 -*-
"""生成 P1-A「门牌/路牌/店招/单位铭牌」合成夹具（全部虚构，可提交）。

生成到本目录：
- sign_door.png     门牌：示例路 88 号（蓝底白字牌）
- sign_road.png     路牌：示例大道 / 东行 2 km（绿底白字）
- sign_shop.png     店招：示例便利店 / 24小时营业
- sign_org.png      单位铭牌：北京示例科技有限公司
- neg_scenery.png   负样本：风景（无文字）
- neg_document.png  负样本：合同正文样式（含地址/公司，但为正文小字）
- neg_card.png      负样本：名片样式（公司/地址/电话小字）
- manifest.json     每个 fixture 的期望 kind 与 ROI（像素）

安全约定：全部虚构文本、无真实单位/地址/个人信息；图上带「虚构样例」小字水印。
用法：
    python -X utf8 app/tests/fixtures/signs/generate_signs.py
"""
from __future__ import annotations

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
WATERMARK = "虚构样例·仅供数据隐站测试"


def _font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _watermark(draw: ImageDraw.ImageDraw, size: tuple[int, int]) -> None:
    draw.text((12, size[1] - 30), WATERMARK, font=_font(18), fill=(200, 200, 200))


def _save(name: str, img: Image.Image) -> None:
    img.save(HERE / name, optimize=True)


def make_door() -> dict:
    W, H = 900, 600
    img = Image.new("RGB", (W, H), (96, 98, 102))
    draw = ImageDraw.Draw(img)
    # 门牌：蓝底白字圆角牌
    x0, y0, x1, y1 = 120, 120, 780, 420
    draw.rounded_rectangle([x0, y0, x1, y1], radius=18, fill=(20, 70, 150),
                           outline=(240, 240, 240), width=6)
    draw.text((150, 200), "示例路 88 号", font=_font(88), fill=(250, 250, 250))
    _watermark(draw, (W, H))
    _save("sign_door.png", img)
    return {"file": "sign_door.png", "kind": "door_address", "roi": [x0, y0, x1, y1]}


def make_road() -> dict:
    W, H = 1200, 520
    img = Image.new("RGB", (W, H), (0, 110, 66))
    draw = ImageDraw.Draw(img)
    draw.rectangle([16, 16, W - 17, H - 17], outline=(250, 250, 250), width=8)
    draw.text((80, 70), "示例大道", font=_font(130), fill=(250, 250, 250))
    draw.text((80, 300), "东行 2 km", font=_font(80), fill=(240, 240, 240))
    _watermark(draw, (W, H))
    _save("sign_road.png", img)
    return {"file": "sign_road.png", "kind": "road_sign", "roi": [16, 16, W - 17, H - 17]}


def make_shop() -> dict:
    W, H = 1100, 720
    img = Image.new("RGB", (W, H), (70, 70, 74))
    draw = ImageDraw.Draw(img)
    # 店招灯箱
    x0, y0, x1, y1 = 80, 100, 1020, 470
    draw.rectangle([x0, y0, x1, y1], fill=(120, 40, 40), outline=(250, 240, 220), width=6)
    draw.text((130, 160), "示例便利店", font=_font(140), fill=(255, 240, 200))
    draw.text((140, 340), "24小时营业", font=_font(72), fill=(255, 240, 200))
    # 门框/玻璃
    draw.rectangle([330, 500, 770, 700], fill=(130, 150, 160), outline=(200, 200, 200), width=4)
    _watermark(draw, (W, H))
    _save("sign_shop.png", img)
    return {"file": "sign_shop.png", "kind": "shop_sign", "roi": [x0, y0, x1, y1]}


def make_org() -> dict:
    W, H = 1000, 700
    img = Image.new("RGB", (W, H), (150, 148, 142))
    draw = ImageDraw.Draw(img)
    # 金属单位铭牌
    x0, y0, x1, y1 = 90, 220, 910, 470
    draw.rectangle([x0, y0, x1, y1], fill=(196, 170, 108), outline=(90, 70, 30), width=5)
    draw.text((150, 300), "北京示例科技有限公司", font=_font(62), fill=(40, 30, 10))
    _watermark(draw, (W, H))
    _save("sign_org.png", img)
    return {"file": "sign_org.png", "kind": "org_plaque", "roi": [x0, y0, x1, y1]}


def make_scenery() -> dict:
    W, H = 1000, 700
    img = Image.new("RGB", (W, H), (135, 190, 235))
    draw = ImageDraw.Draw(img)
    for i in range(H // 2):
        c = int(135 + (i / (H / 2)) * 60)
        draw.line([(0, H // 2 - i), (W, H // 2 - i)], fill=(min(255, c), min(220, c + 20), 235))
    draw.polygon([(0, H), (0, H - 200), (260, H - 380), (520, H - 200), (760, H - 360),
                  (W, H - 220), (W, H)], fill=(70, 110, 80))
    draw.ellipse([760, 80, 880, 200], fill=(250, 230, 150))
    _watermark(draw, (W, H))
    _save("neg_scenery.png", img)
    return {"file": "neg_scenery.png", "kind": None, "roi": None}


def make_document() -> dict:
    W, H = 1000, 1400
    img = Image.new("RGB", (W, H), (252, 251, 248))
    draw = ImageDraw.Draw(img)
    draw.text((250, 60), "技术服务咨询协议（虚构）", font=_font(44), fill=(20, 20, 20))
    lines = [
        "甲方（委托方）：北京示例科技有限公司",
        "地址：北京市朝阳区示例路 88 号院 2 单元 301 室",
        "乙方（受托方）：上海示例咨询有限公司",
        "地址：上海市徐汇区示例大道 100 弄 5 号 501 室",
        "第一条 服务内容：乙方为甲方提供技术咨询服务。",
        "第二条 合同金额：人民币壹拾贰万元整（¥120,000.00）。",
        "第三条 交付地址：北京市朝阳区示例路 88 号。",
        "第四条 双方应于签署后 30 日内完成付款。",
    ]
    y = 160
    for line in lines:
        draw.text((90, y), line, font=_font(28), fill=(30, 30, 30))
        y += 66
    _watermark(draw, (W, H))
    _save("neg_document.png", img)
    return {"file": "neg_document.png", "kind": None, "roi": None}


def make_card() -> dict:
    W, H = 1000, 620
    img = Image.new("RGB", (W, H), (250, 250, 248))
    draw = ImageDraw.Draw(img)
    draw.rectangle([30, 30, W - 31, H - 31], outline=(180, 180, 180), width=2)
    draw.text((70, 70), "示例律师事务所", font=_font(46), fill=(30, 30, 30))
    draw.text((70, 170), "张律师  合伙人", font=_font(34), fill=(60, 60, 60))
    draw.text((70, 270), "手机：13800138000", font=_font(28), fill=(70, 70, 70))
    draw.text((70, 330), "地址：北京市朝阳区示例路 88 号 501 室", font=_font(28), fill=(70, 70, 70))
    draw.text((70, 390), "邮箱：lawyer@example.com", font=_font(28), fill=(70, 70, 70))
    _watermark(draw, (W, H))
    _save("neg_card.png", img)
    return {"file": "neg_card.png", "kind": None, "roi": None}


def main() -> int:
    cases = [make_door(), make_road(), make_shop(), make_org(),
             make_scenery(), make_document(), make_card()]
    manifest = {
        "generator": "app/tests/fixtures/signs/generate_signs.py",
        "note": "全部虚构文本；图为合成夹具，仅用于 sign_targets 自测。",
        "watermark": WATERMARK,
        "cases": cases,
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成 sign fixtures：{HERE}")
    for case in cases:
        print(f"  {case['file']:<20} kind={case['kind']} roi={case['roi']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
