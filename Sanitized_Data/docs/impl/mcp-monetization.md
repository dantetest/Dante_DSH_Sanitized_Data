# MCP 服务商业化设计（定价 / 套餐 / 门禁 / 购买 / 团队·机构）

> **状态**：设计规划（**本批只新增文档，未改任何代码/配置/前端**）· 2026-10-24
> **关联**：[13 MCP 定价与收费管理调研](../research/13-MCP服务定价与收费管理系统调研.md) · [billing-system.md](billing-system.md) · [licensing.md](licensing.md) · [free-tier.md](free-tier.md) · [result-payment.md](result-payment.md)（结果付费，MCP 不参与）· [plans/05 付费与解锁](../plans/05-付费与解锁-软件内购与ICP过渡-规划.md)
> **前提**：MCP 服务已在 v1.2.x 交付（15 工具 + 4 资源模板 + 2 prompts、独立 `Sanitized_Data_MCP.exe`、支持 WorkBuddy/Codex/Claude Desktop/Cursor/DSH）；**licensing 尚未实现**，本设计是 licensing 上线时一并落地。

---

## 0 结论先行

| 问题 | 结论 |
|---|---|
| MCP 单独定价？ | **不单独出 MCP SKU**。MCP 是**专业版功能位**（¥499/年，早鸟 ¥299，买断 ¥999）；团队按席位、机构按项目。 |
| 怎么收费？ | **年付/买断订阅 + 席位/项目合同**；软件内购（A 静态码 / B Native 扫码）+ 官网 + 团队/机构合同；**不做按调用/按次**。 |
| 免费版给不给 MCP？ | **只给只读小工具**（`list_rules` / `check_support` / 小文本 `redact_text`），**文件写操作、Job 复核、导出、还原、审计一律需专业版**（试用期全功能）。 |
| 收多少？ | 专业版 ¥499/年（早鸟 ¥299，限 300，续费恢复 499）、买断 ¥999（不含跨大版本）；团队 5 席 ¥1,999/年；**建议**新增 10 席 ¥3,999、20 席 ¥6,999（待拍板）；机构 ¥3.88 万起 + 可选「MCP 接入包」¥1.5–3 万；行业规则包/审计包作为 P2 增值，不在首发。 |
| 为什么？ | 全球 MCP 生态：MCP 本身几乎不单独卖（Copilot 全档含、Codex 不单卖）；按调用单价 ¥0.0067–0.67/次，覆盖不了通道费且与离线/无账号冲突；国内 MCP 广场全免费、计费只在调用侧（0.01–0.03 元/次）。**卖"本机脱敏 + 可复核/可还原/可审计"，不卖"能接 MCP"**。 |

**一句话**：MCP 是分发通道，不是商品；**商品是「文件先在本机脱敏，再交给 Agent」这件事**。收费点放在"文件写操作 + Job 复核 + 导出/还原/审计 + 团队治理"，用专业版/团队/机构承接。

> **两处待拍板（与 [plans/05](../plans/05-付费与解锁-软件内购与ICP过渡-规划.md) 的差异）**：① 免费版给 **只读小工具**（plan 05 原口径为 MCP 全 Pro）——见 D-M1；② 团队新增 **10 席 ¥3,999 / 20 席 ¥6,999**（现有已发布价只有 5 席 ¥1,999；10 席现价 ¥3,499，改价为 D-M2）——见 D-M2。**本文件为建议稿；未拍板前对外仍按 plan 05/09 执行。**

> **结果付费与 MCP 的关系**：结果付费只面向 **GUI 导出**，且评审结论为“当前约束下需求≈0、默认不立项”；**MCP 不参与结果付费、不发起支付、不按次/按文件卖**，仍只认 Pro/Team/Org，无授权返回 `license_required`。详见 [14 报告](../research/14-结果付费方案设计与对比.md) 与 [result-payment.md](result-payment.md)。

---

## 1 现状：MCP 能力面与商业含义

