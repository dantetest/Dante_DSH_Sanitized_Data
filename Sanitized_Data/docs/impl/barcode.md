# 条码 / 二维码 P0 实现说明（barcodes.py）

> **状态：P0 实现完成 · 2026-10-20 · 仓库 HEAD `fb90233` + 并行底座改动**
> 范围：`app/server/yinzhan/barcodes.py`（新增）+ `app/tests/test_barcodes.py`（新增）+
> `app/tests/fixtures/barcodes/`（新增测试图）+ `app/server/requirements.txt`（新增 1 行依赖）。
> **未改动** mediascan.py / pipeline.py / jobs.py / api.py / 解析器 / 规则包；集成由 Lead 负责。
> 方案依据：[docs/research/02-条码二维码识别与打码方案.md](../research/02-条码二维码识别与打码方案.md)（用户已批准方案 A）。

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| 能力 | 1D（EAN-13/8、UPC-A/E、Code128/39/93、ITF、Codabar…）+ 2D（QR/MicroQR/rMQR、DataMatrix、PDF417 系列、Aztec、MaxiCode）检出并返回打码框 |
| 默认策略 | **默认一律打码**：只返回命中框，不按解码内容判断；解码失败但检测到（`/detect`）也返回 |
| 主 / 兜底 | zxing-cpp 主引擎；cv2（`QRCodeDetectorAruco` + `barcode.BarcodeDetector`）仅在主引擎不可用或本页零命中时兜底 |
| 隐私 | `ScanHit.note` 只写「码制/引擎/是否仅检测」，**绝不写解码内容**；warning 不含 payload |
| 性能 | A4/300dpi 多码页：best 124ms / median 144ms；PDF 200dpi 无码文本页约 185ms（同机并发测试时上浮到 250–400ms，属机器负载） |
| 自测 | `python -X utf8 app/tests/test_barcodes.py` → **43 PASS / 0 FAIL / 0 SKIP**；`pytest app/tests/test_barcodes.py` → 9 passed |
| 集成冒烟 | Lead 已接入解析器与 verify 钩子：图片/PDF/TIFF 三载体"打码→verify 通过、复扫 0 命中"；"故意不涂→VerifyError 拦截"均验证通过；既有 5 样例 e2e 5/5、residual 0 |
| 回归门禁 | `regression.py` **164 项硬检查全绿（exit 0）**；`e2e.py` **5/5 residual 0**；均含本模块，未 push |

---

## 1. 交付物

| 文件 | 说明 |
|---|---|
| `app/server/yinzhan/barcodes.py` | mediascan 扫描器实现，import 时 `register_scanner("barcode", …, kinds=("qrcode","barcode"), priority=60)` |
| `app/tests/test_barcodes.py` | 自测（含 fixtures 生成器；可 `python -X utf8` 直跑或用 pytest） |
| `app/tests/fixtures/barcodes/` | 合成 fixtures（QR/DataMatrix/PDF417/Aztec/MicroQR/EAN-13/EAN-8/Code128/Code39/ITF/旋转/反色/低对比/遮挡/多码页/发票样式/小模块页）+ `manifest.json` + `README.md` |
| `app/server/requirements.txt` | 新增 `zxing-cpp>=3.1,<4`（其余不动） |
| `docs/impl/barcode.md` | 本文件 |

---

## 2. 依赖 spike（已装、已验、可离线）

| 项 | 值 |
|---|---|
| 包 / 版本 | `zxing-cpp 3.1.1`（本机已安装进项目 Python：`C:\Users\Dante\AppData\Roaming\Python\Python312\site-packages\zxingcpp`） |
| 安装方式 | `python -m pip install "zxing-cpp>=3.1,<4"`（需联网一次；之后离线可用） |
| Windows wheel | `zxing_cpp-3.1.1-cp312-abi3-win_amd64.whl`，**1,003,677 B** |
| SHA-256 | `29f98a91148171460b47a942d137ecc90c4b8097636f23cca65263a56bb025d3` |
| 许可证 | **Apache-2.0**（PyPI `License-Expression: Apache-2.0`）；允许商用/分发，需保留许可证与 NOTICE 声明 |
| 运行时依赖 | 无（`Requires-Dist` 为空；wheel 内含 `zxingcpp.pyd` 1,823,232 B + `__init__.py` + `py.typed`/`.pyi`） |
| Python 要求 | `>=3.9`（cp312-abi3，未来 Python 3.13/3.14 也可直接用同 wheel） |
| 模型/联网 | **无模型、无任何网络代码**：纯 C++ 解码库绑定，初始化不下载、不联网（满足 CONTRACT §0 禁网） |
| 离线安装验证命令 | `python -m pip download --no-deps --only-binary :all: -d <目录> "zxing-cpp==3.1.1"`；目标机 `python -m pip install --no-index --find-links <目录> "zxing-cpp>=3.1,<4"` |

