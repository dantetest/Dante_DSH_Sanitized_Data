# 数据隐站 macOS v1.5.2 构建与产物验证记录（2026-11-11）

> **结论**：macOS v1.5.2 双架构 DMG 已在本日通过 GitHub Actions 的 macOS runner 构建成功，
> 两个架构的挂载/启动/`/api/health` 冒烟测试全部通过；产物已通过 SHA-256、UDIF/APFS 结构、
> `Info.plist` 与 Mach-O 架构四重独立复核，并已挂到 GitHub Release 与 Gitea Release。
> 剩余待办是「真实 Mac 人工验收（B 项）」与「把 latest.json + DMG 发布到 CKA `/updates/` 静态目录」。

## 0. 一页速览

| 项 | 值 |
|---|---|
| 版本 | `1.5.2`（`Sanitized_Data/app/server/yinzhan/config.py` 的 `VERSION`） |
| git 标签 | `v1.5.2` → `c0a515e` |
| 构建流水线 | `.github/workflows/build-macos.yml`（build-desktop-macos） |
| 构建 run | https://github.com/dantetest/Dante_DSH_Sanitized_Data/actions/runs/42939394446 |
| 产物 | `Sanitized_Data-1.5.2-arm64.dmg`、`Sanitized_Data-1.5.2-x64.dmg`、`SHA256SUMS-macos.txt` |
| 签名 | ad-hoc（`codesign --force --deep --sign -`），**未 Apple 公证** |
| 最低系统 | macOS 11.0（`app.spec` 的 `LSMinimumSystemVersion`） |
| 构建结论 | macOS (arm64) ✅ / macOS (x64) ✅ / Release 挂载 ✅ |

## 1. 产物清单（SHA-256 已用 GitHub Release 的 `SHA256SUMS-macos.txt` 交叉复核）

| 架构 | 文件名 | 字节数 | SHA-256 |
|---|---|---|---|
| Apple Silicon | `Sanitized_Data-1.5.2-arm64.dmg` | 265,438,348 | `DA8D2540F167BA2CFAD0835604F02B52EEC0001618343CD282E406197CCB5C33` |
| Intel | `Sanitized_Data-1.5.2-x64.dmg` | 211,811,085 | `95FE7B617933B79C571E5E68E488929FDF10E45A7A2397C218F0E744BFBDA5FD` |

下载与存放位置：

| 位置 | 说明 |
|---|---|
| GitHub Release | https://github.com/dantetest/Dante_DSH_Sanitized_Data/releases/tag/v1.5.2 |
| Gitea Release（内部主仓） | https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases/tag/v1.5.2 |
| 本机构建缓存 | `Sanitized_Data/dist_installer/macos-1.5.2/`（含 DMG + `SHA256SUMS-macos.txt`） |
| 清单 | `Sanitized_Data/out/desktop/latest.json`（已含 `macos_arm64` / `macos_x64`） |

## 2. 构建证据（GitHub Actions）

| Job | Runner | 结论 |
|---|---|---|
| macOS (arm64) | `macos-15`（Apple Silicon） | ✅ success |
| macOS (x64) | `macos-15-intel` | ✅ success |
| 挂到 GitHub Release | `ubuntu-latest` | ✅ success |

两个 macOS job 的关键步骤全部 success：安装依赖 → 读取版本号 → PyInstaller 打包 →
ad-hoc 签名 → 打包 DMG（`hdiutil`）→ **冒烟测试** → upload-artifact。

冒烟测试口径（`.github/scripts/smoke_check.py`，两架构均通过）：

1. `hdiutil attach` 挂载刚生成的 DMG；
2. `cp -R` 取出 `Sanitized_Data.app` 并直接启动；
3. 15 秒后请求 `http://127.0.0.1:8760/api/health`，断言
   `version` 非空、`preview_parity == export-parity/v1`、`parsers >= 5`、
   `detectors >= 3`、`engine.ocr` 为真。

即：**包内插件/规则/模型齐全、版本正确、预览与导出字段一致**，不是「能启动就算过」。

## 3. 独立复核（本机 Windows 上可复现，不依赖 macOS）

| 复核项 | 方法 | 结果 |
|---|---|---|
| SHA-256 | 本地 `hashlib` 复算，对照 Release 的 `SHA256SUMS-macos.txt` | ✅ 两包一致 |
| DMG 容器结构 | `scripts/dmg_check.py`：解析末尾 512B UDIF `koly` 尾块、data fork 偏移/长度、sectorCount | ✅ `koly` 有效，data fork 在文件内，虚拟磁盘 arm64 573.0 MiB / x64 478.1 MiB |
| DMG 可读性 | 7-Zip 24.09 打开 DMG → 内层 APFS → 提取 `.app` | ✅ 识别为 UDIF(APFS)，`master-checksum` 为 CRC32 |
| 版本与身份 | 解析 `.app/Contents/Info.plist` | ✅ `CFBundleShortVersionString=1.5.2`、`CFBundleVersion=1.5.2`、`com.blctek.sanitizeddata`、`LSMinimumSystemVersion=11.0` |
| 可执行文件架构 | `scripts/verify_macos_bundle.py` 解析 Mach-O 头 + load commands | ✅ arm64 包：`Sanitized_Data` / `Sanitized_Data_MCP` 均为 `arm64`（min_os 11.0）；x64 包：均为 `x86_64` |
| 签名载荷 | Mach-O 的 `LC_CODE_SIGNATURE` + `Contents/_CodeSignature/CodeResources` | ✅ 均存在（ad-hoc 签名） |
| 关键资源 | `Contents/Resources/web/index.html`、`app.icns`、`yinzhan/` 数据 | ✅ 在位 |

