# MCP L2 与 DSH 联调验收（2026-10-23）

> 范围：MCP L2 server 侧（M2–M4）+ DSH T0 配置级联调 + T1 工具卡 + T2 真实工作台（DSH 原生 browser tab，v1.2.0）。
> 计划基线：[04-MCP-L2-Agent内检测-规划](../plans/04-MCP-L2-Agent内检测-规划.md)
> 实现说明：[docs/impl/mcp.md](../impl/mcp.md)

## 1. 验收结论（先说结果）

| 设计目标 | 状态 | 证据 |
|---|---|---|
| MCP 独立 stdio 入口，可被 DSH 识别 | ✅ | DSH `mcp__sanitized-data__*` 工具真实调用成功 |
| 旧 4 工具契约冻结（只加字段） | ✅ | `test_mcp_v2` 旧字段断言；`test_ai_export_naming` |
| Job 化：MCP 调用产生 `j_*`、审计、`data/out` 成品 | ✅ | DSH 返回 `j_a01399fb`，Web `/api/jobs/{id}` 同 job、`origin=mcp` |
| 11 个复核工具 + resources + prompts | ✅ | stdio 冒烟 15 工具/4 templates/2 prompts；`test_mcp_v2` 36 用例 |
| 同一 job 深链打开工作台 | ✅ | DSH 返回 `http://127.0.0.1:8760/console?job=j_a01399fb`，浏览器打开同 job（截图） |
| 失败语义（verify 失败/媒体确认/无工作台） | ✅ server 侧 | 结构化 isError；verify 失败删除成品；HTTP 不可用 `no_workbench` |
| T1 对话内定制工具卡（自检/计数/job/按钮） | ✅ | `out/dsh_test/plugin_card_running_03_result.png`（运行中卡片）；完成态在 DSH 默认折叠过程中 |
| T2 真实工作台（DSH 原生 browser tab） | ✅ | `native_workbench_tab.png`：DSH 右侧 browser tab 直接显示 127.0.0.1:8760 真实工作台；旧仿制面板已移除 |
| M4 elicitation / confirm_token | 🟡 部分 | human 导出新增一次性 `confirm_token` 挑战（standard 兼容）；协议级 elicitation 仍未实现 |
| 安装包内 `Sanitized_Data_MCP.exe` + DSH 插件 | ✅（v1.2.0 构建） | `app.spec` 双入口；`integrations/dsh-plugin-sanitize-detect` 随构建进入 `dsh-plugin/` |

**一句话**：MCP L2 服务端闭环 + DSH T0/T1/T2 已打通：T2 用 **DSH 原生 browser tab 直接加载真实工作台 WebUI**（已在 web profile 开启 browser 插件后实测），旧仿制面板移除；human 导出增加 confirm_token 挑战。剩余协议级 elicitation、T3 发送前拦截未做。

> **2026-10-23 设计调整**：T2 不再复刻工作台 UI（用户反馈仿制面板观感不真实）；左下角入口直接调用 `sidebarRight.openTab('browser', { url })` 打开真实 `/console?job=...`；旧 `plugin_panel*.png` 仅保留为历史证据，真实工作台 520px 预览见 `out/dsh_test/workbench_520.png`。


## 2. DSH 实测场景

### 2.1 文本脱敏工具（旧工具路径）

用户提示：让 DSH 调 `mcp__sanitized-data__redact_text` 脱敏一段文本。

- DSH 工具卡出现 `mcp__sanitized-data__redact_text · 张伟，手机号...`；
- 工具返回 `redacted: 张伟，手机号16834998731...`、`entities:1`；
- **规则缺口（已在 v1.2.0 修复）**：同一段里的身份证号 `110101199001011234`
  当时未被识别（by_type 只有手机号）。根因是 `cn.idcard` 的校验位硬门槛；
  v1.2.0 新增 `cn.idcard_shape` 兜底（校验位只作置信度信号、pending 人工复核），
  详见 `docs/qa/idcard-checksum-policy-20261023.md`。
