# 04 · MCP L2 规划：Agent / DSH 内「脱敏检测」与工作台复核（规划，未写代码）

> **状态**：规划文档 · 2026-10-21 · **未修改任何产品代码**
> **输入**：[12-MCP交付形态调研](../research/12-MCP交付形态调研.md)（v1.1.2 现状与 L0-L2 缺口）
> **目标**：把"用户在 Agent 里看不到、也操作不了工作台"变成"Agent 对话里有检测卡、DSH 右侧有检测面板、需要细修时一键深链到浏览器工作台"。
> **结论先行**：
> 1. **L0-L2 可以做到不破坏现有项目**——前提是"只加不改、能力协商、默认关闭、旧 4 工具契约冻结"（§1.3 四条约束）。
> 2. 推荐分 6 个可独立回滚的里程碑，总计 **约 20-26 人日**（引擎 12-16、DSH 插件 5-7、QA/发布 3）。
> 3. 最终效果：DSH 对话内出现 **MCP 脱敏工具卡（含结果摘要/缩略图/待确认）** + 右侧 **「脱敏检测」面板（概览/预览/实体/审计）**；框选补漏等复杂操作一键跳到浏览器工作台，**同一个 job** 无感衔接。
> 4. 原型图：[DSH 检测面板](../prototype/mcp-l2-dsh-detection.png) · [工作台深链](../prototype/mcp-l2-workbench-deeplink.png)（见 §5、§6）。

---

## 0 一句话回答："会不会破坏现有项目？"

**不会**，但有 4 个前置约束（§1.3）。理由：

- 现有产品有两条入口：HTTP 工作台（`api.py` + `jobs.py` + `pipeline.py`）与 MCP stdio（`mcp_server.py`）。L0-L2 不碰解析/检测/替换/导出/自检内核（`pipeline.py` 只读复用），只做**新增入口、新增工具、新增资源、复用 JobManager**。
- MCP 现有 4 个工具与返回字段**冻结**：只新增字段/新增工具，不删不改。老客户端（当前 Claude Desktop / Cursor / DSH）看到的仍是 4 个工具。
- 新能力全部 **capability-gated**：客户端不声明 `resources` / `elicitation` 就只用旧路径；服务端协议版本按客户端请求协商。
- 打包是"新增一个 MCP 入口 EXE"，GUI 入口与 `console=False` 现状不变；不需要数据迁移（审计表/映射库 schema 不动）。
- DSH 集成全部走**外部插件 + profile 配置**，不修改 DSH 安装包（asar 不动），删掉配置/插件即回退。

**会变（但不是破坏）**：MCP 调用会开始产生 Job、写审计、成品落到 `data/out`，因此控制台任务列表会多出 MCP 来源的任务。这是 L1 的设计目标，且可用开关 `YINZHAN_MCP_JOBS=0` 回到旧行为。

---

## 1 影响评估（逐项）

### 1.1 评估口径

每条变更按 5 个维度打分：**触及文件 / 现有行为影响 / 回退方式 / 风险级 / 必测项**。风险级：低 = 纯新增或纯文案；中 = 复用现有模块、需回归；高 = 动核心路径（本计划没有这类改动）。

### 1.2 变更清单

