# MCP L2 与 DSH 联调验收（2026-10-23）

> 范围：MCP L2 server 侧（M2–M4）+ DSH T0 配置级联调；T1 工具卡 / T2 右侧面板插件未实现。
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
| T1 对话内定制工具卡（verify 徽标/缩略图/按钮） | ❌ | DSH 当前是通用工具卡；按钮/徽标需客户端插件 |
| T2 右侧「脱敏检测」面板 | ❌ | 未实现 host/client 插件 |
| M4 elicitation / confirm_token | ❌ | 当前用结构化 isError + 显式参数/工作台确认兜底 |
| 安装包内 `Sanitized_Data_MCP.exe` | 🟡 | `app.spec` 已加双入口，下次 Windows 构建验证安装包；**Gitea v1.1.3 发布包不含 MCP**（MCP 在发布后合入源码） |

**一句话**：MCP L2 的**服务端闭环已通**，DSH 能真实“调用工具 → 建 Job → 返回计数/审计可用 → 深链打开同一工作台”；但**没有做 DSH 客户端插件**，所以计划中的“定制工具卡 + 右侧面板 + 发送前拦截”只完成 T0，视觉/交互未达 T1/T2 原型。

## 2. DSH 实测场景

### 2.1 文本脱敏工具（旧工具路径）

用户提示：让 DSH 调 `mcp__sanitized-data__redact_text` 脱敏一段文本。

- DSH 工具卡出现 `mcp__sanitized-data__redact_text · 张伟，手机号...`；
- 工具返回 `redacted: 张伟，手机号16834998731...`、`entities:1`；
- **发现规则缺口**：同一段里的身份证号 `110101199001011234` 未识别
  （by_type 只有手机号）。这是检测规则/校验问题，不是 MCP 链路问题；已列入 §5。
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

## 3. 门禁结果

> 本轮最终数字见 §3.1（全量 run_all/regression 完成后回填）。

| 测试 | 结果 |
|---|---|
| `test_mcp_v2.py` | **36/36 通过**（协议/旧工具/11 新工具/resources/prompts/Job/HTTP mock/失败语义） |
| `test_ai_export_naming.py` | **29/29 通过**（MCP 段改为 MANAGER 带 seal 命名，成品落 data/out） |
| `test_visual.py` | **68/68 通过** |
| `regression.py` 的 MCP 段 | **8/8 通过**（stdio 端到端/旧 4 工具/residual=0/映射） |
| `run_all.py --only mcpv2` | **1/1 通过** |

### 3.1 最终全量门禁

- `app/tests/run_all.py`：**30/30 套件通过**（281.11s；新增 `mcpv2` 套件）。
- `app/tests/regression.py`：**249 项硬检查全绿**（2 条软提示，100.8s）。
- `app/tests/e2e.py`：**5/5**、残留 0（`run_all` 内含）。
- `test_mcp_v2.py`：36/36；`test_ai_export_naming.py`：29/29；`test_visual.py`：68/68。
- 安装形态：PyInstaller 双入口构建成功，`dist/Sanitized_Data/Sanitized_Data_MCP.exe`
  可跑 stdio（stdout 仅 JSON-RPC；已禁用 OpenCL 编译日志污染）；GUI 主入口
  `Sanitized_Data.exe` 不变；MCP exe 无 Web 服务时回退 local（`workbench_available=false`
  + 启动引导）。**v1.1.3 发布包不含 MCP exe**（MCP 在发布后合入），下次构建会带。

## 4. 相对设计目标的差距（诚实评估）

1. **T1/T2 未实现**：计划的核心“检测卡片/右侧面板”是 DSH 客户端插件（5–7 人日），
   本轮只做了 T0 配置 + server 工具能力。当前 DSH 里能看到工具名/参数/JSON 结果，
   但没有“自检通过徽标、缩略图并排、打开工作台/导出按钮”的定制卡，也没有右侧面板。
2. **MCP 结果图片**：`sanitize_document`/render 已支持 `image/png` content block；
   `sanitize_file` 按计划只返回任务摘要。DSH 工具结果图片能否渲染需 T1 卡片配合展示。
3. **elicitation / confirm_token 未做**：human 导出/内嵌媒体确认通过结构化 `isError`
   + 显式参数/工作台兜底；对不支持 elicitation 的客户端安全，但交互不如设计顺滑。
4. **MCP exe 未进 v1.1.3 安装包**：v1.1.3 已发布；`app.spec` 双入口在发布后加入，
   需要下一次构建/发版验证（本次只验证 PyInstaller 源码双入口构建与 stdio）。
5. **规则缺口**：`redact_text` 未识别示例身份证号（校验位/检测器问题）；
   这是产品检测能力，不是 MCP 协议问题，建议转 QA/rules 评估。
6. **auto 探活门控**：仅当 `data/runtime.json` 存在才探 Web，避免测试误连开发机；
   正常安装/启动服务满足。强制 `YINZHAN_MCP_BACKEND=http` 时无服务会明确报错。
7. **深度细修**（框选补漏/整图涂黑/证件档位/时间模糊/未成年人严格模式）仍需回浏览器
   工作台；MCP 只提供复核工具，未把全套 UI 搬进对话/面板。

## 5. 安全与边界

- 仅 127.0.0.1 / stdio；无新增外呼；模型收到的是摘要/可外发路径，原文只在本机工作台/显式
  rehydrate 返回。
- 实体清单默认不回原值；`rehydrate` 是本机 vault 直读，要求 MCP 与 Web 使用同一
  `YINZHAN_DATA_DIR`（正常安装/源码满足）。
- human 导出、内嵌媒体、未成年人严格模式仍需人确认；MCP 不代点。
- 审计链与工作台同源；verify 失败会删成品并返回 isError。
