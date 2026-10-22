# 安装与卸载指南（v1.1.2）

> 适用版本：**v1.1.2**（Windows 10/11 x64 桌面版；macOS arm64 / Intel DMG；Linux 走源码运行）
> 更新日期：2026-10-21
> 相关：[v1.1.2 发布说明](release-notes-v1.1.2.md) · [格式与能力口径表](support-matrix.md) · [构建文档](desktop-build.md) · [项目 README](../README.md)

数据隐站是**本机处理**工具：引擎只监听 `127.0.0.1`，不发起任何外呼。除源码方式首次安装 Python 依赖外，安装完成后的全部功能**断网可用**。

---

## 0. 选哪种安装方式

| 方式 | 适合谁 | 需要 Python | 数据目录 |
|---|---|---|---|
| **Windows 安装包（推荐）** | 绝大多数 Windows 用户 | 不需要（运行时已打包） | `%LOCALAPPDATA%\Sanitized_Data` |
| **Windows 绿色版** | 不想安装、便携使用、受限电脑 | 不需要（运行时已打包） | `%LOCALAPPDATA%\Sanitized_Data` |
| **源码运行** | Linux / macOS / 服务器部署 / 开发排查 | 需要 Python 3.10+ | 项目目录下的 `data/` |

---

## 1. 安装（Windows 安装包）

### 1.1 系统要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 x64 |
| 磁盘 | 建议预留 **400 MB** 以上（程序约 315 MB + 处理临时文件） |
| 权限 | **不需要管理员**，默认装到当前用户目录 |
| Python | **不需要**（已内置） |
| 网络 | 安装完成后断网可用；WebView2 / VC++ 缺失时安装包会尝试静默补齐 |

### 1.2 步骤

1. 从发布页下载 `Sanitized_Data_Setup_1.1.2_x64.exe`，建议先核对发布页给出的 SHA-256（校验命令见 [发布说明](release-notes-v1.1.2.md) 的「校验值」章节）。
2. 双击运行。安装向导会先弹出一页「环境检查」，列出 WebView2 / VC++ 的检测结果与安装位置。
3. 点「安装」：程序文件释放到 `%LOCALAPPDATA%\Programs\Sanitized_Data`，同时创建开始菜单快捷方式（桌面快捷方式为可选勾选，默认不创建）。
4. 缺 WebView2 / VC++ 时安装包会自动静默安装；**装不上不阻断安装**，程序会改用默认浏览器打开界面，功能不变。
5. 安装完成后勾选「立即启动」，首次启动会加载解析器与 OCR（约 2 秒），然后打开原生窗口（WebView2 缺失时为默认浏览器）。

### 1.3 验证安装成功

- 界面底部显示 `数据隐站 · v1.1.2`；
- 或浏览器访问 `http://127.0.0.1:8760/api/health`（端口被占时见 `runtime.json`），返回 JSON 中 `"version": "1.1.2"`、`"engine.ocr": true`；
- 控制台可正常拖入 `samples/` 里的虚构样例并导出。

### 1.4 静默安装（IT / 脚本）

```powershell
Sanitized_Data_Setup_1.1.2_x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER
# 指定目录：
Sanitized_Data_Setup_1.1.2_x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /DIR="D:\Apps\Sanitized_Data"
```

---

## 2. 安装（Windows 绿色版）

1. 下载 `Sanitized_Data-1.1.2-win64-portable.zip`，解压到**可写目录**（不要直接放在压缩包里运行）。
2. 运行目录内的 `Sanitized_Data.exe`。
3. 数据仍然落在 `%LOCALAPPDATA%\Sanitized_Data`（与安装版共用），所以绿色版可以整体移动/复制而不丢数据；卸载 = 直接删除解压目录（数据目录按 §5 处理）。

> 绿色版没有开始菜单/卸载项；首次运行 SmartScreen 提示的处理见 §8。

---

