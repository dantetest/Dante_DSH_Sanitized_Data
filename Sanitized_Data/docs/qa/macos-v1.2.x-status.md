# macOS v1.2.x 构建状态（2026-10-23）

> 任务：P1 #5「macOS v1.2.0 重建」。
> 结论：**本环境无法构建/验证 macOS 包；不是代码阻塞，是构建资源/凭证阻塞。**

## 环境检查

- 当前构建机：Windows 10 Pro 19045 x64，无 macOS runner。
- 历史 macOS 包由 GitHub Actions 构建（`.github/workflows/build-desktop-macos.yml`，仓库
  `dantetest/Dante_DSH_Sanitized_Data`）；需要 `GH_TOKEN`（Contents:Read+Write、Actions:Read）
  才能推送/触发/下载 artifacts。
- 本次检查：`GH_TOKEN` / `GITHUB_TOKEN` 均未设置；本机无 `gh`；`git credential fill github.com`
  无可用非交互凭证。
- Gitea 侧没有 macOS runner；不能用 Windows 交叉编译 PyInstaller macOS 包。

## 现状

- v1.2.0 与 v1.2.1 Windows/Linux 已发布；Windows 安装包/绿色版/源码包已挂 Gitea Release。
- macOS 仍为 v1.1.2 DMG（arm64 / Intel，ad-hoc 签名，未公证）：
  `Sanitized_Data-1.1.2-arm64.dmg` / `Sanitized_Data-1.1.2-x64.dmg`。
- macOS 没有 v1.2.x 的 MCP L2、DSH 插件、身份证校验位兜底、native browser tab 等更新。

## 解锁条件（按优先级）

1. 提供 GitHub PAT（`GH_TOKEN`）并允许推送 `dantetest/Dante_DSH_Sanitized_Data`；
   运行 `out/_macos_build_github.py`（需把版本/Release 目标更新到 v1.2.x），等待双架构
   Actions 完成；
2. 或提供一台 macOS + Python 3.12 + 项目依赖的构建机，执行 `desktop/build_macos.sh`；
3. 构建后必须补：DMG 冒烟、`/api/health` 版本、ad-hoc 签名/未公证说明、Gitea Release 附件。

## 不作为

- 不伪造 macOS 产物，不把 Windows 包改名为 DMG。
- 没有 macOS 实测前，不在 Release 说明中声称 macOS v1.2.x 可用。
