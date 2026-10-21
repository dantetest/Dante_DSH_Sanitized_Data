# 公开 5 样本全链路实测报告（v1.1.0）

> 引擎版本：`1.1.0`（`Sanitized_Data/app/server/yinzhan/config.py: VERSION`）
> 引擎源码 HEAD：`7c553ab`（评测时工作区仅有版本号/文档/安装包等非引擎改动，引擎逻辑与 HEAD 一致）
> 评测脚本：`Sanitized_Data/app/tests/eval_public_5.py`（parse → detect → decide → visual → export → verify）
> 评测时间：2026-10-21 01:35（Asia/Shanghai）· 报告重建：2026-10-21
> 引擎快照：`app/tests/eval_public_5.py` 的 JSON `engine_snapshot`（含 14 个引擎文件 sha256_16 + rules 指纹），完整值见
> `Sanitized_Data/out/qa_public5/public5_report.json`（本地，gitignore）
>
> 安全约定：本报告只含统计/类型/数量/耗时/warning 摘要，不含实体原值或 OCR 文本；样本二进制与导出成品均在
> `%TEMP%` / `Sanitized_Data/out/`（gitignore），不提交、不外传。

## 1. 运行环境

| 项 | 值 |
|---|---|
| OS | Windows 10 Pro 10.0.19045（AMD64，6 逻辑核） |
| Python | 3.12.9（MSC v.1942 64-bit），`python -X utf8` |
| OCR | RapidOCR（onnxruntime 1.26.0），模型 `PP-OCRv6_det_small` / `PP-OCRv6_rec_small` / `ch_ppocr_mobile_v2.0_cls` |
| 其他库 | PyMuPDF 1.27.2.3、Pillow 12.2.0、zxing-cpp（Python 绑定） |
| 会话 TEMP | `C:\Users\Dante\AppData\Local\Temp\dsh-FdHJyu`（DSH 会话级临时目录；非系统默认 TEMP） |
| 样本目录 | `%TEMP%\yz_public5_testset`（实际：上述会话 TEMP 下） |
| 报告/成品 | `Sanitized_Data/out/qa_public5/`（本地，gitignore） |
| 评测命令 | `python -X utf8 app/tests/eval_public_5.py --dir "%TEMP%\yz_public5_testset" --out "D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\out\qa_public5"`（工作目录 `Sanitized_Data/`） |

## 2. 样本来源与取得（可复现）

样本规格见 `Sanitized_Data/app/tests/fixtures/public_5/MANIFEST.md`。本次 5 个样本已按 MANIFEST 重新获取：

**下载 3 个（sha256 与 MANIFEST 完全一致）**

| 文件 | 来源 URL | 许可 | bytes | sha256（前 16） | 校验 |
|---|---|---|---|---|---|
| `public_5_pdf_plos_0000308.pdf` | https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0000308&type=printable | CC BY 4.0 | 91,408 | `b52ca44dfd09d240` | ✅ 与 MANIFEST 一致 |
| `public_5_docx_calibre_demo.docx` | https://filesamples.com/samples/document/docx/sample1.docx | 站点未声明（仅本地测试，不提交） | 1,311,881 | `269329fc7ae54b3f` | ✅ 与 MANIFEST 一致 |
| `public_5_csv_iris.csv` | https://archive.ics.uci.edu/ml/machine-learning-databases/iris/iris.data | CC BY 4.0 | 4,551 | `6f608b71a7317216` | ✅ 与 MANIFEST 一致 |

**生成 2 个（内容全虚构，脚本见 `Sanitized_Data/out/qa_public5/_gen_public5_samples.py`，本地 gitignore）**

