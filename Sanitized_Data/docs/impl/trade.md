# 进出口贸易单证脱敏 · P0 实现说明（trade-cn）

> **状态**：实现稿 · 2026-10-19 · 未 push（按用户要求）
> **任务**：`task-10`（贸易 P0）；上游方案见 [`docs/research/05-进出口贸易单证脱敏清单与方案.md`](../research/05-进出口贸易单证脱敏清单与方案.md)
> **红线**：遵守 [`docs/red-lines.md`](../red-lines.md)（E4 规则/校验器扩展点）；**未修改** `common.yaml`、`finance-cn.yaml`、`regex_det.py`、`pipeline.py`、`jobs.py`、`api.py`、解析器、前端与依赖。
> **并行改动归属**：`pipeline.verify` 跳过 keep 实体的修复、`common.yaml` 的 USCC/Luhn/exclude_near 修复、`finance-cn.yaml` v2 等均为 Lead/其它域在同一工作区的并行改动，**不是本域交付内容**；本域只新增下列 5 个路径。

---

## 1 交付清单

| 路径 | 类型 | 说明 |
|---|---|---|
| `app/server/yinzhan/rules/trade-cn.yaml` | 新增 | 贸易单证规则包，`pack: trade-cn`、`include: [common]`，15 条规则 |
| `app/server/yinzhan/detectors/trade_validators.py` | 新增 | 贸易号码校验器，`@register_validator` 注册（被 `registry.load_plugins()` 自动导入） |
| `app/tests/test_trade.py` | 新增 | P0 单测/自检脚本（规则命中、校验合法性、keep 导出、既有样例无新增误报） |
| `app/tests/fixtures/trade/*.txt` | 新增 | 7 份虚构夹具（发票/装箱单/提单/报关单/合同/信用证/非法号码），全部纯文本，无二进制 |
| `docs/impl/trade.md` | 新增 | 本文件 |

**没有改动**既有规则、核心管线、解析器、替换器与前端；`git status` 中新出现的其它修改均来自并行域。

---

## 2 规则清单（trade-cn，15 条）

优先级数字越大越先检测/越优先占位（`rules.py` 编译排序）。`scope` 统一为
`[pdf, docx, xlsx, text, image, tiff]`（贸易单证不涉及 `mesh`，故 mesh 格式不启用）。

| # | rule id | entity / label | 级别 | 优先级 | 检测 | 校验器 | 替换 | 用途/备注 |
|---|---|---|---|---|---|---|---|---|
| 1 | `trade.export_control_hint` | CONTROL_HINT / 出口管制提示 | L3 | 130 | dict 关键词 | — | keep | 命中"最终用户/最终用途/两用物项/出口管制/END USER/DUAL-USE…"只标记，提醒人工复核 |
| 2 | `trade.container_no` | CONTAINER_NO / 集装箱号 | L2 | 118 | regex + near | `iso6346_container` | surrogate | ISO 6346（含第 11 位校验），near 集装箱/箱号/柜号… |
| 3 | `trade.declaration_no` | DECL_NO / 报关单号 | L2 | 116 | regex + near | `customs_decl_no` | surrogate | 18 位结构：4 关区 + 4 年份 + 1 进出口标志 + 9 顺序号 |
| 4 | `trade.awb_no` | AWB_NO / 空运单号 | L2 | 114 | regex + near | `awb_no` | surrogate | IATA 11 位、末位 mod 7；near 空运/运单/AWB… |
| 5 | `trade.bl_no` | BL_NO / 提单号 | L2 | 112 | regex + near | `bl_no` | surrogate | 承运人前缀 3-4 字母 + 6-12 位序列；排除集装箱形态 |
| 6 | `trade.license_no` | LICENSE / 许可证号 | L3 | 108 | regex + near | `license_no` | **black** | 出口/进口/两用物项/配额许可证；默认涂黑（高敏） |
| 7 | `trade.invoice_no` | INVOICE_NO / 发票号 | L2 | 96 | regex + near | `trade_invoice_no` | surrogate | 贸易发票号（INV/PI/CI + 年份 + 流水）；与财务域 8 位"发票号码"区分 |
| 8 | `trade.contract_no` | CONTRACT_NO / 合同号 | L2 | 94 | regex + near | `contract_no` | surrogate | 合同/订单/协议号 |
| 9 | `trade.seal_no` | SEAL_NO / 封条号 | L2 | 92 | regex + near | `seal_no` | surrogate | 封条/铅封/关锁号；无统一校验位，排除集装箱形态 |
| 10 | `trade.origin_cert_no` | ORIGIN_CERT_NO / 原产地证号 | L2 | 90 | regex + near | `origin_cert_no` | surrogate | CO / FORM E / RCEP 证书号（保守结构） |
| 11 | `trade.amount_fx` | AMOUNT / 外币金额 | L2 | 88 | regex | — | surrogate | USD/EUR/GBP/JPY/HKD/AUD/CAD/CHF/SGD/CNY/RMB + 金额（人民币由 common 的 `cn.amount` 负责） |
| 12 | `trade.org_en` | ORG / 英文机构名 | L2 | 76 | regex | — | surrogate | `… CO., LTD. / LIMITED / INC. / GMBH / S.A. / PTE LTD …` 英文公司名 |
| 13 | `trade.hs_code` | HS_CODE / HS 编码 | L1 | 60 | regex + near | `hs_code` | keep | 6/8/10 位、章 01-97；**禁止换码**，只识别并保留 |
| 14 | `trade.price_term` | TRADE_TERM / 价格条款 | L1 | 52 | regex + near | — | keep | FOB/CIF/CFR/CIP/CPT/EXW/FCA/DDP/DAP/FAS；业务必需保留 |
| 15 | `trade.vessel_voyage` | VESSEL / 船名航次 | L1 | 50 | regex + near | — | keep | 船名/航次/航班；保留（对外版本可人工改黑） |

