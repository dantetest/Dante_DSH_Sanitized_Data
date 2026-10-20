# Office 内嵌图片自动脱敏 + 隐藏对象枚举（P1-B）

> 状态：已实现（P1-B 第一项） · 2026-10-20 · 负责人：researcher-geo（task-31）
> 代码：`app/server/yinzhan/parsers/docx_parser.py`（新增 Office 媒体通用逻辑）、`xlsx_parser.py`（接入/复用）
> 测试：`app/tests/test_office_media.py`（30 项）+ `app/tests/e2e.py`（5/5）+ `app/tests/regression.py`（**213 项硬检查全绿**，当前 QA 口径）
> 背景：P0 只检测已加载 media/embeddings 并让用户二次确认，放行后原图保留（docs/red-lines.md 扩展点 E3）。P1-B 改为：**图片自动脱敏 + 隐藏对象枚举/确认/strict 剥离**。
> 上游确认：字段方案已由 Lead 确认（2026-10-20 消息）；jobs/verify/API 语义由 Lead 接。

---

## 1. 最终接口（字段名与 Lead 已对齐）

### 1.1 `doc.meta["embedded_media"]`（解析时写入；成品 re-parse 同结构）

| 键 | 类型 | 含义 |
|---|---|---|
| `count` | `int` | **无法自动脱敏、需用户确认**的隐藏对象数（= `hidden_object_count`）。**图片不计入**（导出自动脱敏） |
| `parts` | `list[str]` | = `hidden_objects`（保持 jobs `_embedded_media_info` 旧字段兼容；只列隐藏对象） |
| `images` | `list[str]` | 可自动脱敏的内嵌图片 zip 部件（`word/media/*` / `xl/media/*`，PIL 可解码） |
| `image_count` | `int` | 上述图片数量 |
| `hidden_objects` | `list[str]` | 图表 chart、OLE embeddings、activeX、含文本框/形状的 drawing、glossary、不可解码图片（emf/wmf/svg…）、`textbox:<part>#n` 伪条目 |
| `hidden_object_count` | `int` | 隐藏对象数量（= `count`） |
| `auto_redact` | `bool` | 解析器声明是否会自动脱敏图片（`YINZHAN_OFFICE_MEDIA=0` 时为 False，此时图片全部计入 count） |
| `unsupported_images` | `list[str]` | 不可解码图片（已计入 hidden_objects） |
| `textboxes` / `fields` / `footnotes` / `endnotes` | `int` | 审计细分：文本框/域/脚注/尾注数量（field/footnote 只告警，不计入 count） |

另写 `doc.meta["hidden_objects"] = [同上列表]`（审计便捷键，Lead 确认）。

**语义（jobs/verify 按此接）**
- 确认/拦截条件：`count > 0` 且未 `allow_unprocessed_media`；
- 纯图片文档（图片已自动脱敏）`count = 0`，**不再触发旧“内嵌图片未处理”确认**；
- 成品里图片仍存在是正常的（已脱敏）；图片 sanitize 由解析器导出后自检保证，verify 不重扫（P1 决策）。

### 1.2 导出选项

| 选项 | 默认 | 行为 |
|---|---|---|
| `redact_office_media` | `True` | 自动扫描并涂黑内嵌图片；`False`（env `YINZHAN_OFFICE_MEDIA=0` 同效）时图片原样保留 + warning，并计入确认数（parse 阶段按 env 判断） |
| `strict_office_media` | `False` | `True`：无命中图片置空白；隐藏对象尽量剥离（删部件 + 清 rels/内容类型 + 去 XML 引用），剥离不干净 → `ExportError` |
| `allow_unprocessed_media` | `False` | 现有“用户已确认保留不可自动处理媒体”放行开关（jobs 二次确认后传入；verify 同 option 放行） |

### 1.3 `doc.extra["office_media"]`（审计/排查）

```python
{
  "images_total": 3, "images_redacted": 1, "images_clean": 2,
  "images_blanked": 0, "images_skipped": 0, "images_failed": 0,
  "hits_by_kind": {"qrcode": 1},
  "hidden_total": 3, "hidden_stripped": 0, "hidden_kept": 2,
  "redact_office_media": True, "strict_office_media": False,
}
```

---

## 2. 实现要点

### 2.1 解析：zip namelist 全量枚举

`_classify_office_parts(archive, fmt)`（docx_parser.py，xlsx 复用）：

