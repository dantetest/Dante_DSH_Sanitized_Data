# PDF 隐藏数据清理（P0）：附件 / 批注 / 表单 / JavaScript / 缩略图

> 状态：已实现（P0） · 2026-10-20 · 负责人：researcher-geo（task-15）
> 代码：`app/server/yinzhan/parsers/pdf_parser.py`（解析统计 + 导出剥离 + 成品复检）
> 测试：`app/tests/test_pdf_hidden.py`（19 项）+ `app/tests/e2e.py`（5/5）+ `app/tests/regression.py`（**164 项硬检查全绿**，当前 QA 口径）
> 背景证据：`docs/research/08a-图像类敏感信息查漏补缺.md` C3、`docs/research/08b-文本表格与隐藏数据查漏补缺.md` F3/F4/T1/T14
> 上游决策：方案 A 已批准；PDF 隐藏数据默认剥离（D5），用户可显式 `keep_pdf_hidden` 保留并审批。

---

## 1. 接口约定（最终名，Lead/verify/jobs/api 已按此对接）

### 1.1 `doc.meta` 字段（解析时写入；pipeline.verify 按这些键复查）

| 键 | 类型 | 含义 | verify 口径 |
|---|---|---|---|
| `embedded_files` | `int` | PDF 附件数量 | `>0` 且未 `keep_pdf_hidden` → 拦截 |
| `pdf_annotations` | `int` | 全部页面注释数量（Text/FreeText/Highlight/Stamp/Link…） | 同上 |
| `pdf_forms` | `int` | 表单控件（widget）数量 | 同上 |
| `pdf_js` | `bool` | 是否存在 JavaScript（目录动作/名称树/页面动作/xref 全扫） | 同上 |

附加审计字段（verify 不拦截，仅本地展示/排查用）：

| 键 | 类型 | 含义 |
|---|---|---|
| `embedded_file_names` | `list[str]` | 附件名列表（不读内容） |
| `pdf_annotation_types` | `dict[str,int]` | 注释类型摘要（如 `{"Text":1,"FreeText":1}`） |

### 1.2 导出选项

| 选项 | 默认 | 行为 |
|---|---|---|
| `keep_pdf_hidden` | `False` | `False`=默认剥离附件/批注/表单/JS（并清理缩略图）；`True`=保留，写 warning，由 `pipeline.verify` 以同一 option 放行 |

透传链路（Lead 已完成，本实现无需再改）：`api.export` 读 body `keep_pdf_hidden` → `jobs.export(options)` → `pipeline.export(options)` → `parser.export(options)`；`jobs._export_to` 调 `pipeline.verify(..., options=options)`。

### 1.3 `doc.extra`（解析器私有，供审计/排查）

```python
doc.extra["pdf_hidden"] = {
    "before": {"embedded_files": 1, "pdf_annotations": 2, "pdf_forms": 1, "pdf_js": True},
    "removed": {"embedded_files": 1, "pdf_annotations": 2, "pdf_forms": 1, "pdf_js": 1, "thumbnails": 0},
    "kept": False,
}
```

> 建议 Lead 在 `jobs`/`audit` 里落一条审计（见 §6 待接点）；当前 audit 只记 media，不记该字段。

---

## 2. 实现要点

### 2.1 解析（`PdfParser.parse`）

- 打开 PDF 后统计 `_pdf_hidden_stats(pdf)`：
  - 附件：`pdf.embfile_names()`（不读内容）；
  - 批注：逐页 `page.annots()` 计数 + 类型摘要（不读 content）；
  - 表单：逐页 `page.widgets()` 计数；
  - JS：目录 `OpenAction/AA`、`Names/JavaScript` 名称树、页面 `AA`，再对 xref 全表做 `/JavaScript` 与 `/JS` 正则扫描（上限 `YINZHAN_PDF_JS_SCAN_MAX`，默认 50000 个 xref）。
