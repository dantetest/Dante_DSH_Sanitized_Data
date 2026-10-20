# 数据隐站 · 引擎契约 v3

> 所有模块只依赖本文件描述的数据结构与 API。改动契约必须先改本文件。
> v2（2026-10-19 方案 A 启动）：新增页面图像扫描扩展点、Verify 视觉/码类复检、
> 未处理内嵌媒体二次确认、仅清理导出；安全与回归红线见 `docs/red-lines.md`。
> v3（2026-10-20 PIPL P0）：PDF 隐藏数据（附件/批注/表单/JS）默认剥离、图片 EXIF
> Orientation 转正、人脸大图分块/整头、`pipl-cn`/`table-cn` 规则包、
> `table_column`/`numeric_cell` 检测器、xlsx 数值写回、整页涂黑按钮。

## 0. 运行环境

- Python 3.12（`python` 在 PATH 上），Windows 10/11
- 可用三方库（已装）：`fitz`(PyMuPDF 1.27)、`docx`、`openpyxl`、`PIL`、`lxml`、`numpy`、`rapidocr_onnxruntime`、`cv2`、`cryptography`、`pypdf`、`zxing-cpp>=3.1`（Apache-2.0，条码/二维码）
- 可选三方库（P0 正射 TIF 用；未装时解析器必须降级并写 warning）：`rasterio`(BSD)、`pyproj`(MIT)
- **不可用**：GDAL 命令行、trimesh、tesseract、paddleocr
- **网络**：任何代码都不得主动发起网络请求（除 `127.0.0.1` 回环）

## 1. 目录

```
app/server/yinzhan/
  udm.py            数据模型（Lead 拥有，只读）
  registry.py       注册表与协议（Lead 拥有，只读）
  ocr.py            OCR 服务（Lead 拥有，只读）
  config.py         路径与常量（Lead 拥有，只读）
  rules.py          规则包加载（rule-dev 拥有）
  detectors/*.py    检测器（rule-dev 拥有）
  replacers/*.py    替换器（rule-dev 拥有）
  rules/*.yaml      规则包（rule-dev 拥有）
  parsers/*.py      解析器/导出器（parser-dev 拥有）
  pipeline.py       管线（Lead）
  jobs.py           任务与状态机（Lead）
  vault.py          加密映射库（Lead）
  audit.py          审计链（Lead）
  api.py            HTTP API（Lead）
  app.py            服务入口（Lead）
app/web/            前端（web-dev 拥有）
app/tests/          测试（qa 拥有）
samples/            样例文件（qa 拥有）
```

**写作用域严格隔离**：不要修改别人目录下的文件。

## 2. 数据模型（`udm.py`）

```python
Box = tuple[float, float, float, float]      # x0, y0, x1, y1；PDF 用 pt，图片用 px

@dataclass
class Cell:   text: str; row: int; col: int

@dataclass
class Block:
    id: str
    kind: Literal["text", "table", "image"]
    text: str = ""
    bbox: Box | None = None
    cells: list[Cell] = []
    ref: dict = {}          # 解析器私有定位信息，导出器自己解释
    page: int = 0
    attrs: dict = {}        # 字号、样式等

@dataclass
class Page:
    index: int
    width: float
    height: float
    blocks: list[Block] = []
    ref: dict = {}

@dataclass
class Document:
    doc_id: str
    path: str
    fmt: str                       # pdf | docx | xlsx | image | tiff | mesh | text
    pages: list[Page] = []
    meta: dict = {}                # 作者/设备/坐标参考系等，导出时默认丢弃
    extra: dict = {}               # 解析器私有（可放文件句柄）
    warnings: list[str] = []

# v3 约定（解析器必须遵守）：
# - image: parse 时按 EXIF Orientation 转正像素（parse/export/render 一致）；
# - pdf: meta 里记录隐藏数据计数：embedded_files:int、pdf_annotations:int、
#   pdf_forms:int、pdf_js:bool；可选 embedded_file_names/pdf_annotation_types（审计用，
#   不记内容）；doc.extra["pdf_hidden"]={"before":..., "removed":..., "kept":bool}
# - xlsx: 数值单元格 Block.attrs 增加 numeric:bool、date:bool、number_format:str，
#   仅元数据，不改变解析/导出语义。
# - Block.attrs 里表头列级/数值检测依赖解析器给出的 ref（xlsx: sheet/cell/row/col；
#   docx: t/r/c），不得移除。

@dataclass
class Span:
    block_id: str | None = None
    start: int = -1
    end: int = -1
    rect: Box | None = None        # 图像类或已算好区域的场景直接用矩形

@dataclass
class Decision:
    strategy: str                  # surrogate | mask | black | drop | keep
    replacement: str | None = None
    black: bool = False

@dataclass
class Entity:
    id: str
    etype: str                     # PERSON / ID_CARD / PHONE / ...
    label: str                     # 中文名，给人看
    value: str
    spans: list[Span]
    level: str = "L2"              # L0..L3
    confidence: float = 1.0
    origin: str = "regex"          # regex | dict | model | ocr | manual
    status: str = "accepted"       # accepted | pending | rejected
    decision: Decision | None = None

@dataclass
class Rule:
    id: str
    entity: str
    label: str
    level: str
    scope: list[str]               # 适用 fmt
    detect: list[dict]
    replace: dict
    pack: str = ""
    priority: int = 0
    enabled: bool = True
```

