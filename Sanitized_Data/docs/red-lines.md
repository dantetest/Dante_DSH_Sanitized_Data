# 数据隐站 · 更新红线与回归门禁（v1）

> 状态：生效中 · 2026-10-19 起 · 本文件约束后续所有功能更新
> 背景：用户批准的方案 A（底座 → 条码 → 财务 → 图像 → 贸易 → 正射 TIF），并明确要求「**更新功能前确保红线，以前好用的功能不能被破坏**」。任何新功能如果让本文「红线清单」中的一项变红，视为不可交付。
> 配套门禁：[`app/tests/regression.py`](../app/tests/regression.py)（见 [app/tests/README.md](../app/tests/README.md)）。改代码前后都必须跑，结论以它 + `app/tests/e2e.py` 为准。

---

## 一、安全红线（不可妥协，任何版本不得触碰）

| # | 红线 | 验收方式 |
|---|---|---|
| S1 | **只监听回环地址**：默认 `127.0.0.1`，不主动绑 `0.0.0.0`；对外部署由用户显式加参数并套反代 | 启动参数/代码检查 + `/api/health` 的 host/url |
| S2 | **引擎无出站网络**：任何代码不得向非 `127.0.0.1` 发起请求；`/api/health` 必须报 `net: "blocked"` | 静态扫描（`requests`/`urllib.request`/`http.client`/`socket.connect` 白名单仅回环）+ health |
| S3 | **导出是破坏性重写**，不是盖黑框：PDF `apply_redactions()` 重写内容流；图片实心填充后重编码；DOCX 删除 run；XLSX 清值+删隐藏结构 | e2e 残留 0 + 导出后文本抽取复核 |
| S4 | **元数据必须剥离**：PDF author/producer/XMP；DOCX `docProps/*` 作者/公司；图片 EXIF/GPS/ICC；TIFF 地理标签与 sidecar | 导出成品重解析检查 |
| S5 | **审计不记原值**：审计只记录类型与数量、哈希、规则包、模式；不得写原文/原值 | 静态检查 audit 写库字段 + 人工抽查 |
| S6 | **映射库加密**：可逆映射 AES-GCM 落盘，密钥本机；MCP 路径也必须留映射（否则换名不可还原） | 代码检查 + 单测 |
| S7 | **出口自检必须拦截残留**：`pipeline.verify` 失败则删除成品、拒绝交付；新增的视觉/码类残留检测也必须接入同一阻断机制 | e2e/回归的 verify 断言 |
| S8 | **话术诚实**：只承诺「已隐去/去标识化」，不承诺「合规/匿名化」；地理数据只说「降密」 | 文档/页面文案抽查 |

## 二、功能红线（既有能力清单，新功能不得破坏）

