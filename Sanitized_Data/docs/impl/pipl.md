# PIPL 补充 P0 实现说明（txt/text 规则 + 纯数值单元格）

> 状态：已实现（P0）· 2026-10-20 · 对应 task-17 / 调研 `docs/research/08b-文本表格与隐藏数据查漏补缺.md`
> 红线：**不 push**；未改 `common/finance/legal/trade` 规则、`pipeline/jobs/api/registry`、`udm.py`、`surrogate.py`。临时/测试数据全部虚构，不提交真实 PII。
> 测试：`app/tests/test_pipl_rules.py`（11/11）；`e2e.py` 5/5、残留 0；`regression.py` 见交付汇报。

---

## 1. 交付物

| 文件 | 类型 | 内容 |
|---|---|---|
| `app/server/yinzhan/rules/pipl-cn.yaml` | 新增 | PIPL 补充包：18 条文本规则 + 3 条数值单元格规则（`include: [common]`，默认启用） |
| `app/server/yinzhan/detectors/pipl_validators.py` | 新增 | 19 个 `pipl_*` 校验器（导入即注册，命名空间隔离） |
| `app/server/yinzhan/detectors/numeric_det.py` | 新增 | `numeric_cell` 检测器（表头语义 + 数值/日期类型），元数据登记表 |
| `app/server/yinzhan/parsers/xlsx_parser.py` | 修改 | ①Block.attrs 增加 `numeric/date/number_format`（只加元数据）；②导出对 `origin=numeric_cell` 的单元格按**数字**写回（保留 `number_format`，公式/求和可重算），账号类写字符串 |
| `app/server/yinzhan/replacers/amount_consistency.py` | 修改 | 纯数值金额：无单位按"元"、小数位取 `number_format`；同值/合计/保形逻辑不变 |
| `app/tests/test_pipl_rules.py` + `app/tests/fixtures/pipl/text_p0.txt` | 新增 | 11 项测试（规则/校验器/文本正例/样例误报对比/数值写入/日期/账号/工资/流水） |
| `docs/impl/pipl.md` | 新增 | 本文档 |

**未触碰**：`common.yaml`、`finance-cn.yaml`、`legal-cn.yaml`、`trade-cn.yaml`（Lead 已统一把 10 条规则 scope 扩到 image/tiff，与本批无关）、`registry.py`（Lead 已把 `pipl_validators/numeric_det/table_det` 加入 `_PLUGIN_FALLBACK`）、`pipeline.py`（Lead 已加 PDF 隐藏数据校验）、`jobs.py`/`api.py`、`surrogate.py`。

---

## 2. 文本规则（pipl-cn.yaml，18 条）

