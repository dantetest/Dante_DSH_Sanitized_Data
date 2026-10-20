# CSV/TSV 列语义 + 多级/合并表头继承 · P1-B 实现说明

> **状态**：实现稿 · 2026-10-20 · 未 push（按用户要求）
> **任务**：`task-32`（P1-B 第二项）+ `task-38`（P1.5：CSV/TSV 原编码/BOM/换行保留）；上游设计见
> [`docs/research/08b-文本表格与隐藏数据查漏补缺.md`](../research/08b-文本表格与隐藏数据查漏补缺.md) §3.5 E1/E5/E6 与 §4.5
> **红线**：遵守 [`docs/red-lines.md`](../red-lines.md)（E4 扩展点）。只改
> `parsers/text_parser.py`、`detectors/table_det.py`，测试/fixtures/文档随行；
> 未改 pipeline / jobs / api / registry / 其它规则包 / 前端。

---

## 1 交付清单

| 路径 | 类型 | 说明 |
|---|---|---|
| `app/server/yinzhan/parsers/text_parser.py` | 修改 | `.tsv` 支持；CSV/TSV 用 csv 模块解析成单元格 Block（ref={row,col,sep,line}）+ page.ref 保留 dialect/原行 + doc.meta 标记；导出新增 delimited 分支（按原分隔符/引号重写、行/列数不变）；**P1.5：按原编码/BOM 写回（UTF-8-SIG/UTF-16/GB18030），未知/不可编码字符 warning + 回退 UTF-8** |
| `app/server/yinzhan/detectors/table_det.py` | 修改 | 支持 CSV/TSV 的行列 ref；多级/合并表头继承（表头块识别 + 纵向/向左继承 + 表头词守卫）；新增 `inherit_merged`/`max_inherit_span` 选项 |
| `app/tests/test_csv_table.py` | 新增 | **8 个测试组 / 115 项断言**（解析、编码/BOM/换行保留矩阵、列级覆盖、两级/合并表头、无表头退化、导出/复检、规则 scope） |
| `app/tests/fixtures/csv_table/` | 新增 | 虚构客户名单 CSV/TSV、两级表头、合并表头、纵向继承、无表头夹具 + `expected.json` |
| `docs/impl/csv-table.md` | 新增 | 本文件 |

Lead 已接的点（本任务依赖，已完成/进行中）：

- `rules/table-cn.yaml` 11 条规则 `scope` 已由 Lead 增加 `text`（`[xlsx, docx, text]`）；
- 前端/README/CONTRACT/格式清单由 Lead 更新（`.tsv`、CSV/TSV 展示）；
- qa 在 `app/tests/run_all.py` 增加 `test_csv_table.py`。

---

## 2 解析器（text_parser）

### 2.1 行为

| 扩展名 | 解析方式 | Block | doc.fmt | doc.meta |
|---|---|---|---|---|
| `.txt/.md/.json/.log` | 原有按行切分（**行为不变**） | 一行一个，`ref={line,eol}` | `text` | `{encoding, line_count}` |
| `.csv` | `csv` 模块（自动嗅探分隔符：`, ; \t \|`，失败按首行计数兜底） | **每个非空单元格**一个，`ref={row,col,sep,line}` | `text`（不变，兼容既有 text 规则） | `{encoding, line_count, delimited:"csv", delimiter, quotechar, rows, cols, final_newline, raw_rows_ok}` |
| `.tsv` | `csv` 模块（强制 `\t`） | 同上 | `text` | 同上（`delimited:"tsv"`） |

- **选型**：`document.fmt` 保持 `"text"`（Lead 决策 A）——既有 text-scope 规则（common/pipl/trade/legal/…）在 CSV/TSV 上继续逐格生效；`table_column` 只认带 `row/col` ref 的块，所以 `.txt/.md` 天然不触发，不需要 EXT_TO_FMT。
- **原行保留**：`page.ref = {delimited, delimiter, quotechar, eol, final_newline, raw_rows, row_cols, rows}`。`raw_rows` 在"每条记录恰占一行"时保留原始行文本（含原引号/空格），导出时未修改的行逐行原文写回。
  - 引号内换行会使行与记录不对应 → `raw_rows_ok=False`，导出统一走 writer 重写（语义等价、列数不变）。
- **编码（P1.5 已保留）**：沿用 `_decode_text` 解码顺序（UTF-8 BOM → UTF-16 BOM → UTF-8 → GB18030 → 替换解码）；CSV/TSV **导出按解析阶段记录的原编码写回**（见 §2.2b），`.txt/.md` 导出仍为 UTF-8（本任务不改变普通文本行为）。
- **异常兜底**：`csv.Error` 时 warning + 退回按行解析（不抛给用户）。
- **列数不齐**：`row_cols` 记录每行列数，导出按各行原列数保留（不补齐/不截断）。