### 2.1 离线打包要求（Lead 负责落地）

1. `desktop/app.spec`：在现有 `collect_all("rapidocr"/"onnxruntime")` 之外，对 `"zxingcpp"` 做 `collect_all`（或至少 `hiddenimports=["zxingcpp", "zxingcpp.zxingcpp"]` + 收集 `.pyd`）。
2. `desktop/build_win.ps1` 的打包自检里加一条：
   `python -c "import zxingcpp; assert zxingcpp.read_barcodes"`（打包目录内跑），防止"源码能跑、桌面版缺引擎"。
3. `deploy/` 离线包：放入上面那个 wheel（1.0MB），随安装包分发；安装脚本用 `--no-index --find-links`。
4. 体积影响：压缩后约 +1.0MB，解压后 `.pyd` 约 +1.8MB（相对现有 OCR 模型几百 MB 可忽略）。
5. 许可证合规：随安装包附 `zxing-cpp` 的 `LICENSE`（Apache-2.0）与 NOTICE 说明；产品"关于/许可证"页可加一行。
6. 注意：`requirements.txt` 未钉死 `==3.1.1` 而用 `>=3.1,<4`，是指望小版本升级兼容；**打包机需锁一个 wheel**（建议 3.1.1）保证可复现。

---

## 3. 接口与集成

### 3.1 扫描器契约

```python
# app/server/yinzhan/mediascan.py（Lead 拥有，未改动）
ScanHit(kind: str, rect: tuple[float, float, float, float],
        confidence: float = 0.9, note: str = "")

fn(image_rgb: np.ndarray, ctx: ScanContext) -> list[ScanHit]
# image_rgb: HxWx3 RGB（pdf 为渲染图）；rect 用传入图像像素坐标
```

本模块实现 `scan_page(image_rgb, ctx)` 并注册：

```python
mediascan.register_scanner("barcode", scan_page,
                           kinds=("qrcode", "barcode"), priority=60)
```

### 3.2 kind / note 约定

| 实际码制 | 返回 `kind` | `note` 示例 |
|---|---|---|
| QR / Micro QR / rMQR / DataMatrix / PDF417 / Aztec / MaxiCode 等所有 2D | `qrcode`（中文名"二维码"，`jobs._VISUAL_SPEC` 已存在） | `QR Code/zxing`、`Data Matrix/zxing`、`PDF417/zxing/detect` |
| EAN-13/EAN-8/UPC/Code128/39/93/ITF/Codabar 等 1D | `barcode`（"条形码"） | `EAN-13/zxing`、`Code 128/cv2` |
| 检出但解码失败 | 同码制 kind | 追加 `/detect` |

> 说明：为不改 Lead 的 `_VISUAL_SPEC`（当前只有 `qrcode`/`barcode` 两个 key），所有 2D 码统一映射为 `qrcode`；`note` 里保留真实码制方便排查/以后细分。如后续要把 DataMatrix/PDF417 拆成独立类型，需要 Lead 同步 `_VISUAL_SPEC` 与 verify 逻辑。
> **note 永不包含解码内容**（发票码可能含税号/金额/校验码）；本模块任何 warning 也不写 payload。

### 3.3 集成点现状（2026-10-20）

| 环节 | 状态 | 负责人 |
|---|---|---|
| `registry.load_plugins()` 自动 import `yinzhan.barcodes`（`_SCANNER_MODULES`） | ✅ 已就绪（Lead） | Lead |
| image/tiff/pdf 解析器调用 `mediascan.analyze(...)` 并写入 `doc.visual_marks` | ✅ 已就绪（Lead） | Lead |
| `jobs._VISUAL_SPEC` 增加 `qrcode→QRCODE/二维码/accepted/L3`、`barcode→BARCODE/条形码/accepted/L2` | ✅ 已就绪（Lead） | Lead |
| 导出涂黑（rect span 通路） | ✅ 既有逻辑直接可用（本模块只产框） | Lead |
| **Verify 复检钩子**：对成品再跑 mediascan，仍未打掉的码类标记应阻断 | ⏳ 待 Lead（红线 S7） | Lead |
| 打包（`collect_all("zxingcpp")`、离线 wheel、自检） | ⏳ 待 Lead | Lead |
| e2e/regression 全量（底座 ready 后） | ⏳ 待 Lead 通知 | Lead |

