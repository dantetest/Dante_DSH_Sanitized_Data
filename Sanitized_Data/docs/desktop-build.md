# 桌面版构建与分发包

对应两个问题：
1. 安装包怎么检查环境、怎么自动补齐
2. macOS 怎么用 GitHub Actions 出「免开发者账号」的测试包

---

## 一、安装包的环境检查与自动补齐

### 1.1 先说结论：大部分环境根本不需要补

因为 **Python 运行时已经打进程序里了**，用户不需要装 Python、不需要装 pip、不需要配环境变量。
真正需要检查的只有下面这几项：

| 依赖 | 是否必需 | 缺失时怎么办 | 谁来处理 |
|---|---|---|---|
| Python 3.10+ | **不需要** | — | 已内置 |
| WebView2 运行时 | 推荐（独立窗口） | 安装包静默安装；装不上则自动改用默认浏览器，功能不变 | 安装包自动 |
| VC++ 2015-2022 运行库 | 需要（onnxruntime 依赖） | 打包时已把 3 个 DLL 复制进程序目录；安装包再静默装一次兜底 | 构建脚本 + 安装包 |
| .NET Framework | **不需要** | — | pywebview 走 WebView2，不用 .NET |
| 磁盘空间 | 需要 400 MB | 安装前检查并提示 | 安装包 |
| 管理员权限 | **不需要** | — | 装到 `%LOCALAPPDATA%` |

也就是说：**最坏情况下用户什么都不装也能用**，只是没有独立窗口，会开在浏览器里。

### 1.2 安装流程（用户看到的）

```
双击 Setup
   ↓
┌─ 环境检查 ─────────────────────────────┐
│ 系统：Windows 64 位                     │
│ 安装位置：C:\Users\你\AppData\Local\... │
│                                        │
│ 【程序依赖】                            │
│   ! 窗口组件 WebView2 —— 缺失，安装时自动补齐 │
│   ✓ 运行库 VC++ 2015-2022 —— 已安装      │
│                                        │
│ 【说明】本程序自带 Python 运行环境，     │
│        不需要你另外安装任何东西。        │
└────────────────────────────────────────┘
   ↓ 点「安装」
自动静默安装缺失组件（进度条显示"正在安装 WebView2 窗口组件…"）
   ↓
装完弹一句结果（只有失败时才提示，成功不打扰）
```

### 1.3 实现要点（`desktop/installer.iss`）

**探测**

```pascal
// WebView2：三个注册表位置都要查（64 位机器 / 32 位视图 / 当前用户）
if RegQueryStringValue(HKLM64, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-...}', 'pv', v)
// 值为 '0.0.0.0' 表示"登记了但运行时没装"，要当作缺失

// VC++：这个键存在且 Installed=1 才算装了
RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', ok)
```

**补齐**（在 `PrepareToInstall` 里做，此时还没开始铺文件）

```pascal
ExtractTemporaryFile('VC_redist.x64.exe');
Exec(ExpandConstant('{tmp}\VC_redist.x64.exe'), '/quiet /norestart', '', SW_HIDE, ewWaitUntilTerminated, rc);

ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, rc);
```

**失败不阻断**

```pascal
// 装不上也不报错，只提示一句；程序有浏览器兜底
if GetWebView2Version() = '' then
  MsgBox('窗口组件没装上，程序会自动用默认浏览器打开界面，功能完全一样。', mbInformation, MB_OK);
```

### 1.4 构建命令

```powershell
# 1. 拉取运行时安装包（只需一次）
powershell -ExecutionPolicy Bypass -File desktop\fetch_redist.ps1

# 2. 一键打包（装依赖 → PyInstaller → 补 DLL → 自检 → 出安装包）
powershell -ExecutionPolicy Bypass -File desktop\build_win.ps1
```

产物：

