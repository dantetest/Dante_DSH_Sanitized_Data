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
| F4 | 正射 TIF：8 类地理标签剥离、sidecar 清理、多帧、导出后 Geo 复查 | e2e + 导出后 tag 断言 |
| F5 | 双出口（换名可还原 / 涂黑不可还原）+ 映射库 + rehydrate | e2e 双模式 + 还原链路单测 |
| F6 | 印章/签署区/手写签名检测（PDF）与矩形涂黑通路 | e2e 委托代理合同 |
| F7 | 规则包热加载、include、优先级、同值合并、同值扩散 | 规则包完整性断言 + e2e |
| F8 | 框选补漏（rect 归一化）、逐条确认、预览渲染 | HTTP/预览断言（后续补 UI 冒烟） |
| F9 | MCP 四个工具：sanitize_document / redact_text / check_support / list_rules | 回归中逐个调用 |
| F10 | 站点 4 页面 + 图标 + 下载页/MCP 页可访问 | HTTP 冒烟（回归内置） |
| F11 | 桌面版启动链路（`desktop/main.py`、打包脚本、安装包配置）不回退 | 语法/引用检查 + 构建前自检 |
| F12 | 性能不倒退：OCR 页耗时、e2e 总时长不劣化超过阈值 | 基线软记录，超过 50% 报警 |

> 允许的"变化"：新增识别类型/更多实体（例如以后人脸、条码被检出）**不算破坏**；但"原有必须命中的变少 / 导出失败 / 残留>0 / 元数据回来了 / 服务外呼"一律算破坏。

## 三、回归门禁流程（每次改动必须走）

1. **开工前**：跑 `python -X utf8 app/tests/regression.py`，全绿才动代码（基线见 `app/tests/baseline/`）。
2. **每个可感知改动后**：先跑对应模块单测，再跑完整 regression + e2e；红项必须修好或回滚，不得"带病继续"。
3. **提交前**：`git status` 只包含预期文件；`git diff` 人工过一遍；不 push（按用户要求，等通知）。
4. **新增规则/依赖**必须额外过：
   - 新规则在既有样例上的**误报检查**（对比基线，新增命中要逐条解释）；
   - 新依赖：离线 wheel 可用、许可证（免费/安全优先：MIT/BSD/Apache-2.0/PSF）、打包体积与 PyInstaller 收集验证；不引入需要运行时联网或许可证不清晰的组件。
5. **CONTRACT 先行**：改数据结构/契约（`udm.py`、`registry.py`、`pipeline.py`）必须先更新 `app/server/CONTRACT.md`，再改实现。

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

## 五、变更记录

| 日期 | 变更 | 负责人 |
|---|---|---|
| 2026-10-19 | 本红线生效；方案 A 启动 | Lead |
| 2026-10-20 | P0 第一波落地（底座/条码/财务/图像/贸易/前端/TIF），回归门禁 + 正式基线机制建立；keep 策略不再被判残留、MCP 映射落盘、USCC 校验启用、Office 内嵌媒体二次确认、仅清理导出、LOCAL_CS 中性化语义 | Lead + 5 域开发 + qa |
