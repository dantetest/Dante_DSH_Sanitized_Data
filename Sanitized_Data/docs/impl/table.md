# 表格表头列级脱敏 · P0 实现说明（table_column / table-cn）

> **状态**：实现稿 · 2026-10-20 · 未 push（按用户要求）
> **任务**：`task-18`（P0 表格表头列级，2–3 人日档）；上游设计见
> [`docs/research/08b-文本表格与隐藏数据查漏补缺.md`](../research/08b-文本表格与隐藏数据查漏补缺.md) §3.5 E1 / §4.3
> **红线**：遵守 [`docs/red-lines.md`](../red-lines.md)（E4 扩展点）。**零解析器改动**：
> 未修改 `xlsx_parser.py`（finance 正在加 numeric attrs）、`docx_parser.py`、`pipeline.py`、
> `jobs.py`、`api.py`、`registry.py` 与其它规则包；只新增本域文件。

---

## 1 交付清单

| 路径 | 类型 | 说明 |
|---|---|---|
| `app/server/yinzhan/detectors/table_det.py` | 新增 | `table_column` 检测器（约 330 行，零依赖，读现有 `Block.ref`） |
| `app/server/yinzhan/rules/table-cn.yaml` | 新增 | 表头列语义规则包，`pack: table-cn`、`include: [common]`，11 条规则 |
| `app/tests/test_table_column.py` | 新增 | 9 个测试组 / 156 项断言（规则、覆盖、表头鲁棒性、值守卫、pending、既有样例、导出复检） |
| `app/tests/fixtures/table/*.json` | 新增 | 2 份虚构夹具（原型 xlsx 10 实体 / 原型 docx 8 实体），测试运行时生成二进制，不落仓库 |
| `docs/impl/table.md` | 新增 | 本文件 |

`git status` 中其它修改均来自并行域；本域未触碰。

---

## 2 设计

### 2.1 零解析器改动的依据

| 格式 | 解析器已有的 `Block.ref` | 用途 |
|---|---|---|
| XLSX | `{"sheet", "cell", "row", "col"}` + `attrs={numeric,date,formula,number_format}` | 按 sheet 分组、按行列定位单元格 |
| DOCX | `{"t", "r", "c"}`（表格 t / 行 r / 列 c） | 按表格分组、按行列定位单元格 |

检测器只做"分组 → 找表头行 → 同列数据格生成 Entity"，不依赖解析器新增字段；
导出仍走各解析器原有 span 替换通路（XLSX 单元格文本替换 / DOCX run 替换）。

### 2.2 检测流程（`TableColumnDetector`）

1. **分组**：按 `(页面, 表格标识)` 聚合文本块——xlsx 用 `ref["sheet"]`，docx 用 `ref["t"]`；
2. **表头行**：在前 `header_rows`（默认 3，最大 5）行内，选"命中 `headers` 数量最多"的一行；
   `headers` **精确匹配**（仅归一化空白/全角空格/大小写/末尾冒号），因此"名称""备注"等
   泛词不会因包含关系误命中；
3. **逐列生成实体**：对表头行以下同列单元格生成 `Entity`：
   `value = 单元格文本（trim）`，`span = (前导空白偏移, 长度)`，`origin="table"`，
   `etype/label/level` 取规则，`confidence` 默认 0.90；
4. **跳过**：空单元格、重复表头文本、公式单元格（`attrs.formula`）、数值/日期单元格
   （`attrs.numeric/date`，交给 `pipl-cn` 的 `numeric_cell`）、长度越界值；
5. **值守卫**（可选）：`value_pattern`（对去空白值匹配，如电话必须是号码形态）、
   `exclude_validate`（值命中任一校验器则跳过——例如"证件号"列里合法的 USCC 应留给
   `common.cn.uscc`，避免 L3 列规则在同长 span 上抢走 L2 既有实体）；
6. **人工确认**（可选）：`status: pending` 让高风险列生成待确认实体；
7. **合并**：同值合并/重叠裁剪由 `pipeline._merge` 处理；规则 `priority` 全部 **< 15**，
   低于既有规则，保证同一 span 被普通规则命中时保留既有类型/标签（本包只补漏）。

### 2.3 为什么"一条规则 = 一类列语义"

08b 草案的"单规则 + columns 动态语义"需要按列动态决定 entity/level/替换策略；
而现有管线 `pipeline.decide` 的替换策略来自 **rule.replace**，实体只覆盖
etype/label/level（`pipeline._merge` 只会向上取更高 level）。本实现改为
**每条规则固定一类语义**，与引擎语义完全对齐：entity/label/level/replace 全部由规则
声明，检测器只负责"这一列是否该规则的表头 + 数据格取值"。好处：可测试、可配
priority、不同列语义可配不同替换策略；代价：11 条规则而非 1 条（可接受）。

