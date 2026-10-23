# 身份证校验位策略 QA 评估（2026-10-23）

- 范围：`redact_text` / 文档脱敏链路里「校验位不合法身份证号漏识别」缺口。
- 结论：**不接受「校验位硬门槛」的现状**。校验位只作为**置信度信号**：形状仍遮，
  低置信结果进 `pending` 人工复核，而不是整串丢弃。
- 本次改动只涉及规则 YAML + 测试 + 本 QA 文档；未改 `mcp_server.py`、`regex_det.py`
  通用逻辑、前端与发布/基线文件（基线不自行更新，差异见 §11）。

## 1. 结论摘要

| 项目 | 修复前 | 修复后 |
| --- | --- | --- |
| 工单文本 `张伟，手机号13812345678，身份证号110101199001011234` | 仅命中手机号；身份证号原样漏出 | 手机号 + 身份证号各 1 个实体；身份证号被换成合法假号 |
| 校验位错误的 18 位形状串 | 整串丢弃（静默漏 PII） | `ID_CARD` + `pending`（standard/strict），导出仍遮挡 |
| 合法身份证号 | `accepted`（`cn.idcard`） | 不变：`accepted`、`confidence=0.99`、单实体 |
| 规则总数 | 113 | 114（`common` 自有规则 17→18） |

**评估结论：不建议**维持「校验位不通过就丢弃」的硬门槛。理由：

1. 身份证号是高敏感 L3 标识符，出现校验位错误的原因很常见：OCR 把数字读错、
   手工录入错位、伪造/样例号、出生日期越界、打印截断后再补位等。这些场景下
   号码**依然指向真实个人**，按「校验失败=非身份证」丢弃会造成合规级漏遮。
2. `validate` 的现状是**硬丢弃**（`app/server/yinzhan/detectors/regex_det.py:334`
   `if not value or not self.validator(value): continue`），没有「低置信保留」通道，
   因此必须在规则层补一条形状兜底。
3. 校验位的正确用法是**证据强度**：校验通过 → 高置信自动接受；校验不通过但
   形状命中 → 低置信、待确认。这样既不把真实 PII 漏掉，也把误报成本交给人工
   复核，而不是交给「漏遮」。

## 2. 复现与根因

### 2.1 复现

工单文本（`redact_text`，standard 档）：

```text
张伟，手机号13812345678，身份证号110101199001011234
```

修复前：`by_type = {"手机号": 1}`，身份证号原样留在 `redacted` 里。
修复后：`by_type = {"手机号": 1, "身份证号": 1}`，`redacted` 中身份证号变成
`110101196908044199`（虚构假号，校验位合法）。

该号码：前 17 位为 `11010119900101123`，GB 11643 应有校验位 `7`，实际为 `4`；
出生日期 `1990-01-01` 本身合法，因此是**纯校验位错误**的典型样例。

### 2.2 根因链

1. `app/server/yinzhan/rules/common.yaml` 的 `cn.idcard`：同一条 18 位形状 regex，
   配 `validate: cn_id_checksum`，`replace: surrogate`，`priority: 120`。
2. `app/server/yinzhan/detectors/regex_det.py:59-82` 的 `cn_id_checksum` 同时校验：
   长度/字符集、省级行政区划码、GB 11643 校验位、出生日期可解析且
   `1900-01-01 <= 生日 <= 今天`。
3. 同一文件 `regex_det.py:334`：`validate` 不通过直接 `continue`，**没有降级通道**；
   于是「形状正确但校验位/日期不合法」的号码在检测阶段就消失，后续
   `decision`/导出/复检全部无从谈起。
4. `pipeline.detect` 的置信度闸门（`pipeline.py:96-102`）本来支持
   `standard` 档「低于 `pending_below=0.80` 转 pending」，说明产品已有
   「低置信待复核」机制，只是 `cn.idcard` 没有用它。

## 3. 策略决策：校验位 = 置信度信号

采用两段式（高置信规则 + 形状兜底规则），行为定义：