| # | 变更 | 触及文件 | 对现有行为的影响 | 回退 | 风险 |
|---|---|---|---|---|---|
| L0-1 | 打包独立 MCP 入口 `Sanitized_Data_MCP.exe`（console 子系统，与 GUI 共享 `_internal`） | `desktop/app.spec`、`desktop/build_win.ps1`；`installer.iss` 只打包 `dist/*` 自动包含 | GUI 入口/启动流程不变；无 Python 机器可跑 stdio | 删除 spec 中第二个 EXE 目标即可 | 中低（构建 PoC） |
| L0-2 | `/mcp`、README、support-matrix 区分"安装版/源码版"；首页"复制即用"加限定 | `app/web/mcp.html`、`README.md`、`docs/install-guide.md`、`docs/support-matrix.md`、`app/web/home.html` | 纯文案；无运行时行为 | git revert | 低 |
| L0-3 | 版本号/BUILD_ID 提升（1.2.0），`/api/health` 增加 `mcp` 能力字段（只增） | `yinzhan/config.py`、`app/web/*` 版本字符串 | 旧客户端不受影响 | 回退版本号 | 低 |
| L1-1 | MCP 改走 `JobManager`：`sanitize_document` 内部 = create → wait → export；新增 `job_id` 字段（旧字段保留） | `app/server/mcp_server.py`（新增适配层）；`jobs.py` 只加只读 accessor/`origin` 字段 | MCP 结果体**只增不改**；新增 Job/审计/`data/out` 成品；MCP job 参与 24 条内存淘汰（`jobs.py:1240-1253`），需为 MCP 预留 TTL | 环境变量 `YINZHAN_MCP_JOBS=0` 走旧实现（保留旧代码路径 1 个版本） | 中 |
| L1-2 | 工作台支持 `?job=<id>` 深链 + "来源 MCP" 徽标（只读打开已有 job） | `app/web/app.js`（新增 URL 参数解析）、`app/web/style.css` 少量、`api.py` 无改动 | 不带参数时行为完全不变 | 移除参数解析 | 低 |
| L1-3 | MCP 审计补齐（复用 `jobs.export` 的 `audit.append`） | 无新增写点（L1-1 复用即得） | 审计链多出 `mcp` 来源记录；旧记录/`verify_chain` 不变 | 关闭 L1-1 即回退 | 低 |
| L2-1 | MCP resources：jobs 列表/快照/实体/审计/页面预览图 | `mcp_server.py`（新增 resources 实现，只读） | 老客户端看不到 resources，忽略即可 | 能力开关 | 低中（预览图性能） |
| L2-2 | 新增 10 个工具（job 化、实体复核、框选、导出、审计、还原、打开工作台） | `mcp_server.py`（新增 HANDLERS）；`jobs.py`/`api` 复用 | 旧 4 工具完全不变；新工具与 HTTP 路由是同一套 Job 逻辑 | 逐个工具开关 / 移除 | 中（契约冻结） |
| L2-3 | prompts + elicitation + 协议版本协商（2024-11-05 ↔ 2025-06-18） | `mcp_server.py`（initialize/handle 扩展） | 客户端不提 elicitation 时走旧式确认工具；协议版本按请求回应 | 固定回 2024-11-05 | 中（老客户端矩阵） |
| L2-4 | `Sanitized_Data_MCP.exe` 的打包与启动日志/健康检查 | 同 L0-1 | 无 | 同 L0-1 | 低 |
| DSH-1 | DSH profile 注册 sanitized-data MCP server | `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml` | 只影响该 DSH profile；删条目回退；DSH 安装目录不动 | 删除 patch 条目 | 低 |
| DSH-2 | DSH 客户端插件「脱敏检测」面板 + host 代理 | 新插件包 `@blctek/dsh-plugin-sanitize-detect`（不进入 Sanitized_Data 仓库） | 不装插件时 DSH 与现状一致 | 卸载插件 | 中低（跨仓库） |
| DSH-3 | （可选，后续）发送前自动检测 hook | DSH 插件侧新增 | 默认关闭；开启后可能拦截附件发送 | 开关 | 中高（后续评估，不进本期） |

### 1.3 不破坏的四条硬约束

1. **旧契约冻结**：现有 4 个 MCP 工具的名称、参数、返回字段（`ok/output/mode/pack/entities/by_type/verify/mapping_id/warnings`）不允许删改；`sanitize_document` 只允许**新增** `job_id` / `workbench_url` 字段。
2. **能力协商**：`resources` / `prompts` / `elicitation` 只在客户端声明能力时启用；不支持 elicitation 的客户端走 `confirm_token` 回退（或纯导出到工作台确认）。服务端 `initialize` 对客户端请求的协议版本做协商，不硬编码单一版本。
3. **不碰内核**：`pipeline.py` 的解析/检测/决策/导出/verify 语义零改动；MCP 只调用 `JobManager` 与只读快照。
4. **可回退**：`YINZHAN_MCP_JOBS=0`、`YINZHAN_MCP_RESOURCES=0` 两个开关 + 插件可卸载 + 配置条目可删。发布前跑完整回归（`regression.py` 251 项硬检查、`run_all`、e2e、安装验收）+ 老客户端兼容矩阵（§3.4）。