| 层 | 内容 | 商业含义 |
|---|---|---|
| **基础工具（4）** | `sanitize_document`、`redact_text`、`check_support`、`list_rules` | 只读/轻量入口：`list_rules`/`check_support` 可免费用；`redact_text` 可给免费小额度；`sanitize_document` 是文件写操作，归付费 |
| **L2 工具（11）** | `sanitize_file`、`get_sanitize_job`、`list_sanitize_jobs`、`get_sanitize_entities`、`update_sanitize_entity`、`mark_sanitize_region`、`accept_all_sanitize_entities`、`export_sanitize_job`、`get_sanitize_audit`、`rehydrate_sanitize_text`、`open_sanitize_workbench` | **核心付费面**：可控 Job、人在环复核、工作台深链、导出档位、还原、审计 |
| **资源（4 模板 + 1 列表）** | `sanitize://jobs`（列表）、`sanitize://jobs/{id}`、`.../entities`、`.../audit`、`.../pages/{n}` | 与 L2 同权；免费不给 |
| **Prompts（2）** | `review-before-export`、`workbench-fallback` | 仅引导文案、不含任务数据；**免费可用**（帮助用户理解复核流程） |
| **交付形态** | `Sanitized_Data_MCP.exe`、源码 stdio、DSH 插件 | **授权在引擎/服务端**，不能只靠 DSH 插件（卸载插件不能绕过/失去授权） |
| **客户端** | WorkBuddy 5.8.4、Codex、Claude Desktop/Code、Cursor、DSH 等 | "能接 Agent"已是免费能力（元宝全免费、豆包 68/月），**不能当卖点** |

**为什么文件写操作要收费而只读免费**：只读不产生"交付物"，不会替代 GUI 免费版；文件写操作 + 复核/导出才产生可外发成品，是真实价值。对 Agent 用户来说，`list_rules`/`check_support` 免费能先"发现工具"，`redact_text` 免费能处理聊天/日志小段，形成"用起来→要处理文件→升级"的路径。

---

## 2 套餐与权益矩阵（MCP 视角）

> 完整产品权益见 [free-tier.md §1](free-tier.md)；这里只列 MCP/Agent 相关。**Trial = 全功能 14 天**，不单列。

| 能力 | 免费 | 个人版 ¥199/年 | **专业版 ¥499/年** | 团队版 ¥1,999/年/5 席 | 机构版 ¥3.88 万起 |
|---|---|---|---|---|---|
| `list_rules` / `check_support` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `redact_text`（小文本） | 有限额（建议 20 次/天） | ✓ | ✓ | ✓ | ✓ |
| `sanitize_document`（文件脱敏，一调完成） | ✗ | ✗ | ✓ | ✓ | ✓ |
| L2 11 个工具（Job/复核/导出/还原/审计/工作台深链） | ✗ | ✗ | ✓ | ✓ | ✓ |
| 资源（jobs/entities/audit/pages PNG） | ✗ | ✗ | ✓ | ✓ | ✓ |
| Prompts（复核/回退引导） | ✓ | ✓ | ✓ | ✓ | ✓ |
| 工作台深链（`open_sanitize_workbench`） | ✗ | ✗ | ✓ | ✓ | ✓ |
| MCP 审计导出（团队/机构） | ✗ | ✗ | 基础 | ✓ + 团队审计 | ✓ + 机构对接 |
| 团队席位/机器码分发 | — | — | — | ✓（5 席；10 席档） | ✓（合同） |
| MCP 私有化接入/网关（白名单、内网、审计对接） | — | — | — | — | ✓（项目加购 1.5–3 万） |
| 设备数 | 1 | 1 | 2 | 按席位 | 1 台服务器 + 合同席位 |
| 价格 | ¥0 | ¥199/年（早鸟 ¥99；买断 ¥499） | **¥499/年（早鸟 ¥299；买断 ¥999）** | **¥1,999/年/5 席**（建议加 10 席 ¥3,999） | **¥3.88 万起** |

> **现有已发布价**：3 席 ¥1,299 / 5 席 ¥1,999 / 10 席 ¥3,499（09 报告与价格页）。本表 10 席 ¥3,999、20 席 ¥6,999 为 D-M2 建议，**未拍板前以 09/价格页为准**；3 席是否保留也待拍板。

**定价依据（市场）**