| 输入 | 命中规则 | status | 导出行为 |
| --- | --- | --- | --- |
| 校验位/日期/省份码合法 | `cn.idcard`（conf 0.99） | `accepted` | surrogate（AI 换合法假号）/ human 涂黑 |
| 形状命中但 `cn_id_checksum` 不通过 | `cn.idcard_shape`（conf 0.66） | `pending`（standard/strict） | 仍然 surrogate；human 涂黑、AI 换假号 |
| 形状都不命中（非身份证形） | 不命中 | — | 不处理 |

关键点：

- **`pending` 不等于不遮**。`pending` 只是对照台/`counts` 里的「待人工确认」状态；
  `pipeline.decide` 会给所有非 rejected 实体算决策，`jobs._export_to` 导出时
  只要 `decision is not None` 就纳入（human 统一改 `black`、ai 用 surrogate）。
  因此 pending 的身份证号在**两种导出模式里都会被遮**，人工复核只影响
  accept/reject 的产品流程，不构成泄漏面。
- **同一值绝不重复出实体**：兜底规则用 `unless_validate: cn_id_checksum` 与
  `cn.idcard` 互斥；校验通过的值只会走高置信规则。`pipeline._merge` 另按
  `(etype, value)` 合并，同值多规则命中时只保留一个实体（见 §7 的 Luhn 用例）。

## 4. 实现（规则 YAML）

`app/server/yinzhan/rules/common.yaml` 在 `cn.idcard` 之后新增：

```yaml
  - id: cn.idcard_shape
    entity: ID_CARD
    label: 身份证号
    level: L3
    scope: [pdf, docx, xlsx, text, image, tiff, mesh]
    priority: 118          # 低于 cn.idcard(120)、高于 cn.bankcard(110)
    detect:
      - type: regex
        pattern: '<与 cn.idcard 完全相同的 18 位形状 regex>'
        unless_validate: cn_id_checksum   # 校验通过的走 cn.idcard，避免重复/降级
        confidence: 0.66                  # standard 档 min_conf=0.60 → 保留；<0.80 → pending
    replace: {strategy: surrogate}
```

- 为什么用 `unless_validate` 而不是 `validate: none`：如果兜底规则对所有形状串
  都命中，合法身份证会被两条规则同时检出，`pipeline._merge`（`pipeline.py:129-130`）
  会把 low 规则的 pending 状态合并进来，把合法号降成 pending，违背「合法号仍
  accepted」。`unless_validate` 让两条规则**互斥**，这是不改检测器代码的最简做法。
- 为什么 `priority: 118`：与 `cn.bankcard`（110）重叠时身份证形状优先；
  与 `cn.uscc`（85）等重叠时 L3 也优先。所有重叠场景最终只保留一个实体。
- `confidence: 0.66`：落在 [0.60, 0.80)，standard 档既不会被 `min_conf`
  丢弃、又低于 `pending_below=0.80`，恰好进入「待确认」。

## 5. 方案选择：全形状 vs 上下文限定

**选择：全形状（不设 `near` 上下文），不做上下文限定。**

理由：

1. **裸号才是最危险的泄漏面**。表格单元格、系统日志、聊天记录、OCR 断行/分栏、
   表单填空经常只有号码没有「身份证号」标签；上下文限定方案正好漏掉这些场景。
   本次选择的方案对「`110101199001011234` 是一串没有上下文提示的裸号码」
   同样兜住（测试已固化）。
2. **形状 regex 本身已是强约束**：前 6 位非零数字 + `18/19/20` 年份 + 合法月日 +
   17 位数字 + 校验字符 + 两侧词边界，随机 18 位数字命中率仅约 0.10%（§6）。
3. **误报成本低且可控**：误报只会多一个 `pending` 实体，导出时多遮一段数字
   （human 黑块/AI 假号），人工在对照台 reject 即可；漏遮 PII 的代价是合规风险。
   隐私优先场景下这个不对称性支持「宁可多遮」。
4. 上下文信号没有丢：人工复核时对照台能看到该实体在上下文中的位置；弱/强上下文
   的行为一致（均 pending），避免「同一泄漏因写法不同而漏掉」。

不选上下文限定的代价：订单号/流水号等 18 位数字偶发命中形状规则时会被标为
`pending`（§6 量化）。这一成本被限制在「多一次人工确认」，不会丢数据。