### 1.4 为什么现有回归不会大面积挂

- `regression.py:1226-1229` 的 MCP 契约检查是**子集断言**：`need <= set(tool_names)`，新增工具不破坏。
- `mcp_server.py` 的 stdio 测试（`qa_common.py:1395+`）按方法/工具逐个调用，新增方法不影响旧调用。
- 打包/桌面检查（`qa_common.py:761` 等）检查 `mcp_server.py`/`rehydrate.py` 在源码树存在，新增 EXE 是加法。
- 需要重录的只有：版本号/`BUILD_ID` 相关断言、审计记录数（测试均用临时数据目录）、新增测试域（`test_mcp_v2.py`、`test_mcp_resources.py`、`test_dsh_contract.py`）。

### 1.5 主要风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| PyInstaller 双入口共享 `_internal` 的构建/启动问题 | 中 | M0 先做 0.5-1 人日 PoC：同一 `Analysis` 收集两个脚本、两个 `EXE(console=True/False)`、一个 `COLLECT`；失败则退化为独立 `Sanitized_Data_MCP.exe`（体积 +~300MB，需另评估安装包体积） |
| MCP Job 被 24 条淘汰策略挤掉 | 中 | `origin=mcp` 的任务在淘汰时豁免最近 8 个/30 分钟（只改 `_evict` 选择顺序，不改上限语义） |
| 老客户端被 elicitation/协议升级打挂 | 中 | 兼容矩阵 + 默认旧协议；`initialize` 回显客户端版本；新能力按 capability 注入 |
| 预览图拖慢 MCP 调用 | 低中 | resources 按需读、图片走 `render` 缓存（`jobs.render` 已有缓存/预览锁）；工具卡只给第一页缩略图 |
| DSH 面板跨域访问本机 8760 | 中 | 不从前端直连：DSH host 插件做本地代理（host 进程无 CORS 限制，引擎对无 Origin 请求放行，`api.py:304-306`）；面板只拿脱敏后的数据 |
| DSH 版本升级破坏插件 | 低中 | 插件 peerDependency 固定 `@deepseek-ai/cordis ~4.0.5` / `@deepseek-ai/dsh-* 0.2.1-rc.2`；CI 做 DSH 宿主契约测试 |

---

## 2 L2 目标架构

### 2.1 分层

```
┌─────────────┐   ┌──────────────────────────────┐
│ Claude/Cursor│   │ DSH（DeepSeek Harness）      │
│ （MCP 客户端）│   │  对话 / 右侧「脱敏检测」面板   │
└──────┬──────┘   └───────┬──────────────┬───────┘
       │ stdio MCP        │ MCP stdio    │ 本机 HTTP（host 插件代理）
       ▼                  ▼              ▼
┌─────────────────────────────────────────────────┐
│ MCP Server v2（Sanitized_Data_MCP.exe）          │
│  旧 4 工具（不变） + 新 job/复核/导出/资源/提示词  │
└──────────────────────┬──────────────────────────┘
                       │ 复用（唯一事实源）
┌──────────────────────▼──────────────────────────┐
│ JobManager / jobs.py（现有）                      │
│  create → parse → detect → decide → export/verify │
│  → audit.append → vault.save_mapping              │
└──────────────────────┬──────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   data/out、vault、audit 链      HTTP 控制台（现有工作台）
                                  ↕ 深链 ?job=<id>（同一 job）
```

### 2.2 关键设计决策