### 2.4 与既有规则/检测器的边界

| 检测器/规则 | 职责 | 与本检测器的关系 |
|---|---|---|
| `pipl-cn` 的 `numeric_cell`（`numeric_det.py`） | xlsx **纯数值/日期**金额、账号、日期列 | 本检测器跳过 `attrs.numeric/date` 单元格，二者互补不重叠 |
| 既有正则/NER 规则 | 逐单元格/逐块检测 | 本检测器 priority 最低，同值/同 span 时既有结果优先；表头列只补"正则没命中"的值 |
| `trade-cn` 的 `table_column`？ | 不存在（trade 包是贸易单证号码，不处理表格列） | 无冲突 |

---

## 3 规则清单（table-cn，11 条）

> 全部 `scope: [xlsx, docx]`、`replace: {strategy: surrogate}`、`priority < 15`、
> `include: [common]`（只选 table-cn 时仍带通用 PII 规则）。

| # | rule id | entity / label | 级别 | 优先级 | 命中表头（精确，忽略空白/大小写） | 备注 |
|---|---|---|---|---|---|---|
| 1 | `table.person` | PERSON / 表格姓名列 | L2 | 14 | 姓名、名字、员工姓名、客户名称、申请人、参保人、患者姓名、学生姓名、开户人姓名、法定代表人 | 长度 2–30 |
| 2 | `table.contact` | PERSON / 表格联系人列 | L2 | 13 | 联系人、联系人姓名、经办人、业务联系人、紧急联系人 | 长度 2–30 |
| 3 | `table.id_card` | ID_CARD / 表格证件号列 | L3 | 12 | 身份证号、身份证号码、身份证、证件号、证件号码、身份证件号、居民身份证号、护照号 | `exclude_validate: [uscc_checksum]`（合法 USCC 让位 `cn.uscc`） |
| 4 | `table.phone` | PHONE / 表格电话列 | L2 | 11 | 手机号、手机号码、手机、联系电话、电话、联系方式、电话号码、办公电话、家庭电话 | `value_pattern` 号码形态守卫 |
| 5 | `table.address` | ADDRESS / 表格地址列 | L2 | 10 | 地址、住址、联系地址、家庭住址、通讯地址、户籍地址、详细地址 | 长度 2–120 |
| 6 | `table.bank_card` | BANK / 表格银行卡列 | L3 | 9 | 银行卡号、银行卡、卡号、银行账号、银行帐号 | `value_pattern: ^[0-9\-]{8,30}$` |
| 7 | `table.account` | ACCOUNT / 表格账号列 | L3 | 8 | 账号、帐号、账户、账户号、客户号、客户编号 | `value_pattern: ^[0-9A-Za-z\-]{6,40}$` |
| 8 | `table.org` | ORG / 表格单位列 | L2 | 7 | 单位、工作单位、公司、公司名称、单位名称、机构名称、用人单位 | 长度 2–60 |
| 9 | `table.email` | EMAIL / 表格邮箱列 | L2 | 6 | 邮箱、电子邮箱、电子邮件、邮件地址、email、e-mail | `value_pattern` 邮箱形态守卫 |
| 10 | `table.amount` | AMOUNT / 表格金额列 | L2 | 5 | 金额、工资、应发工资、实发工资、余额、合计、小计、收入、费用、成本、报销金额、借款金额 | 只处理**文本**金额；纯数值列由 `pipl.numeric_amount` 负责 |
| 11 | `table.txn_no` | TXN_NO / 表格单号列 | L2 | 4 | 订单号、订单编号、票号、交易流水号、流水号、凭证号、合同号 | `value_pattern` 单号形态守卫 |

**启用 pending（高风险列人工确认）示例**：在对应规则的 detect 项加一行
`status: pending`（`table.id_card` / `table.bank_card` / `table.account` 最适合），
导出前 UI 会把这些实体标为待确认；本包默认 `accepted`，避免批量导入时全是待确认。

---

## 4 测试与实测结果

### 4.1 命令

```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
python -X utf8 app/tests/test_table_column.py          # 本域单测（约 15–25s，含 5 样例 OCR）
cd app\server; python -X utf8 ..\tests\e2e.py          # 冒烟：5/5、残留 0
cd ..\..; python -X utf8 app/tests/regression.py       # 红线门禁
```

### 4.2 实测（2026-10-20 交付时）

