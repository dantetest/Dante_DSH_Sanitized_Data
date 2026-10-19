# 03 · 无人机倾斜摄影三维建模与正射 TIF 脱敏：进度核查与后续方案

> **状态：进度核查稿 · 未动代码 · 2026-10-19**
> 仓库：`D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data`（git HEAD `fb90233`；git 根目录为 `D:\DSH_DESKTOP_WORKSPACE`）
> 核查人：researcher-geo（共享任务 task-3） · 核查方式：只读代码 + 可执行实测
> 边界：本稿只写本文件；未修改任何源码/规则/样例。全部实验临时文件在 `%TEMP%\geo_research\`；`app/tests/e2e.py` 按设计只写 `out/_test_*`（`.gitignore:4` 已忽略 `Sanitized_Data/out/`，实测 `git check-ignore` 通过）。
> 证据标注：`[码]`=代码位置，`[测]`=本次实测（§4 有命令与输出摘要），`[文]`=README/DESIGN 文档原文。

---

## 0 结论摘要

**一句话：正射 TIF 已经能“剥干净”并通过仓库自检，但 DESIGN 3.6 要求的“坐标中性化 / 降采样 / 大文件”都还没做；三维只覆盖 4 种桌面格式的“文本清理 + OBJ/PLY 顶点平移”，无人机倾斜摄影的主产物 OSGB、3D Tiles、LAS/LAZ 完全没有支持。**

直接回答用户“什么进度了”：

1. **正射 TIF 的“去地理参考”链路是可用的**：8 类 GeoTIFF 标签 + EXIF/XMP/设备/描述等元数据在导出重编码时被整体丢弃，`.tfw/.prj/.aux.xml` 同名 sidecar 会删，导出后还有一道 Geo 复查；`samples/正射影像.tif` 的 e2e 实测“3 处识别、换名/涂黑导出残留 0、地理标签已剥离”。`[码 tiff_parser.py:22-98, 206-208]` `[测 §4.2/4.4]`
2. **但“坐标中性化”没有实现**：全仓库没有任何随机平移/旋转/取整/降采样代码，`vault.py` 只存文字映射，坐标变换参数无处可入。`[码 grep 结果 §2.3；vault.py:52-66；jobs.py:454-456]`
3. **大图/高位深/多波段场景目前不可交付**：PIL 整图解码，`Image.MAX_IMAGE_PIXELS=89,478,485`，超过 2 倍（178,956,970 像素）直接抛 `DecompressionBombError`→`ParseError`（实测伪造 20000×20000 即报错）；16bit 会被压成 8bit、4 波段被压成 RGB。`[码 tiff_parser.py:122,165,139,178]` `[测]` §4.9/§4.8
4. **三维**：`.obj/.ply/.glb/.stl` 只做“文本层清理 + OBJ/PLY 顶点减包围盒中心”，STL 头置零、GLB 只改 JSON 字符串且几何 BIN 原样；**贴图/内嵌照片/EXIF/OSGB/3D Tiles/LAS-LAZ 全部未实现**；`samples/` 里没有三维样例，三维路径**没有自动化测试覆盖**。`[码 mesh_parser.py:1-23, 590-1137]`
5. **README 存在表述矛盾**：支持表写“TIF … 坐标可中性化”（README:166），已知限制又写“TIF 坐标中性化还没做随机平移，当前版本只剥离地理标签和元数据”（README:295）。两处必须收敛，见 §2.4。`[文]`
6. **完成度（口径见 §1.3）**：正射 TIF ≈ **33%**（DESIGN 3.6 六项口径 33%、18 项工程清单 33%，两口径一致）；三维 ≈ **20%**（DESIGN 3.6 口径 25%、17 项工程清单 38%，但按真实交付格式加权下调）；**DESIGN M3 里程碑整体 ≈ 25–30%**。M3 的验收字面条款“导出 TIF 用 gdalinfo 查不到任何地理参考”在小样张上实际已达成，但 M3 交付列表里的“坐标中性化”未达成，不能按 M3 完报告。

---

## 1 能力清单对照表

图例：✅ 已实现｜🟡 部分实现｜❌ 未实现。进度判断只按“需求是否被代码+实测证实”，不按代码量。

### 1.1 正射 TIF（DESIGN 3.6 六项 + 工程边界）

| # | 能力 | 要求出处 | 状态 | 证据（代码/实测） | 说明 |
|---|---|---|---|---|---|
| 1 | 地理标签剥离：`ModelPixelScale/ModelTiepoint/ModelTransformation/GeoKeyDirectory/GeoDoubleParams/GeoAsciiParams/GDAL_METADATA/GDAL_NODATA`（8 类） | DESIGN:268；CONTRACT §7 | ✅ | `[码]` tiff_parser.py:22-31（表）、36（白名单）、55-70（重编码只带 274/282/283/296）；`[测]` e2e 样张 4 类标签导出后为 0（§4.4），多帧样张两帧均 0（§4.7） | 白名单优于黑名单：未知标签也不会照抄 |
| 2 | 其它 TIFF 元数据（EXIF/XMP/IPTC/描述/设备/版权） | DESIGN:247-249；README:254 | 🟡 | `[码]` 同上；`[测]` 注入 270/271/272/305/306/315/700/33432 后导出全部消失（§4.5）；**但 ICC profile（tag 34675）原样保留**（实测输出 1036B，字节与输入完全相同） | ICC 不含定位信息，属“元数据清理不彻底”，非地理泄漏；`pipeline.verify` 也不检查 ICC |
| 3 | sidecar 清理：`.tfw/.prj/.aux.xml` | DESIGN:272；CONTRACT §7 | 🟡 | `[码]` tiff_parser.py:73-89（候选仅 4 个：`.tfw/.aux.xml/.prj/` 和 `*.tif.aux.xml`）；`[测]` 预置 7 个 sidecar，导出后删掉 4 个，`.ovr/.kml/.rpc` **仍在**（§4.6） | DESIGN 要求 `.kml/.ovr` 也处理，未覆盖 |
| 4 | 导出后复检（Geo 标签） | CONTRACT §7.2；DESIGN:619 | ✅ | `[码]` tiff_parser.py:206-208（重开成品扫描）+ pipeline.py:545-547（重解析后 meta.geo_tags 非空即 `VerifyError`）；`[测]` 输出重扫为空（§4.4/4.7） | 复检只认 8 类标签、只查当前帧（见 #15） |
| 5 | 多帧 TIFF | README:166；tiff_parser 文档串 | ✅ | `[码]` parse 逐帧 129-144；export 逐帧重绘 171-195；`[测]` 2 帧样张输入两帧都带 Geo 标签，输出两帧都无（§4.7） | 帧数按 `max(页数, 帧数)`，异常帧写 warning |
| 6 | 图面文字（图幅号/控制点号/坐标系名/经纬度）识别与涂黑 | DESIGN:273（人工兜底）；rules/geo-cn.yaml | ✅ | `[码]` geo-cn.yaml:10-70（5 条规则，scope 含 tiff）；`[测]` e2e 识别 3 处并涂黑、残留 0（§4.2） | 依赖 OCR；大图小字丢失见 §4.10 |
| 7 | 坐标随机平移 + 旋转中性化，参数入加密映射库 | DESIGN:269；README:166（宣称） | ❌ | `[码]` 解析器/pipeline 中无 random/rotate/affine/pyproj 代码（grep 结果见 §2.3）；vault.py:52-66 只有文字映射；jobs.py:454-456 只写 session 文字映射；`[测]` 导出前后像素坐标未变，无任何变换参数落盘 | 产品承诺与实现的最大缺口 |
| 8 | 坐标取整到 10m/100m 栅格 | DESIGN:270 | ❌ | 无对应代码 | 与 #7 配套 |
| 9 | 按密级降采样（0.1m→1m） | DESIGN:271 | ❌ | 无重采样代码；导出像素尺寸恒定 800×600（§4.4） | 与 #7 配套 |
| 10 | 大文件流式与内存 | README:295 已知限制未提；DESIGN:635 | ❌ | `[码]` tiff_parser.py:122-123、165-166 `source.load()` 全图解码；135-144/171-183 逐帧 `convert("RGB")` 后再存帧列表；`[测]` 峰值内存 ≈ 7.7 B/像素（24Mpx→215MB，6Mpx→77MB，§4.11） | 20000×15000（300Mpx）单帧 RGB 缓冲 858MB，且会先撞 PIL 上限（§4.9） |
| 11 | PIL 大图上限处理 | — | ❌ | `[测]` `Image.MAX_IMAGE_PIXELS=89,478,485`；>89.5Mpx 出 `DecompressionBombWarning`；>178.96Mpx 抛 `DecompressionBombError`，被 parse 包成 `ParseError: 无法打开 TIFF`（§4.9） | 真实正射常见 2–6 亿像素 → 当前版本**直接打不开** |
| 12 | 16bit / 多波段 / 原始色深保留 | DESIGN:271 隐含 | ❌ | `[码]` tiff_parser.py:139、178 `convert("RGB")`；`[测]` I;16 输入 65536 级输出只剩 256 级；RGBA 4 波段输出变 RGB，第 4 波段整体丢失；JPEG 压缩 TIF 被重编码成 LZW（§4.8） | 正射常见 16bit、RGBN/4 波段、JPEG-in-TIFF，均会被改变 |
| 13 | 压缩 / 分块 / 金字塔（.ovr）保留 | DESIGN:272 | ❌ | `[码]` tiff_parser.py:192 固定 `compression="tiff_lzw"`；无 tiled/overview 代码；输出 tag 只有 strip 结构（§4.4） | 大正射重编码后体积可能膨胀，GIS 打开变慢 |
| 14 | 敏感地物（招牌/车辆/条码/军事设施） | DESIGN:273 | 🟡 | 无自动识别；但**人工框选→涂黑链路可用**：jobs.py:285-320（rect 归一化→页面坐标）+ image_parser.py:267-314（按矩形涂黑）；`[测]` 未跑 UI，仅核实代码路径 | DESIGN 原文即“自动识别不可靠，必须人工框选”，人工能力算部分满足；条码/二维码无解码器（cv2 可用性见 §5.2） |
| 15 | 复检覆盖“非 Geo 元数据/ICC/全部帧” | CONTRACT §7.2 | ❌ | `[码]` pipeline.py:503-508（META_KEYS 只匹配 doc.meta 键名，TIF meta 里是 format/frames/geo_tags，检查不到 EXIF；`GEO_TAGS` 常量定义后未被使用）；537-547（ICC 不在检查项）；tiff_parser.py:92-98（`_verify_geo_stripped` 只开当前帧，异常时返回空=静默通过） | 复检对地理参考够用，对元数据/ICC 是盲区 |
| 16 | 无文字实体时也能“仅清理地理参考”导出 | 工程可用性 | ❌ | `[码]` jobs.py:436-437：`if not [e for e in job.entities ...]: raise YinzhanError("没有需要隐去的内容")` | **只有 Geo 标签、没有可 OCR 文字的真实正射影像无法从 UI 导出**，这是最容易被忽略的阻断点 |
| 17 | 变换参数入加密映射库、可还原 | DESIGN:269 | ❌ | vault.py:52-66 无 geo 字段；jobs.py:454-456 只存文字映射 | 与 #7 绑定 |
| 18 | 正射影像的“命名/水印/交付编号”等 | DESIGN:294 | 🟡 | `[码]` 成品文件名带交付编号 `_已隐去_<seal>.tif`（jobs.py:440-443）+ 审计链（audit.py）；图像内**可见水印/编号未做** | 文件名/审计已有，画面水印未做 |

### 1.2 三维

| # | 能力 | 要求出处 | 状态 | 证据（代码/实测） | 说明 |
|---|---|---|---|---|---|
| 1 | 支持格式：`.obj/.ply/.glb/.stl` | README:168 | 🟡 | `[码]` mesh_parser.py:548-559（只看扩展名）；`[测]` 四种格式均实测可解析（§4.12） | 支持的是“桌面中间格式”，不是倾斜摄影主格式 |
| 2 | OBJ 文本清理：`#` 注释 / `mtllib` / `usemtl` / `o` / `g` | DESIGN:281-284 | ✅ | `[码]` mesh_parser.py:188-199、590-648、782-852；`[测]` 注释里 `CGCS2000` 被涂黑、verify 残留 0（§4.12-H1） | `mtllib` 只改文本，不处理同名 .mtl 与贴图 |
| 3 | OBJ 顶点重定位（减包围盒中心） | DESIGN:281 | ✅ | `[码]` mesh_parser.py:202-240、789-846；`[测]` `v 500123.456 3450123.789 10.5` → `v -8.272 -8.1055 -0.75`（§4.12-H1） | 只平移，不旋转/不量化/不加噪 |
| 4 | PLY header 文本 + 顶点重定位（ASCII/二进制） | DESIGN:281-284 | ✅ | `[码]` mesh_parser.py:404-422、855-1018；二进制超过 256MB 只清头（54 行 `PLY_BINARY_MEMORY_LIMIT`）；`[测]` ASCII PLY comment 涂黑+顶点重定位（§4.12-H2） | 二进制路径依赖 numpy；超限降级为“仅头部清理+告警” |
| 5 | STL 头 80 字节置零 / ASCII 等长覆盖 | DESIGN:281-284 | ✅ | `[码]` mesh_parser.py:1021-1056；`[测]` 二进制 STL 头全 0、84 字节后几何逐字节一致（§4.12-H3） | 几何完全不动（代码显式 warning） |
| 6 | GLB JSON 文本等字节覆盖 | DESIGN:284 | ✅ | `[码]` mesh_parser.py:443-539、1059-1137；`[测]` JSON 中 `ContextCapture`/`XX测绘院` 被空格等长覆盖、chunk 长度不变、JSON 仍可解析（§4.12-H4） | 覆盖策略是“等字节空格/替换”，不能改 JSON 结构 |
| 7 | GLB 几何/BIN 重定位 | DESIGN:281、287 | ❌ | `[码]` 1059-1137 只改 JSON chunk；`[测]` BIN chunk 输入输出逐字节一致（§4.12-H4）；warning 明说“暂不支持几何重定位” | 顶点坐标原样保留 |
| 8 | 贴图文字 OCR→局部涂黑→回写 | DESIGN:282 | ❌ | 无任何 MTL/贴图/GLB image 处理代码 | 招牌/门牌/车牌泄漏面完全未覆盖 |
| 9 | OBJ + MTL + 外部贴图整包处理 | DESIGN:282、287 | ❌ | `[码]` `_export_obj` 只写单个 .obj 文件，不复制/不处理 .mtl 和贴图 | 交付 .obj 时贴图仍指向原文件或丢失；贴图内容未脱敏 |
| 10 | 内嵌照片 / EXIF / GPS | DESIGN:283 | ❌ | GLB BIN 内 image 不解析；OSGB 内嵌 JPEG 不支持；STL/PLY 无照片 | 倾斜摄影成果常带原始照片，未覆盖 |
| 11 | OSGB 分块倾斜模型（ContextCapture/大疆智图：Data\|terra_osgbs + metadata.xml + LOD 瓦片） | DESIGN:277；用户核心场景 | ❌ | 无 OSGB 解析器；`registry` 中无 `.osgb` 扩展名；无 metadata.xml/SRS/SRSOrigin 处理 | **无人机倾斜摄影实际交付的第一格式，能力为 0** |
| 12 | 3D Tiles（tileset.json / b3dm / glb / transform） | DESIGN:277 | ❌ | 无三维解析器；`.b3dm` 不在任何扩展名列表；`tileset.json` 只会被当纯文本（text_parser.py:125），无结构解析 | 1.1 已弃用 b3dm 改 glTF 内容，但存量交付仍多 b3dm |
| 13 | LAS/LAZ 点云（抽稀+加噪、CRS/VLR 清理） | DESIGN:285 | ❌ | 无 laspy 依赖、无点云解析器 | — |
| 14 | 三维坐标中性化（旋转/量化/加噪/参数入库） | DESIGN:269、281、285 | ❌ | 全仓库无 affine/rotate/random 的相关实现 | 只做了“减中心”这一步 |
| 15 | 整包重写（丢弃未知 chunk/私有扩展） | DESIGN:287 | 🟡 | OBJ/PLY 为流式重写；STL/GLB 为字节级补丁（未知块原样保留）；OSGB 无 | 与 DESIGN“整包重写”有差距 |
| 16 | 自动化测试覆盖 | README:275-285 | ❌ | `[测]` `samples/` 只有 5 个文件（pdf/docx/xlsx/png/tif），**没有三维样例**；e2e.py:92 只遍历 samples | 三维代码没有任何 e2e 守护 |
| 17 | 建模软件名/项目名/坐标系名等元数据检测 | DESIGN:284 | 🟡 | `[测]` OBJ 注释 `CGCS2000` 能被 geo-cn `cn.crs` 规则命中（依赖插件加载）；但项目名“XX新区”、瓦片名、坐标数值本身无规则覆盖 | 文本层可覆盖一部分，坐标数值/名称无专门规则 |