1. **唯一事实源**：MCP 不复制脱敏逻辑，全部复用 `JobManager.create/export`（`jobs.py:220`、`:645`）。因此审计（`jobs.py:686`）、失败删除（`jobs.py:740-748`）、映射（`:701-708`）、24 条淘汰（`:1240-1253`）自动与工作台一致——顺带修掉调研中"MCP 不写审计 / 吞 verify 错误 / 输出到 cwd"三个缺口。
2. **Job 化 + 来源标记**：`Job` 增加只读 `origin`（`api` | `mcp`），`snapshot()` 增加 `origin` 字段（只增）。控制台任务列表可显示"MCP"徽标。
3. **预览图两条通道**：工具返回里放 `image/png` content block（DSH 会把 MCP 结果图片存入附件并渲染，见 §4.1），资源 URI（`sanitize://jobs/{id}/pages/{n}?mode=orig|out`）供显式读取；数据类（实体/审计）走 resources，避免把大 JSON 塞进模型上下文。
4. **确认通道**：`mode=human`、`allow_unprocessed_media=true`、异常码候选确认、未成年人严格模式，优先走 MCP elicitation；客户端不支持时回退 `confirm_export` 工具 + 返回 `confirm_token`，或直接提示"请到工作台确认"。
5. **深链**：MCP 结果与面板按钮都返回 `workbench_url = http://127.0.0.1:8760/console?job=<id>`；工作台新增 URL 参数支持（只读打开，不带参数行为不变）。
6. **协议兼容**：服务端支持 `2024-11-05` 与 `2025-06-18`，`initialize` 回显客户端版本（双方都支持时取高版本）；新增能力只在协商通过后声明。

### 2.3 工具 / 资源契约（新增部分）

**保留（冻结）**：`sanitize_document`、`redact_text`、`check_support`、`list_rules`。

**新增工具（11 个，前缀 `sanitize_`）**：

| 工具 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `sanitize_file` | `path, mode?, strictness?, id_card_mode?, time_blur?, packs?` | `job_id, stage, workbench_url` | 创建 Job 并等待 ready（或立即返回、用 get 轮询） |
| `get_sanitize_job` | `job_id` | 快照（stage/progress/counts/pending/warnings/exports） | 轮询/恢复 |
| `list_sanitize_jobs` | `limit?` | 最近 job 列表（含 origin） | 会话内历史 |
| `get_sanitize_entities` | `job_id, status?` | 实体清单（id/label/etype/status/value 摘要/位置/spans） | 面板"实体"页 |
| `update_sanitize_entity` | `job_id, entity_id, action(accept/reject/replace), value?` | 更新后计数 | 与 `api.py:197-204` 同逻辑 |
| `mark_sanitize_region` | `job_id, page, rect[0-1], label?` | 新实体 id | 框选补漏（`api.py:192`） |
| `accept_all_sanitize_entities` | `job_id` | 计数 | 对应 `api.py:202` |
| `export_sanitize_job` | `job_id, mode(ai/human), allow_unprocessed_media?, keep_pdf_hidden?` | `out_path, sha256, seal_no, verify, warnings` | 对等 `api.py:219-230` |
| `get_sanitize_audit` | `job_id? / limit?` | 审计行（无原值）+ 链校验 | 对等 `api.py:233-241` |
| `rehydrate_sanitize_text` | `job_id/mapping_id, text` | 还原后文本 | 复用 `vault.rehydrate`；仅本机、需显式调用 |
| `open_sanitize_workbench` | `job_id?` | URL | 返回深链（客户端打开由 UI 决定） |

**新增 resources（5 类）**：

| URI | 内容 |
|---|---|
| `sanitize://jobs` | 最近 job 列表（JSON） |
| `sanitize://jobs/{id}` | job 快照（JSON） |
| `sanitize://jobs/{id}/entities` | 实体清单（JSON，含 status/spans） |
| `sanitize://jobs/{id}/audit` | 审计记录 + 链校验（JSON） |
| `sanitize://jobs/{id}/pages/{n}?mode=orig\|out` | 页面 PNG（供能渲染资源的客户端；DSH 资源工具以 JSON 文本呈现，图片走工具 content block） |

**新增 prompts（2 个）**：`review-before-export`、`workbench-fallback`（引导模型先预览/处理待确认再导出）。