| 文件 | 说明 |
|---|---|
| `dist\Sanitized_Data\` | 绿色版目录，可直接压缩分发 |
| `out\desktop\Sanitized_Data-1.1.2-win64-portable.zip` | 绿色版压缩包（构建脚本自动生成） |
| `dist_installer\Sanitized_Data_Setup_1.1.2_x64.exe` | 安装包（需要装 Inno Setup 6/7） |

v1.1.2 产物的体积与 SHA-256 见 [§六 构建记录](#六v111-重建记录2026-10-21)。

> **构建脚本编码要求**：`desktop/build_win.ps1` 与 `desktop/installer.iss` 必须保存为
> **UTF-8 with BOM**——Windows PowerShell 5.1 与 Inno Setup 才会按 UTF-8 解析中文；
> 无 BOM 时中文注释会吞掉引号导致脚本解析失败（v1.1.0 构建时踩到过）。
> 编辑脚本后请确认 BOM 仍存在（`Get-Content -Encoding Byte` 前 3 字节为 `239 187 191`）。
>
> **无人值守构建**：脚本在自检前会对产物 exe 执行 `Unblock-File`，避免
> 「打开文件 - 安全警告」对话框阻塞构建；若在无交互桌面会话里构建卡住，先检查是否
> 有该对话框。

---

## 二、macOS：不用开发者账号，先出测试包

### 2.1 结论

**完全可以。** 测试阶段不需要 $99 的开发者账号，需要做两件事：

1. **ad-hoc 签名**（`codesign --sign -`）—— 必须做，否则 Apple Silicon 上根本起不来
2. **告诉测试者绕过 Gatekeeper** —— 右键打开，或一条命令

只是用户第一次打开时会看到「无法验证开发者」，不适合对外正式分发，内部测试完全够用。

### 2.2 GitHub Actions 怎么配

前提：**你的仓库现在在 Gitea（git.blctek.com），GitHub Actions 只认 GitHub 仓库**。
三个选择：

| 方案 | 成本 | 说明 |
|---|---|---|
| **A. 镜像到 GitHub** | 公开仓库免费；私有仓库 macOS 按 10 倍计费 | 最简单，推荐先用这个 |
| B. Gitea Actions + 自己的 Mac runner | 需要一台 Mac 常开 | 代码不出内网 |
| C. 租云 Mac（MacStadium 等） | 约 $50/月起 | 适合长期 |

> ⚠️ **计费提醒**：GitHub 免费账户每月 2000 分钟，但 **macOS runner 按 10 倍消耗**，
> 也就是实际只有 200 分钟/月。一次 macOS 构建约 6–10 分钟，够做 20 次左右。
> 如果仓库是 **public**，则不消耗额度（免费）。

**方案 A 的具体做法**：

```bash
# 1. 在 GitHub 建一个私有仓库（比如 Sanitized_Data-desktop）

# 2. 本地加第二个 remote 并推一次
git remote add github https://github.com/<你的账号>/Sanitized_Data-desktop.git
git push github main

# 3. 之后每次要出包，手动推一次
git push github main
```

**Workflow 已经写好**：`.github/workflows/build-macos.yml`

里面做了这些事：

| 步骤 | 作用 |
|---|---|
| `macos-15`（arm64）/ `macos-15-intel`（Intel）双任务 | 标准 runner 标签口径：`macos-15`=Apple Silicon，`macos-15-intel`=Intel；macOS 13/14 已退役 |
| `pip install -r requirements.txt` | 装引擎依赖 |
| `pyinstaller desktop/app.spec` | 打包成 `.app` |
| `codesign --force --deep --sign -` | **ad-hoc 签名**，arm64 必需 |
| `hdiutil create ... UDZO` | 打成 dmg，文件名带版本号：`Sanitized_Data-<version>-{arm64,x64}.dmg` |
| 冒烟测试 | 挂载 dmg、起 app、`curl /api/health` |
| `upload-artifact` | 产物在 Actions 页面下载 |
| tag 时自动挂 Release | 打 `v*` 标签（如 `v1.1.0`）就出一条 Release |

**触发方式**：push 到 `main` 即同时构建双架构（公开仓库 macOS runner 免费）；也可在 GitHub 页面 → Actions → build-desktop-macos → Run workflow；打 `v*` 标签会额外把两个 DMG 挂到 GitHub Release。

> **前提**：本机需要该 GitHub 仓库的写权限凭据（PAT 或已登录的 gh）；否则无法 push/触发 Actions。没有 GitHub 凭据时，可改用 §2.5 的 Mac 手工构建。

### 2.3 测试者怎么装

```
1. 下载 Sanitized_Data-<version>-arm64.dmg（M 系列芯片）或 -<version>-x64.dmg（Intel）
2. 双击打开，把「数据隐站」拖到 Applications
3. 首次打开被拦时的两种解法：

   方法一（推荐）：在「应用程序」里右键点图标 → 打开 → 再点「打开」
   方法二（终端）：xattr -dr com.apple.quarantine /Applications/Sanitized_Data.app
