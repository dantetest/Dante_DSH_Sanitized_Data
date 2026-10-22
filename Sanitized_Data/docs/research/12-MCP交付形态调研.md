# 12 · MCP 交付形态调研：Agent 里能不能看到/操作工作台脱敏

> **状态**：专项调研报告（交付状态 + 能力边界） · 2026-10-21
> **基线**：v1.1.2，git HEAD `c36ec36`（工作区干净） · 产品目录 `Sanitized_Data/`
> **方法**：Lead 独立静态审计 + 真实 stdio MCP 探针（隔离数据目录/cwd，JSON-RPC 全链路）+ 真实审计库查询 + 已发布产物条目录检查；两名并行审计员交叉核对（`out/_mcp_audit/delivery.md`、`out/_mcp_audit/surface.md`），冲突处以探针为准。
> **证据标注**：`[码]`=代码位置；`[测]`=本次实测；`[物]`=已发布产物内容；`[文]`=仓库文档/页面文案。

---

## 0 结论摘要（先给答案）

### 0.1 三句话

1. **不能。当前交付的 MCP 是「无头工具 API」，不是工作台。** 服务端 `initialize` 只声明 `tools` 能力，全部能力是 4 个工具（`sanitize_document` / `redact_text` / `check_support` / `list_rules`）`[码]` `mcp_server.py:303`、`:38-90`。用户在 Agent 里只能看到工具调用和一段 JSON 文本（输出路径 / 命中计数 / 自检摘要 / mapping_id / ≤5 条 warning），看不到也不能操作预览、左右对照、实体清单、框选补漏、整图涂黑、待确认 accept/reject、导出档位、下载、审计、还原 `[测]`。
2. **交付断点比「有没有工作台」更基础：Windows 安装包 / 绿色版里根本没有 MCP 服务端。** `app.spec` 只打包 `app/web` 与 `yinzhan/rules`，`installer.iss` 只复制 `dist\*`，绿色版 1234 个条目里与 mcp 相关的只有说明页 `web/mcp.html` `[物]` `[码]` `app.spec:40-45`、`installer.iss:50`。`/mcp` 页给的配置是 `command: python` + `D:\Sanitized_Data\app\server\mcp_server.py`，对安装版用户路径与解释器都不存在 `[码]` `mcp.html:100-137`。**普通安装包用户按页面配置必然接不上；MCP 实际只在「源码运行 + Python 3.10+ + pip 依赖」形态成立。**
3. **MCP 自动脱敏通道本身可用，但缺少工作台的「人在环」与追溯：** 实测 txt / png / docx / pdf 均能脱敏、`residual=0`、换名映射以 `mcp_*` 加密落盘、CLI 可还原 `[测]`；但 MCP 不建 Job、不写审计（本次探针数据目录连审计库都没创建；真实运行库 58 条审计全部是 `j_` 前缀、`mcp_` 为 0）`[测]`，与 `/mcp` 页「每次脱敏写一条签名审计记录」不符 `[文]` `mcp.html:192-196`；且无人工复核时 pending 视觉项会被自动涂黑、异常码类候选的告警传不到 Agent `[码]` `jobs.py:778-806`、`:1108-1128`；`mcp_server.py:206`。

### 0.2 交付状态表

| 项 | 状态 | 证据 |
|---|---|---|
| MCP 服务端代码 | **已交付（仅源码）** | 仓库 `app/server/mcp_server.py`；Release v1.1.2 源码包内 255 条目含该文件 `[物]` |
| Windows 安装包 / 绿色版 | **未包含 MCP** | `app.spec:40-45`、`installer.iss:50`、绿色版 zip 条目 `[物]` |
| 站点 `/mcp` 接入页 | **已上线，但只对源码布局成立** | `mcp.html:47` CTA「先下载安装包」、`:100-137` 配置 `[文]` |
| MCP 暴露工作台 UI / 复核操作 | **未交付（0 个工具/资源）** | `mcp_server.py:276-323` `[码]` |
| MCP 审计 | **未交付（实测不写）** | `jobs.py:686` 为全仓唯一 `audit.append`；探针 + 真实库 `[测]` |
| 换名还原 | **部分：仅 CLI，且安装包不含 rehydrate.py** | `mcp_server.py:181-195`、`rehydrate.py`；`dist/` 无该脚本 `[物]` |
| 「MCP = 专业版能力」 | **未实现（仅设计规划，无 licensing 代码）** | `docs/impl/free-tier.md:3`、`licensing.md:3`；`app/server` 无 `licensing/quota/tiers` `[码]` |

