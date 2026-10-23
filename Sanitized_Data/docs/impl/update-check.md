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