`Entity.decision` 由管线填充：`black=True` 表示涂黑；否则用 `replacement` 替换原值。

## 3. 解析器契约（`parsers/*.py`）

继承 `registry.Parser`：

```python
class Parser:
    format_id: str                      # "pdf"
    extensions: tuple[str, ...]         # (".pdf",)

    @classmethod
    def sniff(cls, path: Path) -> float: ...     # 0..1，用来判型

    def parse(self, path: Path, ctx: ParseContext) -> Document: ...

    def export(self, doc: Document, entities: list[Entity],
               decisions: dict[str, Decision], out_path: Path,
               options: dict) -> ExportResult: ...

    def render(self, doc: Document, page: int, mode: str) -> bytes | None:
        """mode: 'orig' | 'out'。返回 PNG 字节；flow 类格式返回 None。"""
```

`ParseContext` 字段：`ocr`（OcrService）、`workdir`（Path）、`options`（dict）。

`ExportResult`：`path: str`、`warnings: list[str]`、`bytes: int`。

**硬要求**：

1. `export` 必须是**破坏性重写**：不能只画黑框，必须真正移除/替换内容（PDF 用 `apply_redactions`）。
2. 必须清理元数据（见 §7）。
3. 未知/不支持的子特性不能静默丢弃，要 `warnings.append(...)`。
4. 解析失败不抛异常给用户，尽量降级并写 `doc.warnings`。
5. 图片解析器必须按 EXIF Orientation 转正像素（parse 与 export/render 一致），否则 OCR/人脸坐标错位且成品方向错误。
6. PDF 导出默认剥离附件/批注/表单控件/JS（`keep_pdf_hidden=True` 才保留），保存后重新打开成品复检计数必须为 0（或显式保留），否则 `ExportError`。
7. 数值单元格导出时：`numeric_cell` 实体写回**数字类型**并保留 `number_format`，账号类保持字符串；公式单元格不破坏。

注册：模块底部 `registry.register_parser(PdfParser)`。

## 4. 检测器契约（`detectors/*.py`）

```python
class Detector:
    id: str
    @classmethod
    def from_spec(cls, spec: dict) -> "Detector": ...
    def detect(self, doc: Document, rule: Rule, ctx: DetectContext) -> list[Entity]: ...
```

`DetectContext`：`strictness`（loose|standard|strict）、`ocr`、`logger`。

辅助：`registry.iter_text_blocks(doc)` 产出 `(block, text)`。

检测器**只负责找**，不负责替换。同一 `value` 在多处命中要合并成一个 Entity（多个 spans）。

内置检测器类型（`detect: [{type: ...}]`）：`regex`、`joined_regex`（同页相邻文本块拼接后匹配，命中映射回逐行 span，支持命名捕获 `value` 与 `value_clean: collapse_ws`）、`dict`、`ner`、`table_column`（按解析器 ref 的表格表头做整列检测，仅供 xlsx/docx）、`numeric_cell`（xlsx 数值单元格，按表头列语义生成 AMOUNT/ACCOUNT 实体）。