| 文件 | 生成方法 | bytes | sha256（前 16） |
|---|---|---|---|
| `public_5_qr_synthetic.png` | `zxingcpp.create_barcode("PUBLIC-5-TEST-QR", QRCode, ec_level="30%")` → `write_barcode_to_image(scale=8)` → PNG；回读解码校验通过 | 1,018 | `97bf5fb63eaa0cd9` |
| `public_5_scanned_annotated.pdf` | PIL 合成 A4 扫描风格页（200dpi、轻微噪声/高斯模糊）+ 虚构姓名/手机/邮箱/身份证/地址/金额 + PyMuPDF 文本批注 1 条与高亮批注 1 条 + `notes.txt` 附件 1 个 | 323,155 | `9974ad067d51f582` |

说明：`#4/#5` 为按 MANIFEST 第 27–34 行规格重新生成的样本，sha256 与 MANIFEST 记录的 2026-10-20 快照不同
（生成脚本未入库，无法逐字节还原；QR 1202B→1018B，扫描 PDF 3,694,380B→323,155B，类别/内容要素一致）；
因此两轮耗时不可直接横比（见 §6）。

下载命令说明：本会话沙箱中 `curl.exe`（schannel）报 `SEC_E_NO_CREDENTIALS`，改用 Python `urllib` 下载成功；
普通机器可直接 `curl -L --fail -o <file> <url>`，或用以下 Python 片段复现：

```powershell
python -X utf8 -c "import urllib.request; urllib.request.urlretrieve('https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0000308&type=printable', r'%TEMP%\yz_public5_testset\public_5_pdf_plos_0000308.pdf')"
```

## 3. 总览

- 样本数：**5**（PDF 2、DOCX 1、CSV 1、PNG 1）
- 解析成功：**5/5**；解析错误：**0**
- 导出成功：**5/5**
- verify 通过：**5/5**（residual=0、visual_residual=0、无隐藏数据/元数据残留）
- 实体合计：**9**（EMAIL:2、ORG:1、QRCODE:1、PHONE:1、CREDIT_LINE:1、ADDRESS:1、ID_CARD:1、SEAL:1，跨文件去重后 8 类；按文件见 §4）
- 决策合计：`surrogate:7`、`black:2`
- P0 缺陷：**0**
- 环境相关观察：内嵌图片文字 OCR 报 `Permission denied`（经排查为 DSH 沙箱临时目录 ACL 限制，
  **非产品缺陷**；产品逻辑在可写临时目录下 5/5 正常，见 §5.2）
- 总耗时：19,074 ms（run1，顺序执行）；最慢 `public_5_pdf_plos_0000308.pdf` 9,322 ms
- 复核运行：2026-10-21 01:40 原环境重跑一次，5/5 导出 + 5/5 verify 通过、0 解析错误，warning 与 run1 完全一致；
  2026-10-21 01:45 用「临时目录可写兼容层」重跑（排除沙箱 ACL 干扰，不改产品代码）：仍 **5/5 通过、0 P0，
  且无任何 `Permission denied` warning**，PLOS 的 2 条内嵌图 OCR 失败消失 → 印证该问题属沙箱环境限制。

## 4. 逐文件结果

| # | 文件 | 类别 | fmt | 页/块 | parse | detect | export | verify | 实体（类型:数量） | 导出产物 | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `public_5_csv_iris.csv` | CSV 表格（无 PII） | text | 1/750 | 4ms | 121ms | 2ms | ok (residual=0) | 0 | 4,551B | ✅ ok |
| 2 | `public_5_docx_calibre_demo.docx` | DOCX（内嵌图片/表格/脚注） | docx | 1/310 | 54ms | 57ms | 1,956ms | ok (residual=0) | 0 | 1,239,330B | ✅ ok（10 条导出 warning） |
| 3 | `public_5_pdf_plos_0000308.pdf` | PDF（文本层+内嵌图） | pdf | 5/78 | 4,669ms | 166ms | 52ms | ok (residual=0) | EMAIL:1、ORG:1 | 84,163B | ✅ ok（2 条内嵌图 OCR 失败 warning） |
| 4 | `public_5_qr_synthetic.png` | 图片（QR） | image | 1/0 | 300ms | 1ms | 3ms | ok (residual=0) | QRCODE:1 | 700B | ✅ ok（QR 已涂黑） |
| 5 | `public_5_scanned_annotated.pdf` | 扫描件+批注/附件 PDF | pdf | 1/11 | 2,055ms | 4ms | 715ms | ok (residual=0) | PHONE:1、EMAIL:1、CREDIT_LINE:1、ADDRESS:1、ID_CARD:1、SEAL:1 | 2,240,716B | ✅ ok（隐藏数据已剥离） |