> 说明：7-Zip 在 Windows 解 APFS 会因无符号链接权限丢掉部分硬链接/符号链接（文件显示 0 字节），
> 因此内部资源只对真实大小 > 0 的条目断言，深层文件完整性以 CI 冒烟测试为准。

## 4. 与 Windows v1.5.2 的差异（发布口径）

| 维度 | Windows | macOS |
|---|---|---|
| 分发形态 | Inno Setup `.exe` + 绿色版 `.zip` | `.dmg`（内含 `.app`，拖入 `/Applications`） |
| 签名 | 无代码签名 | ad-hoc 签名，未公证 → 首次启动被 Gatekeeper 拦截属预期 |
| 数据目录 | `%LOCALAPPDATA%\Sanitized_Data` | `~/Library/Application Support/Sanitized_Data` |
| 自动更新 | 支持（绿色版自替换） | **不支持**，需手动下载新 DMG（见 §5） |
| 引擎/规则/版本 | v1.5.2 | 同源码基线 v1.5.2 |

依赖解析差异（requirements 未锁版本导致，两架构都通过冒烟测试，仅记录备查）：

| 包 | arm64 | x64 |
|---|---|---|
| onnxruntime | 1.31.1 | 1.23.2（该包新版本已不再提供 macOS x86_64 wheel，pip 自动回退） |
| rasterio / pyproj | 1.5.2 / 3.8.0 | 1.5.2 / 3.8.0 |
| rapidocr / zxing-cpp | 3.10.0 / 3.1.1 | 3.10.0 / 3.1.1 |

## 5. 已知限制与后续动作

| # | 项 | 状态 | 建议 |
|---|---|---|---|
| 1 | 未做 Apple 公证 | 已知 | 对外分发保留「右键打开 / `xattr -dr com.apple.quarantine`」指引（见交付说明 §4.2） |
| 2 | macOS 自动更新缺失：`updater.py` 只识别 `windows_portable`/`windows_installer`，`install()` 走 PowerShell | 已核对代码 | v1.5.3 在 `check()` 按 `sys.platform` 分支：darwin 直接提示「请手动下载 DMG」 |
| 3 | 真实 Mac 人工验收 | 待执行 | 按 `docs/qa/macos-install-acceptance-1.5.2.md` 的 B 项执行 |
| 4 | CKA `/updates/` 尚未放置 DMG 与新版 latest.json | 待运维 | 把 `latest.json` + 两个 DMG 放进部署机 `DATA_DIR/updates/`，或在服务器执行 `python3 deploy/publish_update.py --tag v1.5.2` |
| 5 | 依赖未锁版本导致双架构 ORT 版本不同 | 已知 | 后续在 requirements 中按平台锁定 `onnxruntime` 版本 |

## 6. 复现命令（本机 Windows，仅复核用）

```powershell
# 1) 结构校验 + SHA-256（应输出 DMG STRUCTURE RESULT: PASS）
python scripts\dmg_check.py

# 2) 从 GitHub Release 直接下载（不依赖 API）
python scripts\download_macos_direct.py

# 3) Info.plist / Mach-O / 资源校验（需先用 7-Zip 解包到 _verify/<arch>/）
python scripts\verify_macos_bundle.py

# 4) 重新生成下载清单（DMG 已在 dist_installer/macos-1.5.2/）
python prod_compare_2026-11-05\make_download_manifest.py
```

## 7. 复核依据

- `.github/workflows/build-macos.yml`：双架构矩阵、ad-hoc 签名、`hdiutil` DMG、冒烟、Release 挂载
- `.github/scripts/smoke_check.py`：`/api/health` 断言口径
- `Sanitized_Data/desktop/app.spec`：`BUNDLE`、图标、`LSMinimumSystemVersion 11.0`、版本号单一来源
- `Sanitized_Data/app/server/yinzhan/config.py`：`VERSION=1.5.2`、`BUILD_ID=20261104-purchase`
- `Sanitized_Data/docs/release-notes-v1.5.2-macos.md`：对客户交付说明
- `Sanitized_Data/docs/qa/macos-install-acceptance-1.5.2.md`：安装验收清单（A/B/C 分级）