## 5. 替换器契约（`replacers/*.py`）

```python
def build(rule: Rule, value: str, ctx: ReplaceContext) -> Decision
```

`ReplaceContext`：`session`（会话级实体图，保证同一实体全文一致）、`etype`、`seed`。

内置策略：`surrogate`（保形假值）、`mask`（部分遮蔽）、`black`（涂黑）、`drop`（删除）、`keep`（保留）。

## 6. HTTP API（`api.py`，Lead 实现，前端按此对接）

所有响应 `application/json; charset=utf-8`，错误 `{"error": {"code": "...", "message": "..."}}`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 控制台页面 |
| GET | `/api/health` | `{ok, version, net, engine:{ocr, parsers[], packs[]}}` |
| POST | `/api/upload` | 原始字节，头 `X-Filename`（URL 编码）→ `{upload_id, name, size, sha256}` |
| POST | `/api/jobs` | `{upload_id}` 或 `{path}`，`strictness`，可选 `id_card_mode:"fields"\|"whole"`（默认 fields）→ `{job_id}` |
| GET | `/api/jobs` | 最近任务列表 |
| GET | `/api/jobs/{id}` | 状态：`{id, name, fmt, stage, progress, counts:{total,done,pending}, pages, error, warnings}` |
| GET | `/api/jobs/{id}/entities` | `{entities:[Entity...]}` |
| GET | `/api/jobs/{id}/preview/{page}?mode=orig\|out` | `PreviewDoc` |
| GET | `/api/jobs/{id}/render/{page}?mode=orig\|out` | `image/png`（仅 raster） |
| POST | `/api/jobs/{id}/mark` | `{page, rect:[x0,y0,x1,y1] 归一化 0..1, etype, propagate}` → `{added, propagated}` |
| POST | `/api/jobs/{id}/entities/{eid}` | `{action:"accept"\|"reject", replacement?}` |
| POST | `/api/jobs/{id}/accept-all` | → `{total}` |
| POST | `/api/jobs/{id}/export` | `{mode:"ai"\|"human", allow_unprocessed_media?, keep_pdf_hidden?}` → `ExportResult` |
| GET | `/api/jobs/{id}/file?mode=ai\|human` | 下载成品 |
| GET | `/api/audit?limit=50` | 审计记录 |
| GET | `/api/packs` | 规则包 |

`PreviewDoc`：

```json
{
  "job_id": "j_ab12", "page": 0, "total_pages": 1,
  "kind": "raster",
  "width": 595, "height": 842,
  "blocks": [{"id":"b0","kind":"text","text":"...","bbox":[x0,y0,x1,y1],"cells":[]}],
  "entities": [{
     "id":"e1","type":"PERSON","label":"人名","value":"张伟",
     "surrogate":"陈志远","level":"L2","confidence":0.98,
     "status":"accepted","origin":"manual",
     "spans":[{"block_id":"b0","start":4,"end":6}],
     "boxes":[[x0,y0,x1,y1]]
  }]
}
```

- `kind="raster"`：PDF/图片/TIFF。前端用 `<img src=render>`，`boxes` 是页面坐标系矩形。
- `kind="flow"`：DOCX/XLSX/TXT。前端按 `blocks` + `spans` 重排版渲染。

## 7. 出口自检（`pipeline.verify`）

导出后必须：

1. 用全部检测器重跑成品，`residual` 必须为 0 才允许落盘。
2. 扫描元数据黑名单（pdf `/Author` `/Producer` XMP、docx `docProps/*`、图片 EXIF/XMP/GPS、TIFF 地理标签）。
3. 重扫描成品里的视觉/码类目标（`visual_residual` 必须为 0）；未处理内嵌媒体必须已确认。
4. PDF 隐藏数据：成品重解析后 `embedded_files/pdf_annotations/pdf_forms/pdf_js` 必须为 0（除非导出时显式 `keep_pdf_hidden=true`）。
5. 任一不通过 → 删除成品、抛 `VerifyError`。

## 8. 安全红线