**设计取舍**：

- 所有号码类规则都挂 `validate`：校验不通过 = 候选值直接丢弃（`regex_det` 行为），避免把
  手机号/身份证/银行流水号误报成贸易号码（见 `invalid_numbers.txt` 夹具）；
- `near`/结构双约束：号码类要求 8~20 字符上下文提示词；HS/价格条款/船名要求上下文，
  因此**在这些规则落地前后，既有 5 个样例的检测结果完全一致**（§5.4）；
- `keep`（显式保留）只标记不修改：HS/价格条款/船名/出口管制关键词进入"已识别清单"，
  由用户决定；`pipeline.verify` 已由 Lead 修复为跳过 `decision.strategy == "keep"`，
  否则含 HS 编码的贸易件会被判"残留"而无法导出（本域 `test_keep_export` 守住该行为）；
- **SWIFT/BIC 不在本包重复定义**：财务域 `finance-cn` 已有 `cn.swift_bic`（priority 105）与
  `fin_swift_bic` 校验器（含 ISO 3166 国别码）；两条同形态规则会互相抢 span，故 trade-cn
  只保留协同（夹具验证 `BOFAUS3N` 由财务域规则检出）；
- 以下规则**刻意留到 P1**（避免 P0 误报/依赖不足）：信用证号、英文/中文银行账号、港口词典、
  最终用户字段值（需 field_det）、原产地证专门格式。

---

## 3 校验器清单（`trade_validators.py`）

注册位置：`detectors/regex_det.py` 的 `_VALIDATORS`；模块被 `registry.load_plugins()`
扫描 detectors 目录时自动 import（`docs/red-lines.md` E4）。

| 校验器名 | 算法要点 | 正例 | 反例 |
|---|---|---|---|
| `iso6346_container` | 字母值 A=10 起、跳过 11 的倍数；Σ(值×2^i) mod 11，10→0；与第 11 位比较 | `CSQU3054383`（加权和 6185，rem=3） | `CSQU3054384`、`CSQX3054383` |
| `awb_no` | 11 位数字；前 10 位 mod 7 == 第 11 位（IATA unweighted modulus 7） | `99912345676`（9991234567 mod 7 = 6） | `99912345677`、位数不足 |
| `customs_decl_no` | 18 位结构 `\d{4}(19|20)\d{2}[01IE]\d{9}`，年份 1980..当前+1 | `224420250012345678` | 年份 1800、标志非 0/1/I/E |
| `bl_no` | 3-4 字母 + 6-12 数字，总长 9-16；排除 ISO 6346 形态 | `COSU6301234567` | `CSQU3054383`（集装箱）、过短 |
| `hs_code` | 去点/空格后 6/8/10 位数字；章 01-97 | `8471300000` / `84713000` / `847130` | 7 位、章 99、含字母 |
| `trade_invoice_no` | 5-40 位、字母+数字混合；排除纯日期/USCC/身份证/AWB 形态 | `INV-2026-0001` | `2026-01-01`、纯数字、USCC |
| `contract_no` | 5-40 位、字母+数字混合，同上排除 | `HX-2026-001` | 过短、纯日期 |
| `origin_cert_no` | 6-40 位、字母+数字混合 | `CO-2026123456` | 过短 |
| `lc_no` | 8-35 位、字母+数字混合（P1 信用证规则预留，当前未挂 YAML） | `LC26NB00123` | 纯数字、USCC |
| `license_no` | 8-24 位、字母+数字+分隔符；排除 USCC/身份证/纯日期形态 | `2026-EX123456` | `31310000E12345678X`（USCC 形态）、`330203199001011234` |
| `seal_no` | 2-4 字母 + 6-12 数字；排除 ISO 6346 集装箱形态 | `CN1234567` | `CSQU3054383` |

