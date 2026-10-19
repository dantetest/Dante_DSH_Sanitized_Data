# 财务 P0 实现说明（finance-cn v2 / 校验器 / 金额一致性引擎）

> 状态：已实现（P0）· 2026-10-20 · 对应 task-9 / 调研文档 `docs/research/04-财务数据脱敏需求与方案.md` 的 §3–§5
> 红线：未改 `common.yaml` / `regex_det.py` / `surrogate.py` / `pipeline.py` / `jobs.py` / 解析器 / 条码/视觉文件；未 `git push`。
> 测试：`python -X utf8 app/tests/test_finance.py`（21/21 通过）；`app/tests/e2e.py` 5/5、残留 0；`app/tests/regression.py` 162 项硬检查全绿（3 条软提示）。

---

## 1. 交付物与写作用域

| 文件 | 类型 | 内容 |
|---|---|---|
| `app/server/yinzhan/rules/finance-cn.yaml` | 修改 | finance-cn **v2**：v1 五条规则行为不变 + 新增 17 条财务规则（共 22 条自有规则） |
| `app/server/yinzhan/detectors/finance_validators.py` | 新增 | 8 个财务域校验器（`fin_*` 命名空间），导入即注册 |
| `app/server/yinzhan/replacers/amount_consistency.py` | 新增 | 金额一致性引擎：`build_amount_plan()` / `apply_plan()` / 中文大写金额解析与生成 |
| `app/tests/test_finance.py` | 新增 | 21 项财务 P0 测试（校验器正反例、字段识别、金额勾稽/一致性、真实 Job 管线集成、既有样例误报对比、导出残留） |
| `app/tests/fixtures/finance/*.txt` | 新增 | 5 个虚构文本夹具：`license_fields` / `invoice` / `amounts` / `bank_statement` / `uscc_cases` |
| `docs/impl/finance.md` | 新增 | 本文档 |

**未触碰**（按 task-9 要求）：`rules/common.yaml`、`detectors/regex_det.py`、`replacers/surrogate.py`、`pipeline.py`、`jobs.py`、所有 `parsers/*`、`barcodes.py` / `visual*.py` / `mediascan.py`。

管线集成由 Lead 完成（两条路径均已确认接入）：

```python
# app/server/yinzhan/jobs.py:234-247（任务管线）
pipeline.decide(job.entities, job.rules, job.session)
from .replacers import amount_consistency
plan = amount_consistency.build_amount_plan(job.entities, job.rules, job.session)
if plan:
    amount_consistency.apply_plan(job.entities, plan, job.session)

# app/server/mcp_server.py:122-133（MCP sanitize_document）
session = pipeline.Session()
pipeline.decide(det.entities, det.rules, session)
plan = amount_consistency.build_amount_plan(det.entities, det.rules, session)
if plan:
    amount_consistency.apply_plan(det.entities, plan, session)
```

> 集成点约定：`build_amount_plan` / `apply_plan` 必须在 `decide()` 之后、
> `export()` / 预览之前调用；`apply_plan` 会覆盖 AMOUNT 类实体的 decision 并向
> `session.map` 写入"原值→假值"，可逆导出的映射库依赖这一步。
> `test_finance.py::test_job_pipeline_integration` 已固化此集成点，回退会红。

---

## 2. finance-cn v2 规则清单（22 条）

v1 五条（`cn.bank_account` / `cn.customer_no` / `cn.credit_line` / `cn.txn_no` / `cn.iban`）
**id、entity、label、level、scope、priority、pattern 全部保持不变**（回归门禁"已有规则不减少"）。

新增 17 条：

