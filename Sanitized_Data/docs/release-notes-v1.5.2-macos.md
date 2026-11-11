# 数据隐站 macOS v1.5.2 交付说明（DMG · arm64 / x64）

> **结论**：本文件用于「数据隐站 / Sanitized_Data」macOS v1.5.2 客户交付。版本 **1.5.2**（唯一版本源
> `Sanitized_Data/app/server/yinzhan/config.py` 的 `VERSION`），提供 Apple Silicon（arm64）与 Intel（x64）两个 DMG；
> 本版与 Windows v1.5.2 同源码基线、功能对齐。安装包由 GitHub Actions 构建（https://github.com/dantetest/Dante_DSH_Sanitized_Data/actions/runs/42939394446），
> 采用 **ad-hoc 签名、未做 Apple 公证**，首次启动被 Gatekeeper 拦截属预期行为，按 §4 处理即可。
>
> 产物 SHA-256、字节数与构建 run 的 URL 已按 2026-11-11 的实际构建回填（见 §2）；
> 若后续重新构建，请以新一次 Actions 日志中的 `shasum -a 256 *.dmg` / `ls -lh *.dmg` 为准并同步更新 §2。

## 1. 交付对象

| 项 | 值 | 依据 |
|---|---|---|
| 产品 | 数据隐站 / Sanitized_Data | `app/server/yinzhan/config.py` |
| 版本 | `1.5.2` | `config.py` 的 `VERSION` |
| 构建号 | `20261104-purchase` | `config.py` 的 `BUILD_ID`，`/api/health` 原样返回 |
| git 标签 | `v1.5.2`（指向提交 `c0a515e`） | `git tag --list` / `git rev-parse v1.5.2` |
| 系统要求 | macOS 11.0 及以上 | `desktop/app.spec` 的 `LSMinimumSystemVersion` |
| 应用标识 | `com.blctek.sanitizeddata` | `desktop/app.spec` 的 `bundle_identifier` |
| 架构 | arm64（Apple Silicon）/ x64（Intel），两包不通用 | 构建矩阵 `macos-15` / `macos-15-intel` |
| 签名 | ad-hoc（`codesign --force --deep --sign -`），未公证 | `.github/workflows/build-macos.yml` |
| 构建流水线 | `.github/workflows/build-macos.yml` | 仓库根 `.github/` |

> 版本号只维护一处：`app.spec` 在打包时从 `config.py` 正则读取 `VERSION` 写入 `CFBundleShortVersionString` /
> `CFBundleVersion`，构建日志会打印 `[spec] APP_VERSION = 1.5.2`。

## 2. 产物清单

| 架构 | 文件名 | 大小（字节） | SHA-256 |
|---|---|---|---|
| Apple Silicon | `Sanitized_Data-1.5.2-arm64.dmg` | 265438348 | DA8D2540F167BA2CFAD0835604F02B52EEC0001618343CD282E406197CCB5C33 |
| Intel | `Sanitized_Data-1.5.2-x64.dmg` | 211811085 | 95FE7B617933B79C571E5E68E488929FDF10E45A7A2397C218F0E744BFBDA5FD |

- 构建与冒烟日志：https://github.com/dantetest/Dante_DSH_Sanitized_Data/actions/runs/42939394446
- 下载地址：GitHub Release `v1.5.2`（公开）；内部主仓 Gitea Release `v1.5.2` 同步挂载三个资产（两个 DMG + `SHA256SUMS-macos.txt`）
- 每个架构对应 Actions artifact：`macos-arm64` / `macos-x64`；各含 DMG 与该架构的 `SHA256SUMS-macos-*.txt`。
- 打 `v*` 标签时 workflow 还会把两个 DMG 与汇总的 `SHA256SUMS-macos.txt` 挂到 GitHub Release。
- 大小/SHA-256 一律以 Actions 日志中 `shasum -a 256` 与 `ls -lh` 的真实输出为准。

## 3. 本版变更（v1.5.1 → v1.5.2）

### 3.1 变更来源（可复核）

| 提交 | 类型 | 内容 |
|---|---|---|
| `c0a515e` | feat | P0 误报取消脱敏与撤销恢复（本版唯一功能变更） |
| `1009800` | docs | v1.5.1 细节修复说明（交付文档，不改变运行行为） |