本模块**不需要** OCR 文本框（`ctx.options["text_boxes"]` 忽略）；PDF 200dpi 渲染图足够，无需额外 dpi 参数。

---

## 4. 算法与策略

### 4.1 引擎顺序（性能与召回的折中）

1. **zxing-cpp 主引擎**：`read_barcodes(gray, try_rotate=True, try_downscale=True, try_invert=True, return_errors=True)`。
   - 默认尝试旋转 / 缩小 / 反色；`return_errors=True` 让「检出但解码失败」的框也能拿到（打码不依赖解码成功）。
2. **cv2 兜底**：仅当 `zxing` 不可用 **或** 本页 zxing 零命中时执行：
   - QR：`QRCodeDetectorAruco.detectMulti`（detect-only，失败再试 `QRCodeDetector`），工作图最长边 ≤ `YINZHAN_BARCODE_CV2_MAX_EDGE`（默认 2200）后映射回原坐标；
   - 1D：`cv2.barcode.BarcodeDetector.detectAndDecodeWithType`，再补 `detect()`（检测到但没解出也要框）。
   - 代价：2000px 工作图约 QR 52ms + 1D 12ms；只在"主引擎没找到"的页上付。
3. **多尺度 / 分块**：
   - zxing 自带 downscale；本模块另做**大图分块**：最长边 > `YINZHAN_BARCODE_TILE_EDGE`（默认 4000）时按 12% 重叠切块，单页最多 `YINZHAN_BARCODE_MAX_TILES`（默认 16）块，超限告警（可能漏检，写 `doc.warnings`，不静默）；
   - 小图可选 2x 放大重试（`YINZHAN_BARCODE_UPSCALE=1`，默认关，避免倍增耗时）。
4. **合并**：同 kind、IoU ≥ 0.30 的框取并集（宁多涂不可漏），置信度取高，note 合并；单页上限 64 个命中。

### 4.2 静区外扩（返回 bbox 前完成）

| 码类 | 外扩规则 | 依据 |
|---|---|---|
| QR | 从 zxing `extra["Version"]` 算模块数 `21+4*(v-1)`，`module = 最短边/模块数`，四边各外扩 **4 模块**；拿不到版本按 29 模块估 | QR 规范静区 4 模块 |
| Micro QR | 外扩 **2 模块**（`11+2*(v-1)` 估模块数） | Micro QR 规范 |
| 其它 2D（DataMatrix/PDF417/Aztec…） | 短边 6%（≥3px） | 覆盖各自 1–2 模块静区 |
| 1D | 左右各 **≥10 模块**（按码制估总模块数：EAN-13=95、EAN-8=67、Code128≈11×len+35…），上下各 15% 高度 | EAN/UPC 左 11/右 7 取保守值；条码高度方向留余量 |

外扩上限：单方向不超过码框长边的 30%（2D）/25%（1D），避免把相邻正文整片盖掉；最终坐标由 mediascan 再 clamp 到图像内。

### 4.3 默认一律打码

- 检出即返回，**不做内容判断**；解码失败（`valid=False`）同样返回（`confidence=0.5`，note 带 `/detect`）；
- 1D 商品条码的"白名单例外"不在本模块实现（决策点 D3，默认关闭）；将来若做，也只在 Lead 的决策层做，不在扫描器里按 payload 过滤；
- 本模块只负责"哪里可能有码"，**是否涂、涂多大由现有 Entity/Decision 链路决定**（当前 `jobs._VISUAL_SPEC` 默认 accepted + black）。

### 4.4 隐私 / 内容不泄露

- `text` 字段只在局部变量里用于几何估算（如 1D 模块数按 payload 长度），**不返回、不写日志、不写 warning、不写 note**；
- 单测用哨兵串 `QS3CR3T` 与发票 payload 前缀做断言：任何输出 JSON/note 出现即 FAIL；
- 异常信息只保留异常类型（如 `zxing 解码失败：RuntimeError`），不带 payload。

---