**返回体兼容示例**（`sanitize_document` 只增字段）：

```json
{
  "ok": true, "output": "...", "mode": "ai", "pack": "...",
  "entities": 15, "by_type": {...},
  "verify": {"verified": true, "residual": 0},
  "mapping_id": "mcp_...",
  "warnings": [],
  "job_id": "j_ab12cd34",
  "origin": "mcp",
  "workbench_url": "http://127.0.0.1:8760/console?job=j_ab12cd34"
}
```

### 2.4 兼容矩阵（必须通过）

| 客户端 | 旧服务 | 新服务（默认） | 新服务（资源/elicitation） |
|---|---|---|---|
| Claude Desktop（现网） | ✅ | ✅（旧 4 工具不变） | ✅/降级（不支持 elicitation 时回退确认工具） |
| Cursor | ✅ | ✅ | ✅/降级 |
| DSH（0.2.1-rc.2） | ✅ | ✅ | ✅（resources + image 支持验证） |
| 无 resources 能力的自造客户端 | ✅ | ✅（忽略 resources） | ✅（工具内图片可选） |

---

## 3 分期与验收（不写代码）

| 里程碑 | 内容 | 人日 | 验收标准 |
|---|---|---|---|
| M0 基线冻结 | 锁当前 251 项回归基线；PyInstaller 双入口 PoC；MCP v1 行为录像（返回体快照） | 1-1.5 | 回归全绿；PoC 能跑 `initialize/tools/list`；旧返回体快照入库 |
| M1 L0 交付 | MCP 入口 EXE + 文档区分安装版/源码版 + 安装包重打 | 2-3 | 无 Python 机器上，安装版按 `/mcp` 新文档一配即通；GUI/安装/卸载不变；安装包体积增量 <5% |
| M2 Job 化 | MCP 走 JobManager；审计/失败删除/映射与工作台对齐；`origin` 字段 + 淘汰豁免 | 3-4 | MCP 调用产生 `j_*` job + 审计行 + `data/out` 成品；verify 失败返回 `isError` 且成品删除；24 上限与豁免测试通过；旧 4 工具返回字段快照不变 |
| M3 资源与复核工具 | resources（快照/实体/审计/页面图）+ 11 个新工具 + 工作台 `?job=` 深链 | 4-5 | Claude Desktop/Cursor 能读资源；DSH 面板可完成"查看→确认→导出"闭环；旧客户端无感 |
| M4 确认与协议 | prompts + elicitation + 双协议协商 + `confirm_token` 回退 | 2-3 | 兼容矩阵全过；不支持 elicitation 的客户端行为不退化 |
| M5 DSH 检测 | host 代理插件 + 右侧「脱敏检测」面板 + 工具卡渲染（见 §4） | 5-7 | 面板 5 状态（概览/预览/实体/审计/错误）可用；深链打开同一 job；插件卸载后 DSH 恢复原状 |
| M6 回归与发布 | 全量回归 + 安装验收 + 老客户端兼容 + SHA256/发布说明 | 2-3 | 251 项硬检查 + `run_all` + e2e + 安装验收全绿；回退演练通过 |

**总量：约 20-26 人日**（2-3 人并行约 2-3 周）。若只做 M0-M4（不接 DSH 面板）：13-17 人日。

### 3.1 测试计划

- 沿用：`app/tests/regression.py`（251 项硬检查）、`run_all.py`（16 套件，含 previewparity）、`e2e.py`、`docs/qa/install-acceptance-*`。
- 新增：`test_mcp_v2.py`（job 化/失败语义/字段快照）、`test_mcp_resources.py`（URI/分页/权限）、`test_mcp_protocol_compat.py`（双协议/无 elicitation 回退）、`test_mcp_jobs_evict.py`、`test_workbench_deeplink.py`（前端静态断言 + 浏览器实测）。
- DSH 侧：宿主契约测试（mcp-client 配置 schema、tool 名 `mcp__sanitized-data__*`、image content 渲染、resources 工具）；插件卸载/重装回归。
- 人工兼容矩阵：Claude Desktop、Cursor、DSH 各跑一次"检测→确认→导出→还原→审计"。

