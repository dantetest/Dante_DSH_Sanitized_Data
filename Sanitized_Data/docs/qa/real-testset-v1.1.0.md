# 真实测试集全链路实测报告（v1.1.0）

> 引擎版本：`1.1.0`（`Sanitized_Data/app/server/yinzhan/config.py: VERSION`）
> 引擎源码 HEAD：`7c553ab`（评测时工作区仅有版本号/文档/安装包等非引擎改动；引擎逻辑与 HEAD 一致，快照见 §2.3）
> 评测脚本：`Sanitized_Data/app/tests/eval_public_5.py`（通用逐文件评测器：parse → detect → decide → visual → export → verify）
> 评测时间：2026-10-21 01:27:25–01:34:50（Asia/Shanghai，总 443.2s）· 报告重建：2026-10-21
> 输入：用户真实测试集目录（17 个文件，含真实个人信息；**本报告不写文件名、原文、OCR 文本、实体原值**，文件用 R01–R17 匿名标识）
>
> 数据合规：源文件只读、未复制进仓库；导出成品仅写 `Sanitized_Data/out/qa_real_20261021/exported/`（本地、
> gitignore，不提交、不外传）；out/ 内引擎数据/成品含脱敏后内容，验证完建议按 §8 清理。本报告只含统计、
> 类型、数量、耗时、warning 摘要。

## 1. 结论速览

> **更新（2026-10-21，发布前）**：R04 根因（地址保形假值保留完整原值 + verify 假值豁免过宽）
> 已修复并复验，真实测试集修复后 **17/17 全链路通过、P0=0**；本报告保留修复前记录作为证据。
> 修复内容与复验数据见 [r04-fix-verification.md](r04-fix-verification.md)。

| 指标 | 结果 |
|---|---|
| 文件数 | **17**（PDF 10、图片 7） |
| 解析成功 / 解析错误 | **17 / 0** |
| 导出成功 | **17/17** |
| verify 通过 | **16/17**（1 个失败） |
| **P0 缺陷** | **1 个（R04，真实残留泄漏）** |
| 已知限制 | 性能（OCR 双跑）、warning 噪声、异常大码框未自动打码、整页证件过判 |
| 总耗时 | **443,225 ms（≈7.4 分钟）** |
| 实体总数 | **275**（22 类；决策 surrogate 155 / black 119 / keep 1） |
| 导出成品总量 | 81.9 MB（本地验证用） |

**准入判定：不通过（Red）**——16/17 文件全链路通过，但 R04 存在 1 处**真实原文残留**（ADDRESS 假值完整
保留了原始地址），属于必须修复的 P0；其余 16 个文件解析/导出/verify 均通过。

## 2. 运行环境与方法

### 2.1 环境

| 项 | 值 |
|---|---|
| OS | Windows 10 Pro 10.0.19045（AMD64，6 逻辑核） |
| Python | 3.12.9（MSC v.1942 64-bit），`python -X utf8` |
| OCR | RapidOCR（onnxruntime 1.26.0），模型 PP-OCRv6 det/rec small + cls |
| 其他库 | PyMuPDF 1.27.2.3、Pillow 12.2.0 |
| 执行方式 | 后台进程顺序执行（单实例，无并发，避免抢 CPU/端口） |
| 运行命令 | `cd Sanitized_Data && python -X utf8 app/tests/eval_public_5.py --dir "C:\Users\Dante\Desktop\测试集" --out "D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\out\qa_real_20261021"` |
| 原始报告 | `Sanitized_Data/out/qa_real_20261021/public5_report.md` / `.json`（本地，gitignore） |
| 沙箱环境提示 | 本 QA 会话为 DSH workspace-write 沙箱；该沙箱下 `tempfile.mkdtemp`（mode 0700）目录不可写（`Permission denied`），影响 P1.5 内嵌图片 OCR 等用 mkdtemp 的代码路径。真实测试集 17 文件未触发该路径，结果不受影响；详见公开集报告 §5.2。 |