| # | 规则 id | entity | label | 级别 | 校验器 | 关键设计 |
|---|---|---|---|---|---|---|
| 1 | `cn.biz_reg_no` | BIZ_REG_NO | 工商注册号 | L2 | `fin_business_reg_no` | 15 位 + near（注册号/营业执照号）；无校验位，只做结构 |
| 2 | `cn.org_code` | ORG_CODE | 组织机构代码 | L2 | `fin_org_code` | GB 11714 8+1 校验；带/不带连字符两种写法 |
| 3 | `cn.tax_no` | TAX_NO | 纳税人识别号 | L2 | `fin_tax_no` | 18 位复用 USCC mod-31；15 位复用企业注册号；17/20 位结构校验 |
| 4 | `cn.legal_rep` | PERSON | 法定代表人 | L2 | — | 强锚点 + strip_prefix，只替换姓名 |
| 5 | `cn.reg_address` | ADDRESS | 注册地址 | L2 | — | 强锚点；地址替换保持同城层级（surrogate 地址工厂） |
| 6 | `cn.license_no` | LICENSE_NO | 营业执照编号 | L2 | — | 支持副本 `（1/1）` 结构 |
| 7 | `cn.reg_capital` | AMOUNT | 注册资本 | L1 | — | `replace: keep`（公开信息默认保留；可改 surrogate） |
| 8 | `cn.account_license_no` | ACCOUNT_LICENSE | 开户许可证核准号 | L3 | `fin_account_license` | `J`+13 位（央行基本存款账户编号规则） |
| 9 | `cn.swift_bic` | SWIFT_BIC | SWIFT/BIC | L3 | `fin_swift_bic` | ISO 9362 8/11 位 + 国家码白名单 + near |
| 10 | `cn.bank_routing_no` | ROUTING_NO | 联行号 | L2 | — | 12 位弱特征，必须 near（行号） |
| 11 | `cn.pay_platform_id` | PAY_ID | 支付平台账号/商户号 | L3 | — | 标签锚点（商户号/支付宝/微信…） |
| 12 | `cn.invoice_code` | INVOICE_CODE | 发票代码 | L2 | `fin_invoice_code` | 12 位（0 开头结构）或 10 位（历史票种） |
| 13 | `cn.invoice_no` | INVOICE_NO | 发票号码 | L2 | `fin_invoice_no` | 8 位；排除全同/0000 开头 |
| 14 | `cn.invoice_person` | PERSON | 开票人 | L2 | — | 开票人/收款人/复核人/经办人 |
| 15 | `cn.contract_no` | CONTRACT_NO | 合同编号 | L2 | — | 合同编号/协议编号…… |
| 16 | `cn.audit_report_no` | AUDIT_NO | 审计报告编号 | L2 | `fin_audit_report_no` | 结构校验（含"号/字/报告"+≥2 位数字） |
| 17 | `cn.audit_cpa` | PERSON | 签字注册会计师 | L2 | — | 签字注会/注册会计师强锚点 |

设计要点：

- 编号类实体一律 `strip_prefix`，实体 `value` 只含纯编号（如 `031002100311`），
  导出时标签（"发票代码："）原样留在正文，替换只覆盖编号本身。
- 无校验位的编号（注册号/联行号/合同号/审计报告号）靠 `near` 上下文/标签压误报；
  有校验位的一律"宁漏勿错"（校验失败直接丢弃，转人工确认）。
- 金额不新增规则：`common.cn.amount` 与 `finance-cn.cn.credit_line` 已覆盖文本金额；
  一致性缩放由金额引擎在管线后处理（见 §3）。

### 2.1 校验器命名空间（重要，跨域冲突记录）

校验函数注册表 `regex_det._VALIDATORS` 是**全局字典**，`registry.load_plugins()`
按文件名顺序导入插件，**后导入会静默覆盖同名函数**。实测发现贸易域
`detectors/trade_validators.py` 也注册了 `invoice_no` / `swift_bic` 等通用名，
财务域最初注册的 `invoice_no` 被覆盖后导致发票号码规则失效。

处理：财务域全部改为 **`fin_` 前缀**，与 trade 域互不覆盖：

| 注册名 | 用途 |
|---|---|
| `fin_invoice_code` | 发票代码 |
| `fin_invoice_no` | 发票号码 |
| `fin_swift_bic` | SWIFT/BIC |
| `fin_org_code` | 组织机构代码（GB 11714） |
| `fin_business_reg_no` | 工商注册号（15 位） |
| `fin_audit_report_no` | 审计报告编号 |
| `fin_account_license` | 开户许可证核准号 / 基本存款账户编号 |
| `fin_tax_no` | 纳税人识别号 |

