# 调研 A · 全球 MCP 生态与 Agent 工具定价（原始数据）

> **采集日期**：2026-10-24（下表所有条目统一）
> **汇率**：1 USD ≈ **6.72 CNY**（中国外汇交易中心 2026-10-24 中间价，按任务指定口径；本文所有人民币折算只用这一汇率，未混用其他汇率）
> **方法**：`web_search` 定位官方定价页 → `web_fetch` 逐页核对正文；少数页面导航过长/为 SPA，正文由文本渲染代理（r.jina.ai）读取**原始官方 URL 内容**，来源列仍给原始官方页。价格只记录页面实际写出的数字，未取到/未公开一律标注，不凭记忆补。
> **可信度**：高 = 官方定价页正文直接给出；中 = 官方文档/README/帮助中心给出，或只有页面标题/结构可证；低 = 间接证据、需复核。
> **重要提醒（2026 生态变动）**：本次抓到的页面里，Cursor 已是 xAI/Grok 捆绑形态、windsurf.com/pricing 已是 Devin（Cognition）品牌页、Smithery 公告 MCP Proxy 与 managed identity 于 2026-10-31 关停。**与 2025 年公开信息不同之处，一律以本次页面为准并已在备注中标注**，跨年对比需谨慎。

---

## 0 摘要（先看这 6 条）

1. **MCP 本身几乎不单独卖钱**：2025–2026 有公开定价的 MCP 相关产品，收费点都在"被 MCP 调用的能力"（搜索、抓取、浏览器、集成/工具）或"MCP 治理/托管"（网关、RPC、席位），而不是"MCP 协议/工具数量"。GitHub Copilot 把 MCP 集成放进**所有档位（含 Free）**，Codex **不单独卖、含在 ChatGPT 计划里**。
2. **个人 AI 工具价格带 $8–$30/月（¥54–202）**：Cursor Plus $8、Copilot Pro $10、Claude Pro $17–20、Windsurf Pro $20、Cursor Premium $30；**重度档 $100–$200/月（¥672–1,344）**，2026 年 OpenAI 更把 Pro 拉到 **$500/月（¥3,360）**。
3. **按调用价格极低**：搜索 $4–$15/1K（¥0.027–0.10/次）、抓取 $0.5–$5/1K、MCP RPC $0.10/1K（¥0.00067/次）、x402 微支付 $0.001/次（¥0.0067）。**单次 MCP 调用撑不起一个 SKU**，也覆盖不了支付通道费。
4. **MCP 托管/网关是企业收费点**：Smithery PAYG $10/月（100K RPC）、Zuplo $25/月–$1,000/月起、Kong AI 网关 $25/月 + 用量（另收计量 $20/百万事件 + **0.4% 计费流水**）。这层溢价来自认证/审计/配额/monetization，不是"工具能力"。
5. **免费额度是标配**：Firecrawl 1,000 credits、Exa $10/月、Tavily 1,000 credits、Smithery 50K RPC、Zuplo 100K 请求、Tailscale 6 用户、GitHub Copilot Free 也能接 MCP。**没有免费入口的产品在 2026 很难获客**。
6. **对数据隐站的直接结论**：¥499/年 ≈ **$6.2/月**，处在国际个人 AI 工具的 1/3–3/4 价位；MCP 维持"专业版 feature"、不单独 SKU、不做按调用，是本轮数据支持的最稳选择（详见 §4）。

---

## 1 价格总表（19 条）

列说明：**价格**列为页面原文口径（未折算）；**折算人民币**按 1 USD≈6.72 CNY；**可信度**见文首定义。

### 1.1 AI 开发者工具 / AI 订阅（价格锚）