**跨域命名约定（重要）**：`detectors/` 目录按文件名 import，**同名校验器后者覆盖前者**
（`trade_validators` 排在 `finance_validators` 之后）。因此：

- 财务域已改用 `fin_` 前缀（`fin_invoice_no` / `fin_swift_bic` …）；
- 贸易侧发票号使用 `trade_invoice_no`，与财务域 8 位"发票号码"隔离；
- `swift_bic` 由财务域负责，本域不重复注册。**新增跨域校验器请统一加域前缀**。

---

## 4 换名"保形合法"现状（P0 边界，必须知悉）

P0 只保证**检测侧**：命中值 100% 通过对应校验器（`test_trade.py` §1/§3 断言）。
**替换侧尚未保形**：引擎现有 `surrogate` 对未知实体类型走通用按字符类替换
（`replacers/surrogate.py` 的 `_fake_generic`），因此：

| 实体 | 当前换名结果示例 | 问题 | P1 需求 |
|---|---|---|---|
| 集装箱号 | `CSQU3054383` → `ZKLH1204844` | 箱主代码/校验位均被随机化，ISO 6346 不合法 | `CONTAINER_NO` 工厂：保留箱主代码+类别，换 6 位序列并重算校验位 |
| 报关单号 | `224420250012345678` → `933452780918256914` | 年份段/进出口标志被打乱，18 位结构不合法 | `DECL_NO` 工厂：保留前 9 位，换 9 位顺序号 |
| 空运单号 | 同上（通用替换） | 航司代码/校验位随机 | `AWB_NO` 工厂：保留航司 3 位，重算 mod 7 |
| 提单号/封条/许可证/原产地证/发票/合同号 | 保长度/字符类，但前缀语义丢失 | 号码"看起来不像"真实单号，跨单证引用易不一致 | `BL_NO`/`SEAL_NO`/`REF_NO` 工厂：保留前缀/年份/分隔符 |
| 英文公司名 | `ABC INTERNATIONAL TRADING CO., LTD.` → `RFM NIDPGRRDGMMHO GAUMANQ YX., OPE.` | 后缀 `CO., LTD.` 被随机化 | `ORG_EN` 工厂：保留 `CO./LTD./INC.` 等后缀与地名 |
| 金额 | `USD 128,000.00` → `USD 124,740.84` | 单值±25% 保形，但**不联动大写、不保持单价×数量=总价、不跨文件一致** | `AMOUNT_FX` 工厂 + 批级比例（P1/P2） |

> 按 E4 约定：上述替换器应放独立模块（如 `replacers/trade_factories.py`），由 Lead 在
> `surrogate._FACTORIES`/管线挂接——**本批未改 `surrogate.py`**，因此不越权实现。
> 该项是 05 文档 §3.5"换名必须保形合法"的核心缺口，建议作为 P1 第一优先级。

---

## 5 测试与实测结果

### 5.1 命令

```powershell
# 1) 贸易 P0 单测（本域；约 20–30s，含 5 样例 OCR）
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
python -X utf8 app/tests/test_trade.py

# 2) 最短冒烟（5 样例 → 解析/检测/双导出/复检）
cd app\server; $env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py

# 3) 完整回归门禁（qa；交付前必跑）
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
python -X utf8 app/tests/regression.py
```

### 5.2 实测结果（2026-10-19 交付时）

| 检查 | 结果 |
|---|---|
| `test_trade.py` | **135 项检查全绿**（校验器正反例、规则加载、夹具命中、XLSX 表格、keep 导出复验、既有样例回归隔离） |
| 规则加载 | `load_errors = []`；trade-cn 自有 **15** 条 + include common 14 = 包内 29 条；全库定义 66 条（含并行域） |
| 各格式编译 | pdf/docx/xlsx/text 66 条；image/tiff 56 条；mesh 13 条（trade-cn 均不参与 mesh） |
| 夹具召回 | 6 份正例夹具共 **27/27** 条期望规则命中（含 2 个外币金额、2 个管制关键词）；检出值校验合法率 **100%** |
| 负例 | `invalid_numbers.txt`（校验位错/年份错/7 位 HS）→ **0 命中**（校验器全部丢弃） |
| keep 导出 | 报关单夹具 AI/人工双模式导出 + verify 通过，`residual = 0`（keep 不被当残留）；AI 版保留 HS、公司名已换 |
| 既有 5 样例 | 有/无 trade-cn 两次检测：**类型与数量完全一致**、trade-cn 新增命中 **0**、无新增类型；基线 `required_types` 全满足 |
| e2e | **5/5 解析成功、双导出、残留 0** |
| regression | **161 项硬检查全绿**（2 条软提示：名片 OCR 解析耗时抖动，与 trade-cn 无关） |