建议 Lead 在 `docs/red-lines.md` E4 补一句"校验器按域加前缀命名空间
（`fin_`/`trade_`/…），否则同名注册会静默覆盖"。

> 跨域协调现状（2026-10-20 回归日志）：贸易域已把 `trade.swift_bic` 迁移为
> `cn.swift_bic`（由 finance-cn 统一覆盖，含 ISO 9362 校验），并登记在回归的
> "已批准规则迁移白名单"里；trade 侧发票号规则名已改为 `trade_invoice_no`。

---

## 3. 金额一致性引擎（`replacers/amount_consistency.py`）

### 3.1 接口

```python
from yinzhan.replacers import amount_consistency

plan = amount_consistency.build_amount_plan(entities, rules, session)
# -> {entity_id: replacement_text}，只包含可识别的 AMOUNT 类实体
n = amount_consistency.apply_plan(entities, plan, session)
# -> 覆盖 entity.decision（strategy="surrogate"）并写回 session.map
```

- `entities`：`pipeline.detect()` 的结果；只处理 `etype ∈ {AMOUNT, CREDIT_LINE,
  BALANCE, MONEY, PRICE, TOTAL}` 的实体；
- `rules`：`{entity_id: Rule}`（也接受 Rule 列表）；用于读取
  `replace.amount_role`（`line` / `tax` / `total` / `subtotal`，P0 规则暂未使用，
  预留给发票/报表规则）；
- `session`：`pipeline.Session` / dict / None。`build_amount_plan` 会**顺手写回**
  `session.map[(etype, value)] = replacement`，`apply_plan` 幂等再写一次，
  保证可逆导出的加密映射库能还原金额；
- 可选覆盖：给 `session` 设置属性（或 dict 键）`amount_salt`（字符串）/
  `amount_factor_range=(0.90, 1.10)`；默认 `[0.90, 1.10]`，`k != 1`。

### 3.2 算法（按已批准的决策 D2）

1. **解析单个金额**：拆 `lead`（标签/货币符号，原样保留）+ 正负号 + 数字
   （含千分位）+ 小数位 + 单位（元/圆/块/万/万元/亿/亿元/万亿）；
   中文大写金额（`cn.amount_cn`，如 `壹仟叁佰伍拾陆万元整`）单独解析成"元"数值；
   含 `%` 的值一律跳过（比率/税率不缩放）。
2. **选因子 k**：同一文档（P0 保守口径 = 全文档一个勾稽组）内，
   按"归一化到元的唯一金额集合 + salt"做 md5 确定性播种，
   `k ∈ [0.90, 1.10]`；同一输入永远得到同一 k（不依赖调用顺序）。
3. **同值同映射**：归一化后同一个"元"数值只缩放一次；同一金额的
   `人民币12,000,000.00元` / `¥12,000,000.00` / `12,000,000.00元` /
   中文大写四种写法得到同一数值。跨单位（元 vs 万元）取同值各写法里
   **最粗的显示步长**作为量化精度，保证 `5,000万元` 与 `50,000,000.00元`
   仍是同一个元数值。
4. **合计 = 分项重算**：
   - 优先用规则声明的 `amount_role: total`（发票/报表规则可显式标注）；
   - 否则用数值关系兜底：若"最大金额 = 其余金额之和"（容差
     `max(0.01, 0.05%)`）则视为合计；
   - 合计值 = 各分项**显示值**之和（而不是 `原合计 × k`），保证
     "分项相加 = 合计"在显示层严格成立。
5. **保形**：标签前缀、货币符号（人民币/¥/RMB）、单位、千分位、小数位、
   正负号原样保留；中文大写由缩放后数值重新生成；
   `数量/税率/日期/比率` 不缩放（数量不是 AMOUNT 实体；税率含 `%` 被跳过）。
6. **不缩放清单**：规则 `replace.strategy ∈ {keep, black, drop}` 的实体、
   `status=rejected`、已 black 的实体、解析失败的值。

### 3.3 实测数字（`app/tests/fixtures/finance/`）

