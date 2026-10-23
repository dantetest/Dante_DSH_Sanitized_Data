# 数据隐站 Sanitized_Data v1.2.0 发布说明

> 2026-10-23。本版聚焦 **MCP L2 产品体验**（DSH 工具卡 + 真实工作台 browser tab + 同 Job 深链）
> 与 **身份证校验位异常兜底**；同时把 MCP 入口 `Sanitized_Data_MCP.exe` 与 DSH 插件随安装包/源码交付。
> v1.1.3 的 AI 换名版、日志/状态、YuNet 侧脸能力全部保留。

## 一、MCP L2 产品体验（DSH）

### 1. 工具卡（T1）

- DSH 内调用 `mcp__sanitized-data__sanitize_file` / `sanitize_document` 等 15 个工具时，
  显示数据隐站定制卡：**已脱敏徽标、已隐去 N 处、待确认数、自检结果、job、来源 MCP**。
- 展开可查看可外发路径 / 文件、`verify.verified/residual`、job_id；提供
  **在检测面板复核 / 打开完整工作台 / 导出涂黑版**。
- 卡片按运行中/已脱敏/失败三态显示；不认识的工具或错误自动回退通用卡。

### 2. 真实工作台（T2，DSH 原生 browser tab）

- 左下角新增「数据隐站工作台」入口，**直接调用 DSH 原生 browser tab 打开真实 WebUI**：
  `http://127.0.0.1:<runtime端口>/console?job=<当前job>`；
- **不再注册/显示仿制「脱敏检测」面板**（早期版本的概览卡片已移除），避免界面与工作台行为漂移；
- 工具卡「打开工作台」同样打开真实工作台；原生 browser tab 不可用时回退外部浏览器，不回退仿制面板；
- 工作台未启动/不可达时，宿主插件会尝试用白名单本地路径自动拉起（源码 `pythonw app/server/run.py --no-browser` 或安装版 `Sanitized_Data.exe`）；失败再给出启动引导（桌面「数据隐站工作台」快捷方式 / `启动.bat`）。
- `install.ps1` 现在同时自动写入 desktop/web profile 的 `mcp-sanitized-data` client 配置；`-SkipMcp` 可跳过。

### 3. 同 Job 深链

- `open_sanitize_workbench` 返回 `http://127.0.0.1:<runtime端口>/console?job=<id>`；
  Web 工作台新增 `?job=` 只读恢复，打开的是**同一个** MCP 创建的 job（不是复制/重建）。
- MCP server 优先通过 Web API 共享 JobManager（`YINZHAN_MCP_BACKEND=auto|local|http`），
  Web 不可用才回退本地（返回 `workbench_available=false` + 启动引导）。

### 4. DSH 插件交付

- 新目录 `integrations/dsh-plugin-sanitize-detect/`：host 本地代理 + client 工具卡/真实工作台入口；
- `install.ps1` 一键安装到指定 DSH profile（默认 `desktop`），卸载用 `-Uninstall`；
- Windows 安装包增加 `{app}\dsh-plugin\`（含插件与安装脚本），源码包同步包含。

> DSH 0.2.1-rc.2 联调：真实对话调用 MCP 工具、创建 `j_*`、面板/审计/深链均通过；
> T1/T2 为本地客户端插件，不改变 MCP 协议与其他客户端兼容性；T2 用 DSH 原生 browser tab 复用真实工作台，不复制 UI。

### 5. human 导出确认令牌（M4 兜底）

- `export_sanitize_job(mode=human)` 第一次调用返回 `confirmation_required` + 一次性 `confirm_token`（5 分钟、绑定 job）；
- 用户确认后把 token 原样带回才执行不可逆涂黑导出；令牌单次消费，避免模型自行连调绕过；
- 这不是协议级 elicitation；支持 elicitation 的客户端仍可直接走标准交互（后续补）。

## 二、身份证校验位异常兜底（QA）

- 新增 `cn.idcard_shape` 规则：18 位身份证**形状**匹配但 GB11643 校验位/日期/省份码异常时，
  仍识别为 ID_CARD（hidden pending），human 导出涂黑、AI 导出换合法假号；
- 原 `cn.idcard` 校验通过规则不变（高置信 accepted）；用 `unless_validate` 互斥，合法号不会被降级；
- 背景：`redact_text` 此前会漏掉校验位错误的真实形似号（OCR 误识/录错/伪造），
  隐私优先策略改为“校验位只作置信度信号，形状仍遮，pending 人工复核”；
- 量化：5 个既有样例 + 41 条夹具 0 新增误报；随机 18 位误报约 0.10%；
- 详见 `docs/qa/idcard-checksum-policy-20261023.md`。

## 三、验证

- `test_mcp_v2.py` 36/36；`test_dsh_plugin.py` 6/6；`test_pipl_rules.py` 19/19；
  `test_visual.py` 68/68；`test_ai_export_naming.py` 29/29；
- `regression.py` 硬检查全绿；`run_all.py` 全绿；`e2e.py` 5/5；
- DSH 实测：工具卡/真实工作台实体/面板审计/导出/同 Job 深链截图见
  `docs/qa/mcp-l2-dsh-acceptance-20261023.md`；
- PyInstaller 双入口：`Sanitized_Data.exe`（GUI）+ `Sanitized_Data_MCP.exe`（stdio）均构建验证。

## 四、已知限制

1. `redact_text`/规则仍可能漏 OCR 字符级污染、截断、插空格的号码；loose 档位会丢弃
   shape 兜底的 pending（默认 standard 覆盖；需要 loose 全遮需另行调整档位）。
2. 18 位 Luhn 合法的形状值会标为 ID_CARD 而非 BANK（已遮挡，只是类型差异）。
3. T1 定制卡在 DSH 默认 transcript 折叠完成过程时可能需展开「已完成」查看；
   需要 DSH 客户端插件仍以本插件为准，升级 DSH 后需做宿主契约回归。
4. MCP exe 与插件依赖本机 Web 工作台时，须先启动数据隐站；否则 MCP 回退本地、
   面板/深链不可用（会明确提示）。
5. Windows 安装包未代码签名；macOS v1.2.0 未重建，继续 v1.1.2 DMG。

## 五、产物与校验

> 构建完成（2026-10-23）；尺寸/SHA-256 见下表。

| 文件 | 大小 | SHA-256 |
|---|---|---|
| `Sanitized_Data_Setup_1.2.0_x64.exe`（安装包） | 187,709,481 B（179.0 MiB） | `94F3F83D935987090C93EF35964A312F18FF8927CA7D12F42C518908C7305481` |
| `Sanitized_Data-1.2.0-win64-portable.zip`（绿色版） | 211,184,187 B（201.4 MiB） | `A53BD79DE306E5A3BC985ACF3FA14EB23748C1BE3BE83CD9ACC08ECEB9E7813D` |

安装包/绿色版含 `Sanitized_Data_MCP.exe` 与 `dsh-plugin/`（DSH 工具卡/真实工作台）。
macOS v1.2.0 未重建，继续使用 v1.1.2 DMG。

