# 数据隐站 v1.1.1 发布说明（P2/P3 优化版）

> 发布日期：2026-10-21 · 相对 v1.1.0 的补丁版本（无破坏性变更）
> 代码基线：`main` 上 tag `v1.1.1` · 上游清单：`docs/qa/real-testset-v1.1.0.md` §7
> 实现说明：[docs/impl/p2p3-optimizations.md](impl/p2p3-optimizations.md) ·
> 复验记录：[docs/qa/p2p3-fix-verification.md](qa/p2p3-fix-verification.md)

---

## 1. 本版改了什么

v1.1.0 真实测试集 QA 的 P2/P3 五项问题全部处理，**不降低任何安全红线**：

| # | 问题 | v1.1.1 行为 |
|---|---|---|
| 1 | 扫描件 OCR 双跑（10–17 页 60–112s） | 导出后复检不再整册重跑 OCR：`PdfParser.fast_verify` 用低 DPI 渲染 + 逐矩形像素复检（零 OCR），无法证明安全时自动回退原慢路径。真实多页扫描件全链路 **85.4→34.9s / 112.1→61.4s（约 2.4× / 1.8×）** |
| 2 | warning 按页重复噪声（合计 64 条） | 任务/导出/界面统一去重：完全相同 warning 只留一条并标 `（共 N 次）`；不同错误绝不合并 |
| 3 | 整页证件过判（R07/R11 整页涂黑） | 身份证字段可靠命中（正面 4/反面 2）时，`whole_page` 不再无条件整页扩张：卡形页面仍整页兜底，其余返回合理整卡框或只返回字段框；字段读不出仍整卡/整页 fallback。真实 R07/R11 `id_document` 面积 1.000 → **0.467 / 0.796** |
| 4 | 异常大面积码框被静默丢弃 | 面积 >5% 页面的二维码/条码 detect-only 候选进入**待确认列表**（`pending`、暂不涂黑）；用户确认后补 black 才导出。R17 0.353 面积的候选已被列入 |
| 5 | TIF rasterio 大图 Orientation 不转正 | rasterio 路径支持 Orientation 1–8：parse/render/export 全部转正、成品无 274；中性化 transform 同步旋转/交换宽高，复检一致 |

其他：`/api/health` 版本号 1.1.1；安装包/绿色版/源码包同步重建；文档口径同步。

## 2. 性能与验证

| 验证项 | 结果 |
|---|---|
| `regression.py` | **247 项硬检查全绿**（TIF 检查已改为新口径；4 条软提示） |
| `run_all.py` | **19/19 套件通过**（515.22s；新增 3 套 P2 专项） |
| 新增专项 | 扫描件快检 5/5、整页证件 8/8、warning/大码框 9/9 |
| 真实测试集抽测 | R04/R07/R11/R17 全链路通过（verify residual=0）；性能与误报数据见复验记录 |
| 故障注入 | 漏涂黑、替换漏盖、文本层残留均被 verify 拦截（快检不放松校验） |

## 3. 兼容与升级

- 数据目录/库结构不变；直接覆盖安装或解压绿色版即可，原有映射库与审计库继续可用。
- 无新增必装依赖；`rasterio/pyproj` 仍是可选（缺失时 TIF 大图自动降级 Pillow + warning）。
- 新增排障开关（默认无需设置）：
  - `YINZHAN_PDF_FAST_VERIFY=0`：强制扫描件 verify 回退整册重解析（慢、保守）；
  - `YINZHAN_PDF_FAST_VERIFY_DPI`：快检渲染 DPI（72–150，默认 96）。

## 4. 已知限制

- 整页证件收紧后，**卡外背景**不再被整页吞掉；若卡外还有未识别 PII，存在漏遮可能。
  字段读不出/页面本身是卡面时仍走整卡/整页兜底。
- 扫描件快检只对“整册扫描页、所有实体都有决策、覆盖证明成立”的成品启用；混合文本页、
  `keep_pdf_hidden=true`、含无决策实体的文件自动回退慢路径（只损失性能）。
- 快检阈值（黑框 98% / 替换框原墨迹 ≤80%）基于合成件与抽测，真实扫描噪声下仍需
  持续用真实集校验；所有阈值都取安全方向。
- TIF 多帧 + Orientation 仍不逐帧转正（保持帧结构，明确 warning）。
- Windows 安装包仍**未做代码签名**，SmartScreen/杀软可能提示「未知发布者」。
- macOS 桌面包未随本版构建（workflow 就绪，需 GitHub Actions）。

## 5. 产物与校验值

| 产物 | 大小 | SHA-256 |
|---|---|---|
| `Sanitized_Data_Setup_1.1.1_x64.exe`（Windows 安装包） | 168,612,264 B（160.8 MiB） | `E6CC8AAA8D0985BC82062FB515E5A543B32DFB01E3C0E9268070766881CCB2CA` |
| `Sanitized_Data-1.1.1-win64-portable.zip`（Windows 绿色版） | 191,623,232 B（182.7 MiB） | `0BDCEC7FE20DCC77E4BE2F0561627F430855F542A8F734628F814E5805DCAA35` |
| `Sanitized_Data-1.1.1-win64.zip`（源码包） | 见 Release 附件 | 见 Release 附件 `SHA256SUMS.txt` |
| `Sanitized_Data-1.1.1-linux64.tar.gz`（源码包） | 见 Release 附件 | 见 Release 附件 `SHA256SUMS.txt` |

下载与校验：

- Gitea Release：`https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases/tag/v1.1.1`
- 校验命令（Windows PowerShell）：

```powershell
Get-FileHash .\Sanitized_Data_Setup_1.1.1_x64.exe -Algorithm SHA256
Get-FileHash .\Sanitized_Data-1.1.1-win64-portable.zip -Algorithm SHA256
```

安装步骤见 [install-guide.md](install-guide.md)（适用 v1.1.1）。

## 6. 版本关系

- `v1.1.0`：首个可安装交付版（真实测试集 17/17 P0=0，R04/R13 修复）。
- `v1.1.1`：本版，仅 P2/P3 性能/误报/可用性优化；安全红线与既有能力不变。