### 5.3 夹具设计（虚构数据；纯文本，避免二进制入库）

| 夹具 | 覆盖单证 | 期望 trade 规则（部分） |
|---|---|---|
| `commercial_invoice.txt` | 商业发票 | 发票号、合同号、HS、外币金额、价格条款、英文公司名 |
| `packing_list.txt` | 装箱单 | 集装箱号、封条号、发票号引用 |
| `bill_of_lading.txt` | 海运提单 | 提单号、集装箱号、封条号、船名航次、收货人（英文公司名） |
| `customs_declaration.txt` | 出口报关单 | 报关单号、HS、成交方式、许可证号、最终用户/用途关键词、英文公司名 |
| `sales_contract.txt` | 销售合同 | 合同号、英文公司名、外币总价、价格条款 |
| `lc_excerpt.txt` | 信用证片段 | 外币金额、英文公司名、管制关键词；SWIFT 由财务域 `cn.swift_bic` 检出（协同用例） |
| `invalid_numbers.txt` | 负例 | 集装箱/空运单号校验位错、报关单号年份错、7 位 HS → 全部 0 命中 |

### 5.4 误报检查方法（逐条可复核）

1. 对 5 个既有样例分别做**两次检测**：A = 全部规则包；B = 全部规则包剔除 `pack == trade-cn`
   的规则（同一进程、同一解析结果，排除并行域影响）；
2. 比较两次的 `label → count`：必须完全一致；且 A 中由 trade-cn 规则产生的实体数必须为 0；
3. 该检查已写入 `test_trade.py` §5，任何未来新增 trade 规则造成既有样例新增命中都会立即失败；
4. 若将来新增贸易规则确实需要命中某个既有样例（例如样例里出现真实合同号），应在测试里
   显式更新期望并说明原因，而不是放宽断言。

---

## 6 关键决策与跨域协同

| # | 决策 | 原因 |
|---|---|---|
| D1 | 15 条规则全部带 near 或强结构；号码一律挂 validate | P0 的首要风险是"新规则给既有样例制造误报"；先保召回精度 |
| D2 | HS/价格条款/船名/管制关键词用 `keep` | 业务必需、不能换名；只进"已识别清单"提醒用户（依赖 Lead 的 verify keep 修复） |
| D3 | 许可证号默认 `black`（而非换名） | 直接关联出口管制与审批，P0 不做可逆换名 |
| D4 | SWIFT/BIC 不重复建规则 | 财务域 `cn.swift_bic` 已覆盖；同形态规则会互相抢 span（E4 独立文件原则） |
| D5 | 发票号校验器命名 `trade_invoice_no` | 避免与财务域 8 位"发票号码"校验器同名互相覆盖（`detectors/` 按文件名 import） |
| D6 | 测试脚本自带"有/无 trade-cn"隔离对比，不依赖 qa 基线重录 | 新规则导致实体变多属软变化；隔离对比能真正证明"不是 trade-cn 造成的" |

**跨域待办（需 Lead/财务域配合）**：

- `finance-cn` 的 `cn.swift_bic` near 词表建议补"开证行/汇入行/账户行"（贸易信用证常见），
  目前 `SWIFT：` 标签可命中，但只有"开证行："时可能漏；
- `registry._PLUGIN_FALLBACK`（打包兜底清单）目前只列 `regex_det/dict_det/ner_det`，
  未含 `trade_validators`/`finance_validators`。桌面版打包（PyInstaller）后若 detectors 目录
  不可扫描，校验器不会注册，YAML 的 `validate` 会静默降级为 `none`（规则仍可用但不再校验）。
  **建议 Lead 在打包前更新 fallback 清单或在 app.spec 加 hiddenimports**，并加打包冒烟。

---

## 7 已知限制与后续工作

### P1（建议本迭代内）

1. **贸易号码保形替换工厂**（§4）：集装箱/报关单/AWB/提单/封条/许可证/产地证/发票/合同号
   + 英文机构名；需要 Lead 在 `surrogate.py`/管线挂接（E4）。