### 2.2 导出（delimited 分支）

`TextParser.export` 检测到 `doc.meta["delimited"]` 时进入 `_export_delimited`：

1. 从单元格 Block 重建网格，按实体 span 逐格替换（black → `█` 覆盖原长度；surrogate/mask → replacement；drop → 空单元格；keep → 不动）；
2. **值级传播**：检测范围外其它单元格中的同值也按同一决策覆盖（防残留）；
3. 逐行写回：
   - 该行无修改且 `raw_rows` 可用 → 写**原文行**（保留引号/空格/大小写）；
   - 否则 → 用 `csv.writer(delimiter=原分隔符, quotechar=原引号, QUOTE_MINIMAL)` 重写，列数=原列数；
4. 行尾拼接：保留原 `eol`（`\r\n` / `\n`）与"文件末尾是否有换行"；
5. **按原编码写回**（`_encode_output_preserving`）：`utf-8-sig` 保留 BOM、`utf-16`
   保留 BOM、`gb18030`（GBK 超集）按原编码写回；未知编码或目标编码无法表示的字符 →
   **写 warning 并回退 UTF-8**（不静默改编码）；
6. `pipeline.verify` 重解析成品做残留复检（`residual=0` 才允许交付）。

### 2.2b 编码 / BOM / 换行保留矩阵（P1.5 实测）

`doc.meta` 解析阶段新增/补充：`encoding`（原始编码名）、`encoding_output`（导出目标）、
`encoding_source`（`bom`/`content`/`fallback`）、`bom`（bool）、`newline`（`CRLF`/`LF`）。
`page.ref` 继续保留 `eol` / `final_newline` / `raw_rows`。

| 输入（解析识别） | meta 记录 | 导出行为（实测） |
|---|---|---|
| UTF-8 无 BOM | `encoding=utf-8`, `bom=False`, `source=content` | UTF-8 无 BOM，无额外 warning |
| UTF-8 with BOM | `encoding=utf-8-sig`, `bom=True`, `source=bom` | UTF-8 **保留 BOM**（`EF BB BF`） |
| UTF-16（带 BOM） | `encoding=utf-16`, `bom=True`, `source=bom` | UTF-16 + BOM（Python `utf-16`，LE；BE 输入归一为 LE+BOM） |
| GBK | `encoding=gb18030`, `bom=False`, `source=content` | **GB18030 写回**；GBK 可表示字符字节与原文一致（GB18030 是 GBK 超集） |
| GBK + CRLF | 同上 + `newline=CRLF` | 编码、BOM、`\r\n` 同时保留 |
| 引号内换行（任意编码） | `raw_rows_ok=False` | writer 重写整表，行/列数不变、单元格内容保留 |
| 列数不齐（`row_cols=[3,2]`） | `row_cols` 保留 | 导出行列结构保持 `[3,2]` |
| 未知编码 / 解码已用替换字符 | `encoding=utf-8(replace)`, `source=fallback` | warning「原文件编码无法确认…成品按 UTF-8 写回」 |
| 目标编码无法表示某字符 | — | warning「已回退 UTF-8 写回，原编码 X 未保留」 |

### 2.3 行为变化（需知晓）

- `.csv` 的 block 粒度从"行"变为"单元格"：正则/NER 仍逐格检测，但**跨列上下文**变弱（例如标签在一格、值在另一格的场景）。无表头时仍能靠 per-cell 规则命中直接标识（身份证/手机号/邮箱等），不会被 table-cn 误报。
- 现有 5 个样例均非 CSV/TSV，e2e/regression 不受影响；`.txt/.md` 解析与导出逻辑未动。

---

## 3 检测器（table_column）增强

### 3.1 支持格式

`doc.fmt` 或 `doc.meta.delimited` 命中以下之一即处理：`xlsx/xlsm/docx/csv/tsv`，以及 `fmt=="text" + meta.delimited ∈ {csv,tsv}`（本项目当前路径）。分组键：

| 来源 | 分组 | 行列 ref |
|---|---|---|
| XLSX | `(page, "xlsx", sheet)` | `row/col` |
| DOCX | `(page, "docx", t)` | `r/c` |
| CSV/TSV | `(page, "delimited", kind)` | `row/col`（`sep` 辅助判断） |

### 3.2 多级/合并表头继承算法

1. **表头块识别**（前 `header_rows` 行，默认 3）：
   - 从第一个"至少命中 1 个 headers"的行开始；
   - 仅当下一行**连续**且带来**新列**的 headers 命中时才并入（两级表头的叶行）；
   - 数据行里偶然出现一个表头词不会扩展表头块（避免把数据当表头）。