| # | 规则 id | entity / label | 级别 | 校验器 | 关键约束 |
|---|---|---|---|---|---|
| 1 | `pipl.social_id` | SOCIAL_ID / 社交账号 | L2 | `pipl_social_wxid`、`pipl_social_generic` | `wxid_*` 或 平台标签+冒号+ID（QQ/微信/微博/抖音/小红书/快手/钉钉/Telegram/Skype）；named group 只取 ID |
| 2 | `pipl.nickname` | NICKNAME / 昵称/群名 | L2 | `pipl_nickname` | 标签+冒号；confidence 0.78 → 标准档进"待确认" |
| 3 | `pipl.phone_grouped` | PHONE / 手机号 | L2 | `pipl_phone_digits` | 允许空格/短横线分组（common.cn.phone 只认连续 11 位）；label 与 common 一致避免门禁类型变化 |
| 4 | `pipl.express_no` | EXPRESS_NO / 快递/物流单号 | L2 | `pipl_express_no` | 前缀白名单（SF/JD/JT/YT/YD/ZTO/STO/EMS…）+8–16 位；或纯数字 10–20 位 + near `快递/物流/运单/面单` |
| 5 | `pipl.ticket_no` | TICKET_NO / 票号 | L3 | `pipl_ticket_no` | 机票 3+10 位 / 8–12 位 + near `票号/客票/机票/行程单/火车票/车票` |
| 6 | `pipl.card_cvv` | CARD_CVV / 信用卡安全码 | L3 | `pipl_cvv` | `CVV/CVC/安全码/卡背面三位` + 3–4 位 |
| 7 | `pipl.vin` | VIN / 车架号 VIN | L2 | `pipl_vin` | 17 位、不含 I/O/Q、ISO 3779 第 9 位校验；near `车架号/VIN/车辆识别代号` |
| 8 | `pipl.imei` | DEVICE_ID / IMEI/设备号 | L2 | `pipl_imei` | 15 位 + Luhn；near `IMEI/MEID/串号/设备号` |
| 9 | `pipl.mac` | MAC / MAC 地址 | L2 | `pipl_mac` | 6 组十六进制，`:`/`-` 分隔（格式强特征，无 near） |
| 10 | `pipl.device_sn` | DEVICE_SN / 设备序列号 | L2 | `pipl_device_sn` | `SN/S/N/序列号/设备序列号/设备编号/机身码` + 6–24 位；confidence 0.78 → 待确认 |
| 11 | `pipl.ip_addr` | IP_ADDR / IP 地址 | L2 | `pipl_ipv4`、`pipl_ipv6` | 标准库 `ipaddress` 校验；near `IP/登录/来源/客户端` |
| 12 | `pipl.address_unit` | ADDRESS / 门牌/楼栋/单元/室 | L2 | `pipl_address_unit` | 必须含 `栋/幢/号楼/座`（避免把"12号"当门牌）；near `地址/住址/门牌/房间/工位/小区` |
| 13 | `pipl.doorplate` | ADDRESS / 门牌/房间号/工位 | L2 | `pipl_doorplate` | 标签式（房间号/工位/门牌）+ 值 |
| 14 | `pipl.flight_no` | FLIGHT_NO / 航班号 | L3 | `pipl_flight_no` | 2 位航司代码 + 3–4 位数字 + near `航班/登机/行程/机票/值机` |
| 15 | `pipl.train_no` | TRAIN_NO / 车次 | L3 | `pipl_train_no` | `G/C/D/Z/T/K` + 1–4 位 + near `车次/列车/高铁/动车/火车` |
| 16 | `pipl.seat_no` | SEAT_NO / 座位号 | L2 | `pipl_seat_no` | `排号+A–F` + near `座位/座号/舱位/铺位` |
| 17 | `pipl.badge` | EMPLOYEE_NO / 工号/工牌 | L2 | `pipl_employee_no` | 标签 `工牌/工号/员工号/员工编号` + 2–20 位（含数字） |
| 18 | `pipl.datetime` | TIME / 精确时间 | L2 | — | **默认 `enabled: false`**（日期默认不动，避免改变病历/财务日期与基线）；P1 配 `time_blur` 后开启 |

设计要点：

- **弱特征必须 near + 校验器**：快递/票号/SN/IP/门牌/航班/车次/座位/工牌全部有 near 或标签锚点；VIN/IMEI/机房号有严格校验。
- **named group `(?P<value>...)`** 用于"标签+值"规则（社交 ID/昵称/CVV/SN/门牌/工牌），避免 `strip_prefix` 把值里合法的前缀（如 SN）也剥掉。
- **不处理日期**：唯一涉及日期的规则 `pipl.datetime` 默认关闭；候选扫描实测只有日期会命中基线（住院病历 `2025-03-17/29`），故不默认启用。
- **与 finance/trade 的规则边界**：`pipl.ticket_no` 加了 `exclude_near: [发票, 发票号码, 发票代码]`——"发票号码"里含 "票号" 子串，不加会把 `cn.invoice_no` 的 8 位发票号码抢成 TICKET_NO（L3 覆盖 L2）；加排除后 `test_finance.py` 21/21 保持全绿。
- 替换仍走 `surrogate`；VIN/IMEI 等"假值是否为合法校验位"需要 Lead 在 `surrogate.py` 加工厂（本批未改），当前假值只保证同长度/同字符类别，**不保证校验位合法**（AI 读场景可接受，P1 再补）。

### 2.1 校验器（pipl_validators.py，19 个）

`pipl_phone_digits`、`pipl_social_wxid`、`pipl_social_generic`、`pipl_nickname`、
`pipl_express_no`、`pipl_ticket_no`、`pipl_cvv`、`pipl_vin`（+`vin_check_digit`）、
`pipl_imei`（Luhn）、`pipl_mac`、`pipl_device_sn`、`pipl_ipv4`、`pipl_ipv6`（stdlib `ipaddress`）、
`pipl_address_unit`、`pipl_doorplate`、`pipl_flight_no`、`pipl_train_no`、
`pipl_seat_no`、`pipl_employee_no`。