- 国内个人 AI 订阅：入门 ¥19.9–59/月、主流 ¥99–199/月；专业软件年费：福昕商业版 480 元/人/年、用友好会计 498–2,998 元/年 → **¥499 落在"专业软件"与"AI 订阅年付"之间，是最不刺眼的主力档**（详见 13 报告 §4）。
- 国际个人 AI 工具中位 $20/月（≈¥134/月、¥1,600/年）；本产品 ¥499/年 ≈ $6.2/月，约为国际中位 1/3 —— **不按 AI 订阅定价，按专业软件定价**。
- 团队：企业 AI 席位 ¥1,188–3,792/席·年（Qoder/CodeBuddy）；本产品 5 席 ¥1,999 ≈400/席·年，**明显低于市场，保留为"最小可买包"**；10 席档建议 ¥3,999（≈400/席）承接小律所/诊所/代账。
- 机构：单套数据库脱敏软件 18.8–42.9 万、加固项目 54.5 万、高级安全工程师人月 2.67–4.55 万；¥3.88 万 ≈1/5–1/10 套软件、≈1 人月 → **安全但必须清单化交付**（私有化、审计、定制规则、发票合同）。

---

## 3 怎么收：购买入口与授权链路

### 3.1 四个购买入口（同一订单/授权服务）

| 入口 | 场景 | 通道 | 自动解锁 |
|---|---|---|---|
| **软件内购弹窗**（控制台/`/license`/DSH 面板） | 用户在软件里想升级 | A 静态购买码（二维码由本地前端生成，指向官方购买页/平台卡密）；B Native 扫码（ICP/商户号后） | A：回填/导入授权；B：扫码后自动下载并导入 license |
| 官网 `/pricing` → `/buy` | 用户从官网/搜索结果购买 | 微信/支付宝/对公 | 绑定机器码后下载 license，回软件导入 |
| MCP 工具返回升级 CTA | Agent 调用了付费工具 | 返回 `license_required` + 购买提示（purchase_hint；可选 URL） | 同软件内购 |
| 团队/机构 | 管理员/采购 | 对公转账 + 合同 + 人工签发 | 团队 license 分发/机构部署 |

> 规则来自 [plans/05](../plans/05-付费与解锁-软件内购与ICP过渡-规划.md)：A/B 双模式同版本预置、服务端切换；**绝不让模型自动购买/代付**；用户点击才联网（宿主/浏览器侧），引擎保持零外呼。

### 3.2 MCP 侧的购买触发（关键 UX）

未授权调用文件工具时，MCP 返回结构化错误（不是静默失败）：

```json
{
  "ok": false,
  "error": "该工具需要「专业版」授权（¥499/年，早鸟 ¥299）。你的文件不会被上传；授权在本机校验。",
  "code": "license_required",
  "required_edition": "pro",
  "purchase_url": null,
  "purchase_hint": "打开数据隐站「授权」页兑换/购买，或访问官网价格页",
  "trial_available": true,
  "workbench_available": true
}
```

- 与 `mcp_server.py::_error` 现有结构一致（`content` 里放 `{ok:false,error,code,...}`，`isError:true`），不另造嵌套 `error` 对象。
- **错误里不放 `machine_code`**（设备标识可能被 Agent/模型日志带走）；购买页/二维码在本地 UI 生成时带入，或用一次性 bind token。
- `purchase_url` 由配置注入；ICP 前可为 `null`，用 `purchase_hint` 引导到本地 `/license` 或对公入口；**不要写 `<官网>` 占位**。
- `workbench_available` 按真实状态返回（本机 Web 可用即 true），不要固定 false。
- Agent 只能把这段展示给用户，**不能代付/代扫码**；工具描述里写明"notify the user, do not attempt payment"。
- DSH 工具卡可渲染「升级/试用」按钮；Claude/Cursor 等只显示错误文本 + 链接。
- 试用期全功能；免费用户若 `trial_available`，提示"可先开 14 天试用"。
- 授权导入后无需重启 MCP 进程（下次工具调用热加载 license；若缓存则最迟重启客户端）。

---

## 4 门禁实现（产品侧，离线）

### 4.1 授权状态与功能位

