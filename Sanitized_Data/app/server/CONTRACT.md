# 数据隐站 · 引擎契约 v2

> 所有模块只依赖本文件描述的数据结构与 API。改动契约必须先改本文件。
> v2（2026-10-19 方案 A 启动）：新增页面图像扫描扩展点、Verify 视觉/码类复检、
> 未处理内嵌媒体二次确认、仅清理导出；安全与回归红线见 `docs/red-lines.md`。

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
| POST | `/api/jobs` | `{upload_id}` 或 `{path}`，`strictness` → `{job_id}` |
| GET | `/api/jobs` | 最近任务列表 |
| GET | `/api/jobs/{id}` | 状态：`{id, name, fmt, stage, progress, counts:{total,done,pending}, pages, error, warnings}` |
| GET | `/api/jobs/{id}/entities` | `{entities:[Entity...]}` |
| GET | `/api/jobs/{id}/preview/{page}?mode=orig\|out` | `PreviewDoc` |
| GET | `/api/jobs/{id}/render/{page}?mode=orig\|out` | `image/png`（仅 raster） |
| POST | `/api/jobs/{id}/mark` | `{page, rect:[x0,y0,x1,y1] 归一化 0..1, etype, propagate}` → `{added, propagated}` |
| POST | `/api/jobs/{id}/entities/{eid}` | `{action:"accept"\|"reject", replacement?}` |
| POST | `/api/jobs/{id}/accept-all` | → `{total}` |
| POST | `/api/jobs/{id}/export` | `{mode:"ai"\|"human"}` → `ExportResult` |
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
3. 任一不通过 → 删除成品、抛 `VerifyError`。

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