### 1.3 完成度计算（透明口径）

| 口径 | 计算方法 | 结果 |
|---|---|---|
| 正射 TIF · DESIGN 3.6 六项 | DESIGN:268-273 六行：地理标签✅1 + 坐标系❌0 + 精度❌0 + 分辨率❌0 + 附属文件🟡0.5 + 敏感地物人工🟡0.5 → 2/6 | **33%** |
| 正射 TIF · 工程能力清单 | §1.1 的 18 项：✅4 项、🟡4 项（各 0.5）、❌10 项 → (4+2)/18 | **≈33%** |
| **正射 TIF 综合** | 两口径一致，报告取 30–35% 区间中值 | **≈33%（30–35%）** |
| 三维 · DESIGN 3.6 | 5 个泄漏点 + 整包重写：顶点原点🟡0.5 + 贴图文字❌0 + 内嵌照片❌0 + 元数据🟡0.5 + 点云❌0 + 整包重写🟡0.5 → 1.5/6 | **25%** |
| 三维 · 工程能力清单 | §1.2 的 17 项：✅5 项、🟡3 项（各 0.5）、❌9 项 → (5+1.5)/17 | **≈38%** |
| **三维综合** | 工程清单里达标的是“低价值子项”（四种桌面格式的文本/头处理），按真实交付格式权重（OSGB/3D Tiles/LAS/贴图全为 0）下调 | **≈20%** |
| **DESIGN M3 里程碑** | TIF 33% × 0.5 + 三维 20% × 0.5 = 26.5% | **≈25%（25–30%）** |

> 备注：M3 验收条款原文是“导出 TIF 用 gdalinfo 查不到任何地理参考”（DESIGN:619）。小样张实测**已满足**（输出无任何 Geo tag）；但 M3 交付列同时写了“TIF + 三维 + 坐标中性化”，其中“坐标中性化”与三维部分未完成，因此 M3 **不能报完成**。

---

## 2 正射 TIF 详解

### 2.1 代码路径与调用链

```
api /api/jobs → jobs.JobService.parse_job（jobs.py:196 收集 doc.warnings）
  └─ registry.find_parser → TiffParser.parse（tiff_parser.py:119-156）
       ├─ Image.open + source.load()           # 整图解码（122-123）
       ├─ 逐帧：_scan_geo_tags → doc.meta["geo_tags"]（132-144, 150）
       ├─ source.convert("RGB") → _ocr_blocks（139-141；共用 image_parser.py:106-154）
       └─ warnings：「检测到地理参考（…），导出时将剥离」（151-153）
pipeline.detect → geo-cn.yaml 规则（图幅号/控制点号/坐标/坐标系名）
jobs.export / _export_to（jobs.py:429-494）
  └─ TiffParser.export（tiff_parser.py:158-214）
       ├─ source.load()（165-166）
       ├─ _safe_tiffinfo：白名单 274/282/283/296（172, 55-70）
       ├─ 逐帧 convert("RGB") + _apply_decisions（171-183，共用 image_parser.py:267-314）
       ├─ 存 frames 列表 → TIFF LZW 重编码，多帧 save_all（184-203）
       ├─ _remove_sidecars（205, 73-89）
       └─ _verify_geo_stripped 复查（206-208, 92-98）
pipeline.verify（pipeline.py:511-552）→ 重解析成品：原文残留 + meta 黑名单 + geo_tags
```

