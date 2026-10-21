# 桌面版规划 · Windows / macOS

> **历史规划文档（2026-10-19，产物名已按 v1.1.0 更新）。** 本文保留立项时的目标、路线与工作量估算；
> 实际落地状态、构建产物与体积/SHA 以 [desktop-build.md](desktop-build.md) 和
> [release-notes-v1.1.0.md](release-notes-v1.1.0.md) 为准。

> 目标：双击即用、托盘常驻、数据存放在用户目录、全程离线。
> 现状是「本地 HTTP 服务 + 浏览器」，已经满足"数据不出本机"，缺的只是一个像样的分发形态。

---

## 0. 结论先行

| 项 | 结论 |
|---|---|
| 技术路线 | 保留 Python 引擎，PyInstaller 打自包含包，外套 pywebview 原生窗口 |
| 窗口外壳 | pywebview（复用系统 WebView，不额外带 Chromium） |
| Windows 产物 | `Sanitized_Data_Setup_1.1.0_x64.exe`（Inno Setup） |
| macOS 产物 | `Sanitized_Data-1.1.0-arm64.dmg` + `Sanitized_Data-1.1.0-x64.dmg`（需分别构建） |
| 体积 | 立项估算：安装包约 90–110 MB，安装后约 330–380 MB（实测见 [desktop-build.md](desktop-build.md)） |
| 总工作量 | 10–12 人日（不含苹果账号申请周期） |
| **阻断项** | **macOS 正式分发必须做代码签名 + 公证**，需要 Apple Developer Program（$99/年）；没有账号只能出未签名包，用户要右键打开 |

---

## 1. 为什么不是 Electron / Tauri

| 方案 | 体积 | 复杂度 | 结论 |
|---|---|---|---|
| Electron | +150 MB（自带 Chromium + Node） | 中，要维护两套运行时 | 否 |
| Tauri | 外壳很小，但 Python 仍需 sidecar 打包 | 高，Rust 工具链 + 跨平台构建 | 否 |
| **pywebview** | **≈ 0**（Windows 用 WebView2，macOS 用 WKWebView） | 低，纯 Python | **选它** |
| 默认浏览器 | 0 | 最低 | 作为兜底保留 |

pywebview 的关键优势：它调用系统已有的 WebView 组件，不打包浏览器内核。
Win10/11 自带 Edge WebView2 运行时（2022 年后基本全覆盖），macOS 自带 WKWebView。

---

## 2. 目标形态

```
双击图标
   ↓
原生窗口打开控制台（没有地址栏、没有标签页）
   ↓
托盘常驻 → 右键「打开界面 / 打开数据目录 / 退出」
   ↓
退出时清理上传副本与预览中间件（沿用现有"不留存"逻辑）
```

加分项（P5 阶段）：

- 把文件拖到应用图标上直接打开
- 开机自启（托盘设置里开关）
- 单实例：再次双击时激活已有窗口，而不是起第二个服务

---

## 3. 打包方案

### 3.1 Windows

```
PyInstaller --onedir
    ↓  dist/Sanitized_Data/
Inno Setup
    ↓  Sanitized_Data_Setup_1.1.0_x64.exe
```

- **用 onedir 不用 onefile**：onefile 每次启动都要把 350 MB 解压到临时目录，冷启动 3–8 秒；onedir 直接加载，1 秒内出界面。
- **必须带 `--collect-all rapidocr --collect-all onnxruntime`**：这两个包有动态导入，PyInstaller 静态分析会漏。
- 安装到 `%LOCALAPPDATA%\Programs\Sanitized_Data`（不需要管理员权限）。
- 卸载时询问是否保留数据目录。

### 3.2 macOS

- **必须在 macOS 上构建**，PyInstaller 不支持交叉编译。用 GitHub Actions：
  - `macos-14` → arm64（Apple Silicon）
  - `macos-13` → x86_64（Intel）
- `onnxruntime` 没有 universal2 wheel，所以**两个架构分别出包**，不要试图做 universal2。
- 签名与公证流程：

```bash
codesign --deep --force --options runtime \
  --sign "Developer ID Application: <名字> (<TEAMID>)" \
  "dist/Sanitized_Data.app"

xcrun notarytool submit "Sanitized_Data.dmg" \
  --apple-id <apple-id> --team-id <TEAMID> --password <app-专用密码> --wait

xcrun stapler staple "Sanitized_Data.dmg"
```

- 没有开发者账号时：出未签名 DMG，文档里写清楚「右键 → 打开」绕过 Gatekeeper。

