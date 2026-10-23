# T3「发送前自动拦截」评估（v1.2.0）

> 结论：**不在 v1.2.0 实现自动 hook**；当前 MCP 方案仍是“Agent 主动调用工具”模式。
> 原因是 DSH 0.2.1-rc.2 的客户端插件契约没有暴露通用的“提交前改写/扫描附件”钩子；
> 在 MCP 层也无法在模型看到附件前拦截。

## 1. DSH 现有可挂载点

- `conversation.composer`：可插入输入框附近的 UI（按钮/提示/草稿栏），但不会自动改写提交内容。
- `conversation.composer.dock`：输入区 dock，可放“先脱敏”入口。
- 命令执行层：`@deepseek-ai/dsh-commands#commands/execute:submittedAttachments`
  可以把已提交附件交给命令执行器；这是**用户显式执行命令**的路径，不是通用 Agent 发送 hook。
- 消息提交后附件才进入会话内容；MCP 工具调用发生在这之后，无法阻止原文先进入模型上下文。

## 2. 可选方案

| 方案 | 可行性 | 代价/风险 |
|---|---|---|
| A. DSH 上游增加 `beforeSend`/`transformAttachments` hook | 最正确 | 需要 DSH 团队改客户端/协议；我们只能提需求 |
| B. 客户端插件在 composer 加“先脱敏再发送”按钮，用户手动触发 | 现在可做 | 仍是用户显式操作，不是自动拦截；可作为 T3 过渡 UX |
| C. MCP 代理/网关在模型请求前扫描 | 不符合本地隐私边界 | 需要截获请求或改模型链路，v1.2.0 不做 |
| D. 监听粘贴/拖拽 DOM 自动扫描 | 脆弱 | 不同 DSH 版本 DOM 变化大；无法覆盖所有上传路径 |

## 3. 建议

1. 先把 T3 作为 **DSH 上游需求**：在 composer 提交事务里提供可插拔的
   `transformAttachments` / `beforeSend` 本地 hook，失败时阻止发送并提示。
2. 在 hook 落地前，只做 **B**：composer dock 增加“先脱敏再发送”，明确提示用户；
   不把它伪装成自动拦截。
3. 当前 v1.2.0 保持既有能力：`sanitize_file`/`sanitize_document` 工具 + 工作台 +
   工具卡/浏览器 tab；安全叙事不承诺“发送前自动拦截”。

## 4. 验收边界

- 不新增外呼、不在 MCP 层解析/上传原文。
- 若后续实现 B，必须复用同一 JobManager/规则/导出管线和审计，不另造一套脱敏逻辑。