### 4.1 逐文件明细

**#1 CSV（Iris）**
- `size=4551 sha256=6f608b71… text_chars=3800 blocks=750`；rules 176ms / verify 3ms / total 310ms
- 实体 0（数据本身无 PII，符合预期）；导出成品 4,551B
- warning：`CSV 有 1 行列数与最大列数（5）不一致，导出时按各行列数原样保留`（表尾空行，正常）
- 问题：**正常**。表格数据无敏感实体，不触发任何脱敏。

**#2 DOCX（calibre 演示文档）**
- `size=1311881 sha256=269329fc… text_chars=6907 blocks=310`；export 1,956ms / total 2,140ms
- 实体 0（文档内容无 PII，正则粗检 0 命中）
- 导出 warning 10 条（摘要）：已删除 1 个设置项、清空 5 项文档核心属性、删除 6 个隐藏部件、1 个外部链接关系告警、
  同步清理 2 条失效关系/2 条内容类型声明；**内嵌图片文字 OCR 失败 ×3（`Permission denied`，见 §5.2）**；
  `4 张内嵌图片未检出视觉/码类目标，已保留原样`；`内嵌图片文字 OCR：处理 3 张，命中 0 处，脱敏 0 张`
- 解析 warning 5 条：18 处超链接显示文字未纳入检测、1 个嵌套/内容控件表格未展开、脚注/尾注未纳入检测、
  4 张内嵌图片将自动扫描、18 处域代码未纳入检测（均为文档结构覆盖范围的已知限制，见 §5.3）
- 问题：**已知限制**（hyperlink/域代码/脚注/嵌套表不做文字检测）+ **环境相关观察**（内嵌图 OCR 失败 3 次，
  经排查为沙箱 ACL 限制，非产品缺陷，见 §5.2）。

**#3 PLOS PDF（学术论文）**
- `size=91408 sha256=b52ca44d… pages=5 blocks=78 text_chars=26515`；parse 4,669ms（含内嵌图片 P1.5 helper）/ verify 4,415ms / total 9,322ms
- 实体：EMAIL:1、ORG:1（论文公开作者机构/通讯邮箱，属预期检测；决策 surrogate:2）
- verify：residual=0、visual_residual=0；导出 84,163B
- 解析 warning 7 条：`照片较大，小脸可能漏检` ×5（图多页噪声）+ **内嵌图片文字 OCR 失败 ×2
  （`Permission denied`，见 §5.2）**
- 问题：**已知限制/性能**（parse+verify 双次 OCR 使 5 页 PDF 用时 9.3s）。

**#4 QR PNG**
- `size=1018 sha256=97bf5fb6… blocks=0`；total 479ms；实体 QRCODE:1（决策 black:1）；导出 700B
- verify：residual=0、visual_residual=0（涂黑后无二维码残留）
- warning：`第 1 页未识别到文字`（纯图无文字，正常噪声）
- 问题：**误报/噪声**（纯图无文字提示，不影响 verify）。

**#5 扫描+批注+附件 PDF（虚构内容）**
- `size=323155 sha256=9974ad06… pages=1 blocks=11 ocr_blocks=11 text_chars=186`；parse 2,055ms / export 715ms / verify 4,025ms / total 6,823ms
- 实体：PHONE:1、EMAIL:1、CREDIT_LINE:1、ADDRESS:1、ID_CARD:1、SEAL:1（虚构值全部被 OCR 检出并按规则脱敏）
- 导出 warning：`已剥离 PDF 隐藏数据：附件 1 个、批注 2 条`（隐藏数据剥离链路验证通过）
- verify：residual=0、visual_residual=0；导出 2,240,716B
- 问题：**已知限制/性能**（扫描件 verify 二次 OCR 4.0s）。