2. **列语义解析**（每列）：表头块内**自深向浅**：
   - 本格非空：命中 headers → 采用；非空但未命中 → 该列不用该规则（不向父行/左侧借）；
   - 本格空白：先向左找最近非空（合并表头跨列；越过的空槽数受 `max_inherit_span` 限制，默认 8；遇非空表头即停），再退到上一层父行同列（纵向继承）。
3. **数据行** = 表头块最后一行之后；逐格生成实体（沿用 P0 的 value_pattern / exclude_validate / pending / 重复表头与表头词跳过）。
4. **表头词守卫**：`_HEADER_VOCAB`（常见列头词表）中的值不再当数据生成（多级表头叶行里其它规则的列头文字不会被误报为数据）；新增列头词时同步扩充该表（见 §6）。

### 3.3 选项

```yaml
detect:
  - type: table_column
    headers: [联系方式, 手机号]
    header_rows: 3
    inherit_merged: true        # 合并表头向左继承（默认 true）
    max_inherit_span: 8         # 向左继承最多越过几个空槽
    # 其余同 P0：status/value_pattern/exclude_validate/min_len/max_len/confidence…
```

### 3.4 无表头/非表格

- 前 `header_rows` 行无任何 headers 命中 → 返回空（不误报）；
- `.txt/.md` block 无 row/col ref → 不触发；
- 泛词（如"产品名称""数量"）不在规则 headers 中 → 不触发；
- 同一 CSV 里上下堆叠多张表时只按最前面的表头处理（P1 多表切分，见 §6）。

---

## 4 测试与实测

### 4.1 命令

```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
python -X utf8 app/tests/test_csv_table.py        # 本域单测（8 组 / 115 断言）
python -X utf8 app/tests/test_table_column.py     # 既有表格 P0 回归（9 组 / 156 断言）
cd app\server; python -X utf8 ..\tests\e2e.py     # 5 样例冒烟
cd ..\..; python -X utf8 app/tests/regression.py  # 红线门禁
```

### 4.2 实测（2026-10-20 交付时）

| 检查 | 结果 |
|---|---|
| `test_csv_table.py` | **8 个测试组 / 115 项断言全绿** |
| 解析 | `.tsv` 已注册；CSV 21 Block / 3 行 × 7 列；`delimited/delimiter/quotechar/rows/cols/raw_rows_ok` 正确；引号内逗号保留；`.txt` 仍按行切块、无 row/col ref |
| 编码/换行 | UTF-8 BOM（`utf-8-sig`，BOM 剥离）、GB18030 解码、分号分隔识别、CRLF 保留、引号内换行（`raw_rows_ok=False` 走 writer）、空文件、列数不齐（`row_cols=[3,2]`）全部通过 |
| 编码保留（P1.5） | GBK→GB18030 写回（无 BOM）、utf-8-sig→保留 BOM、UTF-16→保留 BOM、GBK+CRLF→编码+换行双保留、GBK+引号换行/列数不齐/5000 字符超长字段均往返一致；未知编码/不可编码字符→warning + UTF-8 回退（不静默）；每项导出 `residual=0`、行/列数不变 |
| 列级覆盖 | 客户名单 CSV **14/14**（7 列 × 2 行）、TSV **14/14**、两级表头 **10/10**；含缺位身份证、空格手机号、无后缀单位、文本金额 |
| 两级/合并表头 | 父级分组行（个人信息/客户信息）+ 叶行（姓名/身份证号/手机号/单位/邮箱）取叶行；`姓名,联系方式,,单位` 空槽向左继承出第 2 个电话列（4 个电话值）；纵向"父行联系方式 + 叶行手机号"两列均生效；`inherit_merged=False` / `max_inherit_span=1` 行为符合预期 |
| 无表头 | table-cn 直接/全量均 0 命中；legacy 规则仍逐格命中（ID_CARD/PHONE/ORG ≥2）；同内容 `.txt` 与泛词表头也不误报 |
| 导出/复检 | AI/涂黑/TSV 三种：`residual=0`、行数/列数不变（`[7,7,7]`）、表头原文保留、抽样无敏感原值；CRLF 导出仍 CRLF；引号内换行保留为同一单元格 |
| 规则/注册 | table-cn 11 条 scope 均含 `text`（Lead 已接）；text 格式可编译全部 table 规则；pdf 不含；`doc.fmt=csv` 时检测器仍可用（兼容未来 EXT_TO_FMT） |
| e2e | **5/5 解析成功、双导出、残留 0** |
| regression | **227 项硬检查全绿**（软提示：开发中未提交改动/OCR 耗时波动，与 CSV/TSV 无关） |
| 既有表格 P0 | `test_table_column.py` 156 项断言全绿（继承算法重写后行为不退化） |

