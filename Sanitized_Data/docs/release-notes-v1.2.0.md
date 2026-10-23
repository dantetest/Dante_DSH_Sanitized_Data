# 数据隐站 Sanitized_Data v1.2.0 发布说明

> 2026-10-23。本版聚焦 **MCP L2 产品体验**（DSH 工具卡 + 右侧「脱敏检测」面板 + 同 Job 深链）
> 与 **身份证校验位异常兜底**；同时把 MCP 入口 `Sanitized_Data_MCP.exe` 与 DSH 插件随安装包/源码交付。
> v1.1.3 的 AI 换名版、日志/状态、YuNet 侧脸能力全部保留。

## 一、MCP L2 产品体验（DSH）

### 1. 工具卡（T1）

- DSH 内调用 `mcp__sanitized-data__sanitize_file` / `sanitize_document` 等 15 个工具时，
  显示数据隐站定制卡：**已脱敏徽标、已隐去 N 处、待确认数、自检结果、job、来源 MCP**。
- 展开可查看可外发路径 / 文件、`verify.verified/residual`、job_id；提供
  **在检测面板复核 / 打开完整工作台 / 导出涂黑版**。
- 卡片按运行中/已脱敏/失败三态显示；不认识的工具或错误自动回退通用卡。

### 2. 右侧「脱敏检测」面板（T2）

- DSH 右侧栏新增常驻入口「脱敏检测」，面板支持：
  - **概览**：文件/格式/来源 MCP/状态；已隐去、待确认、自检指标；类型分布；可外发版预览；
    「全部接受 / 导出换名版 / 导出涂黑版」；
  - **实体**：逐条 label/status/值，本地 accept/reject/replace；
  - **审计**：HMAC 链、最近记录、模式/实体数/seal；
  - 底部「打开完整工作台 ↗」深链。
- 面板经宿主插件本地代理访问 `127.0.0.1` 工作台（同源、无 CORS），**不新增外呼**；
  工作台未运行给出启动引导。

### 3. 同 Job 深链

- `open_sanitize_workbench` 返回 `http://127.0.0.1:<runtime端口>/console?job=<id>`；
  Web 工作台新增 `?job=` 只读恢复，打开的是**同一个** MCP 创建的 job（不是复制/重建）。
- MCP server 优先通过 Web API 共享 JobManager（`YINZHAN_MCP_BACKEND=auto|local|http`），
  Web 不可用才回退本地（返回 `workbench_available=false` + 启动引导）。

### 4. DSH 插件交付

- 新目录 `integrations/dsh-plugin-sanitize-detect/`：host 本地代理 + client 工具卡/面板；
- `install.ps1` 一键安装到指定 DSH profile（默认 `desktop`），卸载用 `-Uninstall`；
- Windows 安装包增加 `{app}\dsh-plugin\`（含插件与安装脚本），源码包同步包含。

> DSH 0.2.1-rc.2 联调：真实对话调用 MCP 工具、创建 `j_*`、面板/审计/深链均通过；
> T1/T2 为本地客户端插件，不改变 MCP 协议与其他客户端兼容性。

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
- DSH 实测：工具卡/面板实体/面板审计/导出/同 Job 深链截图见
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
| `Sanitized_Data_Setup_1.2.0_x64.exe`（安装包） | 187,703,688 B（179.0 MiB） | `53E109FA07EFAEAA872DCAE2144339333A834612E45F824D2C0A01BC38C3DA71` |
| `Sanitized_Data-1.2.0-win64-portable.zip`（绿色版） | 211,185,636 B（201.4 MiB） | `A3704EE0B85519D783E38C18C3DD0E8F7AC3289A694CD286D122BE01A33F940C` |

安装包/绿色版含 `Sanitized_Data_MCP.exe` 与 `dsh-plugin/`（DSH 工具卡/面板插件）。
macOS v1.2.0 未重建，继续使用 v1.1.2 DMG。