| 夹具 | 原值 | 替换后 | 关系 |
|---|---|---|---|
| `amounts.txt` | 分项 `5,000,000.00` + `7,000,000.00`，合计 `12,000,000.00`；同一金额另有 `人民币…`、`¥…` 两种写法 | `5,059,830.00` + `7,083,762.00 = 12,143,592.00`；三种写法的 12,000,000 全部换成 `12,143,592.00` | 合计 = 分项和 ✔；同值同映射 ✔；k=1.011966 ✔ |
| `invoice.txt` | 金额 `10,000,000.00`、税额 `1,300,000.00`、价税合计 `11,300,000.00` | `10,281,350.00` + `1,336,575.50 = 11,617,925.50` | 价税合计 = 金额 + 税额 ✔；税率 13% 不动 ✔ |
| `bank_statement.txt` | 期初 `1,000,000.00`、收 `500,000.00`、付 `300,000.00`、余额 `1,500,000.00`/`1,200,000.00` | 全部同一 k=0.949647（如 `500,000→474,823.50`，`1,200,000→1,139,576.40`） | 同组同一因子 ✔；1,200,000 两个 span 合并为一个实体、一个替换值 ✔ |
| `license_fields.txt` | 注册资本 `人民币5000万元`（L1 keep） | 不缩放（`keep`） | 公开信息保留 ✔ |

### 3.4 已知局限（P1 待办）

1. **XLSX 纯数值单元格金额不参与**：规则引擎只吃文本（`cn.amount` 要求"元/万元"
   单位或货币前缀），`利润表` 里的 float 单元格仍不会命中——需要新增
   `numeric_cell` 检测器（表头关键词驱动、排除百分比/日期格式），涉及
   `xlsx_parser`/管线，Lead 保留。**需求已写在本文档 §5**。
2. **银行流水余额链**：同 k 缩放保持线性关系（`期初+发生=余额` 在舍入误差内
   仍成立），但 P0 不逐行重算，长流水会有"分币级"漂移；P1 可在引擎里增加
   余额链重算（需要同文档上下文/标签）。
3. **多级小计/嵌套合计**：P0 只识别"一个合计 = 其余之和"或显式
   `amount_role: total`；多层小计的合并报表 P1 增强。
4. **跨文件一致性**：P0 是单文档单组；同一客户的多文件（财报/流水/合同）
   需要任务组 + 组级映射库（04 文档 §3.2，Lead 排期）。
5. **英文公司名**：需要 surrogate 的英文 ORG 工厂（本批不改 surrogate.py）。
6. **舍入**：每个分项按各自单位精度舍入；当分项与合计单位不同（元/万元）
   时合计显示值可能差 ≤1 个最小单位，已在测试中按单位粒度校验。

---

## 4. 测试与门禁

```powershell
# 财务 P0 单测（21 项，临时目录隔离，不污染仓库 data/）
python -X utf8 app/tests/test_finance.py

# 最短冒烟（5 样例）
cd app\server; $env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py

# 完整红线门禁
python -X utf8 app/tests/regression.py
```

测试覆盖（21 项）：

- 校验器：USCC 合法/律所执业证号非法、发票代码/号码、SWIFT-BIC、组织机构代码
  校验位、工商注册号、开户许可证、审计报告编号、纳税人识别号；
- 规则包：22 条自有规则、v1 五条仍在、所有 `validate` 名已注册、scope/priority 合法；
- 字段识别：营业执照字段（USCC/注册号/组织代码/税号/法人/注册地址/执照编号/
  开户许可证/SWIFT/合同号/审计报告号/签字注会）、发票（代码/号码/开票人）、
  银行流水、USCC 正反例（`31310000E12345678X` 被校验位拦截）；
- 金额：分项/合计、同值多写法、中文大写同步、单位/千分位保形、比率不缩放、
  keep/rejected 跳过、会话映射可逆、确定性；
- 利润表 xlsx（金额为"元"文本单元格）：解析→检测→缩放→导出→复检全链路，
  断言合计=分项和、单元格原文 0 残留；
- 真实 Job 管线集成（`JobManager` → `jobs.py` 的金额一致性接入点 → 双导出 →
  verify）：断言导出后合计=分项和、原文金额 0 残留；
