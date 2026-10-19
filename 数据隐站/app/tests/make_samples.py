"""生成测试样例。只在开发时跑，产物落在 samples/。

用法：python app/tests/make_samples.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(parents=True, exist_ok=True)

CONTRACT = """委 托 代 理 合 同

委托人（甲方）：张伟
性        别：男
身份证号码：310104198703124510
住        址：上海市徐汇区衡山路820弄12号501室
联系电话：13817264451

受托人（乙方）：上海市明理律师事务所
执业许可证号：31310000E12345678X
承 办 律 师：李静怡
联系电话：021-62889017

案        由：房屋买卖合同纠纷
案        号：（2025）沪0104民初18732号
承 办 法 官：陈国栋
对方当事人：王建国

一、委托事项
甲方因与王建国房屋买卖合同纠纷一案，委托乙方指派律师担任其诉讼代理人。

二、代理费用
本案争议标的额为人民币2,860,000.00元。代理费为人民币86,000.00元，
由甲方于本合同签订之日起五日内支付至乙方下列账户：
户    名：上海市明理律师事务所
开 户 行：中国工商银行上海市徐汇支行
账    号：6222 0210 0112 3456 789

甲方（签字）：____________     乙方（盖章）：____________
2025 年 3 月 12 日
"""

MEDICAL = """住 院 病 历 首 页

姓名：赵淑芬            性别：女        年龄：67
身份证号：310110195803127747
住院号：ZY202503170045
联系电话：13901827733
家庭住址：上海市杨浦区控江路1555弄8号402室
入院日期：2025-03-17
出院日期：2025-03-29

主诉：反复胸闷气促三年，加重一周。
诊断：冠状动脉粥样硬化性心脏病
      慢性心力衰竭（NYHA III 级）
      2型糖尿病

主治医师：周文博
责任护士：孙晓梅
科室：心血管内科

出院医嘱：继续口服阿司匹林肠溶片 100mg 每日一次，
定期复查，两周后门诊随访。
"""


def make_pdf(path: Path) -> None:
    import fitz
    doc = fitz.open()
    # 第一页：合同全文
    page = doc.new_page(width=595, height=842)
    rect = fitz.Rect(56, 56, 540, 800)
    page.insert_textbox(rect, CONTRACT, fontname="china-s", fontsize=10.5, lineheight=1.55)
    # 第二页：补充条款
    page2 = doc.new_page(width=595, height=842)
    page2.insert_textbox(
        fitz.Rect(56, 56, 540, 800),
        "补 充 条 款\n\n"
        "1. 乙方指定联系人：李静怡，电话 021-62889017。\n"
        "2. 甲方送达地址：上海市徐汇区衡山路820弄12号501室。\n"
        "3. 争议标的额如有变更，以（2025）沪0104民初18732号案为准。\n"
        "4. 本合同一式两份，双方各执一份。\n",
        fontname="china-s", fontsize=10.5, lineheight=1.6,
    )
    doc.set_metadata({"author": "上海市明理律师事务所", "title": "委托代理合同",
                      "producer": "Microsoft Word", "creator": "张伟"})
    doc.save(str(path))
    doc.close()


def make_docx(path: Path) -> None:
    import docx
    d = docx.Document()
    d.add_heading("住院病历首页", level=0)
    for line in MEDICAL.split("\n"):
        p = d.add_paragraph()
        p.add_run(line)
    table = d.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "检查项目"
    table.cell(0, 1).text = "结果"
    table.cell(0, 2).text = "备注"
    table.cell(1, 0).text = "患者"
    table.cell(1, 1).text = "赵淑芬"
    table.cell(1, 2).text = "住院号 ZY202503170045"
    d.core_properties.author = "周文博"
    d.core_properties.title = "住院病历"
    d.core_properties.company = "上海市第一人民医院"
    d.core_properties.last_modified_by = "周文博"
    d.save(str(path))


def make_xlsx(path: Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "对账单"
    rows = [
        ["客户名称", "账号", "证件号", "金额", "联系电话"],
        ["张伟", "6222 0210 0112 3456 789", "310104198703124510", 2860000.00, "13817264451"],
        ["赵淑芬", "6217 0033 8899 1122 334", "310110195803127747", 198000.50, "13901827733"],
        ["王建国", "6228 4801 2233 4455 667", "310105197211083312", 76000.00, "13611223344"],
        ["上海市明理律师事务所", "6222 0210 0112 3456 789", "31310000E12345678X", 86000.00, "021-62889017"],
    ]
    for r in rows:
        ws.append(r)
    wb.properties.creator = "财务部"
    wb.properties.lastModifiedBy = "李会计"
    wb.properties.title = "2025年3月对账单"
    wb.save(str(path))


def make_image(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1000, 620), (246, 241, 230))
    d = ImageDraw.Draw(img)
    font_path = None
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc",
                 r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(cand):
            font_path = cand
            break
    big = ImageFont.truetype(font_path, 46) if font_path else ImageFont.load_default()
    mid = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default()
    d.text((48, 40), "名  片", font=big, fill=(30, 26, 20))
    lines = [
        "上海市明理律师事务所",
        "李静怡  合伙人律师",
        "手机：13817264451",
        "电话：021-62889017",
        "邮箱：lijingyi@mingli-law.cn",
        "地址：上海市徐汇区衡山路820弄12号501室",
    ]
    y = 140
    for line in lines:
        d.text((48, y), line, font=mid, fill=(40, 34, 26))
        y += 62
    d.rectangle([760, 380, 940, 470], outline=(178, 58, 46), width=3)
    d.text((790, 405), "明理律所", font=mid, fill=(178, 58, 46))
    img.save(str(path), quality=95)


def make_tiff(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (800, 600), (232, 228, 214))
    d = ImageDraw.Draw(img)
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    font = ImageFont.truetype(font_path, 28) if os.path.exists(font_path) else ImageFont.load_default()
    d.text((40, 40), "正射影像 DOM", font=font, fill=(40, 40, 40))
    d.text((40, 96), "图幅号：H51G023045", font=font, fill=(40, 40, 40))
    d.text((40, 150), "控制点：CP-2025-117", font=font, fill=(40, 40, 40))
    d.text((40, 204), "坐标系：CGCS2000 / 3-degree GK CM 117E", font=font, fill=(40, 40, 40))
    for i in range(0, 800, 80):
        d.line([(i, 0), (i, 600)], fill=(210, 205, 190), width=1)
    for i in range(0, 600, 80):
        d.line([(0, i), (800, i)], fill=(210, 205, 190), width=1)
    info = {
        33550: (0.1, 0.1, 0.0),          # ModelPixelScale
        33922: (0.0, 0.0, 0.0, 500000.0, 3450000.0, 0.0),  # ModelTiepoint
        34735: (1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4490),
        42112: "AREA_OR_POINT=Area",
    }
    img.save(str(path), tiffinfo=info)


def main() -> int:
    targets = [
        ("委托代理合同.pdf", make_pdf),
        ("住院病历.docx", make_docx),
        ("对账单.xlsx", make_xlsx),
        ("名片.png", make_image),
        ("正射影像.tif", make_tiff),
    ]
    for name, fn in targets:
        p = SAMPLES / name
        try:
            fn(p)
            print(f"  OK  {p.name}  {p.stat().st_size} bytes")
        except Exception as exc:
            print(f"  !!  {p.name}  {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