### 2.2 评测口径

每个文件独立执行：解析（文本层优先，扫描件降级 OCR）→ 规则检测 → 决策（black / surrogate / keep）→
视觉/码类实体（人脸/证件/印章/二维码/条码/签署区/手写）→ 导出 → 成品重解析 verify（原文残留、元数据、
视觉/码类残留、PDF 隐藏数据）。`verify` 失败即视为该文件不通过；`_issues` 由脚本按「P0 缺陷 / 已知限制 /
误报」归类，本报告在脚本结论基础上人工复核并补充根因。

### 2.3 引擎快照（可追溯性）

- `git HEAD = 7c553ab`；评测时 `git dirty = true`，但脏文件均为版本号/网页/安装包/文档等**非引擎文件**
  （`config.py` 版本号 1.0.0→1.1.0、web/desktop/docs 等），引擎逻辑文件 mtime 均早于本轮改动。
- 关键引擎文件 sha256 前 16 位：`pipeline.py a4ccf67e64170e40`、`jobs.py 4e7f273f6a1d86d6`、
  `pdf_parser.py a4c5749b401ae9b2`、`image_parser.py 1d7ce01ef5b4e655`、`visual_targets.py 58eaff8325e9147b`、
  `mediascan.py 4482a03e9720d55d`、`barcodes.py 8244b461c64fb7fa`、`rules.py dafc8941644eb9de`、
  `udm.py 6f9d61d547bd36e2`、`ocr.py 2419f4f3e78a31ec`（完整 14 文件 + rules 指纹见 JSON `engine_snapshot`）。
- 注意：`yinzhan/replacers/surrogate.py` **不在** `_ENGINE_SNAPSHOT_FILES` 指纹列表内，而本次 P0 根因恰在其
  `_split_locality`/`_fake_address`；建议把 replacers 加入快照，保证换名逻辑可追溯。

## 3. 样本清单（匿名）

17 个文件：PDF 10 个（3 个为文本层 PDF，7 个为扫描件全 OCR）、图片 7 个（PNG 2、JPG 5）。
"文本形态"按解析结果判定（未人工阅读原文）；类别只按检测到的实体/视觉类型归类。

| ID | 类别（按检测特征归类，已泛化） | fmt | 页 | 文本形态 | 文本块/OCR 块 | 文本字符 | 大小 | sha256 前 12 | 实体数 |
|---|---|---|---|---|---|---|---|---|---|
| R01 | 文本层 PDF（票据类） | pdf | 1 | 文本层 | 25/0 | 268 | 140,549B | `2acdf6aebfc1` | 9 |
| R02 | 文本层 PDF（票据类） | pdf | 1 | 文本层 | 25/0 | 287 | 141,296B | `5d273942c7c1` | 9 |
| R03 | 扫描件（短文档） | pdf | 2 | OCR | 35/35 | 676 | 1,398,954B | `186ccf573567` | 13 |
| R04 | 扫描件（多页合同） | pdf | 10 | OCR | 255/255 | 5,298 | 4,213,972B | `60581a5b1831` | 39 |
| R05 | 微信图片（票据类截图） | png | 1 | OCR | 53/53 | 539 | 77,273B | `d7aa22b03a61` | 19 |
| R06 | 微信图片（票据类截图） | png | 1 | OCR | 54/54 | 517 | 78,938B | `debae6e2d1a9` | 20 |
| R07 | 微信图片（证件类照片） | jpg | 1 | OCR | 4/4 | 51 | 724,178B | `53a41b63cc75` | 3 |
| R08 | 微信图片（人像/手写类照片） | jpg | 1 | OCR | 1/1 | 1 | 4,221,031B | `1382e9722d46` | 2 |
| R09 | 微信图片（人像+码类照片） | jpg | 1 | OCR | 1/1 | 1 | 4,323,842B | `efcd40da2df9` | 4 |
| R10 | 微信图片（人像+码类照片） | jpg | 1 | OCR | 1/1 | 1 | 4,190,506B | `a9746a196401` | 5 |
| R11 | 微信图片（身份证类照片） | jpg | 1 | OCR | 8/8 | 66 | 795,225B | `c9d07f03a64a` | 5 |
| R12 | 扫描件（合同） | pdf | 3 | OCR | 64/64 | 1,095 | 1,042,213B | `42b8268acb38` | 12 |
| R13 | 扫描件（协议） | pdf | 10 | OCR | 283/283 | 4,949 | 2,835,039B | `40e8500986cd` | 41 |
| R14 | 扫描件（协议） | pdf | 10 | OCR | 269/269 | 4,983 | 3,433,979B | `d9d3a37e74ad` | 41 |
| R15 | 扫描件（法律文书） | pdf | 5 | OCR | 119/119 | 2,413 | 1,088,763B | `422f9878c71f` | 22 |
| R16 | 文本层 PDF（法律文书） | pdf | 2 | 文本层 | 19/0 | 355 | 167,850B | `b9e7284e2642` | 5 |
| R17 | 扫描件（历史已处理件） | pdf | 17 | OCR | 624/624 | 7,874 | 15,175,315B | `de1c8e3382ab` | 26 |

