# PDF 文本层内嵌图片检测 / 整块涂黑（P1-B）

> **状态：P1-B 实现稿 · 2026-10-20**
> 改动文件：`app/server/yinzhan/parsers/pdf_parser.py`（增量）。
> 新增：`app/tests/test_pdf_embedded.py`、`app/tests/fixtures/pdf_embedded/`、本文件。
> **未改** jobs / pipeline / registry（映射与 verify 由 Lead 接入，见 §5）。

---

## 1. 要解决的问题

有文本层的 PDF 只处理文字层，页面内嵌的**证件照 / 聊天截图 / 二维码 / 扫描图**
不会被 OCR/扫描，图片里的敏感内容会漏。P1-B 不重建 PDF：对页面内嵌图片单独检测，
命中敏感目标时把**整块图片区域**作为黑块涂掉（保守，避免图片局部文字漏涂）。

## 2. 触发条件与流程

```
文本层页（blocks 非空、非扫描页）且 page.get_images() 非空
  → get_images(full=True) + get_image_rects(xref)
  → 只保留面积 ≥ 5% 页面的图片（YINZHAN_PDF_EMBEDDED_MIN_AREA）
  → 同区域去重、每页最多 8 张（YINZHAN_PDF_EMBEDDED_MAX_IMAGES，截断写 warning）
  → 按显示区域渲染 clip（YINZHAN_PDF_EMBEDDED_DPI，默认 150dpi）
  → mediascan.analyze(fmt="pdf_embedded") 先扫不需要 OCR 的：
       人脸 / 车牌 / 二维码 / 条码
  → 若无命中且 OCR 可用、图片像素 ≥ 6 万：对图片 clip 做 OCR，
    带 text_boxes 再扫：证件 / 聊天截图 / 招牌 / 车牌文字
  → 筛可信敏感命中（见 §3），有命中 → 产出一条
    kind="embedded_image" 视觉标记（页面图片矩形）
```

**纯文本 PDF、扫描页不受影响**：无图片的页不会进入本分支（只多一次
`get_images()` 调用，微秒级）；扫描页仍走既有整页 OCR/扫描链路，不产
`embedded_image`。小于 5% 页面的小图（logo、小图标）不处理。

## 3. 命中过滤（为什么正常配图不误报）

只有以下 kind 且满足置信度才把整块图片判为敏感：

| kind | 条件 |
|---|---|
| `qrcode` / `barcode` | **必须成功解码**；`note` 含 `/detect` 的仅检测结果不算 |
| `face` | confidence ≥ 0.75 |
| `plate` | confidence ≥ 0.80 且非 `unconfirmed` |
| `id_document` / `id_photo` / `id_name` / `id_number` / `id_address` / `id_authority` / `id_validity` | confidence ≥ 0.65 |
| `chat_*`（头像/昵称/群名/时间/输入框） | confidence ≥ 0.60 |
| `road_sign` / `door_address` / `shop_sign` / `org_plaque` | 采用 sign_targets 的置信度（≥0.76） |

普通风景/装饰图在测试夹具中 0 命中；名片/发票/合同文本页走文字层规则，不产
`embedded_image`。

## 4. 输出的 visual mark 格式

```python
VisualMark(
    kind="embedded_image",
    page=<页号>,
    rect=<页面显示坐标的图片矩形（与其它 visual_marks 同一坐标系）>,
    confidence=0.8,
    note="engine=embedded_image;n=<命中目标数>;area=<图片面积占页面比例，3 位小数>",
)
```

- `rect` 是图片在页面上的**显示区域**（旋转页经 `_to_page_space` 换算），
  导出时走既有的 rect redaction 链路整块涂黑；
- 同一页/同一区域（IoU ≥0.85）只产出一个；
- `note` 不含任何识别值，只有引擎/数量/面积；
- 无敏感命中 → 不产任何东西（无 warning、无标记）。

## 5. Lead 接点（当前工作区已出现对应预置改动，提交时确认）

1. `jobs._VISUAL_SPEC`：`"embedded_image": ("EMBEDDED_IMAGE", "内嵌图片（敏感）",
   "accepted", "L3")`；`_TYPE_LABEL["EMBEDDED_IMAGE"] = "内嵌图片（敏感）"`。
2. `pipeline._RECT_ETYPE_KIND`：`"EMBEDDED_IMAGE": "embedded_image"`（verify 视觉残留）。
3. 前端实体类型标签/颜色（可沿用 black 样式）。
4. 可选：把 `test_pdf_embedded.py` 加入 `run_all.py`（qa）。
5. 已接入时无需改 parser；未接入时 parser 仍会产 mark，只是 jobs/verify 不消费。

