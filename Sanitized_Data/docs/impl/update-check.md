# 数据隐站更新检查与一键更新（设计稿）

> 目标：发布新版本后，客户端能**发现更新 → 提醒 → 用户点“立即更新” → 自动下载/校验/安装/重启**；
> 用户点“稍后”则暂时忽略。**不做静默更新**，也不强制。

## 1. 先说当前阻塞：更新源不能直接用私有 Gitea

本机实测：

- `https://gitea.blctek.com:8443/.../releases/tag/v1.2.1` → 302 到 `/user/login`
- Gitea Release API → **403**（未登录）

所以客户端如果直接查当前 Gitea，无法检测更新。可选的更新源：

| 方案 | 可行性 | 说明 |
|---|---|---|
| **A. 静态更新清单**（推荐） | 高 | `latest.json` + 安装包放在内网/公网静态服务器/CDN；不需要 Gitea 登录 |
| B. Gitea 仓库/Release 开放匿名只读 | 中 | 需改仓库权限；要评估内部资料暴露 |
| C. 客户自带 Gitea 账号 | 低 | 客户需登录，体验差；只适合内部员工 |
| D. 内嵌只读 token | 低 | token 泄露/轮换/权限风险，不建议 |

**推荐 A**：发布时同时更新静态 `latest.json`，Gitea 只作为开发仓库/备份。

## 2. 推荐更新清单

`GET https://<updates-host>/sanitized-data/latest.json`

```json
{
  "version": "1.3.0",
  "tag": "v1.3.0",
  "release_url": "https://gitea.../releases/tag/v1.3.0",
  "notes": "更新内容摘要（纯文本/Markdown）",
  "published_at": "2026-10-24T10:00:00+08:00",
  "assets": {
    "windows_installer": {
      "name": "Sanitized_Data_Setup_1.3.0_x64.exe",
      "url": "https://<updates-host>/sanitized-data/Sanitized_Data_Setup_1.3.0_x64.exe",
      "size": 187706160,
      "sha256": "CDAD...B4A"
    },
    "windows_portable": {
      "name": "Sanitized_Data-1.3.0-win64-portable.zip",
      "url": "...",
      "size": 211194458,
      "sha256": "..."
    }
  }
}
```

要求：

- 清单 HTTPS；客户端只接受配置的更新源域名，不跟随清单里的任意外链；
- `sha256` 必填；下载后本地校验失败就删除，不执行；
- `release_url` 只用于“查看详情”，不允许直接执行；
- 客户端忽略 prerelease/draft；版本比较按数字段。

## 3. 用户流程

```text
启动 / 每 24h（仅用户开启自动检查时）
        │
        ▼
GET latest.json（短超时，失败静默）
        │
        ├─ 网络/证书/解析失败 → 不打扰；保留手动“检查更新”
        │
        └─ 新版本
              │
              ▼
        顶部提示：数据隐站 vX.Y.Z 可用  [立即更新] [稍后]
              │
              ├─ [立即更新]
              │     → 弹出更新说明/大小/是否继续
              │     → 下载安装包到 %TEMP%\Sanitized_Data_Update
              │     → 校验 size + SHA-256
              │     → 关闭主程序 → 运行安装包静默更新
              │     → 自动重启新版
              │
              └─ [稍后]
                    → 记录 dismissed_version + 24h
                    → 同版本 24h 内不再提示
```

## 4. 安装方式

### 4.1 安装版（主路径）

- 下载 `windows_installer`；
- 校验通过后，用安装包参数：
  `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /CURRENTUSER`
- 更新器 helper：
  1. 等待当前进程退出；
  2. 启动安装包；
  3. 安装完成后启动新 `Sanitized_Data.exe`；
  4. 清理临时文件。
- 数据目录 `%LOCALAPPDATA%\Sanitized_Data` 保留，不迁移。

### 4.2 绿色版

- 下载 `windows_portable`；
- 优先做成“解压到 `<当前目录>\update\<ver>`，提示用户下次从新目录启动”；
- 或先做“下载并校验，打开所在目录”作为过渡，**不静默替换正在运行的 exe**。