命名统一 `pipl_` 前缀，避免与 `fin_*` / `trade_*` / `regex_det` 同名覆盖（本项目已实测导入顺序覆盖问题）。

---

## 3. 纯数值单元格（B 部分）

### 3.1 解析器：只加元数据

`xlsx_parser.parse` 在生成 Block 时新增：

```python
attrs = {
    "formula": bool,        # 原有
    "numeric": bool,        # int/float 且非 bool
    "date": bool,           # datetime/date/time
    "number_format": str,   # 如 "#,##0.00" / "0%" / "General"
}
```

不改变单元格文本/解析/导出语义；老代码读 `attrs["formula"]` 与 `get("formula")` 的用法保持兼容。

### 3.2 检测器：`numeric_cell`（detectors/numeric_det.py）

- 仅 `.xlsx/.xlsm`；按 `block.ref={sheet,row,col}` 重组表格；
- 在首 `header_rows`（默认 3）行里找"命中列头关键词最多的一行"作为表头（兼容标题行在上的报表："2026 年度利润表" 在第 1 行、表头在第 2 行）；
  **表头行必须是纯文本行**：含数值/日期/公式的行是数据行（否则 "收入A" 这类含"收入"关键词的数据行会被误判成表头）；
- 列头命中后，只处理"表头行以下 + attrs 匹配"的单元格：
  - `mode: amount`：`attrs.numeric=True`、非公式、`number_format` 不含 `%` → AMOUNT；
  - `mode: account`：数值且数字位 ≥ `min_digits`（默认 6）→ ACCOUNT；
  - `mode: date`：`attrs.date=True` → DATE（规则是 `keep`，默认不修改）；
- 实体元数据（`kind/number_format/decimals/sheet/cell`）登记在模块级 `_META[entity_id]`：因为 `Entity` 没有 attrs 字段（`udm.py` 由 Lead 维护），跨模块用 `numeric_det.get_numeric_meta(entity.id)` 读取；导出/预览与检测同进程，安全。

### 3.3 金额引擎：无单位数值按"元"

`replacers/amount_consistency._parse_token` 增加分支：`origin=numeric_cell` 且 `kind=amount` 的实体 → `canonical=Decimal(value)`（元）、`decimals=meta["decimals"]`；其余"组内统一因子 k、合计=分项重算、同值同映射、保形"逻辑完全复用。

**零金额不缩放**：0 经 k 缩放仍是 0，原"必须变"守卫只在非零值时补一个最小单位——否则 0 会被改成 1，破坏合计/勾稽。

### 3.4 导出：写回数字而不是字符串

`xlsx_parser.export` 对 `origin=numeric_cell` 的实体不做字符级替换，改为整格写回：

- AMOUNT：把新值解析为 `int`/`float` 写入（保留原 `number_format`）→ Excel 公式、求和、图表正常；
- ACCOUNT：写字符串（避免前导零/精度丢失）；
- 数值被黑（human 模式）：仍走原"清空 + 纯黑填充"路径；
- 值级传播与普通字符替换均跳过数值单元格；导出 warning 会写"已按数值写回 N 个单元格"。
- 成品仍 `fullCalcOnLoad=True`，打开 Excel 自动重算（openpyxl 不保留公式缓存，属预期）。

### 3.5 实测（虚构数据）

| 夹具 | 结果 |
|---|---|
| 利润表（标题行 + `项目/本期金额`，5,000,000 + 7,000,000 = 12,000,000，`#,##0.00`） | 3 个 AMOUNT 全识别；金额引擎 `合计=分项`；导出后 B3/B4/B5 为 float、`#,##0.00` 保留；`C3=SUM(B3:B4)` 公式保留、`fullCalcOnLoad=True`；`verify residual=0` |
| 工资表（应发/实发，General 格式） | 3 个唯一金额识别（12000 出现两次合并为一个多 span 实体）；同一文档同一缩放因子（整数舍入误差 ≤0.0001） |
| 流水（期初 100,000 + 发生 20,000 = 期末 120,000，另含 0 元行） | 合计按分项重算，勾稽通过；0 保持 0（不会被"必须变"改成 1） |
| 数值账号（15 位）+ 日期 + 百分比列 | 账号按字符串写回；日期 `keep` 类型不变；`0%` 列不识别为金额 |
| 真实 JobManager 路径（标题行 + `#,##0.00` + `=SUM` 公式） | 导出后 B2/B3/B4 为数字 `4709910/6593874/11303784`（合计=分项和），`number_format` 保留、公式保留，`verify residual=0` |
| 既有 5 样例（无 pipl 包 vs 全量） | 唯一新增：`对账单.xlsx` 的 4 个纯数值金额 AMOUNT（origin=`numeric_cell`，属预期）；其余 0 新增、0 丢失 |