- 只监听 `127.0.0.1`，随机端口写入 `runtime.json`。
- 上传文件放临时目录，任务结束即删。
- 映射表用 `cryptography` AES-GCM 加密落盘，密钥在本机 `keys/` 下。
- 审计只记类型与数量，**不记原值**。
- 任何对外发包的行为都要显式拒绝（代码里不出现 `requests` / `urllib` 外呼）。
- 完整的更新红线与回归门禁见 `docs/red-lines.md`，改代码前后必须跑 `app/tests/regression.py`。

## 9. 前端契约

- 单页应用，`app/web/index.html` + `app.js` + `style.css`，由 `api.py` 静态托管。
- 视觉沿用 `原型.html` 的打字机主题（纸色 `#F4EEE1`、墨 `#1A1714`、朱红 `#B23A2E`、涂黑 `#0D0B09`）。
- 必须实现：投放 → 识别进度（轮询 `/api/jobs/{id}`）→ 对照台（raster 与 flow 两种渲染）→ 框选补漏 → 逐条确认 → 交付（两种导出）。

## 10. v2 扩展点（方案 A P0 起）

### 10.1 页面图像扫描（`yinzhan/mediascan.py`）

- 给「OCR 读不到、只能靠图像找」的目标统一入口：人脸、证件、车牌、路牌、门址、印章/签名、二维码/条码。
- 扩展模块在 import 时注册：`mediascan.register_scanner(name, fn, kinds=..., priority=...)`；`fn(image_rgb, ctx) -> list[ScanHit]`，`ctx.options` 会带 `text_boxes`（OCR 文本框，像素坐标）与 `dpi`。
- 解析器（image/tiff/pdf）在拿到页面图后调用 `mediascan.analyze()`，命中写进 `Document.visual_marks`；没有注册扫描器时 `has_enabled()` 为 False，零开销。
- 扫描器顶层模块（如 `yinzhan/barcodes.py`、`yinzhan/visual_targets.py`）由 `registry.load_plugins()` 自动 import；扫描器异常只写 `doc.warnings`，不得中断解析。
- `VisualMark.kind` 约定：`seal | signature | face | id_document | plate | road_sign | door_address | qrcode | barcode`。`jobs._VISUAL_SPEC` 定义 kind → etype/中文名/默认状态/密级；视觉类一律 `black` 决策。
- 性能开关：`YINZHAN_SCAN`（总开关）、`YINZHAN_SCAN_VISUAL`、`YINZHAN_SCAN_BARCODE`、`YINZHAN_SCAN_DISABLE`、`YINZHAN_SCAN_DPI`（PDF 扫描渲染 dpi，默认 200）、`YINZHAN_SCAN_MAX_PIXELS`。

### 10.2 Verify 复检（`pipeline.verify`）

```python
verify(exported, entities, parser, ctx, *, doc=None, options=None) -> dict
```

返回新增：`visual_residual: int`（重解析成品后仍扫到的同类视觉/码类目标数）、
`media: {"count": int, "confirmed": bool}`。任一残留/未确认媒体 → `VerifyError`，成品删除。

### 10.3 未处理内嵌媒体（P0：检测 + 二次确认）

- DOCX/XLSX 解析器在 `doc.meta["embedded_media"] = {"count": int, "parts": [...]}` 记录内嵌图片/OLE。
- `POST /api/jobs/{id}/export` 检测到该字段且 `allow_unprocessed_media` 不为 true 时：
  HTTP 400，`{"error": {"code": "media_confirmation_required", "message": "...N 张..."}}`；用户确认后重发 `{"mode": ..., "allow_unprocessed_media": true}`。
- MCP `sanitize_document` 同规则：参数 `allow_unprocessed_media`（默认 false）。
- 导出产物重新解析后仍有媒体且未确认 → Verify 拦截。

### 10.4 仅清理导出

- 没有可涂实体、但 `pipeline.has_cleanup_targets(doc)` 为真（`doc.meta["geo_tags"]`、`doc.extra["geo_params"]`、可清理元数据）时允许导出：只做元数据/地理参考剥离。
- 典型：只有 Geo 标签、没有可 OCR 文字的正射影像；此前会被「没有需要隐去的内容」卡死。

### 10.5 地理中性化语义（正射 TIF P0，已批准 D1/D2/D3）

