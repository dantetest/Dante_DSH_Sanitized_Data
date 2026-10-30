# AI 回件还原（restore）· 实现契约

> 状态：已实现（2026-10-29）。后端 `app/server/yinzhan/restore.py`、
> HTTP API `app/server/yinzhan/api.py`、界面 `app/web/restore.*`、
> MCP `restore_sanitize_file` 已落地；测试见 `app/tests/test_restore.py` /
> `test_mcp_restore.py`。
> 目标：AI 拿「换名版」干完活交回一个新文件；本机用同一 job 的「假值→真值」
> 映射，把回件里的假值换回真值，输出 `*_还原.*` + 还原报告。全程本机、不联网。

## 1. 现状与边界

- 已有：换名版导出时把映射 AES-GCM 加密写入 `data/vault/<job_id>.bin`
  （`yinzhan/vault.py`，密钥 `data/keys/master.key`）；文本还原
  `rehydrate_sanitize_text`（MCP）与 `rehydrate.py`（CLI）。
- 新增：**文件级回件还原**（本功能）。只还原「被替换过的文本实体」：
  - 能还原：回件文本/表格/正文/页眉页脚/脚注批注里的假值（精确 + 变体匹配）；
    Excel 的纯数字金额单元格也按 AMOUNT 映射还原（见 §2）。
  - 尽力还原：PDF 文本层（重绘，版式可能位移）；图片/扫描件（OCR 文本 +
    可选的图片重绘）。
  - **不能**还原：涂黑区域（人脸/印章/手写/整图）、图片内文字、AI 自己
    改写/重算/编造的内容。
- 金额：等比缩放过的假金额，只有「原样出现」的才能直接换回；AI 自己算出
  的合计/税额不在映射表里，报告里要列为需人工核对（P1 再做缩放因子反算）。

## 2. 映射与匹配

- 映射来源：`vault.list_vault()` 逐个 `load_mapping()` 得到 `meta.name`、
  `meta.seal`、`saved_at`、`map{etype: {原值: 假值}}`。
- 去重：同一假值可能出现在多个 etype（如 RECORD_NO / MEDICAL_RECORD），
  按假值去重；同一假值映射到不同原值 = 冲突，保留首个 canonical 并告警
  （近似实体归并时可能发生，报告提示人工核对）。
- 长串优先、单遍扫描不级联；对常见变体加别名：
  - AMOUNT：`¥`/`￥`、千分位、去空格、裸数字（≥4 位或有小数）
  - USCC/ID_CARD：大小写 X
  - PHONE/BANK/ACCOUNT：连字符/空格变体、纯数字
  - CASE_NO / 发票号 / 交易号等结构化值：空白压缩、中文/全角括号归一
  - 带标签值（`账号:…`、`住院号：…`、`住院号 …`）：额外生成「去标签核心号码」
    派生匹配项；替换时用核心真值，避免出现「住院号 住院号：ZY…」这种前缀重复。
    派生项只参与匹配，不计入映射实体数。
  - PERSON/ORG/ADDRESS/EMAIL：NFKC 归一化后的等价写法
- Excel 纯数字单元格：按 AMOUNT 映射建立「假金额数值→真金额数值」表（元/万元
  两种单位），只处理无公式的数值单元格；公式结果与图表不重算。
- 回件上传后对每个映射打分：`hits`（命中处数）、`matched`（命中不同假值数）、
  `coverage = matched / pairs`；回件文件名与映射原名一致时 `name_match=true`。
  排序：`name_match` → `coverage` → `hits`，取前 8。全部 0 命中时返回最近
  5 个映射供手动选择。

## 3. HTTP API（同源，JSON）

错误统一 `{"error": {"code": "...", "message": "中文"}}`，HTTP 400/404/413/500/503。

### 3.1 `GET /api/restore/mappings`