## 6. 误报评估（量化）

用与规则完全相同的 regex + `cn_id_checksum` 在合成样本上统计
（n=300,000 均匀随机 18 位数字，首位 1–9；另各 100,000 条时间戳风格）：

| 样本类型 | 兜底命中率 | 说明 |
| --- | --- | --- |
| 均匀随机 18 位数字 | 形状命中 **0.103%**（308/300,000），其中校验不通过 **0.102%**（305） | 形状 regex 已排除 99.9% 随机数字 |
| 订单号 `YYYYMMDD`+10 位 | **0.428%** | 日期位与身份证年/月/日位置偶发对齐 |
| 订单号 `YYYYMMDDHHMMSS`+4 位 | **1.05%** | 18 位时间戳更易偶发对齐 |

实际语料核对（5 个既有样例 + `app/tests/fixtures` 下 41 个文本类夹具）：

- 样例中所有 18 位数字串（对账单.xlsx 3 个、住院病历.docx 1 个、
  委托代理合同.pdf 1 个）全部是**校验位合法的真实形状号**，修复后仍只走
  `cn.idcard`（accepted），**没有新增实体、没有新增 pending**；
- 夹具中 18 位数字串也全部为合法身份证号；修复后未出现新的误报实体
  （`test_pipl_rules.test_baseline_samples_no_unexpected_fp` 及回归门禁复核）。

风险场景与处理建议：

| 场景 | 风险 | 现策略行为 | 处理 |
| --- | --- | --- | --- |
| 订单号/流水号恰好 18 位且年月日位对齐 | 误报 pending | 遮挡 + 待确认 | 对照台 reject；不丢失原文信息给用户 |
| 纯随机数字碰巧形状命中 | 同上（≈0.1%） | 遮挡 + 待确认 | 同上 |
| 18 位银行卡号 | 与银行卡规则重叠 | 形状优先 → `ID_CARD` pending，只遮一次 | 类型可能标错，但已遮挡；见 §7 |
| 19 位雪崩 ID / 含字母编号 | 不命中 | 不处理 | 非身份证形状，属预期 |

## 7. 规则重叠与去重

1. **`cn.idcard`（120）vs `cn.idcard_shape`（118）**：`unless_validate` 互斥。
   合法号只出 1 个实体、走 `cn.idcard`（测试断言 `rule.id == "cn.idcard"`）。
2. **`cn.bankcard`（110，Luhn + `unless_validate: cn_id_checksum`）**：
   - 18 位形状 + 身份证校验通过 → 银行卡规则已被 `unless_validate` 排除；
   - 18 位形状 + 身份证校验不通过 + Luhn 通过（构造值 `110101199001011233`）：
     两条规则都命中同一 span，`pipeline._merge` 保留 `ID_CARD`（priority/level 更高，
     同长 span 不再出 BANK 实体）。测试断言「恰好 1 个实体、类型 ID_CARD、无 BANK」。
   - 16/17/19 位银行卡不受影响（形状 regex 只认 18 位）。
3. **`cn.uscc`（85）**：带字母的统一社会信用代码不受影响。纯数字形状串中约
   3.9%（12/305）会恰好通过 USCC 校验位，但这种纯数字串本来就不会命中
   `cn.uscc`（其 regex 要求至少一个字母），现在会被 `ID_CARD` 兜住并遮挡
   —— 属于覆盖增强；类型待人工判断。
4. **`table.id_card` 等表格规则（priority 12）**：同 `entity=ID_CARD` + 同 value 时
   由 `_merge` 合并为一个实体；合法表格身份证仍由 common 规则捕获，行为不变。
5. **`pipl.express_no` / `pipl.ticket_no` 等数字编号规则**：优先级更低、级别更低
   （L2），同 span 冲突时保留 `ID_CARD`（L3），不会重复涂黑、不会重复实体。
6. `redact_text` 只按 `det.entities` 的 span 替换，同值只替换一次。

## 8. AI / human / redact_text 行为差异