## 5. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `YINZHAN_SCAN` | 1 | mediascan 总开关（Lead 的 mediascan 读取） |
| `YINZHAN_SCAN_BARCODE` | 1 | 条码扫描分域开关（mediascan 按 kinds 识别） |
| `YINZHAN_SCAN_DISABLE` | 空 | 显式禁用名单（如 `barcode`） |
| `YINZHAN_SCAN_DPI` | 200 | PDF 页扫描渲染 dpi（Lead 的 pdf_parser 读取，72–400） |
| `YINZHAN_SCAN_MAX_PIXELS` | 0 | 大图保护提示（mediascan 只告警；本模块用分块控制内存） |
| `YINZHAN_BARCODE_ENGINE` | auto | `auto` / `zxing` / `cv2` |
| `YINZHAN_BARCODE_CV2_MAX_EDGE` | 2200 | cv2 兜底工作图最长边 |
| `YINZHAN_BARCODE_TILE_EDGE` | 4000 | 超过该长边则分块扫描 |
| `YINZHAN_BARCODE_TILE_OVERLAP` | 0.12 | 分块重叠比例 |
| `YINZHAN_BARCODE_MAX_TILES` | 16 | 单页最多分块数（超出截断并告警） |
| `YINZHAN_BARCODE_UPSCALE` | 0 | 小图零命中时是否 2x 放大重试 |
| `YINZHAN_BARCODE_DEBUG` | 0 | 把单页扫描耗时写进 `doc.warnings`（排查用，含尺寸/耗时/命中数，不含内容） |

---

## 6. 验证记录

### 6.1 单测

```powershell
# 直跑（推荐，Windows 需要 -X utf8）
python -X utf8 app/tests/test_barcodes.py
# 结果：PASS 43 · FAIL 0 · SKIP 0 · 约 3–9s
# 性能记录：{"a4_300dpi_ms": 144.2, "a4_300dpi_ms_best": 124.4, "pdf_200dpi_no_code_ms": 184.7}
# 若与其它 teammate 的 OCR/回归任务并行（CPU 满载），best 可能 200~400ms，
# 单测按「≤200 PASS / 200~400 记为机器负载 WARN / >400 FAIL」处理，
# 算法成本以空闲时为准（本机空闲 best 124–163ms）。

# pytest（可选）
python -m pytest app/tests/test_barcodes.py -q
# 结果：9 passed
```

测试项：注册/接口契约、13 个单码 fixture（含旋转/反色/低对比）、遮挡码 4 个「检出但仍返回」、3 个 A4 页（多码 6/6、发票样式 2/2、小模块 3/3）、note 防泄露、7 个涂黑复检（涂黑后 0 命中）、既有样例零误报、性能、开关。

### 6.2 召回（fixtures 全绿）

| 类别 | 覆盖 | 结果 |
|---|---|---|
| 2D | QR、MicroQR、DataMatrix、PDF417、Aztec | 全部检出+解码 |
| 1D | EAN-13、EAN-8、Code128、Code39、ITF | 全部检出+解码 |
| 变形 | 旋转 90°、反色、低对比、中心遮挡 36% | 均返回命中（遮挡码为 `/detect`） |
| 多码/排版 | A4 300dpi 6 码同页、发票样式页、3/4/6px 小模块页 | ROI 全部命中 |

### 6.3 性能（本机 6C/Windows，含并行 teammate 任务时波动）

| 场景 | 耗时 |
|---|---|
| A4/300dpi 多码页（6 码） | best **124ms** / median **144ms**（目标 ≤200ms ✓） |
| PDF/200dpi 无码文本页（zxing 零命中 + cv2 兜底） | **≈185ms**（硬上限 800ms 内） |
| 单码小图（QR 246×246 等） | 1–70ms |
| 上表在 CPU 满载（其它 teammate 跑 OCR）时会升到 250–400ms；测试用 best 值衡量算法成本 | — |

### 6.4 集成冒烟（真实解析器 → 打码 → verify 视觉残留钩子）

用真实解析器 + `jobs._visual_entities` + `pipeline.export/verify` 跑图片 / PDF / TIFF 三类载体（临时脚本，未入库）：