### 2.2 已实现部分（逐项）

1. **地理标签识别（8 类）**：`tiff_parser.py:22-31`。parse 时逐帧扫描并写 `doc.meta["geo_tags"]`，UI 能看到“检测到地理参考（ModelPixelScaleTag、…）”告警。
2. **剥离方式 = 白名单重编码**：`_safe_tiffinfo`（55-70）只把 274(Orientation)/282(XResolution)/283(YResolution)/296(ResolutionUnit) 带进新 TIFF；其余标签（8 类 Geo、EXIF/XMP/IPTC/描述/设备/版权、结构性 Strips）一律不照抄。注释明确说明“结构性标签照抄会写出损坏文件”（34-35）。这是正确的工程选择：**未知标签也不会漏**。
3. **sidecar 清理**：`_remove_sidecars`（73-89）删成品同名的 `.tfw/.prj/.aux.xml`（含 `*.tif.aux.xml` 两种命名）；只动输出名派生文件，不碰源文件。
4. **导出后复检**：重新打开成品扫 8 类标签（92-98），有残留写 warning；`pipeline.verify` 再重解析，`meta.geo_tags` 非空抛 `VerifyError` 并删除成品（pipeline.py:545-551、jobs.py:488-491）。
5. **多帧**：逐帧解析 OCR + 逐帧重绘 + `save_all`（129-144、171-195）。
6. **图面文字脱敏**：与图片共用 `image_parser._ocr_blocks/_apply_decisions`，geo-cn 规则对 TIFF 生效（图幅号 `H51G023045`、控制点号 `CP-2025-117`、坐标系名 `CGCS2000` 均在 e2e 中被识别处理）。
7. **人工框选补漏**：raster 预览上 `/api/jobs/{id}/mark` 传归一化矩形 → `jobs.py:285-320` 转页面坐标 → `image_parser._apply_decisions`（267-314）按矩形涂黑。对“军事设施/政府大院”等 DESIGN 要求人工框选的场景，**能力已具备**（但非自动）。
8. **任务告警可传达**：parse/export 的 warnings 汇总进 job.warnings（jobs.py:196、api 返回）。

### 2.3 未实现 / 部分实现（逐项 + 代码证据）

| 缺口 | 代码证据 | 影响 |
|---|---|---|
| **坐标中性化整体缺失** | 全仓库 grep `random|rotate|affin|translate|pyproj|proj4|EPSG` 在解析器/pipeline 中无命中（仅 ocr.py:245-249 的扫描纠偏 rotate、replacers/surrogate.py 的文字随机假值、jobs.py 的 job_id 随机）；`vault.py` 无 geo 段 | DESIGN:269 的核心交付物；产品目前只能“去掉坐标”，不能“给一套假坐标” |
| 坐标取整/降采样 | 无 | DESIGN:270-271 |
| 变换参数入加密映射库 | vault.py:52-66（仅 `{etype:{原值:假值}}`）；jobs.py:454-456 只在 ai 模式写文字映射 | 即便将来做中性化，也没有现成通道存参数 |
| 大文件/流式 | tiff_parser.py:122-123、165-166 全图 `load()`；171-183 每帧转换后 append 到 `frames` 列表（多帧时内存≈帧数×单帧）；共用逻辑 `image_parser._to_export_image`（319-325）与预览 `_render_image_bytes`（360-382）同样是“整图解码→缩略”，预览大图也会吃整图内存 | 峰值 ≈7.7 B/像素；见 §4.11 |
| PIL 像素上限 | Pillow `Image.MAX_IMAGE_PIXELS=89,478,485`，2× 上限抛错；tiff_parser 未捕获/未提示（仅 parse 外层包成 ParseError） | >179Mpx 的影像**打不开**；真实正射常超 |
| 16bit/多波段 | 139、178 `convert("RGB")` | 16bit→8bit（值域压缩 256×）；4 波段→RGB（第 4 波段丢失）；CMYK/调色板也统一转 RGB |
| 压缩/分块/金字塔 | 192 固定 `tiff_lzw`，无 tile/overview 选项 | 无损重压缩、体积变化、GIS 性能下降 |
| sidecar 全谱 | 76-81 只列 4 个候选 | `.ovr`（金字塔）、`.kml`、`.rpc/.rpb`、`.imd`、`*.tif.xml` 等未处理（实测 `.ovr/.kml/.rpc` 残留） |
| ICC profile | `_safe_tiffinfo` 不带 34675，但 Pillow TIFF `_save` 会从 `im.info` 回退取 ICC（本机 Pillow 12.2.0 `TiffImagePlugin` 的 `icc = encoderinfo.get("icc_profile", im.info.get("icc_profile"))`）；`convert("RGB")` 会把 info 带过去 | 实测 1036B ICC 字节级原样保留；PNG 输出同样保留 ICC。ICC 不含坐标，但违背“元数据剥离”表述 |
| 导出前置条件 | jobs.py:436-437：无实体直接拒绝导出 | 只有 Geo 标签、OCR 无文字的正射影像**无法交付**（虽然 TiffParser.export 本身能处理空实体） |
| 复检盲区 | tiff_parser.py:92-98（只当前帧/异常静默返回空）；pipeline.py:503-508（GEO_TAGS 定义未用、META_KEYS 对 TIF meta 无效） | 多帧非首帧、ICC、EXIF 均不在“出口自检”覆盖内 |
| 图面小文字 | image_parser.py:28 `OCR_MAX_EDGE=2400` + ocr.py:37 `MAX_OCR_SIDE=1600` 双重缩放 | 见 §4.10 实测：6000×4000 画布只有 48px 行被识别，32/24/16px 全丢 |
| 敏感地物自动识别 | 无；visual.py 的红章/签名检测仅 pdf_parser.py:204-223 调用，图片/TIFF 不跑 | 招牌/车辆/条码只能靠人工框选 |
| 条码/二维码 | 无解码代码（cv2 在依赖里，可后续用 `cv2.QRCodeDetector`） | 见 §5.2 方案 |
| 命名/水印/编号 | 文件名带交付编号（jobs.py:440-443）+ 审计链已有；画面内可见水印无 | 画面水印为规划项 |

### 2.4 README 表述矛盾（必须收敛）

| 位置 | 原文 | 与实际关系 |
|---|---|---|
| README:166（支持表 TIF 行） | `剥离地理标签，坐标可中性化` | 声称具备中性化能力 |
| README:295（已知限制） | `TIF 坐标中性化还没做随机平移；当前版本只剥离地理标签和元数据，坐标变换在路线图上` | 承认未实现 |
| DESIGN:269（产品设计） | `随机平移 (dx,dy) + 旋转 θ，参数存入加密映射库` | 设计要求 |
| README:260（安全边界） | `地理数据只能“降密”：坐标移了，河流道路的形状还在` | 话术已强调只降密 |

**结论**：`README:166` 的“坐标可中性化”是**超出实现的产品承诺**。在同一份 README 里，支持表说“可”，已知限制说“没做”，属于对外口径矛盾。建议本版本二选一：① 把 README:166 改为“剥离地理标签与元数据；坐标中性化为路线图能力”；② 或先实现 §5.2 的 P0-3 再保留该表述。对外材料（含投标/客服话术）建议按 ① 收敛，直到中性化有 e2e 验收。

### 2.5 导出产物形态（实测）

| 项目 | 输入 `samples/正射影像.tif` | 输出 `out/_test_正射影像_{ai\|human}.tif` |
|---|---|---|
| 尺寸/模式 | 800×600 RGB | 800×600 RGB |
| 帧数 | 1 | 1 |
| 压缩 | 无压缩（1440312B） | LZW（换名 48832B / 涂黑 43974B） |
| tag 列表 | 256,257,258,259,262,273,277,278,279,284,**33550,33922,34735,42112** | 只剩结构性 tag：256,257,258,259,262,273,277,278,279,284 |
| 地理标签 | 4 类 | 0 |
| sidecar | 无 | 无 |
| 复检 | — | `_verify_geo_stripped=[]`；pipeline.verify 通过、残留 0 |

> 注意：样张是 `app/tests/make_samples.py:166-186` 生成的**合成图**（800×600、3 类 Geo tag + 1 个 GDAL_METADATA、无 16bit/多波段/sidecar/大尺寸），e2e 通过**不能代表真实正射影像**。

---

## 3 三维详解

### 3.1 支持矩阵与代码路径

| 格式 | 解析抽什么 | 导出做什么 | 几何 | 贴图/内嵌图 | 代码位置 |
|---|---|---|---|---|---|
| `.obj` | `#` 注释、`mtllib/usemtl/o/g` 行；流式扫 `v` 得 vmin/vmax/顶点数 | 逐行重写：敏感文本覆盖；`v` 行减包围盒中心；其余原样 | ✅ 平移 | ❌（不读 .mtl/贴图） | mesh_parser.py:188-240、590-648、782-852 |
| `.ply` | header 的 `comment/obj_info`；format/element/property；顶点数 | 重写 header 文本；ASCII 逐行/二进制 numpy 就地减包围盒中心（>256MB 只清头） | ✅ 平移 | ❌（PLY 无内嵌图，但带纹理的 PLY 扩展未处理） | 245-422、651-677、855-1018 |
| `.stl` | 80 字节头文本；ASCII/二进制判定 | 敏感时头 80B 置零（二进制）或等长空格覆盖（ASCII）；**几何不动** | ❌ 透传 | ❌ | 427-438、680-704、1021-1056 |
| `.glb` | 读全文件→JSON chunk 的字符串（asset/extras/names/uris…） | JSON chunk 内等字节长度覆盖；**BIN chunk 与几何不动** | ❌ 透传 | ❌（BIN 里 images 不解析） | 443-539、707-740、1059-1137 |
| OSGB/3D Tiles/LAS/LAZ/.gltf/MTL | — | — | — | — | 无 |