```json
{
  "mappings": [
    {
      "job_id": "j_ab12cd34",
      "name": "D01_invoice.pdf",
      "seal": "20261029-0001",
      "saved_at": "2026-10-29T20:15:38+08:00",
      "pairs": 9,
      "by_type": {"ORG": 2, "AMOUNT": 4, "PERSON": 1, "USCC": 2}
    }
  ],
  "count": 5
}
```

按 `saved_at` 倒序。`pairs` 为去重后的假值数；`by_type` 用人话标签（见 §6）。

### 3.2 `POST /api/restore/upload`

原始字节 body（与 `/api/upload` 一致），`X-Filename` 为 URL 编码的文件名。

```json
{
  "restore_id": "r_ab12cd34",
  "name": "回件.docx",
  "format": "docx",
  "bytes": 12345,
  "sha256": "…",
  "chars": 2048,
  "preview": "回件提取的前 800 字（本机显示）…",
  "candidates": [
    {"job_id": "j_x", "name": "D01_invoice.pdf", "seal": "20261029-0001",
     "saved_at": "…", "pairs": 9, "hits": 12, "matched": 8,
     "coverage": 0.89, "name_match": false}
  ],
  "candidates_total": 3,
  "notes": ["图片：将 OCR 出还原文本；版式无法原样回写"]
}
```

- `format` ∈ `txt|md|csv|json|docx|xlsx|pptx|pdf|image|unknown`。
- 图片/扫描件会走 OCR，可能耗时数秒；前端需 loading 态。
- `candidates` 最多 8 个，可能为空数组；`coverage` 为 0–1 小数；
  `name_match` 表示回件文件名与映射原名一致（用于预选）；
  `candidates_total` 为命中的映射总数（未截断）。

### 3.3 `POST /api/restore/run`

```json
{"restore_id": "r_ab12cd34", "job_id": "j_ab12cd34"}
```

```json
{
  "restore_id": "r_ab12cd34",
  "job_id": "j_ab12cd34",
  "job_name": "D01_invoice.pdf",
  "seal": "20261029-0001",
  "format": "docx",
  "strategy": "docx/ooxml-text",
  "counts": {"replaced": 12, "unique_replaced": 8, "unmatched": 0,
             "scan_pages": 0, "warnings": 2},
  "by_type": {"PERSON": 2, "ORG": 3, "AMOUNT": 4},
  "replacements": [
    {"fake": "西藏新华和德发展有限公司", "orig": "西藏大唐科技发展有限公司",
     "etype": "ORG", "label": "机构", "count": 2}
  ],
  "warnings": ["3 处真值比假值长，版式可能被挤压", "第 2 页无文本层，未回写"],
  "outputs": [
    {"kind": "main", "name": "回件_还原.docx", "bytes": 12000, "sha256": "…",
     "url": "/api/restore/file?rid=r_ab12cd34&kind=main"},
    {"kind": "text", "name": "回件_还原文本.txt", "bytes": 1000, "sha256": "…",
     "url": "/api/restore/file?rid=r_ab12cd34&kind=text"}
  ],
  "elapsed_ms": 321
}
```

- `outputs` 至少 1 项；`kind=main` 是主成品，`kind=text` 是扫描件/图片的
  还原文本侧车（可没有）。纯扫描件/图片没有可回写的版式文件时，文本侧车会
  被提升为 `kind=main`（主产物可能是 `.txt`）。
- 报告含真实值，只在本机展示；**前端不得把报告内容发往任何外部服务**。
- 错误码：`bad_request`、`unsupported_format`、`mapping_not_found`、
  `no_match`（回件里没有该映射的假值）、`restore_failed`、`ocr_unavailable`、
  `too_large`。

### 3.4 `GET /api/restore/file?rid=<rid>&kind=main|text`

二进制下载，`Content-Disposition` 带 `outputs[].name`。

## 4. 前端页面（app/web/restore.html + restore.js + restore.css）

- 路由 `/restore`（`api.PAGE_ALIASES` 已注册），控制台首页/顶栏有「回件还原」
  入口；交付弹窗里也有「AI 交回文件后 → 回件还原」链接。