- 截图：`out/dsh_test/dsh_mcp_final_final.png`

### 2.2 文件 Job + 复核工具 + 深链

用户提示：`sanitize_file` → `get_sanitize_job` → `open_sanitize_workbench`。

- 返回 `job_id=j_a01399fb`、`stage=ready`、6 个实体（手机号/固定电话/邮箱/律所/人名/地址）；
- `origin=mcp`、`backend=http`、`workbench_available=true`；
- Web 工作台 `/api/jobs/j_a01399fb` 返回**同一个 job**；
- 浏览器打开深链：`名片.png` 已就绪，6 个实体可逐条确认，页面标题与左侧任务一致；
- 截图：`out/dsh_test/dsh_mcp_httpjob_final_final.png`、
  `out/dsh_test/workbench_mcp_job.png`

### 2.3 复现方式（T0，不改用户默认 profile）

```powershell
# 1) 启动本机数据隐站（Web 工作台）
python -X utf8 app/server/run.py --no-browser

# 2) 用带 MCP patch 的 DSH web profile 启动（独立端口，不动默认桌面 profile）
dsh --profile web --patch out\dsh_mcp.patch.yml --no-open --port 60361
# 3) 浏览器打开日志里的 URL，在新会话里说：
#    “调用 mcp__sanitized-data__sanitize_file 处理 samples\名片.png，
#     再用 get_sanitize_job / open_sanitize_workbench 给我同一 job 的链接”
```

> 注意：DSH patch 必须写成 `- insert:` 追加条目；直接把 `- id: mcp-sanitized-data`
> 放顶层会被 loader 当作“补丁不存在的 id”跳过（本次联调踩过）。
> 安装版 command 换成 `%LOCALAPPDATA%\Programs\Sanitized_Data\Sanitized_Data_MCP.exe`。

### 2.4 截图证据

| 文件 | 内容 |
|---|---|
| `out/mcp_acceptance/dsh-redact-tool.png` | DSH 真实调用 `mcp__sanitized-data__redact_text`，工具卡与脱敏结果 |
| `out/mcp_acceptance/dsh-job-tools.png` | DSH 顺序调用 `sanitize_file`→`get_sanitize_job`→`open_sanitize_workbench`，返回同一 job 与深链 |
| `out/mcp_acceptance/workbench-same-job.png` | 打开返回的 `console?job=j_a01399fb`，工作台显示 MCP 创建的同一任务（名片.png，6 实体） |
| `out/mcp_acceptance/dsh-tool-call-card.png` | 工具调用进行中的 DSH 卡片（通用工具卡，非定制 T1） |
| `out/mcp_acceptance/mcp-page.png` | `/mcp` 页面 L2 工具/深链说明 |
| `out/dsh_test/plugin_panel.png` | T2 右侧面板概览（MCP 任务/6 实体/预览/导出按钮） |
| `out/dsh_test/plugin_panel_entities.png` | T2 实体页（逐条接受/拒绝/替换） |
| `out/dsh_test/plugin_panel_audit.png` | T2 审计页（HMAC 链/记录） |
| `out/dsh_test/plugin_panel_export.png` | 面板“导出换名版”执行成功 |
| `out/dsh_test/plugin_card_running_03_result.png` | T1 定制工具卡（运行中“数据隐站·建立脱敏任务”） |
| `out/dsh_test/workbench_520.png` | T2 真实工作台在约 520px 侧栏宽度下的实际效果 |


## 3. 门禁结果

> 本轮最终数字见 §3.1（全量 run_all/regression 完成后回填）。

| 测试 | 结果 |
|---|---|
| `test_mcp_v2.py` | **36/36 通过**（协议/旧工具/11 新工具/resources/prompts/Job/HTTP mock/失败语义） |
| `test_dsh_plugin.py` | **6/6 通过**（插件包结构/工具卡/真实工作台 tab/本地代理回环边界/Node 语法） |
| `test_pipl_rules.py` | **19/19 通过**（含身份证校验位异常兜底） |
| `test_ai_export_naming.py` | **29/29 通过**（MCP 段改为 MANAGER 带 seal 命名，成品落 data/out） |
| `test_visual.py` | **68/68 通过** |
| `regression.py` 的 MCP 段 | **8/8 通过**（stdio 端到端/旧 4 工具/residual=0/映射） |
| `run_all.py --only mcpv2` | **1/1 通过** |