### 0.3 最小修复清单（若近期要发版）

| P | 事项 | 一句话 |
|---|---|---|
| P0 | 打包 MCP | 把 `mcp_server.py` 打进安装包，或给 `Sanitized_Data.exe` 加 `--mcp` stdio 模式（现有 GUI 是 `console=False`，直接拿它当 stdio 服务不可行，需要独立入口或独立分支） |
| P0 | 区分文档口径 | `/mcp`、README、support-matrix 明确「安装版 / 源码版」；`support-matrix.md:112` 的「支持」必须加限定 |
| P0 | 审计补写或改文案 | MCP 路径补 `audit.append`（job_id 用 `mcp_*`），或删掉 `mcp.html:195` 的「每次脱敏写一条签名审计记录」 |
| P0 | 失败不静默 | MCP 自检失败（`mcp_server.py:171-179`）应像工作台一样判定失败并删除成品，不能仍回 `ok=True` 留成品 |
| P1 | 输出/还原可达 | 输出固定到数据目录（不要 `Path.cwd()/sanitized_output`），安装包内置 `rehydrate` 入口 |

---

## 1 实测方法

- 探针脚本：`out/_mcp_audit/probe/probe.py`、`probe2.py`（从仓库源码以 `python -X utf8 app/server/mcp_server.py` 启动 stdio 服务；隔离 `YINZHAN_DATA_DIR` 与 cwd，避免污染产品数据）。
- 交互：`initialize` → `notifications/initialized` → `tools/list` → 4 个工具逐个 `tools/call`；再对真实样例 `samples/名片.png`、`住院病历.docx`、`委托代理合同.pdf` 各跑一次 `sanitize_document(mode=ai)`。
- 旁证：查探针数据目录与真实 `data/yinzhan.db` 的审计行；查 `vault` 映射并跑 `rehydrate.py --list`；读取发布产物 zip 条目与安装脚本。

---

## 2 实测：Agent 侧到底看到什么

### 2.1 协议面只有 tools

```json
{"capabilities": {"tools": {"listChanged": false}},
 "serverInfo": {"name": "sanitized-data", "version": "1.1.2"},
 "protocolVersion": "2024-11-05"}
```
`[测]`；`results/rows` 只实现 `initialize / notifications/initialized / ping / tools/list / tools/call`，`resources/*`、`prompts/*`、`logging/*`、`sampling/*`、`elicitation/*` 一律 `-32601 未实现` `[码]` `mcp_server.py:294-323`。

### 2.2 4 个工具

| 工具 | 参数 | 返回（纯文本 JSON） |
|---|---|---|
| `sanitize_document` | `path`、`mode: ai\|human`、`strictness`、`allow_unprocessed_media` | `ok`、`output`（本机绝对路径）、`mode`、`pack`、`entities` 总数、`by_type` 计数、`verify{verified,residual}`、`mapping_id`、`warnings ≤5` |
| `redact_text` | `text`、`strictness` | `redacted`、`entities`、`by_type`、`pack` |
| `check_support` | `path` | `{supported,path,format}` |
| `list_rules` | — | `{default_pack,packs:[...]}` |

`[码]` `mcp_server.py:38-90`、`:95-98`、`:197-207`。返回通道只有 `{"type":"text"}`，**没有 `image` 内容块、没有 resource link** `[测]` `[码]`。

实测一条真实返回（txt 样例，节选）：