## 4. 总体统计

### 4.1 处理结果

- 解析成功 17/17，解析错误 0；无崩溃、无超时中断。
- 导出成功 17/17（导出版本与输入同格式：10 PDF + 7 图片；成品合计 81.9 MB）。
- verify 通过 16/17；失败 1 个（R04：`VerifyError: 仍有 1 处原文残留`）。
- PDF 隐藏数据：R01/R02 各检测到批注 2 条并在导出时剥离（导出 warning：`已剥离 PDF 隐藏数据：批注 2 条`），
  成品复检无残留；其余文件无隐藏数据。
- 输入侧隐藏数据/元数据：无"元数据未清理 / PDF 隐藏数据未清理 / 视觉残留"类 verify 失败。

### 4.2 实体统计（275 个）

| 类型 | 数量 | 类型 | 数量 | 类型 | 数量 |
|---|---|---|---|---|---|
| ORG 机构 | 45 | AMOUNT 金额 | 44 | SEAL 印章（视觉） | 34 |
| FACE 人脸（视觉） | 34 | HANDWRITING 手写（视觉） | 28 | PERSON 人名 | 18 |
| ADDRESS 地址 | 17 | USCC 统一社会信用代码 | 12 | QRCODE 二维码（视觉） | 12 |
| PHONE 电话 | 8 | ACCOUNT 账号 | 5 | SIG_BLOCK 签署区（视觉） | 5 |
| INVOICE_CODE/NO | 2/2 | CASE_NO 案号 | 2 | CONTRACT_NO 合同号 | 1 |
| ID_AUTHORITY / ID_VALIDITY | 1/1 | ID_PHOTO / ID_NAME | 1/1 | ID_NUMBER / ID_ADDRESS | 1/1 |

- 决策分布：`surrogate 155`（保形假值）、`black 119`（涂黑）、`keep 1`（R15 中 1 处按规则显式保留，设计内，非残留）。
- 视觉 marks 合计：`seal 38、face 34、qrcode 14、id_document 2、id_* 字段各 1`。
- 图片类文件（7 个）合计 **58** 实体；PDF 类（10 个）合计 **217** 实体；合计 275。

### 4.3 耗时

| 指标 | 值 |
|---|---|
| 总耗时 | 443,225 ms |
| 均值 / 中位数 | 26,072 ms / 5,709 ms |
| 最慢 | R17 112,137 ms、R04 85,364 ms、R13 77,607 ms、R14 61,749 ms |
| >60s 文件 | 4 个（均为多页扫描件） |
| >20s 文件 | 7 个（全部含 OCR） |
| 阶段合计 | parse 201,036 ms、verify 222,468 ms、export 18,765 ms、detect 435 ms |
| OCR 相关占比 | parse+verify ≈ **95.5%**；PDF 类合计 409.6s、图片类合计 33.7s |
| 文本层 PDF | R01 549ms、R02 319ms、R16 295ms（无 OCR，均 <0.6s） |