## 6. 导出后成品复检（解析器侧）

`PdfParser.export()` 在保存成品后、隐藏数据复检前执行
`_verify_embedded_images_black()`：对每个 `EMBEDDED_IMAGE` 实体的 rect 重新打开
成品、渲染区域，要求**黑像素（RGB max ≤45）占比 ≥95%**；不满足则写
`warning: 第 X 页内嵌图片区域未完全涂黑（黑像素 xx%），请人工复核`（不删成品，
最终由 `pipeline.verify` 的视觉残留兜底）。没有 `embedded_image` 实体时不渲染、
零额外开销。

## 7. 性能

| 场景 | 实测（本机） |
|---|---|
| 纯文本 PDF（`text_only.pdf`） | 中位 **72ms**，无扫描、无 warning（与基线逻辑相同） |
| 文本层 + 1 张正常配图（`normal_only.pdf`） | ~1.1s（整页扫描 + 图片 OCR 判负） |
| 文本层 + 3 张图（2 敏感 1 正常，`sensitive.pdf`） | ~2.8s（含二维码解码、证件 OCR、正常图 OCR 判负） |
| 小二维码图片（<5% 页） | ~0.2s，跳过图片扫描 |
| 扫描页（无文本层） | 走既有整页链路，不进入本分支 |

成本主要在图片 OCR（约 0.3–1.5s/张，按面积/内容变化）；每页最多 8 张、只处理
≥5% 页面的图。可用环境变量收紧/关闭：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `YINZHAN_PDF_EMBEDDED_MIN_AREA` | 0.05 | 图片最小面积占比（0.01–0.60） |
| `YINZHAN_PDF_EMBEDDED_MAX_IMAGES` | 8 | 每页最多扫描张数（1–64） |
| `YINZHAN_PDF_EMBEDDED_DPI` | 150 | 图片 clip 渲染 dpi（96–300） |
| `YINZHAN_PDF_EMBEDDED_OCR_MIN_PIXELS` | 60000 | 小于该像素数不做图片 OCR（1 万–400 万） |
| `YINZHAN_PDF_EMBEDDED_DEBUG` | 0 | 输出「处理/命中/耗时」warning（只含数量，不含识别值） |

## 8. 已知限制

- **只处理文本层页**；整页扫描页由既有扫描链路处理（已有 QRCODE/FACE 等标记）。
- **图片内纯文字规则 P1.5**：当前仅当图片内检出敏感目标才整块涂黑；普通文字
  截图（如无敏感目标的聊天记录）不会标记。
- 竖排/艺术字/低分辨率图片 OCR 不保证；人脸检测仍受 Haar 上限限制。
- **JPG2000/CMYK/遮罩等异常格式**：优先用 `page.get_pixmap(clip=...)` 渲染
  （fitz 统一转 RGB），解码失败会写「内嵌图片解码跳过…请人工复核」warning。
- **旋转页**：图片 rect 经显示坐标换算；极端旋转/裁切组合建议人工复核。
- 图片过多（>8 张）只扫面积最大的 8 张并写 warning，其余建议人工复核。
- 近满页的大图若命中敏感目标会整页涂黑（保守策略，产品可接受再放开）。

## 9. 测试

```powershell
python -X utf8 app/tests/test_pdf_embedded.py
# 结果：PASS 23 / FAIL 0 / SKIP 0（约 13s，真实 OCR）
```

覆盖：
- `sensitive.pdf`（文本层 + 内嵌二维码/虚构证件图 + 正常风景图）：2 条
  `embedded_image`、rect 对齐、note 格式、page/confidence；正常配图不命中；
- `normal_only.pdf` / `tiny_qr_only.pdf` / `scanned_qr.pdf`：0 条
  `embedded_image`（负样本 / 面积门控 / 扫描页）；
- `text_only.pdf`：无标记、无 warning、中位 72ms；
- debug 开关输出处理/命中统计；
- 端到端：`jobs._visual_entities` → `parser.export`（redaction）→ 成品图片区域
  黑像素 100%/98.8% → 重解析 0 条 `embedded_image` → `pipeline.verify` 通过。

**全量门禁**：`python -X utf8 app/tests/run_all.py`（11 域）应保持全绿；本改动
只影响「文本层页 + 有内嵌图片」的 PDF，普通纯文本 PDF 与既有 e2e/regression 不变。

夹具生成（全部虚构、可提交）：

```powershell
python -X utf8 app/tests/fixtures/pdf_embedded/generate_pdfs.py
```