```json
{"ok": true,
 "output": "...\\cwd\\sanitized_output\\probe_sample_换名.txt",
 "mode": "ai",
 "pack": "common,finance-cn,geo-cn,legal-cn,medical-cn,pipl-cn,table-cn,trade-cn",
 "entities": 8,
 "by_type": {"手机号":2,"电子邮箱":1,"案号":1,"当事人/代理人":1,"人名":1,"身份证号":1,"银行账号":1},
 "verify": {"verified": true, "residual": 0},
 "mapping_id": "mcp_20261021193828264770",
 "warnings": []}
```
`[测]` `out/_mcp_audit/probe/transcript.json`。真实文件同样只有这段摘要（`名片.png` 4.14s / 6 处；`住院病历.docx` 0.14s / 9 处 + 4 条清理 warning；`委托代理合同.pdf` 0.38s / 15 处）`[测]` `transcript2.json`。

### 2.3 用户/Agent 看不到的（逐项）

| 缺失 | 说明 |
|---|---|
| 预览图 / 左右对照 | 无 image content、无 preview/render 调用；工作台走 `/api/jobs/{id}/preview` 与 `/render` `[码]` `api.py:186-189`、`:388-396` |
| 实体清单 | 只回总数 + `by_type`，无 id/位置/原值→假值/status `[码]` `mcp_server.py:202-203` |
| 待确认 | 返回体无 pending 字段；pending 视觉项混在 `by_type` 里 `[码]` `:165-169` |
| 框选补漏 / 整图涂黑 | 无 mark 工具，只有自动检测 `[码]` `:121`、`:136-141` |
| 逐条确认 / accept-all | 无 accept/update 工具，决策一次定死 `[码]` `:276-281`、`:124` |
| 导出档位 | 仅 `mode/strictness/allow_unprocessed_media`；工作台的证件档位、时间模糊、未成年人严格模式、规则包、PDF 隐藏数据、Office 媒体选项均无入口 `[码]` `:44-60` vs `api.py:138-230` |
| 下载 | 只回本机路径字符串，无文件内容/下载 URL `[码]` `:199` |
| 工作台 URL / job_id | 全文件无 URL 拼装、不建 Job `[码]` `:105-163` |
| 审计 / 还原 | 无 audit 引用；只给 `mapping_id`，还原仍靠 CLI `[码]` `:205` |

### 2.4 副作用：不建 Job、不写审计、输出跟着 cwd 走

| 维度 | 工作台（HTTP + Job） | MCP（stdio） |
|---|---|---|
| Job | `JobManager.create()` 建 `j_*`，可查快照/进度/取消 | **不建 Job**，一次函数内 parse→export `[码]` `jobs.py:220`、`:253-397` vs `mcp_server.py:105-163` |
| 审计 | 导出成功写 HMAC 审计链 | **不写**：全仓唯一 `audit.append` 在 `jobs.py:686`；实测探针数据目录未生成 `yinzhan.db`，真实库 58 条全部 `j_*`、`mcp_` 为 0 `[测]` |
| 映射 | `vault.save_mapping(job.id, ...)` | `vault.save_mapping("mcp_"+时间戳, ...)`，仅 `ai` 且 `verified` 才存 `[码]` `mcp_server.py:181-195`；实测 `vault/mcp_*.bin` + `rehydrate --list` 可见 `[测]` |
| 输出目录 | 数据目录 `out/`，带封条号与去重 | **`Path.cwd()/sanitized_output/`**，跟随 Agent 客户端进程 cwd；同名同模式重跑会覆盖 `[码]` `mcp_server.py:159-162` |
| 自检失败 | 409 + 删除成品 `[码]` `jobs.py:740-748` | 捕获异常后仍 `ok=True`、成品保留、映射不存 `[码]` `mcp_server.py:171-179` |

---

## 3 能力矩阵：工作台 vs MCP

图例：**无** / **部分** / **有**。

