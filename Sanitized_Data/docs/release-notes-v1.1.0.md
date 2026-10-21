# 数据隐站 v1.1.0 发布说明

> 版本：**v1.1.0** · 构建日期：2026-10-21 · 平台：Windows 10/11 x64（安装包 / 绿色版）
> 前序版本：v1.0.0（2026-10-19 首次构建：`Sanitized_Data_Setup_1.0.0_x64.exe`）
> 代码基线：`7c553ab`（版本号升级前 HEAD）；发布提交：**tag `v1.1.0`**
> 安装 / 卸载 / 升级：[install-guide.md](install-guide.md) · 能力口径：[support-matrix.md](support-matrix.md)

---

## 摘要

v1.1.0 是 v1.0.0 之后的第一个能力版本：新增 **P1-A** 图像场景能力（聊天截图逐项涂黑、门牌/路牌/店招、未成年人提示、精确时间模糊化、组合风险提示）、**P1-B**（Office 内嵌图片自动脱敏、隐藏对象枚举/确认/剥离、CSV/TSV 列语义、PDF 内嵌图片）与 **P1.5** 补充（Office 内嵌图片文字 OCR、证件类型扩展、CSV/TSV 原编码保留等）；同时修复 **二进制 PLY 导出几何损坏**与 **TIF Orientation 未转正**两个 P0 缺陷。Windows 安装包 / 绿色版已按 v1.1.0 重新构建。

> 定位不变：本机处理、引擎不联网；导出为破坏性重写并做成品复检。**「去标识化」不等于「匿名化」**，本产品只承诺「已隐去」，不承诺「已合规」。

---

## 1. 相对 v1.0.0 的新增能力

### 1.1 P1-A：图像场景与准标识符

| 能力 | 行为 | 边界 |
|---|---|---|
| **聊天截图逐项** | 识别微信式聊天截图，逐项涂黑：联系人/自己头像、昵称、群名称、状态栏/消息时间、底部输入框 | 仅图片、需 OCR 文本；WhatsApp/Slack/折叠屏等自定义皮肤可能漏或框偏；发票/照片/名片已做 0 误报负样本 |
| **门牌 / 路牌 / 店招 / 单位铭牌** | OCR 可读时识别牌面区域，默认「待确认」涂黑，清单可撤销 | 无 OCR/白色招牌/竖排手写/远距小字不承诺自动；普通文档/合同/名片不触发 |
| **未成年人提示** | 从身份证号生日推断 <14 → 对照台红色提示；人工点「启用严格模式」后证件整卡 + 全部实体强制涂黑（不可换名还原） | 只做身份证生日推断，不做人脸年龄估计；未检出时不显示 |
| **精确时间模糊化** | 投放开关（默认关）：只把「完整日期+时刻」里的时刻替换成时段（如 19:28→晚上），日期保留 | 只处理有完整日期的时刻；纯状态栏时间/无日期时间暂不自动 |
| **组合风险提示** | 同一媒体出现 ≥2 类准标识符（位置/证件/人脸/车辆/联系人/时间）时黄色提示 + 一键整图涂黑 | 只提示不自动改；不承诺消除重识别 |
| **YuNet 侧脸模型** | **未随包**：侧脸/低头/极小脸仍可能漏检，漏检时提示人工复核/整图涂黑 | YuNet 权重 MIT，但训练用 WIDER FACE 为 CC BY-NC-ND（非商用/禁止演绎），商用需先法务/许可确认 |

### 1.2 P1-B：Office 内嵌图 / CSV/TSV / PDF 内嵌图与隐藏数据