| # | 既有能力 | 保护方式 |
|---|---|---|
| F1 | PDF（文本层 + 扫描件）解析、换名、涂黑、真删除 | e2e 5 样例 + 关键实体最小数量 |
| F2 | DOCX / XLSX / TXT 解析、值级替换、元数据清理 | e2e + 元数据断言 |
| F3 | 图片 OCR（PNG/JPG/WEBP/BMP）、涂黑重编码、去 EXIF | e2e 名片样例 |
| F4 | 正射 TIF：**真实地理参考残留 0**（LOCAL_CS 中性化结构按 D3 允许，可 GIS 打开；源 ModelPixelScale/Tiepoint/EPSG/GDAL_METADATA/RPC/ICC/EXIF 残留即 FAIL）、sidecar 清理、多帧、导出后复查 | e2e + 导出后 `real_geo_residual` 断言 |
| F5 | 双出口（换名可还原 / 涂黑不可还原）+ 映射库 + rehydrate | e2e 双模式 + 还原链路单测 |
| F6 | 印章/签署区/手写签名检测（PDF）与矩形涂黑通路 | e2e 委托代理合同 |
| F7 | 规则包热加载、include、优先级、同值合并、同值扩散 | 规则包完整性断言 + e2e |
| F8 | 框选补漏（rect 归一化）、逐条确认、预览渲染、**整图涂黑（rect=[0,0,1,1]）** | HTTP/预览断言（回归内置 API 级） |
| F9 | MCP 四个工具：sanitize_document / redact_text / check_support / list_rules | 回归中逐个调用（含 stdio 协议） |
| F10 | 站点 4 页面 + 图标 + 下载页/MCP 页可访问 | HTTP 冒烟（回归内置） |
| F11 | 桌面版启动链路（`desktop/main.py`、打包脚本、安装包配置）不回退 | 语法/引用检查 + 构建前自检 |
| F12 | 性能不倒退：OCR 页耗时、e2e 总时长不劣化超过阈值 | 基线软记录，超过 50% 报警 |
| F13 | **PDF 隐藏数据默认剥离**（附件/批注/表单/JS/缩略图）+ `pipeline.verify` 拦截未清理 | `test_pdf_hidden.py` + 回归构造隐藏数据夹具（含 keep 不绕过文本残留复检） |
| F14 | **图片 EXIF Orientation 转正**（解析/渲染/导出像素方向一致，导出无 EXIF） | 回归合成 Orientation=6 JPEG 夹具 |
| F15 | **PIPL 补充规则 + 纯数值单元格**：`pipl-cn`（21 条）识别社交/快递/票号/IMEI 等；`numeric_cell` 识别 XLSX 纯数值金额并按数字写回 | `test_pipl_rules.py` + 回归 xlsx 数值夹具（openpyxl 断言单元格为数字） |
| F16 | **表格表头列级检测**（`table-cn` 11 条，XLSX/DOCX 整列覆盖） | `test_table_column.py` + 回归 xlsx 列级覆盖夹具 |
| F17 | **前端整图涂黑**按钮（后端 `/mark` rect=[0,0,1,1] → black 实体铺满页面） | 回归 API 级断言（black/coverage） |
| F18 | 判决书类文本检测（跨行案号/地址、标签姓名、竖排审判员、带空格金额） | `test_legal_detect.py` |
| F19 | **身份证 A 方案**：默认 `fields` 档正面遮头像/姓名/身份证号/住址（性别/民族/出生保持可见），**反面遮签发机关+有效期限（国徽/标题/其余卡面保留）**；可选 `whole` 整卡涂黑；`POST /api/jobs` 默认 fields、非法值回退 fields；正面无脸/无法定向、反面两行读不出 → 整卡回退并提示 | `test_visual`（48 项）+ 回归真链路合成证件夹具（API 默认/非法、fields 正面四字段+保留行、fields 反面两字段+国徽标题保留、whole 整页、两面 fallback） |
| F20 | **P1-A 第一波**：聊天截图逐项（头像/昵称/群名/时间/输入框，仅图片、微信式布局、不整屏）；门牌/路牌/店招/单位铭牌 OCR 锚点扫描（默认 pending、白纸门控）；未成年人身份证生日推断 <14 → 提示 + 人工 `POST /minor-strict`（证件整卡+全部实体强制涂黑）；时间模糊 `time_blur`（默认关，仅完整日期+时刻→时段，日期保留）；组合风险 `combination_risk`（≥2 类准标识符只提示） | `test_visual`（56 项）+ `test_sign_targets`（39 项）+ 回归真链路：聊天合成图 5 类 CHAT_*、发票/名片 0 误报；招牌 4 类实体+导出黑；未成年虚构证件推断/minor-strict/未检出 400；时间模糊 TIME 替换与日期保留；组合风险类别断言 |

| F21 | **P1-B（Office/CSV/PDF 内嵌图）**：Office 内嵌图片**默认自动脱敏**（face/id/plate/qr/barcode/chat/sign），隐藏对象（图表/文本框/OLE/不可解码图）改为**需确认**（`media_confirmation_required`）；`strict_office_media` 剥离隐藏对象并把无命中图置白；CSV/TSV 支持列级+多级/合并表头（`table-cn` scope 含 text），导出保持原分隔符/引号且列数不变；PDF 文本层页 ≥5% 页面内嵌图识别为 `EMBEDDED_IMAGE` 并整块涂黑、重解析黑像素复检 | `test_office_media`（30 项）+ `test_csv_table`（72 断言）+ `test_pdf_embedded`（23 项）+ 回归真链路：Office 图片自动脱敏/重扫 0 命中/正常图字节不变/隐藏对象确认+strict/audit；CSV/TSV 列级覆盖+导出形状不变；PDF 内嵌图实体+黑覆盖+重解析 0 |