- 默认 `geo_mode="neutralized"`：剥离源真实地理参考（ModelPixelScale/ModelTiepoint/
  ModelTransformation/GeoKeyDirectory/RPC 等），写入 **LOCAL_CS 本地任意 CRS**
  （GIS 可打开，但无真实世界坐标）；变换参数（dx/dy/θ/锚点/源 CRS WKT）写入
  `doc.extra["geo_params"]`，jobs 自动存进加密 vault 的 extra["geo"]。
- `geo_mode="strip"`（可选）：彻底不写任何 Geo tag。
- 语义约定（Verify 依赖）：`doc.meta["geo_tags"]` 只表示**成品里仍存在的真实地理参考**，
  中性化/剥离后必须为空；`doc.meta["geo_neutralized"]=True` 表示已做中性化；
  `doc.meta["geo_source_tags"]` 记录源文件被剥离的真实标签名（审计用）。
  **LOCAL_CS 结构本身不算残留，真实 CRS/RPC/ICC/EXIF/GPS/XMP 残留才算。**
- 解析器侧与 `pipeline.verify` 双重复检；`meta` 键名含 icc/exif/gps/xmp/iptc 且为真 → VerifyError。

### 10.6 规则与依赖纪律

- 新增规则包按域独立文件（`trade-cn.yaml`、`finance-cn.yaml` 等），默认全部启用；**新增规则必须过既有样例误报检查**。
- 新增校验器放域独立文件（`detectors/finance_validators.py`、`detectors/trade_validators.py`），`@register_validator` 注册；`regex_det` 通用新增 `unless_validate`（反向校验）与 `exclude_near`（上下文排除）。
- `common.yaml` 由 Lead 统一维护：USCC 已挂 `uscc_checksum`；银行卡排除身份证；固定电话排除发票代码。
- 新增依赖必须离线可装、许可证免费安全（MIT/BSD/Apache-2.0/PSF），并在 `desktop/app.spec` 与 `requirements.txt` 同步。**不得引入运行时联网组件**。

---

## 11. v3 / PIPL P0（2026-10-20）

### 11.1 图片朝向

- `image_parser` 的 parse/export/render 必须在入口调用 `_apply_orientation()`（PIL `ImageOps.exif_transpose`，仅单帧）再转 RGB；页面尺寸取转正后的像素尺寸。导出重编码不携带 EXIF，成品方向必须正确。
- TIFF 若走 Pillow 路径同理；rasterio 路径的 Orientation 支持列已知限制（P1）。

### 11.2 PDF 隐藏数据

- 解析：`pdf_parser` 统计附件/批注/表单/JS 写入 `doc.meta`（字段见 §2），并 warning "将默认剥离；`keep_pdf_hidden` 可保留"。
- 导出：先删附件/表单/批注/JS、清 `/Thumb`，再做 redaction 与 metadata/XMP；保存后重开复检，任一计数 >0（且未 keep）→ 删成品并 `ExportError`。
- Verify：成品 `embedded_files/pdf_annotations/pdf_forms/pdf_js` 任一非 0 且未 `keep_pdf_hidden` → `VerifyError`。
- `has_cleanup_targets`：仅有隐藏数据、无可涂文字的 PDF 允许"仅清理导出"。
- 审计：`counts["pdf_hidden"]`、`counts["keep_pdf_hidden"]` 记录剥离/保留痕迹。

### 11.3 图片目标

- `visual_targets`：人脸检测新增长边 >1600 分块（2×2 cap640 / 3×3 cap960）与整头外扩（上 35% 发际、左右 25% 耳廓、下 15% 下颌）；缩放检测时写 `doc.warnings`（会经 job.warnings 展示）；侧脸/低头/遮挡/极小脸为已知限制（P1 YuNet 可选，模型未随包）。
- 车牌：新增完整牌面色块检测（HSV 蓝/绿/黄底 + 矩形拟合），OCR 仅确认；不可读回退 OCR 框并 note `color/<色>;unconfirmed`。
- 证件：文本块数门控（≥18 块不判证件；12–17 块禁止 ≥25% 整页扩张，只涂锚点邻域），避免发票/合同整页误伤。
- 码类低置信门控：`/detect`（仅检测未解码）且面积 >5% 页面的候选不自动打码，写 warning 让用户人工确认（`jobs._visual_entities`）。

### 11.5 身份证脱敏档位（2026-10-20 用户选 A）