- 复用 [licensing.md](licensing.md) 的 `license` 状态：`free / trial / paid / grace / expired`；
- 功能位建议：`mcp`（文件写操作 + L2 全量）属于 Pro/Team/Org；只读工具不要求 `mcp`；试用期按全功能临时授予；
- 团队/机构：`license.features` 含 `mcp`，`team_id` 标识；机构可含 `mcp_gateway`（私有化接入包）。
- **不要**在 MCP 协议层做鉴权/计费（MCP 无标准付费语义）；授权在本地进程内校验。

### 4.2 执行点

| 位置 | 做什么 | 失败行为 |
|---|---|---|
| `mcp_server.py` 工具分发（`_handle_tools_call` 里用 `PAID_TOOLS` 集合统一检查；`_tool_guard` 保持异常包装） | 对 **12 个付费工具**（`sanitize_document` + 11 个 L2：`sanitize_file`/`get_sanitize_job`/`list_sanitize_jobs`/`get_sanitize_entities`/`update_sanitize_entity`/`mark_sanitize_region`/`accept_all_sanitize_entities`/`export_sanitize_job`/`get_sanitize_audit`/`rehydrate_sanitize_text`/`open_sanitize_workbench`）检查 `licensing.require("mcp")` | 返回 `isError` + `{ok:false,error,code:"license_required",...}`（不执行任何文件操作） |
| 资源列表 `resources/list` 与 `sanitize://jobs` | 付费/试用才返回 job 列表；免费返回空列表或引导 | **不泄露任务名/数量/URI** |
| 资源读取 `resources/read`（jobs/{id}/entities/audit/pages） | 同上；免费返回 **JSON-RPC error**（如 -32002 资源不可用/-32001 未授权），不是"错误资源内容" | 不泄露任务数据 |
| Prompts（`review-before-export`/`workbench-fallback`） | 只是引导文案、不含任务数据，**免费/试用/付费都可用** | — |
| `redact_text` | 免费限额（建议 20 次/天，本地计数；与 GUI 免费版共用 `license/state.json`，用文件锁避免 Web/MCP 并发写坏） | `quota_exceeded` |
| `list_rules`/`check_support` | 免费不限 | — |
| 引擎 `JobManager`/`create_job` | 已有免费版限额与 license 门禁（见 free-tier.md） | 同上（MCP 与 GUI 共享配额/授权） |
| 审计 | 导入/试用/门禁触发写本地审计（只记类型） | — |

> **实现顺序**：先 `licensing` 内核（W1）→ 在 `_handle_tools_call` 加 `PAID_TOOLS` 检查 + `_list_resources`/`_read_resource` 入口 gate → 再改 UI/DSH 卡片。**不要**在 MCP 协议层做鉴权/计费（MCP 无标准付费语义）；授权在本地进程内校验。

### 4.3 工具可见性策略

- **建议**：`tools/list` 仍返回全部 15 个工具（附描述"需要专业版"），调用时返回 `license_required`。理由：Agent 能发现能力、用户能看到升级路径；隐藏工具会让 Agent 不知道有 L2 能力。
- 可选优化（P1）：`initialize` 的 `instructions` 带一句"文件写工具需专业版，可提示用户升级"；DSH 卡片显示锁标记。
- 不要返回"假成功"或静默降级（MCP 已有 `_tool_guard` 错误结构可复用）。

### 4.4 不与 GUI 免费版冲突

- MCP 与 GUI 共享同一 `quota`/license 状态：免费用户 GUI 3 份/天；MCP 免费只读不受份数影响；MCP 文件写需 Pro（不消耗免费额度）。
- 试用期 MCP 全功能，与 GUI 一致。
- 付费后不限次（本地无服务器成本）；**不设"每日 50 次"之类的本地配额**——按次限额会被用户视为"本地还限次"的恶感，且市场按次单价极低（0.01–0.03 元/次），没有定价依据。用 license 条款里的"合理使用/禁止转售"约束滥用。

---

## 5 团队版 / 机构版：MCP 治理是溢价点

### 5.1 团队版（¥1,999/年/5 席，建议加 10 席 ¥3,999）