## 3. 源码运行（Linux / macOS / 开发）

### 3.1 Windows / Linux / macOS

```bash
# Windows：双击「启动.bat」或 start.bat
# Linux / macOS：
chmod +x start.sh && ./start.sh
```

首次运行前安装依赖（需要联网或本地 wheelhouse）：

```bash
pip install -r app/server/requirements.txt
```

- Python 版本：**3.10+**（`start.sh` 会检查，过低会直接提示）。
- 浏览器会自动打开 `http://127.0.0.1:8760`（端口被占自动顺延到 8839）；`Ctrl+C` 退出。
- 源码运行的数据目录是项目下的 `data/`，与桌面版不同。
- 服务器部署（systemd / nginx 反代）见 [README「服务器部署」](../README.md#服务器部署)。
- 离线安装：`requirements.txt` 里的 rapidocr / zxing-cpp / rasterio / pyproj 均带 Windows wheel，可先在有网机器 `pip download` 后用 `--no-index --find-links` 安装；运行时不联网。

### 3.2 MCP 接入

`app/server/mcp_server.py` 是 stdio MCP 服务，供 Claude Desktop / Cursor 等 Agent 调用；配置见界面 `/mcp` 页面或 [README「MCP 接入」](../README.md#mcp-接入)。

---

## 4. 数据位置与保留策略

### 4.1 安装版 / 绿色版

| 内容 | 默认路径 |
|---|---|
| 程序目录 | `%LOCALAPPDATA%\Programs\Sanitized_Data`（安装版） |
| 数据根目录 | `%LOCALAPPDATA%\Sanitized_Data` |
| 上传副本 / 工作目录 | `<数据目录>\uploads\`、`<数据目录>\work\`（**退出时自动清空**） |
| 导出成品 | `<数据目录>\out\`（保留，用户主动交付的文件） |
| 加密映射库 / 密钥 | `<数据目录>\keys\`、`<数据目录>\yinzhan.db` |
| 运行信息 | `<数据目录>\runtime.json`（端口 / URL / 版本 / PID / 日志路径） |
| 服务日志 | `<数据目录>\logs\server.log`（按日期翻转为 `server-YYYY-MM-DD.log`，默认保留最近 30 个、约 30 天） |
| 诊断文件 | `<数据目录>\diagnose.txt`（运行 `--diagnose` 后生成） |

- macOS 桌面版（v1.1.2 起提供 arm64 / Intel DMG）对应 `~/Library/Application Support/Sanitized_Data`。
- 数据目录可覆盖：设置环境变量 `YINZHAN_DATA_DIR`；导出目录可单独用 `YINZHAN_OUTPUT_DIR` 覆盖。
- 服务日志默认在 `<数据目录>\logs\`，每天 0 点后按日期翻转（由当日首条日志触发）；服务器端排查见 [README「日志与状态监控」](../README.md#日志与状态监控)。
- 安装版/绿色版**程序目录与数据目录分离**，替换程序不影响数据。

### 4.2 源码运行

数据在项目目录下：

```
<项目>\data\
├─ uploads/  work/     临时（退出清空）
├─ out/                导出成品
├─ keys/  yinzhan.db   加密映射库 / 审计库
├─ logs/               服务日志（server.log + 按日期归档）
├─ runtime.json        端口 / URL / 日志路径
└─ server.log          旧版单文件日志（启动时自动迁移到 logs/，可删除）
```

> 映射库用于「换名版」还原，密钥留在本机；**不要**把 `data/keys` 或 `yinzhan.db` 发给别人。审计库只记类型/数量/文件哈希，不记内容原值。

---

## 5. 卸载

### 5.1 安装版

1. 退出程序（确保窗口关闭、任务栏无残留进程）。
2. 「设置 → 应用 → 已安装的应用」找到「数据隐站」→ 卸载；或开始菜单 →「数据隐站」→「卸载 数据隐站」。
3. 卸载会删除程序目录（`%LOCALAPPDATA%\Programs\Sanitized_Data`）与开始菜单项；服务日志在数据目录 `logs/` 下，不随卸载删除。
4. **数据目录 `%LOCALAPPDATA%\Sanitized_Data` 不会被删除**（保留映射库/审计库/成品）。

彻底清理（确认不再需要历史映射与成品后）：

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Sanitized_Data"
```

静默卸载：

```powershell
& "$env:LOCALAPPDATA\Programs\Sanitized_Data\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

### 5.2 绿色版

直接删除解压出来的程序目录即可；数据目录按 §4 处理。

---

## 6. 升级（旧版 → v1.1.2）

1. 退出正在运行的程序。
2. 建议备份数据目录（复制一份 `%LOCALAPPDATA%\Sanitized_Data`）。
3. 运行 v1.1.2 安装包覆盖安装（同一 AppId，会升级到原来的程序目录）；绿色版解压新版到任意目录运行即可。
4. 启动后核对版本（§1.3）。
5. **数据目录结构不变**：加密映射库（换名还原）、密钥、审计链均沿用，旧版导出的成品仍在 `out/`。

> 说明：v1.1.2 与 v1.0.0 使用同一数据目录与库结构；升级前备份是保险动作，不是必需步骤。

---

## 7. 断网使用

- 桌面版（安装包 / 绿色版）安装完成后**全程可断网**：OCR 模型、条码解码、规则包、Web 资源都随包分发，运行时不下载任何东西。
- 引擎只监听 `127.0.0.1`，没有外呼代码；`/api/health` 返回 `"net": "blocked"`。
- 源码方式首次 `pip install` 需要网络（或用离线 wheelhouse）；之后运行同样断网可用。
- 内网/隔离网分发：把安装包与 `SHA-256` 一起拷贝进去即可。

---

## 8. 常见问题排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| SmartScreen 提示「Windows 已保护你的电脑」/「未知发布者」 | v1.1.2 **未做代码签名** | 点「更多信息」→「仍要运行」；先核对发布页 SHA-256。正式签名在后续版本 |
| 杀软报毒/拦截 | PyInstaller 打包程序常见误报 | 添加信任/白名单；核对 SHA-256；必要时提交误报申诉 |
| 双击安装包没反应 | 安装包不完整 / 被拦截 | 核对 SHA-256 后重新下载；看 Windows 事件/杀软记录 |
| 启动后没有独立窗口，开了浏览器 | WebView2 缺失或安装失败 | 属预期兜底，功能完全一样；想要独立窗口可手动安装 [WebView2 运行时](https://developer.microsoft.com/microsoft-edge/webview2/)。此模式下没有主窗口，退出需在任务管理器结束 `Sanitized_Data.exe`（或先补装 WebView2） |
| 双击 `Sanitized_Data.exe` 没反应/闪退 | 启动失败 / 上次实例残留 / 数据目录不可写 | 看 `<数据目录>\logs\server.log`；在程序目录运行 `Sanitized_Data.exe --diagnose`，把生成的 `diagnose.txt` 发给支持；任务管理器结束残留进程后重试 |
| 提示「已经在运行」 | 单实例机制：再次双击会激活已有实例 | 找已打开的窗口/浏览器标签页；无响应则结束进程后重试 |
| 浏览器显示「本机服务未连接」 | OCR 首次加载中 / 服务未起 | 等 2–5 秒刷新；仍不行看 `<数据目录>\logs\server.log`，或源码运行 `python app/server/status.py` |
| 端口被占 | 8760 起被占用 | 自动顺延（8760–8839），实际端口看 `<数据目录>\runtime.json`；也可设 `YINZHAN_PORT` |
| 界面显示版本不是 v1.1.2 | 运行的是旧程序 / 覆盖安装未完成 | 确认启动的是新安装目录的 exe；重装 v1.1.2 |
| 数据目录不可写 / 启动即崩 | 企业受限环境 AppData 权限 | 程序会自动降级到程序目录或临时目录；也可显式指定 `YINZHAN_DATA_DIR` 到可写目录 |
| 中文路径下异常 | 路径编码/杀软干扰 | 支持中文路径；如遇异常可先换短英文路径排查 |
| 卸载后数据还在 | **预期行为**：数据目录不随卸载删除 | 确认不需要历史映射/成品后，手动删除 `%LOCALAPPDATA%\Sanitized_Data` |
| OCR 不可用（源码运行） | 依赖未装全 / 模型缺失 | 重装 `pip install -r app/server/requirements.txt`；看启动日志中的 `OCR 不可用` 提示 |
| 扫描件慢 | 无文本层，逐页离线 OCR（PP-OCRv6 约 2.1 秒/页） | 属正常性能；可调 `YINZHAN_OCR_MAX_SIDE`（调小更快） |

---

## 9. 环境变量（排障/定制）

| 变量 | 默认 | 作用 |
|---|---|---|
| `YINZHAN_DATA_DIR` | `%LOCALAPPDATA%\Sanitized_Data`（桌面版）/ 项目 `data`（源码） | 覆盖数据根目录 |
| `YINZHAN_OUTPUT_DIR` | `<数据目录>\out` | 覆盖导出成品目录 |
| `YINZHAN_PORT` | 自动 8760 起 | 指定监听端口 |
| `YINZHAN_DESKTOP` | 打包运行时自动置 1 | 源码方式模拟桌面版数据目录 |
| `YINZHAN_ALLOWED_ORIGINS` | 空 | 服务器反代场景显式放行来源（见 README） |
| `YINZHAN_LOG_DIR` | `<数据目录>\logs` | 日志目录（按日期翻转） |
| `YINZHAN_LOG_LEVEL` | `INFO` | 日志级别（排障可设 `DEBUG`） |
| `YINZHAN_LOG_KEEP_DAYS` | `30` | 历史日志按日归档保留个数（约 30 天）；0 = 不清理 |
| `YINZHAN_LOG_REQUESTS` | `1` | 0 = 关闭逐请求日志 |
| `YINZHAN_LOG_HEARTBEAT_SEC` | `300` | 心跳间隔秒数；0 = 关闭 |
| `YINZHAN_LOG_CLIENT_IP` | `0` | 1 = 请求日志记客户端 IP |
| `YINZHAN_LOG_STDERR` | 自动 | 0 = 关闭控制台同步输出 |
| `YINZHAN_TIFF_GEO_MODE` | `neutralized` | TIF 坐标处理：`neutralized`（LOCAL_CS）或 `strip`（彻底去参考） |

> 命令行：`Sanitized_Data.exe --diagnose` 生成诊断文件；源码运行还支持 `--port` / `--host` / `--no-browser` / `--no-ocr`。

---

## 10. 获取安装包与校验

| 产物 | 位置 |
|---|---|
| 安装包 `Sanitized_Data_Setup_1.1.2_x64.exe` | 发布页（Gitea Release）/ 构建机 `dist_installer/` |
| 绿色版 `Sanitized_Data-1.1.2-win64-portable.zip` | 发布页 / 构建机 `out/desktop/` |
| 源码包 `Sanitized_Data-1.1.2-win64.zip` / `-linux64.tar.gz` | 已从站点 `/download` 下架；内部仓库 / Release 历史附件可获取 |
| macOS `Sanitized_Data-1.1.2-arm64.dmg` / `-x64.dmg` | 发布页（Gitea Release）/ 构建产物 out/desktop/macos/ |

校验（PowerShell）：

```powershell
Get-FileHash .\Sanitized_Data_Setup_1.1.2_x64.exe -Algorithm SHA256
```

体积与 SHA-256 以 [v1.1.2 发布说明](release-notes-v1.1.2.md) 的「校验值」章节 为准。