结论：耗时几乎全部来自**扫描件 OCR 两次执行**（解析一次 + verify 重解析一次）。多页扫描件（10–17 页）
单文件 60–112s，是当前最显著的性能限制（P2，不阻塞正确性）。

## 5. 逐文件结果

| ID | fmt/页 | 实体（主要类型） | parse | detect | export | verify | 总耗时 | 结果 |
|---|---|---|---|---|---|---|---|---|
| R01 | pdf/1 | USCC:2、AMOUNT:4、ORG:2、QRCODE:1 | 221ms | 51ms | 44ms | ok (r=0,v=0) | 549ms | ✅ |
| R02 | pdf/1 | USCC:2、AMOUNT:4、ORG:2、QRCODE:1 | 229ms | 7ms | 39ms | ok (r=0,v=0) | 319ms | ✅ |
| R03 | pdf/2 | PHONE:1、AMOUNT:4、ORG:5、ADDRESS:1、SEAL:2 | 9,269ms | 11ms | 1,383ms | ok (r=0,v=0) | 21,198ms | ✅ |
| R04 | pdf/10 | ORG:7、CONTRACT_NO:1、AMOUNT:4、ADDRESS:1、ACCOUNT:2、SIG_BLOCK:2、FACE:17、SEAL:3、HANDWRITING:2 | 38,669ms | 63ms | 4,009ms | **FAIL: 仍有 1 处原文残留** | 85,364ms | ❌ **P0** |
| R05 | png/1 | INVOICE_CODE:1、INVOICE_NO:1、PHONE:2、USCC:2、PERSON:2、AMOUNT:4、ORG:3、ADDRESS:2、QRCODE:1、HANDWRITING:1 | 2,874ms | 10ms | 46ms | ok (r=0,v=0) | 5,709ms | ✅ |
| R06 | png/1 | 同 R05，另 HANDWRITING:2 | 2,094ms | 9ms | 45ms | ok (r=0,v=0) | 4,680ms | ✅ |
| R07 | jpg/1 | QRCODE:1、ID_AUTHORITY:1、ID_VALIDITY:1（视觉含整页 id_document） | 1,676ms | 3ms | 27ms | ok (r=0,v=0) | 2,913ms | ✅（整页证件误报） |
| R08 | jpg/1 | FACE:1、HANDWRITING:1 | 2,981ms | 2ms | 176ms | ok (r=0,v=0) | 6,448ms | ✅ |
| R09 | jpg/1 | QRCODE:3、FACE:1 | 2,483ms | 1ms | 128ms | ok (r=0,v=0) | 5,582ms | ✅ |
| R10 | jpg/1 | QRCODE:2、FACE:2、HANDWRITING:1 | 2,475ms | 1ms | 144ms | ok (r=0,v=0) | 5,448ms | ✅ |
| R11 | jpg/1 | ADDRESS:1、ID_PHOTO:1、ID_NAME:1、ID_NUMBER:1、ID_ADDRESS:1 | 1,636ms | 3ms | 110ms | ok (r=0,v=0) | 2,877ms | ✅（整页证件误报） |
| R12 | pdf/3 | AMOUNT:2、PERSON:3、ORG:3、SIG_BLOCK:1、SEAL:3 | 9,168ms | 15ms | 1,167ms | ok (r=0,v=0) | 21,247ms | ✅ |
| R13 | pdf/10 | PHONE:1、USCC:2、ORG:7、AMOUNT:9、PERSON:2、ADDRESS:4、ACCOUNT:1、SIG_BLOCK:1、FACE:5、SEAL:3、HANDWRITING:6 | 33,690ms | 61ms | 3,486ms | ok (r=0,v=0) | 77,607ms | ✅ |
| R14 | pdf/10 | USCC:2、ORG:6、AMOUNT:7、PERSON:2、ADDRESS:5、ACCOUNT:1、SIG_BLOCK:1、SEAL:2、FACE:7、HANDWRITING:8 | 27,443ms | 58ms | 3,722ms | ok (r=0,v=0) | 61,749ms | ✅ |
| R15 | pdf/5 | PHONE:2、CASE_NO:1、ORG:5、AMOUNT:2、PERSON:5、ADDRESS:1、ACCOUNT:1、SEAL:2、HANDWRITING:3 | 13,502ms | 30ms | 1,039ms | ok (r=0,v=0) | 29,105ms | ✅（keep 1 处，设计内） |
| R16 | pdf/2 | CASE_NO:1、ORG:2、PERSON:1、SEAL:1 | 122ms | 7ms | 34ms | ok (r=0,v=0) | 295ms | ✅ |
| R17 | pdf/17 | PERSON:1、SEAL:18、QRCODE:2、FACE:1、HANDWRITING:4 | 52,507ms | 105ms | 3,167ms | ok (r=0,v=0) | 112,137ms | ✅（异常大码框待确认） |