| 类别 | 匹配规则（DOCX / XLSX） | 计入 |
|---|---|---|
| 可自动脱敏图片 | `word/media/`、`xl/media/` 且扩展名 png/jpg/jpeg/gif/bmp/tif/tiff/webp | `images`（不计 count） |
| 不可解码图片 | media 下 emf/wmf/svg/ico/heic… | `unsupported_images` → `hidden_objects` |
| OLE/附件 | 路径含 `/embeddings/` | `hidden_objects` |
| 图表 | `word/charts/`、`xl/charts/` | `hidden_objects` |
| activeX | 路径含 `activex` | `hidden_objects` |
| 文本框/形状 | DOCX `w:txbxContent`（document/header/footer/footnotes/endnotes）；XLSX `xl/drawings/*.xml` 含 `xdr:txBody`/`xdr:sp`；DOCX `word/drawings/*.xml` 含 txbx | `hidden_objects`（伪条目 `textbox:<part>#n`） |
| 词汇表/外部链接 | `word/glossary/document.xml`、`xl/externalLinks/*` | `hidden_objects` |
| 脚注/尾注/域 | 部件名 / `w:instrText` 计数 | 只写计数与 warning（不阻断） |

> `python-docx` / `openpyxl` 保存成品时可能**静默丢弃未被引用的部件**（实测 DOCX 孤儿 EMF、XLSX 注入 OLE 被丢）；`doc.extra["office_media"]["hidden_kept"]` 按**成品实际 re-classify** 结果统计，不按源文件计数糊弄用户。

### 2.2 导出：图片自动脱敏（默认）

对每张可解码图片：

1. `Image.open` → 按 EXIF Orientation 转正（`_apply_orientation`，手机照片常见）；
2. 尺寸保护：`YINZHAN_OFFICE_MEDIA_MAX_PIXELS`（默认 40M）/`YINZHAN_OFFICE_MEDIA_TOTAL_PIXELS`（默认 300M）/`YINZHAN_OFFICE_MEDIA_MAX_IMAGES`（默认 60），超限跳过并 warning；
3. 扫描：`mediascan.analyze`（face/id/plate/qrcode/barcode/chat/sign 等，取决于已注册扫描器）；图片长边超过 `YINZHAN_OFFICE_SCAN_MAX_SIDE`（默认 3000）时先缩放扫描（坐标回算，warning“二维码等细节可能漏检”）；
   - 有 OCR 服务时为前 `YINZHAN_OFFICE_OCR_MAX_IMAGES`（默认 30）张图跑 OCR 并把 text_boxes 传给扫描器（身份证/车牌/聊天昵称等文本锚点类目标需要）；OCR 不可用时只做视觉/码类扫描并 warning；
4. 有命中：PIL 按命中框（外扩 2px，不透明黑）涂黑 → 同格式重编码（JPEG quality=92 / PNG / GIF / BMP / WEBP / TIFF-LZW，**不携带 EXIF/ICC**）→ zip 级替换**同一部件名**（OOXML rels/content-types 不变，Word/Excel 正常打开）；
5. 无命中：保留原图 + warning（`strict_office_media=True` 时改为置空白，保留尺寸/格式）。

### 2.3 隐藏对象：默认确认、strict 剥离

- 默认：保留图表/OLE/activeX/文本框等，`count > 0` → jobs 走既有 `media_confirmation_required` 二次确认；导出后 re-parse 成品 `count` 仍 >0，verify 需同 `allow_unprocessed_media` 才放行（与 Lead 语义一致）。
- `strict_office_media=True`：
  - 删除隐藏对象部件（charts/embeddings/activeX/glossary/drawings-with-text/unsupported images）；
  - `_clean_rels` / `_clean_content_types` 同步清理关系与内容类型声明；
  - `_office_strip_strict_xml` 删除 XML 里的 `txbxContent`/`object`/`oleObject`/`chart` 元素（document/header/footer/footnotes/endnotes + xl/drawings）；
  - 重新 classify 成品，仍有隐藏对象 → `ExportError`（不静默放行）。

### 2.4 导出后自检

- 重开成品 zip，提取**被替换过的图片**重新跑 `mediascan`（含 OCR 文本锚点），任一仍有命中 → `ExportError("导出后 Office 内嵌图片复检失败：…")` 并删除成品；
- strict 下再断言成品隐藏对象=0（否则 `ExportError`）；
- 无命中保留的图片按定义不会有命中（同一扫描器确定性），不重复扫描（成本）。

### 2.5 可调 env