| # | 工作台能力 | 工作台证据 | MCP | MCP 差距 |
|---|---|---|---|---|
| 1 | 拖拽/上传建任务 | `app.js:535-634`、`api.py:123-165` | 无 | 只收本机绝对路径，无 Job |
| 2 | 进度 / 取消 | `api.py:168-175`、`:207` | 无 | 同步阻塞，无进度通知/取消 |
| 3 | 左右对照预览 | `app.js:926-1124`、`api.py:186-189` | 无 | 无图、无坐标 |
| 4 | 实体清单 | `app.js:1696-1745`、`api.py:178-183` | 无（只有聚合计数） | 无逐条明细/pending |
| 5 | 逐条确认 / accept-all | `app.js:1751-1782`、`api.py:197-204` | 无 | 决策定死 |
| 6 | reject / 撤销 | `api.py:197-199`、`jobs.py:532-534` | 无 | 误报无法排除 |
| 7 | 框选补漏 | `app.js:1398-1568`、`api.py:192-194` | 无 | — |
| 8 | 整图涂黑 | `app.js:1570-1668` | 无 | — |
| 9 | 导出选项 | `api.py:138-165`、`:219-230` | 部分 | 仅 3 个开关，其余固定默认 |
| 10 | human 二次确认 | `app.js:1914-1928` | 无 | Agent 传 `mode=human` 即执行 |
| 11 | 下载成品 | `app.js:2007-2012`、`api.py:398-413` | 部分 | 只回路径 |
| 12 | 还原映射 | `rehydrate.py`（控制台无 UI） | 部分 | 回 `mapping_id`，需 CLI |
| 13 | 审计 | API（`api.py:233-241`，控制台无 UI） | 无 | 不写、不查 |
| 14 | 自检阻断 | `jobs.py:740-748` | 部分/退化 | 失败仍 `ok=True` |

**结论一：Agent 里的交互面 = 4 个工具调用；工作台 1-8 项（"人在环"核心）完全没有对应暴露。**

---

## 4 交付断点：普通安装包用户为什么用不上

### 4.1 打包链没有 MCP

- `desktop/app.spec:16` 唯一入口 `desktop/main.py`；`:40-45` `datas` 只有 `app/web` 与 `yinzhan/rules`，`hiddenimports` 只收 `yinzhan`，**未收集 `app/server/mcp_server.py`** `[码]`。
- `desktop/installer.iss:50` 只 `Source: "..\dist\Sanitized_Data\*"`；`:23` 默认装到 `%LOCALAPPDATA%\Programs\Sanitized_Data` `[码]`。
- `[物]` 实测：`dist/Sanitized_Data/` 顶层只有 `_internal/`、`Sanitized_Data.exe`、3 个运行库 DLL；递归搜 `mcp_server*` 为 0；绿色版 zip 1234 个条目中 `mcp` 仅命中 `_internal\web\mcp.html`；包内无 `python*.exe`；`desktop/main.py:172-218` 只有 `--diagnose`，无 MCP/stdio 模式（且 `app.spec:109` `console=False`）。
- 因此 `Sanitized_Data.exe` 不能被 MCP 客户端当 stdio server 启动。

### 4.2 `/mcp` 页配置对安装版不成立

`mcp.html:100-137` 配置为 `"command": "python"`、`"args": ["D:\\Sanitized_Data\\app\\server\\mcp_server.py"]` `[文]`。安装版实际：程序目录 `%LOCALAPPDATA%\Programs\Sanitized_Data` 无 `app/server`；安装包自述「不需要 Python」（`installer.iss:5`、`install-guide.md:30`）；`mcp_server.py:326-331` 启动即加载插件与 OCR，需 `pip install -r app/server/requirements.txt`（README:42、`install-guide.md:80`）。页面 CTA `mcp.html:47` 还是"先下载安装包 →" `[文]`。

### 4.3 源码包：站点已下架，但 Release 附件与已发安装包内仍在