| 检查 | 结果 |
|---|---|
| `test_table_column.py` | **9 个测试组 / 156 项断言全绿** |
| 检测器注册 | `table_column` 已注册；`table-cn.yaml` `load_errors = []`，自有 **11** 条规则 |
| 格式编译 | xlsx/docx 各含 11 条 table 规则；pdf/text/image/tiff/mesh **0 条**（scope 生效） |
| 原型 xlsx | **10/10** 实体（姓名 2、缺位身份证 2、带空格手机号 2、无后缀单位 2、文本金额 2） |
| 原型 docx | **8/8** 实体（姓名/电话/单位/邮箱各 2） |
| 表头鲁棒性 | 表头在第 3 行可识别；泛词"名称/结果/备注"不生效；重复表头跳过；带空格表头归一化；无表格 docx 0 命中 |
| 数值/公式 | 数值金额、公式单元格跳过；文本金额保留；纯数值手机号列 P0 不处理（已知边界） |
| 值守卫 | 电话列 "无/N/A" 跳过、带空格号码保留；证件号列合法 USCC 让位 `cn.uscc`、缺位身份证仍覆盖 |
| pending | `status: pending` 生效（2/2 pending） |
| 既有 5 样例 | 既有实体 **零减少**；新增仅对账单 **2 条**（见 §5）；其余 4 个样例 **0 新增** |
| 导出复检 | 原型 xlsx 导出 + `pipeline.verify`：`residual = 0`，成品无原值残留 |
| e2e | **5/5 解析成功、双导出、残留 0** |
| regression | **回归门禁全绿**（交付时为 167 项硬检查；qa 并行更新脚本后为 164 项，均全绿；软提示仅为"开发中未提交改动/OCR 耗时波动"，与 table-cn 无关） |

### 4.3 测试覆盖矩阵

| 测试组 | 断言要点 |
|---|---|
| 1. 规则包/检测器 | 注册、包存在、11 条 id、scope 仅 xlsx/docx、priority<15、surrogate、编译格式、include common |
| 2. 原型 xlsx | 10/10 覆盖、rule 归属、span=(0,len)、origin=table、confidence |
| 3. 原型 docx | 8/8 覆盖、同上 |
| 4. 表头定位 | 第 3 行表头、泛词排除、重复表头、空白归一化、无表格 |
| 5. 数值/公式 | numeric/formula 跳过、文本金额保留、数值手机号 P0 不处理 |
| 6. 值守卫/让位 | 电话/证件号守卫 + 全量规则下 USCC 仍由 `cn.uscc` 识别 |
| 7. pending | 直接构造检测器验证 status |
| 8. 既有样例 | 零丢失 + 对账单新增白名单（逐条解释） |
| 9. 导出复检 | export + verify residual=0 + 成品抽样无原值 |

---

## 5 既有样例新增实体逐条解释（对账单.xlsx，仅 2 条）

`对账单.xlsx` 表头：`客户名称 | 账号 | 证件号 | 金额 | 联系电话`，4 行数据。
接入 table-cn 后（全量规则）新增 2 个实体，均在白名单内：

| # | 实体 | 来自列 | 为什么是合理新增 | 为什么不重复/不误报 |
|---|---|---|---|---|
| 1 | `PERSON`「王建国」（label 人名） | 客户名称 A4 | 该姓名 NER 单独置信度 < `standard.min_conf`（0.60）原本漏检；列语义提供强证据后置信度升到 0.90，通过闸门（规则归属合并后显示为 `cn.person`） | 同列张伟/赵淑芬已被 NER 命中，同值合并；A5「上海市明理律师事务所」保留既有 `ORG/律师事务所` 类型（既有规则 priority 更高，列语义 PERSON 不抢 span） |
| 2 | `ACCOUNT`「6228 4801 2233 4455 667」（label 表格账号列） | 账号 B4 | 该卡号未通过 Luhn，既有 `cn.bankcard` 不命中；列语义（账号列）补上，L3 可逆换名 | 其余 3 个卡号已被 `cn.bankcard`（L3）命中，同 span 时既有实体优先，不产生重复 |

其它 4 个既有样例（住院病历.docx / 名片.png / 委托代理合同.pdf / 正射影像.tif）：
**零新增、零减少**。住院病历的表头为"检查项目/结果/备注"，均不在列语义清单内，
因此 docx 表格不产生任何列级实体（符合"泛词不默认"的设计）。

> 说明：两者都属 08b 预先声明的"整列处理带来的合理新增"（门禁允许；regression 把
> 新增识别当软变化）。是否把 `客户名称` 映射为 PERSON 而不是 ORG、是否把 `账号` 列
> 并入 `bank_card`，属于产品口径，见 §7 决策点。

---

## 6 需要 Lead / 其它域接的点