| 能力 | 行为 | 边界 |
|---|---|---|
| **Office 内嵌图片自动脱敏** | DOCX/XLSX 里 `word/media`、`xl/media` 图片：扫人脸/证件/车牌/二维码/条码/聊天/招牌，命中即按框涂黑并 zip 回写；成品重开复检命中 0 | 只处理图片里的视觉/码类目标；图片里的**纯文字 OCR 规则**由 P1.5 补充；无命中图片保留并 warning |
| **Office 隐藏对象** | 图表/文本框/OLE/activeX/不可解码图片等枚举为「隐藏对象」，导出前确认（`allow_unprocessed_media`），strict 可剥离 | strict 会改变文档外观；剥离失败会阻断导出；XFA/SmartArt/SVG 文本未专门处理 |
| **CSV/TSV 列语义** | 表头列级脱敏；两级/合并表头继承；导出按原分隔符/引号写回、行/列数不变；`.tsv` 可直接解析 | 无表头时退化为普通规则；编码/BOM 保留见 P1.5；同一文件上下堆叠多表只按最前面的表头处理 |
| **PDF 内嵌图片** | 文本层页里面积 ≥5% 的图片：扫人脸/证件/车牌/码/聊天/招牌；视觉无命中时再做**图片内纯文字 OCR 规则检测**（手机号/邮箱/身份证号等），命中则整块图片区域涂黑；成品黑像素复检 | 只处理文本层页；图片内文字需印刷体且 OCR 可读；旋转页/同页 >8 图/异常格式建议人工复核 |
| **PDF 隐藏数据** | 附件/批注/表单/JavaScript 默认剥离，并清理缩略图；成品复检 | 如确需保留附件，API 可用 `keep_pdf_hidden=true`（需知情；敏感原文仍会被出口自检拦截） |

### 1.3 P1.5：识别与保真补充

| 能力 | 行为 | 边界 |
|---|---|---|
| **Office 内嵌图片文字 OCR** | Office 内嵌图片增加**纯文字 OCR 规则**（印刷体手机号/身份证号/邮箱/金额等，命中即按文字框涂黑） | 文字 OCR 受预算限制（默认 30 张/60s/篇，可 env 调）；中文手写/艺术字/低分辨率不承诺 |
| **证件类型扩展** | 证件类型扩到 11 类，新增护照号规则 | 小卡远景/透视过大/OCR 读不出锚点回退整卡 |
| **CSV/TSV 编码保留** | 按原编码/BOM/换行写回（UTF-8 / UTF-8 BOM / GB18030 / UTF-16）；未知编码 warning + 回退 UTF-8 | `.txt/.md` 导出仍为 UTF-8 |
| **PDF 图片内文字** | PDF 文本层页的内嵌图片做纯文字 OCR 规则检测 | 印刷体、OCR 可读；手写/艺术字不承诺 |

### 1.4 P0 缺陷修复

| 缺陷 | 修复 | 验证 |
|---|---|---|
| **二进制 PLY 导出几何损坏** | 二进制 PLY 的 header 命中注释行改为整行删除（不再写变长 UTF-8 内容）；数据段按新 header 偏移重算、逐字节等长写回；导出后结构自检（重读顶点/面数据比对，顶点按包围盒中心平移）；复检失败删除成品并抛 `ExportError`，**不再把带敏感注释的源文件原样复制为成品** | `test_mesh` **7/7**；ASCII/二进制 LE/BE、截断源、面数据篡改用例通过 |
| **TIF Orientation 未转正** | Pillow 路径 parse/render/export 按 EXIF tag 274 转正像素，成品不再写 274；rasterio 大图路径保持原始像素朝向并明确 warning（P1 限制，见已知限制） | `test_tiff` **36/36**（Orientation=6 合成 TIF 方向正确、无 274） |

### 1.4.1 真实测试集验收修复（发布前阻塞项）

真实测试集 17 份文件全链路验收发现 2 个必须修复项（另在打包/自检中发现并修复 2 个打包缺陷），
均已在 v1.1.0 发布前修复并复验（详见 [QA：R04 修复与复验记录](qa/r04-fix-verification.md)）：