## 5. 设置与提醒

本地设置（`data/settings.json` 或现有配置）：

```json
{
  "update": {
    "auto_check": false,
    "last_check": 0,
    "dismissed_version": "",
    "dismissed_until": 0
  }
}
```

- 默认 **auto_check=false**；首次启动/设置页询问用户是否开启；
- 手动“检查更新”始终可用；
- “稍后”只影响该版本 24h；升级到更高版本会重新提示；
- 网络失败不弹错误，只在设置页显示“上次检查失败”和时间。

## 6. 隐私边界

启用自动检查后，客户端会向更新源发起 HTTPS 请求，更新源可看到 IP/时间/User-Agent。
**不上传任何文件/文本/实体/PII**。产品文案从“完全不联网”改为：

> 默认不联网；仅当用户开启更新检查时，访问更新源获取版本信息。

如果客户完全不能联网：保持关闭，不影响主功能。

## 7. 技术拆分

### 后端

- 新增 `app/server/yinzhan/update.py`：
  - manifest URL（默认空；配置后启用）
  - `check_update(current)`：HTTP(S) GET + 超时 + 版本比较 + 域名白名单
  - `download_asset(asset, dest)`：断点/进度可选，size + SHA-256 校验
  - `install_installer(path)`：spawn helper，不阻塞 HTTP
- API：
  - `GET /api/update/check`
  - `POST /api/update/download`
  - `POST /api/update/install`
  - `GET /api/settings/update` / `POST`（开关/稍后）
- 所有网络失败返回结构化错误，不抛出到页面；默认空 URL = 功能关闭。

### 前端

- 顶栏新增更新提示条（发现新版本时出现）；
- 设置页新增“自动检查更新 / 立即检查 / 上次检查时间”；
- 更新弹窗显示版本/大小/SHA-256 前 12 位/更新内容，按钮“立即更新/稍后”；
- 下载/安装过程显示进度与错误提示。

### 网络/证书

- 生产更新源使用受信任 HTTPS 证书；
- 若内部自签：配置企业 CA 或使用受信更新源；**不要关闭 SSL 校验**；
- Gitea 私有登录问题不进入客户端逻辑。

## 8. 发布流程增量

每次发版多一步：更新并上传 `latest.json`（先传安装包，再更新清单，避免客户端拿到死链）。

```text
build → tests → tag/Gitea Release → 上传安装包/绿色版/SHA256SUMS
      → 更新静态 latest.json（version/url/sha256/size）
      → 通知用户（可选）
```

客户端会自动发现；用户决定是否立即更新。

## 9. 需要确认的产品决策

1. **更新源放哪**：推荐静态 `updates.blctek.com`（或现有内网静态服务器/CDN）；是否可行？
2. **默认自动检查**：建议首次启动询问，默认关闭；是否接受？
3. **绿色版**：先做“下载并校验，打开目录”，还是一起做自动替换？
4. **安装包未签名**：Windows SmartScreen 仍可能提示；更新安装也会遇到，是否后续补代码签名？

确认后按 v1.3.0 实现：后端 manifest/下载/校验、前端提示与设置、安装 helper、测试。

## 10. 决策记录（2026-10-23）

- **更新源选择：B — 公司 Gitea 的独立公开 release-only 仓库。**
  - 不公开源码仓库；只建一个公开仓库（例如 `sanitized-data-updates`），
    仅放 Release 资产/更新清单，避免把源码暴露给客户。
  - 客户端检查该仓库的 `releases/latest`（匿名 GET）。
  - 若公司 Gitea 全局要求登录（当前 `REQUIRE_SIGNIN_VIEW` 导致 302/403），
    需要管理员允许匿名读取该公开仓库/Release，或改用 A 的静态 HTTPS 更新源。
- **A（CKA 静态服务器/CDN）**：技术上也完全可行，前提是能给客户提供稳定 HTTPS；
  但当前不需要额外基础设施，优先 B。
- **默认策略：首次启动询问一次，默认关闭自动检查；`稍后` 24 小时，不强制。**
- 若 B 的匿名访问无法开通，再切 A；客户端只依赖配置的清单 URL，不绑死 Gitea。