**关键实现细节**：
- 判型只看扩展名（`sniff` 553-559），不看文件头；改扩展名会走错分支。
- OBJ 只用流式两遍扫描（202-230、799-831），不把大模型读进内存；PLY 二进制用 `numpy.frombuffer(bytearray(raw_data))` 整读（980-1018），受 256MB 上限约束；GLB `path.read_bytes()` 整读（709）——**GLB/OSGB 级大文件都没有流式方案**。
- 导出失败会 `_copy_fallback` 原样复制（156-165）并靠 `pipeline.verify` 兜底拦截；但 verify 对三维只重跑文本检测，**几何/贴图修改无从验证**。
- 所有格式都不写 `doc.meta` 的软件/项目/坐标系字段（本来也没解析），也**不处理 EXIF/GPS**（三维文件本身通常无 EXIF，但 GLB 内嵌 JPEG、OSGB 内嵌照片可能带）。

### 3.2 各格式实测结论（详见 §4.12）

| 场景 | 实测结果 | 是否达标 |
|---|---|---|
| OBJ 大地坐标 | `v 500123.456 3450123.789 10.5` → `v -8.272 -8.1055 -0.75`（减包围盒中心） | ✅ 平移达成 |
| OBJ 注释坐标系统名 | `# 坐标系：CGCS2000 …` → `# 坐标系：████████ …`，verify 残留 0 | ✅ 文本清理达成 |
| OBJ 项目名/瓦片名/mtllib | `XX新区`、`Tile_+000_+000_L20`、`mtllib 项目模型.mtl` 原样保留 | ❌ 无规则、无关联文件处理 |
| PLY header comment | `comment 项目 XX新区 CGCS2000` → `comment 项目 ████ CGCS2000`（只命中 `CGCS2000` 规则），顶点减中心 | 🟡 文本规则命中即处理 |
| 二进制 STL | 头 80B 全 0；84B 之后与输入逐字节一致 | ✅ 头清理；❌ 几何不动 |
| GLB | JSON 中 `ContextCapture`/`XX测绘院` 等长空格覆盖，JSON 仍可解析；BIN 逐字节一致 | ✅ JSON 文本；❌ 几何/内嵌贴图 |

### 3.3 对照 DESIGN 3.6 三维五泄漏点

| DESIGN 泄漏点（DESIGN:281-285） | 现状 | 证据 |
|---|---|---|
| 1. 顶点里的工程原点（大地坐标） | 🟡 只对 OBJ/PLY 做“减包围盒中心”；STL/GLB 不做；没有旋转/量化/加噪 | mesh_parser.py:789-846、943-1018、1054、1137 |
| 2. 纹理贴图里的文字（招牌/门牌/车牌/横幅） | ❌ 完全未做：GLB BIN 内 images 不解析；OBJ 不读 .mtl/贴图 | 无代码；§1.2 #8/#9 |
| 3. 内嵌照片和 GPS（摄影测量原始照片 EXIF） | ❌ 完全未做 | 无代码 |
| 4. 建模软件名/项目名/坐标系名/拍摄日期 | 🟡 仅“文本块 + geo-cn 规则”能命中坐标系名；项目名/软件名/日期无规则；GLB asset 字段可被规则命中 | §4.12-H1/H4 |
| 5. 点云精度（抽稀+加噪） | ❌ 无 LAS/LAZ 支持 | 无代码 |
| 导出时整包重写、丢弃未知 chunk | 🟡 OBJ/PLY 重写；STL/GLB 字节补丁；OSGB 无 | mesh_parser.py:743-1137 |

### 3.4 对照 DESIGN M3 里程碑（DESIGN:612-622）

| M3 交付项（DESIGN:619） | 判定 | 说明 |
|---|---|---|
| TIF 正射影像 | 🟡 | 基础剥离可用；大图/16bit/中性化未完成（§1.1） |
| 三维 | 🟡/❌ | 4 种桌面格式有限处理；OSGB/3D Tiles/LAS 未支持 |
| 坐标中性化 | ❌ | 全仓库无实现 |
| 验收：导出 TIF 用 gdalinfo 查不到任何地理参考 | ✅（标签层面达成，小样张） | 输出 tag 列表已无任何 Geo 标签（§4.4）；本机无 GDAL，未直接跑 gdalinfo，结论由 PIL tag_v2 实测推断；真实大图当前打不开，无法验证 |

---

## 4 实测记录