- 任务级参数 `id_card_mode`：`fields`（默认，正面只遮头像/姓名/身份证号/住址，反面遮签发机关/有效期）/ `whole`（整卡或整页黑）。
- 扫描器 `visual_targets` 对身份证正面返回 `id_photo/id_name/id_number/id_address`，对反面返回 `id_authority/id_validity`；`id_document` 保留给整卡档；note 约定：`side=front|back;fields=n;orient=0|90|180|270`，定位失败时 `fallback=1`。
- `jobs._visual_entities` 按档位过滤：fields 取字段（按 side 匹配；该面字段缺失/失败回退整卡并 warning）；whole 只取 id_document。
- `_VISUAL_SPEC` 新映射：`ID_PHOTO/ID_NAME/ID_NUMBER/ID_ADDRESS/ID_AUTHORITY/ID_VALIDITY` 对应中文「证件头像/证件姓名/证件号码/证件住址/证件签发机关/证件有效期」。
- Verify：`_RECT_ETYPE_KIND` 补上述字段 kind，成品重扫后按字段图 IoU 复检。

### 11.4 规则与表格

- 新增规则包 `pipl-cn.yaml`（21 条：社交/聊天 ID、昵称、快递、票号、CVV、VIN、IMEI、MAC、SN、IPv4/6、门牌/楼栋、航班/车次/座位、工号/工牌、空格手机号、datetime 默认关闭）与 `table-cn.yaml`（11 条表头列级）。
- 新增检测器：`table_column`（xlsx/docx 表头列级）、`numeric_cell`（xlsx 数值单元格）；新增校验器 `pipl_validators.py`（`pipl_*` 前缀）。
- 10 条敏感规则 scope 补 `image, tiff`：`cn.amount_cn`、`cn.credit_line`、`cn.iban`、`cn.reg_capital`、`cn.bank_routing_no`、`cn.pay_platform_id`、`cn.lawyer_license`、`cn.legal_party`、`cn.department`、`cn.sensitive_diagnosis`。
- xlsx 导出：`numeric_cell` 写数字类型、保留 number_format；账号写字符串；公式保留并置 fullCalcOnLoad。

### 11.6 P1-A（2026-10-20，进行中）

- 聊天截图逐项：isual_targets 新 kind chat_avatar/chat_name/chat_group/chat_time/chat_input；仅 mt=image 且 OCR 具备时启用；判定用布局信号（状态栏/标题/左右头像列/气泡/底部输入条）；jobs._VISUAL_SPEC 对应 CHAT_* etype，默认 accepted、逐项不整屏；PDF/文档页跳过。
- 招牌扫描器：新模块 yinzhan/sign_targets.py（mediascan 注册 door_address/road_sign/shop_sign/org_plaque，priority 55）；基于 OCR 文本框锚点+合并+外扩，白纸门控；jobs._VISUAL_SPEC 对应 DOOR_ADDRESS/ROAD_SIGN/SHOP_SIGN/ORG_PLAQUE（默认 pending L2）；egistry._SCANNER_MODULES 已含该模块。
- 解析器：mediascan.analyze 的 options 增加 mt（image/tiff/pdf），供扫描器做格式门控。
- 未成年人：Job.minor_detected（身份证生日推断 <14，仅身份证号，不做人脸年龄）；snapshot 返回 minor_detected/minor_strict；POST /api/jobs/{id}/minor-strict：人工启用后证件整卡 + 全部实体强制涂黑（不可换名），未检出时 400。
- 时间模糊：Job.time_blur（创建参数，默认 false）；只处理「完整日期+时刻」中的时刻，生成 TIME 实体，AI 替换为时段（凌晨/上午/中午/下午/晚上），日期保留；human 模式涂黑。
- 组合风险：Job.combination_risk={level,categories}（位置/证件/人脸/车辆/联系人/时间 ≥2 类），只写 warning，不自动改。
- 前端：投放台/对照台时间模糊开关、未成年红条+严格模式按钮、组合风险黄条+整图涂黑、聊天/招牌类型映射；创建 job body 增加 	ime_blur。
- 已知限制：聊天截图需 OCR 且为微信式布局；招牌无 OCR/白底不承诺；未成年人仅身份证生日推断；时间模糊默认关；组合风险只提示不消除重识别。
