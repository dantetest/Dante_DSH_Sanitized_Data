# 下载页简化（2026-11-05）

## 线上现状

访问 `https://sd.blctek.com:8443/download` 实测：

- 页面仍是旧版 v1.3.5；
- 安装包/绿色版按钮是灰色，`href` 被移除；
- 页面 SHA 行显示“正在读取更新清单…”；
- `/updates/latest.json` 返回 **404 `not_found`**，页面 JS 因此把所有下载按钮禁用；
- macOS 区域和服务器部署说明占了大量篇幅。

## 本次修改

文件：`app/web/download.html`

改成静态、最短页面：

- 只保留：标题、系统要求、版本、Windows 安装包、Windows 绿色版、SHA-256；
- 删除：营销段落、macOS 双架构区、服务器部署说明、更新清单 JS；
- 两个按钮直接指向 v1.5.2 Gitea Release 资产，**不再依赖 `/updates/latest.json`**，不会再因清单 404 变灰；
- 版本、文件大小、SHA 全部写死并校验收敛到当前发布版本；
- 页面无 JS、无外部脚本。

## 资产链接（已确认 HEAD 200）

```text
https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases/download/v1.5.2/Sanitized_Data_Setup_1.5.2_x64.exe
https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases/download/v1.5.2/Sanitized_Data-1.5.2-win64-portable.zip
```

## 部署

`sd.blctek.com` 的网站源码需要单独更新（桌面安装包不包含下载页；本地包已由 `desktop/strip_local_web.py` 移除下载页）。

按 CKA 现有流程：

```bash
cd /opt/sanitized-data        # 或 sd 实际部署目录
git pull
sudo systemctl restart sanitized-data
```

验收：

```bash
curl -fsS https://sd.blctek.com:8443/download | grep -E 'v1.5.2|1.5.2|下载安装包'
```

浏览器强刷后应看到：

- 版本 chip `v1.5.2`；
- 两个黑色按钮可点击；
- SHA-256 两行；
- 没有 macOS/服务器部署长文。

## 注意

- 以后发新版本，需要同步改 `download.html` 的版本/文件名/大小/SHA/下载 URL；不再依赖更新清单；
- 如果仍希望动态 manifest，需要同时修复 `/updates/latest.json` 的发布链路，否则会再次出现灰按钮；
- 本地桌面版不显示下载页，公网网站保留此页。
