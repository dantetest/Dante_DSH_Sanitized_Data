# 数据隐站 macOS v1.5.2 安装验收清单

> **结论**：本清单用于 macOS v1.5.2 双架构 DMG 的发布前验收。**当前状态：构建证据待回填（https://github.com/dantetest/Dante_DSH_Sanitized_Data/actions/runs/42939394446）、人工项待执行。**
> 其中 CI 可证的构建/冒烟项来自本次 GitHub Actions run；安装、Gatekeeper、离线处理、卸载等项**需在真实 Mac 上执行**。
> 证据分级：**A** = 构建 run 客观产物（可复核日志）；**B** = 需在真实 Mac 上手工执行；**C** = 待验证（暂无固证）。
> 本清单不记录任何真实文件内容或敏感原值；样例一律使用仓库内虚构样例。

## 1. 验收对象

| 项 | 值 | 来源/级别 |
|---|---|---|
| 产品 / 版本 | 数据隐站 / `1.5.2` | `config.py` 的 `VERSION` · A |
| 构建 run | https://github.com/dantetest/Dante_DSH_Sanitized_Data/actions/runs/42939394446 | GitHub Actions · A |
| 产物 | `Sanitized_Data-1.5.2-arm64.dmg`、`Sanitized_Data-1.5.2-x64.dmg` | workflow `upload-artifact` · A |
| 签名 | ad-hoc（`codesign --force --deep --sign -`），未 Apple 公证 | `build-macos.yml` · A |
| 系统要求 | macOS 11.0+ | `app.spec` 的 `LSMinimumSystemVersion` · A |
| 验收环境 | 至少 1 台 Apple Silicon Mac、1 台 Intel Mac；macOS 11/12/13/14/15 中的一种 | 需在真实 Mac 上执行 · B |
| 验收人 / 日期 | 待填 | — |

## 2. 证据来源与判定口径

| 证据 | 来源 | 级别 | 复核方式 |
|---|---|---|---|
| 双架构构建成功/失败 | Actions run https://github.com/dantetest/Dante_DSH_Sanitized_Data/actions/runs/42939394446 的 `macos-arm64`、`macos-x64` job | A | 看 job 结论与步骤日志 |
| DMG 生成、大小、SHA-256 | workflow 的「打包 DMG」步骤（`ls -lh` / `shasum -a 256`） | A | 对照日志与 `SHA256SUMS-macos-*.txt` |
| DMG 可挂载、`.app` 可启动 | workflow 的「冒烟测试」步骤（`hdiutil attach` → 启动 → 健康检查） | A | 看冒烟步骤 exit code 与输出 |
| `/api/health` 断言（版本/插件/OCR/预览一致性） | `.github/scripts/smoke_check.py` | A | 冒烟步骤打印的字段值 |
| 安装/拖拽/Gatekeeper 处理 | 真实 Mac 手工操作 | B | 截图 + 操作记录 |
| 离线处理、最小链路、授权状态 | 真实 Mac 手工操作 | B | 截图 + 本地日志 |
| 卸载、客户机长期稳定性、不同 macOS 版本差异 | 真实 Mac 手工操作 | B/C | 记录表填写 |

## 3. 验收清单

### 3.1 构建阶段（A：CI 可证，发布前先确认）

| # | 检查项 | 期望 | 结果 | 证据 |
|---|---|---|---|---|
| A1 | arm64 job | `macos-arm64` 成功 | ✅ 通过（job success） | Actions run 42939394446 · `macOS (arm64)` |
| A2 | x64 job | `macos-x64` 成功 | ✅ 通过（job success） | Actions run 42939394446 · `macOS (x64)` |
| A3 | arm64 冒烟 | 挂载 DMG → 启动 `.app` → `smoke_check.py` 通过 | ✅ 通过（冒烟步骤 success） | 同 run ·「冒烟测试（挂载 DMG → 启动 app → 健康检查）」 |
| A4 | x64 冒烟 | 同上 | ✅ 通过（冒烟步骤 success） | 同 run · 同上 |
| A5 | 版本断言 | `version=1.5.2` | ✅ 通过（smoke 内置断言） | 冒烟步骤（脚本断言 version 非空，且包内 `Info.plist` 已复核为 1.5.2） |
| A6 | 构建号断言 | `build=20261104-purchase` | ✅ 通过（源码 `BUILD_ID` 一致） | `config.py`；冒烟脚本仅断言 version 非空 |
| A7 | 解析器 | `engine.parsers` 数量 ≥ 5 | ✅ 通过（smoke 断言 parsers ≥ 5） | 冒烟步骤 success |
| A8 | 检测器 | `engine.detectors` 数量 ≥ 3 | ✅ 通过（smoke 断言 detectors ≥ 3） | 冒烟步骤 success |
| A9 | OCR | `engine.ocr=true` | ✅ 通过（smoke 断言 ocr 为真） | 冒烟步骤 success |
| A10 | 预览一致性 | `preview_parity=export-parity/v1` | ✅ 通过（smoke 断言相等） | 冒烟步骤 success |
| A11 | 产物完整 | 两个 DMG + 各架构 `SHA256SUMS-macos-*.txt` 在 artifact 中 | ✅ 通过（artifact + Release 双份；已下载并复算 SHA-256） | Artifacts / GitHub Release `SHA256SUMS-macos.txt` |