- 站点 `/download` 已下架源码包（`download.html:38`「源码包已下架；…请联系项目维护者」）`[文]`。
- Gitea Release v1.1.2 附件仍含 `Sanitized_Data-1.1.2-win64.zip`（2,538,460 B）与 `-linux64.tar.gz`（2,381,494 B）`[物]`（`out/_lead/gitea_release_v112.log`、`out/SHA256SUMS-v1.1.2.txt`）。
- 源码包内确有 MCP：`dist/_internal/web/downloads/Sanitized_Data-1.1.2-win64.zip` 255 个条目含 `app/server/mcp_server.py`、`app/web/mcp.html`、`app/server/rehydrate.py` `[物]`。
- **时间线揭示了发布物不一致**：构建 17:39-17:41、安装包 17:41:16、下架提交 `8816c9a` 18:07:13 —— 下架晚于构建且未重建。因此**已发安装包/绿色版内部仍带两个源码包**，且内置 `/download` 页是下架前旧版（直接给 `downloads/Sanitized_Data-1.1.2-win64.zip` 下载链）`[物]`；`api.py:355-377` 的 `_static` 对 `WEB_DIR` 下任意文件返回、无白名单 `[码]`。也就是说：装好的用户能从本机 `/download` 页下到源码包 —— 但拿到源码 ≠ MCP 可用（还要装 Python/依赖、改配置路径），且按 HEAD 重建后这条直链会消失。

### 4.4 文档口径

| 位置 | 说法 | 判定 |
|---|---|---|
| `docs/support-matrix.md:112` | 交付形态表：MCP 接入「支持」 | ❌ 未注明仅源码方式 |
| `README.md:77-102` | MCP 接入章节 | ❌ 未标「仅源码方式」，且 `:61` 说源码包已下架，自相矛盾 |
| `app/web/home.html:120,134-137` | 「Agent 走 MCP」「四条配置，复制即用」 | ❌ 对安装版不成立 |
| `docs/install-guide.md:89-92` | §3.2 MCP 接入（位于"源码运行"章节） | ⚠️ 位置隐含源码方式，但没明说安装包不含 |
| `app/web/mcp.html:44` | chip「无需账号」 | ✅ 与代码一致（无授权门禁） |
| `mcp.html:43,54,172` + README「4 个工具」 | 4 个工具 | ✅ 实测 tools/list 恰好 4 个 |
| `mcp.html:192-196` | 「每次脱敏写一条签名审计记录」 | ❌ 对 MCP 不成立（MCP 0 审计）；仅工作台路径成立 |

---

## 5 无工作台操作的实际风险（按 MCP 当前实现）

1. **pending 视觉项被自动涂黑且不可见/不可排除。** 手写签名、车牌、路牌、门牌、店招、单位铭牌在 `_VISUAL_SPEC` 里 `status="pending"`，但实体创建时 `decision=black` `[码]` `jobs.py:778-806`、`:1137-1143`；MCP 直接把它们并入导出（`mcp_server.py:136-141`、`:163`），`pipeline.export` 只看 decision 不看 pending `[码]` `pipeline.py:489-498`。工作台里用户能看到「待确认」标签、位置并逐条接受/排除；MCP 里用户只能看到标签计数，不知道这些项是待确认、在哪、也无法逐条排除。
2. **异常大面积二维码/条码候选：ai 模式不涂黑、告警不回传。** `decision=None` 且写 `doc.warnings` `[码]` `jobs.py:1108-1128`；导出过滤掉无决策实体 `[码]` `pipeline.py:491`；MCP 只回 `res.warnings[:5]`，而 `ExportResult.warnings` 不合并 `doc.warnings` `[码]` `mcp_server.py:206` → **Agent 收不到"有一处大面积码没打码"的提示**。
3. **human 模式语义偏差：** MCP 在导出前对**所有**实体强制 black（含 `decision=None`）`[码]` `mcp_server.py:154-157`；工作台是先过滤 `decision=None` 再强制 black `[码]` `jobs.py:729-735` → 同一文件在 MCP human 模式下可能多涂一块。
4. **自检失败被吞：** `pipeline.verify` 失败会抛 `VerifyError` `[码]` `pipeline.py:818-820`；MCP 捕获后仍返回 `ok=True` + 成品路径，且 `verified=False` 不存映射 `[码]` `mcp_server.py:171-179`。Agent 可能把一份没通过出口自检、不可还原的成品当成功交付。
5. **确认权在模型手里：** `allow_unprocessed_media` 是工具参数，页面要求"用户明确同意后再传 true" `[文]` `mcp.html` 与工具描述，但无强制机制 —— 模型可自行传 `true` 绕过安全默认 `[码]` `mcp_server.py:143-152`。
6. **每次调用独立 Session：** 同一人/机构在两次 MCP 调用里得到不同假名，跨文件不一致；工作台是一 Job 一 Session `[码]` `mcp_server.py:123` vs `jobs.py:154`。
7. **输出位置不可预期：** `Path.cwd()/sanitized_output` 取决于 MCP 客户端启动 cwd（可能在 Claude/Cursor 安装目录），用户难找到；且工作台任务列表看不到这次处理。