- 视觉复用控制台（`style.css` 的纸张/墨色/印章红变量），顶栏与控制台一致。
- 三步式：
  1. **投放**：拖拽/点击选择回件；隐私提示「只在本机还原，不上传」。
  2. **选映射**：候选卡片（原名 / 封条号 / 时间 / 实体数 / 覆盖率 / 命中数），
     可展开「全部映射」搜索选择；默认预选 coverage 最高者。
  3. **结果**：汇总（已还原处数/假值数/告警数）、按类型统计、替换明细表
     （类型/假值/真值/次数）、告警列表、下载按钮（主成品 + 文本侧车）、
     「再还原一份」。
- 状态：加载中 / 空映射 / 上传中 / 匹配完成 / 还原中 / 完成 / 失败。
- 安全：所有服务端字符串用 `textContent` 渲染（禁止 innerHTML 拼接）；
  报告里出现真实值，页面要有「仅本机查看、勿回传云端模型」提示。
- 不引入外部 CDN/字体；不修改 `style.css`/`site.css`/`app.js`/服务端文件。

## 5. 输出与审计

- 成品写入 `data/out/`，命名 `<回件名>_还原<ext>`（主）/`<回件名>_还原文本.txt`
  （侧车），重名自动加 `(2)`。
- `audit.append(job_id=<映射 job>, doc_name=<回件名>, doc_hash=<回件 sha256>,
  fmt=<回件格式>, counts={replaced, unique_replaced, by_type, warnings},
  rule_pack="restore", mode="restore", out_hash, out_name)`。
- 回件临时副本放 `data/work/restore/<rid>/`，退出时随 `purge_runtime_dirs()` 清理。

## 6. 类型标签

复用 `jobs._TYPE_LABEL`，并补了映射库里常见的旧类型：
USCC→统一社会信用代码、ACCOUNT→银行账号、RECORD_NO→住院号/病历号、
MEDICAL_RECORD→病历号/住院号、LICENSE→许可证号、INVOICE_CODE/NO→发票代码/号码、
TXN_NO→交易号、CREDIT_LINE→授信额度、SHEET_NO→图幅号、CONTROL_POINT→控制点。
未知 etype 显示原文。

## 7. MCP 工具（Agent 通道）

`restore_sanitize_file`，参数 `path`、`job_id`、可选 `out_dir`、
`include_originals`（默认 false）。语义：

- 调 `yinzhan.restore.run_path()` 直接对本地回件还原（不经上传会话），写
  `data/out/` 并写审计；返回 `counts / by_type / warnings / outputs(路径)`。
- **默认不返回 fake→orig 明细**：MCP 客户端可能是云端模型；只有
  `include_originals=true` 才带 `replacements`（本机可信调用方）。
- 还原固定在本机 MCP 进程内执行（不经 Web `/api/restore/*`）。hybrid/HTTP
  后端模式下要求 MCP 进程与 Web 进程共享同一 `YINZHAN_DATA_DIR`（安装版天然
  同目录；源码/dev 请显式对齐），否则映射库/输出目录会各看各的。
- DSH 插件的 `TOOL_KEYS` 已收录 `restore_sanitize_file`，会显示「回件还原」
  工具卡；未授权时与其它 L2 一样受 `mcp` 功能位门禁。

## 8. 测试与验收（本机记录）

`data/vault` 现有 99 条映射，其中 52 条含可还原文本实体（其余是只有地理
参数的 TIF 任务，自动跳过）。示例：

- `j_c6e1849b` D01 发票：USCC/AMOUNT/PERSON/ORG 共 9 对
- `j_a75235cd` D08 判决书：16 对
- `j_94366e4d` D12 病历 docx：8 对（含“住院号：…”带前缀变体）
- `j_8f9108ed` D07 合同：23 对

