# 数据隐站 v1.5.3 发行说明（Windows / macOS）

> **本版核心**：修复更新模块的**平台隔离**缺陷 —— Windows 客户端只获取 Windows 包，
> macOS 客户端只获取对应架构的 DMG，不再出现「macOS 端看到并下载 Windows 绿色版」。
> 版本 `1.5.3`（单一版本源 `Sanitized_Data/app/server/yinzhan/config.py` 的 `VERSION`），
> Windows x64 与 macOS（Apple Silicon / Intel）同源码基线发布。

## 1. 产物清单

| 平台 | 文件 | 说明 |
|---|---|---|
| Windows x64 | `Sanitized_Data_Setup_1.5.3_x64.exe` | Inno Setup 安装包 |
| Windows x64 | `Sanitized_Data-1.5.3-win64-portable.zip` | 绿色版（一键更新使用此包） |
| macOS Apple Silicon | `Sanitized_Data-1.5.3-arm64.dmg` | 拖入「应用程序」安装 |
| macOS Intel | `Sanitized_Data-1.5.3-x64.dmg` | 拖入「应用程序」安装 |
| 校验 | `SHA256SUMS-v1.5.3-release.txt`、`SHA256SUMS-macos.txt` | SHA-256 清单 |

- 构建证据：[构建与产物验证记录](qa/v1.5.3-build-20261111.md)（SHA-256/大小/冒烟/架构复核在此回填）
- 下载：GitHub Release `v1.5.3`（公开，含 macOS DMG 与 `SHA256SUMS-macos.txt`）；公司 Gitea Release `v1.5.3`（内部主仓，含 Windows/macOS 全部资产）
- 安装步骤与已知限制见下文 §4、§5。

## 2. 本版变更

### 2.1 更新平台隔离（唯一功能变更）

| 平台 | 行为变化 |
|---|---|
| Windows | 检查更新只认 `windows_portable`（绿色版自替换）与 `windows_installer`（手动安装入口）；**不会**再读取 macOS 资产 |
| macOS | 检查更新只认 `macos_arm64` / `macos_x64`（按当前芯片二选一）；**不会**再读取 Windows 资产；弹窗改为「下载 DMG 安装包」，隐藏「立即更新」 |
| 其它平台 | 明确提示「不在自动更新支持范围」，不下载任何包 |

实现要点（`app/server/yinzhan/updater.py`）：

1. 按 `sys.platform` + `platform.machine()` 生成当前平台允许的资产键列表；
2. 对选中资产做四重校验：键的平台归属、扩展名（Windows `.zip/.exe`；macOS `.dmg`）、反平台标记黑名单、macOS 架构互斥；
3. **没有跨平台回退**：本平台无可用资产时报错并给下载页链接，绝不拿另一平台的包顶上；
4. `install()` 增加平台门禁：非 Windows 直接拒绝自动替换，避免在 macOS 上调用 PowerShell helper；
5. 状态接口新增 `platform / arch / platform_label / mode / manual / release_url`，前端据此渲染。

详细设计与测试见 [`docs/impl/update-check.md` §14](../impl/update-check.md) 与
[`docs/qa/update-platform-isolation-2026-11-11.md`](qa/update-platform-isolation-2026-11-11.md)。

### 2.2 macOS 双架构 DMG 同步发布

v1.5.2 起 macOS 已可出包，本版继续随版本发布 arm64 / x64 两个 DMG（ad-hoc 签名、未公证；
首次启动需右键「打开」或执行 `xattr -dr com.apple.quarantine /Applications/Sanitized_Data.app`）。

### 2.3 站点与文档

- 下载页更新到 v1.5.3，Windows / macOS 卡片与 SHA-256 行同时展示；
- 更新日志新增 v1.5.3 条目；
- 新增平台隔离设计说明与验证记录。

## 3. 升级方式

| 现用平台 | 操作 |
|---|---|
| Windows（安装版） | 应用内「检查更新」下载绿色版自动替换；或下载 `Setup_1.5.3_x64.exe` 覆盖安装 |
| Windows（绿色版） | 应用内「检查更新」自动替换；或下载绿色版 zip 解压覆盖 |
| macOS | 应用内「检查更新」只提供 DMG 下载入口；下载对应架构 DMG → 打开 → 把 `Sanitized_Data.app` 拖入「应用程序」覆盖 |

> 数据目录不迁移：Windows `%LOCALAPPDATA%\Sanitized_Data`，macOS `~/Library/Application Support/Sanitized_Data`；
> 升级不会删除映射库与历史成品。

## 4. 校验

- 先按 §1 的 SHA-256 清单核对下载文件；
- macOS 也可执行 `shasum -a 256 Sanitized_Data-1.5.3-*.dmg`；
- 启动后 `/api/health` 应返回 `version=1.5.3`、`build=20261111-platform-update`；
  Windows 打包自检另要求 `scanners` 含 `barcode`、`OCR=true`、授权 keyring 校验通过。

## 5. 已知限制

| 项 | 说明 |
|---|---|
| macOS 自动替换 | 本版仍只做「提示 + 下载 DMG 手动安装」，不做应用内替换（`.app` 自替换需应用外 helper，未在本版范围） |
| Apple 公证 | macOS 包仅 ad-hoc 签名，未公证；Gatekeeper 首次拦截属预期 |
| 更新源域名白名单 | 仍未实现；当前依赖 HTTPS + SHA-256 + 平台/扩展名校验 |
| 依赖版本 | `requirements.txt` 未锁版本，macOS 双架构的 `onnxruntime` 解析版本可能不同（功能一致，冒烟通过） |

## 6. 复核依据

- `Sanitized_Data/app/server/yinzhan/updater.py`（平台选择/校验/门禁）
- `Sanitized_Data/app/tests/test_updater.py`（17 个用例）
- `Sanitized_Data/app/web/app.js`、`app/web/index.html`（手动模式 UI）
- `.github/workflows/build-macos.yml`、`desktop/build_win.ps1`、`desktop/installer.iss`
- `Sanitized_Data/deploy/publish_update.py`（四资产键发布口径）