（r=residual，v=visual_residual；均为 0 表示成品无原文/视觉残留。）

### 5.1 各文件要点

- **R01/R02（发票，文本层）**：每份检出统一社会信用代码 2、金额 4、机构 2、二维码 1；PDF 隐藏数据（批注 2）
  在导出时剥离；verify 通过。文本层 PDF 全链路 <0.6s，性能良好。
- **R03/R12/R15（扫描件 PDF）**：OCR 检出电话/机构/地址/案号/金额/签约区/印章/手写等，全部 verify 通过；
  单文件 21–29s（parse+verify 双 OCR）。
- **R04（10 页扫描件，P0）**：见 §6。
- **R05/R06（微信图片-票据）**：各检出 19/20 个实体（发票代码/号码、电话、信用代码、人名、金额、机构、地址、
  二维码、手写），导出涂黑/替换后 verify residual=0，图片级脱敏链路正常。
- **R07/R11（证件类照片）**：身份证字段（签发机关/有效期限/姓名/号码/住址/照片）检出并按 black 处理，
  verify 通过；但 `id_document` 视觉框覆盖整页（面积 1.0），触发"整页证件误判"提示（§7.4）。
- **R08/R09/R10（人像/码类照片）**：人脸 1–2、二维码 2–3、手写命中，全部 black；verify 通过。
- **R16（文本层通知书）**：案号/机构/人名/印章，295ms 全通过。
- **R17（17 页扫描件，输入侧标注"已脱敏"）**：仍检出印章 18、二维码 2、人脸 1、手写 4 并按 black 处理，
  verify 通过；其中 1 个二维码候选面积 0.353 被判"异常大面积码类框，未自动打码"（§7.3）。若该文件是历史
  成品，说明历史版本对视觉目标覆盖不完整，建议人工复核（不影响本轮导出/verify）。

## 6. P0 缺陷明细（1 个）

### P0-1 R04：ADDRESS 保形假值完整保留原始地址 → 成品仍含原文（verify 正确拦截）

- **现象**：R04（10 页扫描件合同，39 实体）导出成功（19.5MB），verify 重解析报告 `仍有 1 处原文残留`，
  该文件不通过；其余 16 文件 verify 通过。