---

## 5 兼容性与风险

| # | 点 | 说明 |
|---|---|---|
| 1 | `.csv` block 粒度变化 | 行 → 单元格。规则仍逐格生效；依赖"整行 block"的代码/集成（暂无）需注意 |
| 2 | 输出编码 | CSV/TSV 按原编码/BOM 写回（P1.5）：utf-8/utf-8-sig/utf-16/gb18030；未知编码或不可编码字符 → warning + UTF-8 回退。`.txt/.md` 仍统一 UTF-8 |
| 3 | 引号/空白 | 未修改行原文写回；修改行由 writer 重写 → 引号策略可能规范化（语义等价、列数不变；`raw_rows_ok=False` 时全量重写） |
| 4 | 多表堆叠 | 同一 CSV 里多张表只识别最前面的表头（P1 多表切分） |
| 5 | 合并单元格数据区 | CSV 的合并数据格（导出时常见为"仅左上角有值"）会被解析成空列 → 该行该列无实体（与 xlsx/docx 已知边界一致，P1 可取上一行值） |
| 6 | 表头词守卫 | `_HEADER_VOCAB` 中的词不会作为数据生成；新增业务列头词需同步加表（零代码维护） |
| 7 | 大文件 | 一次性读入 + 逐格 Block（与 xlsx 同量级）；百万行 CSV 的流式解析留给 P1 |
| 8 | 前端/README/CONTRACT | 由 Lead 更新（`.tsv`、CSV/TSV 展示）；`job.fmt` 仍显示 `text`，如需显示 CSV/TSV 由 Lead 用 `doc.meta.delimited`/格式映射处理 |

---

## 6 已知限制与 P1 路线

| # | 限制 | P1 方案 |
|---|---|---|
| 1 | 同文件多表堆叠只取首个表头 | 用"连续数据行/空行/表头块"分块，或解析器提供表格边界 |
| 2 | 合并数据格（非表头）值缺失 | 解析时向上继承数据值（需保留"空 vs 缺失"信息） |
| 3 | 流式/超大 CSV | 流式 reader + 分页 Block（与 jobs 分页协同） |
| 4 | UTF-16 字节序 | 带 BOM 的 UTF-16 统一按 Python `utf-16`（LE+BOM）写回；BE 输入归一为 LE（BOM 保留，字节序不保证原样） |
| 5 | 表头词/同义词覆盖 | 维护 `_HEADER_VOCAB` + 规则 `headers`；P2 可做词表热加载 |
| 6 | CSV 中公式/数值语义 | CSV 无类型，纯数字金额按文本 AMOUNT 处理（value_pattern 守卫）；与 xlsx `numeric_cell` 不同源 |

---

## 7 回滚与验收

**回滚**：把 `text_parser.py`、`table_det.py` 恢复为任务前提交版本，删除本任务新增的
`app/tests/test_csv_table.py`、`app/tests/fixtures/csv_table/`、`docs/impl/csv-table.md`；
Lead 加在 `table-cn.yaml` 的 `text` scope 由 Lead 决定保留（对 `.txt/.md` 无害，检测器按 ref 过滤）。

**交付前验收（按顺序）**：

```powershell
python -X utf8 app/tests/test_csv_table.py        # 期望：8 组 / >=115 断言全绿
python -X utf8 app/tests/test_table_column.py     # 期望：9 组 / 156 断言全绿（P0 不退化）
cd app\server; python -X utf8 ..\tests\e2e.py     # 期望：5/5、残留 0
cd ..\..; python -X utf8 app/tests/regression.py  # 期望：回归通过、硬项全绿
git status --short                                # 期望：仅本任务写作用域文件（+并行域改动）
```

**验收口径对照**（task-32 + task-38）：

- ✅ `.tsv` 支持；CSV/TSV 用 csv 模块解析成单元格 Block（ref={row,col,sep}，与 xlsx 一致）+保留原行；
- ✅ table_det 支持 csv/tsv ref；前 1–3 行多级表头继承到子列；无表头退化为既有规则、不误报；
- ✅ TextParser 导出按分隔符/引号写回、保持列数/行数；
- ✅ **P1.5 编码保留**：GBK/utf-8-sig/UTF-16 按原编码+BOM 写回；CRLF/引号换行/列数不齐/超长字段往返一致；导出后回读编码/BOM/换行一致、`residual=0`、行列数不变；未知编码/不可编码字符 warning 回退 UTF-8；
- ✅ 现有 `test_csv_table.py` 断言不回归（扩充后 8 组/115 断言全绿）；
- ✅ 不 push；不提交真实 PII（夹具全部虚构）。