## 11. 2026-10-23 实测：update 仓库匿名访问仍被登录墙拦截

- 仓库 `baoluo/sanitized-data-updates` 已存在且 `private=false`；
- 未登录访问：
  - `/api/v1/repos/baoluo/sanitized-data-updates` → **403**
    `{"message":"Only signed in user is allowed to call APIs."}`
  - `/api/v1/.../releases` → **403**
  - 网页 `/sanitized-data-updates/releases` → **302 /user/login**
- 原因：Gitea 实例级 `REQUIRE_SIGNIN_VIEW`（未登录不能浏览），不是仓库本身私有。
- 解锁 B 需要管理员把 `REQUIRE_SIGNIN_VIEW=false`，或给该公开仓库做匿名只读白名单；
  否则只能走 A（CKA/静态 HTTPS 清单）。

## 12. 2026-10-23 复查：CKA 服务器可用，但还没有静态更新目录

- `https://cka.blctek.com:8443/`：200，nginx，TLS 正常（verify=True 也通过）；
- `/api/health`：200，当前部署产品版本 `1.2.1`；
- `/download`：200，但下载按钮仍指向 Gitea Release（未登录会进登录页）；
- `/updates/latest.json`、`/downloads/`：404；
- 结论：**A（CKA 静态更新源）方向可行**，但需要在 CKA 上有一个可写入的静态目录
  （例如 `/sanitized-data-updates/`）或提供部署/上传方式；
  否则无法放 `latest.json` 和安装包，客户端仍没有可用更新源。
- B（Gitea 公开仓库）仍被实例级“必须登录”拦截，暂不作为首选。

---

## 13 实现状态（2026-10-31）

