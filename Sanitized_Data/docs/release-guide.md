# 版本发布与用户推送指南

> 适用：Windows 安装包 / 绿色版 / 源码包；macOS 另见 `docs/qa/macos-v1.2.x-status.md`。
> 当前**没有自动更新器**：用户通过 Gitea Release / 下载页手动获取新版本；这是本机隐私边界下的默认方式。

## 0. 先判断版本号

| 变更 | 建议版本 |
|---|---|
| 修 bug、补丁、文案/文档 | patch：`1.2.1 → 1.2.2` |
| 兼容的新功能 / MCP 工具 / UI | minor：`1.2.x → 1.3.0` |
| 破坏兼容/数据格式/配置 | major：`1.x → 2.0.0` |

## 1. 版本号与文档（先改再构建）

至少同步这些位置：

- `app/server/yinzhan/config.py` → `VERSION`
- `desktop/installer.iss` → `MyAppVersion`
- `app/web/home.html` / `mcp.html` / `download.html` / `app.js`（底部版本、下载文件名、Release 链接）
- `README.md`、`docs/install-guide.md`、`docs/support-matrix.md`
- 新建 `docs/release-notes-vX.Y.Z.md`（用户可读：新功能/修复/验证/已知限制/产物哈希）
- `docs/desktop-build.md` 补构建记录

> 版本号在 `config.py` 会进入 `/api/health`、安装包、导出回执；不要只改 UI。

## 2. 构建

在仓库根目录：

```powershell
powershell -ExecutionPolicy Bypass -File desktop\build_win.ps1
```

产物：

- `dist_installer\Sanitized_Data_Setup_<ver>_x64.exe` — Inno Setup 安装包（含 `Sanitized_Data_MCP.exe`、`dsh-plugin/`）
- `out\desktop\Sanitized_Data-<ver>-win64-portable.zip` — 绿色版
- `dist\Sanitized_Data\` — 免打包运行目录

构建脚本会自检 `/api/health`、OCR、解析器、扫描器；失败会退出非 0。

**重要**：本机在 DSH 工作区内直接跑安装包可能被历史 ACL 拦截；安装/卸载 smoke 请把安装包复制到
`%TEMP%` 等普通目录再执行（不是安装包缺陷）。

## 3. 源码包（如需随 Release 上传）

```powershell
# out/_make_source_bundles.py 里 VERSION 改成新版本
python -X utf8 out\_make_source_bundles.py
```

生成：

- `app\web\downloads\Sanitized_Data-<ver>-win64.zip`
- `app\web\downloads\Sanitized_Data-<ver>-linux64.tar.gz`

## 4. 发布前门禁

```powershell
python -X utf8 app\tests\run_all.py
python -X utf8 app\tests\regression.py
python -X utf8 app\tests\test_mcp_v2.py
python -X utf8 app\tests\test_dsh_plugin.py
```

建议再加：安装→启动 health→卸载 smoke；打包后的 `Sanitized_Data_MCP.exe` stdio initialize/tools/list/call。

## 5. 提交与打 tag

```powershell
git add ...
git commit -m "release(vX.Y.Z): ..."
git push origin main
git tag vX.Y.Z
git push origin refs/tags/vX.Y.Z
```

## 6. 创建 Gitea Release 并上传资产

在 https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases 新建 Release：

- Tag：`vX.Y.Z`
- Target：`main`
- 标题：例如 `数据隐站 vX.Y.Z · 功能说明`
- 正文：`docs/release-notes-vX.Y.Z.md` 内容
- 上传附件：
  - `Sanitized_Data_Setup_<ver>_x64.exe`
  - `Sanitized_Data-<ver>-win64-portable.zip`
  - `Sanitized_Data-<ver>-win64.zip`
  - `Sanitized_Data-<ver>-linux64.tar.gz`
  - `SHA256SUMS-vX.Y.Z.txt`（每行 `SHA256  文件名  (字节数)`）

> 当前机器上可用 `git credential fill` 取 Gitea 凭证，参考历史脚本
> `out/_release_v120.py`（按版本改 TAG/ASSETS/正文）执行 API 发布；凭证不要写进仓库。
> 也可直接用 Gitea 网页上传，避免脚本凭证问题。

## 7. 更新用户可访问的下载页

- `app/web/download.html`：版本号、文件名、Release 链接、体积
- 首页/MCP 页底部版本
- `README` 的“当前版本”“产物”“发布说明”链接

用户打开 `/download` 就能拿到新包；这是当前的“推送”入口。

## 8. 通知用户

当前没有客户端推送通道，建议：

1. 发布 Release 后发群/IM/邮件：Release URL + 一句话更新点 + 是否需备份；
2. 说明升级方式：
   - 安装版：直接运行新安装包覆盖安装（同 AppId；数据目录 `%LOCALAPPDATA%\Sanitized_Data` 保留）；
   - 绿色版：解压新版到新目录运行，或覆盖程序目录；
   - DSH 插件：安装包内 `dsh-plugin\install.ps1 -Profile desktop`，然后**重启 DSH**；
3. 提醒未代码签名，SmartScreen 可能提示；让用户核对 `SHA256SUMS`。

## 9. 升级数据兼容

- 安装版沿用同一数据目录；升级前建议备份 `%LOCALAPPDATA%\Sanitized_Data\data`（审计/映射/成品）。
- 绿色版默认同目录 `data/`；换目录前把旧 `data/` 复制过去。
- 升级后打开 `/api/health` 确认 `version` 为新版本。

## 10. 自动更新（尚未实现）

产品当前**不联网检查更新**，符合“本机处理、不外呼”的边界。
若以后要做自动更新，建议：

- **opt-in** 的“检查更新”（只访问 Gitea Release API，用户显式开启）；
- 只下载、校验 SHA-256，再提示用户手动安装；不做静默自动安装；
- 更新检查失败不能影响主流程；
- 默认关闭，避免改变隐私承诺。

## 11. macOS

- 不能在 Windows 上交叉构建；
- 需要 macOS 构建机或 GitHub Actions + `GH_TOKEN`；
- 没有构建/实测前，Release 说明不要声称 macOS 新版可用。

## 12. 最小版速查（只发 Windows 安装包）

```powershell
# 1) 改版本号/发布说明
# 2) 构建
powershell -ExecutionPolicy Bypass -File desktop\build_win.ps1
# 3) 门禁
python -X utf8 app\tests\run_all.py
# 4) 提交/tag/push
git add . ; git commit -m "release(vX.Y.Z): ..." ; git push origin main
git tag vX.Y.Z ; git push origin refs/tags/vX.Y.Z
# 5) Gitea Release 上传 Setup + portable + SHA256SUMS
# 6) 更新 app/web/download.html + README
# 7) 群里发 Release 链接
```