| 路径 | pending 身份证号的最终效果 |
| --- | --- |
| `redact_text`（MCP） | 替换为 surrogate 假号（本工具无 human 模式） |
| 文件导出 human（涂黑版） | `jobs._export_to` 统一改 `Decision(black=True)` → 黑块覆盖 |
| 文件导出 ai（换名版） | surrogate 假号（`_fake_id` 保证同省前 6 位、假生日、重算校验位） |
| 对照台/统计 | `status=pending` 计入「待确认」；accept/reject 只改复核状态 |

需要说明的是：`pending` 实体在**导出时不会被跳过**（`decision` 已生成），
所以「待确认」不会造成漏遮；但用户 reject 后该值会按 `keep` 保留，属于用户显式决策。

## 9. 档位矩阵（strictness）

`config.STRICTNESS`：`loose(min_conf=0.80)` / `standard(0.60, pending<0.80)` /
`strict(0.35, pending<0.95)`；Web、API、MCP 的默认档均为 **standard**
（`app/web/app.js`、`api.py:130`、`jobs.py:133`）。

| 档位 | 形状兜底（0.66） | 说明 |
| --- | --- | --- |
| standard（默认） | 保留，`pending` | 满足本次目标：遮挡 + 待人工复核 |
| strict | 保留，`pending` | 更严格档同样遮挡 |
| loose | **丢弃**（0.66 < 0.80） | 已知取舍：loose 的产品语义是「少打扰」。会漏校验位异常的号 |

**这是本策略的已知残余风险**：选择 loose 档的用户仍可能漏遮校验位异常身份证号。
要覆盖 loose 只有两种办法，均超出「只改规则 YAML」的本次范围：
(a) 把兜底规则 confidence 提到 ≥0.80：会变成 accepted，失去 pending 复核语义；
(b) 调整 `STRICTNESS`/管线阈值或增加按档位的规则开关（改配置/管线）。
建议在后续产品决策中二选一，并同步更新本表与测试
`test_idcard_shape_strictness_matrix`。

## 10. 测试与结果

新增/修改的测试（`app/tests/test_pipl_rules.py`，新增 8 项，总计 19 项）：

1. `test_idcard_shape_rule_contract`：兜底规则复用同一 regex、无硬 validate、
   `unless_validate=cn_id_checksum`、conf∈[0.60,0.80)、priority 低于 `cn.idcard`；
2. `test_idcard_valid_checksum_still_accepted`：合法号 `310104198703124510`
   仍 accepted、走 `cn.idcard`、单实体、假号自身校验位合法；
3. `test_idcard_bad_checksum_shape_pending_and_redacted`：工单串
   `110101199001011234` → ID_CARD、pending、surrogate；并**走真实 MCP
   `redact_text` 工具路径**断言原值不再出现、`by_type` 含「身份证号」1 个；
   再用同一决策模拟 human 涂黑导出，断言原文被同长度黑块覆盖；
4. `test_idcard_shape_invalid_date_and_x_suffix_pending`：非法日期（1990-02-30）、
   未来日期（2099-01-01）、`X` 结尾且校验位错误 → 均 pending；
5. `test_idcard_shape_bare_number_no_context_pending`：无上下文的裸号同样兜住；
6. `test_idcard_non_shape_random_no_false_positive`：订单号/随机 18 位/19 位银行卡/
   含字母 USCC 均不产生 ID_CARD；
7. `test_idcard_overlap_bankcard_single_entity`：形状+Luhn 重叠值只保留 1 个
   ID_CARD 实体、无 BANK 实体；
8. `test_idcard_shape_strictness_matrix`：standard/strict=pending，loose=丢弃
   （锁定已知取舍，改策略必须同步改测试与本文档）。

执行结果：

| 命令 | 结果 |
| --- | --- |
| `python -X utf8 app/tests/test_pipl_rules.py` | **19/19 通过，exit=0** |
| `python -X utf8 app/tests/test_visual.py` | **68/68 通过，exit=0** |
| `python -X utf8 app/tests/regression.py` | **通过：250 项硬检查全绿，exit=0**（耗时 138.54s） |
| `python -X utf8 app/tests/test_table_column.py`（额外） | 9 组 / 180 项断言全绿，exit=0 |
| `python -X utf8 app/tests/test_ai2_rules.py`（额外） | 50 项通过、0 失败，exit=0 |
| `python -X utf8 app/tests/test_mcp_v2.py`（额外） | 36 个用例 OK，exit=0（MCP 契约未受影响） |

