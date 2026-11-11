# v1.5.3 更新平台隔离：实现与验证记录（2026-11-11）

> **结论**：`updater.py` 已改为「按平台/架构选取清单资产」：Windows 只取 `windows_portable`（自动替换）
> 或 `windows_installer`（手动安装），macOS 只取 `macos_arm64` / `macos_x64`（手动下载 DMG），
> 且对扩展名、反平台标记、macOS 架构做交叉校验；任何情况都不跨平台回退。
> 单元测试 **17/17 通过**（原 5 + 新增 12），模拟四资产清单的选取结果见 §3。

## 1. 背景（v1.5.2 的缺陷）

- `check()` 只查 `windows_portable` → macOS 客户端一旦遇到更高版本，会看到并下载 Windows 绿色版 zip；
- `install()` 固定调用 PowerShell helper（`apply_update.ps1`）→ macOS 上不可能成功；
- 清单其实已经同时发布 `macos_arm64` / `macos_x64`（`deploy/publish_update.py`），缺的是**客户端选包时的平台维度**。

## 2. 改动清单

| 文件 | 改动 |
|---|---|
| `app/server/yinzhan/updater.py` | 新增 `_platform_id()` / `_arch_id()` / `_platform_asset_keys()` / `_asset_matches()` / `_select_update_asset()` / `_manual_hint()`；`check()` 按平台选包并回填 `platform/arch/platform_label/mode/manual/release_url`；`start_download()` 只允许 `portable`；`install()` 非 Windows 直接拒绝 |
| `app/server/yinzhan/api.py` | 无需改动（`/api/update/status` 原样透传新字段） |
| `app/web/app.js` | 新增 `updateIsManual()` / `applyUpdateMode()`；手动模式显示「下载 DMG / 下载安装包」链接、隐藏「立即更新」；弹窗文案带平台标签 |
| `app/web/index.html` | 更新弹窗新增 `#btnUpdateManual` 链接 |
| `app/tests/test_updater.py` | 新增 `TestPlatformIsolation` 12 个用例 |
| `docs/impl/update-check.md` | 新增 §14 平台隔离设计与发布注意事项 |

## 3. 模拟验证：同一份四资产清单，各平台选到什么（真实运行结果）

输入清单（`version=1.5.3`，含 4 个资产键），分别模拟平台执行 `check()`：

| 模拟平台 | `platform_label` | 选中资产键 | 文件 | kind | mode | manual | 结果 |
|---|---|---|---|---|---|---|---|
| `windows` + x64 | Windows x64 | `windows_portable` | `…-win64-portable.zip` | portable | auto | false | ✅ 应用内自动更新 |
| `macos` + arm64 | macOS Apple Silicon（arm64） | `macos_arm64` | `…-arm64.dmg` | dmg | manual | true | ✅ 只给 DMG 下载入口 |
| `macos` + x64 | macOS Intel（x64） | `macos_x64` | `…-x64.dmg` | dmg | manual | true | ✅ 只给 DMG 下载入口 |
| `other`（Linux 等） | other | — | — | — | none | false | ✅ 报「不在自动更新支持范围」，不下载任何包 |

macOS 的 manual 提示原文：

> macOS 版请下载 DMG，打开后把 Sanitized_Data.app 拖入「应用程序」覆盖安装；本程序不会自动替换 macOS 应用。

复现命令（本机脚本，未入库）：

```powershell
python -X utf8 scripts\probe_update_platform.py
```

## 4. 反向用例（证明不会跨平台取包）

| 场景 | 期望 | 验证 |
|---|---|---|
| macOS 平台，清单只有 `windows_*` | 报错，不下载 zip/exe | `test_macos_never_takes_windows_asset` ✅ |
| Windows 平台，清单只有 `macos_*` | 报错，不下载 dmg | `test_windows_never_takes_macos_asset` ✅ |
| 清单把 `.dmg` 错填到 `windows_portable` 键 | 扩展名校验拒绝 | `test_windows_rejects_dmg_named_portable` ✅ |
| macOS arm64，清单只有 `macos_x64` | 不回退，报错 | `test_macos_missing_arch_asset_errors` ✅ |
| macOS，`macos_arm64` 条目名是 `.exe` | 扩展名校验拒绝 | `test_macos_rejects_unexpected_extension` ✅ |
| 非 Windows 平台，状态被手工置 ready 后 `install()` | 拒绝自动替换 | `test_macos_install_refuses` ✅ |
| 未知平台 | 报错且不选包 | `test_unknown_platform_errors` ✅ |

## 5. 测试执行记录

```text
命令：cd Sanitized_Data && python -X utf8 app\tests\test_updater.py
结果：Ran 17 tests in 0.204s — OK
（TestUpdater 5 个 + TestPlatformIsolation 12 个）
```

覆盖点：版本比较、正常清单可选、无更新、下载 SHA-256 校验、源码模式拒绝安装、
Windows 优先级/手动回退、跨平台拒绝、扩展名与架构校验、未知平台、`no_update` 平台字段。

## 6. 发布侧配套与剩余事项

- `deploy/publish_update.py` 已原生支持 4 个资产键（Windows installer/portable + macOS arm64/x64）；
  v1.5.3 发布时同一次 `latest.json` 即可服务两个平台，**不需要两份清单**。
- **v1.5.3 已按本记录出包**（2026-11-11）：版本号 1.5.3、BUILD_ID `20261111-platform-update`；
  Windows 安装包/绿色版由本机构建，macOS 双架构 DMG 由 GitHub Actions 构建；产物与 SHA-256 见
  [`v1.5.3-build-20261111.md`](v1.5.3-build-20261111.md)，发行说明见
  [`release-notes-v1.5.3.md`](../release-notes-v1.5.3.md)。
- 仍需运维执行：把 `latest.json` + 4 个安装包部署到 CKA `/updates/`（客户端检查更新依赖该静态目录）。
- macOS 应用内自动替换（helper 进程）仍不在范围：v1.5.3 为「提示 + 手动下载 DMG」，避免破坏 `.app`。

## 7. 复核依据

- `Sanitized_Data/app/server/yinzhan/updater.py`（平台选择/校验/门禁）
- `Sanitized_Data/app/tests/test_updater.py`（17 个用例）
- `Sanitized_Data/app/web/app.js`、`app/web/index.html`（手动模式 UI）
- `Sanitized_Data/docs/impl/update-check.md` §14（设计说明）
- `Sanitized_Data/deploy/publish_update.py`（四资产键发布口径）
