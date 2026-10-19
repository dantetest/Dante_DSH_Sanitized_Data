# 数据隐站 · 引擎契约 v1

> 所有模块只依赖本文件描述的数据结构与 API。改动契约必须先改本文件。

## 0. 运行环境

- Python 3.12（`python` 在 PATH 上），Windows 10/11
- 可用三方库（已装）：`fitz`(PyMuPDF 1.27)、`docx`、`openpyxl`、`PIL`、`lxml`、`numpy`、`rapidocr_onnxruntime`、`cv2`、`cryptography`、`pypdf`
- **不可用**：GDAL、trimesh、tesseract、paddleocr
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

## 9. 前端契约

- 单页应用，`app/web/index.html` + `app.js` + `style.css`，由 `api.py` 静态托管。
- 视觉沿用 `原型.html` 的打字机主题（纸色 `#F4EEE1`、墨 `#1A1714`、朱红 `#B23A2E`、涂黑 `#0D0B09`）。
- 必须实现：投放 → 识别进度（轮询 `/api/jobs/{id}`）→ 对照台（raster 与 flow 两种渲染）→ 框选补漏 → 逐条确认 → 交付（两种导出）。