| F22 | **P1.5（内嵌图文字 / 证件类型 / CSV 编码 / 护照号）**：Office 内嵌图片在视觉/码类后对**纯文字 PII** 做 OCR 规则脱敏（统计 `text_hits/text_images_redacted/text_verify_*`，预算 `YINZHAN_OFFICE_TEXT_MAX_IMAGES/MAX_SECONDS`，总开关 `YINZHAN_OFFICE_TEXT_OCR`，导出后文字自检 0 命中）；PDF 文本层内嵌「纯文字图」也做规则检测 → `EMBEDDED_IMAGE`（note `engine=embedded_image;text=N;area=`，开关 `YINZHAN_PDF_EMBEDDED_TEXT_OCR`）；证件类型扩展 6 类（工作证/居住证/港澳通行证/台胞证/外国人永居证/银行卡，按 `doc=` 显示标签并整卡涂黑；银行卡不整页扩张）；CSV/TSV 保留原编码/BOM/换行（未知编码 warning 回退 UTF-8）；护照号规则 `cn.passport_no`（legal-cn，near 护照，`PASSPORT` + surrogate），规则 103→104 | `test_office_media`（43 项）+ `test_pdf_embedded`（40 项）+ `test_visual`（61 项）+ `test_csv_table`（115 断言）+ 回归真链路：Office 文字 OCR/预算/开关/审计；PDF text_sensitive 标记+涂黑/开关；6 类证件 label+整卡+银行卡不整页+负样本 0；护照 near/假值/不误报；GBK/BOM/UTF-16/CRLF 编码保留+未知回退 |> 允许的"变化"：新增识别类型/更多实体（例如以后人脸、条码、PIPL/表格规则被检出）**不算破坏**；但"原有必须命中的变少 / 导出失败 / 残留>0 / 元数据或隐藏数据回来了 / 服务外呼"一律算破坏。

| F23 | **P0 修复（PLY 二进制完整性 / TIF Orientation）**：PLY 导出后 parser 自检（重读成品 header element/数据段长度、顶点按 center 平移、面数据逐字节）；不一致/源损坏 → `ExportError` 并删除成品，**不再降级复制源文件**；子格式=ascii / binary_little_endian / binary_big_endian 三子格式数据段长度不偏移。TIF：Pillow 路径 parse/render/export 按 Orientation 转正，`KEEP_TAGS` 移除 274（成品不写 Orientation）；rasterio 大图路径不转正像素但 warning（`docs/impl/tiff.md` 已知限制），成品仍无 274 | `test_mesh`（7 项）+ `test_tiff`（36 项）+ 回归真链路：PLY 三子格式 jobs→导出→独立重读（header 无敏感/数据段等长/顶点平移/面一致）；截断源 ExportError 且无成品、篡改成品自检删除；TIF Orientation=6 转正（60×120，render/export 像素一致、无 274）、无 274 源尺寸不变、rasterio 路径 warning+无 274 |> 新增实体必须逐条解释：预期覆盖增强视为合理新增（软提示）；无法解释的才按误报处理（需 owner 修规则）。

## 三、回归门禁流程（每次改动必须走）

1. **开工前**：跑 `python -X utf8 app/tests/regression.py`，全绿才动代码（基线见 `app/tests/baseline/`）。
2. **每个可感知改动后**：先跑对应模块单测，再跑完整 regression + e2e；红项必须修好或回滚，不得"带病继续"。
3. **收口/提交前（全套）**，按顺序：
   1) 各域测试：`python -X utf8 app/tests/run_all.py --only barcodes,finance,trade,visual,legal,pipl,table,pdfhidden,signs,tiff`；
   2) e2e：`cd app/server && python -X utf8 ../tests/e2e.py`（必须 5/5、残留 0）；
   3) 红线门禁：`python -X utf8 app/tests/regression.py`（必须退出码 0）；
   4) 一条命令全套：`python -X utf8 app/tests/run_all.py`（当前 **11 个套件**，regression + 10 个功能域）；
   5) `git status` 只包含预期文件；`git diff` 人工过一遍；不 push（按用户要求，等通知）。