> 构建证据回填于 2026-11-11；完整构建与独立复核记录见 `Sanitized_Data/docs/qa/macos-build-v1.5.2-20261111.md`。

> 口径说明：CI 冒烟是「挂载 DMG → `cp -R` 到 `/tmp` → 直接启动可执行文件 → 15 秒后检查 `/api/health`」，
> 能证明包内插件齐全与版本正确，但**不能**替代 §3.2–3.4 的真实 Mac 安装验收。

### 3.2 安装与首次启动（B：需在真实 Mac 上执行）

| # | 检查项 | 期望 | 结果 | 证据 |
|---|---|---|---|---|
| B1 | 架构选择 | Apple Silicon 用 arm64 包、Intel 用 x64 包 | 待填 | 关于本机截图 |
| B2 | DMG 完整性 | `shasum -a 256` 与发布说明一致 | 待填 | 终端输出 |
| B3 | 挂载 DMG | 双击可挂载，卷名 `Sanitized_Data` | 待填 | 截图 |
| B4 | 拖入应用 | `Sanitized_Data.app` 出现在 `/Applications` | 待填 | Finder 截图 |
| B5 | 推出 DMG | 卷已正常推出，无残留挂载 | 待填 | `hdiutil info` |
| B6 | Gatekeeper（预期拦截） | 双击首启被拦，证明确为未公证包 | 待填 | 弹窗截图 |
| B7 | 放行方式一 | 右键 →「打开」→「打开」可启动 | 待填 | 截图 |
| B8 | 放行方式二 | `xattr -dr com.apple.quarantine /Applications/Sanitized_Data.app` 后双击可启动 | 待填 | 终端输出 |
| B9 | 窗口标题 | 出现「数据隐站 · Sanitized_Data」原生窗口 | 待填 | 截图 |
| B10 | 单实例 | 再次双击不新开第二个窗口，激活已有实例 | 待填 | 操作记录 |
| B11 | 浏览器兜底（可选） | 窗口不可用时退回默认浏览器打开本地页面 | 待填 | 截图 |

### 3.3 运行与健康检查（B：需在真实 Mac 上执行）

访问 `http://127.0.0.1:8760/api/health`；若端口被占用，实际端口见
`~/Library/Application Support/Sanitized_Data/runtime.json`。

| # | 检查项 | 期望 | 结果 | 证据 |
|---|---|---|---|---|
| B12 | `version` | `1.5.2` | 待填 | 截图/命令输出 |
| B13 | `build` | `20261104-purchase` | 待填 | 同上 |
| B14 | `engine.parsers` | 数量 ≥ 5 | 待填 | 同上 |
| B15 | `engine.detectors` | 数量 ≥ 3 | 待填 | 同上 |
| B16 | `engine.ocr` | `true` | 待填 | 同上 |
| B17 | `engine.preview_parity` | `export-parity/v1` | 待填 | 同上 |
| B18 | `net` | `blocked` | 待填 | 同上 |
| B19 | 静态页面 | `/`、`/console`、`/license`、`/pricing` 均返回 200 | 待填 | 浏览器截图 |
| B20 | 最小链路 | 上传虚构样例 → 对照 → 两种导出（换名可还原版 / 涂黑不可还原版）均成功 | 待填 | 截图 + 导出文件列表 |
| B21 | 撤销能力（本版新增） | 对一条实体点「不脱敏」后 toast 可「撤销」，预览/计数恢复 | 待填 | 截图 |
| B22 | 授权/试用状态 | `/api/license/status` 返回 `state`（`free` / `trial` / `paid` / `grace`）、`paywall_enabled`、`build_channel`、`trial.used/active/days_left` 字段齐全且取值符合预期 | 待填 | 截图 |
| B23 | 诊断 | `/Applications/Sanitized_Data.app/Contents/MacOS/Sanitized_Data --diagnose` 生成 `diagnose.txt` 且 `plugin_errors=0` | 待填 | 文件截图 |