## 5. 问题分级

### 5.1 P0 缺陷：0 个
5/5 解析成功、5/5 导出成功、5/5 verify 通过（residual=0、visual_residual=0），无崩溃、无解析/导出失败，
无 PDF 隐藏数据/元数据残留。本次样本未发现 P0。

### 5.2 环境相关观察：内嵌图片文字 OCR `Permission denied`（已排查为非产品缺陷）

- **现象**：本会话 run1/run2 中，P1.5「内嵌图片文字 OCR 规则脱敏」对
  `%TEMP%\...\yz_media_text_*` / `yz_office_text_*\embedded.png` 报 `[Errno 13] Permission denied`：
  DOCX 导出 3 次（4 张内嵌图片）、PLOS PDF 解析 2 次（内嵌 figure）。两次独立运行位置/次数一致（非偶发）。
- **排查结论（环境限制，非引擎逻辑缺陷）**：本 QA 会话运行在 DSH 沙箱（workspace-write）下，该沙箱对
  **mode 0700 的目录**存在 ACL 限制——`os.mkdir(path, 0o700)` / Python `tempfile.mkdtemp()` 创建的目录
  「创建成功但随即不可写入/不可 chmod/不可删除」，而 `os.mkdir(path, 0o777/0o755)` 的目录完全可写。
  证据（全部本机最小复现）：
  - `os.mkdir(base, 0o777)`、`0o755`、`0o750`、`0o770`、`Path.mkdir()` 默认 → 目录内写文件 OK；
  - `os.mkdir(base, 0o700)`、`tempfile.mkdtemp()` → 目录内写文件 `PermissionError`（`icacls` 也无法读取该目录）；
  - 引擎 `media_text.py` 恰用 `tempfile.mkdtemp()`（mode 0700）→ 必然失败；SQLite/工作目录等由
    `Path.mkdir()` 创建的路径不受影响（因此真实测试集 17 文件无此 warning）。
- **反证（排除产品缺陷）**：用「临时目录可写兼容层」（`out/qa_public5/_envshim/sitecustomize.py`，仅本机 QA
  用，不改产品/测试代码）把 `tempfile.mkdtemp` 建目录模式改为可写后重跑：**5/5 导出 + 5/5 verify 通过、
  0 解析错误、0 P0（总 26.1s），DOCX 与 PLOS 的 `Permission denied` warning 全部消失**（DOCX 导出 warning
  10→7 且无失败项，PLOS 行 warning 7→5 仅剩"照片较大"噪声），P1.5 内嵌图片文字 OCR 正常执行
  （DOCX `内嵌图片文字 OCR：处理 3 张，命中 0 处，脱敏 0 张` 正常返回）。→ 产品逻辑正常，问题来自评测沙箱。
- **影响与建议**：
  - 本次两个公开样本图片无敏感文字（DOCX 命中 0 处、PLOS 为公开学术图表），未造成实际泄漏，导出/verify 均通过；
  - 该环境限制会让 P1.5 内嵌图片文字 OCR 在**本沙箱内**必然跳过，若后续在本环境跑这类用例，请按环境问题解读、
    不要记为产品 bug；建议在普通 Windows 环境（无此沙箱 ACL 限制）复核一次内嵌图 OCR 用例；
  - 若在真实用户 Windows 环境仍出现同类 `Permission denied`，再按「临时文件句柄/杀软锁」方向排查。
- **复现**：
  ```powershell
  cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
  # 原环境（会复现 Permission denied）
  python -X utf8 app/tests/eval_public_5.py --dir "%TEMP%\yz_public5_testset" --out "D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\out\qa_public5_rerun"
  # 加临时目录可写兼容层（消除环境限制，验证产品逻辑）
  $env:PYTHONPATH="D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\out\qa_public5\_envshim"
  python -X utf8 app/tests/eval_public_5.py --dir "%TEMP%\yz_public5_testset" --out "D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\out\qa_public5_nosandbox"
  ```
  最小复现/证据脚本（本地，gitignore）：`out/qa_public5/_repro_media_text.py`、`out/qa_public5/_trace_media_text.py`。