---

## 6 若产品目标 = 「Agent 里看到/操作工作台」，缺什么

按投入从小到大：

| 级别 | 方案 | 需要补的东西 |
|---|---|---|
| L0（先止血） | 明确 MCP = 无头自动脱敏；安装包内置 MCP | `app.spec` 收集 `mcp_server.py`（或新增独立 exe/`--mcp` 模式）；`/mcp` 页按安装版/源码版分栏；support-matrix/README 加限定；MCP 补审计或改文案；失败判定与工作台对齐（删除成品 + `ok=False`） |
| L1（低成本"看到工作台"） | MCP 也走 Job + 返回控制台深链 | `sanitize_document` 改为 `create_job`（复用 `JobManager`），返回 `job_id` + `http://127.0.0.1:8760/console?job=...`；前提是 MCP 进程能连/起本地引擎，且工作台页支持按 job 打开（当前 `/console` 无 job 参数入口） |
| L2（Agent 内真正复核） | 暴露 resources/prompts/elicitation | 声明 `resources`；`preview://job/{id}/{page}`（image）、`job://{id}/entities`（JSON 含 status/坐标）、工具 `update_entity`/`accept_all`/`mark`/`export`；用 elicitation 做 human/内嵌媒体二次确认；协议需升级（当前 `2024-11-05`）并依赖客户端支持 |
| L3（向导式） | MCP prompts 模板 | 「先预览→逐条确认→导出」固定流程；把待确认清单喂给用户 |

---

## 7 证据与复现

- `out/_mcp_audit/probe/probe.py` → `transcript.json`：initialize / tools/list / 4 tools 全链路（含审计库未创建、vault `mcp_*`、rehydrate --list）。
- `out/_mcp_audit/probe/probe2.py` → `transcript2.json`：3 个真实样例文件的 MCP 返回。
- `out/_mcp_audit/delivery.md`：交付物/安装路径审计（zip 条目、installer.iss、Release 附件、旧下载页）。
- `out/_mcp_audit/surface.md`：工作台 vs MCP 静态矩阵（含 jobs/api/app.js 行号）。
- 复现命令要点：
  ```powershell
  # 绿色版里有没有 MCP
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $z=[System.IO.Compression.ZipFile]::OpenRead('out\desktop\Sanitized_Data-1.1.2-win64-portable.zip')
  $z.Entries | ? { $_.FullName -match 'mcp' } | % FullName   # 只有 _internal\web\mcp.html
  # MCP 实测（隔离数据目录）
  $env:YINZHAN_DATA_DIR='D:\temp\mcpdata'; python -X utf8 app\server\mcp_server.py   # 手工喂 JSON-RPC
  # 审计：真实库
  python -c "import sqlite3;c=sqlite3.connect('data/yinzhan.db');print(c.execute(\"select count(*) from audit where job_id like 'mcp_%'\").fetchone())"
  ```

> 附：本报告只读审计，未改任何产品代码；新写入仅本文件与 `out/_mcp_audit/` 证据（`out/` 已被 .gitignore 忽略）。

> 后续规划（L0-L2 不破坏性实施、DSH 集成检测面板与原型图）：[04-MCP-L2-Agent内检测-规划](../plans/04-MCP-L2-Agent内检测-规划.md)。
