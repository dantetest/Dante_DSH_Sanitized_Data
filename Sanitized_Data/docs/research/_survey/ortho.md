# 正射 TIF 脱敏 · 当前进度核查（HEAD 9930db6 + 工作区）

> 核查员：ortho-auditor（task-1）· 核查时间 2026-10-20 20:19–20:36（工作区快照）· 仓库 `D:\DSH_DESKTOP_WORKSPACE`，项目 `Sanitized_Data/`
> 口径：**只认「代码 + 实测」**；[码]=代码证据 file:line，[测]=本次实测输出，[文]=仅文档声称、本次未复测。
> 环境：Windows 11 / Python 3.12.9 / rasterio 1.4.3 (GDAL 3.9.3) / pyproj 3.7.2 / Pillow、RapidOCR。
> 工作区：HEAD 9930db6；另有 sign_targets/visual 等未提交改动（不属于本核查，未触碰）。`tiff_parser.py` 工作区仅 1 行改动（`options` 加 `"fmt": "tiff"`，见 §10）。
> **并发注意**：核查期间工作区被另一任务（P1-A：聊天截图/招牌/时间模糊）持续修改——`pipeline.py` 20:25、`README.md` 20:28、`CONTRACT.md` 20:30、`run_all.py` 20:33、`qa_common.py` 20:32、`regression.py` 20:34 均有新改动。本报告的 **regression 200 项计数与部分行号对应 20:21–20:27 的修改前快照**；P1-A 合入后项数/行号可能再变，最终数字以 Lead 在 P1-A 收口后重跑为准（TIF 相关结论不受影响：P1-A 只加扫描器 kind/文档，未动 georef/tiff 导出路径）。

---

## 0. 结论摘要

| 口径 | 旧基线（docs/research/03，fb90233·2026-10-19） | 当前实测（HEAD 9930db6 + 工作区） |
|---|---|---|
| P0 交付（tiff.md task-11 六项 + A1–A8） | 0% | **6/6 已落地并实测通过**（33/33 单测 + 200 项硬检查 regression + e2e 5/5） |
| DESIGN §3.6 六项口径 | 33% | **约 4/6 ≈ 67%**（差「坐标取整 10m/100m」「按密级降采样」两项，设计明确但不在 P0 范围） |
| 03 文档 18 项工程清单 | 33%（4✅+4🟡+10❌） | **15/18 ≈ 83%**（14✅ + 2🟡 + 2❌，见 §1） |
| 「坐标中性化 / 大图 / 16bit / sidecar / 无实体导出」 | 全部 ❌ 或部分 | **全部 ✅**（中性化 v1 平移+旋转、400Mpx 可处理、16bit/4 波段保留、sidecar 9 种全删、无实体可导出） |

**一句话**：旧文档「≈33%」已过时；**P0 范围内的正射 TIF 脱敏已可交付**，当前缺口集中在 DESIGN 更高口径（取整/降采样/多帧中性化/Orientation）与产品化（UI 暴露选项、真实大图性能/内存实测、打包体积）。

---

## 1. 逐项进度表

状态：✅ 已实现且有实测｜🟡 部分实现/有代码无针对性实测｜❌ 未实现。

### 1.1 地理参考 / 元数据 / 复检

| # | 项 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| 1 | 地理标签识别与剥离（33550/33922/34264/34735/34736/34737/42112/42113/RPC 50844） | ✅ | [码] `georef.py:63-104`（GEO_TAG_NAMES/REAL_GEOREF_TAGS/LOCAL_GEOREF_TAGS/META_LEAK_TAGS）；`tiff_parser.py:363-395`（rasterio 解析路径按 real/local 分流）；[测] 样例源 4 类 Geo tag → 成品仅 `[ModelTransformationTag, GeoKeyDirectoryTag, GeoAsciiParamsTag]`（34264/34735/34737），无 33550/33922/RPC；`verify_exported_geo` 返回 [] | LOCAL_CS 结构按 D3 允许保留（见 §5 口径） |
| 2 | 其它元数据（EXIF/XMP/IPTC/GPS/描述/设备/版权/XMP 700/33432 等） | ✅ | [码] `georef.py:82-95` META_LEAK_TAGS；rasterio 导出重写 GTiff profile（不复制源 tag）`tiff_parser.py:656-709`；[测] test_tiff `test_exif_xmp_stripped`、`test_metadata_clean` 通过；独立抽验成品 leak=[] | 旧基线「ICC 保留」问题已修 |
| 3 | ICC profile（34675）剥离 | ✅ | [码] 同上 + `_detect_meta_leaks` `tiff_parser.py:297-302`；[测] `test_icc_stripped` 通过（注入 34675 的夹具 → 输出无 34675） | — |
| 4 | 导出后复检（解析器侧 + 管线侧双保险） | ✅ | [码] `georef.verify_exported_geo` `georef.py:787-859`（本地 CRS/EPSG=None/泄漏 tag/transform==参数/GCP）；`tiff_parser.py:599-613` 导出后调用，失败即 `ExportError`；`pipeline.verify` `pipeline.py:643-766`（重解析 + geo_tags + 视觉残留）；[测] 独立抽验 `pipeline.verify -> {"verified": true, "residual": 0}`；`test_pipeline_verify` 通过 | `pipeline.verify` 本身不直接扫 TIFF tag（看不到 ICC/EXIF），TIFF 的 tag 级复检由解析器侧兜底——双保险语义成立但依赖解析器路径必执行 |
| 5 | sidecar 全谱清理（.tfw/.prj/.aux.xml/.ovr/.kml/.rpc/.rpb/.imd/.xml/.tif.xml） | ✅ | [码] `georef.read_linked_sidecars` `georef.py:862-890`；`tiff_parser._remove_sidecars` `tiff_parser.py:142-168`；[测] 独立抽验预置 9 种 sidecar → 导出后 **9/9 全删**；`test_sidecars_cleaned` 通过 | 只删成品名派生文件，不碰源文件（符合纪律） |
| 6 | 多帧 TIFF 处理 | 🟡 | [码] `scan_tiff` 多页/overview 区分 `georef.py:293-411`；Pillow 逐帧解析/导出 `tiff_parser.py:429-517,729-776`；[测] `test_multiframe_counted`、`test_parse_frames`、`test_export_strips_geo_and_warns` 通过（2 帧输出 2 帧、0 Geo、warning「未做坐标中性化」） | **多帧只剥离、不中性化**（有显式 warning）；多帧复检只扫首 IFD（追加帧由 Pillow 以无 tag 写出，实测安全） |
| 7 | overview（内部金字塔 IFD）不误判为多帧 | ✅ | [码] `scan_tiff` 用 tag 254 NewSubfileType 跳过 overview `georef.py:375-392`；[测] `test_overview_ifd_not_counted_as_page` 通过；独立抽验 4096² 输出 `frames=1, overview_frames=3` | — |