| 变量 | 默认 | 含义 |
|---|---|---|
| `YINZHAN_OFFICE_MEDIA` | 1 | 自动脱敏总开关（0=关闭；parse 会把图片计入确认数） |
| `YINZHAN_OFFICE_STRICT` | 0 | strict 默认开关（导出 option 优先） |
| `YINZHAN_OFFICE_MEDIA_MAX_IMAGES` | 60 | 单次导出最多处理图片数 |
| `YINZHAN_OFFICE_MEDIA_MAX_PIXELS` | 40,000,000 | 单张图片像素上限 |
| `YINZHAN_OFFICE_MEDIA_TOTAL_PIXELS` | 300,000,000 | 累计像素上限 |
| `YINZHAN_OFFICE_SCAN_MAX_SIDE` | 3000 | 扫描前缩放长边上限 |
| `YINZHAN_OFFICE_OCR_MAX_IMAGES` | 30 | 单次导出最多跑 OCR 的图片数 |

---

## 3. 实测数字（2026-10-20，本机 Windows + Python 3.12.9）

夹具：虚构 PII 正文 + QR 码图 + 普通插图 + 注入 chart/OLE/EMF（DOCX）、真图表 + 注入 OLE（XLSX）。

| 用例 | 结果 |
|---|---|
| DOCX 解析 meta | `image_count=3`（QR+普通+face）、`hidden_object_count=3`（chart+OLE+EMF）、`count=3`、`auto_redact=True`、`unsupported_images=[word/media/image9.emf]`；warning 含“自动扫描/隐藏对象/不可解码图片” |
| DOCX 默认导出 | QR 图被涂黑（`mediascan` 重扫 0 命中，灰度均值<10）；普通图**字节完全不变**；chart+OLE 保留（EMF 被 python-docx 静默丢弃）；`doc.extra.office_media.images_redacted=1`、`hidden_kept=2`；verify 不带 option 拦截、带 `allow_unprocessed_media` 通过（residual=0）；python-docx 重开 OK |
| DOCX strict 导出 | `hidden_object_count=0`、chart/OLE 部件不存在；所有图片置黑（重扫 0 命中）；verify 不带 option 直接通过；python-docx 重开 OK |
| DOCX 负样本（仅普通图） | `count=0`、图片字节不变、warning“未检出”、verify 通过 |
| DOCX 文本框 | 解析 `textboxes>=2`、hidden 含 `textbox:…`；strict 后 `document.xml` 无 `txbxContent`、re-parse `textboxes=0` |
| XLSX 解析 meta | `image_count=2`、`hidden_object_count>=2`（chart+OLE）、`count` 同步、warning 完整 |
| XLSX 默认导出 | QR 涂黑；普通图字节不变；真图表保留 → `hidden_kept>=1`；verify 不带 option 拦截、带 option 通过；openpyxl 重开 OK |
| XLSX strict 导出 | `hidden_object_count=0`、`xl/charts`/`xl/embeddings` 不存在；图片置黑；verify 通过；openpyxl 重开 OK |
| 自检阻断 | monkeypatch “涂黑不生效” → 导出抛 `ExportError`（含“复检失败”）且成品被删除 |
| 开关 | `redact_office_media=False`：QR 原样保留、warning“自动脱敏已关闭”；monkeypatch env 默认关闭时 parse `auto_redact=False` 且图片计入 `count` |
| 门禁 | `test_office_media.py` **30/30 通过（26.0s）**；`e2e.py` **5/5、残留 0**；`regression.py` **213 项硬检查全绿** |

### 测试命令

```powershell
# 域内单测（30 项）
python -X utf8 app\tests\test_office_media.py

# 红线 e2e（必须 5/5、残留 0）
cd app\server
$env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py

# 红线回归（必须全绿）
python -X utf8 app\tests\regression.py

# 保留夹具人工查看
$env:YINZHAN_WRITE_FIXTURES='1'; python -X utf8 app\tests\test_office_media.py
```

夹具说明见 `app/tests/fixtures/office_media/README.md`（PII 全虚构，二进制不入库）。

---

## 4. 行为矩阵

