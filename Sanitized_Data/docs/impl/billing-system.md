# 收费管理系统设计（订单 / 授权 / 客户 / 团队 / 发票 / 续费）

> **状态**：设计规划（**本批只新增文档，未改任何代码/配置/前端**）· 2026-10-24
> **关联**：[13 MCP 定价与收费管理调研](../research/13-MCP服务定价与收费管理系统调研.md) · [payment-invoice.md](payment-invoice.md)（支付/发票细节）· [licensing.md](licensing.md)（离线授权）· [monetization.md](monetization.md)（商业化总览）· [plans/05 付费与解锁](../plans/05-付费与解锁-软件内购与ICP过渡-规划.md)
> **结论先行**：收费管理不是「一个支付按钮」，而是**商业侧的客户/订单/授权/续费/发票/对账/后台**闭环；license 记录必须自有。建议采用**模块化单体购买服务 + 独立签名器 + 独立管理后台**，不采购完整 CRM，不把授权托管给第三方。

---

## 0 一句话答案

| 问题 | 答案 |
|---|---|
| 要不要自己的客户管理系统记录 license？ | **要一个自有的「授权与客户档案」系统（license 为系统 of record）**；**不需要**完整 CRM。销售线索/商机先用飞书/企微/表格，客户成单后写入自有收费系统。 |
| MCP 服务怎么收费？ | **不单独按 MCP 计费**：MCP 是专业版功能位（¥499/年，早鸟 ¥299）；团队按席位、机构按项目；可另卖「MCP 接入包」。详见 [13 报告 §5](../research/13-MCP服务定价与收费管理系统调研.md)。 |
| 整个收费管理系统要补什么？ | 现有 [payment-invoice.md](payment-invoice.md) 是**设计稿（未实现）**，覆盖「订单/支付/签发/发票/后台」骨架；需补 **客户与组织档案、客户自助门户、团队/席位、续费自动化、优惠/活动、报表、工单、权限审计、对账与财务导出**，并明确 MCP/Agent 治理（P1，本地策略/审计导出，不做云端下发）。 |

---

## 1 目标、边界与设计原则

### 1.1 目标

1. **收得稳**：微信/支付宝/对公三条通道；回调验签、金额核对、幂等、重复支付退款、每日对账。
2. **交付准**：付款后签发离线 license；绑定机器码；续费/换机/加席可重签；离线可验证。
3. **续得上**：到期提醒、宽限期、续费入口、老用户锁价、自动续费（P1 可选）。
4. **票能开**：电子普票自动化、专票人工、红冲；与订单/退款一致。
5. **客户可查**：客户/组织/联系人/订单/授权/设备/发票/工单在一个后台可查；客服不用翻聊天记录。
6. **可运营**：价格/早鸟/优惠码/渠道可配置；收入/退款/续费/转化报表可用；活动不靠改代码。
7. **MCP/Agent 可管**（P1）：团队/机构的 MCP 接入、审计、策略可管理；个人版不暴露管理负担。

### 1.2 非目标（P0 不做）

- 不做完整 CRM（线索、商机、销售漏斗、营销自动化、呼叫中心）；
- 不做 ERP/财务总账（只做订单/发票/对账与导出，账务仍走财务系统）；
- 不做产品内账号体系/遥测（引擎保持零外呼）；
- 不做本地 MCP 的按次计量/在线扣费（见 13 报告 §5；不产生实际收入、破坏信任）；
- 不做微服务拆分/多租户 SaaS（0–5000 客户规模，模块化单体足够）。

### 1.3 设计原则

1. **商业侧与产品侧物理分离**：产品只导入 license；购买/支付只发生在商业站点或用户主动点击的宿主侧（见 plan 05 A/B 模式）。
2. **单一事实来源**：客户、订单、支付、license、发票各一张主表；任何页面只读服务端状态，不在前端拼状态。
3. **事件驱动**：所有状态变化写 `audit_events`；订单/支付/授权/发票状态机显式；可追溯、可重放。
4. **可运营 > 可扩展**：先用 SQLite/Postgres + 模块化单体，把流程跑通；不做过度架构。
5. **最小化 PII**：只收集交付/开票必需信息；不采集用户文件；不采集设备指纹明文（机器码哈希）。
6. **fail closed**：回调验签失败、金额不符、订单状态异常一律不发货 + 告警。

---

## 2 系统边界与总体架构