4. **新增规则/依赖**必须额外过：
   - 新规则在既有样例上的**误报检查**（对比基线，新增命中要逐条解释）；
   - 新依赖：离线 wheel 可用、许可证（免费/安全优先：MIT/BSD/Apache-2.0/PSF）、打包体积与 PyInstaller 收集验证；不引入需要运行时联网或许可证不清晰的组件。
5. **新增能力必须有回归夹具**：PDF 隐藏数据、EXIF 朝向、表格列级/数值写回、整图涂黑、
   身份证 fields/whole 档位等，由 `regression.py` 在临时目录构造**虚构**夹具真实跑通
   「解析→导出→verify」，不得只做静态检查（身份证人脸检测用引擎 test hook 注入确定性命中，
   仓库不放真人证件照片）。
6. **CONTRACT 先行**：改数据结构/契约（`udm.py`、`registry.py`、`pipeline.py`）必须先更新 `app/server/CONTRACT.md`，再改实现。
7. **基线更新纪律**：`--update-baseline --reason "..."` 仅在确认是"改进而非回归"、且全套绿后执行；
   理由与差异写入 `app/tests/baseline/SNAPSHOT.md` §8/§9，并在交接说明；不得为让门禁变绿而重录。

## 四、本批（方案 A）扩展点设计约定

> 为了让 5 个功能域并行开发不互相踩，统一走下面 3 个扩展点；各自实现放在独立文件里，核心文件由 Lead 维护。

### E1. 页面图像扫描器（`mediascan.py`，新增，Lead 拥有）

- 目的：给「没有文字、只能靠图像找」的目标（人脸/证件/车牌/路牌/门牌/印章/签名/二维码/条码）一个统一入口，供 `image_parser` / `tiff_parser` / `pdf_parser` 调用，并为 Verify 复检复用。
- 接口（草案）：
  ```python
  # mediascan.py
  def register_scanner(name: str, fn, *, kinds: tuple[str, ...], priority: int = 0) -> None: ...
  def analyze(image_rgb: "np.ndarray", *, page: int, dpi: float = 0.0,
              options: dict | None = None) -> list[dict]:
      """返回 [{"kind": "face", "rect": (x0,y0,x1,y1), "confidence": float, "note": str}, ...]；
      rect 用调用方图像坐标系（图片=px，PDF=渲染 px，TIFF=帧 px）。"""
  ```
- 各扫描器放独立模块，自己 `register_scanner`：
  - `visual.py` 扩展：`seal`/`signature` 复用现有实现；新增 `face`/`id_document`/`plate`/`road_sign`/`door_address`（图像 P0，researcher-faces）。
  - `barcodes.py`（新增）：`qrcode`/`barcode`（条码 P0，researcher-codes）。
- 解析器接入（Lead 统一改，3 个解析器各加 2–5 行）：`doc.visual_marks` 继续作为载体（扩展 kind），jobs 的 `_VISUAL_SPEC` 增加新 kind → etype/label/level/默认决策。
- 开关：环境变量 `YINZHAN_SCAN_VISUAL`（默认 1）、`YINZHAN_SCAN_BARCODE`（默认 1）、`YINZHAN_SCAN_MAX_SIDE`（默认 3000，控制 CPU）。

### E2. Verify 复检钩子（`pipeline.verify` 扩展，Lead 拥有）

- 新增可注册的导出复检器：
  ```python
  # 在 pipeline.py 或独立 verify.py
  def register_verifier(name: str, fn) -> None: ...
  def verify(exported, entities, parser, ctx, *, doc=None, rules=None, options=None) -> dict
  ```
- 复检内容（P0 底座先做）：
  1. 既有：文本残留 + 元数据 + TIF Geo；
  2. **重解析后视觉/码类残留**：对 rect 类实体，重解析成品的 `visual_marks`，若仍有同 kind 标记与实体框 IoU ≥ 0.3 → `残留`；
  3. **未处理媒体**：DOCX/XLSX 内嵌图片数量 > 0 且未被处理 → 阻断/要求二次确认；
  4. **仅清理场景**：没有实体但存在可清理元数据（TIF Geo、EXIF 等）时，也必须通过复检才允许落盘。
- 兼容：`verify()` 旧调用方式保持可用（新增参数默认 `None`），MCP 路径同步升级。

### E3. Office 内嵌媒体（P0 仅"检测 + 阻断/确认"，P1 自动脱敏）