### 1.2 坐标中性化 / 可还原 / vault

| # | 项 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| 8 | 坐标中性化 v1（随机平移 + 旋转，写 LOCAL_CS 本地任意 CRS） | ✅ | [码] `georef.make_params` `georef.py:618-671`（θ∈±3°、dx/dy∈±200–5000m、scale=1、anchor=源 bbox 中心、`dst_transform=local∘src_transform`；θ 过小强制 ±0.5°）；`tiff_parser.py:683-688` 写 profile crs/transform；[测] 样例导出：CRS=`LOCAL_CS["Sanitized_Data local arbitrary"...]`、EPSG=None、`transform==params.dst_transform`（元素级 ≤1e-6）；θ=0.978706°、dx=-3992.14、dy=-2599.204；`test_no_real_georef`、`test_original_transform_replaced` 通过 | 只做「平移+旋转」，**未做坐标取整/降采样**（见 §4 P1） |
| 9 | 可还原（参数即钥匙） | ✅ | [码] `world_to_local/local_to_world/pixel_to_local/local_to_pixel` `georef.py:701-749`；`validate_params` 校验 `georef.py:759-782`；[测] 独立抽验 4 组像素往返 **max 误差 4.47e-9 px**；`test_params_reversible`、`test_dst_transform_consistency` 通过 | — |
| 10 | 参数入加密 vault（jobs `extra["geo"]`） | ✅ | [码] `jobs.py:598-605`（ai 模式 `vault.save_mapping(..., extra={"geo": geo_params})`）；`vault.py` AES-GCM（blob magic `YZ1`）；[测] 独立抽验：写入后二进制无明文 `4490/CGCS2000`、`load_mapping` 读回 `geo` 与参数完全相等；test_tiff 断言 `doc.extra["geo_params"]` 可 JSON 序列化 | human（涂黑）模式按设计不存参数 → 不可还原，与产品承诺一致 |
| 11 | `geo_mode="strip"`（彻底去参考） | ✅ | [码] `georef.geo_mode` `georef.py:124-127`、`strip_params` `georef.py:674-688`、`tiff_parser.py:688` 不写 crs/transform；[测] `test_strip_mode` 通过（成品 0 Geo tag、rasterio crs=None、`verify_exported_geo(expect_neutralized=False)==[]`） | **仅能通过环境变量 `YINZHAN_TIFF_GEO_MODE=strip` 或解析器 options 触发；HTTP API/UI 未暴露**（`api.py:217-225` 导出 options 只有 allow_unprocessed_media/keep_pdf_hidden），P2 缺口 |
| 12 | 无实体「仅清理」导出 | ✅ | [码] `pipeline.has_cleanup_targets` `pipeline.py:593-616`（geo_tags / extra.geo_transform / extra.geo_params / 元数据键；20:25 前快照为 585-608）；`jobs.py:560-564` 放行；`tiff_parser.py:362-386` 解析时写 geo_tags/extra.geo_transform；[测] 独立抽验：1200×900 纯 Geo 无文字 TIFF → entities=0、cleanup=True、导出成功、成品 LOCAL_CS/无泄漏；`test_noise_guard` 通过 | 这是旧基线列出的「最易忽略阻断点」，已修复 |

### 1.3 大文件 / 位深 / 波段 / 压缩 / 金字塔

