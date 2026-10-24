# 数据隐站 v1.2.2 发布说明

> 版本：**v1.2.2**（GTM 热修）· 2026-10-24
> 定位：不增加收费功能，只把公开发布、下载、DSH 工作台和 macOS 体验补齐；licensing 从 v1.3.0 开始。

## 一、本版重点

1. **CKA / GitHub 公开发布**
   - `/updates/latest.json` 由 CKA 产品自身的 `/updates/` 静态路由提供；
   - `/download` 页读取清单，Windows 安装包/绿色版/macOS DMG 都指向本服务器资产；
   - GitHub public Release 作为镜像，避免 Gitea 登录墙截断下载。
2. **DSH 左下角工作台**
   - 默认打开空白工作台，不再自动挑最近样例 job；
   - DSH 启动时后台预热并拉起工作台，修复重启后首次点击 `ERR_CONNECTION_REFUSED`。
3. **macOS v1.2.2**
   - 双架构 DMG 重建，包含 DSH 工作台修复；
   - ad-hoc 签名、未公证，首次打开需右键 → 打开。
4. **检测能力**
   - 延续 v1.2.1 之后的 P2/P3 收口：大图人脸分块、身份证卡页误报过滤、条码大图兜底、warning 去重。

## 二、已知限制

1. 本版**未做代码签名**；SmartScreen / 杀软可能提示“未知发布者”。证书采购下周确认，v1.3.0 必须签。
2. macOS 未做 Apple 公证，仍为 ad-hoc 测试包。
3. 无 licensing / 自动更新；这些在 v1.3.0 / v1.4.0。
4. 大文件 Job 仍可能超过 10 分钟，建议 20–30 页拆分或走工作台。
5. CKA 清单需运维执行 `deploy/publish_update.py --tag v1.2.2` 后才会出现本版资产。

## 三、产物与校验

> 构建完成后回填精确大小和 SHA-256。

| 文件 | 大小 | SHA-256 |
|---|---|---|
| `Sanitized_Data_Setup_1.2.2_x64.exe` | 待回填 | 待回填 |
| `Sanitized_Data-1.2.2-win64-portable.zip` | 待回填 | 待回填 |
| `Sanitized_Data-1.2.2-arm64.dmg` | 待回填 | 待回填 |
| `Sanitized_Data-1.2.2-x64.dmg` | 待回填 | 待回填 |

## 四、升级说明

- 同 AppId 覆盖安装，数据目录保留；
- 旧版 v1.2.1 冻结，不再覆盖资产；
- 升级前备份数据目录是保险动作，不是必需步骤。