### 3.1 最终全量门禁

- `app/tests/run_all.py`：**31/31 套件通过**（320.26s；新增 `mcpv2`/`dshplugin` 套件）。
- `app/tests/regression.py`：**249 项硬检查全绿**（新增 ID shape 规则；待 Lead 重录 baseline）。
- `app/tests/e2e.py`：**5/5**、残留 0（`run_all` 内含）。
- `test_mcp_v2.py`：36/36；`test_dsh_plugin.py`：6/6；`test_pipl_rules.py`：19/19；
  `test_ai_export_naming.py`：29/29；`test_visual.py`：68/68。
- 安装形态：PyInstaller 双入口构建成功，`dist/Sanitized_Data/Sanitized_Data_MCP.exe`
  可跑 stdio（stdout 仅 JSON-RPC；已禁用 OpenCL 编译日志污染）；GUI 主入口
  `Sanitized_Data.exe` 不变；MCP exe 无 Web 服务时回退 local（`workbench_available=false`
  + 启动引导）。**v1.2.0 构建将 MCP exe 与 `dsh-plugin/` 一起放入安装包/绿色版**；
  v1.1.3 发布包不含这些。

## 4. 相对设计目标的差距（诚实评估）

1. **T1/T2 均已实现**：定制工具卡已在 DSH 0.2.1-rc.2 跑通；T2 移除仿制面板，
   改为 DSH 原生 browser tab 直接打开 `http://127.0.0.1:8760/console?job=...`；
   Web profile 需显式启用 `ui-sidebar-browser`（Desktop 默认启用），已在 1440x1100 实测。
   工作台不可用时宿主会自动尝试拉起（白名单本地路径），失败则保留启动引导。
2. **MCP 结果图片**：`sanitize_document`/render 支持 `image/png` content block；
   面板预览通过同源代理直接显示；工具卡当前以摘要/详情为主，未把缩略图内嵌进卡。
3. **elicitation 未做，confirm_token 已做**：human 导出新增一次性确认令牌；协议级 elicitation 仍未实现，内嵌媒体确认仍用结构化 isError + 显式参数/工作台兜底。
4. **T3 发送前自动拦截未做**：仍是“Agent 主动调用工具”模式；DSH 0.2.1-rc.2 无通用 beforeSend hook，评估见 [t3-pre-send-hook-eval.md](../impl/t3-pre-send-hook-eval.md)。
5. **规则缺口已修复**：新增 `cn.idcard_shape` 兜底校验位异常号（pending 复核）；
   详见 `docs/qa/idcard-checksum-policy-20261023.md`。loose 档位会丢弃 pending
   （默认 standard 覆盖），作为已知限制。
6. **auto 探活门控**：仅当 `data/runtime.json` 存在才探 Web，避免测试误连开发机；
   正常安装/启动服务满足。强制 `YINZHAN_MCP_BACKEND=http` 时无服务会明确报错。
7. **深度细修**（框选补漏/整图涂黑/证件档位/时间模糊/未成年人严格模式）仍需回浏览器
   工作台；面板提供入口/深链，未把全套工作台搬进 DSH。

## 5. 安全与边界

- 仅 127.0.0.1 / stdio；无新增外呼；模型收到的是摘要/可外发路径，原文只在本机工作台/显式
  rehydrate 返回。
- 实体清单默认不回原值；`rehydrate` 是本机 vault 直读，要求 MCP 与 Web 使用同一
  `YINZHAN_DATA_DIR`（正常安装/源码满足）。
- human 导出、内嵌媒体、未成年人严格模式仍需人确认；MCP 不代点。
- 审计链与工作台同源；verify 失败会删成品并返回 isError。