- `docx_parser` / `xlsx_parser` 在 parse 时把未处理媒体数量与部件名写入 `doc.meta["embedded_media"]`（至少 `{count, parts}`），并保留 warning。
- 导出路径：`jobs._export_to` 在检测到未处理媒体且调用方未显式确认时，抛 `YinzhanError`（错误码 `media_confirmation_required`），由 API 返回给前端二次确认；确认后带 `allow_unprocessed_media=true` 放行并写入审计。
- P1（本批不做）再做 DOCX/XLSX 媒体提取→走图像脱敏管线→zip 级替换。

### E4. 规则/替换器扩展（各功能域独立文件，避免冲突）

- 规则包：新增/修改的 YAML 文件**按域独立**（`trade-cn.yaml` 新建；`finance-cn.yaml` 由财务域改；`common.yaml` 的修复由 Lead 统一改并跑全量回归）。
- 校验器：新增文件 `detectors/finance_validators.py`、`detectors/trade_validators.py` 等（自动被 `registry.load_plugins` 导入）；共享的 `regex_det.py` 由 Lead 维护，只做通用能力（如 `unless_validate`）。
- 替换器：金额一致性等新逻辑放独立模块（`replacers/amount_consistency.py` 等），由 Lead 挂接管线，避免多人改 `surrogate.py`。

### E5. 地理中性化（P0 底座后做）

- 坐标变换参数入 `vault`（扩展映射结构，加密）；随机平移+旋转 v1；变换后写 LOCAL_CS 本地任意 CRS（已批准 D3：可 GIS 打开、无真实世界坐标）；`geo_mode="strip"` 可选彻底去参考。
- 语义（Verify 依赖）：`meta["geo_tags"]` 仅表示"成品里仍存在的真实地理参考"，中性化后必须为空；`meta["geo_neutralized"]=True` 标记已完成；LOCAL_CS 结构本身不算残留，真实 CRS/RPC/ICC/EXIF 残留才算。
- 大图/位深/overview 走可选依赖 `rasterio`（BSD-3，内含 GDAL）/`pyproj`（MIT），**必须能在离线 wheel 下安装**；不装时降级为 Pillow 并给出明确 warning，不静默失败。

### E6. PDF 隐藏数据剥离（P0 二波，Lead）

- `pdf_parser` 解析时统计 `embedded_files` / `pdf_annotations` / `pdf_forms` / `pdf_js` 写入 `doc.meta`。
- 导出默认**破坏性剥离**附件/批注/表单/JS/缩略图；`keep_pdf_hidden=True` 可显式保留。
- `pipeline.verify` 对成品重解析后检查上述 4 个 meta 计数，非 0 且未显式 keep → 抛 `VerifyError` 阻断；
  **keep 不能绕过文本残留复检**（隐藏数据里的敏感文本仍会被 residual 拦住）。
- 回归：`test_pdf_hidden.py` + `regression.py` 构造含附件/批注/表单/JS 的虚构夹具，断言默认剥离与 verify 拦截。

### E7. 图片 EXIF Orientation 转正（P0 二波）

- `image_parser._apply_orientation`：parse / export / render 三处都先按 EXIF Orientation（3/6/8）转正像素。
- 验收：解析后页面宽高为转正后的尺寸；render PNG、导出成品的像素尺寸一致；导出成品无 EXIF/ICC/XMP/GPS。
- 回归：合成 Orientation=6 JPEG（存储 400×200 → 展示 200×400）夹具。

### E8. 表格列级检测 + 纯数值单元格（P0 二波）

- `table-cn`（11 条，表头列级，scope xlsx/docx）：按表头定位整列覆盖；`table_column` 检测器值形状守卫 + `exclude_validate` 让位。
- `numeric_cell` 检测器（pipl/财务共用）：识别 XLSX 纯数值金额/账号/日期；导出时金额按**数字**写回（保留 number_format、公式可重算），账号按字符串写回。
- 回归：虚构 xlsx 夹具断言表头列级覆盖 + openpyxl 读回单元格类型为数字；样例 `对账单.xlsx` 新增
  「人名×1、表格账号列×1、金额（数值）×4」共 6 实体属**预期覆盖增强**（软提示，不算回归）。

## 五、当前基线（2026-10-20 P0 修复收口）