| # | 项 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| 13 | 大图窗口化读写（>1.79 亿像素可处理） | ✅ | [码] rasterio 优先单页路径 `tiff_parser.py:327-427`；导出窗口循环 + `Window` 读写 `tiff_parser.py:697-708`；Pillow 回退超限明确中文 `ParseError` `tiff_parser.py:433-448`；[测] test_tiff `TestBigImage` 2 项通过（20000×20000=4 亿像素，parse<60s、export<180s 断言、frames=1、overview≥1、中性化复检通过、参数可还原；整套 33 项 74.65s 内含该用例）；`test_pillow_fallback_refuses_huge` 通过 | **峰值内存 1016MB 仅为 tiff.md 文档声称 [文]，本次未测**；大图夹具为全零稀疏 TIFF（输出 0.8MB），真实高熵正射输出体积/耗时会更大 |
| 14 | 16bit / 多波段 / 原始 dtype 保留 | ✅ | [码] 输出继承 `src.dtypes[0]`/`src.count`，`tiff_parser.py:641-645,656-664`；非 8bit RGB 的换名文本退化为涂黑+warning `tiff_parser.py:864-873`；Pillow 回退统一 8bit RGB（文档明示）；[测] `test_16bit_3band_preserved`、`test_4band_preserved` 通过；独立抽验 2048² uint16×4：输出 dtype=uint16、count=4、前 512 窗口**逐像素相等（maxdiff=0）**、nodata=65535 保留、warning 明示「4 波段前 3 波按 RGB，第 4+ 原样」 | 4+ 波段语义（RGBN/alpha）**不校验**（文档已声明） |
| 15 | 压缩 / 分块 / 内部金字塔 | ✅ | [码] `EXPORT_COMPRESS=deflate`、tiled 256、predictor、`build_overviews([2,4,8,16,32])` `tiff_parser.py:78-84,646-717`；JPEG 对非 8bit RGB 自动降级 DEFLATE + warning；[测] 独立抽验 4096²：导出 0.92s、DEFLATE、tiled block 256×256、3 级 overview；2048²（16bit×4）0.70s、2 级 overview；`TestScanTiff` overview 用例通过 | `.ovr` 不复制旧文件（sidecar 删除），金字塔写成品内部；源 JPEG 压缩会重编码为 DEFLATE（体积可能变大，文档已声明） |
| 16 | 预览渲染（大图不整图解码） | ✅ | [码] `TiffParser.render` `tiff_parser.py:521-537`：rasterio 路径按 `MAX_RENDER_EDGE=2000` 降采样读，失败回退 image_parser 渲染；[测] 独立抽验：样例原图 render 返回 PNG（28.5KB）、中性化成品 `mode="out"` render 返回 PNG（22.5KB） | HTTP `/api/jobs/{id}/render` 走同一入口 `api.py:383-391`；regression 的「渲染接口返回 PNG」检查在主路径 PDF 上（`regression.py:735-738`），**无 TIFF 专项 e2e** |
| 17 | 预览/人工框选（rect 归一化 → 像素涂黑） | 🟡 | [码] TIFF 导出窗口内按归一化 rect 涂黑：`_collect_paint_items` `tiff_parser.py:781-816`（`_denormalize_rect`/`_span_box_in_block`）、`_paint_window` `tiff_parser.py:840-903`；[测] 独立抽验：page 0 rect=(0.1,0.1,0.3,0.3) 人工 black 实体 → 成品对应像素区 `all_zero=True`、区外 max=232（未被误涂）；regression 的框选/整图涂黑检查在主路径 PDF（`regression.py:739-750`） | 后端链路对 TIFF 已实测可用；**缺 TIFF 专用 e2e/UI 实测**（大图 rect 坐标回算未覆盖） |
| 18 | 图面小字 OCR（图幅号/控制点号/坐标系名） | 🟡 | [码] 大图 OCR 用降采样预览并 warning 提示人工复核 `tiff_parser.py:397-426`；[测] e2e 样例 800×600：识别 3 处（图幅号/控制点号/坐标系名称），残留 0；`test_detect_entities` 通过；[文] tiff.md §8.2 声明「条码/小字等细节可能漏检」 | 真实 2–6 亿像素正射的小字/条码召回**无实测数据**；warning 是唯一兜底 |

### 1.4 降级 / 拒绝 / 命名审计