| 问题 | 修复 | 验证 |
|---|---|---|
| **R04 地址保形假值泄漏（P0）**：地址只有省/市/区层级时，`_fake_address` 会把完整原地址保留进假值；verify 的「假值内部豁免」理论上会放行 | ① `_fake_address` 切不出 street 时改用 `_replace_district` 替换地区名；② `_generate` 增加「假值不得完整包含原文」不变量；③ verify 豁免改为按原值分组、假值含完整原文时不豁免；④ 新增 `test_surrogate` 回归并纳入 `run_all` | 修复后：单文件 verify **residual=0**；真实测试集复跑 **17/17 verify 通过、0 P0** |
| **R13 金额验证假阳性（阻塞导出）**：金额假值「25万元」包含另一笔原金额「5万元」子串，verify 把更长数字 token 内的子串误判为残留 | verify 对数字类值增加 token 边界判断（命中两侧仍有数字时视为更长数字 token 的一部分，不判残留）；地址/人名等仍全串严格比对 | 修复后：该文件 verify 通过；新增数字片段回归用例 |
| **Windows 端口冲突检测**：`pick_port` 用 SO_REUSEADDR 探测，Windows 下会「绑定成功」已被监听的端口（新实例静默命中旧实例） | 改为不带 SO_REUSEADDR 的本地 bind 探测；服务器在 Windows 下关闭 `allow_reuse_address` | 独立端口自检 `version=1.1.0`；端口占用自动顺延 |
| **打包资源/自检缺陷**：安装包缺 OpenCV Haar 级联数据（安装版人脸检测会静默退化）；构建自检可能被安全警告对话框卡住/命中旧实例 | `app.spec` 补收 `cv2` 数据；`build_win.ps1` 自检改用 .NET CreateProcess、独立端口 18760 + 临时数据目录 + 版本校验 | 安装版 health/parsers/scanners/最小链路全部通过（[安装验收记录](qa/install-acceptance-v1.1.0.md)） |



### 1.5 其他同期新增（P0 第一/二波与识别修复）

v1.0.0 构建（2026-10-19）之后合入、v1.1.0 一并交付的能力（按提交时间）：

| 批次 | 内容 |
|---|---|
| **P0 第一波**（2026-10-20） | 条码/二维码识别与打码、财务/图像/贸易域规则与前端支持、正射 TIF 脱敏链路、红线回归门禁 |
| **P0 二波**（2026-10-20） | **PDF 隐藏数据剥离**（附件/批注/表单/JS）、图片 **EXIF Orientation 转正**、人脸分块后涂黑整头、新增 `pipl-cn` / `table-cn` 规则包、Excel 纯数值金额写回、对照台「整图涂黑」 |
| **身份证 A 方案**（2026-10-20） | 默认「关键字段」档（头像/姓名/号码/住址 + 反面签发机关/有效期），可选「整卡涂黑」，字段定位失败回退整卡 |
| **真实样例识别修复**（2026-10-20） | 判决书类跨行案号/地址、标签姓名、竖排审判员、带空格金额的漏识别修复 |
| **门禁扩充** | P1.5 收口时 `regression` 251 项 / `run_all` 15 套件；v1.1.0 发布前新增 `test_surrogate` 后按本机口径为 **`run_all` 16 套件 / `regression` 251 项**（以最新输出为准）；规则包 **8 个 / 104 条规则** |

### 1.6 版本与安装包

- 版本号统一升至 `1.1.0`：`app/server/yinzhan/config.py`、`desktop/app.spec`、`desktop/installer.iss`、`desktop/build_win.ps1`、Web 页面（首页/下载页/MCP 页/app.js）。
- 安装包 / 程序窗口图标更新为项目图标（图源见 `docs/research/assets/icon-source/`，适配记录见 [research/06-图标适配检查与替换记录.md](research/06-图标适配检查与替换记录.md)）。
- Windows 产物按 v1.1.0 重新构建：安装包 `Sanitized_Data_Setup_1.1.0_x64.exe`、绿色版 `Sanitized_Data-1.1.0-win64-portable.zip`；安装、卸载、升级步骤见 [install-guide.md](install-guide.md)。
- macOS 免签名测试包（workflow）未随 v1.1.0 重建，见 [desktop-build.md](desktop-build.md)。