---

## 4 DSH 集成方案（作为「检测」）

### 4.1 已在 DSH 0.2.1-rc.2 验证的能力（证据来自 DSH 安装包内插件源码）

| 能力 | 证据 |
|---|---|
| 原生 MCP 客户端桥（stdio / streamable-http） | `@deepseek-ai/dsh-mcp-client` 的 `Config`：`transport / serverName / command / args / env / cwd / toolCallTimeoutMs / reconnect`（`lib/index.js:780-800`）；工具名映射为 `mcp__<serverName>__<rawName>`（`:56-60`、`:64-72`） |
| MCP 结果图片可进对话 | MCP `image` content 会被解码、预检并存入 DSH 附件系统（`lib/index.js:74-80`、`:307-353`），随后作为图片渲染 |
| MCP resources | 内置 3 个模型工具 `list_mcp_resources` / `list_mcp_resource_templates` / `read_mcp_resource`（`@deepseek-ai/dsh-mcp-resources/lib/index.js:53-93`）；资源二进制以文本摘要呈现（`:16-24`）→ **预览图应走工具 image content，资源用于 JSON** |
| 客户端插件体系 | Cordis profile：`cordis.patch.yml` 加载条目 + `package.json` 的 `dsh.profile.bundles`；客户端插件声明 `dsh.client.inject`（如 `@deepseek-ai/dsh-client-ui-sidebar-right` 的 `package.json:29-41`） |
| 第三方插件可安装 | 当前 profile 已装第三方 `@xmanrui/dsh-im`（`%USERPROFILE%\.dsh\profiles\desktop\package.json`）→ 私有插件可行 |
| 面板挂载点 | `dsh-client-ui-sidebar-right`（右侧栏、面板/头部展开、导航服务）、`dsh-client-ui-jobs`（任务列表 + 流式记录面板）可参考 |

### 4.2 集成层级（建议按 T0 → T1 → T2 递进）

| 层级 | 做什么 | 代码量 | 效果 | 回退 |
|---|---|---|---|---|
| **T0 配置级（0 代码）** | profile 的 `cordis.patch.yml` 注册 `@deepseek-ai/dsh-mcp-client`（stdio → 安装版 `Sanitized_Data_MCP.exe`）；agent 指令加"文件先脱敏后交模型" | 0（DSH 配置） | 对话里出现 `mcp__sanitized-data__*` 工具；工具卡显示摘要；如工具返回 image content 可显示缩略图 | 删除 patch 条目 |
| **T1 对话内检测卡** | DSH 客户端插件自定义工具卡渲染：verify 徽标、命中计数、缩略图、待确认数、"打开工作台"/"导出"按钮 | 2-3 人日 | 不装面板也能"看到/操作"核心结果 | 卸载插件 |
| **T2 右侧「脱敏检测」面板（原型目标）** | host 插件（本地代理 + 生命周期）+ client 插件（右侧栏面板：概览/预览/实体/审计 + 操作） | 5-7 人日 | 完整检测体验（§5） | 卸载插件 |
| T3（后续） | 发送前自动检测/拦截 hook（附件/粘贴文本进入模型前触发） | 待 DSH hook 扩展评估 | 真正的"检测网关" | 开关 |

### 4.3 DSH 配置示例（T0，规划用，未实施）

```yaml
# %USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml（追加条目）
- id: mcp-sanitized-data
  name: "@deepseek-ai/dsh-mcp-client"
  config:
    transport: stdio
    serverName: sanitized-data        # 公开工具名：mcp__sanitized-data__*
    command: "C:\\Users\\<user>\\AppData\\Local\\Programs\\Sanitized_Data\\Sanitized_Data_MCP.exe"
    args: []
    toolCallTimeoutMs: 600000
    reconnect: { enabled: true, maxAttempts: 10 }
```

### 4.4 数据流与安全