| 项 | 数值 |
|---|---|
| 基线文件 | `app/tests/baseline/baseline.json` + `SNAPSHOT.md`（版本化，`--update-baseline --reason` 重录） |
| 代码状态 | HEAD `bdae2f2` + P1-B 未提交工作树（`engine_clean=false` 已如实记录） |
| 规则包 | **8 个**（common/finance-cn/legal-cn/medical-cn/geo-cn/trade-cn/pipl-cn/table-cn），自有规则 **104 条**（P1.5 新增 legal-cn `cn.passport_no`） |
| 编译规则数 | pdf/text 89、docx 100、xlsx 103、image/tiff 86、mesh 13（+1 护照号） |
| 5 个既有样例 | 实体 52 个（对账单 13→19 为预期新增），双导出 residual 全 0，元数据/隐藏数据全清（本轮无聊天/招牌/证件样例，**功能基线数值不变**） |
| 红线门禁 | `regression.py` **251 项检查全绿（硬项失败 0；以最新输出为准）**（+0 软提示）；退出码 0 |
| 全套测试 | `run_all.py` **15 个套件**全绿：regression + barcodes/finance/trade/visual/legal/pipl/table/pdfhidden/signs/officemedia/csv/pdfembedded/mesh/tiff（test_visual 61、test_sign_targets 39、test_office_media 43、test_csv_table 115 断言、test_pdf_embedded 40、test_mesh 7、test_tiff 36） |
| 身份证 A 方案 | `fields` 默认（正面头像/姓名/号码/住址；反面签发机关/有效期限；性别/民族/出生、国徽/标题保留）、`whole` 可选整卡；API 默认/非法值回退 fields；无法定向/两行读不出回退整卡 |
| P1-A 第一波 | 聊天截图逐项（5 类 CHAT_*，仅图片、微信式布局）；招牌/门牌/路牌/铭牌 OCR 扫描（4 kind，pending）；未成年人身份证生日推断 + `/minor-strict`；时间模糊 `time_blur`；组合风险 `combination_risk` |
| P1-B 第一波 | Office 内嵌图默认自动脱敏；隐藏对象确认（`media_confirmation_required`）；`strict_office_media` 剥离/置白；CSV/TSV 列级+多级表头、导出形状不变；PDF 文本层内嵌图（≥5%）→ `EMBEDDED_IMAGE` 整块涂黑+复检；audit `counts.office_media` |
| P1.5 | Office/PDF 内嵌图**文字 OCR** 规则脱敏（预算+开关+自检）；6 类证件标签（工作证/居住证/港澳通行证/台胞证/外国人永居证/银行卡，银行卡不整页）；CSV/TSV 原编码/BOM/换行保留（未知回退 UTF-8+warning）；护照号 `PASSPORT`（near 护照、surrogate），规则 103→104 |
| P0 修复 | PLY 三子格式导出后结构自检（header/数据段长度/顶点 center 平移/面逐字节），失败 ExportError 且删除成品、不再复制源文件；TIF Pillow 路径 Orientation 转正且成品无 274，rasterio 路径 warning 不转正（已知限制）、成品无 274 |
| 已知不修 | 83 双人低头侧脸（Haar 上限，YuNet 因 WIDER FACE CC BY-NC-ND 未随包）；聊天需 OCR/微信式布局；招牌无 OCR/白底不承诺；Office 内嵌图纯文字 P1.5、strict 改变外观；PDF 内嵌图仅文本层页/≥5%、异常格式降级；PLY ASCII 行式数据段长度可变化（顶点/面完整重读）；rasterio 大图路径不转正 Orientation 像素 |

## 六、变更记录