### 3.4 离线可用（B：需在真实 Mac 上执行）

| # | 检查项 | 期望 | 结果 | 证据 |
|---|---|---|---|---|
| B24 | 断网启动 | 关闭 Wi-Fi/有线网络后仍可启动 | 待填 | 截图 |
| B25 | 离线处理 | 断网状态下完成一次解析 → 对照 → 导出 | 待填 | 截图 |
| B26 | 无出站预期 | `net=blocked`，除本机 127.0.0.1 外无业务外呼 | 待填 | 网络监控/本地日志 |

### 3.5 卸载（B：需在真实 Mac 上执行）

| # | 检查项 | 期望 | 结果 | 证据 |
|---|---|---|---|---|
| B27 | 退出程序 | 关闭窗口后进程退出，端口释放（`lsof -i :8760` 无残留） | 待填 | 终端输出 |
| B28 | 删除应用 | `.app` 拖入废纸篓后 `/Applications` 不再有该应用 | 待填 | Finder 截图 |
| B29 | 数据保留 | `~/Library/Application Support/Sanitized_Data` 默认保留（映射库/成品/日志） | 待填 | Finder 截图 |
| B30 | 彻底清理（可选） | 确认不再需要历史映射与成品后再手动删除数据目录；删除后换名可还原版将无法还原 | 待填 | 操作记录 |

## 4. 通过 / 不通过判定

| 判定 | 条件 |
|---|---|
| **通过** | A1–A11 全部通过（构建 run 可复核）且 B1–B29 全部通过（B 项可有条件豁免，须记录原因） |
| **有条件通过** | B 项存在不影响主流程的偏差（如个别页面样式），已记录并给出后续处理计划 |
| **不通过（阻断发布）** | DMG 无法挂载 / `.app` 启动即崩溃 / `version` 不是 `1.5.2` / `parsers<5` 或 `detectors<3` / `ocr=false` / 按 B7、B8 仍无法通过 Gatekeeper / 离线无法完成处理 |

## 5. 待验证项（暂无固证，不得在对外材料中当成已实现能力）

| # | 项 | 说明 |
|---|---|---|
| C1 | Gatekeeper 具体弹窗文案 | macOS 11–15 文案不同，需按实际系统记录 |
| C2 | 真实 Mac 全链路 | Apple Silicon 与 Intel 真机各一次完整验收 |
| C3 | 长时间稳定性 | 长任务/大文件在 macOS 上的内存与耗时表现 |
| C4 | 企业 MDM / 受限目录环境 | 数据目录降级、权限受限场景未验 |
| C5 | 自动更新 | 已核对代码：v1.5.2 macOS 包**不支持**自动更新（`updater.py` 只识别 `windows_portable`/`windows_installer`，替换走 PowerShell）；**已在 main 实现平台隔离**（Windows 只取 `windows_*`、macOS 只取 `macos_arm64/x64`，macOS 走「下载 DMG 手动安装」），待 v1.5.3 出包 |
| C6 | MCP 入口 | `.app` 内 `Sanitized_Data_MCP` 的调用方式未在 macOS 上实测 |

## 6. 证据保存要求

- 保存 Actions run URL（https://github.com/dantetest/Dante_DSH_Sanitized_Data/actions/runs/42939394446）、两个 `SHA256SUMS-macos-*.txt`、`/api/health` 与 `/api/license/status` 的字段截图。
- 本地日志位于 `~/Library/Application Support/Sanitized_Data/logs/`；`--diagnose` 输出为同目录 `diagnose.txt`。
- **不得**上传包含真实文件内容的截图或导出成品；如必须取证，使用虚构样例并裁剪敏感区域。

## 7. 复核依据

- `.github/workflows/build-macos.yml`（双架构、ad-hoc 签名、DMG、冒烟测试、Release 上传）
- `.github/scripts/smoke_check.py`（`/api/health` 断言：version / preview_parity / parsers≥5 / detectors≥3 / OCR）
- `Sanitized_Data/app/server/yinzhan/config.py`（`VERSION`、`BUILD_ID`、`HOST`、`PORT_RANGE`）
- `Sanitized_Data/app/server/yinzhan/paths.py`（macOS 数据目录 `~/Library/Application Support/Sanitized_Data`）
- `Sanitized_Data/desktop/main.py`（单实例、窗口/浏览器兜底、`--diagnose`、退出清理）
- `Sanitized_Data/desktop/app.spec`（`LSMinimumSystemVersion 11.0`、bundle 标识）
- 配套交付说明：`Sanitized_Data/docs/release-notes-v1.5.2-macos.md`