- 复核命令：`git log --oneline v1.5.1..v1.5.2`（范围仅上述 2 个提交；`config.py` 在该范围内只改 `VERSION`）。
- `78a6ff9`（`docs(desktop): P0 误报取消脱敏说明`）是同功能配套说明文档，提交在 tag `v1.5.2` 之后、随 `main` 发布；
  其内容对应的功能代码已在 `c0a515e` 内，不影响本版产物。

### 3.2 功能要点

| 能力 | 说明 |
|---|---|
| 单处「不脱敏」 | 右侧「隐去清单」的自动识别实体可逐个取消脱敏；按钮默认悬停显示，待确认条目常显 |
| 高风险二次确认 | `PERSON / ID_CARD / PHONE / EMAIL / BANK / PASSPORT / LICENSE / MEDICAL` 等类型取消前二次确认 |
| 撤销恢复 | 取消后底部 toast 提供「撤销」，恢复原脱敏决策；后端 `Entity.rejected_decision` 暂存、`action=restore` 恢复 |
| 预览内取消 | 预览中的红色替换文字/黑色条支持双击取消脱敏 |
| 导出语义不变 | 预览、导出、计数继续按 `status=rejected` 过滤，导出器未改动 |

> 本版**不做**批量「本类全部不脱敏」、不做「全部不脱敏」、不做「已忽略」列表（按产品确认的范围）。

### 3.3 与 Windows v1.5.2 的对齐

| 维度 | 对齐情况 |
|---|---|
| 版本号 | 一致：`config.py` 的 `VERSION=1.5.2`，Windows/macOS 共用 |
| 引擎与功能 | 同一源码基线（tag `v1.5.2`），解析/检测/导出/授权逻辑一致 |
| 规则包 / 模型 | 同一 `app.spec` 收集（规则包、YuNet 模型、license 公钥、OCR/条码引擎） |
| 差异 | 分发形态、签名与首次启动处理、数据目录路径（见 §6） |

## 4. 安装步骤

### 4.1 选对架构

| 机器 | 选择 |
|---|---|
| Apple Silicon（M1/M2/M3/M4 等） | `Sanitized_Data-1.5.2-arm64.dmg` |
| Intel Mac | `Sanitized_Data-1.5.2-x64.dmg` |

在「系统设置 → 通用 → 关于本机」查看芯片型号；不确定时先按芯片选包，不要混装。

### 4.2 安装

1. 下载对应 DMG，先按 §5 校验 SHA-256。
2. 双击 DMG，挂载卷名为 `Sanitized_Data`。
3. 把 `Sanitized_Data.app` 拖入「应用程序 / Applications」。
4. 在 Finder 侧栏推出该 DMG。
5. 首次启动（因 ad-hoc 签名、未公证，被 Gatekeeper 拦截属预期）：
   - **方法一（推荐）**：在「应用程序」中右键点「数据隐站」→「打开」→ 再点「打开」；
   - **方法二（终端）**：
     ```bash
     xattr -dr com.apple.quarantine /Applications/Sanitized_Data.app
     ```
     然后正常双击启动。
   - 若系统仍提示无法验证开发者，可到「系统设置 → 隐私与安全性」允许本次打开（不同 macOS 版本文案略有差异）。
6. 启动后会出现原生窗口；窗口不可用时自动退回默认浏览器打开本地页面。
7. 浏览器访问 `http://127.0.0.1:8760/` 也可进入控制台；端口被占用时程序会在 `8760–8840` 内另选，
   实际地址记录在 `~/Library/Application Support/Sanitized_Data/runtime.json`。

> 处理敏感文件时请保持本机离线或按内部规范使用：引擎在本机运行，`/api/health` 的 `net` 字段为 `blocked`。

## 5. 校验与自检

### 5.1 校验文件完整性

```bash
shasum -a 256 Sanitized_Data-1.5.2-arm64.dmg
shasum -a 256 Sanitized_Data-1.5.2-x64.dmg
```

输出应与本文件 §2 的 `DA8D2540F167BA2CFAD0835604F02B52EEC0001618343CD282E406197CCB5C33` / `95FE7B617933B79C571E5E68E488929FDF10E45A7A2397C218F0E744BFBDA5FD` 完全一致（本次构建已回填）。

### 5.2 启动后自检（与 CI 冒烟同口径）

参照 `.github/scripts/smoke_check.py`，访问 `http://127.0.0.1:8760/api/health`，逐项确认：