| # | 事项 | 归属 | 说明 |
|---|---|---|---|
| 1 | `app/tests/run_all.py` 增加 `test_table_column.py` | qa（tests 写作用域） | 当前 run_all 的 specs 列表未含本测试；红线门禁不依赖它（域测试） |
| 2 | qa 基线软记录确认 | qa | 对账单新增 `人名×1`、`表格账号列×1`；regression 已按"新增识别=软变化"处理，167 硬项全绿；是否重录 `baseline.json` 的 `baseline_types` 由 qa 决定（重录前请先跑一次普通回归确认无硬项失败） |
| 3 | `registry._PLUGIN_FALLBACK` | ✅ 已含 `table_det` | 打包兜底无需再改（Lead 已预置） |
| 4 | `CONTRACT.md` 检测器清单 | Lead（可选） | 新增检测器 id `table_column`；若文档列检测器清单可补一行 |
| 5 | 前端 pack 选择 | 前端/产品 | `/api/packs` 动态返回 table-cn，无需改代码；若产品希望默认勾选 table-cn，需改默认 pack 列表（前端/API） |
| 6 | P1 依赖解析器/其它域 | 见 §7 | CSV/TSV 行列 ref（text_parser）、合并表头继承（需解析器暴露 merged ranges）、数值手机号/证件列（numeric_cell 扩展）、docx 嵌套表 ref |

---

## 7 已知限制与 P1 路线

| # | 限制 | 影响 | P1 方案 |
|---|---|---|---|
| 1 | 只处理 xlsx/docx | CSV/TSV 无列语义 | 08b E5：`text_parser` 给 CSV/TSV 生成 `ref={row,col}`，本检测器直接复用（约 1–1.5 人日，解析器域） |
| 2 | 数值/日期/公式单元格跳过 | Excel 里存成数字的手机号/身份证/账号列漏检 | 扩展 `numeric_cell` 的 mode（phone/id/account 形态，需按 number_format/长度判定，约 1 人日） |
| 3 | 合并单元格/多级表头不继承 | 两级表头（"个人信息"跨列）或合并表头只有左上角值时列语义错位/漏列 | 08b E6：xlsx 需解析器暴露 `merged_cells.ranges`（或 meta），docx 需 gridSpan/vMerge 信息；检测器再做"向上/向左继承"（约 1–1.5 人日） |
| 4 | 同一 sheet 上下堆叠多张表 | 只按最前面的表头行处理，第二张表的数据可能被当第一张表的数据行 | P1 用"连续数据行/空行分块"或解析器提供表格边界（约 0.5–1 人日） |
| 5 | 表头同义词有限 | 新业务表头（如"受助人""持卡人"）需人工扩充 `headers` | 维护规则即可（零代码）；可考虑 P1 表头词典 + 用户自定义 |
| 6 | `status: pending` 默认关闭 | 高风险列（身份证/银行卡/账号）默认直接可逆换名 | 产品决策：可在规则 enable pending；或 UI 按列类型二次确认 |
| 7 | 表头行判定=前 N 行中命中最多的一行 | 数据行里恰好出现多个表头词（如第二行是"姓名"）可能误选 | P1 结合"数据行连续性/类型分布"判定，或要求表头行下方至少 1 行非空数据 |
| 8 | 空/重复表头、空单元格跳过 | 部分表格首列合并/空表头列漏检 | P1 合并表头继承（同 #3） |

---

## 8 回滚与验收

**回滚**（只删本域新增文件，不影响既有功能）：

```powershell
Remove-Item app\server\yinzhan\detectors\table_det.py
Remove-Item app\server\yinzhan\rules\table-cn.yaml
Remove-Item app\tests\test_table_column.py
Remove-Item app\tests\fixtures\table -Recurse
Remove-Item docs\impl\table.md
```

**交付前验收（按顺序）**：

```powershell
python -X utf8 app/tests/test_table_column.py   # 期望：9 组 / >=156 断言全绿
cd app\server; python -X utf8 ..\tests\e2e.py   # 期望：5/5、残留 0
cd ..\..; python -X utf8 app/tests/regression.py  # 期望：回归通过、硬项全绿
git status --short                              # 期望：仅新增本域 5 个路径（+并行域改动）
```

**验收口径对照**（task-18）：

- ✅ 检测器 `type: table_column`，规则 YAML 每条列语义；
- ✅ xlsx `ref={sheet,cell,row,col}` / docx `ref={t,r,c}`，零解析器改动；
- ✅ 前 1–3 行表头、精确匹配、泛词不默认、高风险列可 pending、不处理数字单元格；
- ✅ 原型 10/10 覆盖；既有 5 样例零非预期误报（新增 2 条逐条解释）；
- ✅ e2e 5/5 残留 0；regression 全绿（硬项数随 qa 脚本并行更新浮动，交付时 164–167 均全绿）；
- ✅ 不 push、不提交真实 PII（夹具全部虚构）。