> 环境：Windows 10/11 + Python 3.12.9；Pillow 12.2.0；numpy 2.4.6；RapidOCR(PP-OCRv6 small) 就绪；PyMuPDF 1.27.2。机器空闲内存约 1.7GB（测试时）。
> 所有脚本/产物在 `%TEMP%\geo_research\`；仓库内仅 e2e 按设计写 `out/_test_*`（gitignore）。
> 命令统一带 UTF-8（中文 Windows 控制台下 e2e 的 `▸` 等字符会 GBK 报错，Lead 已提示；建议 `$env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py`）。

### 4.1 仓库自检 e2e（实测）

```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\app\server
$env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py
```

输出摘要（本次实测，2026-10-19 23:20）：

| 文件 | 格式 | 识别 | 换名导出 | 涂黑导出 |
|---|---|---|---|---|
| 住院病历.docx | docx | 9 处 | 残留 0（34620B） | 残留 0（34512B） |
| 名片.png | image | 6 处 | 残留 0（44097B） | 残留 0（17104B） |
| 委托代理合同.pdf | pdf·2 页 | 15 处 | 残留 0（7157B） | 残留 0（2768B） |
| 对账单.xlsx | xlsx | 13 处 | 残留 0（5369B） | 残留 0（5226B） |
| 正射影像.tif | tiff | **3 处**（图幅号/控制点号/坐标系名称） | 残留 0（48832B） | 残留 0（43974B） |
| 汇总 | 5/5 解析成功、全部通过；总耗时 17.61s；tiff 解析 1417ms、检测 3ms | | | |

tiff 告警原文：`检测到地理参考（ModelPixelScaleTag、ModelTiepointTag、GeoKeyDirectoryTag、GDAL_METADATA），导出时将剥离`。

### 4.2 与 Lead 基线对照（差异说明）

| 指标 | Lead 基线（2026-10-19，HEAD fb90233） | 本次实测 | 差异解释 |
|---|---|---|---|
| e2e 通过 | 5/5，残留 0 | 5/5，残留 0 | 一致 |
| 正射影像.tif 解析 | 1988ms | 1417ms | OCR 模型冷/热状态、磁盘缓存与系统调度抖动；两次都含 OCR，差值 571ms（-29%），不影响结论 |
| 正射影像.tif 检测 | 1ms | 3ms | 同上（毫秒级抖动） |
| 识别项 | 图幅号/控制点号/坐标系名称 各 1 | 相同 | 一致 |
| 剥离 warning | 4 类标签并在导出剥离 | 相同 | 一致 |

> 两次实测互证：**小样张的 TIF 剥离链路是稳定可用的**；但样张是合成 800×600 图，不足以代表真实正射（§4.3 说明）。

### 4.3 样例 TIF 标签实测（PIL `tag_v2`）

```powershell
python $env:TEMP\geo_research\tags.py
```

| 项目 | 值 |
|---|---|
| 文件 | `samples/正射影像.tif`（1,440,312B，由 `app/tests/make_samples.py:166-186` 生成） |
| 图像 | 800×600，RGB，n_frames=1，无压缩（raw） |
| tag_v2 全部 tag | 256,257,258,259,262,273,277,278,279,284, **33550,33922,34735,42112** |
| 33550 ModelPixelScaleTag | `(0.1, 0.1, 0.0)` |
| 33922 ModelTiepointTag | `(0.0, 0.0, 0.0, 500000.0, 3450000.0, 0.0)` |
| 34264 ModelTransformationTag | 不存在 |
| 34735 GeoKeyDirectoryTag | `(1,1,0,3, 1024,0,1,2, 1025,0,1,1, 2048,0,1,4490)` → GTModelType=2（地理）、GTRasterType=1（PixelIsArea）、GeographicType=4490（CGCS2000） |
| 34736/34737 | 不存在 |
| 42112 GDAL_METADATA | `AREA_OR_POINT=Area` |
| 42113 GDAL_NODATA | 不存在 |
| 同名 sidecar | `.tfw/.aux.xml/.prj/.ovr/.kml/.rpc` 均不存在 |
| `Image.MAX_IMAGE_PIXELS` | 89,478,485 |

### 4.4 导出前后标签实测

| 项目 | 输入 | 换名导出 | 涂黑导出 |
|---|---|---|---|
| 输出文件 | — | `out/_test_正射影像_ai.tif` 48832B | `out/_test_正射影像_human.tif` 43974B |
| tag 列表 | 含 33550/33922/34735/42112 | 只剩 256,257,258,259,262,273,277,278,279,284 | 同左 |
| 8 类 Geo 中实际存在的标签 | 4 类 | **0** | **0** |
| 压缩 | raw | tiff_lzw | tiff_lzw |
| `_verify_geo_stripped` | — | `[]`（无残留） | `[]` |
| `pipeline.verify` | — | `verified=True, residual=0` | 同左 |

### 4.5 元数据注入实验（EXIF/XMP/描述/设备/ICC）

输入：样张副本 `%TEMP%\geo_research\a_meta_in.tif`，注入 tag：
`270=项目：XX新区倾斜摄影 无人机航测`、`271=DJI`、`272=Mavic 3E`、`305=ContextCapture 10.20`、`306=2026:10:19 12:00:00`、`315=张三`、`33432=Copyright XX测绘院`、`700=<x:xmpmeta>…`、`34675=ICC_PROFILE…`，另有 3 类 Geo 标签。

| 标签 | 输入 | 输出 `a_meta_out.tif` | 判定 |
|---|---|---|---|
| 270 ImageDescription | 有 | 无 | ✅ 已剥 |
| 271 Make / 272 Model / 305 Software | 有 | 无 | ✅ 已剥 |
| 306 DateTime / 315 Artist / 33432 Copyright | 有 | 无 | ✅ 已剥 |
| 700 XMP | 有 | 无 | ✅ 已剥 |
| **34675 ICC profile** | 有（伪造 ICC 字节） | **仍保留**（tag 34675 存在；另做一组专门比对：输入 1036B ICC → 输出 1036B，字节完全相同） | ❌ 未剥（ICC 内容为色彩配置，风险低但属元数据残留） |
| 33550/33922/34735 Geo | 有 | 无 | ✅ 已剥 |

> 代码原因：`_safe_tiffinfo` 不写 34675，但 `source.convert("RGB")`（tiff_parser.py:178）保留了 `im.info["icc_profile"]`，Pillow TIFF 保存时会回退读取（本机 Pillow 12.2.0）。图片管线的 PNG 导出同样保留 ICC（实测 484B），JPEG 导出不保留。修复成本低：导出前 `new_im.info.pop("icc_profile", None)`。

### 4.6 sidecar 清理范围实测

预置输出同名文件后导出：

| sidecar | 导出后 | 说明 |
|---|---|---|
| `b_side_out.tfw` | 已删除 | tiff_parser.py:77 |
| `b_side_out.prj` | 已删除 | :79 |
| `b_side_out.aux.xml` | 已删除 | :78 |
| `b_side_out.tif.aux.xml` | 已删除 | :80 |
| **`b_side_out.ovr`** | **保留** | ❌ 金字塔未处理 |
| **`b_side_out.kml`** | **保留** | ❌ DESIGN:272 要求处理 |
| **`b_side_out.rpc`** | **保留** | ❌ RPC 是强定位信息（DESIGN:268 点名 RPC） |

### 4.7 多帧 TIFF 实测

| 项目 | 输入（2 帧） | 输出（2 帧） |
|---|---|---|
| 帧 0 Geo 标签 | 33550/33922/34735 | 无 |
| 帧 1 Geo 标签 | 33550/33922/34735 | 无 |
| `doc.meta["frames"]` | 2 | — |
| `_verify_geo_stripped(out)` | — | `[]`（**仅检查当前帧/frame 0**；输出两帧都已剥离，但代码层面非首帧不在复检范围） |
| warnings | — | 无 |

### 4.8 位深/波段/压缩实测

| 用例 | 输入 | 输出 | 结论 |
|---|---|---|---|
| 16bit 灰度 | `I;16`，值域 0..65535（65536 级） | `RGB` 8bit，唯一灰度值 **256** 个 | ❌ 值域压缩 256×，辐射信息丢失 |
| 4 波段 RGBA（模拟 RGBN/带 alpha 正射） | `RGBA` 64×64 | `RGB` | ❌ 第 4 波段（NIR/alpha）整体丢失 |
| CMYK | `CMYK` 64×64 | `RGB` | 🟡 转 RGB（本用例颜色转换无额外差异，但色彩空间已变） |
| JPEG 压缩 TIFF | `compression=jpeg quality=90` 122887B | LZW 141122B，像素与解码输入一致 | 🟡 无额外像素损失，但重压缩格式/体积变化；输出失去 JPEG 压缩优势 |
| 原始压缩 | raw 1440312B | LZW 43974B（合成图） | 无压缩 TIF 会变小，但真实影像 LZW 常比 JPEG 大 |

### 4.9 大图与 PIL 上限实测

| 用例（伪造 TIF 头） | 结果 |
|---|---|
| 10000×10000 = 100,000,000 px | `Image.open` 成功；**DecompressionBombWarning**：“Image size (100000000 pixels) exceeds limit of 89478485 pixels…”；随后 load 因伪造数据截断报 OSError（真实文件会正常解码） |
| 20000×20000 = 400,000,000 px | **DecompressionBombError**：“Image size (400000000 pixels) exceeds limit of 178956970 pixels…” |
| 用 `TiffParser.parse` 打开 400Mpx 伪造文件 | 抛 `ParseError: 无法打开 TIFF：Image size (400000000 pixels) exceeds limit of 178956970 pixels…` |

阈值推导：`Image.MAX_IMAGE_PIXELS = 89,478,485`；`> 2× = 178,956,970` 直接抛错（Pillow 行为）。**真实正射影像常见 2–6 亿像素（如 1km²@5cm ≈ 20000×20000=4 亿），当前版本会直接拒绝处理，且错误信息是英文 PIL 文案。**

### 4.10 OCR 缩放对图面小文字的损失实测

固定上限：`image_parser.OCR_MAX_EDGE=2400`（image_parser.py:28）× `ocr.MAX_OCR_SIDE=1600`（ocr.py:37），大图有效缩放 = 0.4 × 0.667 = **0.267**。

| 画布 | 文字字号 | 有效字号 | 识别结果 |
|---|---|---|---|
| 6000×4000（模拟大正射） | 48 / 32 / 24 / 16 px | 12.8 / 8.5 / 6.4 / 4.3 px | **只识别到 48px 那一行**（`[48px] 上海明理律师事务所`）；32/24/16px 全丢 |
| 1600×1200（对照，无缩放） | 48 / 32 / 24 / 16 px | 原字号 | 4 行全部识别 |

对真实正射的推算：20000px 宽影像 → OCR 输入等效 1600/20000 = **0.08 缩放**；0.05m GSD 下 1m 宽的招牌约 20px → 等效 1.6px，OCR 不可能识别。**“正射影像中的文字/招牌”目前基本只能靠人工框选，自动 OCR 仅对叠加大字（图廓注记/标题）有效。**

### 4.11 大文件吞吐与内存实测（TiffParser.parse+export，ocr=None）

| 尺寸 | Mpx | 输入/输出 | parse | export | 峰值内存（含约 29MB 基线） | 每像素峰值 |
|---|---|---|---|---|---|---|
| 3000×2000 | 6.0 | 17.2MB / 0.2MB | 21ms | 96ms | **77MB** | ≈8.0 B/px |
| 6000×4000 | 24.0 | 68.7MB / 0.9MB | 96ms | 327ms | **215MB** | ≈7.7 B/px |

推算：`20000×15000`（300Mpx）单帧 RGB 缓冲 858MB，按 7.7B/px 峰值约 **2.3GB**；而该尺寸已超 PIL 上限，实际在 `Image.open` 阶段就会被拒。内存来自三处叠加：`source.load()` 解码缓冲 + `convert("RGB")` 新图 + 原始图对象；多帧再乘帧数。

### 4.12 三维实测（TEMP 合成文件）

| 用例 | 结果 |
|---|---|
| H1 OBJ（3 顶点，大地坐标 + 注释 + mtllib）：走完整 detect→decide→export→verify | parse `coordinate_offset=True`；detect 命中 `CRS=CGCS2000`；导出注释变 `████████`、顶点变 `-8.272 -8.1055 -0.75`；warnings“顶点已减去包围盒中心重定位”；**verify `verified=True, residual=0, checked=1`**；项目名/瓦片名/mtllib 原样 |
| H2 PLY(ascii)：comment + 大地坐标 | comment 中 `CGCS2000` 被黑（`XX新区` 无规则不动）；顶点减中心；warnings 2 条 |
| H3 二进制 STL：80B 头含项目名 + 1 三角形 | 头 80B 全 0；`84B 之后几何逐字节一致`；warning“STL 暂不支持几何重定位” |
| H4 GLB：JSON 内 generator/copyright/name + BIN 内 9 个 float 顶点 | JSON chunk 长度不变、敏感串被等长空格覆盖、JSON 仍可解析；**BIN chunk 逐字节一致（几何未动）**；warning“GLB 暂不支持几何重定位” |
| 附注 | 上述 detect 需 `registry.load_plugins()`（服务由 app.py:60 加载；裸脚本不加载会导致规则不生效——本次实测先踩到、后纠正） |

### 4.13 人工框选能力核实（代码级，未跑 UI）

`POST /api/jobs/{id}/mark` → `jobs.py:285-320`：前端传归一化 `rect`，raster 用页面宽高乘回像素坐标，`Span(rect=..., page=...)`；`image_parser._apply_decisions`（267-314）对 rect span 调 `_paint_box`（218-243）涂黑/替换。→ **TIFF 上人工框选敏感地物→涂黑是通的**，这是当前唯一覆盖“车辆/军事设施/条码”等无 OCR 目标的手段。

---

## 5 差距与后续方案

### 5.1 无人机倾斜摄影典型产物 → 泄漏面 → 现状 → 方案

| 产物 | 典型形态 | 主要泄漏面 | 本版本现状 | 建议方案（阶段） |
|---|---|---|---|---|
| **OSGB 分块模型** | `Data/Tile_*/Tile_*.osgb` 多级 LOD + `metadata.xml`（`SRS`/`SRSOrigin`；ENU/EPSG/LOCAL/PRJ 串模式） | `metadata.xml` 的 SRSOrigin（工程原点=真实坐标）、瓦片目录名里的坐标、顶点绝对坐标、内嵌 JPEG 贴图里的招牌文字 | ❌ 完全不支持 | P2-1 纹理抽取/回写 v1；P2-2 节点变换/几何中性化 v2；**若本版本不做，必须在 UI 明确“OSGB 不支持”** |
| **3D Tiles** | `tileset.json` + `*.b3dm`（1.0）或直接 glTF（1.1）+ 贴图；tile transform（`transform`/`region`/`box`） | tileset.json 的 transform/region、b3dm 内 GLB 的 JSON 文本、贴图文字、batch table 属性 | ❌ | P1-3：复用 GLB 逻辑，解析 b3dm 头/feature table/batch table；JSON 文本清理 + 贴图抽取；transform 中性化写回 |
| **OBJ + MTL + 贴图** | `.obj/.mtl/xxx.jpg` 目录 | 贴图文字、MTL 材质名、OBJ 注释/mtllib/对象名、顶点工程坐标 | 🟡 仅 OBJ 文本+顶点平移；贴图/MTL 未处理 | P1-1：读 `mtllib/map_Kd`→贴图走 ImageParser 脱敏→重写 MTL/贴图→整包输出目录 |
| **LAS/LAZ 点云** | `*.las/*.laz`，含 CRS WKT VLR、项目名 VLR、扫描仪信息 | 点坐标（真实 XYZ）、CRS、VLR 文本、精度（轮廓反推） | ❌ | P1-4：laspy+lazrs；随机平移+旋转+量化、抽稀+加噪、清 VLR/CRS、重算 header bbox |
| **空三/控制点/工程文件** | ContextCapture/大疆智图工程 XML、控制点 txt/dat、KML/KMZ 范围线 | 控制点真实坐标、工程名、相机参数、pos 文件 | ❌（`.xml/.kml/.kmz` 无解析器；`.json` 可由 `text_parser` 走文本管线，text_parser.py:125；text 规则覆盖有限） | P1-5：XML/JSON/KML 文本解析器（走 text 管线）+ geo 规则；KML 坐标串距离脱敏 |
| **原始照片** | JPG/DNG 批量，EXIF GPS/相机/时间 | EXIF GPS（精确定位）、拍摄时间、设备序列号 | 🟡 JPG 走 image 管线**已验证 EXIF/GPS 全剥**（重编码丢弃）；DNG/RAW 不支持，批量目录不支持 | 部分已具备；P1-5 加目录批处理与 RAW 提示 |
| **建模报告 PDF** | 成果报告/精度报告 PDF | 项目名、坐标、控制点号（文本层或扫描件） | 🟡/✅ PDF 管线已支持（文本层+OCR），geo-cn 规则可命中 | 复用现有 PDF 管线，补 geo 规则验收样例 |

### 5.2 正射 TIF 技术路线（两条路，建议 B）

**共同目标**：在“不联网、Windows 可离线打包、CONTRACT 当前禁 GDAL/trimesh”的约束下，补齐“可交付真实正射影像”的闭环。

**路线 A（纯 Pillow/numpy 增强，不改契约）**
- 大文件：受 PIL 只能整图解码限制，无法真正流式；只能“文件大小/像素数预检 + 明确拒绝提示 + 极限内存优化（及时释放 source、逐帧写盘）”，上限建议 ≤89Mpx。
- 16bit/多波段：Pillow 可保存 `I;16` 与多帧，但“重绘涂黑 + 保留 16bit/多波段”需要按 band 处理（`split()`/`merge()`），numpy 可行；4 波段无通用“RGBN”语义，需用户选主 3 波段。
- 压缩/金字塔：Pillow TIFF 可传 `compression=tiff_deflate/tiff_adobe_deflate`；tiled/overview 需手写 TIFF 或后处理，成本高。
- 工作量：P0-2 约 **8–12 人日**，且大文件仍无解。

**路线 B（引入 rasterio 可选依赖；建议）**
- rasterio 1.4.3 有 Windows cp312 wheel（约 25.4MB，PyPI 官方二进制包自带 GDAL），可 windows 分块读写、保留 dtype/波段、`compress`/`tiled`/`build_overviews`、写 `.ovr`；许可证 BSD-3。
- 用法：`import rasterio` 成功→走分块管线；失败→回退 Pillow 并提示“大文件不支持/请安装可选依赖”。**不硬依赖 GDAL CLI**，PyInstaller 打包时按 `--collect-binaries rasterio` 收集 GDAL 动态库（体积 +50–80MB，需实测）。
- 坐标中性化算法（与路线无关，纯数学）：
  1. 读原 Affine（`ModelPixelScale+ModelTiepoint` 或 `ModelTransformation`），解析 GeoKeyDirectory 得 CRS；
  2. 生成密钥随机量 `θ∈[-3°,+3°]`、`(dx,dy)` 取 10m/100m 栅格整数倍、降采样比例 `k`；
  3. 对像素做旋转+缩放重采样（PIL `rotate`/rasterio `reproject`/warp），写回新的本地任意直角系标签（或自定义本地 CRS 的 WKT，不声明任何真实 EPSG）；
  4. 参数 `{angle, dx, dy, scale, src_crs, seed}` 用 AES-GCM 入 `data/vault/<job>.bin` 的 `geo` 段（扩展现有 vault payload），供“可还原导出”解密；
  5. 导出后复检：新文件必须只含“中性化后的本地坐标”，且不含原始 SRSOrigin/CRS 文本；
  6. 可选“彻底去地理参考”模式（不写任何 Geo 标签），与“中性化”模式二选一。
- 工作量：P0-3 约 **3–4 人日**（算法 2d + vault/导出 1d + e2e 1d）。

**公共补齐项（两条路线都要做）**
- sidecar 全谱：`.ovr/.kml/.rpc/.rpb/.imd/*.tif.xml` 删除；`.ovr` 若保留则在导出时重建（而非复用旧金字塔）；KML 若随附需同步变换或删除。
- ICC/XMP 清理：导出前清 `im.info["icc_profile"]`；`_safe_tiffinfo` 已覆盖其余。
- 复检强化：`pipeline.verify` 增加 TIF 专项——重开输出**所有帧**、检查 8 类 Geo + EXIF/ICC/IPTC/描述、检查 `doc.meta` 中不含设备字段；同时修掉 pipeline.py:508 `GEO_TAGS` 未使用的死代码。
- “仅清理”导出模式：允许实体为空时导出（Geo/元数据清理仍执行），修 `jobs.py:436-437` 的阻断。
- 测试：新增 5 个 GeoTIFF 样例（16bit 1 波段、RGBA 4 波段、>100Mpx 小文件模拟、带 6 种 sidecar、2 帧多帧），e2e 断言“gdalinfo/rasterio 无 CRS + 无 Geo tag + ICC 清空 + 多帧全检”。

### 5.3 三维技术路线

| 阶段 | 目标 | 技术要点 | 依赖 |
|---|---|---|---|
| P1-1 OBJ+MTL+贴图 | 倾斜摄影交付的 OBJ 目录包可脱敏 | 解析 `mtllib`、`usemtl`、`map_Kd/map_Ka/…`；贴图逐张走 `ImageParser`（OCR/涂黑）后重写到 `<out>/textures/`；OBJ/MTL 文本同步覆盖；整包目录输出 | 现有 image 管线 |
| P1-2 GLB/glTF 内嵌图 | GLB 内嵌照片/贴图可脱敏 | 解析 JSON `images`→`bufferViews`；从 BIN 抽 JPEG/PNG 字节→PIL 脱敏→若长度变化需重算 bufferView/accessor offsets 与 BIN 对齐；几何数据原样；`.gltf+.bin+textures` 同逻辑 | PIL，自研 GLB 重打包 |
| P1-3 3D Tiles | 3D Tiles 瓦片集可脱敏 | 解析 `tileset.json`（递归 `children`/`content.uri`/`transform`/`region`/`box`）；对 `.b3dm`（magic `b3dm` + feature/batch table + GLB）复用 P1-2；JSON 文本按现有等长/重写策略；`transform` 中性化参数入 vault；外部 tileset 递归 + 目录整包输出 | 自研 b3dm 解析 |
| P1-4 LAS/LAZ | 点云可脱敏 | laspy（BSD-2）+ lazrs（Apache-2.0，win_amd64 wheel）；X/Y/Z 随机平移+旋转+量化（如 0.5m 栅格）+抽稀（如 50%）+高斯噪声；清 CRS WKT VLR(34735/2112)、项目/扫描仪 VLR；重算 header min/max；可选导出 LAS 或 LAZ | numpy、laspy、lazrs |
| P2-1 OSGB 纹理 v1 | OSGB 内嵌贴图文字可处理 | 自研 OSG 二进制容器扫描：定位内嵌 JPEG/PNG 字节（SOI/EOI、PNG 签名），抽取→图像脱敏→**等长/重排写回**（若长度变化需改块长度并对齐）；`metadata.xml` 文本清理（SRS/SRSOrigin） | 自研解析（参考 OSG 格式，不复制 LGPL 代码） |
| P2-2 OSGB 几何 v2 | OSGB 几何坐标中性化 | 解析 PagedLOD/MatrixTransform/几何顶点数组；对绝对坐标做平移/旋转/量化；层级越多风险越大，建议只对“根原点/顶层变换”做中性化并在文档声明边界 | 自研解析，工作量最大 |
| P2-3 敏感地物自动识别 | 车辆/招牌/二维码 | cv2 `QRCodeDetector`（依赖内已有 opencv）；车辆/招牌需离线检测模型（ONNX），注意模型许可证（YOLO 系 AGPL，商用需换 Apache/MIT 模型）；**默认仍以人工框选兜底** | opencv + 模型许可评估 |

### 5.4 依赖选择（离线、Windows、许可证）

| 依赖 | 用途 | 许可证 | Windows 离线 wheel | 体积 | 建议 |
|---|---|---|---|---|---|
| **rasterio 1.4.3** | 大文件分块、dtype/波段、压缩、overview、CRS/tag 读写 | BSD-3（Mapbox）<sup>[1]</sup> | PyPI 有 `rasterio-1.4.3-cp312-cp312-win_amd64.whl`（≈25.4MB，自带 GDAL）<sup>[2]</sup> | 安装后约 +50–80MB（估算） | **P0 引入（可选依赖）**；需同步修订 CONTRACT §0（当前禁 GDAL） |
| **pyproj 3.7.2** | 坐标系识别/转换、CGCS2000 换带、局部 ENU | MIT<sup>[3]</sup> | PyPI 有 win_amd64 wheel（≈6MB）<sup>[4]</sup> | 约 +10–20MB（估算） | P0 可选；不联网可用（PROJ 数据随 wheel） |
| **GDAL 本体（CLI/官方包）** | 验收入口 `gdalinfo` | MIT 风格<sup>[5]</sup> | PyPI 官方 Windows 二进制包情况本次未核实，选型时需再确认；建议不直接依赖、验收工具另装 | 大 | 只作**验收工具**，不作运行时依赖 |
| **laspy** | LAS/LAZ 读写 | BSD-2-Clause<sup>[6]</sup> | 纯 Python，仅依赖 numpy；PyPI 可用 | 小 | P1-4 引入 |
| **lazrs 0.8.0** | LAZ 编解码后端（Rust） | Apache-2.0（移植自 LGPL LASzip）<sup>[7]</sup> | PyPI 有 `lazrs-0.8.0-cp312-cp312-win_amd64.whl`（≈0.42MB）<sup>[8]</sup> | 小 | P1-4 引入；LGPL 血缘需法务确认 |
| Pillow / numpy / opencv / rapidocr | 图像、像素、OCR、编解码 | 现有依赖 | 现有 requirements 已满足 | — | 复用 |
| trimesh | 三维网格库 | — | **CONTRACT §0 明确不可用** | — | 不引入；三维几何自研 |

### 5.5 不联网打包

- 所有 wheel 在本机/内网 PyPI 镜像预下载：`pip download rasterio pyproj laspy lazrs --only-binary=:all: --platform win_amd64 --python-version 312 -d vendor/wheels`（打包机执行，产品运行时**不联网**）。
- PyInstaller 需把 rasterio 的 GDAL 数据/动态库纳入：`--collect-binaries rasterio --collect-data rasterio`（需实测；GDAL 插件/证书按需裁剪）。
- 体积影响：桌面安装包当前约 315MB（仓库构建记录 266→315MB；README:353 提到 PyInstaller 体积 300MB+），引入 rasterio+pyproj 预计再 +60–100MB；需在 P0 验收时给出实测值。
- 网络红线：产品运行代码不出现外呼；离线 wheel 只在构建/安装阶段使用（符合 CONTRACT §0 与 README 安全边界）。

---

## 6 分期与工作量

> 估算口径：1 名熟悉 Python/PIL/PyPI 打包的开发，含自测与文档；不含 UI 大改（现有框选/确认 UI 可复用）。人日为区间。

### P0 · 正射 TIF 可交付闭环（建议优先）

| 编号 | 任务 | 交付 | 人日 | 依赖 |
|---|---|---|---|---|
| P0-1 | sidecar 全谱 + ICC/XMP 清理 + 复检强化（全帧、非 Geo 元数据）+ 修 `GEO_TAGS` 死代码 | 安全边界补丁 | 1.5–2 | — |
| P0-2 | 大文件/16bit/多波段/压缩/overview：rasterio 可选依赖 + Pillow 降级 + 明确拒绝提示 | 真实正射可处理 | 4–6 | D4 决策（契约修订） |
| P0-3 | 坐标中性化 v1：随机平移+旋转+取整/降采样+本地 CRS/无 CRS 二选一 + 参数 AES-GCM 入 vault + 可还原导出 | M3“坐标中性化” | 3–4 | P0-2 |
| P0-4 | 工程边界：允许“仅清理”导出（无实体也可出成品）+ UI 文案（“已剥离坐标/未中性化”如实提示） | 可用性修复 | 1–1.5 | — |
| P0-5 | 测试与验收脚本：5 个 GeoTIFF 样例 + e2e 扩展 + gdalinfo/exiftool/rasterio 检查清单 | 回归保障 | 1.5–2 | P0-1..4 |
| **合计** | | | **11–15.5 人日** | |

### P1 · 三维基础补齐（倾斜摄影中间格式）

| 编号 | 任务 | 交付 | 人日 | 依赖 |
|---|---|---|---|---|
| P1-1 | OBJ + MTL + 外部贴图：抽取→图像脱敏→回写→整包目录 | OBJ 倾斜成果可脱敏 | 3–4 | P0 图像管线 |
| P1-2 | GLB/glTF BIN 内嵌 image 抽取→脱敏→重打包（offset 重算） | GLB/glTF 贴图可脱敏 | 4–6 | — |
| P1-3 | 3D Tiles（tileset.json + b3dm + GLB 内容 + transform） | 3D Tiles 可脱敏 | 5–7 | P1-2 |
| P1-4 | LAS/LAZ：坐标中性化+抽稀+加噪+VLR/CRS 清理+header 重算 | 点云可脱敏 | 4–5 | — |
| P1-5 | 空三/控制点/工程文件（XML/JSON/KML/KMZ）+ 原始照片批量 | 工程文件与照片可脱敏 | 2–3 | — |
| **合计** | | | **18–25 人日** | |

### P2 · OSGB 与深度能力

| 编号 | 任务 | 交付 | 人日 | 依赖 |
|---|---|---|---|---|
| P2-1 | OSGB 纹理抽取/回写 v1 + metadata.xml 文本清理 | OSGB 贴图可处理 | 5–8 | — |
| P2-2 | OSGB 节点变换/几何坐标中性化 v2 | OSGB 几何可中性化 | 10–15 | P2-1 |
| P2-3 | 敏感地物自动识别（招牌/车辆/二维码；离线模型许可评估） | 自动检测（可选） | 8–15 | 模型许可决策 |
| P2-4 | 性能与打包（多线程/内存映射/安装包体积/大目录增量） | 工程化 | 3–5 | P0/P1 |
| **合计** | | | **26–43 人日**（含可选项） | |

### 推荐排期（三档）

| 档位 | 范围 | 人日 | 结果 |
|---|---|---|---|
| MVP（最小可交付） | P0 全部 + P1-1 + P1-2 + UI 明示“OSGB/3D Tiles/LAS 未支持” | **18–26** | 真实正射可脱敏（含中性化）；OBJ+贴图、GLB 贴图可脱敏 |
| 推荐版本 | MVP + P1-3 + P1-4 + P1-5 | **29–41** | 3D Tiles/LAS 也覆盖，OSGB 仍明确不支持 |
| 完整倾斜摄影 | 推荐版本 + P2-1 + P2-2 | **44–64** | OSGB 纹理与几何都能处理（P2-2 风险最高） |

---

## 7 验收标准（可执行）

| # | 对象 | 验收项 | 方法/命令 | 通过阈值 |
|---|---|---|---|---|
| A1 | 正射 TIF | 无地理参考（或仅中性化后本地坐标） | `gdalinfo out.tif`（或 rasterio `src.crs/src.transform/tags()`） | 无 8 类 Geo tag；若开中性化，坐标=本地任意系且与真实坐标无固定映射 |
| A2 | 正射 TIF | 无 EXIF/GPS/ICC/描述/设备字段 | `exiftool -a -G1 out.tif`；rasterio tags 全查 | GPS/设备/项目字段 0；ICC 0；XMP 0 |
| A3 | 正射 TIF | sidecar 清理 | 输出目录 `Get-ChildItem` | 无 `.tfw/.prj/.aux.xml/.ovr/.kml/.rpc/.rpb/.imd` 残留（或按策略同步变换） |
| A4 | 正射 TIF | 多帧全帧剥离 | 逐帧 `tag_v2` | 每一帧 Geo 标签数=0 |
| A5 | 正射 TIF | 大文件/位深/波段 | 16bit、4 波段、200Mpx 样本实测 | 按 P0-2 声明的支持矩阵全部通过；超限文件在 UI 明确拒绝而非崩溃 |
| A6 | 正射 TIF | 内存上限 | 处理前后 `PeakWorkingSet64` | 单文件峰值 ≤ 约定上限（建议 4GB；300Mpx 需分块路线） |
| A7 | 正射 TIF | 变换参数可还原 | vault 解密后按参数逆变换 | 逆变换误差 ≤1 像素；参数文件 AES-GCM、密钥本机 |
| A8 | 正射 TIF | 导出后复检不阻断正常交付 | `pipeline.verify` | 正常样例 verified=True；人为注入 Geo/EXIF 时 VerifyError |
| A9 | 三维 | 文本残留 0 | 对输出重新跑 detect | 原敏感值残留 0（含注释/JSON/header） |
| A10 | 三维 | 贴图文字残留 0 | 抽取贴图再跑 OCR/人工抽检 | 贴图中敏感文本 0（人工抽检≥20 张/批） |
| A11 | 三维 | EXIF/GPS 0 | `exiftool` 重扫输出贴图/内嵌图 | GPS/设备字段 0 |
| A12 | 三维 | 可打开性 | OSGB 查看器 / Cesium / CloudCompare / Blender 打开 | 原软件能正常打开、几何不破面、贴图正常 |
| A13 | 三维 | 坐标变换参数留存 | vault 检查 | 平移/旋转/量化/抽稀/噪声参数可查、可还原（如承诺） |
| A14 | 全链路 | e2e 回归 | `python -X utf8 ..\tests\e2e.py` | 新增 3D/大 TIF 样例后仍全绿；gdalinfo/exiftool 检查脚本返回 0 |
| A15 | 产品话术 | 不夸大 | README/UI 文案审查 | “只降密不匿名”“OSGB 支持状态”与实际一致（修 README:166/295 矛盾） |

---

## 8 风险与决策点

### 8.1 风险边界（必须写清、不可承诺“合规”）

1. **只能“降密”，不能“匿名”**（README:260、DESIGN:275）：坐标删了/移了，河流、道路、山脊、建筑轮廓仍在——用公开影像/地图做形状匹配仍可能反推位置。文案不写“已合规”。
2. **随机变换不等于安全**：参数存在 vault 就是“可还原”，参数泄漏=还原；参数丢了=数据不可用（除非明确不可还原模式）。
3. **人工框选是最终兜底**：无 OCR 的招牌/车辆/条码，自动识别不可靠（DESIGN:273 已承认），产品必须保留框选补漏并教育用户“必须人工复核”。
4. **OSGB 格式没有官方规范**：osgb 是 OpenSceneGraph 的二进制序列化格式，由 OSG 源码定义（OSGPL = LGPL-2.1 + wxWindows 例外）；自研解析只能“读格式/写格式”，**不得复制 LGPL 代码**，需 clean-room + 法务确认；不同建模软件（CC/大疆智图）的瓦片组织与 metadata.xml 也有差异。
5. **贴图重写风险**：JPEG 字节长度变化会破坏 OSGB/BIN 块偏移；必须“等长替换/重排块长度”并有失败阻断策略。
6. **旋转/降采样有损**：中性化需要重采样，会降低影像质量；是否可接受由用户决策。
7. **大文件/性能**：真实倾斜项目动辄几十~几百 GB；流式、并发、断点续做是工程风险，P0 只解决单文件正射。
8. **打包体积**：引入 rasterio/pyproj/GDAL 后桌面安装包可能 +60–100MB，需用户接受。
9. **许可证**：lazrs 血缘含 LASzip（LGPL）；GDAL/rasterio/pyproj/laspy 均为宽松许可；YOLO 系模型 AGPL，商用需替换。
10. **验证工具依赖**：CONTRACT 禁 GDAL，若验收要用 gdalinfo 需在“验收环境”另装，不进入产品运行时。

### 8.2 待用户审核决策点

| 编号 | 问题 | 选项 | 建议 | 影响 |
|---|---|---|---|---|
| D1 | **坐标中性化是否本版本必须？** | A 必须（P0-3）/ B 下一版本（先修剥离与可用性） | A（README 已对外承诺，且是 M3 交付项） | 影响 P0 人日 +3–4 |
| D2 | 中性化后是否需要**可还原**？ | A 可还原（参数入 vault）/ B 不可还原（一次性丢弃参数） | A（与现有“换名可还原”定位一致） | 决定是否扩 vault 结构 |
| D3 | 交付物是否**保留假地理参考**（GIS 可叠加）还是彻底去参考？ | A 本地任意 CRS（可 GIS 打开）/ B 纯图片（无任何 CRS） | A + 可选 B | 决定 P0-3 的重采样与标签写入量 |
| D4 | 是否允许**引入 rasterio（捆绑 GDAL）/pyproj** 并修订 CONTRACT §0？ | A 允许可选依赖 / B 不允许（纯 Pillow，接受大文件不支持） | A（否则真实正射做不了） | 决定 P0-2 路线与打包体积 |
| D5 | **OSGB 是否列入本版本？** | A 本版本（P2-1/P2-2，+15–23 人日）/ B 下版本（先 3D Tiles/OBJ+贴图）/ C 不做、只做明确不支持提示 | B 或 C（OSGB 解析风险最高，建议先用 3D Tiles/OBJ 覆盖） | 直接决定“倾斜摄影三维”能不能叫支持 |
| D6 | 大图处理上限与拒绝策略 | 如 1 亿/2 亿/分块无上限 | 分块路线下不设硬上限，设“单文件 ≤ X GB 或 ≤ Y 亿像素”提示 | 影响 P0-2 范围 |
| D7 | 16bit/多波段：保留原样还是统一 8bit RGB？ | A 保留（体积大、处理复杂）/ B 统一降 8bit（明确告知）/ C 用户可选 | C（默认保留，8bit 选项加速） | 影响 P0-2 与验收 A5 |
| D8 | 贴图脱敏失败时策略 | A 阻断导出 / B 告警放行后人工复核 | A（与“出口自检”定位一致） | 影响安全边界与可用性 |
| D9 | 敏感地物自动识别（车辆/招牌/条码）是否本版本？ | A 做（+8–15 人日，模型许可待评估）/ B 只保留人工框选 | B（DESIGN 本身要求人工框选；自动模型有许可与误报风险） | 影响范围与合规 |
| D10 | 三维/正射交付形式 | 单文件 / 整包目录 + zip / 保留原目录结构 | 整包目录（OBJ/OSGB 必须带贴图与 metadata.xml） | 影响管线与 UI |
| D11 | “只降密不匿名”的用户告知形式 | 启动弹窗/导出确认/水印 | 导出前二次确认（沿用现有安全边界话术） | 合规与体验 |

---

## 附录 A：实测命令与脚本位置

```powershell
# 1) 仓库自检（Lead 基线同款；中文控制台务必 UTF-8）
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\app\server
$env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py

# 2) 样例 TIF 标签检查（$TMP = %TEMP%\geo_research）
python -X utf8 $env:TEMP\geo_research\tags.py

# 3) 元数据注入 / sidecar / 多帧 / 位深实验
python -X utf8 $env:TEMP\geo_research\exp_metadata_sidecar_multiframe.py

# 4) PIL 像素上限 / OCR 缩放 / 大文件内存实验
python -X utf8 $env:TEMP\geo_research\exp_bomb_ocr_large.py
python -X utf8 $env:TEMP\geo_research\exp_memory.py

# 5) 图像 EXIF/ICC 与 TIF ICC 实验
python -X utf8 $env:TEMP\geo_research\exp_image_meta.py

# 6) 三维 OBJ/PLY/STL/GLB 实验
python -X utf8 $env:TEMP\geo_research\exp_mesh.py
```

临时产物：`%TEMP%\geo_research\{a_meta_*,b_side_*,c_multi_*,d1..d4_*,e_bomb_*,f_big_6000.*,g2_6000x4000*,h1..h4_*}`；仓库内新增仅 `out/_test_*`（gitignore 覆盖）。`git status` 中只有任务开始前就已存在的 3 个未跟踪图标文件（`微信图片_*.png`、`金粒图标.ico/.svg`），**没有任何源码/规则/样例改动**。

## 附录 B：代码位置索引

| 主题 | 位置 |
|---|---|
| TIFF 解析/导出 | `app/server/yinzhan/parsers/tiff_parser.py`（Geo 表 22-31；白名单 36/55-70；sidecar 73-89；复检 92-98；parse 119-156；export 158-214） |
| 图片/OCR/涂黑共用 | `app/server/yinzhan/parsers/image_parser.py`（OCR 上限 28；框/涂黑 218-314；编码 319-357） |
| OCR 服务（二次缩放） | `app/server/yinzhan/ocr.py:37, 237-286` |
| Geo 规则 | `app/server/yinzhan/rules/geo-cn.yaml:10-70` |
| 管线/复检 | `app/server/yinzhan/pipeline.py:503-552` |
| 任务/导出/映射 | `app/server/yinzhan/jobs.py:26-64,196,285-320,429-494`；`vault.py:52-92` |
| 三维 | `app/server/yinzhan/parsers/mesh_parser.py`（文档串 1-23；OBJ 186-240/590-648/782-852；PLY 245-422/651-677/855-1018；STL 427-438/680-704/1021-1056；GLB 443-539/707-740/1059-1137） |
| 契约 | `app/server/CONTRACT.md:8-10（可用/不可用库）、143-148（导出硬要求）、222-228（出口自检）` |
| 设计 | `DESIGN.md:242-249（各格式要求）、260-287（3.6 地理与三维）、612-622（M3 里程碑）` |
| README | `README.md:157-168（支持表）、275-285（自检）、289-299（已知限制）、260（降密边界）` |
| e2e/样例 | `app/tests/e2e.py:27-76`、`app/tests/make_samples.py:166-186` |

## 附录 C：参考资料（外部，2026-10-19 访问；仅作数据）

1. rasterio 许可证（BSD-3）：<https://github.com/rasterio/rasterio/blob/main/LICENSE.txt>
2. rasterio 1.4.3 PyPI 元数据与 `cp312-win_amd64` wheel：<https://pypi.org/pypi/rasterio/1.4.3/json>
3. pyproj 许可证（MIT）：<https://github.com/pyproj4/pyproj/blob/main/LICENSE>
4. pyproj 3.7.2 PyPI 元数据（含 win_amd64 wheel）：<https://pypi.org/pypi/pyproj/3.7.2/json>
5. GDAL 许可证（MIT 风格）：<https://github.com/OSGeo/gdal/blob/master/LICENSE.TXT>
6. laspy 许可证（BSD-2-Clause）/可选 laszip、lazrs、pyproj：<https://github.com/laspy/laspy/blob/master/pyproject.toml>
7. lazrs（Apache-2.0，移植自 LASzip）：<https://github.com/laz-rs/laz-rs>
8. lazrs 0.8.0 PyPI 元数据（含 win_amd64 wheel）：<https://pypi.org/pypi/lazrs/0.8.0/json>
9. OpenSceneGraph 许可证（OSGPL≈LGPL-2.1+wxWindows 例外）：<https://github.com/openscenegraph/OpenSceneGraph/blob/master/LICENSE.txt>
10. OSGB 瓦片组织与 metadata.xml（SRS/SRSOrigin、ENU/EPSG/LOCAL 模式，中科图新文档）：<http://doc.tuxingis.com/zhuanti/guide/1.1%E3%80%90osgb%E6%A0%BC%E5%BC%8F%E5%80%BE%E6%96%9C%E6%91%84%E5%BD%B1%E6%95%B0%E6%8D%AE%E3%80%91%E8%AF%B4%E6%98%8E.html>
11. 3D Tiles 规范（1.0 OGC 社区标准 / 1.1 仓库，b3dm 已弃用）：<https://github.com/CesiumGS/3d-tiles>、<http://docs.opengeospatial.org/cs/18-053r2/18-053r2.html>
12. glTF 2.0 规范（3D Tiles 1.1 内容基础）：<https://github.com/KhronosGroup/glTF>
13. ASPRS LAS 1.4 规范（R15 镜像 PDF）：<https://paulbourke.net/dataformats/laz/LAS_1_4_r15.pdf>；LAZ（LASzip）：<https://laszip.org/>；ASPRS LiDAR 分部：<https://www.asprs.org/divisions-committees/lidar-division/laser-las-file-format-exchange-activities>

---

*本稿为只读调研结论；所有“已完成/未完成”判断均以 HEAD `fb90233` 的代码与本机实测为准，未参考任何未合并分支或口头承诺。*