### 5.3 已知限制（不阻塞，P2/P3 优化候选）

1. **性能**：扫描件/含内嵌图片 PDF 的 parse 与 verify 各做一次 OCR（verify 二次 OCR 是主要成本），
   5 页 PLOS PDF 9.3s、单页扫描件 6.8s；建议 P2 缓存/收紧触发条件。
2. **DOCX 覆盖范围**：超链接显示文字、脚注/尾注、嵌套表格/内容控件表格、域代码（HYPERLINK/INCLUDETEXT）
   未纳入文字检测（10 条导出 warning 已明确标注；本次样本无 PII）。
3. **warning 噪声**：`照片较大，小脸可能漏检` 按页重复（PLOS 5 次）；纯图无文字时提示`未识别到文字`；
   建议 P3 去重/降噪。
4. **CSV**：表尾行列数与最大列数不一致（1 行）仅告警、按原样保留（正常）。
5. **格式覆盖**：文档内嵌图片文字只处理 PNG/JPEG/BMP/WEBP（EMF/WMF/SVG 跳过，属 helper 设计约束）。

### 5.4 误报 / 噪声
- `第 1 页未识别到文字`（纯 QR 图）：正常提示，不影响导出/verify → 噪声。
- 本次 5 个样本无视觉/码类目标误报（suspicious marks 均为空）。
- PLOS 论文中的 EMAIL/ORG 属真实公开学术信息，按产品策略做了保形替换（预期行为，非误报）。

## 6. 与 MANIFEST 历史结果对比

| 指标 | MANIFEST（2026-10-20，快照 ad4ded1） | 本次（2026-10-21，HEAD 7c553ab） |
|---|---|---|
| 导出成功 | 5/5 | **5/5** |
| verify 通过 | 5/5 | **5/5** |
| 解析错误 | 0 | **0** |
| P0 | 0 | **0** |
| PLOS PDF 耗时 | 13.4s | 9.3s |
| 扫描 PDF 耗时 | 7.9s | 6.8s |
| 新增观察 | — | 环境相关（非产品缺陷）：本沙箱 `tempfile.mkdtemp`（mode 0700）目录不可写，导致 P1.5 内嵌图 OCR 报 `Permission denied`；可写临时目录下复跑 5/5 通过、无该 warning |

耗时差异主要来自机器/负载与扫描样本重新生成（本次 323KB vs 快照 3.69MB），不宜视为性能回归/改善结论。

## 7. 结论与建议

- **功能门禁**：公开 5 样本 **5/5 解析成功、5/5 导出成功、5/5 verify 通过，P0=0**，与历史基线一致；QR 涂黑、
  扫描件 6 类虚构 PII 检出与脱敏、PDF 隐藏数据（批注 2 + 附件 1）剥离均验证通过。
- **环境提示（非产品缺陷）**：本 QA 会话的 DSH 沙箱下 `tempfile.mkdtemp`（mode 0700）目录不可写，导致 P1.5
  内嵌图文字 OCR 报 `Permission denied`；用临时目录可写兼容层复跑后 5/5 通过且 warning 消失，产品逻辑正常。
  后续若在本会话环境跑含内嵌图 OCR 的用例，请按环境限制解读；建议在普通 Windows 环境复核一次该用例。
- **建议**：P2 优先（扫描件/内嵌图 OCR 成本）> P3（warning 去重、DOCX 结构覆盖补全）；P1.5 内嵌图 OCR 的
  可靠性建议在普通 Windows 环境复核一次（本沙箱限制导致无法在本会话验证）。
- 真实测试集（17 个文件）结果见 `Sanitized_Data/docs/qa/real-testset-v1.1.0.md`（含 1 个 P0，需与本次公开集结论合并看）。