```
┌────────────────────────────── 公网（商业侧，独立部署） ──────────────────────────────┐
│                                                                                      │
│  用户浏览器 / 软件内购弹窗 / DSH 插件卡                                                │
│      │  HTTPS                                                                        │
│      ▼                                                                               │
│  nginx（TLS、限流、静态站）                                                           │
│      ├── 静态官网：首页/价格/购买/帮助/协议/状态                                        │
│      └── /api → 购买服务（FastAPI，模块化单体）                                        │
│                ├── catalog/order/payment/refund                                      │
│                ├── license/entitlement（调用 signer）                                 │
│                ├── customer/org/seat/subscription                                    │
│                ├── invoice/notification/coupon/report                                │
│                ├── admin API（RBAC + 2FA）                                            │
│                └── webhooks（微信/支付宝）                                             │
│                          │                                                           │
│         ┌────────────────┼──────────────────┬──────────────────┐                     │
│         ▼                ▼                  ▼                  ▼                     │
│   PostgreSQL      独立 signer 服务     对象存储          邮件/短信/企业微信           │
│  （订单/客户/      （Ed25519 私钥，     （发票 PDF/       （授权/发票/提醒送达）        │
│   license/发票）   内网 socket/独立主机） license 文件）                               │
│                                                                                      │
│  可选：CRM（飞书多维表格/纷享销客）、云开票 API、财务系统（金蝶/用友）导出              │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                   ▲ 用户下载/复制 license.json
                                   │（产品不联网）
┌────────────────────────────── 本机（产品侧，零外呼） ────────────────────────────────┐
│  数据隐站引擎 / MCP server：离线验签 → free/trial/pro/team/org → 功能门禁/限额         │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**部署建议**：购买服务与官网可同机（2C4G 起）；signer 独立 systemd（同机不同进程可接受，最好独立内网主机）；数据库每日备份异地；静态站走 nginx 缓存。**不把商业代码/依赖打进产品安装包。**

---

## 3 业务域与后端模块

| 域 | 模块职责 | 核心实体 | 优先级 |
|---|---|---|---|
| **商品与价格** | SKU、价格、早鸟活动、优惠码、渠道价、价格变更历史 | `sku`, `campaign`, `coupon`, `price_history` | P0 |
| **订单** | 建单、订单状态机、订单查询、关闭、续费/升级/换机/加席订单 | `order`, `order_item` | P0 |
| **支付** | 微信/支付宝/对公、回调验签、金额核对、幂等、退款、多渠道流水 | `payment`, `refund` | P0 |
| **授权** | license 签发（调 signer）、绑定/重签/续期、功能位、到期宽限、离线文件 | `license`, `license_binding` | P0 |
| **客户与组织** | 客户主档、联系人、组织/团队、席位、备注、来源 | `customer`, `org`, `seat`, `contact` | P0 |
| **订阅/续费** | 订阅周期、到期提醒、宽限、续费订单关联、老用户锁价 | `subscription`, `renewal` | P0（基础）/P1（自动化） |
| **发票** | 申请、开票队列、电子普票/专票、红冲/作废、PDF 存储 | `invoice` | P0（人工/半自动） |
| **通知** | 邮件/短信/企业微信模板：授权、发票、到期、退款 | `notification` | P0 |
| **优惠/活动/渠道** | 早鸟限量、优惠码、渠道码、代理商结算记录 | `coupon`, `campaign`, `channel` | P1 |
| **工单/售后** | 客服工单、换机/重发/退款申请、SLA | `ticket` | P1 |
| **报表** | 收入/MRR、订单、退款、发票、续费、转化、早鸟消耗 | 视图/物化表 | P1 |
| **MCP/Agent 治理** | 团队 agent 白名单、MCP 审计导出、机构策略 | `agent_policy`, `agent_audit` | P1（团队/机构） |
| **IAM/审计** | 后台账号、角色、2FA、操作审计 | `admin_user`, `role`, `audit_event` | P0 |
| **对账/财务导出** | 渠道账单 vs 支付流水、退款、发票、CSV/API 给财务 | `reconciliation` | P0（脚本）/P1（自动化） |
| **集成** | 云开票、邮件/短信、CRM/财务系统、企业微信 | 适配器 | P1 |

> **模块化单体边界**：同一 FastAPI 应用内按 `orders/`、`payments/`、`licenses/` 分模块；跨模块只通过服务函数/事件，不共享 ORM 写路径；未来量级上来可拆 signer/支付回调/报表 worker。

---

## 4 数据模型（核心表）

> 详细字段见 [payment-invoice.md §6](payment-invoice.md)；这里给「客户/组织/席位/续费/工单/审计」补齐，避免只有订单没有客户。

```sql
-- 客户主档（一个买家一个 customer；不存文件、不存密码）
customer(
  id PK, customer_no UNIQUE,        -- 随机编号，不可枚举
  name TEXT,                        -- 个人姓名/单位联系人（可选）
  phone TEXT, email TEXT,           -- 至少一个可达
  org_id FK NULL,                   -- 归属组织（个人为空）
  source TEXT,                      -- 官网/软件内购/DSH/渠道/线下
  tags JSON, notes TEXT,
  created_at, updated_at
);
-- 组织/团队/机构
org(
  id PK, org_no UNIQUE, name TEXT, org_type TEXT,  -- team|enterprise|hospital|firm
  contact_customer_id FK, tax_no TEXT, address TEXT,
  contract_no TEXT, contract_start, contract_end,
  status TEXT, created_at, updated_at
);
-- 席位（团队版/机构版）
seat(
  id PK, org_id FK, customer_id FK NULL,
  license_id FK, machine_code_hash TEXT, seat_label TEXT,
  status TEXT,                     -- active|released|replaced
  activated_at, released_at
);
-- 订阅/续费（license 的周期视角）
subscription(
  id PK, customer_id FK, org_id FK NULL, license_id FK,
  sku_id TEXT, term TEXT,          -- annual|perpetual
  start_at, end_at, updates_until,
  status TEXT,                     -- active|grace|expired|canceled
  renewal_reminded_at, auto_renew INTEGER DEFAULT 0
);
-- 授权（表名与 payment-invoice 统一为 licenses；order_id 可空以支持存量 grant）
licenses(
  id PK, license_id UNIQUE, order_id INTEGER NULL, source TEXT,  -- order|grant|manual
  customer_id FK, org_id FK NULL,
  sku TEXT, edition TEXT, term TEXT, seats INTEGER, max_devices INTEGER,
  machine_codes JSON, features JSON, team_id TEXT NULL,
  issued_at, not_before, expires_at, updates_until,
  key_id, sig, status TEXT,         -- active|replaced|revoked
  parent_license_id FK NULL,        -- 续费/升级链
  created_at
);
-- 赠送/补偿授权（存量用户 1 年专业版、客服补偿；必须有审计与到期）
grant_license(
  id PK, grant_no UNIQUE, customer_id FK NULL, org_id FK NULL,
  license_id FK NULL, machine_code_hash TEXT,
  sku TEXT, edition TEXT, term TEXT, seats INTEGER, max_devices INTEGER,
  reason TEXT,                     -- migration_2026|support_comp|partner
  granted_by TEXT, grant_batch TEXT, created_at, expires_at
);
-- 工单（P1）
ticket(
  id PK, ticket_no UNIQUE, customer_id FK, order_id FK NULL, license_id FK NULL,
  type TEXT,                        -- invoice|reissue|refund|bug|other
  subject TEXT, status TEXT, priority TEXT,
  assignee TEXT, created_at, updated_at, closed_at
);
-- 审计事件（表名与 payment-invoice 统一为 audit_logs）
audit_logs(
  id PK, actor_type TEXT,           -- customer|admin|system|channel
  actor_id TEXT, action TEXT, target_type TEXT, target_id TEXT,
  detail JSON, ip TEXT, created_at
);
-- 通知/发票/退款/支付/订单/权限等沿用 payment-invoice.md
```

**对既有 `payment-invoice.md` 订单表的修改（必须同步）**：

| 字段 | 用途 |
|---|---|
| `customer_id INTEGER NULL` | 订单归属客户；下单自动建/合并客户 |
| `org_id INTEGER NULL` | 团队/机构订单归属组织 |
| `price_snapshot JSON` | 下单时的价格/早鸟/折扣快照；续费锁价与财务对账依据 |
| `order_access_token_hash TEXT` | 订单查询 token（已在 payment-invoice 设计） |
| `upgrade_from / reissue_fee_cents / seat_delta` | 升级/换机/加席订单 |

**表名统一**（避免两套表）：`licenses`（不是 license）、`audit_logs`（不是 audit_event）、`admin_users`（不是 admin_user）；`orders.customer_id/org_id` 为空的历史订单回填为“未知客户”，不得删除。

**机器码口径**：服务端 `licenses.machine_codes` 存**明文**（用于重签/客服核对），`grant_license.machine_code_hash`/`seat.machine_code_hash` 存哈希；后台展示掩码（如 `K7Q2-****-****-EFGH`）；不要在两处混用。

**索引与约束要点**

- `customer.phone`/`email` 唯一性按「去重规则」处理（同一手机号可下多单，但客户主档合并；以验证码验证过的联系方式为准）；
- `licenses.machine_codes` 长度 ≤ `max_devices`；`team_id` 仅 team/org；`parent_license_id` 支持续费/升级链；
- `payment` 以 `(channel, transaction_id)` 唯一；`order_no`/`customer_no`/`ticket_no` 随机 20–24 字符；
- 所有金额 `INTEGER` 分；所有时间 UTC ISO8601；
- **不要**在日志/审计里写完整手机号、邮箱、发票税号明文（脱敏后存）。

---

## 5 接口契约（在 existing 基础上补齐）

### 5.1 公开/客户侧

| 方法/路径 | 用途 | 鉴权 |
|---|---|---|
| `GET /api/catalog` | 价格/SKU/活动（公开页/软件内购共同数据源） | 无 |
| `POST /api/order` | 建单（sku、数量、发票需求、来源） | 无（限流/验证码） |
| `GET /api/order/{order_no}?token=` | 订单状态/绑定入口 | 订单 access token |
| `POST /api/order/{order_no}/bind` | 绑定机器码并签发 license | bind token |
| `POST /api/order/{order_no}/rebind` | 换机申请（限额/需审批） | 订单 token + 客服/后台 |
| `GET /api/license/{license_id}.json?token=` | 下载 license | download token |
| `POST /api/renew` | 续费下单（license_id） | 订单/客户 token |
| `POST /api/invoice` | 申请发票（普票/专票） | 订单 token |
| `GET /api/invoice/{invoice_id}` | 发票状态/PDF 下载 | 订单 token |
| `POST /api/refund` | 退款申请（未绑定签发 7 天内） | 订单 token |
| `POST /api/license/resend` | 重发 license（邮件/下载链接） | 订单 token |
| `GET /api/customer/orders` | 我的订单（P1 账户体系后） | 登录态 |
| `GET /api/customer/licenses` | 我的授权/设备（P1） | 登录态 |
| `GET /api/team/seats`、`DELETE /api/team/seats/{id}` | 团队席位列表/移除（P1） | 团队管理员登录态 |
| `POST /api/team/seat` | 团队席位绑定/更换（P1） | 团队管理员登录态 |
| `POST /api/ticket` | 提交售后工单（P1） | 登录态/订单 token |

### 5.2 后台

| 方法/路径 | 用途 |
|---|---|
| `GET/POST /admin/orders` | 订单列表/详情/标记对公已付/关闭 |
| `GET /admin/customers`、`/admin/customers/{id}` | 客户 360：订单/授权/设备/发票/工单 |
| `GET/POST /admin/licenses`、`/admin/licenses/{id}/reissue` | 授权列表/重签/撤销标记 |
| `GET /admin/devices` | 机器码绑定/断绑/换绑记录 |
| `GET/POST /admin/invoices` | 开票队列/开具/红冲 |
| `GET/POST /admin/refunds` | 退款审批/状态 |
| `GET/POST /admin/coupons`、`/admin/campaigns` | 优惠码/早鸟活动 |
| `GET /admin/teams`、`/admin/teams/{id}` | 团队/席位管理 |
| `GET/POST /admin/orgs`、`/admin/contracts` | 机构/合同/报价台账（P1） |
| `GET /admin/tickets`、`/admin/tickets/{id}` | 工单队列/处理（P1） |
| `GET /admin/reconciliation` | 渠道对账差异/处理 |
| `GET/POST /admin/mcp-policies` | 团队/机构 MCP 本地策略文件模板/导出记录（P1，非云端下发） |
| `GET /admin/grants`、`POST /admin/grants/batch` | 赠送/补偿授权批量签发（存量用户迁移） |
| `GET /admin/reports/*` | 收入/退款/发票/续费/转化 |
| `GET /admin/audit` | 操作审计 |
| `GET/POST /admin/settings/*` | 价格/支付/邮件模板/权限 |

> 所有后台接口：登录 + TOTP 2FA + RBAC + 操作审计；敏感操作（签发、退款、红冲、改价）需二次确认。

---

## 6 前端：客户侧需要什么

### 6.1 页面清单（P0/P1）

| 页面/组件 | 内容 | 鉴权 | 优先级 |
|---|---|---|---|
| 官网首页 | 价值/功能/价格入口/MCP 页 | 无 | 已有，需补价格入口 |
| `/pricing` | 4 档价格 + 机构 banner + 对比表 + FAQ | 无 | P0（已有设计 [pricing-page.md](pricing-page.md)） |
| `/buy` | 选版本/联系方式/发票需求/支付方式 | 无 | P0 |
| `/bind` | 机器码录入、生成/下载 license、邮件重发 | bind token | P0 |
| `/order` | 订单查询、重新下载授权、申请发票/退款 | 订单 token/短信 | P0 |
| `/license`（产品内） | 授权状态/导入/备份/机器码/购买入口 | 本地 | P0（已有设计） |
| 软件内购弹窗 | A 静态二维码/B Native 支付、支付状态、成功解锁 | 本地 + 用户点击联网（B） | P0（plan 05） |
| DSH 插件升级卡 | 授权状态/价格/二维码/导入引导 | 本地 | P0（已有原型） |
| `/customer`（P1 账户） | 我的订单/授权/设备/发票/续费 | 手机号验证码/微信登录 | P1 |
| `/team`（P1） | 团队成员/席位/机器码/续费/发票 | 团队管理员 | P1 |
| `/help`、`/status`、协议/隐私/退款 | 自助帮助、服务状态、法律文本 | 无 | P0/P1 |
| 发票申请页 | 普票/专票表单、开票状态、下载 | 订单 token | P0 |

### 6.2 客户侧体验要求

- **非技术用户**（律师/医生/财务）：机器码复制、授权导入要有一句话+截图/GIF；不出现「token/签名/回调」等术语；
- **两条路径**：ICP 前 A（静态码/对公/卡密/授权码）；ICP 后 B（Native 扫码自动解锁）；同一 UI 服务端切换；
- **MCP 用户**：Agent 工具返回 `license_required` 时，界面/终端给出「复制购买链接/打开购买弹窗」；**不得让模型代付**；
- **失败兜底**：支付成功但授权未生成/邮件失败，页面必须能凭订单 token 重新下载；
- **合规**：购买页必须显著提示退款政策/数字化商品规则，勾选后下单；价格页不虚构原价/倒计时；发票说明可达。

---

## 7 前端：管理后台需要什么

| 模块/页面 | 功能 | 优先级 |
|---|---|---|
| 登录/2FA/账号 | TOTP、密码策略、登录审计、锁定 | P0 |
| 仪表盘 | 今日/本月收入、订单数、支付成功率、退款、待开票、待处理工单、到期续费提醒 | P0 |
| 订单 | 搜索/筛选/详情/标记对公已付/关闭/重发授权 | P0 |
| 客户 360 | 基本信息/订单/授权/设备/发票/工单/备注 | P0 |
| 授权 | 列表/详情/重签/换绑/撤销标记/下载 license/到期 | P0 |
| 设备绑定 | 机器码、绑定时间、换机记录、断绑 | P0 |
| 支付/对账 | 渠道流水、差异列表、退款队列、重复支付处理 | P0 |
| 发票 | 待开/已开/红冲、PDF 上传/下载、专票资料审核 | P0 |
| 团队/机构 | 组织、席位、成员、合同、机构报价单 | P1 |
| 优惠/活动/渠道 | 早鸟限量、优惠码、渠道码、分佣记录 | P1 |
| 报表 | 收入/退款/发票/续费/转化/早鸟消耗、导出 CSV | P1 |
| 工单 | 售后队列、换机/退款/发票申请 | P1 |
| MCP/Agent 治理 | 团队 agent 白名单、MCP 审计导出、机构策略 | P1 |
| 系统设置 | 商品目录、支付参数、邮件模板、角色权限、公告 | P1 |
| 操作审计 | 谁在何时改了什么、二次确认记录 | P0 |

**后台要求**：所有操作可追溯；金额/授权/退款类操作二次确认；导出有审计；角色至少 `super_admin / finance / support / sales` 四类。

---

## 8 授权、续费与客户生命周期的联动

```
获客（官网/软件内购/DSH/渠道）
   → 建客户(customer) → 下单(order) → 支付(payment) → 绑定(bind) → 签发 license
   → 订阅(subscription active) → 到期前提醒 → 续费订单(renewal) → 新 license（parent 链）
   → 到期宽限 → 过期回 free（产品侧）→ 重新购买
   → 退款/售后（ticket/refund）→ 发票红冲
```

关键规则（与 [licensing.md](licensing.md) 一致）：

- license 是**授权的唯一事实**；订单/客户表只是商业记录。产品只认 license 文件；
- 续费 = 新订单 + 新 license（同机器码、新到期），旧 license `replaced`；买断 = `perpetual` + `updates_until`；
- 换机 = 后台重签（新机器码），旧 license `replaced`；自助换绑仅限后台/客服；
- 退款：**未绑定签发且 7 天内**可退；已绑定/已签发不退（数字化商品）；退款后 license `revoked` 仅记录，离线无法远程关停（见 licensing 风险）；
- 到期提醒：30/14/7/1 天邮件/软件内横幅；宽限 14 天；**正式价用户续费锁正式价；早鸟仅首年、续费恢复正式价（早鸟用户是否享 9 折待拍板 D-M8）**；`price_snapshot` 记录下单价格用于财务对账，不用于永久锁早鸟价；
- **存量迁移（必须）**：v1.2.x 存量安装用户按 plan 05 P3 赠送 1 年专业版；用 `grant_license`（`source=grant`，`order_id` 可空）+ 批量按机器码签发/去重/审计，后台 `/admin/grants/batch`；**明确 Pro 还是 Personal**（plan 05 写 Pro、plan 02 写 Personal，需拍板；建议 Pro 以不中断 MCP）；<br>建议目标版本 **v1.3.0**（licensing 首次上线），发版说明写清“存量用户 1 年 Pro 从首次启动/导入授权日起算”；
- **MCP 权益**：license `features` 含 `mcp`（专业版+）；门禁在产品/MCP 侧，不在商业侧；详见 [mcp-monetization.md](mcp-monetization.md)。

---

## 8.5 选型与预算（来自 13 报告 §6 / raw-C）

| 模块 | 买 or 自建 | 具体选型 | 成本（2026-10-24 调研） | 理由 |
|---|---|---|---|---|
| 离线授权内核（Ed25519/机器码/trial/门禁） | **自建** | 本仓库 [licensing.md](licensing.md) | 5–7 人日，≈¥0.4–1.05 万；0 订阅 | 离线、中国主体/发票/数据合规；比 Keygen 等更便宜 |
| 购买服务（订单/回调/绑定/签发/后台/对账） | **自建** | FastAPI + SQLite/Postgres + 独立 signer | 15–20 人日，≈¥1.2–3.0 万 | 订单→支付→授权→发票是一条链；第三方只覆盖一段 |
| license SaaS（P2 备选，不做主库） | 买 | Keygen Std1 $99/月≈¥665/月（¥6,653/年）；Cryptlex $100/月；LicenseSpring $199/月；LimeLM $47/月；Licentric $5–199/月（支持 Ed25519 离线，仍不推荐做主库） | P2 才考虑 | 数据出境、无中国发票、停服风险；离线文件仍要自建 |
| 支付（境内） | 直连 | 微信支付/支付宝 | 行业口径 0.6%（以签约合同为准） | 比 MoR（Paddle 5%+$0.50、LS 5%+50¢、Gumroad 10%+$0.50）便宜 8–18 倍 |
| 发票 | P0 人工 / P1 买 | 人工/税控 P0；P1 云开票（诺诺/高灯/百望三选一） | P0 按张；P1 按张 + 商户费（公开锚点：明道云加购 ¥999/商户/年、税号 ¥1,500/年） | 自建开票资质/税控复杂，API 更快 |
| CRM/客户台账 | P0 免费工具；P1 低代码 | 企微 + 飞书多维表格（¥0）；P1 明道云 Standard ¥14,900/年（30 用户）或简道云标准版 ¥8,000 起/年 | P0 ¥0；P1 ¥0–1.5 万/年 | 0–50 人单产品线，专业 CRM 采购/实施不划算 |
| 电子合同 | P1 按需买 | e签宝/法大大（未公开，按份/套餐） | 按合同量 | 仅团队/机构合同需要 |
| 客服 | P0 免费；P2 按需 | 企业微信/邮箱 P0；美洽等 P2 | ¥0–按坐席 | 早期工单量小 |
| 海外收款（P2） | 买 | Paddle/Lemon Squeezy（需确认中国主体资格） | 5%+$0.50 级 | 仅海外/外币补充，不能替代中国发票 |

**预算结论**：P0 **固定 SaaS 采购 ≈ ¥0**（只有人日投入；服务器约 ¥100–400/月、短信/邮件、支付费率、发票按张为变动成本，另计）；P1 增量约 **¥1–3 万/年**（云开票 + CRM-lite 可选）；P2 再开 license SaaS/海外 MoR/专业 CRM 预算。**license 记录必须放自建核心 DB，第三方只做门户/查看，不做主库。**

### 8.6 结果付费（第三轨，可选试点）

- 设计见 [result-payment.md](result-payment.md) 与 [14 报告](../research/14-结果付费方案设计与对比.md)；
- 商业侧新增 **订单类型 `result`**（P0 只做单份；`credits_pack` P1 再评估）、`result_token` 签发、逐单退款/发票（支持按月合并）、后台结果订单/异常；
- 与年费/席位的关系：**不替代**；团队/机构不开放；MCP 不参与（只认 Pro/Team/Org）；
- 增量估算：**默认只做“首单 ¥9.9 获客券” 8–12 人日**（不改免费额度/水印/导出 gate）；完整版需 D-R1 且约 **31–45 人日**（含预览链路重构/产品底线改写），**未拍板前不立项**；在 P0a/P0b 之后，不占 P0 主路径；
- 风险：微支付/逐单发票/客服成本、年费蚕食、截图绕过；评审结论是“当前约束下需求≈0”，先做首单券验证获客，不做完整版。

---

## 9 安全、合规与运维

| 项 | 要求 |
|---|---|
| 支付安全 | 回调验签、金额 int 分核对、幂等、重复支付退款、渠道证书轮换、限流、防重放（详见 payment-invoice §8） |
| 账号安全 | 后台 2FA、强密码、登录锁定、RBAC、操作审计；客服账号不能改价/签发（需审批） |
| 密钥 | 支付密钥/APIv3/签名私钥只在商业服务器/签名器；不进仓库/安装包/CI；轮换预案 |
| 数据最小化 | 只收交付/开票必需信息；不采集用户文件；机器码仅存授权所需（授权文件含明文、后台展示掩码、日志脱敏）；PIPL 告知与删除机制 |
| 第三方工具 | 飞书/企微/低代码仅放**线索/联系人/订单状态**，不放开票税号、机器码、授权文件；隐私政策写明第三方处理字段/目的/保留期 |
| 数据保留 | 订单/发票按会计档案要求（财务确认，通常 ≥10 年）；日志 6–12 个月；隐私政策写明 |
| 备份/DR | DB 每日备份 + 异地；对象存储版本化；恢复演练季度；RPO ≤24h，RTO ≤4h |
| 监控 | 站点可用性、支付成功率、回调失败、授权签发失败、开票积压、退款积压、邮件退信 |
| 对账 | 每日拉渠道账单 vs `payments`；差异告警人工处理；财务月度导出 |
| 合规 | ICP 备案/营业执照展示、用户协议/EULA/隐私/退款政策、发票说明；不承诺合规、不虚假原价/倒计时 |
| 反滥用 | 下单/绑定/查询限流、验证码、订单 token 高熵、后台登录风控 |

---

## 10 分期与估算（评审后上调）

> 以 [plans/05](../plans/05-付费与解锁-软件内购与ICP过渡-规划.md) 的 33–46 人日为基线；下表按模块**自下而上重估**（含约 20% 缓冲，不含市场/法务/客服）。P0 必须拆成 **P0a（ICP 前，可卖对公/卡密/人工签发）** 与 **P0b（ICP/商户号后，Native 支付/自动解锁/自动开票）**，否则「P0 能真实收款」依赖资质、排期必滑。

| 阶段 | 范围 | 人日 | 交付标志 |
|---|---|---|---|
| **P0a 收款前闭环**（现在可做） | 授权内核、免费版/限额、内购入口 A、客户台账最小集、对公+人工签发、发票人工、后台订单/授权/客户基础、存量 grant | **28–36** | 不依赖商户号即可卖对公/卡密；存量用户可领 1 年 Pro |
| **P0b 线上收款**（ICP+商户号后） | 微信/支付宝直连、回调/对账、绑定自动签发、Native 扫码 B、云开票、退款自动化 | **20–28** | 真实支付→自动解锁→开票→对账闭环 |
| **P0c 结果付费（默认只首单券）** | 首单 ¥9.9 券/人工核销/订单后台；完整版需 D-R1（预览重构+水印+gate 例外） | **8–12（首单券）/31–45（完整版）** | 先验证首单→年费升级；完整版未拍板不立项 |
| **P1 运营完善**（团队/机构起量前） | 客户自助门户、团队/席位、优惠/渠道、续费自动化、报表、工单、发票自动化、RBAC 细化、MCP 治理（本地策略/审计导出） | **25–35** | 团队自助；财务报表；客服工单闭环 |
| **P2 规模化/治理** | 机构合同/报价、CRM/财务集成、电子合同、海外 MoR（如需）、集中 MCP 网关（另立项） | **20–30** | 机构可签约交付；销售/财务打通 |
| **合计（P0a–P2）** | 完整收费管理系统 | **约 90–130 人日** | 2 后端 + 1 前端约 3–4 个月（并行） |

**取舍**：若要在 11 月早鸟，只做 **P0a**（28–36 人日）+ 人工/对公；P0b/P1 后置。若砍掉 P1/P2 的 CRM/电子合同/海外 MoR/集中网关，可压到约 **75–95 人日**。

---

## 11 验收清单（P0）

| # | 验收项 | 通过标准 |
|---|---|---|
| 1 | 客户主档 | 下单自动建/合并客户；后台可按手机号/订单号/机器码查到全部记录 |
| 2 | 订单/支付 | 微信/支付宝/对公三条链路；回调验签/金额/幂等；重复支付退款 |
| 3 | 授权 | 付款→绑定→下载 license→离线导入→解锁；换机/续费重签；旧 license replaced |
| 4 | 续费 | 到期提醒（邮件/软件内）；宽限；续费后到期日延长；正式价用户锁价、早鸟恢复正式价 |
| 4.5 | 存量用户 | `grant_license` 批量签发（1 年 Pro）+ 去重 + 审计；无订单 license 可查、可续费 |
| 5 | 发票 | 普票申请→开具→邮件/下载；专票资料审核；退款红冲 |
| 6 | 后台 | 订单/客户/授权/发票/退款/对账/审计可用；敏感操作二次确认；2FA/RBAC |
| 7 | 软件内购 | A 静态码（零外呼）/B Native（用户点击联网）切换；失败回退 |
| 8 | MCP 门禁 | 未授权调用 MCP 返回 `license_required`；专业版全通；团队 license 逐机校验 |
| 9 | 对账 | 每日差异可列出；财务可导出；退款/发票与订单一致 |
| 10 | 安全/合规 | 无私钥进包/仓库；回调安全；PII 最小化；协议/退款/隐私页面可达 |

---

## 12 开放问题

| # | 问题 | 建议 | 谁定 |
|---|---|---|---|
| Q1 | 个人客户是否需要登录账户？还是只靠订单 token？ | P0 用订单 token/短信验证码，P1 再上账户（手机验证码/微信登录） | 产品 |
| Q2 | 团队管理台做在公网还是软件内？ | 公网 `/team`（管理员）；软件内只展示授权状态 | 产品 |
| Q3 | 是否采购 CRM？何时？ | P1 前不买；用飞书/企微表格；销售 ≥2 人且线索 >100/月 再评估纷享销客/销售易 | 销售 |
| Q4 | 是否用 Keygen/Cryptlex 等 license SaaS？ | 不依赖；自建离线 license（核心资产）；如要快速后台可自托管 Keygen 仅做辅助 | 工程 |
| Q5 | 发票自动化选哪家？ | 云开票 API（诺诺/高灯/百望）P1 接入；P0 人工/半自动 | 财务 |
| Q6 | 续费自动扣款做不做？ | 中国用户对自动续费敏感，P1 只做提醒+手动续费；自动续费需合规明示 | 产品 |
| Q7 | 机构合同/报价单是否进系统？ | P1 简单合同台账；完整合同管理在 CRM/电子签（e签宝/法大大） | 销售/法务 |
| Q8 | 客户数据能否导出/删除（PIPL）？ | P0 后台导出/删除流程；隐私政策写明 | 法务 |
