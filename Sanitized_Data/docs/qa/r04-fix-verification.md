# R04 修复与复验记录（v1.1.0 发布前）

> 关联报告：[真实测试集全链路实测报告](real-testset-v1.1.0.md)（修复前记录，R04 = P0）
> 修复提交：v1.1.0 发布提交（见仓库 `main` / tag `v1.1.0`）
> 复验时间：2026-10-21（Asia/Shanghai）
> 数据合规：本文件不写文件名、原文、OCR 文本、实体原值，仅记录类型/数量/机制与结果。

## 1. 问题与根因

真实测试集 R04（10 页扫描件合同）在修复前 verify 报 `仍有 1 处原文残留`：

- 根因：`app/server/yinzhan/replacers/surrogate.py::_split_locality()` 对「整段地址只有
  省/市/区层级」（11 字）返回 `locality == 整段地址`、`rest == ""`；
  `_fake_address()` 无条件保留 `locality` 前缀 → 完整原始地址被原样写进假值，
  成品可见文字与文本层均含原值（同值扩散到第 5、10 页，0-based 页 4/9）。
- 同源风险：`pipeline.py::_replacement_intervals()` 会把「假值内部」一律当作豁免区间；
  假值若完整包含原文，理论上会被放过。本次因替换文字在成品文本层被换行拆分、
  完整假值未匹配上，才恰好被残留检查拦住——属于防御纵深缺口。

## 2. 修复内容

| # | 文件 | 修复 |
|---|---|---|
| 1 | `replacers/surrogate.py::_fake_address` | 当地址切不出 street 部分（`rest` 为空）时，改用 `_replace_district()` 替换地区名，不再原样保留完整地址 |
| 2 | `replacers/surrogate.py::_generate` | 新增隐私不变量：**任何候选假值（含兜底）都不得完整包含原文值**，命中则换候选 |
| 3 | `pipeline.py::_replacement_intervals` | 改为按原值分组的豁免区间；**假值完整包含原文时不登记豁免**，交回残留检查报出 |
| 4 | `app/tests/test_surrogate.py` | 新增 5 项回归：整值省市区地址/多类型假值不变量/安全假值豁免/泄漏假值不豁免/共同前缀仍豁免；已纳入 `run_all`（第 16 套件） |
| 5 | `app/server/yinzhan/media_text.py` | 内嵌图文字 OCR 临时目录改为「调用方 workdir → 系统 TEMP → 数据目录」逐级探测可写、默认权限建唯一子目录、用完即删；不再依赖 `tempfile.mkdtemp`（受限环境 0o700 目录不可写，见公开集报告 §5.2） |
| 6 | `app/tests/eval_real_media.py` | 引擎快照指纹纳入 `replacers/surrogate.py` 与 `media_text.py`，覆盖此类回归 |
| 7 | `pipeline.py::verify` + `_is_numeric_fragment` | 修复 R13 金额验证假阳性：数字类值命中时做 token 边界判断（「25万元」里的「5万元」子串不判残留）；地址/人名等仍全串严格比对 |
| 8 | `desktop/app.spec` | 打包补收 OpenCV Haar 级联数据（`collect_data_files("cv2")`）——此前安装包缺 `haarcascade_*.xml`，打包版人脸检测会报错并静默退化 |
| 9 | `desktop/build_win.ps1` | 自检改用 .NET `ProcessStartInfo.UseShellExecute=$false` 启动（避开「打开文件-安全警告」对话框卡死），并按本次实例 `runtime.json` 端口 + 版本号校验，避免命中旧实例造成假通过；自检固定独立端口 18760 + 临时数据目录 |
| 10 | `app.py::pick_port` + `api.py::QuietServer` | 修复 Windows 端口冲突检测：POSIX 语义的 `SO_REUSEADDR` 在 Windows 允许绑定**已监听**端口，两个进程「都绑定成功」后新实例会静默命中旧实例（自检症状：`runtime.json` 为 1.1.0 而 `/api/health` 返回 1.0.0）。改为先 `connect` 探测再 `bind`，Windows 下关闭 `allow_reuse_address` |

## 3. 复验结果

| 项 | 结果 |
|---|---|
| R04 单文件重跑（修复后） | 解析 ok；导出 ok；**verify ok（residual=0，visual=0）**；实体 39（ADDRESS 1、ORG 7、AMOUNT 4、FACE 17、SEAL 3 等）；86.996s（本机沙箱、OCR 双跑） |
| R13 单文件重跑（数字边界修复后） | 解析 ok；导出 ok；**verify ok（residual=0）**；实体 41；74.5s |
| 真实测试集 17 文件全量重跑（修复后） | 解析 **17/17**、导出 **17/17**、**verify 17/17（P0=0）**、0 崩溃；总 589,067ms（与构建并发跑，OCR 双跑为成本主项）；统计见 `out/qa_real_final_20261021/public5_report.md`（本地、gitignore） |
| 公开 5 样本 | 5/5 解析・导出・verify（见 [public-5-v1.1.0.md](public-5-v1.1.0.md)） |
| 新增回归 `test_surrogate` | **6/6** 通过（5 项假值/豁免 + 1 项数字片段）；`run_all` 由 15 套件扩为 **16 套件** |
| 全量门禁（发布前） | `regression` **251 项硬检查全绿**；`run_all` **16/16 套件全绿**（总 310.8s；详见发布说明 §4） |
| 安装包验收 | 安装/启动/最小链路（双导出 `verify residual=0`）/卸载全部通过；记录见 [install-acceptance-v1.1.0.md](install-acceptance-v1.1.0.md) |

**结论：R04 关闭；真实测试集准入由「Red」转为「Green」（17/17 全链路通过、0 P0）。**

## 4. 方法学与环境说明

- 本机 QA/门禁运行在 DSH Windows `workspace-write` 沙箱下，该沙箱对 `tempfile.mkdtemp`
  （0o700）创建的目录存在 ACL 写入限制，导致部分用 mkdtemp 的测试夹具假失败；
  门禁运行时使用仅测试环境的临时目录兼容层（`out/_envshim/sitecustomize.py`，gitignore，
  **不进入产品包**），产品代码本身不依赖该兼容层。
- 产品侧 `media_text.py` 原来正好用 mkdtemp，属于「受限环境能力静默降级」，本次一并修成
  逐级可写探测；公开 5 样本复跑中 `Permission denied` warning 已消失。
- 复验成品仅写 `Sanitized_Data/out/**`（gitignore），验证后清理；源文件未复制进仓库。