可用 `ai_view_test/ai2ai_20261029/blind/*` 的换名版成品模拟「AI 回件」。

实测（2026-10-29，取上述换名版当回件）：

| 回件 | 结果 |
|---|---|
| D12 病历 docx | 8 对映射、9 处替换、残留假值 0、跨 run 人名/页眉/表格均还原 |
| D01 发票 PDF | 9 对、11 处替换、残留 0；5 处真值较长自动缩字 |
| D08 判决书 PDF | 16 对、30 处替换、残留 0 |
| D11 名片 PNG | OCR 6 处全部还原，输出重绘图 + 还原文本；字体/版式为近似 |
| txt/csv/json | 编码（UTF-8/GB18030）、单元格值、JSON 值均正确 |

自动测试：

- `python -X utf8 app/tests/test_restore.py` —— 22 项：映射/变体/派生核心写法/
  空白与括号变体/xlsx 数值单元格/不级联/txt/GB18030/csv/json/docx（跨 run +
  页眉页脚 + 表格）/xlsx/pdf 文本层/图片/扫描 PDF/no_match/低质量映射告警/
  HTTP API（上传→还原→下载→审计）/页面可达。
- `python -X utf8 app/tests/test_mcp_restore.py` —— 12 项：工具 schema/默认
  不返回原值/include_originals/缺参/映射不存在/不支持格式。
- `python -X utf8 app/tests/test_mcp_v2.py` —— 37 项既有 MCP 回归全绿。
- 界面：Playwright（系统 Edge）实测「上传 D12 → 自动预选 → 还原 9 处 →
  明细 8 行 → 下载主成品 34606B」，console 0 error。
- **端到端联测（2026-10-29）**：测试集 4 份 PDF/Word → 产品脱敏 → 4 个盲读子代理
  产出 docx/xlsx/pdf 回件 → 产品回件还原 → 与原文对照：D01/D12 100%、D07 19/19、
  D08 16/16，残留假值全部 0。发现并修复「带标签核心号码漏还原/前缀重复、案号空格
  变体、Excel 数值金额单元格」等问题；详见
  `docs/qa/restore-e2e-20261029.md`。

### 已知边界（P1 起）

- 金额缩放因子反算（AI 自己算出的合计/税额）尚未做，报告只提示人工核对。
- 图片/扫描件的重绘是近似（字体、换行、跨行实体可能漏还原）。
- Word/Excel/PPT 里图片、图表、嵌入对象中的文字不处理；Excel 公式结果不重算。
- PDF 长真值会缩字；跨行被拆开的实体可能匹配不到。
- 映射库没有 TTL/跨文档组映射；同一真值在不同 job 可能是不同假值，按
  job 分别还原。
- 映射来源本身可能有 OCR/版面误检实体（如人名=公章）或近似实体归并；还原会
  忠实换回这些“原值”，报告会给出低质量映射告警，需上游脱敏侧过滤。
- AI 基于假值做的推算/结论（身份证推生日、假金额算合计、引用局部区号等）
  即使值被换回，推理关系也可能不成立；报告已提示人工复核。
- 上游修复（脏实体/归并过合并/伪值一致性/PDF 阅读顺序）的研究方案见
  [`docs/plans/07-AI视图上游质量修复方案.md`](../plans/07-AI视图上游质量修复方案.md)；
  首轮落地与 E2E 结果见
  [`docs/qa/upstream-guard-20261029.md`](../qa/upstream-guard-20261029.md)，
  第二轮一致性结果见
  [`docs/qa/consistency-e2e-20261030.md`](../qa/consistency-e2e-20261030.md)。
- 还原开头会重新检查映射一致性：旧 job 若存在「同假值多原值/跨类别归并/超大簇」，
  报告会显式提示（只含计数，不含原值）。
- 回件 PDF 自带 `china-s` 子集字体时，还原用独立系统 CJK 字体并 subset，
  避免真值插入后 `get_text` 变成 `\x00`（2026-10-30 D08 实测）。