- 有隐藏数据时写 warning：
  `检测到 PDF 隐藏数据（附件 1 个（secret.txt）、批注 2 条、表单字段 1 个、JavaScript），导出时将默认剥离；如需保留请显式使用 keep_pdf_hidden=True`
- 统计异常只写 warning，不影响正文解析。

### 2.2 导出（`PdfParser.export`）——顺序：**先剥离隐藏数据，再 redaction，最后清元数据**

1. 打开源 PDF；`keep_pdf_hidden=True` 时跳过剥离并写风险 warning；
2. `_strip_pdf_hidden(pdf, warnings)`：
   - 附件：逐个 `pdf.embfile_del(name)`（内容不再打包）；
   - 表单：逐页清 `field_value` → `widget.update()` → `page.delete_widget()`，随后 `catalog/AcroForm = null`（字段字典/值/动作一起移除）；`is_form_pdf` 变 False；
   - 批注：逐页 `page.delete_annot()`（含 Link 注释，链接会一并消失）；
   - JavaScript：清目录 `OpenAction`/`AA`、`Names/JavaScript`、页面 `AA`，其余孤立 JS 动作对象清空（保护 Catalog/Page 对象，必要时写 warning）；
   - 附加清理（本轮顺带做的泄漏面）：页面 `/Thumb` 缩略图（可能保存未脱敏画面）+ 目录 `PageMode=/UseAttachments` 改 `UseNone`；
3. 再执行原有 redaction + 替换文字（与隐藏数据清理互不干扰：批注/控件已先删除）；
4. `set_metadata({})` + `del_xml_metadata()`；
5. `pdf.save(..., garbage=4, deflate=True)`；
6. **解析器侧成品复检**：重新打开成品统计隐藏数据，任一 >0 → 删除成品（同文件导出除外）并 `ExportError: 导出后 PDF 隐藏数据复检失败：…`；
7. 写入 `doc.extra["pdf_hidden"]` 供审计，返回 `ExportResult`。

### 2.3 安全语义

- 默认**剥离**，`keep_pdf_hidden` 是显式逃生门（业务附件确实需要时用），且保留时写 warning；
- `keep_pdf_hidden` **不能绕过文本残留复检**：若附件/批注/表单里的原文同时也进了 PDF 文本层（例如 FreeText/控件值会被 `get_text()` 读出），`pipeline.verify` 仍会因“原文残留”拦截——这是预期且正确的安全行为（不能一边保留敏感原文一边声明已脱敏）；
- 审计口径：保留动作必须有痕迹（Lead 加 audit 字段，见 §6）。

---

## 3. 实测数字（2026-10-20，本机 Windows + Python 3.12.9 + PyMuPDF 1.27.2）

### 3.1 主夹具（附件 1 / 批注 2 / 表单 1 / JS 有，均含虚构 PII + 唯一 marker）

| 阶段 | 结果 |
|---|---|
| 解析 `doc.meta` | `embedded_files=1`、`embedded_file_names=['secret.txt']`、`pdf_annotations=2`、`pdf_annotation_types={"Text":1,"FreeText":1}`、`pdf_forms=1`、`pdf_js=True`；warning 一条（含 keep 提示） |
| ai 导出 | 成品：`embfile_count=0`、`annots=0`、`widgets=0`、`is_form_pdf=False`、JS 扫描 False；marker 全无 |
| human 导出 | 同上 |
| 成品文本 | 可见正文手机号已 redaction（残留 0）；隐藏数据里的 5 个虚构手机号均不在 `page.get_text()` |
| `pipeline.verify` | `verified=True, residual=0, pdf_hidden={}`（ai/human 均通过） |
| 导出 warning | `已剥离 PDF 隐藏数据：附件 1 个、批注 2 条、表单字段 1 个、JavaScript` |
| `doc.extra["pdf_hidden"]` | `before`/`removed`/`kept` 三键齐全，可审计 |

### 3.2 keep_pdf_hidden 与复检