- 既有 5 样例：用 v1 规则（过滤 17 条新规则）与 v2 全量规则分别检测，
  **新增命中 0、丢失命中 0**；
- 文本导出：`pipeline.export` + `pipeline.verify` residual=0，原文金额不残留。

### 4.1 门禁结果（2026-10-20 00:0x）

- `test_finance.py`：**21/21 通过**；
- `e2e.py`：**5/5、双导出残留 0**（识别条数与基线一致：合同 15、病历 9、
  对账单 13、名片 6、TIF 3）；
- `regression.py`：**162 项硬检查全绿**（含 3 条软提示，退出码 0）。
  软提示之一为"e2e 总耗时 38.25s > 基线 15.86s ×1.5"——当时同机有 3 个
  regression 进程 + TIF spike 在跑（并发重负载），且财务规则未改变任何样例的
  识别条数（`test_no_changes_on_baseline_samples` 断言新增/丢失均为 0），
  不属于财务 P0 引入的性能回归；单跑 e2e 为 5/5 正常。

---

## 5. 给 Lead 的扩展需求（未做，不要在本次 P0 里塞）

### 5.1 XLSX/DOCX 纯数值金额检测器（P1，最高优先）

需求：新增 `detect.type: numeric_cell` 检测器（`detectors/numeric_det.py`，Lead 管线扩展），
由 `xlsx_parser` / `docx_parser` 的单元格/表格结构提供表头语义：

- 识别"金额类列"：表头含 `金额/余额/本期/上期/收入/成本/费用/利润/资产/负债/
  所有者权益/税额/应发/实发/工资` 等关键词；
- 收集列内 `int/float` 单元格（排除日期、百分比格式、编号）；
- 输出 `Block`/`Entity` 供金额引擎接管（engine 已能处理纯数字文本，只需把
  float 转成 `"28600000"` 这样的金额实体，或直接给 entity 加 `attrs.decimals`）；
- 影响面：`xlsx_parser.py` / `docx_parser.py` / `pipeline.py`（Lead 所有），
  财务域只提供检测器与测试夹具。

### 5.2 发票金额角色标记

给发票规则加 `replace: {strategy: surrogate, amount_role: tax|line|total}`
即可让引擎用显式角色识别"价税合计=金额+税额"（当前靠数值关系兜底，已可用）。

### 5.3 MCP 路径接入金额引擎

见 §1 末尾，3 行代码。

### 5.4 任务组 / 跨文件映射

04 文档 §3.2 方案 A：`Job.group_id` + 组级 vault + `rehydrate --group`；
本次未做（Lead 排期）。

---

## 6. 风险与回滚

| 风险 | 说明 / 缓解 |
|---|---|
| 新规则误报 | 5 个既有样例实测 0 新增；新增编号规则全部有校验位或 near 上下文；`test_finance.py` 固化"既有样例 0 新增" |
| SWIFT 8 位普通单词 | ISO 国家码白名单会放过 `INDUSTRY`(ST)/`DATABASE`(BA) 这类恰好撞国家码的单词，靠规则 `near: [SWIFT, BIC, 银行代码…]` 压；后续可再加词表 |
| 校验器命名覆盖 | 已用 `fin_` 前缀规避；建议写进 red-lines E4（见 §2.1） |
| 金额"合计=分项"误判 | 仅当"最大数=其余之和"或显式 `amount_role: total` 才重算；容差 0.01/0.05%；误判概率低，最坏情况是合计按分项和改，属保守修正 |
| 中文大写生成 | 支持到"万亿"分节与角/分；极端写法（如 `壹万零壹元整`）解析已覆盖常见形式，未覆盖的手写体由 OCR/人工兜底 |
| 回滚 | `git checkout -- app/server/yinzhan/rules/finance-cn.yaml`；删除新增 4 个文件/夹具目录即可（均为新增，无数据库/配置迁移） |

---

## 7. 变更记录

| 日期 | 变更 | 说明 |
|---|---|---|
| 2026-10-20 | finance-cn v2 + 8 校验器 + 金额一致性引擎 + 21 项测试 | task-9 P0；未 push |