---

## 2. 安装方式

| 方式 | 适合 | 入口 |
|---|---|---|
| **Windows 安装包** | 绝大多数用户；双击安装到当前用户目录，无需管理员权限，不需要本机装 Python | `Sanitized_Data_Setup_1.1.0_x64.exe` |
| **Windows 绿色版** | 不想安装 / 便携使用；解压后直接运行 | `Sanitized_Data-1.1.0-win64-portable.zip` |
| **源码运行** | Linux / 服务器部署 / 自行排障；需要 Python 3.10+ | `pip install -r app/server/requirements.txt` 后运行 `启动.bat` / `start.sh` |

- 系统要求：Windows 10/11 x64；磁盘预留约 400 MB；WebView2 推荐（安装包自动补齐，缺失时自动改用默认浏览器，功能不变）。
- 数据默认放在 `%LOCALAPPDATA%\Sanitized_Data`，安装版程序目录为 `%LOCALAPPDATA%\Programs\Sanitized_Data`；**卸载不会删除数据目录**。
- 详见 [install-guide.md](install-guide.md)。

---

## 3. 升级：v1.0.0 → v1.1.0

1. 退出正在运行的数据隐站（确保任务栏/托盘无残留进程）。
2. 建议先备份数据目录 `%LOCALAPPDATA%\Sanitized_Data`（映射库/密钥/审计库都在里面）。
3. 直接运行 v1.1.0 安装包覆盖安装（Inno 使用同一 AppId，会原地升级到同一程序目录）。
4. 启动后核对版本：控制台页面底部版本号 / 访问 `http://127.0.0.1:8760/api/health`，`version` 应为 `1.1.0`。
5. 数据目录沿用，**映射库（换名还原）、密钥、审计链不随程序升级丢失**；旧版导出的成品仍在 `out/`。

绿色版升级：解压新版到新目录后直接运行即可（数据目录在 AppData，不受程序目录替换影响）。

---

## 4. 质量与验证

| 项 | 结果 |
|---|---|
| `regression` | **251 项硬检查全绿**（本机环境，以实际输出为准；含 P0 真链路检查） |
| `run_all` | **16/16 套件全绿**（新增 `test_surrogate`；总 310.8s） |
| 规则包 / 规则数 | 8 个包 / **104 条规则**（`load_errors=0`） |
| `test_mesh` / `test_tiff` / `test_surrogate` | 7/7 · 36/36 · 6/6 |
| `e2e`（5 个样例双导出） | 5/5，复检残留 0 |
| 真实测试集（用户提供 17 份） | **17/17 解析/导出/verify 通过，P0=0**（修复 R04/R13 后复跑） |
| 公开 5 样本 | 5/5 解析/导出/verify 通过（0 P0） |
| 打包后自检（安装版程序） | `version=1.1.0`、`ocr=true`、`parsers=7`、`scanners` 含 barcode/visual/sign、`net=blocked` |
| silent 安装 / 最小链路 / 卸载 | 安装成功（HKCU 卸载项「数据隐站 1.1.0」）；安装版上传→双导出（AI/人读）`verify residual=0`；卸载 exit 0、程序目录删除、**数据目录保留**（详见 [安装验收记录](qa/install-acceptance-v1.1.0.md)） |
| 代码基线 | `7c553ab`（版本号升级前）→ 发布 tag **`v1.1.0`** |

> 测试统计以仓库当前 `app/tests` 为准；真实测试集回归结果见后续 QA 报告。

---

## 5. 已知限制（摘要）