2. **`field_det` 字段级检测器**：解决"标签在左、值在右"的通道（联系人/最终用户/银行账号/
   港口/信用证号），当前正则的显式标签覆盖不全。
3. **大写金额与勾稽**：金额换名后同步英文 `SAY TOTAL`/中文大写，保持"单价×数量=总价"。
4. **PDF 表单域（AcroForm）/附件/注释**：电子报关单、原产地证、单一窗口导出件的漏检面。
5. **许可证/原产地证格式收紧**：当前为保守结构 + 上下文；需真实样本（脱敏后）再收紧。

### P2（跨单证任务组，本批不做 —— 后续实现接口需求）

> 目标：同一批业务（合同/发票/装箱单/提单/报关单/LC/保险单）共享实体映射，
> 公司名、金额、数量、箱号在成品中一致，并可做勾稽检查。当前阻塞点在"一次只处理一个文件"
> （`jobs.py` 每任务独立 `pipeline.Session`；`vault` 按 `job_id` 存储；`api.py` 单文件上传）。

**建议接口/模型（文档级，供 Lead 排期）**

```text
Group {
  group_id, name, created_at, pack_ids, strictness,
  files: [{ job_id, role: 合同|发票|装箱单|提单|报关单|信用证|保险单|产地证|EUC|其他 }],
  session（共享实体图）, status
}

API:
  POST /api/groups                       创建组（pack_ids/strictness）
  POST /api/groups/{gid}/files           加入已上传文件（upload_id + role）
  POST /api/groups/{gid}/run             逐文件解析+检测（共享 session）
  GET  /api/groups/{gid}                 组状态/文件/实体汇总
  POST /api/groups/{gid}/export?mode=ai|human&file=all|{job_id}
  POST /api/groups/{gid}/verify          组级复检（残留+一致性+勾稽）
  GET  /api/groups/{gid}/map             映射（需用户显式确认；仅本地）
```

**实现要点**

1. `jobs.Job` 允许注入共享 `Session`（当前 `job.session = factory(Session)`，`jobs.py`）；
2. **先全组检测、再按固定顺序统一 decide**：`surrogate` 的随机种子含 `len(session.map)`，
   边检测边决策会受文件顺序影响，必须两阶段；
3. `vault.save_mapping(group_id, merged_map)`；组级 `rehydrate`；`audit` 表加
   `group_id`/`file_role`（SQLite 迁移）；`wipe` 扩展到组；
4. 金额：**批级同币种比例因子**（所有金额乘同一因子，保持勾稽）+ 大写联动；
5. 勾稽引擎：单证类型识别 → 字段抽取（表格优先）→ 归一化 → J1–J15
   （见 05 文档 §3.3）→ 报告"通过/不一致（阻断）/缺字段（提示）"；
6. 前端：组控制台（文件列表+角色、共享映射面板、一致性/勾稽报告、批量导出）；
7. 验收：同原值跨文件映射一致率 100%、J1–J12 通过率 100%、每文件残留 0、
   导出号码校验合法率 100%。

### 其它已知边界

- 规则对"纯图片无文字"的贸易单证无效（先 OCR，再靠规则；OCR 误读会破坏号码校验，
  低置信候选可能被 validate 丢弃，表现为漏检——P1 需低置信提示）；
- `mesh` 格式不启用 trade-cn（`scope` 不含 mesh）；
- 夹具为纯文本；PDF/Office 形态的贸易单证回归由 05 方案 P1 的样例集补充。

---

## 8 回滚与验收

**回滚**：删除上述 5 个新增路径即可（无既有文件被修改；若并行域已改动其它文件，按各自归属回滚）。

```powershell
Remove-Item app\server\yinzhan\rules\trade-cn.yaml
Remove-Item app\server\yinzhan\detectors\trade_validators.py
Remove-Item app\tests\test_trade.py
Remove-Item app\tests\fixtures\trade -Recurse
Remove-Item docs\impl\trade.md
```

**交付前验收命令（按顺序）**：

```powershell
python -X utf8 app/tests/test_trade.py      # 期望：全部通过（>=135 项）
cd app\server; python -X utf8 ..\tests\e2e.py   # 期望：5/5、残留 0
cd ..\..; python -X utf8 app/tests/regression.py  # 期望：回归通过、硬项全绿
git status --short                          # 期望：仅新增本域 5 个路径（+并行域改动）
```

---

*本文档只描述 trade 域 P0；跨单证 P2 与替换工厂 P1 未实现，按上述接口需求另行排期。*
