# macOS v1.2.1 构建状态（2026-10-24）

> 任务：最新 macOS 双架构重建。
> 结论：**已完成**。GitHub Actions `build-desktop-macos` 双架构构建、ad-hoc 签名、DMG、冒烟测试全部通过；
> DMG 已上传 Gitea Release `v1.2.1`，CKA 更新清单待运维执行 publish_update.py。

## 构建

- 构建源：GitHub `dantetest/Dante_DSH_Sanitized_Data`，commit `dd6ec59`
- Workflow：`.github/workflows/build-macos.yml`
- Run：`https://github.com/dantetest/Dante_DSH_Sanitized_Data/actions/runs/40087148675`
- Runner：`macos-15`（arm64）+ `macos-15-intel`（x64）
- 版本：`1.2.1`（`app/server/yinzhan/config.py`）
- 签名：ad-hoc（`codesign --force --deep --sign -`），未做 Apple 公证
- 冒烟：挂载 DMG → 启动 `.app` → `/api/health`，两架构均通过

## 产物

| 架构 | 文件 | 大小 | SHA-256 |
|---|---|---|---|
| Apple Silicon | `Sanitized_Data-1.2.1-arm64.dmg` | 221,285,796 B | `4C9CBFAE778DC215F55CA86BA93F9E851A66A8E79D35E67ED586C61AED42849F` |
| Intel | `Sanitized_Data-1.2.1-x64.dmg` | 210,325,842 B | `B6146DAC0F0F071E862F7F54221F4A2EBD5DAAEA3DE70D6014896E494FECA55F` |

汇总文件：`SHA256SUMS-macos-1.2.1.txt`

## 服务器

- Gitea Release：`https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases/tag/v1.2.1`
- 已上传：两个 DMG + `SHA256SUMS-macos-1.2.1.txt`
- CKA：`publish_update.py` 已支持 `--macos-arm64/--macos-x64`，运维在 CKA `git pull` 后执行：
  ```bash
  sudo -u sanitized env GITEA_TOKEN="$(sudo cat /etc/sanitized-data-gitea-token)" \
    python3 deploy/publish_update.py --tag v1.2.1
  ```
  之后 `/updates/latest.json` 的 `assets` 会包含 `macos_arm64` / `macos_x64`，下载页 macOS 按钮自动可用。

## 说明

- 本次 macOS 包包含最新 `main` 代码（含 P2/P3 检测收口）；版本号仍为 1.2.1，作为 v1.2.1 的 macOS 补构建。
- 首个打开仍会被 Gatekeeper 拦截：右键 → 打开，或
  `xattr -dr com.apple.quarantine /Applications/Sanitized_Data.app`。
- 正式对外分发前的 Apple 签名/公证仍未做（需要开发者账号），目前是测试包。