- **定位证据（脱敏）**：
  - 残留实体类型 = `ADDRESS`（地址，`origin=model`，`strategy=surrogate`，无 black），原值 11 个汉字；
  - 该实体假值 18 字符，**以完整原始地址开头**（假值中原始值偏移 0，后接 4 汉字 + 3 数字的虚构路名门牌）；
  - 根因函数：`yinzhan/replacers/surrogate.py::_split_locality()` 对该地址返回 `locality_len=11 / rest_len=0`
    （"省市区前缀"切成了**整段地址**），`_fake_address()` 随之"保留省市区"= 保留整段原地址 + 随机后缀；
    固定种子多次调用 100% 复现"假值包含全量原值"。
  - 成品验证：导出 PDF 的数字文本层 `contains_original_value=True`（原 PDF 为纯扫描、无文本层，故该文本只能来自
    换名写回的假值）；可见替换文字同样显示"原地址+虚构后缀"。
  - 同值扩散导致该地址在 2 个页面（第 5、10 页；0-based 页 4/9）各被替换为同一假值，两处均含原地址。
- **影响**：对外成品仍携带完整原始地址（可搜索文本 + 可见文字），属**真实隐私泄漏**，verify 拦截正确。
- **verify 逻辑附带风险（建议一并加固）**：`pipeline.verify` 的 `_replacement_intervals` 设计为"假值内部的
  原文子串不算残留"（用于豁免"人民法院/省市区"这类共同片段）。按代码逻辑，若该 18 字符假值在成品复检文本中
  连续匹配成功，其内部完整保留的 11 字原文会被**整体豁免**、verify 反而放行——这是与 P0 同源的第二处风险。
  本次 verify 之所以拦住，是因为该处假值在成品文本层被拆行/分块后未能连续匹配，豁免未生效（推断，未进一步
  复现拆行细节）。建议：
  1. 修复 `_split_locality` 只保留省/市/区（县/旗/镇/街道）层级，`_fake_address` 不保留门牌以下原值；
  2. verify 豁免收紧：仅豁免短共同片段（如 ≤3–6 字或白名单），**禁止豁免完整包含原文值的假值**；
  3. 增加回归用例：构造会触发 locality 过宽切分的地址样例，断言假值不包含原值。
- **复现**：见 §2.1 命令；脱敏根因脚本 `Sanitized_Data/out/qa_real_20261021/_diag_r04_repl.py`（本地，gitignore）。
- **严重度**：P0（verify residual>0 且确认为真实泄漏），建议发布前修复并重跑本测试集。

## 7. 已知限制与误报

### 7.1 已知限制：性能（P2，不阻塞）

- 扫描件 parse 与 verify 各做一次 OCR：14/17 文件走 OCR，OCR 相关耗时占 95.5%；4 个多页扫描件 >60s，
  最慢 17 页 112.1s。建议：verify 复用 parse 的 OCR 结果/局部缓存；或对扫描页在导出后仅对涂改区域做局部复检。

### 7.2 已知限制：warning 噪声（P3）

- `照片较大，小脸可能漏检，请人工复核/整图涂黑` 按页重复出现（R04 10 次、R13 10 次、R14 10 次、R17 17 次、
  R15 5 次、R12 3 次、R03 2 次，合计 64 次），属图多页噪声；建议去重/按文件汇总一次。
- 纯图片无文字提示（公共集 QR 样本类）不影响 verify。

### 7.3 已知限制：异常大面积码类框未自动打码（待确认）

- R17 检出 1 个 `qrcode` 候选面积 0.353（占页 35.3%），被判"异常大面积码类框，未自动打码"。
  该启发式用于防止把整页误当二维码涂黑；建议人工确认该页是否存在需要打码的码类目标。

### 7.4 误报：整页证件过判（安全方向，P2 优化）

- R07、R11 的 `id_document` 视觉框面积比 = 1.0（整页），触发"整页证件误判"提示，导致整页 black。
  R11 同时正确检出身份证字段（ID_NAME/ID_NUMBER/ID_ADDRESS/ID_PHOTO），R07 检出签发机关/有效期限。
  方向是"过度涂黑"（比漏涂安全），但会降低成品可用性；建议收紧整页证件判定（结合长宽比/字段命中）。
- 本次无"敏感目标漏涂"类视觉误报；16 个通过文件 visual_residual 均为 0。

### 7.5 其他观察（非缺陷）