| 用例 | 结果 |
|---|---|
| `keep_pdf_hidden=True`（隐藏数据无 PII） | 成品 `embedded_files=1`、`pdf_forms=1`、`pdf_js=True`、注释保留；warning 含“保留…存在外发泄漏风险”；**verify 不带 option 拦截**（`PDF 隐藏数据未清理：…`），**带 option 放行**（`verified=True`） |
| `keep_pdf_hidden=True`（隐藏数据含 PII） | 即使带 option，verify 仍因“原文残留”拦截（预期，见 §2.3） |
| 模拟清理失效（monkeypatch `_strip_pdf_hidden` 空操作） | 导出抛 `ExportError: 导出后 PDF 隐藏数据复检失败：…`，成品被删除（不留半成品） |
| 批注盖在正文手机号上 | 导出后 `annots=0` 且正文手机号已 redaction |
| 页面缩略图夹具 | `/Thumb` 键变 `null`，缩略图 marker 不残留 |
| 既有 `samples/委托代理合同.pdf` | 解析隐藏数据全 0、无相关 warning；导出/verify 不变（`pdf_hidden={}`） |

### 3.3 门禁

| 门禁 | 结果 |
|---|---|
| `app/tests/test_pdf_hidden.py` | **19/19 通过**（4.7s，0 skipped） |
| `app/tests/e2e.py` | **5/5 通过、残留 0**（exit 0） |
| `app/tests/regression.py` | **164 项硬检查全绿**（exit 0；主路径 verify 已带 `pdf_hidden={}` 字段） |

### 3.4 测试命令

```powershell
# 域内单测（19 项）
python -X utf8 app\tests\test_pdf_hidden.py

# 红线 e2e（必须 5/5、残留 0）
cd app\server
$env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py

# 红线回归（必须全绿）
python -X utf8 app\tests\regression.py

# 保留夹具人工查看
$env:YINZHAN_WRITE_FIXTURES='1'; python -X utf8 app\tests\test_pdf_hidden.py
```

夹具见 `app/tests/fixtures/pdf_hidden/README.md`（PII 全部虚构，二进制不入库）。

---

## 4. 与 08a/08b 验收条款对照

| 验收条款（08a C3 / 08b F3） | 结果 |
|---|---|
| 解析记录附件/批注/表单数量与名称（不读内容） | ✅ `embedded_files`/`embedded_file_names`/`pdf_annotations`/`pdf_annotation_types`/`pdf_forms`/`pdf_js` |
| 导出默认剥离附件（`embfile_del`） | ✅ 成品 `embfile_count=0` |
| 删除批注 | ✅ 成品 `annots=0`（含盖在正文上的批注） |
| 表单清值/删除（不破坏正文） | ✅ 控件删除 + `AcroForm=null`；正文不变（redaction 前先删） |
| JS 删除 | ✅ 目录 OpenAction/AA/名称树/页面 AA/孤立动作对象；成品扫描 False |
| Verify 重解析成品 0 附件/0 注释 | ✅ Lead 的 `pipeline.verify` 已实现 `pdf_hidden` 检查；本解析器导出后自检兜底 |
| 保留选项 + 审计 | ✅ `keep_pdf_hidden` 保留并 warning；audit 字段建议 Lead 补（§6） |
| e2e 5/5 与 regression 不退化 | ✅ e2e 5/5；regression 164/164 |

---

## 5. 行为矩阵

| 场景 | 默认（keep=False） | keep=True |
|---|---|---|
| 附件（含 PDF 包内任意文件） | `embfile_del` 删除，内容不再打包 | 保留（warning） |
| 批注（Text/FreeText/Highlight/Stamp/Link…） | 全删（链接annot一并删除，链接目标失效） | 保留（warning） |
| 表单控件 | 删 widget + `AcroForm=null`，字段值/字段文字/字段动作全消失 | 保留（warning） |
| JavaScript | 目录/名称树/页面/孤立动作对象清理；疑似目录/页面对象只 warning 不盲删 | 保留（warning） |
| 页面缩略图 `/Thumb` | 删除（可能残留未脱敏画面） | 保留（不主动警告） |
| `PageMode=/UseAttachments` | 改为 `/UseNone` | 保留 |
| 导出后自检 | 重新打开成品，0/0/0/False 才放行，否则删成品 + `ExportError` | 跳过 |
| pipeline.verify | `pdf_hidden={}` 通过 | 需带 `keep_pdf_hidden=True` 才放行；隐藏数据含原文仍会被文本残留拦截 |