| 字段 | 期望 | 说明 |
|---|---|---|
| `version` | `1.5.2` | 与安装包版本一致 |
| `build` | `20261104-purchase` | `config.py` 的 `BUILD_ID`，用于确认是新包 |
| `engine.parsers` | 数量 ≥ 5 | 解析器未漏打包；缺失会直接报错 |
| `engine.detectors` | 数量 ≥ 3 | 检测器未漏打包 |
| `engine.ocr` | `true` | OCR 可用 |
| `engine.preview_parity` | `export-parity/v1` | 预览与成品使用同一套涂黑矩形 |
| `net` | `blocked` | 文件处理引擎不出站 |

> 也可直接执行 `curl -s http://127.0.0.1:8760/api/health` 查看上述字段；字段缺失或数量不足时应停止交付并反馈。

## 6. 已知限制（仅列可证实项）

| 项 | 说明 | 状态 |
|---|---|---|
| Apple 公证 | 仅 ad-hoc 签名，未做 Apple 公证；首次启动需按 §4.2 放行 | 已确认设计事实 |
| 安装器形态 | 只提供 DMG，`.app` 拖入即可；无 `.pkg` 安装器、无独立卸载器 | 已确认 |
| 卸载 | 退出程序 → 将 `.app` 拖入废纸篓；数据目录默认保留，需手动删除 | 已确认 |
| 数据位置 | `~/Library/Application Support/Sanitized_Data`（可用 `YINZHAN_DATA_DIR` 覆盖） | 已确认 |
| 真实 Mac 全链路 | 安装/离线处理/卸载的人工验收尚未在真实 Mac 上完成 | 待验证 |
| 自动更新 | macOS 包**不支持**自动更新，请手动下载新 DMG 覆盖安装。原因（已核对代码）：`updater.py` 的 `check()` 只识别 `windows_portable` / `windows_installer`，`install()` 通过 PowerShell 脚本替换文件，均无 macOS 分支。v1.5.2 清单 version 与当前版本相同，不会弹出错误；后续版本发布 Windows 资产后，macOS 客户端点「检查更新」可能看到 Windows 包，属已知问题 | 已确认（代码）→ 建议 v1.5.3 按 `sys.platform` 分支修复 |
| 双架构互斥 | 两个 DMG 为对应架构原生包，不通用 | 已确认 |

## 7. 回填记录（2026-11-11 本次构建）

| 项 | 值 | 来源 |
|---|---|---|
| 构建 run | https://github.com/dantetest/Dante_DSH_Sanitized_Data/actions/runs/42939394446 | Actions 页面 |
| arm64 DMG SHA-256 | `DA8D2540F167BA2CFAD0835604F02B52EEC0001618343CD282E406197CCB5C33` | Release 的 `SHA256SUMS-macos.txt` + 本地复算 |
| x64 DMG SHA-256 | `95FE7B617933B79C571E5E68E488929FDF10E45A7A2397C218F0E744BFBDA5FD` | 同上 |
| arm64 DMG 字节数 | `265438348` | DMG 文件属性 / Release 元数据 |
| x64 DMG 字节数 | `211811085` | 同上 |

> 若后续重新构建：以新 run 日志中的 `shasum -a 256 *.dmg` / `ls -lh *.dmg` 为准更新本表与 §2，并全文检索 `[[` 确认无残留占位符。

## 8. 复核依据

- `Sanitized_Data/app/server/yinzhan/config.py`（`VERSION` / `BUILD_ID` / `HOST` / `PORT_RANGE`）
- `Sanitized_Data/desktop/app.spec`（版本号读取、`BUNDLE`、`LSMinimumSystemVersion`、打包资源清单）
- `Sanitized_Data/desktop/main.py`（单实例、窗口/浏览器兜底、`--diagnose`、数据目录清理）
- `.github/workflows/build-macos.yml`（双架构、ad-hoc 签名、DMG、冒烟、Release 挂载）
- `.github/scripts/smoke_check.py`（`/api/health` 断言口径）
- `Sanitized_Data/docs/ops/P0_REJECT_FALSE_POSITIVE_2026-11-05.md`、`DETAIL_FIXES_2026-11-05.md`（本版变更说明）
- `Sanitized_Data/docs/qa/macos-install-acceptance-1.5.2.md`（配套安装验收清单）