- 团队 license 含 5 个机器码，管理员在购买后台录入成员机器码；成员各自导入同一授权文件；
- 团队权益：Pro 全部 MCP + **本地审计导出**（`origin=mcp` 的任务/类型/哈希；管理员逐机导出、人工汇总，**不做云端回收/下发**）+ **共享规则包**（管理员分发文件、本地导入）+ 管理台（P1：席位/机器码/到期/续费）+ 发票；
- 增席：后台重签（新增机器码）；减席：重签（旧文件仍可能被旧机使用，合同约束，接受）。
- **不承诺**“规则包云端下发”“Agent 白名单强制控制”：产品零外呼/无账号，策略只能以**本地签名文件**形式由管理员分发；Agent 自报的 clientInfo 只能做提示，不能当安全控制。

### 5.2 机构版（¥3.88 万起 + 可选 MCP 接入包 ¥1.5–3 万）

- 机构 license 绑服务器机器码，`org_deploy` + `mcp` + `team_admin`；
- **3.88 万机构版 base 交付**：内网通用部署、通用审计对接、定制规则包、SLA、培训；**不含 MCP 专项**（MCP server 同机部署/规则包/审计字段模板/SIEM 对接模板）。
- **MCP 接入包（可选，1.5–3 万，与 base 互斥清单）**：
  * 同机 MCP server 部署 + Agent 客户端接入配置 + MCP 审计字段/SIEM 对接模板 + MCP 规则包 + 培训；
  * **不含**集中式 MCP 网关（多用户/鉴权/隔离/网络改造）——那需另立项目、预算另算（当前 `mcp_server.py` 的 `_api_base` 硬编码 `127.0.0.1`，无账号/多用户/Job owner，集中部署需改造）；
  * 适合律所/医院/企业信息科，对标企业采购习惯（B 端人月 2.67–4.55 万，项目 1.5–3 万属轻量）。
- 机构不按"每人一个 MCP license"卖；按合同席位 + 服务器授权。
- **明确不卖的控制**：集中式 Agent 白名单/并发控制/云端策略回收（需要账号+联网+网关，另行立项）。

### 5.3 不做的

- 不做 MCP 托管/市场/代理（Smithery proxy 2026-10-31 关停、Cursor 并入 xAI、Windsurf 并入 Devin——中间层在洗牌，不是护城河）；
- 不做云端 MCP 网关（与"本机零外呼"定位冲突）；若未来做独立云产品，另行定价（按席位 + credits），不混入本产品；
- 不把 MCP 接入当主卖点；主卖点是"文件先在本机脱敏"，MCP 只是让 Agent 自动调用。

---

## 6 价格、促销与续费规则

| 规则 | 内容 |
|---|---|
| 专业版 | ¥499/年；早鸟 ¥299/年（限量 300，**仅首年，续费恢复 ¥499**）；买断 ¥999（永久可用，**不含 v2.x 跨大版本更新**） |
| 个人版 | ¥199/年；早鸟 ¥99（限量 1,000）；买断 ¥499；**不含 MCP 文件能力**（只读工具免费） |
| 团队版 | 5 席 ¥1,999/年（≈400/席）；**建议**加 10 席 ¥3,999（≈400/席）、20 席 ¥6,999（≈350/席）（均待拍板） |
| 机构版 | ¥3.88 万起/年（100 席 5.88 万 / 200 席 8.88 万）；MCP 接入包 1.5–3 万一次性（可选，与 base 交付互斥） |
| 试用 | 14 天全功能（含 MCP）；到期回 free；不自动扣费 |
| 老用户 | 存量安装用户赠送 1 年专业版（plan 05 P3；以 **grant 授权**落库，详见 [billing-system.md](billing-system.md) §8.5）；之后按 Pro 续费 |
| 续费 | 到期前 30/14/7/1 天提醒；宽限 14 天；**早鸟仅首年，续费恢复正式价**（早鸟用户可享续费 9 折，待拍板 D-M8）；**正式价用户续费锁正式价** |
| 渠道/OEM | 企业 Agent 平台/律所/医院渠道：按席位批量授权，建议 6–7 折结算；零售价不低于官网 9 折 |
| 不做 | 按调用/按次/按文件计费；MCP 单独 SKU；MCP 市场托管；个人收款码；虚假原价/倒计时 |

