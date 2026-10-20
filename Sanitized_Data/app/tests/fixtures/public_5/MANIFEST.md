# 公共 5 样本实测清单（P1.5 真实样本自查）

> 生成：2026-10-20 · 用途：按用户要求「去网上下载 5 个，自己测试检查」，
> 用现有引擎跑 parse → detect → decide → visual → export → verify。
> 本地样本文档：`%TEMP%\yz_public5_testset\`（**二进制默认不提交**）。
> 评测脚本：`app/tests/eval_public_5.py`；报告：`%TEMP%\yz_public5_report.md`。
> 本次评测引擎快照：`ad4ded1`（详见报告 JSON 的 `engine_snapshot`）。
> 下载日期：2026-10-20（北京时间）。

## 提交策略

- **只提交本 MANIFEST + `app/tests/eval_public_5.py`**；5 个样本二进制全部留在
  `%TEMP%`，不进仓库（其中 DOCX 站点许可不明确，PDF/CSV 虽许可清晰但含二进制大文件）。
- 样本中无真实个人证件/手机号/住址；生成件全部为虚构内容。
- 测试仅本机行为，不用于对外分发/演示/训练。

## 样本清单

| # | 文件（本地） | 类别 | 来源 URL | 许可 | 提交？ | sha256 | bytes |
|---|---|---|---|---|---|---|---|
| 1 | `public_5_pdf_plos_0000308.pdf` | PDF：文本层 + 内嵌栅格图 | https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0000308&type=printable （文章：https://doi.org/10.1371/journal.pone.0000308） | **CC BY 4.0**（PLOS 文章页声明 Creative Commons Attribution License） | 否 | `b52ca44dfd09d240543b80315f0cd43fe9ba7946f2b5650d5580213ed5d9186c` | 91,408 |
| 2 | `public_5_docx_calibre_demo.docx` | DOCX：内嵌图片/表格/脚注/超链接 | https://filesamples.com/samples/document/docx/sample1.docx | **不明确**：站点未声明许可；内容为 calibre DOCX 功能演示文档（calibre 项目 GPLv3，但本次来源为再分发站点）→ 仅本地测试、不提交、不转发 | 否 | `269329fc7ae54b3f289b3ac52efde387edc2e566ef9a48d637e841022c7e0eab` | 1,311,881 |
| 3 | `public_5_csv_iris.csv` | CSV 表格：Iris（无 PII） | https://archive.ics.uci.edu/ml/machine-learning-databases/iris/iris.data （数据集页：https://archive.ics.uci.edu/dataset/53/iris） | **CC BY 4.0**（UCI 数据集页 Creative Commons Attribution 4.0 International） | 否 | `6f608b71a7317216319b4d27b4d9bc84e6abd734eda7872b71a458569e2656c0` | 4,551 |
| 4 | `public_5_qr_synthetic.png` | 图片：QR 码 | 本地生成（zxing-cpp，Apache-2.0 库；内容 `PUBLIC-5-TEST-QR`） | 生成内容，无第三方素材 | 默认否 | `2587be4a79a6702f37f83eb6e23b34e98c303a741b326e4cfe69a4c705ebe64c` | 1,202 |
| 5 | `public_5_scanned_annotated.pdf` | 扫描件 + 批注 + 附件 PDF | 本地生成（PIL 合成扫描页 + PyMuPDF 写批注/附件；文字全虚构） | 生成内容，全虚构 | 默认否 | `5c88e412b5ff608f1af3e786400dba0176c91506ac2d2acbf9b7fdbb3e15282f` | 3,694,380 |

## 生成件说明（#4/#5）

- QR：`zxingcpp.create_barcode("PUBLIC-5-TEST-QR", BarcodeFormat.QRCode, ec_level="30%")`，
  保存为 PNG；无 URL/个人信息。
- 扫描件：PIL 合成 A4 扫描风格页面（带轻微噪声/模糊），文字与图形全部虚构：
  姓名「张三（虚构）」、手机 13800138000、邮箱 test@example.com、
  身份证号 110101199003074477（校验位合法但为虚构）、地址「北京市朝阳区示例路 88 号」、
  金额 120,000.00；页面含文本批注/高亮批注与 `notes.txt` 附件，用于验证 PDF 隐藏数据剥离。

## PII / 许可自查

- PLOS PDF：学术论文，含作者姓名/通讯邮箱等公开学术元数据、图表；无私人联系方式/证件。
- DOCX：对正文/表格做手机号/身份证号/邮箱正则粗检，0 命中；但许可不明确，故仅本地测试。
- Iris CSV：公开数据集，仅数值列；无 PII。
- 生成件：全虚构；无真实人像/证件。

## 复跑

```powershell
# 下载/生成到 %TEMP%\yz_public5_testset（本次样本已在本地；其他机器需按上表来源获取）
# 实测（解析→检测→导出→verify，报告自动写 %TEMP%\yz_public5_report.md）
python -X utf8 app/tests/eval_public_5.py --dir "%TEMP%\yz_public5_testset"
```

实测结果（详见 `%TEMP%\yz_public5_report.md`）：5/5 导出成功、5/5 verify 通过、
0 解析错误；无 P0 缺陷；主要已知限制为 PDF 内嵌图片 OCR/P1.5 helper 使图多页面变慢
（PLOS 13.4s、扫描 PDF 7.9s），以及图多页的「小脸可能漏检」重复 warning（P2/P3 优化候选）。