```

装好之后就和正式版没区别，全功能可用、离线可用。

### 2.5 手工构建（有 Mac 时）

没有 GitHub Actions 凭据时，可在一台 macOS 机器上直接构建（需 Xcode Command Line Tools）：

```bash
bash desktop/build_mac.sh
```

脚本会：读取 `config.py` 的 VERSION → PyInstaller 打 `.app` → ad-hoc 签名 →
`hdiutil` 出 DMG（`Sanitized_Data-<version>-<arch>.dmg`）→ 启动 app 用
`.github/scripts/smoke_check.py` 断言「插件齐全 + 预览=成品字段在位」。

### 2.4 什么时候必须买账号

| 场景 | 需要账号 |
|---|---|
| 自己/同事内部测试 | 不需要 |
| 发给外部客户试用 | 建议买（否则第一印象是"来源不明"） |
| 正式对外分发 | 必须（否则用户要手动绕 Gatekeeper，很多人会放弃） |
| 自动更新 | 必须 |

买了之后只需要在 workflow 里加三步：导入证书 → `codesign` 换成正式证书 → `notarytool submit` + `stapler staple`。

---

## 三、当前进度

| 阶段 | 状态 |
|---|---|
| P0 启动器（`desktop/main.py`） | ✅ 完成，已实测：服务起得来、数据落 AppData、单实例可用 |
| P0 路径适配（`yinzhan/paths.py`） | ✅ 完成，三种形态（源码 / 桌面 / 环境变量覆盖）都验证过 |
| P1 PyInstaller 打包 | ✅ 完成（v1.1.0，2026-10-21）：`dist\Sanitized_Data\` 已重建，记录见 §五 |
| P2 Inno Setup 安装包 | ✅ 完成（v1.1.0，2026-10-21）：`dist_installer\Sanitized_Data_Setup_1.1.0_x64.exe`，记录见 §五 |
| P3 macOS 免签名测试包 | ✅ 完成（v1.1.2，2026-10-21）：GitHub Actions `macos-15`（arm64）+ `macos-15-intel`（Intel）双架构构建成功，ad-hoc 签名 + DMG + 冒烟检查全过；`Sanitized_Data-1.1.2-{arm64,x64}.dmg` 已挂 Gitea Release |
| P4 正式签名公证 | ⏸ 需要 Apple 开发者账号 |
| P5 托盘 / 开机自启 | ⏸ 未开始（窗口 + 自动浏览器兜底、单实例已实现） |

> §四 是 v1.0.0 的首次构建记录，保留作历史对照；v1.1.0 构建记录见 §五。

---

## 四、第一次真实构建记录（v1.0.0，2026-10-19）

### Windows

| 项 | 结果 |
|---|---|
| 构建机 | Windows 10/11 x64，Python 3.12 |
| PyInstaller | 6.22.3 |
| Inno Setup | **7.1.0**（语法与 6 基本兼容，脚本已适配两版） |
| 首次产物体积 | ❌ **1058 MB**（失控） |
| 修正后体积 | ✅ **314.7 MB** 解压目录 / **144.7 MB** 绿色包 / **134.1 MB** 安装包 |

**踩到的三个坑（都已修）**

1. **体积失控到 1 GB**
   `collect_all("rapidocr")` 顺着可选依赖把环境里的 `torch`（253 MB）、`jaxlib`（221 MB）、`llvmlite`（102 MB）全扫了进来。
   修复：`excludes` 里显式排除这些以及 scipy/sklearn/pandas/tensorflow 等。

2. **打包后 0 个解析器**
   `spec` 里 `collect_submodules("yinzhan")` 在 spec 求值阶段执行，那时 `app/server` 还不在 `sys.path` 上 → 收集结果为空 → 插件模块没进包。
   修复：spec 顶部先 `sys.path.insert(0, str(APP / "server"))`；同时 `registry.py` 加了静态插件清单兜底（`pkgutil` 在冻结环境里扫不到目录）。

3. **AppData 不可写导致启动即崩**
   受限机器上 `%LOCALAPPDATA%` 可能拒绝写入。`paths.py` 现在会做**可写性探测**并自动降级：
   用户目录 → 程序目录 → 临时目录。`config.py` 建目录失败也不再直接抛异常。

**验证结果**（绿色版解压后直接跑）

```
✓ 启动成功，OCR 就绪，解析器 docx/image/mesh/pdf/text/tiff/xlsx
✓ 住院病历.docx    9 实体   0.4s   复检残留 0
✓ 对账单.xlsx     13 实体   0.2s   复检残留 0
✓ 名片.png         6 实体   2.5s   复检残留 0
✓ 样例合同.pdf      12 实体  12.0s   复检残留 0
✓ 样例函件.pdf      13 实体   8.0s   复检残留 0
```

安装包在无交互桌面会话里无法完成 GUI 安装流程，**需在真实桌面上双击验证**；
安装包本体编译通过（Inno 7，67 秒，134.1 MB）。

### macOS（v1.1.2，2026-10-21）

| 项 | 结果 |
|---|---|
| 构建机 | GitHub Actions：`macos-15`（arm64）+ `macos-15-intel`（x64） |
| 产物 | `Sanitized_Data-1.1.2-arm64.dmg`（204,563,156 B）/ `-x64.dmg` |
| 签名 | ad-hoc（`codesign --force --deep --sign -`），未公证，首次打开需右键→打开 |
| 冒烟 | 挂载 DMG → 启动 .app → `/api/health`：7 解析器 / 6 检测器 / OCR 就绪 / `version=1.1.2` / `preview_parity=export-parity/v1`，两架构均通过 |
| 包内校验 | 7-Zip 解包核对：`Info.plist` CFBundleVersion=1.1.2、Mach-O 架构正确、`web/logo.svg`（金粒图标）在包、8 个规则包齐全 |

### 历史（v1.0.0）macOS 构建记录

arm64 包一次构建成功（127.8 MB DMG），但当时同样存在「0 个解析器」的问题，
冒烟测试只检查了 `/api/health` 返回 200，没有检查解析器数量。
**已修正**：workflow 的冒烟测试现在会断言 `parsers >= 5`、`detectors >= 3`、`ocr == True`，
下次构建会自动拦下这类问题。

---

## 五、v1.1.0 重建记录（2026-10-21）

> 构建内容：P1-A / P1-B / P1.5 能力与 P0 缺陷修复合入后的第一个正式安装包；版本号统一升到
> `1.1.0`（`config.py` / `app.spec` / `installer.iss` / `build_win.ps1` / Web 页面）。
> 代码基线：`7c553ab`；发布提交：tag `v1.1.0`。本次构建同时包含发布前修复：
> R04 地址假值泄漏、R13 金额验证假阳性、Windows 端口冲突检测、OpenCV Haar 级联数据打包。

| 项 | 结果 |
|---|---|
| 版本 | `1.1.0` |
| 构建时间 | 2026-10-21 12:39–12:46（Asia/Shanghai，含源码包/下载页的最终构建） |
| 构建机 | Windows 10 Pro 19045 x64，Python 3.12.9 |
| 构建命令 | `powershell -ExecutionPolicy Bypass -File desktop\build_win.ps1` |
| PyInstaller | 6.22.3（onedir；含 `yinzhan` 子模块 44 个、cv2 数据文件 110 个） |
| Inno Setup | 7.1.0（编译耗时 118s） |
| 绿色版目录 `dist\Sanitized_Data\` | **422.7 MiB**（解压后） |
| 绿色版压缩包 `out\desktop\Sanitized_Data-1.1.0-win64-portable.zip` | **191,606,234 B（182.7 MiB）** / SHA-256 `0D0AC503A352E3076A05B5B28189FA9F0FFA208BEEFBE60018E53D8A0416B2AD` |
| 安装包 `dist_installer\Sanitized_Data_Setup_1.1.0_x64.exe` | **168,594,482 B（160.8 MiB）** / SHA-256 `64FF299C936B94A06F4BC7EACFC33BD5E071E646AAB91B9CF207F449016734CB` |

### 5.1 打包后自检

| 检查项 | 期望 | v1.1.0 实测 |
|---|---|---|
| `/api/health` → `version` | `1.1.0` | ✅ `1.1.0`（独立端口 18760 + 临时数据目录） |
| `/api/health` → `engine.ocr` | `true` | ✅ `true` |
| `/api/health` → `engine.parsers` | ≥ 7（docx/image/mesh/pdf/text/tiff/xlsx） | ✅ 7 个全部加载 |
| `/api/health` → `engine.scanners` | 含 `barcode` 等 | ✅ `barcode,visual_targets,sign_targets` |
| 最小脱敏链路（解析 → 导出 → 复检残留 0） | 通过 | ✅ 安装版实测：上传→双导出（AI/人读）`verify residual=0` |
| silent 安装到临时目录 → 启动 → 卸载 | 通过 | ✅ 安装成功（HKCU 卸载项 1.1.0）→ 启动/health/导出通过 → 卸载 exit 0、程序目录删除、数据目录保留 |

> 安装/卸载与最小链路的完整记录见 [qa/install-acceptance-v1.1.0.md](qa/install-acceptance-v1.1.0.md)。
> 环境提示：本机曾在 DSH 工作区内直接执行安装包时 Setup 初始化退出（exit 1），把安装包复制到
> 普通目录后安装成功——判断为工作区历史沙箱 ACL 残留影响，不是安装包缺陷；正式分发从 Release
> 下载到普通目录双击即可。

---

## 六、v1.1.2 重建记录（2026-10-21）

> 构建内容：真实测试集 QA §7 的 P2/P3 五项优化（扫描件 verify 零 OCR 快检、warning 去重、
> 整页证件收紧、异常大码框待确认、TIF rasterio Orientation 修复）；版本号统一升到 `1.1.2`
> （`config.py` / `app.spec` / `installer.iss` / `build_win.ps1` / Web 页面）。
> 代码基线：`main` 上 tag `v1.1.2`。

| 项 | 结果 |
|---|---|
| 版本 | `1.1.2` |
| 构建时间 | 2026-10-21 13:48–13:51（Asia/Shanghai，最终构建含下载页更新） |
| 构建机 | Windows 10 Pro 19045 x64，Python 3.12.9 |
| 构建命令 | `powershell -ExecutionPolicy Bypass -File desktop\build_win.ps1` |
| PyInstaller | 6.22.3（onedir；含 `yinzhan` 子模块、cv2 数据文件 110 个） |
| Inno Setup | 7.1.0 |
| 绿色版目录 `dist\Sanitized_Data\` | 约 422.8 MiB（解压后） |
| 绿色版压缩包 `out\desktop\Sanitized_Data-1.1.2-win64-portable.zip` | **191,689,909 B（182.8 MiB）** / SHA-256 `7058FFCD46DD221A1F1D7DF271D380F6ED342CA2FB28C7EADDE2B8B53D05DC72` |
| 安装包 `dist_installer\Sanitized_Data_Setup_1.1.2_x64.exe` | **168,691,705 B（160.9 MiB）** / SHA-256 `937FEAE026F7570C02BEF31C9E7A4832A105621ECF12C5EC9224F8E7ED02711F` |

### 6.1 打包后自检

| 检查项 | 期望 | v1.1.2 实测 |
|---|---|---|
| `/api/health` → `version` | `1.1.2` | ✅ `1.1.2`（独立端口 18760 + 临时数据目录） |
| `/api/health` → `engine.ocr` | `true` | ✅ `true` |
| `/api/health` → `engine.parsers` | ≥ 7（docx/image/mesh/pdf/text/tiff/xlsx） | ✅ 7 个全部加载 |
| `/api/health` → `engine.scanners` | 含 `barcode` 等 | ✅ `barcode,visual_targets,sign_targets` |
| 构建脚本退出码 | 0 | ✅ 0（含 PyInstaller、Inno 编译、绿色版压缩） |

> 本版构建沿用 v1.1.0 的安装/卸载验收结论（安装器仅版本号变化，未改安装逻辑）；
> 正式分发仍从 Release 下载到普通目录双击安装。

---

## 七、v1.2.0 构建记录（2026-10-23）

> 构建内容：MCP L2 产品体验（15 工具/资源/深链/DSH 工具卡/右侧检测面板）、
> `Sanitized_Data_MCP.exe` 双入口、`dsh-plugin/` 插件、身份证校验位异常兜底；
> 版本 `1.2.0`（`config.py` / `app.spec` / `installer.iss` / Web 页面统一）。

| 项 | 结果 |
|---|---|
| 版本 | `1.2.0` |
| 构建命令 | `powershell -ExecutionPolicy Bypass -File desktop\build_win.ps1` |
| 构建自检 | `/api/health` version=1.2.0、OCR=True、7 解析器、扫描器齐全 |
| 绿色版目录 | `dist\Sanitized_Data\`（含 `Sanitized_Data.exe`、`Sanitized_Data_MCP.exe`、`dsh-plugin/`） |
| 安装包 | **187,709,481 B（179.0 MiB）** / SHA-256 `94F3F83D935987090C93EF35964A312F18FF8927CA7D12F42C518908C7305481` |
| 绿色版 | **211,184,187 B（201.4 MiB）** / SHA-256 `A53BD79DE306E5A3BC985ACF3FA14EB23748C1BE3BE83CD9ACC08ECEB9E7813D` |

> 发布：2026-10-23 已创建 Gitea Release v1.2.0（https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases/tag/v1.2.0），安装包/绿色版/源码包/SHA256SUMS 已上传。
> 安装验收（2026-10-23，从工作区外临时目录静默安装）：exit 0 → 启动 health
> version=1.2.0/OCR=True/扫描器齐全 → 卸载 exit 0、程序目录删除；
> 安装包内确认含 `dsh-plugin/` 与 `Sanitized_Data_MCP.exe`。
> DSH 插件需用户运行 `{app}\dsh-plugin\install.ps1 -Profile desktop` 并重启 DSH。

### 7.1 源码包（随 v1.2.0 Release 上传）

| 文件 | 大小 | SHA-256 |
|---|---|---|
| `Sanitized_Data-1.2.0-win64.zip` | 4,372,403 B | `097E8C3AF0FB8FD8428A90BC98524988BA26BECA1E238D30BE721F7F717CDC68` |
| `Sanitized_Data-1.2.0-linux64.tar.gz` | 4,194,344 B | `87B9070B40F1B032481E639005A41A44A44E8DFC860ED164CF4FC68403E12BFD` |

> 最终构建 2026-10-23 19:58（Asia/Shanghai）：已含 T2 原生 browser tab、自动拉起工作台、
> 去仿制面板、install.ps1 自动 MCP client 配置；安装/卸载 smoke（工作区外）通过。

---

## 八、v1.2.1 构建记录（2026-10-23）

> P1 补丁：能力感知 MCP elicitation（human 导出）、一次性 confirm_token 兜底、
> 工具卡图片缩略图/最近 Job footer、install.ps1 自动 MCP 配置、工作台自动拉起。
> macOS 本环境无法构建（无 Mac/无 GH_TOKEN），仅 Windows/Linux 产物。

| 项 | 结果 |
|---|---|
| 版本 | `1.2.1` |
| 构建命令 | `powershell -ExecutionPolicy Bypass -File desktop\build_win.ps1` |
| 构建自检 | `/api/health` version=1.2.1、OCR=True、7 解析器、扫描器齐全 |
| 安装包 | **187,706,160 B（179.0 MiB）** / SHA-256 `CDAD54068520099216D76742AE8ACF0E9CEC573FC5482EA1DF82E64BDBE45B4A` |
| 绿色版 | **211,194,458 B（201.4 MiB）** / SHA-256 `B1B8A5B602CD448D4FB9682DAF2A069BA5D9E4074D1063988C5F4601DEEF9DA1` |
| 插件 | 含原生 browser tab、自动拉起、footer 最近 job、工具卡缩略图、install 自动 MCP |

### 8.1 源码包

| 文件 | 大小 | SHA-256 |
|---|---|---|
| `Sanitized_Data-1.2.1-win64.zip` | 4,376,355 B | `3BE3D4CF36D09551580D24ADB62156CF425AC37954D8DB072C5F9C309ACF1A0E` |
| `Sanitized_Data-1.2.1-linux64.tar.gz` | 4,197,419 B | `A9410E3C5354DFBECE4C9D9ED346A372A30B603A2910426CD97938FA923BD5B6` |

> 发布：2026-10-23 已创建 Gitea Release v1.2.1（Windows/Linux；macOS 因无构建环境未发布）：
> https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases/tag/v1.2.1