| 限制 | 说明 |
|---|---|
| **三维能力有限** | 只支持 `.obj/.ply/.glb/.stl` 的文本/注释清理 + OBJ/PLY 顶点平移（GLB 仅 JSON 等长覆盖、STL 头置零）；贴图/MTL/GLB 内嵌图、**OSGB / 3D Tiles / LAS-LAZ**、三维结构化 verify 与预览均为 0，综合完成度约 **20%**，**不承诺真实倾斜摄影交付** |
| **TIF 边界** | 坐标中性化为 LOCAL_CS（GIS 可打开、无真实坐标），参数可逆；多帧 TIFF 只剥离不中性化（warning）；rasterio 大图路径不转正 Orientation（P1 限制）；纯地理标签、无可识别文字的影像支持"只清理"导出 |
| **人像/OCR** | 侧脸/遮挡/极小脸可能漏检（YuNet 未随包）；手写体、艺术字、低分辨率扫描不承诺；聊天截图仅微信式布局 |
| **Office / PDF 内嵌图** | 只处理视觉/码类目标与印刷体文字规则；无命中图片保留（可能仍带 EXIF/GPS）；隐藏对象需导出前确认（strict 可剥离，会改变外观） |
| **文件名** | 导出名沿用源文件名；文件名含个人信息时请先改名（命名策略待确认） |
| **PDF 隐藏数据** | 默认剥离附件/批注/表单/JS；`keep_pdf_hidden=true` 为显式逃生门，保留时写 warning，敏感原文仍会被出口自检拦截 |
| **安装包** | 未做代码签名，SmartScreen / 杀软可能提示「未知发布者」；请核对发布页提供的 SHA-256 |
| **合规口径** | 「去标识化」不等于「匿名化」，不承诺满足《个人信息保护法》/匿名化标准；地理数据只能「降密」，形状/纹理仍可能反推 |

> 完整逐项口径见 [support-matrix.md](support-matrix.md) 与 README「已知限制」。

---

## 6. 校验值

| 文件 | 体积 | SHA-256 |
|---|---|---|
| `Sanitized_Data_Setup_1.1.0_x64.exe` | 168,594,784 B（160.8 MiB） | `86BCC6B66A50308A39F698A5BF7CE1BD761FD721E3668A99ADBD819F63329E0B` |
| `Sanitized_Data-1.1.0-win64-portable.zip` | 191,606,312 B（182.7 MiB） | `F709237BF1DC5F6FB249389A0C5CCC6D97368D16CDBBA28D0C466AC0850F31D8` |
| `Sanitized_Data-1.1.0-win64.zip`（源码包） | 2,490,079 B（2.37 MiB） | `578AFCF764B414307EC26C220C85707BD55A1703A2B448E8CB66D96C0341BC34` |
| `Sanitized_Data-1.1.0-linux64.tar.gz`（源码包） | 2,340,724 B（2.23 MiB） | `54BAE9250C7C55DD4790C98D4CBA2EBFFB193FA650712FAA90C7120F50C18BF0` |

- 发布提交：**tag `v1.1.0`**
- 构建时间：**2026-10-21 12:43（Asia/Shanghai，最终构建）**；PyInstaller 6.22.3 · Inno Setup 7.1.0
- 绿色版目录 `dist\Sanitized_Data\` 约 422.7 MiB（解压后）
- 校验命令（Windows PowerShell）：`Get-FileHash .\Sanitized_Data_Setup_1.1.0_x64.exe -Algorithm SHA256`

---

## 7. 相关文档

- 安装 / 卸载 / 升级 / 排障：[install-guide.md](install-guide.md)
- 格式与能力口径表：[support-matrix.md](support-matrix.md)
- 构建与分发（Windows / macOS）：[desktop-build.md](desktop-build.md)
- 桌面版规划（历史）：[desktop-plan.md](desktop-plan.md)
- 项目说明与规则包：[../README.md](../README.md)
- 三维 / 正射实现说明：[impl/mesh.md](impl/mesh.md)、[impl/tiff.md](impl/tiff.md)