| # | 项 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| 19 | 未装 rasterio 的 Pillow 降级（明确 warning、不静默） | ✅ | [码] 惰性导入 + `has_rasterio()` `georef.py:33-57`；导出提示「未做坐标中性化」`tiff_parser.py:574-580`；>2×Pillow 上限中文报错 `tiff_parser.py:433-448`；[测] `test_pillow_fallback_parse_and_export`、`test_pillow_fallback_refuses_huge` 通过（monkeypatch HAS_RASTERIO=False） | 降级机只能剥离、不能中性化——文档已明示，属产品策略问题 |
| 20 | 无地理参考的普通 TIFF | ✅ | [码] `read_georef` kind=none → make_params 返回 strip 记录 `georef.py:630-638`；[测] `test_photo_style_no_georef` 通过（不报错、不中性化、只剥离） | — |
| 21 | 命名 / 交付编号 / 审计 | ✅ | [码] jobs 成品名 `_换名_`/`_已隐去_ + seal`、审计链 `jobs.py:576-597`；[测] e2e 产物命名与 README:321 一致；regression 审计链检查通过（见 §2） | 画面可见水印/编号仍未做（DESIGN 294） |
| 22 | Pillow→EXIF Orientation（TIF） | ❌ | [码] `_apply_orientation` 仅 image_parser 使用 `image_parser.py:328-346`；`tiff_parser.py` 全文无调用（imports 列表 `tiff_parser.py:35-38` 不含它），Pillow 解析 `tiff_parser.py:460-473` 直接 `convert("RGB")`，导出 `_safe_tiffinfo` 却保留 tag 274 `tiff_parser.py:54-55,124-139`；rasterio 路径不读 Orientation（CONTRACT 已列 P1） | **CONTRACT §11.1（`CONTRACT.md:328`）明写「TIFF 若走 Pillow 路径同理（必须转正）」，代码未实现**——真实的 contract-vs-code 缺口；带 Orientation 的多帧/无 rasterio TIFF 可能出现「像素未转正但成品保留 274」的显示/OCR 不一致。见 §4 P1-1 |
| 23 | DESIGN §3.6「精度：坐标取整 10m/100m」 | ❌ | [码] `make_params` 只有随机 dx/dy（200–5000m），无取整/栅格对齐；[文] DESIGN:270 | 不在 P0 决策范围（D1/D2/D3/D7 未含），属 DESIGN 完整口径缺口 |
| 24 | DESIGN §3.6「分辨率：按密级降采样（0.1m→1m）」 | ❌ | [码] `make_params(scale=1.0)` 已留 scale 参数，但全仓库无调用方传 scale≠1；`tiff_parser` 导出无重采样（窗口原分辨率拷贝）；[文] DESIGN:271 | 同上；实现入口已预留（scale），补选项+重采样+测试即可 |

**18 项工程清单折算**（03 §1.1）：✅14（#1–7、#10–13、#15–17、#20 中对应项）、🟡2（#14 敏感地物仅人工、#18 命名水印）、❌2（#8 取整、#9 降采样）→ **15/18 ≈ 83%**；DESIGN §3.6 六项 → 4/6 ≈ 67%；P0（task-11 六项 + A1–A8）→ **6/6 = 100%**。

---

## 2. 测试实测记录

