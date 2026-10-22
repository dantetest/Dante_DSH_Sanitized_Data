# MCP L2 · Agent 内脱敏检测（实现说明）

> 状态：**M2–M4 server 侧 + DSH T0 配置级已实现**（2026-10-23）；T1 工具卡 / T2 右侧面板插件尚未实现。
> 计划基线：[04-MCP-L2-Agent内检测-规划](../plans/04-MCP-L2-Agent内检测-规划.md)
> 验收记录：[mcp-l2-dsh-acceptance-20261023](../qa/mcp-l2-dsh-acceptance-20261023.md)

## 1. 交付形态

| 形态 | 说明 |
|---|---|
| `app/server/mcp_server.py` | MCP 2024-11-05 / 2025-06-18 stdio 服务，15 个工具、4 类资源模板、2 个 prompt |
| `Sanitized_Data_MCP.exe` | PyInstaller 双入口之一（console 子系统，stdout 只走 JSON-RPC），随 Windows 安装包/绿色版分发 |
| 源码运行 | `python -X utf8 app/server/mcp_server.py`，适合开发和 DSH `--patch` 联调 |

## 2. 工具

**保留（冻结）**：`sanitize_document`、`redact_text`、`check_support`、`list_rules`。

- `sanitize_document` 成功字段保持旧 12 个（ok/output/mode/pack/entities/by_type/verify/
  mapping_id/warnings），只新增 `job_id`/`origin`/`workbench_url`；可渲染格式附一张
  `image/png` 出稿预览。
- 内部走 JobManager：创建 `origin=mcp` 任务 → 等 ready → human/ai 导出；审计、映射、
  失败删除成品、`data/out` 输出与工作台同源。

**新增 11 个 `sanitize_*` 工具**：`sanitize_file`、`get_sanitize_job`、`list_sanitize_jobs`、
`get_sanitize_entities`、`update_sanitize_entity`、`mark_sanitize_region`、
`accept_all_sanitize_entities`、`export_sanitize_job`、`get_sanitize_audit`、
`rehydrate_sanitize_text`、`open_sanitize_workbench`。

- 实体清单默认**不回原值**（只回 id/label/etype/status/value_len/spans/level）；
  `update`/`replace` 使用显式 replacement；`rehydrate` 仅本机显式调用。
- `export_sanitize_job` 对等 `POST /api/jobs/{id}/export`；`get_sanitize_audit` 支持
  `job_id` 过滤 + `audit.verify_chain()`。
- `open_sanitize_workbench` 返回 `http://127.0.0.1:<runtime端口>/console?job=<id>`。

## 3. 资源与 prompts

| URI 模板 | 内容 |
|---|---|
| `sanitize://jobs` | 最近任务列表（JSON，含 origin/stage/counts/workbench_url） |
| `sanitize://jobs/{id}` | 任务快照 |
| `sanitize://jobs/{id}/entities` | 实体清单（脱敏视图） |
| `sanitize://jobs/{id}/audit` | 审计记录 + 链校验 |
| `sanitize://jobs/{id}/pages/{n}?mode=orig\|out` | 页面 PNG（blob，统一转 PNG，≤2MB） |

prompts：`review-before-export`、`workbench-fallback`（引导先复核待确认再导出/复杂细修回工作台）。

## 4. 后端模式（关键）

MCP 是独立 stdio 进程，**必须和 Web 工作台共享 Job**：

- 优先探测 `data/runtime.json` 的实际端口（默认 8760）`GET /api/health`；健康时所有
  任务/实体/导出/审计/页面渲染走 Web API，**深链打开的是同一个 j_* job**。
- Web 服务不可用时回退本地 JobManager（兼容 Claude Desktop/Cursor 无 GUI 场景），
  此时不返回可用的 workbench_url，并在结果里提示启动数据隐站。
- 开关：`YINZHAN_MCP_BACKEND=auto`（默认）| `local` | `http`。`auto` 仅在
  `data/runtime.json` 存在时探活（避免测试/CI 误连开发机 8760）；强制 `http` 时探活失败
  返回结构化 `isError code=no_workbench`，不会建本地 job 再给 Web 深链。
- Web API 创建任务支持 `origin:"mcp"`，任务列表显示 MCP 徽标；`origin` 默认 api。

## 5. DSH 集成（T0，已实机验证）

DSH 0.2.1-rc.2 用 `@deepseek-ai/dsh-mcp-client`；在 profile patch 中用 **`insert`** 追加
（注意：不是直接写顶层 `- id` 条目，顶层条目只能打补丁已有 id）：

```yaml
- insert:
    - id: mcp-sanitized-data
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        transport: stdio
        serverName: sanitized-data
        command: "C:\\Program Files\\Python312\\python.exe"
        args:
          - "-X"
          - "utf8"
          - "D:\\DSH_DESKTOP_WORKSPACE\\Sanitized_Data\\app\\server\\mcp_server.py"
        cwd: "D:\\DSH_DESKTOP_WORKSPACE\\Sanitized_Data"
        toolCallTimeoutMs: 600000
        reconnect:
          enabled: true
          maxAttempts: 10
```

安装版把 command 换成 `%LOCALAPPDATA%\\Programs\\Sanitized_Data\\Sanitized_Data_MCP.exe`、
args/cwd 留空即可。DSH 侧工具名为 `mcp__sanitized-data__<tool>`。

> 说明：本机验证时用 `dsh --profile web --patch <patch.yml>` 启动独立 Web 实例，
> 不改用户默认 profile；正式安装建议把 patch 写入 `~/.dsh/profiles/<profile>/cordis.patch.yml`。

## 6. 安全边界

- 只监听 127.0.0.1 / stdio；不新增外呼、不联网下载模型。
- 返回给 Agent 的是摘要/脱敏视图/可外发路径；原文仅在用户显式 rehydrate 时本机返回。
- human 导出、内嵌媒体确认、未成年人严格模式仍需人确认；MCP 不代点。
- 审计只记类型/数量/哈希；成品失败删除与工作台一致。

## 7. 验证

```powershell
python -X utf8 app/tests/test_mcp_v2.py        # MCP 协议/工具/资源/Job/HTTP 后端
python -X utf8 app/tests/test_ai_export_naming.py
python -X utf8 app/tests/regression.py         # 含 MCP 段
python -X utf8 app/tests/run_all.py            # 全套
```

本机实测（见 QA 报告）：DSH 对话实际调用 `mcp__sanitized-data__redact_text` 与
`sanitize_file/get_sanitize_job/open_sanitize_workbench`，创建 `j_*` 任务、6 个实体、
stage=ready，并返回深链。

## 8. 未完成 / 后续（对照计划）

| 计划项 | 状态 |
|---|---|
| M2 Job 化 / M3 资源+复核工具 / M4 双协议 | ✅ server 侧已实现 |
| M4 elicitation + confirm_token | ⏳ 未实现；当前结构化 isError + 工作台/显式参数兜底 |
| M1 独立 MCP EXE + 安装包 | 🟡 `app.spec` 已加双入口，需随下次 Windows 构建验证安装包 |
| T1 对话内工具卡（verify 徽标/缩略图/按钮） | ⏳ 依赖 DSH 客户端插件，未实现 |
| T2 右侧「脱敏检测」面板 | ⏳ 依赖 host/client 插件，未实现 |
| T3 发送前自动拦截 hook | ⏳ 未实现 |