---

## 4. 代码改动（总量不大）

| 文件 | 改动 |
|---|---|
| `app/server/yinzhan/config.py` | 数据目录改为平台标准位置：Windows `%LOCALAPPDATA%\Sanitized_Data`，macOS `~/Library/Application Support/Sanitized_Data`；支持 `YINZHAN_DATA_DIR` 覆盖 |
| `app/server/yinzhan/paths.py`（新增） | 统一处理冻结路径：`sys._MEIPASS` / `sys.executable`，把 `web/`、`rules/`、OCR 模型定位到 bundle 内 |
| `desktop/main.py`（新增） | 入口：单实例锁 → 起服务（端口自动选）→ 起窗口 → 退出收尾 |
| `desktop/window.py`（新增） | pywebview 窗口 + 托盘菜单 |
| `desktop/app.spec` | PyInstaller 配置 |
| `desktop/build_win.ps1` | Windows 构建脚本 |
| `desktop/build_mac.sh` | macOS 构建 + 签名 + 公证脚本 |
| `desktop/installer.iss` | Inno Setup 脚本 |

**核心原则**：引擎（`app/server/yinzhan/`）一行不改，桌面化只是加了一层"启动器 + 分发"。

---

## 5. 分阶段路线

| 阶段 | 内容 | 工期 | 产出 |
|---|---|---|---|
| **P0** | `desktop/main.py` + pywebview 窗口，用系统 Python 跑通 | 1 天 | Windows 上双击出原生窗口 |
| **P1** | PyInstaller onedir 打包 + 首次运行自检（OCR 模型、端口、数据目录） | 2 天 | 免安装绿色版 zip |
| **P2** | Inno Setup 安装包、卸载、开始菜单、文件关联 | 1 天 | `_Setup_x64.exe` |
| **P3** | macOS `.app` + 本地 ad-hoc 签名（自用验证） | 2 天 | 未签名 DMG |
| **P4** | 开发者 ID 签名 + 公证 + 正式 DMG（需账号） | 2–3 天 | 可正式分发的 DMG |
| **P5** | 托盘、开机自启、拖拽打开、单实例完善 | 1 天 | 完整体验 |

P0–P2 可以马上开始，不依赖任何外部账号。

---

## 6. 风险与对策

| 风险 | 说明 | 对策 |
|---|---|---|
| 体积大 | 350 MB，用户可能嫌大 | OCR 模型占 30 MB、onnxruntime 15 MB、opencv 40 MB、PyMuPDF 25 MB、Python 运行时 20 MB。可通过去掉 opencv 的无关模块省 15 MB，但收益有限 |
| PyInstaller 漏模块 | rapidocr / onnxruntime 动态导入 | `--collect-all` + 打包后跑一次端到端自检 |
| macOS 公证失败 | 缺账号、缺专用密码 | 先用未签名 DMG 内部试用；正式分发前申请账号 |
| Windows SmartScreen | 未签名 exe 会提示"未知发布者" | 买 OV/EV 代码签名证书（¥1500–4000/年）；EV 可立即建立信誉 |
| 杀软误报 | 打包型 Python exe 常见误报 | 签名 + 提交误报申诉 |
| 自动更新 | 初期不做 | 启动时比对 `/api/health` 的版本号，有新版提示去 `/download` |
| 数据目录迁移 | 从安装目录改到 AppData | 首次启动检测旧路径并提示迁移 |

---

## 7. 验收标准

- 干净的 Windows 10 / 11（**没装 Python**）双击安装，60 秒内出现界面
- 拔网线，全部功能可用
- 处理演示文件（5 页扫描件）从拖入到出结果 < 30 秒
- 退出后 `%LOCALAPPDATA%\Sanitized_Data\uploads` 为空
- 卸载后残留仅数据目录（且可选清除）
- macOS 上：首次打开无 Gatekeeper 警告（P4 后）

---

## 8. 与 Web 版的关系

桌面版和 Web 版**共用同一套引擎和前端**，区别只在分发形态：

| 形态 | 场景 | 入口 |
|---|---|---|
| 桌面版 | 律师/医生个人电脑，双击即用 | `Sanitized_Data.exe` |
| Web 版（现有） | 内网服务器部署，多人在浏览器用 | `https://cka.blctek.com:8443` |
| MCP | Agent 自动调用 | `mcp_server.py` |

桌面版**不会**取代 Web 版：服务器部署适合团队和内网，桌面版适合单机和个人。