- R15 有 1 个实体按 `keep` 策略保留（规则显式保留，如编号/条款类），verify 将其排除在残留检查外，属设计内。
- R17 输入文件名标注"已脱敏"，但输入内容仍能检出印章 18/二维码 2/人脸 1/手写 4：说明历史成品的视觉目标
  覆盖不完整（或该文件本就是未处理原件）；本轮已按 black 处理并通过 verify，建议人工确认历史成品质量。
- PDF 隐藏数据剥离（R01/R02 批注）与成品复检在本轮通过；`keep_pdf_hidden` 默认关闭符合预期。

## 8. 漏检评估

- 本轮脚本级检查：17/17 有实体（无"文本丰富但 0 实体"文件），7/7 图片有实体（无"图片无实体"），
  未触发脚本的"疑似漏检"规则；16/17 verify residual=0。
- 不能据此断言"无漏检"：verify 只检查"检出实体是否残留"，无法发现"原本应检出但从未被检出"的 PII。
  建议做一次人工抽查（随机 3–5 个成品，重点看扫描件多页/多语种纸张/表格区域），并在修复 P0 后重跑本测试集。
- 数据安全：本轮成品仅本地保存供 verify；**R04 成品仍含原地址，禁止外发**；验证结束后建议删除
  `Sanitized_Data/out/qa_real_20261021/exported/`、`_engine_data/`、`_work/`、`_diag_work/`（本地、gitignore），
  或至少确认不随安装包/仓库分发。

## 9. 总体结论与后续建议

1. **准入判定：不通过（Red）**。17/17 解析、17/17 导出成功，16/17 verify 通过；但 R04 存在 1 处真实原文残留
   （ADDRESS 假值机制缺陷），属 P0，必须修复后重跑本测试集。
2. **修复优先级**
   - **P0**：`_split_locality`/`_fake_address` 地址假值保留整段原值 → 修假值生成 + 收紧 verify 假值豁免逻辑
     （§6），补回归用例与 replacers 引擎指纹。
   - **环境提示（非产品缺陷，待外部复核）**：公开集评测中 P1.5 内嵌图片文字 OCR 报 `Permission denied`；
     已排查为本 DSH 沙箱对 `tempfile.mkdtemp`（mode 0700）目录的 ACL 限制，产品在可写临时目录下复跑 5/5 通过、
     warning 消失（详见 `Sanitized_Data/docs/qa/public-5-v1.1.0.md` §5.2）。建议在普通 Windows 环境复核一次；
     若在本会话沙箱内跑回归门禁，注意 `app/tests/` 中大量 `tempfile.mkdtemp` 夹具可能因此误报失败。
   - **P2**：扫描件 OCR 双跑成本（62–112s/多页文件）；整页证件过判收紧。
   - **P3**：warning 去重/降噪。
3. **回归建议**：修复后重跑本测试集（17 文件）+ 公开 5 样本 + 回归门禁（由 Lead 统一安排，避免并发抢资源），
   要求 17/17 verify 通过、公开集 5/5 通过，重点复核 R04 与其余含 ADDRESS 的文件（R03/R11/R13/R14/R15/R05/R06，共 17 个
   ADDRESS 实体）。
4. **环境提示**：本会话 `curl.exe`（schannel）不可用（`SEC_E_NO_CREDENTIALS`），改用 Python urllib；公开集下载
   校验与运行时无关。

---

**附录：证据文件（均在本地、gitignore，不提交）**

- `Sanitized_Data/out/qa_real_20261021/public5_report.md` / `public5_report.json`（原始逐文件明细）
- `Sanitized_Data/out/qa_real_20261021/_diag_r04_repl.py`（P0 脱敏根因脚本）
- `Sanitized_Data/out/qa_real_20261021/exported/`（17 个脱敏成品，仅本地 verify；R04 成品含残留，禁止外发）
- `Sanitized_Data/docs/qa/public-5-v1.1.0.md`（公开 5 样本报告）