| 用例 | 检出 | 打码导出 | verify | 成品复扫 |
|---|---|---|---|---|
| PNG 含 QR + 文字，正常涂黑 | `qrcode ×1` | ✅ | ✅ `verified=true, visual_residual=0` | qrcode 0 |
| PNG 同上，**故意不涂**（keep） | `qrcode ×1` | ✅ 原样导出 | ✅ 被拦：`VerifyError: 仍有 1 处视觉/码类目标残留：二维码@0` | qrcode 1 |
| PDF 文本层页 + 内嵌 QR，正常涂黑 | `qrcode ×1`（另检出 signature 1） | ✅ | ✅ `visual_residual=0` | qrcode 0 |
| PDF 同上，**故意不涂** | 同上 | ✅ 原样 | ✅ 被拦：`二维码@0` 残留 | qrcode 1 |
| TIFF 多帧（第 2 帧带 QR），正常涂黑 | `qrcode ×1`（page=1） | ✅ | ✅ `visual_residual=0` | qrcode 0 |
| TIFF 同上，**故意不涂** | 同上 | ✅ 原样 | ✅ 被拦：`二维码@1` 残留 | qrcode 1 |

> 结论：Lead 的 verify 视觉/码类残留钩子（`pipeline._visual_residual`，`QRCODE→qrcode`/`BARCODE→barcode`）与扫描器输出契合；「不涂 → 复检拦截 → 拒绝交付」链路可用。

### 6.5 回归门禁状态

- 2026-10-20 00:00 `python -X utf8 app/tests/regression.py`：**回归通过，164 项硬检查全绿（含 1 条软提示），exit 0**；
  - 开工时的 1 项硬失败（`image_parser._save_image` EXIF/ICC，底座 P0 进行中）已由 Lead 侧修复，本次为修复后结果。
- 2026-10-20 00:00 `python -X utf8 app/tests/e2e.py`：**5/5 解析成功，换名/涂黑 residual 均 0，exit 0**。
- 本模块只新增文件 + requirements 一行；上述回归/e2e 均在同一工作区（含本模块）通过。
- 未执行 `git push`。

---

## 7. 已知边界与后续

| # | 边界 | 影响 / 处理 |
|---|---|---|
| B1 | 大图分块上限 16 块；超限只扫前 N 块并告警 | 超大拼接图可能漏检；后续可异步/降采样策略（P2） |
| B2 | 极小模块（<2px/module）或强畸变/严重残缺 | 检测器物理极限；已用"高召回参数 + 人工复核 + 导出后 Verify"兜底，不承诺 100% |
| B3 | 所有 2D 统一显示为"二维码" | 若需区分 DataMatrix/PDF417 等，需 Lead 扩 `_VISUAL_SPEC` + verify（P1，可选） |
| B4 | cv2 兜底不做逐方向旋转（zxing 主引擎负责旋转） | 主引擎可用时无影响；仅 zxing 缺失的场景可能漏掉侧向 1D |
| B5 | 检测密集文本页时 zxing 会做全页搜索 | 无码页约 80–185ms 成本；若后续 PDF 页数多、性能吃紧，可加"文字层页先 150dpi 预筛"（P2） |
| B6 | `zv`（1D 白名单例外）未实现 | 决策点 D3 默认关闭；按用户审核意见再排期 |
| B7 | Office 内嵌图片 P1 未做 | 本批红线只要求"检测+阻断/确认"（Lead 负责 E3），条码扫描器已可供其复用 |
| B8 | Verify 钩子 | ✅ 已由 Lead 接入（`pipeline._visual_residual`，`QRCODE→qrcode`/`BARCODE→barcode`），集成冒烟已验证"不涂→拦截"；本模块无需改动 |
| B9 | 真实票据样例尚未采集 | 合成集召回好≠真实照片；P2 需用户提供/合规采集真实发票、营业执照、商品条码照片做调优 |
| B10 | PyInstaller 未实机验证 | 打包后需在干净 Windows 上跑自检（`import zxingcpp` + 解一个 fixture） |

---

## 8. 复现 / 维护

```powershell
# 依赖（联网一次；离线机器用 wheel --no-index）
python -m pip install "zxing-cpp>=3.1,<4"

# 自测（首次会自动生成 fixtures）
python -X utf8 app/tests/test_barcodes.py

# 重建 fixtures：删 app/tests/fixtures/barcodes 下 png+manifest 后重跑自测

# 端到端（集成后）
cd app\server; $env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py
```

维护注意：
- 升级 zxing-cpp 小版本时，重点回归：`return_errors` 行为、`extra["Version"]`、格式字符串（note 里用）、`position` 角点顺序；跑一遍 `test_barcodes.py` 即可发现。
- 修改 fixtures 生成逻辑后需重跑并提交新 PNG/manifest（sha256 会变）。
- 本模块不写日志、不落盘、不联网；所有解码内容仅在调用栈内存中短暂存在。