**月度订阅**：国内个人用户对年付接受度高（专业软件年费带 480–2,998 元），**P0 不做月付**；若数据表明 Agent 用户需要低门槛，可在 P1 加 Pro 月付 ¥59/月（年付 499 ≈ 8.5 折）。

---

## 7 与收费管理系统的衔接

| 数据/动作 | 归属 |
|---|---|
| SKU/价格/早鸟 | 购买服务 catalog；MCP 侧只读 license，不读价格 |
| 订单/支付/发票 | 购买服务（见 [billing-system.md](billing-system.md)） |
| license 签发/绑定/续费 | signer + license 服务；MCP 只导入/校验 |
| 客户档案/团队席位 | 购买服务 customer/org/seat 表 |
| MCP 审计/agent 治理 | 产品本地（审计链）+ 团队后台导出；机构对接 |
| 用量/按次 | **不采集、不计量**（本地 MCP 无云成本；不破坏零外呼/隐私） |

---

## 8 验收与测试

| # | 用例 | 预期 |
|---|---|---|
| 1 | 免费用户调用 `list_rules`/`check_support` | 成功 |
| 2 | 免费用户调用 `redact_text` ≤20 次 | 成功；第 21 次 `quota_exceeded` |
| 3 | 免费用户调用 `sanitize_file`/`export`/`rehydrate`/L2 | `license_required` + 购买提示（`purchase_hint`，可选 URL），不创建 Job/不写文件 |
| 4 | 免费用户读 L2 resources | 拒绝，不泄露 job/实体/审计 |
| 5 | 试用用户 | 全 15 工具 + resources + prompts 可用；到期回免费 |
| 6 | 专业版 license 导入 | 全功能；重启 MCP/客户端后仍有效；离线 |
| 7 | 个人版 license | 无 MCP 文件能力（只读可用） |
| 8 | 团队 license（5 机器码） | 列表内机器可用；列表外拒绝；审计可导出 |
| 9 | 授权过期 + 宽限 | MCP 按状态机变化；数据不丢 |
| 10 | 免费用户点击 purchase_url | 打开官方购买页（用户主动），Agent 不代付 |
| 11 | 断网 | 以上全部成立，无外站连接 |
| 12 | DSH 插件卸载 | 不影响引擎内授权；重装仍有效 |

---

## 9 实施拆分（在 licensing/W1 之上）

| # | 任务 | 人日 | 依赖 |
|---|---|---|---|
| 1 | 授权功能位 `mcp` + trial 全功能 + 免费只读策略 | 1–2 | licensing W1 |
| 2 | `mcp_server.py` 工具/资源/prompt 门禁 + `license_required` 结构 | 1–2 | W1 |
| 3 | `redact_text` 免费日限额（与 quota 共用计数） | 0.5 | free-tier W2 |
| 4 | 购买入口：MCP 错误里的 purchase_url、DSH 卡片升级按钮、软件内购弹窗 | 1–2 | pricing-page/plan 05 |
| 5 | 团队 license 分发/审计导出 + 机构 MCP 接入包文档 | 2–3 | billing-system P1 |
| 6 | 测试（免费/试用/专业/团队/离线/断网） | 1–2 | 全部 |
| **合计** | | **6.5–10.5 人日**（不含 licensing/W1 与购买服务） | |

---

## 10 开放问题

| # | 问题 | 建议 | 谁定 |
|---|---|---|---|
| Q1 | 免费 MCP 是否给 `redact_text` 20 次/天 | 给；作为发现入口 | 产品 |
| Q2 | `sanitize_document` 是否免费（受 3 份/天） | 不给（文件写操作归 Pro），避免与 Pro 抢 | 产品 |
| Q3 | 专业版是否加月付 | P1 再议（¥59/月） | 产品 |
| Q4 | 团队 10 席档是否定价 ¥3,999 | 建议；需与已发布价格页同步 | 市场 |
| Q5 | 机构 MCP 接入包是否独立卖 | 建议；1.5–3 万项目价 | 销售 |
| Q6 | 老用户赠送 1 年专业版是否含 MCP | 含（plan 05 P3） | 产品 |
| Q7 | 是否在 `initialize` 里显示授权状态 | 可选；不要泄露 license 详情 | 工程 |