回归门禁关键明细：

- 「已有规则不减少」：自有 114 条（基线 113），通过；
- 「各格式编译规则数不减少」：docx=104 / image=90 / mesh=14 / pdf=99 /
  text=104 / tiff=90 / xlsx=107（均 +1），通过；
- 「新增规则 +1 条（允许，不算回归）」，通过；
- 5 个基线样例实体数与基线**逐一致**：住院病历.docx=9、名片.png=6、
  委托代理合同.pdf=15、对账单.xlsx=19、正射影像.tif=3；无「新增/多识别实体」软提示；
- 双导出（ai/human）全部成功、残留合计 0、元数据清理通过；
- 软提示 2 条：`git HEAD 已变化`、`引擎/样例有未提交改动（common.yaml，测试中暂态）`，
  均为开发态提示，非回归。

## 11. 基线差异（不自行更新 baseline）

只报告，不更新 `app/tests/baseline/baseline.json`；由 Lead 决定是否重录。

| 基线字段 | 基线值 | 当前值 | 判定 |
| --- | --- | --- | --- |
| `rules.defined_rules_total` | 113 | **114（+1）** | 新增规则，软项（允许） |
| `rules.packs.common.own_count` | 17 | **18** | 同上 |
| `rules.compiled_by_fmt.pdf` | 98 | **99** | 同上 |
| `.docx` | 103 | **104** | 同上 |
| `.xlsx` | 106 | **107** | 同上 |
| `.text` | 103 | **104** | 同上 |
| `.image` | 89 | **90** | 同上 |
| `.tiff` | 89 | **90** | 同上 |
| `.mesh` | 13 | **14** | 同上 |
| 5 个样例的实体数/类型 | 9/6/15/19/3 | 9/6/15/19/3（回归实测） | 不变（合法身份证不触发兜底） |
| 规则包删减/迁移 | 无 | 无 | 硬项通过 |

`app/tests/test_pipl_rules.py::test_baseline_samples_no_unexpected_fp`
（pipl vs 全量对比）通过：新增规则在 common 包，不属于 pipl 增量，对比口径不受影响。

## 12. 残余风险与后续建议

1. **loose 档漏遮**（§9）：已知取舍，需产品决策（改 STRICTNESS 或档位化规则）。
2. **OCR 字符级污染**：正则要求 17 位数字 + `[\dX]`，若 OCR 把数字读成 `O/0`、
   截断一位、插入空格的这类号码，形状兜底仍匹配不到（与修复前一样）。
   建议后续评估「宽松形状（允许 1–2 个 OCR 混淆字符）」的独立规则。
3. **类型标错成本**：18 位 Luhn 合法数字会被标为 ID_CARD 而非 BANK（§7）；
   两者都会被遮挡，AI 视图读到的假号长度一致，仅实体类型不同。
4. **误报 pending 的人工成本**：时间戳风格 18 位订单号命中率约 0.4%–1%，
   批处理大量此类文档时会产生成批 pending；可考虑后续在规则层加
   `exclude_near`（如「订单号/流水号」）做定向降误报，但需先解决
   「同一行先出现订单号、后出现真实身份证号」的窗口误排除问题。
5. 17 位「缺位身份证」在普通文本里仍不识别（仅表格列规则覆盖，属既有边界）。

## 13. 结论

- **是否接受校验位硬门槛：不接受**（会静默漏 PII，且失败原因常见）。
- **采纳策略：校验位只作为置信度信号，形状仍遮，低置信进 pending 人工复核**。
- 合法身份证行为不变（accepted、高置信、单实体）；校验位异常号码不再漏遮；
  误报率已量化（随机 18 位约 0.1%、时间戳订单号 0.4%–1%），且误报代价仅为
  多一次人工确认与多余遮挡，不丢数据。
- 本次只改规则 YAML + 测试 + QA 文档；MCP/前端/发布文件与现有 baseline 未动。