| 日期 | 变更 | 负责人 |
|---|---|---|
| 2026-10-19 | 本红线生效；方案 A 启动 | Lead |
| 2026-10-20 | P0 第一波落地（底座/条码/财务/图像/贸易/前端/TIF），回归门禁 + 正式基线机制建立；keep 策略不再被判残留、MCP 映射落盘、USCC 校验启用、Office 内嵌媒体二次确认、仅清理导出、LOCAL_CS 中性化语义 | Lead + 5 域开发 + qa |
| 2026-10-20 | 真实判决书漏识别修复（跨行案号/地址、标签姓名、竖排审判员、带空格金额）；新增 `joined_regex` 检测器与 `test_legal_detect.py`；基线规则 66→71 | Lead + qa |
| 2026-10-20 | **P0 第二波（PIPL 收口）**：PDF 隐藏数据剥离 + verify 拦截、图片 EXIF Orientation 转正、`pipl-cn`(21)/`table-cn`(11) 规则包 + 校验器、`numeric_cell` 数值写回、10 条规则 scope 扩 image/tiff、前端整图涂黑；基线规则 71→103（8 包）、对账单 +6 实体（预期）、`regression.py` 177 项硬检查、`run_all.py` 10 套件全绿 | Lead + 6 域开发 + qa |
| 2026-10-20 | **身份证 A 方案**（用户追加要求）：默认 `fields` 只遮头像/姓名/身份证号/住址，`whole` 可选整卡；jobs `id_card_mode` + `_visual_entities` 按档过滤 + 证件内 OCR 文字实体让位 + 照片较大 warning；API/前端选择器；`pipeline._RECT_ETYPE_KIND` 字段复检映射；回归新增 17 项真链路检查（API 默认/非法、fields 四字段+保留行、whole 整页、反面、无脸回退），`regression.py` **199 项**；5 样例无证件，规则/实体基线数值不变 | Lead + faces + designer + qa |
| 2026-10-20 | **身份证 A 方案增量**（用户再追加）：反面 `fields` 档也遮签发机关+有效期限（ID_AUTHORITY/ID_VALIDITY，国徽/标题/其余卡面保留）；jobs side/字段过滤 + pipeline 复检映射；回归新增反面 7 项真链路检查（201 项全绿）；5 样例无证件，规则/实体基线数值不变 | Lead + faces + qa |
| 2026-10-20 | **P1-A 第一波**：聊天截图逐项（CHAT_AVATAR/NAME/GROUP/TIME/INPUT；仅 image、微信式布局、合成 5 类 0 误报）、招牌扫描器（sign_targets：门牌/路牌/店招/单位铭牌，OCR 锚点+白纸门控）、未成年人提示+严格模式（`/minor-strict` 整卡+全部黑）、时间模糊 `time_blur`、组合风险提示；前端安全提示条/时间开关/新类型；`test_visual` 56、`test_sign_targets` 39、`regression.py` **215 项**、`run_all.py` **11 套件**；5 样例无新增，基线数值不变 | Lead + faces + codes + designer + qa |
| 2026-10-20 | **P1-B 第一波**：Office 内嵌图片自动脱敏 + 隐藏对象确认语义（strict 剥离/置白、audit `counts.office_media`）、CSV/TSV 列语义+多级/合并表头+导出形状不变、PDF 文本层内嵌图检测（`EMBEDDED_IMAGE` 涂黑+重解析复检）；`regression.py` **229 项**、`run_all.py` **14 套件**；5 样例无图片/文本层变化，基线规则/实体数值不变 | Lead + geo + trade + codes + qa |
| 2026-10-20 | **P1.5**：Office 内嵌图片文字 OCR 自动脱敏（预算/开关/导出后自检）、PDF 文本层内嵌纯文字图检测（note text=N → EMBEDDED_IMAGE）、证件类型扩展 6 类（按 doc 显示标签整卡涂黑；银行卡不整页）、CSV/TSV 原编码/BOM/换行保留、护照号规则 `cn.passport_no`（PASSPORT，near 护照）；规则 103→104；`test_office_media` 43、`test_pdf_embedded` 40、`test_visual` 61、`test_csv_table` 115 断言；`regression.py` **241 项**、`run_all.py` **14 套件**；5 样例实体/残留不回归 | Lead + geo + faces + trade + codes + qa |
| 2026-10-20 | **P0 修复**：二进制 PLY 导出结构自检（header/数据段长度/顶点 center 平移/面逐字节）失败即 ExportError 并删成品、不再复制源文件；三子格式回归 7 项；TIF Pillow 路径 Orientation parse/render/export 转正、KEEP_TAGS 移除 274，rasterio 路径 warning（已知限制）；新增 `test_mesh.py` 纳入 run_all（15 套件）；`regression.py` **251 项**；规则 104/5 样例实体 52/residual 0 不变；`eval_public_5.py` 为手动评测脚本不进 run_all | Lead + geo + qa |
