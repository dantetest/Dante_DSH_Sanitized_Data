# pipl_public 测试素材包（合成夹具 + 公开素材清单）

> 用途：给「图像 P0（人脸/证件/车牌/门牌/路牌）+ 条码二维码 + PII 表格」做
> 真实媒体评测（脚本：[`app/tests/eval_real_media.py`](../../eval_real_media.py)）。
> **不写入产品、不打包、不联网。**

## 1. 目录内容

| 文件 | 是否提交 | 说明 |
|---|---|---|
| `generate_synthetic.py` | ✅ 提交 | 合成夹具生成器（PIL/openpyxl；全部虚构数据） |
| `synthetic_id_card_front.png` / `synthetic_id_card_back.png` | ✅ 提交 | 虚构身份证正/反面（合法校验位的**虚构**号码；图上带「虚构样例」水印） |
| `synthetic_plate.png` | ✅ 提交 | 虚构车牌（京A·12345，水印） |
| `synthetic_chat.png` | ✅ 提交 | 虚构聊天截图（姓名/手机号/身份证/银行卡/地址/金额均为虚构） |
| `synthetic_door_sign.png` / `synthetic_road_sign.png` | ✅ 提交 | 虚构门牌 / 路牌 |
| `synthetic_invoice.png` | ✅ 提交 | 虚构发票样式（含 QR；税号/公司/金额均虚构） |
| `synthetic_table.xlsx` / `synthetic_table.csv` | ✅ 提交 | 虚构 PII 表格（姓名/身份证/手机/银行卡/邮箱/地址/金额） |
| `synthetic_manifest.json` | ✅ 提交 | 合成夹具元数据（水印、虚构号码） |
| `MANIFEST.md` | ✅ 提交 | **公开素材**清单：URL / 许可证说明 / 用途 / 是否提交（否） |
| `%TEMP%\pipl_public_testset\*` | ❌ 不提交 | 从 GitHub raw 下载的真实/示例照片与文档扫描图，只在本机评测 |

## 2. 安全约定

- 合成夹具**全部为虚构数据**，不含真实人像/证件/单位；身份证号虽然校验位合法，
  但序列与地址均为虚构示例，不能对应真实个人。
- 公开素材（真实人像/文档扫描图）不复制进仓库、不打包、不对外展示；
  图片版权以原出处为准，仅本地功能评测使用。
- **公开素材只允许内部功能评测**：不得对外分发、不得用于产品演示/官网素材、
  不得用于模型训练或微调（详见 `MANIFEST.md`「许可与内部测试边界」；
  AGPL 来源已移除，实际参与评测 31 个文件）。
- 评测脚本只读输入目录、报告写到 `%TEMP%`，**不导出成品、不复制素材**；
  报告不记录实体原值与 OCR 文本。
- 本目录不构成任何合规结论。

## 3. 重新生成合成夹具

```powershell
python -X utf8 app/tests/fixtures/pipl_public/generate_synthetic.py
```

生成结果是确定性的（相同字体/库版本下一致）。若修改了虚构数据，请同步更新
`synthetic_manifest.json` 与本文档说明。

## 4. 公开素材下载（本地，不提交）

`MANIFEST.md` 记录了下载来源、许可说明与**内部测试边界**；下载动作不在产品代码内、
也不在评测脚本内，只在开发机用临时脚本完成（GitHub raw；Wikimedia 429 未使用）。
若需重建公开素材，可按 MANIFEST 中的 URL 重新下载到 `%TEMP%\pipl_public_testset\`；
**AGPL 来源（openalpr appicon）已删除，不参与评测**。

## 5. 评测用法（eval_real_media.py）

```powershell
# 0) 一条命令：默认用户测试集（目录不存在时退回本目录合成夹具）
python -X utf8 app/tests/eval_real_media.py

# 1) 指定目录（--dir 可多次，支持图片/PDF/TIFF/Office/text；默认用户测试集）
python -X utf8 app/tests/eval_real_media.py ^
    --dir "C:\Users\Dante\Desktop\测试集" ^
    --dir "%TEMP%\pipl_public_testset" ^
    --dir app/tests/fixtures/pipl_public

# 2) 只看合成夹具 / 跳过 OCR（快速看人脸/证件/码等视觉扫描）
python -X utf8 app/tests/eval_real_media.py --dir app/tests/fixtures/pipl_public
python -X utf8 app/tests/eval_real_media.py --dir app/tests/fixtures/pipl_public --no-ocr

# 3) 其它参数
#    --out DIR          报告目录（默认 %TEMP%\yz_eval_<时间戳>）
#    --strictness        loose|standard|strict（默认 standard，与产品一致）
#    --limit N          每个目录最多 N 个文件（调试）
#    --include .heic,.gif   额外扩展名
#    --json-only        只写 JSON
#    --quiet            只打印汇总
```

**输出**（默认 `%TEMP%\yz_eval_<时间戳>\`）：

| 文件 | 内容 |
|---|---|
| `report.json` | 逐文件明细：fmt/pages/blocks、实体类型计数与决策分布、视觉 marks 明细（kind/note/confidence/rect/面积比）、耗时、warnings、错误；`meta.engine_snapshot` 记录引擎文件 sha256 + git HEAD/dirty（并行开发期可追溯） |
| `report.csv` | 同上扁平表（utf-8-sig，Excel 可开；含 `mark_notes`、`suspicious_marks`、`whole_page_id`、`id_fp_on_document`） |
| `summary.md` | 汇总 + 逐文件表格 + 疑似整页误涂/证件误判统计 |

**字段判读**（用于 P0 验收，不下合规结论）：
- `visual_mark_details[].note`：`/detect` = 仅检测未解码；`whole_page` = 证件整页覆盖；
  `conforms` 之类不出现——note 不含解码内容。
- `suspicious_marks`：无效码大框（`invalid_large`，/detect 且面积≥15%）+
  整页证件（`whole_page_id`，证件面积≥90%）的并集，需人工复核。
- `id_fp_on_document`：整页证件误判落在票面/合同页（发票关键词或文字块≥20），
  属"可能把整页发票/合同涂黑"的高风险项。
- `id_card`（仅图片/TIFF）：`id_black`/`whole_card_covered`/`vertical_text_blocks`
  用于判断旋转证件照片是否整卡被 black。
- 性能：`total_ms` 含 OCR+扫描；`parse_ms` 占绝对大头；中位/最大见 `summary.md`。

**隐私与边界**：脚本不联网、不导出、不复制输入；用户测试集只读；报告不写实体原值/OCR 文本。
本评测**不属于红线门禁**（`app/tests/regression.py`），是发布前人工评测/回归参考。

## 6. 与回归门禁的关系

- `app/tests/regression.py` / `run_all.py`：自动化红线门禁（不依赖用户真实素材）；
- `eval_real_media.py`：真实媒体人工评测（依赖用户测试集/公开素材），**不进 CI 门禁**；
- 建议在发布前与每次视觉/码类能力变更后各跑一次，结果存 `%TEMP%` 并人工过一眼。