> 环境说明（重要）：本机当前 DSH 沙箱把 `%TEMP%（C:\Users\Dante\AppData\Local\Temp\dsh-*）` 设为「可建目录、不可写文件」，而测试夹具用 `tempfile.mkdtemp()`（0700 ACL）创建，导致**裸跑测试出现 13 个 PermissionError/ERROR（环境问题，非产品缺陷）**。
> 处理：在 gitignored 的 `out\_test_tiff\` 放本地 `sitecustomize.py`（仅把 mkdtemp 改为默认 ACL 的 `os.mkdir`）+ `TMP/TEMP` 指向该目录后重跑；**未改仓库源码/测试**。所有结论以修正后的完整跑分为准。复现命令见 §9。

| # | 命令 | 结果 | 耗时 | 证据/备注 |
|---|---|---|---|---|
| T1 | `python -X utf8 app\tests\test_tiff.py`（含 400Mpx 大图用例，未设 SKIP_BIG） | **通过：33 项（skipped=0）**；`Ran 33 tests ... OK` | 74.65s（套件）/ 91.6s（含启动） | 日志 `out\_test_tiff\test_tiff_run2.log`；33 项与 tiff.md 声称一致 |
| T2 | `python -X utf8 app\tests\regression.py`（直接跑，取硬检查计数） | **200 项硬检查全绿（含 5 条软提示）**，`回归通过`；含 TIF `real_geo_residual=[]`、`local_crs=true`、多帧不丢失、预览/渲染/框选、审计链、MCP 等 | 62.88s | 20:24 快照（**P1-A 合入前**）；日志 `out\_test_tiff\regression_direct.log`；旧文档 166 项已过时（快照 200 项；P1-A 收口后项数可能再增） |
| T2b | `python -X utf8 app\tests\run_all.py --only regression` | `[PASS] regression`，`1/1 通过` | 111.83s（含外层进程/e2e 子流程，环境耗时波动不影响结论） | 日志 `out\_test_tiff\regression_run2.log`；命令层面另有 2 条软提示（e2e 总耗时 13.87s > 基线 9.23s×1.5、名片 OCR 4268ms>1583ms），属本机 OCR 冷启动/负载，非 TIF 回归 |
| T3 | `cd app\server; python -X utf8 ..\tests\e2e.py` | **5/5 全部通过、全部残留 0**；正射影像.tif：tiff·1 页·4 块·识别 **3 处**（图幅号/控制点号/坐标系名称）·解析 3835ms·检测 2ms；换名导出 **33774B**、涂黑 **26353B**；warning「检测到真实地理参考…导出时将剥离并做坐标中性化」 | 23.8s（独立进程；regression 内 13.87s） | 与 tiff.md §6「33774B/26353B」完全一致；解析 3835ms vs 文档 1048ms 属本机/OCR 预热差异 |
| T4 | 独立抽验脚本 `out\_test_tiff\indep_check.py`（自写、只读仓库代码） | 见下表 | 17.8s | 结果 `out\_test_tiff\indep_run.log` / `indep_result.txt` |

独立抽验（不依赖仓库测试断言）：

| 用例 | 实测结果 |
|---|---|
| 样例解析 | geo_source_tags=4 类、extra 有 geo_transform、entities=3、cleanup=True |
| 中性化导出 800×600 | 0.08s、33774B；LOCAL_CS、EPSG=None；成品 Geo 结构仅 34264/34735/34737；无 50844；`transform==dst_transform`；`pipeline.verify verified=true residual=0`；θ=0.978706°、dx=-3992.14、dy=-2599.204 |
| 可还原 | 4 组像素往返 max 误差 **4.47e-9 px** |
| vault | 密文 blob magic `YZ1`；不含明文 `4490`/`CGCS2000`；解密后 `geo` 与参数完全相等 |
| sidecar | 预置 9 种 → 导出后 **9/9 删除**（left=[]） |
| 无实体仅清理 | 纯 Geo 无文字 TIFF：entities=0、cleanup=True、导出成功、LOCAL_CS/无泄漏 |
| 16bit×4 波段 2048² | 0.70s；dtype uint16、count=4、`values_equal=True(maxdiff=0)`、nodata=65535 保留、2 级 overview、tiled 256 |
| 4096² 金字塔 | 0.92s；DEFLATE、tiled 256、3 级内部 overview |
| 预览/人工框选 | render(orig/out) 均返回合法 PNG；rect=(0.1,0.1,0.3,0.3) 涂黑后成品区内 `all_zero=True`、区外 max=232 |
| 400Mpx 大图 | 由 T1 `TestBigImage` 2 项覆盖并通过（parse<60s/export<180s/overview≥1/复检通过/可还原）；**峰值内存未测** |

---

## 3. 当前可交付结论（真实正射：能处理 / 会拒绝 / 降级）

**能处理（默认 `geo_mode=neutralized`，rasterio 可用、单页）**
1. 任意大小单页 GeoTIFF（窗口流式，`BIGTIFF=IF_SAFER`；test 覆盖 4 亿像素）：保留源 dtype（uint8/uint16/float 等）与波段数（1/3/4+），输出 DEFLATE + tiled 256 + 内部金字塔（≥1024 边长时）。
2. 任意常见源 CRS（EPSG/WKT，rasterio 可读）：剥离真实 Geo 结构 → 写 **LOCAL_CS 本地任意 CRS（EPSG=None，GIS 可打开）**；随机平移（±200–5000m）+ 旋转（±3°，至少 ±0.5°）；参数 JSON 入 `doc.extra["geo_params"]`，ai 模式自动 AES-GCM 入 vault `extra["geo"]`。
3. 成品可复算还原：`georef.local_to_pixel/local_to_world` 拿到原图像素/世界坐标；独立实测往返 <1e-8 像素。
4. 元数据全剥：Geo/EXIF/GPS/XMP/IPTC/ICC/描述/设备/版权；派生 sidecar（.tfw/.prj/.aux.xml/.ovr/.kml/.rpc/.rpb/.imd/.xml）全删。
5. 导出后双重复检（解析器 tag 级 + pipeline 重解析），命中即 `ExportError`/`VerifyError` 并删成品。
6. 无文字实体、只有 Geo 的真实正射：可「仅清理」导出（`has_cleanup_targets` 放行）；人工框选 rect 涂黑可在导出窗口内生效（大图不额外增内存）。
7. 预览：`/render` 对大图走降采样（最长边 2000），不整图解码给浏览器。

**会拒绝**
- 未装 rasterio 且像素 > 约 2×Pillow 上限（≈1.79 亿像素）：明确中文 `ParseError`（提示装 rasterio/切图），不静默失败、不产出半成品。
- 损坏/非 TIFF/IFD 越界：`scan_tiff` 返回 error → Pillow 回退或 `ParseError`/`ExportError`。
- 导出后仍有真实 CRS/EPSG/源 tiepoint/RPC/ICC/EXIF 等：`ExportError`（成品删除，jobs 负责）。
- 无任何可清理目标且无实体：jobs 报「没有需要隐去的内容」（正常业务拦截）。

**降级（有显式 warning，不静默）**
- **多帧 TIFF** → Pillow 回退：逐帧剥离 Geo/元数据，**不做坐标中性化**（warning 明示）。
- 未装 rasterio/pyproj 或 rasterio 打不开 → Pillow：只剥离；16bit/多波段/金字塔降级为 8bit RGB/LZW（warning）。
- 单页但非 8bit RGB 上的「换名文本」→ 退化为涂黑（保持 dtype/波段，warning 一次）。
- 大图 OCR/扫描基于降采样预览（1/2、1/5 …）：条码/小字可能漏检，warning 提示人工框选复核。
- CMYK/YCbCr：预览/OCR 按近似 RGB，成品按原波段保留（warning，查看器需支持）。
- 源 JPEG-in-TIFF → 重编码 DEFLATE（无损但体积可能变大）。
- Pillow 路径的 TIF Orientation **未转正**（见 P1-1，当前连 warning 都没有——真实缺口）。
- 带旋转分量的 geotransform 写 `ModelTransformationTag`：QGIS/ArcGIS 可开，个别只支持 north-up 的旧工具可能提示（文档已声明）。

---

## 4. 剩余 P1 / P2 缺口与工作量估算

> 对照 tiff.md §8「诚实边界」；P0 范围外但在 DESIGN/CONTRACT 口径内的优先。人日为 1 名熟悉 Python/rasterio 开发，含自测+文档。

| 级别 | 缺口 | 现状/证据 | 建议 | 人日 |
|---|---|---|---|---|
| **P1-1** | **TIF Orientation 未转正**（CONTRACT §11.1 要求 Pillow 路径转正，代码无调用；导出还保留 tag 274） | `CONTRACT.md:328` vs `tiff_parser.py:35-38,460-473,54-55`；rasterio 路径已列 P1 known limit | Pillow parse/render/export 调 `_apply_orientation`；导出不再保留 274（或写 1）；加 Orientation=6 多帧夹具回归 | 0.5–1 |
| **P1-2** | DESIGN §3.6「坐标取整 10m/100m」未做 | `georef.make_params` 仅随机 dx/dy | dx/dy 取整到 10/100m 栅格（可选档位），写参数/复检 | 0.5–1 |
| **P1-3** | DESIGN §3.6「按密级降采样 0.1m→1m」未做 | `make_params(scale=)` 预留但无调用/选项/UI | 窗口重采样（`Resampling.average/bilinear`）+ scale 选项 + 参数 + 测试；注意与分辨率/精度语义 | 1.5–2.5 |
| **P1-4** | 多帧 TIFF 不做中性化（仅剥离） | `tiff_parser.py:566-580`；`test_export_strips_geo_and_warns` | 若业务确有多页正射/堆栈需求：逐帧 transform+窗口重绘+全帧复检；若不做，UI 明示不支持 | 2–3 |
| **P1-5** | UI/API 未暴露 `geo_mode`、中性化状态/参数摘要 | `api.py:217-225` 只有 2 个选项；strip 仅环境变量；前端无「已中性化/未中性化」标记 | API 加 `geo_mode`（neutralized/strip）+ 导出结果加 `geo_neutralized` 字段；UI 展示 warning/状态 | 1–1.5 |
| **P1-6** | TIFF 预览/框选缺专项回归（后端已实测可用） | 独立抽验通过；regression 的 F8 检查在主路径 PDF | 加 TIFF e2e：preview/render/mark(rect)/整图涂黑 + 大图 rect 坐标回算 | 0.5–1 |
| **P2-1** | 真实大图性能/内存无实测（400Mpx 峰值 1016MB 仅文档；夹具是全零稀疏，输出 0.8MB 不具代表性） | [文] tiff.md §6 | 用真实或高熵 2–6 亿像素正射实测 parse/export/峰值 RSS/输出体积；按需调 `YINZHAN_TIFF_WINDOW/PREVIEW_MAX_SIDE` | 1–2 |
| **P2-2** | DESIGN 性能预算「1GB TIF 建金字塔 <90s、峰值 <1.5GB」未验证 | DESIGN:328 | 同上，补 1GB 级基准测试与基线 | 0.5–1 |
| **P2-3** | 4+ 波段语义（RGBN/alpha）不校验、换名只画前 3 波段 | tiff.md §8.2 #4；代码 warning 一次 | 加 `--band-semantics`/检测 ExtraSamples，明确提示或选项 | 0.5–1 |
| **P2-4** | CMYK/YCbCr 成品保留但查看器兼容性未验证 | `tiff_parser.py:388-395` warning | 真实 CMYK TIF 实测 QGIS/常见查看器；必要时转 RGB 选项 | 0.5–1 |
| **P2-5** | 打包体积未实测（估算 +60–100MB） | tiff.md §4.3 [文]；`app.spec:36` 已 collect_all | 构建 onedir 实测评测包体积/启动 | 1–2 |
| **P2-6** | 旋转 geotransform 与旧 GIS 工具兼容性 | tiff.md §8.2 #6 | 提供可选「仅平移（north-up）」模式或在交付说明写清 | 0.5–1 |
| **P2-7** | 大图 OCR 小字召回（降采样预览） | tiff.md §8.2 / §3 矩阵 | 分块裁剪 OCR 增强（跨 image/规则，独立立项） | 3–5（非本 P0） |

**P1 合计约 6–10 人日；P2 合计约 6–13 人日**（不含 P2-7 跨域增强）。其中 P1-1（Orientation）是**唯一的契约级实现缺口**，建议优先收口。

---

## 5. 文档口径矛盾 / 过时清单

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| D1 | `DESIGN.md:619`（M3 验收） | 「导出 TIF 用 gdalinfo 查不到任何地理参考」与已批准 D3（LOCAL_CS 可 GIS 打开）**字面矛盾**；中性化成品 gdalinfo 会显示 LOCAL_CS（只是 EPSG=None） | 改为「无真实世界坐标（无 EPSG/源 tiepoint/RPC）」；否则 M3 只有 strip 模式能过 |
| D2 | `docs/research/03-…md`（2026-10-19，基线 fb90233） | 通篇过时：标题「未动代码」、结论「正射≈33%」、§1.1 #2「ICC 保留」/#3「sidecar 只删 4 个」/#7–#13「中性化/取整/降采样/大图/位深/压缩全 ❌」/#16「无实体无法导出」 | 标注「历史基线（fb90233，已被 P0 取代）」或重算；引用时以本报告/`docs/impl/tiff.md` 为准 |
| D3 | `docs/research/00-总览与审核清单.md` | 同样过时：§0 表「正射 TIF ≈33%」、L84–87「中性化/取整/降采样未实现、无实体被阻断」、L130/L133 差距清单 | 同上，至少加过时提示 |
| D4 | `docs/impl/tiff.md:5` | 「regression 166/166 全绿」→ 本快照实测 **200 项硬检查全绿**（HEAD 基线增长，且 P1-A 合入前） | 更新为当前项数（以 P1-A 收口后重跑为准，含软提示） |
| D5 | `docs/impl/tiff.md` §6 数字 | test_tiff「25.4s」→ 本次 74.65s（33 项仍全过）；e2e TIFF 解析「1048ms」→ 本次 3835ms；regression「33.0s」→ 本次 62.9s | 注明测量机器/日期，或改为「<阈值」表述；数字不影响结论 |
| D6 | `docs/impl/tiff.md` §8 | 未列 **TIF Orientation（Pillow 路径）** 边界，而 CONTRACT §11.1 已把它写成必须做 | tiff.md §8 增一条；或按 P1-1 修代码 |
| D7 | `README.md:321` | e2e 表「正射影像.tif…地理标签已剥离」；当前默认成品是 **LOCAL_CS 中性化（有本地 Geo 结构、无真实坐标）**，不是「标签全剥离」 | 改为「已中性化（LOCAL_CS，无真实坐标）」；strip 模式才是全剥离 |
| D8 | `README.md:344` 已知限制 | 写「已实现 LOCAL_CS 中性化…更细的随机平移/加噪可后续增强」——「加噪」既未实现也无计划；且未提多帧不中性化、无 rasterio 只剥离、400Mpx 内存、旋转 geotransform、取整/降采样未做 | 按 §3 降级矩阵补全（用户可见的诚实边界） |
| D9 | `README.md:166` 支持表 | 「✓ ✓ ✓ 剥离地理标签，坐标可中性化」与实现一致，但未注明降级条件（多帧/无 rasterio/非 8bit 换名退化） | 表内加「见已知限制」脚注 |
| D10 | 实现 vs CONTRACT（非纯文档） | `CONTRACT.md:328`「TIFF 若走 Pillow 路径同理（Orientation 转正）」vs `tiff_parser.py` 无 `_apply_orientation` | 按 P1-1 修代码，契约不动 |
| D11 | `DESIGN.md:210` | 解析器表写「`.tif/.tiff` | GDAL」；CONTRACT 明确「不可用 GDAL 命令行」，实现走 rasterio（自带 GDAL 库）/Pillow | 表述可改为「rasterio（可选，内含 GDAL）/Pillow」 |

**已收敛（无矛盾）**：`CONTRACT.md:296-312`（§10.4/§10.5 语义 = 代码 + 测试）、`docs/red-lines.md:29`（F4）、`:119-123`（E5）、`README.md:166/344` 与 `docs/impl/tiff.md` 主体口径一致；`qa_common.tiff_geo()`（`qa_common.py:387-465`）已按 LOCAL_CS 语义区分 `real_geo_residual`/`local_crs`，regression TIF 检查（`regression.py:296-316`）已按 D3 放行 LOCAL_CS、按真实残留判失败。

---

## 6. 对「当前架构能否承载正射后续增强」的观察

1. **rasterio 可选依赖模式（推荐保留）**：`georef.py:33-49` 模块级 try/except + 函数内惰性 import + `has_rasterio()/has_pyproj()`；解析/导出按 `has_rasterio() and frames<=1` 分流（`tiff_parser.py:327-341`）；未装时 Pillow 降级并 warning。requirements 已加（`requirements.txt:22-24`）、PyInstaller `collect_all` 已挂（`desktop/app.spec:36`）、离线 wheel 与校验和已记录（tiff.md §4.2）。**P1/P2 地理增强可以继续在这条模式上加，不必硬依赖 GDAL**。代价是**双路径长期并存**：Orientation 缺口正是「Pillow 路径漏跟 CONTRACT」的例子；建议对每条新能力显式写「rasterio 路径 / Pillow 路径」行为矩阵并双测。
2. **内存模型可扩展但未封顶**：导出按 2048 窗口读、按 256 分块写，峰值≈窗口×波段×dtype + 输出块，P1-3 降采样/多帧可复用窗口循环；但大图路径在 `FULLREAD_MAX_PIXELS=40M` 以内会**整帧读取**做 OCR 预览（40Mpx×3B≈120MB，可接受），超过则降采样到 3000 边。更大窗口/更多波段会线性涨。DESIGN 的「<1.5GB / 1GB TIF <90s」**没有真实数据基准**，应先补 P2-1/P2-2 实测再谈承诺。
3. **UI 预览/框选架构可行**：`render()` 已按 2000 边降采样（`tiff_parser.py:521-537`），HTTP render 复用同一入口；框选 rect 在导出窗口循环里按像素绘制（`_paint_window`），大图不需要整图内存。**但**：① `geo_mode` 没进 API/UI（strip 只能环境变量）；② 前端无「已中性化/未中性化」状态；③ 无 TIFF 预览/框选 e2e（大图 rect 坐标回算未验证）。三点都是低风险增量，适合作为 P1-5/P1-6 一并做。
4. **verify 语义分层合理，但 TIFF 的 tag 级证据链只有一层**：`pipeline.verify` 重解析后看 `meta["geo_tags"]`（解析器按 real/local 分流，LOCAL_CS 落 `geo_local_tags`，不会误拦）；真正扫 ICC/EXIF/RPC/源 tiepoint 的是解析器侧 `georef.verify_exported_geo`。**该层不可省**：若未来出现不经过 `TiffParser.export` 的导出路径（如新格式/批量工具），pipeline.verify 对 TIFF tag 是盲的。另：多帧/多 IFD 复检目前只看首 IFD，未来做多帧中性化时必须扩展为全帧扫描。
5. **vault/可还原语义清晰**：`geo_params` 是否入 vault 由 jobs 的 ai/human 出口决定（`jobs.py:598-605`），human 不可还原符合产品承诺；但「可还原」只到「参数+成品→原图像素坐标」，没有面向用户的还原工具（`rehydrate.py` 目前只处理文字映射）——若对客户承诺「可还原正射」，P1 还应补一个坐标还原 CLI/说明（0.5–1 人日）。
6. **自研 IFD 扫描器（georef.scan_tiff）是双刃剑**：不解码像素、支持 classic+BigTIFF、按 tag254 跳 overview，支撑「无 rasterio 时的剥离/复检」和 sidecar/页数判断；但它绕开 GDAL，对 COG 的 mask IFD、SubIFD、私有标签等真实世界变体的判定未验证。**建议以 rasterio 为真实文件的 source of truth，scan_tiff 只作降级/复检辅助**，并在 P2-1 用真实数据集回归。
7. **性能数字的样本偏置**：大图夹具是全零稀疏 GeoTIFF（输出 0.8MB），只证明了「算法可跑通、时间上界成立」，不能外推真实正射的耗时/体积/内存；对外承诺前必须用真实样例重测（P2-1）。

---

## 7. 本次核查边界与未验证项

- **未改任何仓库源码/测试/规则/样例/产品文档**（唯一新增文件即本报告；中间产物在 gitignored 的 `out/_test_tiff/`）；未 commit/checkout/stash/push；工作区其它任务的未提交改动未触碰。
- **并发修改声明**：核查窗口内（20:25–20:35）另一任务（P1-A）正在修改 `pipeline.py`/`README.md`/`CONTRACT.md`/`run_all.py`/`qa_common.py`/`regression.py`；本报告测试数字对应 **20:21–20:27 的修改前快照**。P1-A 只新增视觉扫描 kind、API 字段与文档，未改 `georef.py`/`tiff_parser.py` 的地理路径，因此 TIF 结论不变；但 `200 项` 硬检查计数与个别行号在 P1-A 收口后需以 Lead 重跑结果为准。
- 未跑全套 `run_all`（按要求只跑 regression/test_tiff/e2e 及独立抽验）。
- 未测：400Mpx 峰值内存、1GB TIF 性能预算、真实高熵正射样例、BigTIFF（>4GB）写入、COG/mask TIFF、CMYK 真实文件、UI 浏览器实测、桌面安装包体积。
- [文] 标记的数字（如 tiff.md 的 1016MB/4.7s/7.0s、+60–100MB 打包体积、离线 venv 安装 50.9s）未复测，引用时需保留「文档声称」限定。

---

## 8. 附：关键文件与行号索引

| 主题 | 位置 |
|---|---|
| 可选依赖/常量/泄漏标签 | `georef.py:33-104` |
| geo_mode / strip 参数 | `georef.py:124-127,674-688` |
| TIFF IFD 扫描（多帧/overview） | `georef.py:293-411` |
| 读地理参考（rasterio 优先 + Pillow 回退） | `georef.py:487-552` |
| 中性化参数与可还原数学 | `georef.py:618-671,701-749` |
| 成品复检 | `georef.py:787-859`；`tiff_parser.py:599-613` |
| sidecar 全谱 | `georef.py:862-890`；`tiff_parser.py:142-168` |
| 大图/位深/中性化导出 | `tiff_parser.py:631-725` |
| Pillow 回退导出 | `tiff_parser.py:729-776` |
| 预览渲染 | `tiff_parser.py:521-537` |
| 人工 rect 涂黑 | `tiff_parser.py:781-816,840-903` |
| 无实体仅清理闸门 | `pipeline.py:585-608`；`jobs.py:560-564` |
| vault 入参 | `jobs.py:598-605`；`vault.py save/load_mapping` |
| TIF 回归检查 | `regression.py:296-316`；`qa_common.py:387-465` |
| 契约/红线 | `CONTRACT.md:296-312`、`:328`；`docs/red-lines.md:29,119-123` |
| DESIGN 口径 | `DESIGN.md:264-275,328,619` |
| README 口径 | `README.md:166,321,344` |
| 实现说明 | `docs/impl/tiff.md`（§1/§3/§6/§8） |
| 旧基线（过时） | `docs/research/03-…md:25,34-50,79-85,473-478`；`docs/research/00-…md:15,84-87,130-133` |

## 9. 复现命令（本机沙箱适配）

```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
# 沙箱下 %TEMP% 不可写；用 gitignored 目录做 TMP 并加载本地 sitecustomize 避开 tempfile 0700 ACL
$b = "$PWD\out\_test_tiff"; $env:PYTHONPATH=$b; $env:TMP=$b; $env:TEMP=$b
python -X utf8 app\tests\test_tiff.py
python -X utf8 app\tests\regression.py
cd app\server; python -X utf8 ..\tests\e2e.py
```