- 已实现：updater.py + /api/update/* + 顶栏检查/更新弹窗；
- 更新方式：**绿色版 zip 自替换**（绕开当前 Inno 安装器问题），SHA-256/大小校验；
- 交互：启动后自动检查一次，只有用户点「立即更新」才下载；「稍后」24h；手动检查始终可用；
- 测试：	est_updater.py 5/5，API 本地 manifest 下载/校验/源码模式拒绝通过；
- 未完成：v1.3.4 构建、CKA latest.json 发布、安装器修复。

## 14. 平台隔离：Windows 只更新 Windows 包，macOS 只更新 macOS 包（v1.5.3，2026-11-11）

### 14.1 问题与根因

v1.5.2 的 `check()` 只查 `windows_portable` / `windows_installer`。一旦清单出现比当前版本更高的版本，
**macOS 客户端会看到 Windows 绿色版 zip**，点「立即更新」会下载 Windows 包，`install()` 还会调用
PowerShell helper（macOS 上必然失败）。根因不是清单字段缺失（`deploy/publish_update.py` 早已同时发布
`macos_arm64` / `macos_x64`），而是**客户端选包逻辑没有平台维度**。

### 14.2 清单契约（服务端发布的就是这一份）

| 资产键 | 平台/架构 | 文件类型 | 客户端行为 |
|---|---|---|---|
| `windows_portable` | Windows x64 | `.zip`（绿色版） | `auto`：应用内下载 → SHA-256 校验 → 自替换 → 重启 |
| `windows_installer` | Windows x64 | `.exe`（Inno 安装包） | `manual`：只给下载入口，不静默执行安装器 |
| `macos_arm64` | macOS Apple Silicon | `.dmg` | `manual`：只给 DMG 下载入口，提示拖入「应用程序」覆盖 |
| `macos_x64` | macOS Intel | `.dmg` | `manual`：同上 |

同一份 `latest.json` 同时带四个键；**客户端按平台各取所需，服务端不需要维护两份清单**。

### 14.3 客户端选择规则（`app/server/yinzhan/updater.py`）

- `_platform_id()`：`sys.platform` → `windows` / `macos` / `other`
- `_arch_id()`：`platform.machine()` → `arm64` / `x64`
- `_platform_asset_keys()`：当前平台允许的候选有序表（Windows：portable → installer；macOS：按架构取一个 dmg）
- `_select_update_asset()` + `_asset_matches()` 做四重硬校验：
  1. **键的平台归属**必须等于当前平台（键名本身就是平台命名空间）；
  2. **扩展名**必须匹配（Windows 只收 `.zip`/`.exe`，macOS 只收 `.dmg`）；
  3. **反平台标记黑名单**：Windows 端资产名/URL 出现 `macos/darwin/.dmg/apple` 一律拒绝；macOS 端出现 `windows/win32/win64/.exe/.zip/setup` 一律拒绝；
  4. **macOS 架构互斥**：arm64 端拒绝名字里带 `x64` 的包，反之亦然。
- **没有跨平台回退**：当前平台没有可用资产时直接 `phase=error` + 人类可读提示 + `release_url`，绝不拿另一个平台的包顶上。
- `install()` 增加平台门禁：非 Windows 直接拒绝自动替换，避免在 macOS 上误跑 PowerShell helper。
- `start_download()` 只允许 `kind=portable`；`dmg` / `installer` 属 manual，不在应用内下载。
- `/api/update/status` 的返回新增 `platform` / `arch` / `platform_label` / `mode`（`auto`/`manual`/`none`）/ `manual` / `release_url`，供前端渲染。

### 14.4 前端（`app/web/app.js` + `index.html`）

- **自动更新**（Windows 绿色版）：沿用「立即更新 → 下载/校验 → 安装并重启」。
- **手动模式**（macOS DMG / Windows 安装包）：更新弹窗显示「下载 DMG 安装包 / 下载安装包」链接
  （`#btnUpdateManual`，`target=_blank`），隐藏「立即更新」；正文带出 `platform_label`
  （如「macOS Apple Silicon（arm64）」）与后端给的安装提示，避免用户拿错包。
- 弹窗不会对 manual 资产调用 `/api/update/download`，因此不会出现「在 macOS 上下载 Windows zip」。

### 14.5 测试（`app/tests/test_updater.py`）

原 5 个用例 + 新增 12 个平台隔离用例，**17/17 通过**：

- Windows：优先 portable（auto）；只有 installer 时 manual 且拒绝应用内下载；拒绝 macOS-only 清单；拒绝把 `.dmg` 错填到 `windows_portable` 键；
- macOS：arm64 / x64 各取对应 dmg；拒绝 Windows-only 清单；缺本架构资产时不回退；拒绝错误扩展名；`install()` 拒绝自动替换；
- 其它：未知平台报错；同版本 `no_update` 也回填 `platform/arch` 字段。

运行：`python -X utf8 app\tests\test_updater.py`

### 14.6 发布侧注意

- `deploy/publish_update.py` 已支持 4 个资产键：可用 `--installer/--portable/--macos-arm64/--macos-x64` 指定本地文件，
  或从 Gitea Release 按文件名自动挑选；发布时先传安装包、后更新 `latest.json`。
- 客户端只依赖一份 `latest.json`；**不要**为 Windows/macOS 拆成两个清单。
- **资产 url 必须写生产部署的绝对地址**（默认 `https://cka.blctek.com:8443/updates/<文件>`）。
  客户端下载用 urllib 直接请求 `asset.url`，相对路径 `/updates/...` 会「能发现更新但下载失败」；
  macOS 手动下载链接也会因此指向本机 127.0.0.1。`publish_update.py` 现已拒绝非绝对 `--url-prefix`
  （确需相对路径时须显式加 `--allow-relative-urls`，仅限下载页自测）。
- v1.5.3 暂不做 macOS 应用内自动替换（DMG 需要用户拖拽）；若后续要自动化，可新增
  「下载 DMG → `open` 挂载 → 提示拖入」的半自动流程，但仍不应调用 Windows helper。

### 14.7 未做/边界

- macOS 自动替换（应用外 helper 进程）未实现，当前为「提示 + 手动下载」。
- 清单版本不高于当前版本时，不下载任何资产（`no_update`）。
- 更新源域名白名单仍未实现（历史遗留）；当前依赖 HTTPS + SHA-256 及平台/扩展名校验。