---

## 6. 需要 Lead 接的点

1. **UI/文案（Lead/web）**：导出对话框需要说明“PDF 附件/批注/表单/JS 默认剥离”；`keep_pdf_hidden` 是高级选项，勾选时应二次确认并提示“保留内容可能含原文、外发风险”。目前 `api.py` 已接收该参数，前端是否发送需 Lead 决定。
2. **审计（Lead/jobs/audit）**：建议 `audit.append(..., extra={"pdf_hidden_removed": {...}, "keep_pdf_hidden": bool})`，把保留动作和清理计数写进审计（当前 audit 只记 media，不计隐藏数据）。解析器已提供 `doc.extra["pdf_hidden"]` 可直接取。
3. **verify（已完成）**：Lead 已在 `pipeline.verify` 实现四键检查 + `keep_pdf_hidden` option，本任务实测通过；无需再改。
4. **红线/契约文档（Lead）**：`docs/red-lines.md` 的 PDF 元数据/出口自检条目可补充“附件/批注/表单/JS”，并在 CONTRACT §7 出口自检里加一行；本任务不改这些文件。
5. **`has_cleanup_targets`（Lead 可选）**：若希望“只有隐藏数据、没有文字实体”的 PDF 也能导出（当前会因无实体被 jobs 拦截），可把 `meta["embedded_files"]/["pdf_annotations"]/...` 计入清理目标；当前实测样本都有实体，未改 Lead 文件。

---

## 7. 剩余风险与边界（诚实声明）

1. **删批注会丢审阅意见/Link 注释**：默认剥离是安全优先；需要保留的业务审阅信息用 `keep_pdf_hidden=True`（需用户确认 + 审计）。
2. **删表单控件会丢字段内文字/外观**：字段值不再可填/可读；正文文字不受影响（正文在 redaction 前不动）。
3. **JS 全表扫描上限**：`YINZHAN_PDF_JS_SCAN_MAX`（默认 50000 xref）；超大 PDF 超出上限时仅检测目录/名称树/页面动作，极端情况下可能漏检孤立 JS 对象（会在 warning 里体现清理失败才会拦截）。
4. **XFA/动态表单**：PyMuPDF 的 `widgets()` 主要覆盖 AcroForm；极少数 XFA 动态表单可能不在计数内（P1 需要专门评估，当前 PDF 测试集未覆盖）。
5. **加密 PDF**：仅支持空密码/已解密场景（与现有解析行为一致）；带密码 PDF 仍按原逻辑报错。
6. **数字签名/签名域**：清理会使既有签名失效（导出破坏性重写本来就会导致失效）。
7. **JS 内容本身的执行**：本产品不执行 PDF JS；这里只做“移除”，不解释语义。
8. **页面缩略图**：本轮顺带剥离；若业务强依赖缩略图，可在 P1 提供开关。
9. **附件格式**：附件一律整包删除，不做内容检测（附件可能是原始扫描件/整包客户数据——删除是对的，但用户需知情）。
10. **话术**：清理隐藏数据 ≠ 匿名化；附件/批注/表单内容若被保留（keep）或截图传播，仍可能泄露原文。

---

## 8. 变更记录

| 日期 | 变更 | 负责人 |
|---|---|---|
| 2026-10-20 | 新增 PDF 隐藏数据解析统计/默认剥离/成品复检/缩略图清理；新增 `test_pdf_hidden.py` 19 项；e2e 5/5、regression 164/164 全绿 | researcher-geo |