| # | 产品/厂商 | 类别 | 收费模型 | 价格（页面原文口径） | 折算人民币（×6.72） | 来源 URL | 采集日期 | 可信度 |
|---|---|---|---|---|---|---|---|---|
| A1-1 | **Cursor**（2026 已并入 xAI/Grok 生态） | AI 编程 IDE | 订阅 + 用量池；第三方模型按 API 原价另计 | Free $0/月；Plus $8/月；Premium $30/月；Super $100/月；Ultra $200/月；Teams $30+/用户/月；Enterprise 定制 | Plus ¥53.8；Premium ¥201.6；Super ¥672；Ultra ¥1,344；Teams ¥201.6+/席/月 | [cursor.com/pricing](https://cursor.com/pricing) | 2026-10-24 | 高（官网正文） |
| A1-2 | **GitHub Copilot** | AI 编程助手 | 订阅 + GitHub AI Credits（1 credit = $0.01） | Free $0；Pro $10/用户/月（含 $15 credits）；Pro+ $39/用户/月（$70 credits）；Max $100/用户/月（$200 credits）；Business/Enterprise 本页未给价（管理/SSO/IP 赔付另属企业档） | Pro ¥67.2；Pro+ ¥262.1；Max ¥672；credits 1 分 ≈ ¥0.067 | [github.com/features/copilot/plans](https://github.com/features/copilot/plans) | 2026-10-24 | 高（官网正文） |
| A1-3 | **Claude**（Anthropic） | AI 助手 + Claude Code | 订阅（个人/席位）+ API 用量 | Free $0；Pro $17/月（年付，$200 预付）或 $20/月付；Max 从 $100/月起（5×/20× 用量，含 $100/$200 每月 API credits）；Team Standard $20/席/月（年付，$25 月付）、Premium $100/席/月（年付，$125 月付）；Enterprise $20/席/月（年付）+ API 用量，20 席起 | Pro ¥114.2（年付）/¥134.4（月付）；Max ¥672 起；Team ¥134.4–¥672/席/月；Enterprise ¥134.4/席/月 + 用量 | [claude.com/pricing](https://claude.com/pricing) | 2026-10-24 | 高（官网正文） |
| A1-4 | **ChatGPT**（OpenAI） | AI 助手 | 订阅（个人档 + 企业席位） | Free / Go / Plus / Pro 四档；**Pro 三档：Pro 100 = $100/月、Pro 200 = $200/月、Pro 500 = $500/月**；Plus/Go 具体金额本次**未公开/未取到**（pricing 页价格数字为动态渲染，仅显示“/month”）；Business ≥2 用户、Enterprise 询价 | Pro ¥672 / ¥1,344 / ¥3,360；Plus/Go 未取到 | [chatgpt.com/pricing](https://chatgpt.com/pricing)、[帮助中心 Pro 档位](https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers) | 2026-10-24 | Pro 高（帮助中心表格）；Plus/Go 未公开（未取到） |
| A1-5 | **Windsurf**（现为 Devin/Cognition 品牌） | AI 编程 | 订阅 + 席位 | Free $0；Pro $20/月；Max $200/月；Teams $80/月团队费 + $40/月/完整 dev 席位；Enterprise 定制 | Pro ¥134.4；Max ¥1,344；Teams ¥537.6/月 + ¥268.8/席/月 | [windsurf.com/pricing](https://windsurf.com/pricing) | 2026-10-24 | 高（官网正文；页面标题已是 Devin） |
| A1-6 | **OpenAI Codex** | AI 编程 | **不单独定价，捆绑在 ChatGPT 计划** | 含在 Free/Go/Plus/Pro/Business/Enterprise；Free/Go 为受限试用，Plus 起「扩展 Codex 用量」，Pro「Maximum Codex tasks」；用量见各档周限额 | 无独立价格（含在 A1-4 计划内） | [chatgpt.com/codex/pricing](https://chatgpt.com/codex/pricing/) | 2026-10-24 | 高（官网正文） |

### 1.2 MCP / API Server 商业化（按调用、按 credit）

| # | 产品/厂商 | 类别 | 收费模型 | 价格（页面原文口径） | 折算人民币（×6.72） | 来源 URL | 采集日期 | 可信度 |
|---|---|---|---|---|---|---|---|---|
| A2-1 | **Firecrawl** | 网页抓取 API（有 MCP/集成） | 订阅 + credit；PAYGO 增量 | Free $0（500 searches 或 1,000 页/月）；Hobby $16/月（年付；2,500 searches/5,000 页；加购 1,000 credits/$5）；Standard $83/月（年付，100k credits，年省 $198）；Growth/Scale 档金额本页未取到（结构 500k/1M credits）；Enterprise 定制 | Hobby ¥107.5/月；Standard ¥557.8/月；$0.005/credit ≈ ¥0.034 | [firecrawl.dev/pricing](https://www.firecrawl.dev/pricing)、[计费文档](https://docs.firecrawl.dev/billing) | 2026-10-24 | 高（官网+文档正文） |
| A2-2 | **Exa** | AI 搜索 API（有 MCP server） | 订阅（credit 额度）+ PAYGO 按端点 | Free：$10 credits/月 + $10 注册奖励，**含 MCP server 接入**；PAYGO：Search $4/1k（Lite）/$7（Auto）/$12（Deep）、Contents $1/1k 页、Monitors $15/1k、Answer $5/1k、Agent $0.012–$1.00/次；Enterprise 定制 | Search $0.027–0.081/次；Contents ¥0.0067/页；Monitors ¥0.10/次；Agent ¥0.081–¥6.72/run | [exa.ai/pricing](https://exa.ai/pricing) | 2026-10-24 | 高（官网正文） |
| A2-3 | **Tavily**（Nebius） | AI 搜索 API | 订阅 + credit | Free 1,000 credits/月；PAYG $0.008/credit；Project $30/月（4,000 credits/月）；Enterprise 定制 | ¥0.0538/credit；Project ¥201.6/月 | [tavily.com/pricing](https://www.tavily.com/pricing) | 2026-10-24 | 高（官网正文） |
| A2-4 | **Browserbase** | 浏览器/搜索/抓取基础设施（有 MCP） | 订阅 + 超量按量 | Free $0（3 并发、1 browser hour、各 1,000 Search/Fetch）；Developer $20/月（100 browser hours 后 $0.12/hr；Search $5/1k；Fetch $1/1k；代理 $12/GB）；Startup $99/月（500 hr 后 $0.10/hr；Fetch $0.5/1k；代理 $10/GB）；Scale 定制 | Developer ¥134.4/月；Startup ¥665.3/月；browser hour ¥0.81/¥0.67 | [browserbase.com/pricing](https://www.browserbase.com/pricing) | 2026-10-24 | 高（官网正文） |
| A2-5 | **Pipedream** | 集成/工具平台（MCP servers 全档含） | 订阅 + 按用户 + 按 credit | Free $0（开发环境全功能，含 MCP servers）；Startup $99/月（年付；$150/月付）：含 100 external users 后 $2/用户、含 10,000 credits 后 $0.012/credit；Business 定制（SSO/SCIM/HIPAA/静态出口 IP/SLA） | Startup ¥665.3/月（月付 ¥1,008）；$2 ≈ ¥13.4/用户；$0.012 ≈ ¥0.081/credit | [pipedream.com/pricing](https://pipedream.com/pricing) | 2026-10-24 | 高（官网正文） |
| A2-6 | **Anthropic 平台按调用能力**（MCP 相邻参照） | Agent 运行时/工具调用 | 按用量 | Managed Agents **$0.08/session-hour**；Web search **$10/1K searches**；Code execution $0.05/小时/容器（每日 50 小时免费）；模型 token 另计 | ¥0.5376/session-hr；¥0.0672/次搜索；¥0.336/hr | [claude.com/pricing](https://claude.com/pricing) | 2026-10-24 | 高（官网正文） |

### 1.3 MCP 托管 / 市场 / 网关

| # | 产品/厂商 | 类别 | 收费模型 | 价格（页面原文口径） | 折算人民币（×6.72） | 来源 URL | 采集日期 | 可信度 |
|---|---|---|---|---|---|---|---|---|
| A3-1 | **Smithery** | MCP 托管/注册表（代理、托管 OAuth） | 订阅 + 按 RPC | Hobby Free：50K RPC/月、3 namespaces、managed OAuth、持久连接；Pay as you Go **$10/月**：100K RPC/月，$10 credits 用完后 **$0.10/1K RPC**；Custom 定制（限速/SLA/Slack） | ¥67.2/月；RPC ≈ ¥0.00067/次 | [smithery.ai/pricing](https://smithery.ai/pricing) | 2026-10-24 | 高（官网正文）；注：公告 MCP Proxy 与 managed identity **2026-10-31 关停**，站点保留为公共 registry |
| A3-2 | **Zuplo** | MCP / AI / API 网关 | 订阅 + 按请求量 + 席位 | Free $0（100K req/月；MCP Gateway 仅开发：1K tool invocations、3 upstream、5 MCP users）；Builder **$25/月**（100K req 含，$100/100K 超量；10K tool invocations）；Enterprise **from $1,000/月**（年合同；MCP users/servers/tool invocations 不限；99.5% SLA 起；1M req 含） | Free ¥0；Builder ¥168/月；Enterprise ¥6,720/月起 | [zuplo.com/pricing](https://zuplo.com/pricing) | 2026-10-24 | 高（官网正文） |
| A3-3 | **Kong Konnect（AI Management）** | AI/MCP 网关 + 计量计费 | 订阅 from $25/月 + 用量 | Plus from **$25/月** + usage：Serverless AI Gateway $25/月/控制面（≤5）；Hybrid $200/月；DCGW $500/月 + $0.15/GB；AI 网关含 1M req/月，$200/每多 1M（上限 10M）；model proxy $100/模型/月（≤5）；AI 可观测性前 1M 含，之后 $20/1M；**Metering $20/百万事件；Billing 抽 0.4% 计费流水**；access tokens $30/1M；Enterprise 定制 | Plus ¥168/月 起；DCGW ¥3,360/月；1M req 超量 ¥1,344；0.4% 流水另计 | [konghq.com/pricing](https://konghq.com/pricing) | 2026-10-24 | 高（官网正文） |

### 1.4 MCP 商业化基础设施（按次/按调用收款）

| # | 产品/厂商 | 类别 | 收费模型 | 价格（页面原文口径） | 折算人民币（×6.72） | 来源 URL | 采集日期 | 可信度 |
|---|---|---|---|---|---|---|---|---|
| A4-1 | **x402（Yield.xyz MCP server 实例）** | MCP 按次微支付协议（真实接入案例） | 免费配额 + 按调用 x402 微支付 | 无 API key：read 工具 1,000 次/工具/IP/24h 免费、wallet 工具 30 次/钱包/24h；超出后 **$0.001/次**（USDC on Base；X Layer 支持 USDC/USDT0/USDG）；自备 key 绕过配额按账户计费；机器可读定价 `/.well-known/x402` | ¥0.0067/次 | [docs.yield.xyz · x402 定价页](https://docs.yield.xyz/docs/x402-payments-pricing) | 2026-10-24 | 高（官方文档正文） |
| A4-2 | **PayMCP**（开源 SDK） | MCP 收款/订阅 SDK | 按次 `@price` / 订阅 `@subscription`；开源 | MIT 开源，未见平台抽成；单次价格由开发者自定（README 示例 **$0.19/次、$0.99/次**）；支持 Stripe / Adyen / Coinbase Commerce / PayPal / Square / Walleot / x402（USDC Base、Solana）；**明示不兼容本地 stdio 分发**（会把支付密钥暴露给终端用户），且「大多数主流 MCP 客户端不支持 x402 自动支付」 | 示例 $0.19 ≈ ¥1.28；$0.99 ≈ ¥6.65；SDK 无价格 | [github.com/PayMCP/paymcp](https://github.com/PayMCP/paymcp) | 2026-10-24 | 中（官方 GitHub README） |

### 1.5 本地 / 隐私 / 安全类工具（"本机 MCP 安全工具"参照）

| # | 产品/厂商 | 类别 | 收费模型 | 价格（页面原文口径） | 折算人民币（×6.72） | 来源 URL | 采集日期 | 可信度 |
|---|---|---|---|---|---|---|---|---|
| A5-1 | **Tailscale** | 零信任网络 / 本机连接（含 AI agent 治理产品 Aperture） | 按席位（seat-based） | Personal **$0**（≤6 用户，非商用）；Standard **$8/用户/月**；Premium **$18/用户/月**；Enterprise 定制；tagged resources 含 50 个、之后 $1/月/个；ephemeral 1,000 min/月（Standard）/10,000（Premium）；Aperture（AI agent 工具调用管控）为 platform extension，需询价 | Personal ¥0；Standard ¥53.8/席/月；Premium ¥121.0/席/月；资源 ¥6.72/个/月 | [tailscale.com/pricing](https://tailscale.com/pricing) | 2026-10-24 | 高（官网正文） |
| A5-2 | **1Password** | 企业密码/身份/凭证安全（含 AI agent 运行时凭证） | 企业询价（未公开单价） | 页面只列产品线（Unified Access / Enterprise Password Manager / SaaS Manager / Privileged Access / Device Trust / Device Trust），**无自助价目**；企业需联系销售或经 AWS Marketplace 采购；**未公开**（推断依据：2026 定价页已改为企业平台介绍页，不列席位价；个人档价目本次未出现在该页） | 未公开 | [1password.com/pricing](https://1password.com/pricing) | 2026-10-24 | 中（仅结构可证） |

> **条目数：19 条**（1.1 节 6 条、1.2 节 6 条、1.3 节 3 条、1.4 节 2 条、1.5 节 2 条），全部带可点击原始官方 URL 与统一采集日期。

---

## 2 三个最重要的价格锚

### 锚 1：个人 AI 工具订阅 ≈ **$20/月（¥134/月，¥1,600/年）** 是中位价
- 实测：Cursor Plus $8、Copilot Pro $10、Claude Pro $17（年付）/$20（月付）、Windsurf Pro $20、Cursor Premium $30 → **¥54–¥202/月**。
- 对数据隐站的意义：¥499/年 ≈ **$74/年 ≈ $6.2/月**，约为国际个人 AI 工具中位价的 **1/3**。这说明 ¥499 的定价"看起来便宜"，有品牌向上空间；但也说明中国个人用户对"纯本地工具"的支付意愿通常低于"含模型算力"的 AI 订阅——**不能简单按 $20/月 对标**。

### 锚 2：重度/专业档 **$100–$200/月（¥672–¥1,344）**，2026 年出现 **$500/月（¥3,360）** 顶档
- 实测：Cursor Super $100、Ultra $200；Copilot Max $100（含 $200 credits）；Claude Max $100 起；Windsurf Max $200；ChatGPT Pro 100/200/500 = $100/$200/$500。
- 对数据隐站的意义：这些价格里大头是**模型算力**，不是工具能力。本地脱敏工具无法也不该对标这一带；真正可比的是"专业软件订阅"（下面 Tailscale/1Password 一类）。

### 锚 3：工具调用单价 **$0.001–$0.10/次（¥0.0067–¥0.67）**；MCP 网关 **$25–$1,000+/月（¥168–¥6,720/月）**
- 实测：x402 $0.001/次；Exa Search $0.004–0.012/次（$4–12/1k）；Tavily $0.008/credit；Smithery RPC $0.10/1K（¥0.00067/次）；Zuplo Builder $25/月含 10K tool invocations（≈¥0.0168/次）；Kong 网关 $25/月 + $200/百万请求 + 0.4% 计费流水。
- 对数据隐站的意义：**按调用收费在本地离线产品上不成立**——单次调用市场价不到 ¥1，扣掉支付通道费后为负；且产品零外呼、无账号，无法做调用计量与收款。这一条直接否掉"按次收费"选项。

---

## 3 分类观察（MCP 商业化 2025–2026 的 5 种形态 + 3 个生态异常）

### 3.1 五种收费形态（都有 2025–2026 公开定价案例）

| 形态 | 做法 | 本次实测案例 | 价格带 |
|---|---|---|---|
| **免费开源 / 免费引流** | MCP server 开源，靠 API 或上层产品变现 | Exa 免费档含 MCP server；Pipedream Free 含 MCP servers；GitHub Copilot Free 也能接 MCP；PayMCP 开源 MIT | $0 |
| **订阅 / 席位** | 按人按月/年收费，MCP 是其中一个 feature | Cursor、Copilot、Claude、Windsurf、Tailscale | $8–$200/席/月 |
| **按调用 / credit** | 按 API 调用、credit、RPC、浏览器小时计费 | Firecrawl、Exa、Tavily、Browserbase、Smithery RPC、x402 $0.001/次 | 单次 < $0.1；月订阅 $10–$99 |
| **平台捆绑** | MCP 不单独卖，含在既有产品档位里 | Codex 含在 ChatGPT 计划；GitHub Copilot 全档含 MCP 集成；Anthropic 平台 web search $10/1K 作为能力计费 | 无独立价 |
| **企业定制 / 网关治理** | 卖认证、审计、配额、monetization、SLA | Kong AI Management、Zuplo Enterprise、Pipedream Business、1Password 企业 | $25/月起，企业 $1,000/月起或询价 |

### 3.2 三个生态异常（说明中间层在洗牌，别把 MCP 通道当资产）

1. **Smithery**：MCP Proxy 与 managed identity 于 **2026-10-31 关停**，只保留公共 registry——"MCP 托管/代理"作为独立生意在收缩。
2. **Cursor**：2026 页面已是 xAI/Grok 捆绑形态（Grok Bot、X Premium 权益、第三方模型 API 原价另计），不再是 2025 年那个独立 Cursor 定价。
3. **Windsurf**：windsurf.com/pricing 已是 **Devin（Cognition）** 品牌与团队定价（$80/月团队费 + $40/席）。

> 结论：**不要把"MCP 市场/托管"当作产品护城河**。工具的价值在数据、合规与领域能力（本机脱敏就是典型），MCP 只是分发通道。

### 3.3 免费额度的行业惯例（可直接抄）
- 固定额度型：Firecrawl 1,000 credits、Tavily 1,000 credits、Smithery 50K RPC、Zuplo 100K 请求。
- 现金额度型：Exa $10/月、Claude Pro/Max 含 $100–$500/月 API credits、GitHub 1 credit=$0.01。
- 功能受限型：Tailscale 6 用户免费、GitHub Copilot Free 2,000 次补全/月。
→ 共同点：**免费档能"跑通完整链路"，但额度/高级能力受限**；这与数据隐站现有 free-tier（3 份/天、20 页/份、无 MCP）方向一致。

---

## 4 对「数据隐站 / Sanitized_Data」MCP 定价的启示（10 条）

> 现状口径：MCP 属于专业版 feature（¥499/年，早鸟 ¥299，买断 ¥999；≈$74/年、$44.5/年、$148.7）。本机 stdio、零外呼、无账号、无遥测；15 工具 + 4 类资源 + 2 prompts；客户端覆盖 WorkBuddy / Codex / Claude Desktop / Cursor / DSH。

1. **维持"MCP = 专业版 feature"，不要单独出 MCP SKU。**
   依据：GitHub Copilot 把 MCP 集成放进所有档位（含 Free）；Codex 干脆不单独卖、含在 ChatGPT 计划里；Exa/Tavily/Pipedream 都把 MCP 当既有 API/平台的一个入口。MCP 是**分发通道**，单独标价会让用户困惑"为什么接个协议还要钱"，还会和免费核心能力打架。

2. **¥499/年（≈$6.2/月）价格合理，不建议上调；早鸟 ¥299 ≈ 6 折可保留，但必须绑定"首年"且续费恢复 ¥499。**
   依据：国际个人 AI 工具中位 $20/月（¥134/月），本产品约 1/3；但本地工具无模型算力成本，用户参照系更接近 Tailscale（$8/席/月）而非 Cursor。国内个人客单价下，¥499 已是"高客单工具"档，再高会伤转化。

3. **免费额度按"能跑通但不解决生产问题"来设：MCP 只读小工具免费，写操作付费。**
   建议：`list_rules` / `check_support` / 小文本 `redact_text` 免费；`sanitize_file` / `export_sanitize_job` / `rehydrate_sanitize_text` / 批量与审计导出返回 `feature_required` + 升级 CTA。对齐 Copilot「Free 也能接 MCP，但 AI Credits 有限」的做法，也保护免费版 3 份/天口径。

4. **明确不做按调用/按次计费（三条硬理由）。**
   ① 市场单次价 ¥0.0067–0.67，扣支付通道费（Stripe 2.9%+$0.30 量级）后为负；② PayMCP 官方明确**不兼容本地 stdio 分发**（会把收款密钥暴露给终端用户）；③ x402 需要客户端支持，而 PayMCP README 直言"大多数主流 MCP 客户端不支持 x402 自动支付"；④ 与产品「零外呼、无账号」红线冲突。→ **年付/买断是最干净的收费形态。**

5. **团队/机构版是比"卖 MCP"更值得做的第二增长点。**
   依据：Tailscale $8–18/席/月、Claude Team $20–100/席/月、Pipedream 超 100 用户 $2/用户、Zuplo 企业 $1,000/月起。律所/医院/财税机构 5–50 人场景，可做 **¥199–499/席/年**（$30–74/席/年）+ 部署/审计导出/行业包增值，客单是个人版的 2–5 倍；不破坏个人版价格体系。

6. **企业"MCP 治理"能力先不做，但要在规划里留位。**
   Kong/Zuplo 的溢价来自认证、审计、配额、SLA、monetization（Kong 甚至收 0.4% 计费流水）。本产品目前**无账号体系、零外呼**，做不了集中治理；若未来做，可对标其 1/10 价位（**¥3,000–8,000/年/机构**），且必须等"账号/遥测"口径变化再动。

7. **可加的真实 SKU 是"行业规则包/审计导出"，不是 MCP。**
   法律/医疗/财务/外贸/跨境规则包 ¥99–199/年/包、审计导出包（HMAC 链报告）¥99–199/年；Exa/Tavily 的"按能力加价"逻辑支持"基础订阅 + 能力包"。这比"卖协议接入"更可解释，也与现有专业版权益兼容。

8. **对外价格表达用"≈$6.2/月"做锚，但对内宣传打"合规/零外呼"差异，不打价格战。**
   对比话术：国际个人 AI 工具 $20/月是"买算力"，本产品 ¥499/年是"买不出门的数据"；对标 Tailscale（$53.8/席/月）反而显得便宜。避免用户把本产品和 Cursor/ChatGPT 订阅放同一价格坐标系。

9. **早鸟转化参考行业"低门槛升级"路径：free → $10–$30/月 → $99+/月。**
   Smithery $10/月、Exa/Tavily 免费额度、Firecrawl Hobby $16/月都是"先用起来再升级"。本产品早鸟 ¥299 对应 $3.7/月，低于所有国际同行入门档 → 适合冲量，但**限量 300 + 首年 + 续费恢复**三件套必须写死在规则里，避免永久低价预期。

10. **监控重点：MCP 中间层洗牌风险。**
    Smithery proxy 关停、Cursor 并入 xAI、Windsurf 并入 Devin —— 平台方随时会收编/关停 MCP 入口。数据隐站的正确站位是**"客户端插件 + 引擎内授权门禁"**（授权在引擎侧、不依赖 DSH 插件），这与现状规划一致；**不要在 MCP 市场/托管上投入**。

---

## 5 数据缺口与待复核（下一轮调研清单）

| 缺口 | 影响 | 处理建议 |
|---|---|---|
| ChatGPT **Plus / Go** 具体月费（pricing 页数字为动态渲染未取到） | 个人订阅锚点少一档 | 用移动端/浏览器实测或帮助中心文章复核；Pro 三档已由帮助中心确认 |
| GitHub Copilot **Business / Enterprise** 席位价 | 企业档价格带缺一块 | 查 github.com/features/copilot/copilot-business 或 docs 的 business 计费页 |
| Firecrawl **Growth / Scale** 具体金额；Apify、Composio、Zapier MCP、Glama、mcp.run、MCP Market、TrueFoundry、LiteLLM 未逐一核对 | 托管/市场类覆盖不全 | 下一轮按"免费/付费档 + 计费单位"逐页补 |
| Supabase / Neon / Cloudflare / Notion / Linear / Stripe / GitHub 官方 MCP 是否单独定价 | 平台捆绑型缺具体价目 | 多为"随订阅免费/包含"，需逐家查 MCP 文档页确认，不可凭印象写 |
| 1Password 具体席位价（2026 改为企业询价） | 安全类工具价格锚缺数字 | 查 AWS Marketplace 挂牌价或历史页快照 |
| x402 facilitator 抽成费率、Stripe 机器支付/agentic commerce 费率 | 按调用方案的"净收入"算不准 | 查 x402 官方 facilitator 页 + Stripe agentic commerce 文档 |
| 汇率 6.72 未二次核对 CFETS 页面 | 折算可能有 ≤1% 偏差 | 按任务口径使用；如需精确，复核中国外汇交易中心 2026-10-24 中间价 |

---

## 6 来源 URL 一览（去重，全部为本次实际抓取）

| 类别 | URL |
|---|---|
| AI 工具 | [cursor.com/pricing](https://cursor.com/pricing) · [github.com/features/copilot/plans](https://github.com/features/copilot/plans) · [claude.com/pricing](https://claude.com/pricing) · [chatgpt.com/pricing](https://chatgpt.com/pricing) · [help.openai.com · Pro tiers](https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers) · [chatgpt.com/codex/pricing](https://chatgpt.com/codex/pricing/) · [windsurf.com/pricing](https://windsurf.com/pricing) |
| MCP / API Server | [firecrawl.dev/pricing](https://www.firecrawl.dev/pricing) · [docs.firecrawl.dev/billing](https://docs.firecrawl.dev/billing) · [exa.ai/pricing](https://exa.ai/pricing) · [tavily.com/pricing](https://www.tavily.com/pricing) · [browserbase.com/pricing](https://www.browserbase.com/pricing) · [pipedream.com/pricing](https://pipedream.com/pricing) |
| MCP 托管 / 网关 | [smithery.ai/pricing](https://smithery.ai/pricing) · [zuplo.com/pricing](https://zuplo.com/pricing) · [konghq.com/pricing](https://konghq.com/pricing) |
| MCP 计费基础设施 | [docs.yield.xyz · x402 定价](https://docs.yield.xyz/docs/x402-payments-pricing) · [github.com/PayMCP/paymcp](https://github.com/PayMCP/paymcp) |
| 本地 / 隐私 / 安全 | [tailscale.com/pricing](https://tailscale.com/pricing) · [1password.com/pricing](https://1password.com/pricing) |

> 备注：所有价格均为 2026-10-24 页面快照。定价页会变（本次已见证 Cursor/Windsurf 品牌与价格变动、Smithery proxy 关停），引用前建议复抓一次。