- **数据流**：DSH 对话 → MCP stdio → Job（解析/检测/导出/自检/审计/映射）→ 返回摘要 + 缩略图 + `job_id` + 深链；面板经 **host 插件本地代理**读 `GET /api/jobs/{id}` 等（避免浏览器跨域；引擎对无 Origin 的本机请求放行，见 `api.py:304-306`），操作用 `POST` 走同一代理。
- **安全边界不变**：仅 127.0.0.1；不新增外呼；面板展示的是脱敏后预览/统计，原值只在工作台本机可见；审计只记类型/数量/哈希；MCP 返回给模型的内容保持"摘要 + 可外发路径"。
- **权限**：DSH 的审批/确认弹窗沿用（human 导出、内嵌媒体确认必须人点；模型不能代点，见 §2.2-4）。

### 4.5 插件交付形态

- 包名建议 `@blctek/dsh-plugin-sanitize-detect`（private npm / 本地 tarball / DSH 插件市场私有源）。
- 形态：一个 Cordis bundle，包含 host 侧（本地代理、引擎生命周期、健康检查）与 client 侧（右侧面板、工具卡渲染、深链按钮）；`dsh.profile.bundles` 追加即可安装，卸载删除条目/包。
- 兼容：peerDependencies 固定 `@deepseek-ai/cordis ~4.0.5`、`@deepseek-ai/dsh-client-ui-* 0.2.1-rc.2`；DSH 升级后跑宿主契约测试。

---

## 5 最终展示效果（用户视角）

### 5.1 场景脚本

1. **触发检测**：用户在 DSH 里说"把 `D:\合同.pdf` 脱敏后总结要点"（或引用附件）。Agent 调 `sanitize_file`，DSH 对话出现 **MCP 工具卡**：
   - 标题：`sanitized-data` → `sanitize_file`，状态"已脱敏 · 1.7s · 自检通过"；
   - 摘要：`15 处已隐去 · 残留 0 · 换名版可还原`；
   - 缩略图：原页/可外发版并排（敏感处红框 → 涂黑/假名）；
   - 按钮：`在检测面板复核` · `打开完整工作台` · `导出涂黑版`。
2. **看检测**：右侧自动出现 **「脱敏检测」面板**（也可从右侧栏常驻 tab 打开）：
   - 顶部：文件名、格式/页数、来源 `MCP`、状态"自检通过"；
   - 指标：`15 已隐去 / 2 待确认 / 0 残留`；
   - Tab：**概览**（类型分布 + 待确认清单 + 审计链 + 映射状态）、**预览**（左右对照页面，黑条按住 300ms 透视仅本机）、**实体**（逐条 accept/reject/replace）、**审计**（链校验、封条号、记录列表）；
   - 底部：`导出换名版 / 导出涂黑版 / 还原映射`；所有操作与 MCP/工作台同源，审计同步增长。
3. **细修**：需要框选补漏、整图涂黑、证件档位、时间模糊等复杂操作 → 点 **`打开完整工作台`** → 浏览器打开 `http://127.0.0.1:8760/console?job=j_...`，**同一个 job** 已在对照台就绪；改完回 DSH，面板状态自动刷新（复用同一快照接口）。
4. **追溯/还原**：面板"审计"页显示第 N 条记录、HMAC 链校验通过、封条号；换名版可"还原映射"粘贴模型回答，本机还原（默认不展示原值）。
5. **失败态**：自检失败 → 工具卡红色"自检未通过"、不返回 ok、成品被删除、面板给出问题清单（与工作台一致）；无工作台运行时给出"启动数据隐站"引导，不影响对话。

### 5.2 视觉原则

- 检测面板不抢对话主位：默认窄栏（约 420px），可折叠；状态色统一（绿=通过、琥珀=待确认、红=失败、灰=未开始）。
- 工具卡只放"够判断"的信息：摘要 + 缩略图 + 计数；详情进面板。
- 所有"不可逆动作"（涂黑导出、整图涂黑）二次确认；所有导出可见审计号。

---

## 6 原型图