> 说明：`对账单.xlsx` 的 `金额` 列现在是纯数值 → 被识别并被金额引擎缩放；这是表格 P0 的预期收益，回归软记录会体现"新增识别"，qa 重录基线时需注明。

---

## 4. 测试与门禁

```powershell
# PIPL/数值 P0 单测（10 项，临时目录隔离）
python -X utf8 app/tests/test_pipl_rules.py

# 最短冒烟（5 样例）
cd app\server; $env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py

# 完整红线门禁
python -X utf8 app/tests/regression.py
```

本次实测（2026-10-20 12:5x）：

- `test_pipl_rules.py`：**11/11 通过**；域回归 `test_finance.py` 21/21、`test_table_column.py`（trade）9 组 156 断言、`test_trade.py` 135 项均通过；
- `e2e.py`：**5/5、双导出残留 0**；对账单类型新增 `金额（数值）×4`、`表格账号列×1`（后者为 trade 的 `table-cn` 文本列规则，非本包）；
- `regression.py`：见交付汇报（预期"新增识别/新增规则"为软提示，硬检查应全绿）。

---

## 5. 需要 Lead / qa 接的点

1. **registry**：`_PLUGIN_FALLBACK` 已由 Lead 加入 `pipl_validators / numeric_det / table_det` ✓；开发态 `pkgutil` 自动加载，无需再改。
2. **规则包默认启用**：`pipl-cn` 与 `table-cn`（trade 新增）都会被 `load_packs()` 自动发现并默认启用；前端包列表/`/api/packs` 会多两个包，无需改代码（如需 UI 文案请前端确认）。
3. **scope**：Lead 已把 10 条规则 scope 扩到 image/tiff（非本批），与 pipl 包无冲突；本包规则 scope 均为 pdf/docx/xlsx/text/image/tiff 或 xlsx（数值）。
4. **verify**：Lead 已在 `pipeline.verify` 增加 PDF 隐藏数据检查（`embedded_files/pdf_annotations/pdf_forms/pdf_js`），与数值无关；数值单元格残留仍走"重解析 + 文本残留"路径（实测 0）。
5. **qa 基线**：`对账单.xlsx` 新增 4 个 `AMOUNT` 数值实体（预期）→ 属"新增识别"软变化；基线重录时请注明原因；`run_all.py` 的测试清单可加 `pipl` 键（本次未改，属 qa 文件）。
6. **P1（未做，已在文档注释）**：`pipl.datetime` 默认关闭，启用需 `replacers/time_blur.py`（时刻→时段/整组偏移）；VIN/IMEI/信用卡假值的合法校验位需要 `surrogate.py` 加工厂（Lead 所有）；表头文本列级由 trade `table-cn` 负责，本包只做数值/日期单元格。

---

## 6. 风险与回滚

| 风险 | 说明/缓解 |
|---|---|
| 新增规则误报 | 5 个基线样例 0 非预期误报（唯一新增为对账单数值金额，属预期）；弱特征全部 near+校验器 |
| 数值写回破坏公式 | 只写数字、保留 `number_format`、`fullCalcOnLoad=True`；测试断言公式单元格未被替换、B3+B4=B5 |
| 金额精度/格式 | 小数位取 `number_format`（`#,##0.00` → 2 位）；General → 整数；float 精度对财务可接受（P1 可改 Decimal 写入） |
| 账号数值精度 | 15 位以内安全；16 位以上 Excel 本身会丢精度，账号在 Excel 中通常应存文本，本包数值账号写回字符串 |
| 日期默认处理 | `pipl.numeric_date` 规则 `keep`，`pipl.datetime` 默认禁用；不改变基线 |
| 回滚 | 删除 `pipl-cn.yaml`、`pipl_validators.py`、`numeric_det.py`、`test_pipl_rules.py`、`fixtures/pipl/`；`git checkout -- app/server/yinzhan/parsers/xlsx_parser.py app/server/yinzhan/replacers/amount_consistency.py`（或反向 diff 撤销 attrs/数值写回分支） |