| 场景 | 默认（redact=True, strict=False） | strict=True |
|---|---|---|
| 可解码图片 + 检出目标 | 涂黑 → 同格式重编码 → zip 替换；`images_redacted` | 同左 |
| 可解码图片 + 无命中 | 保留原图 + warning“未检出…P1.5” | 置空白（保留尺寸/格式） |
| 不可解码图片（emf/wmf/svg…） | 保留 + 计入隐藏对象（需确认） | 删除部件 + 清 rels/内容类型 |
| 图表 chart | 保留 + 计入隐藏对象（需确认） | 删部件 + 清 rels/内容类型 + 去 XML 引用 |
| OLE / activeX | 同上（openpyxl/python-docx 保存时可能已静默丢弃，按成品实况统计） | 同上 |
| 文本框（docx w:txbxContent / xlsx drawing shape） | 计入隐藏对象（需确认） | 删 XML 元素 / 删 drawing 部件 |
| 图片数量/大小超限 | 跳过 + warning（未脱敏保留） | 跳过 + warning（未脱敏保留，仍可能被后续人工复核） |
| 导出后自检 | 被替换图片重扫必须 0 命中 | 同左 + 隐藏对象=0 |
| verify（Lead） | `count>0` 且未 allow → 拦截；图片不重扫 | strict 后 `count=0` 直接通过 |

---

## 5. 需要 Lead 接的点（jobs/verify/API/UI）

1. **jobs 确认文案/语义（Lead 已确认将改）**：`_embedded_media_info` 读同一结构，但 `count` 现在只代表隐藏对象；提示文案应从“N 张未脱敏的内嵌图片”改为“N 个无法自动脱敏的隐藏对象（图表/文本框/OLE 等），是否仍要导出？”；图片已自动脱敏，不再触发确认。
2. **verify**：按 `count`/`hidden_objects` 判断 + `allow_unprocessed_media`；**不要**把“成品里图片数量 >0”当失败（图片存在是正常的）。图片 sanitize 由解析器自检保证（P1 决策）。
3. **API/前端**：`redact_office_media`（默认 true）、`strict_office_media`（默认 false）透传到 `pipeline.export`；普通用户无需暴露 strict，高级选项/严格模式可给；`allow_unprocessed_media` 保持现有二次确认 UI。
4. **audit（建议）**：`audit.append(extra={"office_media": doc.extra["office_media"]})`，记录自动脱敏图片数、命中类型、隐藏对象保留/剥离数（当前 audit 只记 media_count/confirmed）。
5. **红线文档**：旧“Office 内嵌图片需确认”更新为“图片自动脱敏 + 隐藏对象需确认/strict 剥离”；`has_cleanup_targets` 可考虑把 `embedded_media.count` 计入（“只有隐藏对象、无文字实体”的文件目前会卡“没有需要隐去的内容”，P1 可选）。
6. **UI 文案**：导出结果展示“已自动脱敏 N 张内嵌图片（命中类型）/ M 个隐藏对象已保留或剥离”。

---

## 6. 剩余风险与边界（P1.5 再补）

1. **内嵌图片里的纯文字 OCR 规则脱敏未做**（P1.5）：图片中截图文字（身份证号/手机号/地址）不会被 `geo/legal` 等文本规则命中，只有视觉/码类扫描器；文档已写明 P1.5。
2. **无命中保留图片的 EXIF/GPS/ICC 未剥离**：按需求“无敏感目标保留原图”，若照片带 GPS 元数据仍可能泄漏；P1.5 可加“统一重编码去 EXIF”（需权衡画质/体积）。
3. **扫描/OCR 上限**：图片过多/过大/超 OCR 预算时跳过并 warning（不静默）；二维码在缩放扫描时可能漏检（warning）。
4. **不可解码图片/图表/OLE 默认保留**，需要用户确认；strict 剥离可能让文档外观/功能变化（图表消失、OLE 丢失），属预期。
5. **伪条目**：文本框以 `textbox:<part>#n` 伪条目出现在 `hidden_objects`，不是真实 zip 部件；jobs/UI 展示时注意。
6. **XFA/动态表单、SVG 文本、SmartArt** 未专门处理（可能落在“不可解码/未知部件”告警或漏检）。
7. **PDF 内嵌图片**不在本任务范围（PDF 隐藏数据清理是另一任务）。
8. **本机 Office 打开验证**用 python-docx/openpyxl 重开（任务允许）；真实 Word/Excel 需用户测试集复验（尤其 strict 剥离后的文件）。
9. **话术**：自动脱敏图片 ≠ 匿名化；保留的隐藏对象/背景文字仍可能重识别。

---

## 7. 变更记录

| 日期 | 变更 | 负责人 |
|---|---|---|
| 2026-10-20 | 新增 Office 内嵌媒体全量枚举 + 图片自动脱敏（mediascan→PIL 涂黑→zip 替换）+ 隐藏对象确认/strict 剥离 + 导出后自检；新增 `test_office_media.py` 30 项；e2e 5/5、regression 213/213 全绿 | researcher-geo |