| 图 | 文件 | 说明 |
|---|---|---|
| DSH 内「脱敏检测」面板 | [mcp-l2-dsh-detection.png](../prototype/mcp-l2-dsh-detection.png) | 对话内 MCP 工具卡 + 右侧检测面板（概览/待确认/审计）的最终形态示意 |
| 工作台深链 | [mcp-l2-workbench-deeplink.png](../prototype/mcp-l2-workbench-deeplink.png) | 从 DSH 一键跳转的浏览器工作台，同一 job、MCP 来源徽标 |

> 原型为 **UX 形态示意图**（非产品最终视觉）：图 1 为绘制稿；图 2 内嵌工作台为历史版本真实截图（v1.0.0），仅示意"同一 job 深链"效果，最终以 v1.1.2+ 与 DSH 主题为准；生成脚本在 `out/_mcp_l2_proto/`（未纳入版本库）。

---

## 7 决策点（需拍板）

| # | 决策 | 建议 |
|---|---|---|
| D1 | DSH 集成先 T1（对话内卡片）还是直接 T2（右侧面板） | 先 T1（2-3 人日，快速可验收），T2 跟随 |
| D2 | 预览图走工具 image content 还是 resources | 两者都要：工具卡 image content；resources 供模型显式读 |
| D3 | human/内嵌媒体确认走 elicitation 还是必须回工作台 | elicitation 优先，无能力回退"回工作台确认" |
| D4 | MCP job 保留策略 | `origin=mcp` 豁免最近 8 个/30 分钟，其余沿用 24 条淘汰 |
| D5 | MCP 是否作为专业版付费能力 | 本期不加门禁（licensing 尚未实现）；商业化与 ICP/内购路线见 [05 付费与解锁规划](05-付费与解锁-软件内购与ICP过渡-规划.md)，建议 L2 与 licensing 同版评审 |
| D6 | MCP 输出目录 | 统一 `data/out`（与工作台一致），不再用 `Path.cwd()` |
| D7 | 独立 MCP EXE vs 同 EXE `--mcp` | 优先 M0 PoC 验证共享 `_internal` 双入口；失败再评估独立 EXE |

---

## 8 不破坏承诺 checklist（发布前逐项打勾）

- [ ] 旧 4 工具名称/参数/返回字段快照 diff 为空（只允许新增字段）
- [ ] `regression.py` 251 项硬检查全绿；`run_all` 全绿；e2e 5/5
- [ ] 安装包：无 Python 机器安装 → GUI 正常 → `Sanitized_Data_MCP.exe` 可被 Claude Desktop/DSH 识别
- [ ] 老客户端兼容矩阵（Claude Desktop / Cursor / DSH）通过
- [ ] MCP 失败语义与工作台一致（verify 失败→isError+删除成品）
- [ ] 审计链 `verify_chain` 通过；MCP 记录可查、无原值
- [ ] 回退开关演练：`YINZHAN_MCP_JOBS=0` 回到旧路径、插件卸载后 DSH 恢复、配置条目删除后 DSH 启动正常
- [ ] 数据目录无迁移：旧 vault/审计/成品可直接用
- [ ] 原型与最终实现一致性评审（本文 §5）

---

## 9 证据索引

- 现状与缺口：[12-MCP交付形态调研](../research/12-MCP交付形态调研.md)（MCP 无头、安装包不含、审计/失败/pending 缺口）
- 工作台能力：`app/web/app.js`、`app/server/yinzhan/api.py:123-241`、`jobs.py:220-751`
- 复用点：`jobs.py:220`（create）、`:645-724`（export+audit+vault）、`:740-748`（verify 失败删除）、`:1240-1253`（淘汰）
- MCP 现状：`app/server/mcp_server.py:38-90`、`:154-179`、`:197-207`
- DSH 能力证据：`@deepseek-ai/dsh-mcp-client`（Config、工具名、图片投影）、`@deepseek-ai/dsh-mcp-resources`（3 个资源工具）、`@deepseek-ai/dsh-client-ui-sidebar-right`（面板插件形态）、profile `cordis.patch.yml` / `package.json`
