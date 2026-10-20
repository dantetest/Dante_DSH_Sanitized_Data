# 三维 / 正射脱敏：当前架构适配性评估与「同项目扩展 vs 独立项目」立项建议

> 作者：arch-analyst（共享任务 task-3，Team Lead = lead）· 2026-10-20
> 性质：**只读分析**。未改任何源码/规则/测试；本文是唯一写入文件；实验均在 `%TEMP%\arch_check\`。
> 代码基线：git HEAD `9930db6` + 工作区未提交的「signs（门头/铭牌）」改动（`git status` 可见 `jobs.py +216 / visual_targets.py +438 / app.js +193 / api.py +11` 等）。
> 行号以本次读取时工作区为准；`pipeline.py` 在分析期间被并行任务追加 2 行（L523 后），引用处已按当前内容核对，仍有 ±2~5 行漂移可能。
> 证据标注：**[码]**=代码位置；**[测]**=本次实测（命令/探针见附录 B）；**[文]**=仓库文档原文。

---

## 0 结论摘要（直接回答用户）

**一句话：三维/正射都能在现有架构上继续做，但只建议正射继续走「同仓同应用」；三维应做成「同仓、独立域模块（共享内核 + 可选依赖 + 功能开关 + 独立发布包）」，OSGB 单独立项或本阶段不做。不建议为三维整体另开一个独立仓库/项目。**

三条分界线：

1. **正射 TIF 不是「新方向」，它已经是现有架构里的第二类栅格格式**，且 P0 真闭环已落地（rasterio 分块/位深/波段/坐标中性化/vault 参数/sidecar/复检，[码] `georef.py`、[码] `tiff_parser.py`、[文] `docs/impl/tiff.md`）。剩余 P1（多帧中性化、量化/降采样、验收自动化等）是**同文件原位增强**，工期 3–5 人日，不需要任何新抽象 → **继续 A**。
2. **三维现状是「文本/位图管线穿了一件 3D 外衣」**：`mesh_parser` 把 OBJ/PLY/STL/GLB 的文本行塞进 `Page(A4)` 的 `Block`（[码] `mesh_parser.py:48-49,590-648,707-740`），预览渲染成 `kind="flow"` 的文本页（[测] 附录 B-3），贴图/MTL/内嵌图/几何（GLB/STL）均未处理（[码] `mesh_parser.py:1-23,1059-1137`；[文] `docs/research/03` §1.2/§3.3）。要继续做真正的三维，必须新增 **Package/Asset（多文件整包）、Scene/瓦片树、PointCloud、结构性 Verify、UI scene 视图** 五类抽象；其中 OBJ+MTL+贴图、GLB 内嵌图、LAS/LAZ 可在同仓独立域模块内完成 → **B**；3D Tiles 属 B 域内较深子项；OSGB 与完整 3D 预览的**容器/规范/交互风险**明显超出当前单文件+页面模型 → **C（条件触发）或 D（本阶段不做）**。
3. **现在最该做的不是选边，而是把「能力边界」先说诚**：投放台已宣称支持「三维模型」（[码] `app/web/index.html:45`）、README 支持表也列了三维（[文] `README.md:168`），而实际能力只有 4 种中间格式的文本清理 + OBJ/PLY 顶点平移。这一口径矛盾在三维立项前必须收敛（做法见 §6 P0）。

**给用户的建议（可直接转述）**：正射继续在现有产品里收口；三维作为同一个代码仓的独立域模块立项（共享规则/检测/替换/映射库/审计/任务框架/图像扫描与 OCR 内核，独立入口、独立可选依赖、独立发布包），先做 OBJ+MTL 贴图与 GLB 内嵌图（6–10 人日），再做 3D Tiles/点云（10–16 人日）；OSGB 等高风险的倾斜摄影整包，只在出现测绘类客户合同/真实样例时单独立项，不在本阶段承诺。

---

## 1 评估口径

| 项 | 内容 |
|---|---|
| 目标问题 | 基于当前架构，三维/正射脱敏以后能不能做；还是需要单独开项目专做三维和正射 |
| 评估对象 | 引擎内核（UDM/管线/契约/解析器/检测/替换/verify/job/vault/audit）+ 前端对照台 + 打包发布 + 测试门禁 |
| 「可行」判据 | ① 有无落点（可原位扩展/加抽象/不适配）；② 复用度（复用现有内核的比例）；③ 验证能力（出口自检能否拦住）；④ 交付与维护代价 |
| 局限 | 没有真实倾斜摄影/点云样例；OSGB 无公开规范、本机无 LAS 库；未跑大目录吞吐与真实浏览器 3D 性能；三维 UI 未实测 |

---

## 2 当前架构：组件地图与一次 job 的调用链

### 2.1 组件地图

```
                          ┌──────────────── app/web（单页，2111 行 app.js）────────────────┐
                          │ 投放台 → 识别中 → 对照台（raster <img> / flow DOM 重排）→ 交付台  │
                          │ 框选补漏（rect 归一化）· 逐条确认 · 整图涂黑 · 导出双卡          │
                          └───────────────┬───────────────────────────────────────────────────┘
                                          │ HTTP 127.0.0.1（同源校验，api.py）
┌─────────────────────────────────────────▼───────────────────────────────────────────────────┐
│ api.py：/upload /jobs /preview /render /mark /entities /export /file /audit /packs /health   │
└───────────────┬───────────────────────────────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────┐   ┌──────────────────────────────────────────────────────────┐
│ jobs.py（JobManager）         │──▶│ registry.py（Parser/Detector 协议 + 插件自动导入）        │
│ 1 job = 1 文件 + 后台线程状态机│   │ parsers/：pdf docx xlsx image tiff text mesh              │
│ 进度/取消/淘汰/导出/预览/映射  │   │ detectors/：regex dict ner joined numeric table           │
└───────┬──────────────┬───────┘   │ replacers/：surrogate mask black amount_consistency       │
        │              │           └──────────────────────────────────────────────────────────┘
        │              │
┌───────▼──────────────▼──────────────────────────────────────────────────────────────────────┐
│ pipeline.py：detect → merge → decide(Session) → export(parser.export) → verify(重解析)     │
│ udm.py：Document / Page / Block / Span / Entity / Decision / VisualMark（格式归一化层）    │
└───────┬─────────────────────────────────────────────────────────────────────────────────────┘
        │
┌───────▼──────────────┐ ┌──────────────────────────┐ ┌──────────────────────────────────────┐
│ vault.py（AES-GCM）   │ │ audit.py（链式 HMAC 审计）│ │ mediascan.py（页面图像扫描器注册表）  │
│ 文字映射 + extra{geo} │ │ 只记类型/数量/哈希        │ │ barcodes / visual_targets / sign_*    │
└──────────────────────┘ └──────────────────────────┘ └──────────────────────────────────────┘
        │ 导出侧还有：georef.py（正射中性化数学/复检）、image_parser（OCR+涂黑可复用）
        └─ 打包：desktop/app.spec + installer.iss + build_win.ps1 + .github/workflows/build-macos.yml
```

### 2.2 一次 job 的完整调用链（[码] 行号为用户操作对应实现）

| # | 阶段 | 入口/实现 | 关键行为 |
|---|---|---|---|
| 1 | 投放 | `app.js:584-630` | `POST /api/upload`（原始字节 + `X-Filename`）→ `POST /api/jobs {upload_id,strictness,id_card_mode,time_blur}`；单文件、单请求（[码] `api.py:121-163`） |
| 2 | 建任务 | `jobs.py:168-187` | `registry.find_parser(path)` 按扩展名/嗅探选解析器，`fmt=parser.format_id`，后台线程 `_run` |
| 3 | parse | `jobs.py:218-227` → `parser.parse(path, ParseContext)` | 解析器把文件归一化为 `Document`；图像类会 OCR + `mediascan.analyze` 写 `doc.visual_marks`；三维只抽文本行成 Block |
| 4 | rules | `jobs.py:28-66,231` | `rules.compile_rules(packs, fmt)`，按 `Rule.scope` 字符串匹配 `doc.fmt` |
| 5 | detect | `pipeline.py:53-105` | 只遍历 `registry.iter_text_blocks(doc)`（text 且非空）；同值合并、重叠裁剪、置信度闸门 |
| 6 | 补漏启发式 | `pipeline.py:398-431`（表单人名）、`342-385`（并列人名）、`445-484`（签署区） | 依赖 `Block.bbox` 与页面坐标系（PDF pt / 图片 px） |
| 7 | decide | `pipeline.py:180-205` + `Session`；`jobs.py:256-265` 金额一致性 | 按规则替换策略生成 `Decision`；会话级保证同值同假值 |
| 8 | 扩散/视觉 | `pipeline.py:232-271`；`jobs.py:289-305` | 文本值全文扩散；`visual_marks` → rect 实体（黑） |
| 9 | ready | `jobs.py:333-335` | `stage="ready"`，UI 轮询 `GET /api/jobs/{id}` |
| 10 | 预览 | `pipeline.build_preview:827-879`；`api.py:184-187,383-391` | `kind` 只有 `raster`（`<img src=/render>`）与 `flow`（DOM 按 blocks+spans 重排） |
| 11 | 人工 | `jobs.py:401-451`（mark→rect→black）；`467-484`（accept/reject/replace） | 框选只产黑矩形；整图涂黑 = `rect=[0,0,1,1]` |
| 12 | export | `jobs.py:545-648` → `pipeline.export:489-498` → `parser.export(doc, ents, decisions, out_path)` | 单文件输出，文件名 = 源 stem + 「换名/已隐去」+ 交付编号 + **源扩展名**（`jobs.py:575-580`） |
| 13 | verify | `pipeline.verify:635-758` → 再次 `parser.parse(exported)` | 只查：文本残留（重跑同一批实体值）、meta 黑名单、`geo_tags`、视觉/码类 IoU≥0.3、内嵌媒体确认、PDF 隐藏数据；失败删除成品抛错（`jobs.py:640-645`） |
| 14 | 留痕 | `jobs.py:585-605`；`vault.py:52-66`；`audit.py` | audit 记类型/数量/哈希；AI 模式把文字映射与 `extra["geo"]` 写加密 vault |
| 15 | 交付 | `api.py:393-408`；`app.js:1937-1990` | 下载单文件；展示 verify 数字 |

### 2.3 各抽象对格式的隐含假设（这是三维/正射落点的根因）

| 抽象 | 定义位置 | 隐含假设 | 对三维/正射的意义 |
|---|---|---|---|
| `Document` | `udm.py:80-88` | **一个 Document = 一个物理文件**；`fmt` 单个字符串；`pages` 是页面列表；`extra/meta` 是解析器私有袋 | 正射：完全适配（1 文件 = 1 影像）；三维整包（OBJ+MTL+贴图、OSGB 目录、3D Tiles 目录）无表达位 |
| `Page` | `udm.py:71-76` | 2D 页面 `width/height` + blocks；没有 scene/camera/LOD/tile 概念 | 正射：1 页 1 帧适配；三维：只能伪造 A4 页面（`mesh_parser.py:48-49,590-591`） |
| `Block` | `udm.py:48-56` | `kind` 只有 `text/table/image`；主载荷是 `text`；`bbox` 是页面坐标 | 三维的贴图、材质、顶点/索引、瓦片、节点均无 kind；只能当 text 行 |
| `Span` | `udm.py:118-140` | 寻址只有两种：文本区间 `(block_id,start,end)` 或页面矩形 `(page,rect)`；`is_text` 判定 `block_id+start`（L138-140） | 无法表达顶点区间、纹理 UV 区域、tile id、点索引；三维涂黑只能退化为「贴图 2D 矩形」 |
| `Parser` | `registry.py:24-45` | `parse(path)->Document`、`export(out_path)->ExportResult`、`render(page)->PNG bytes`；`can_render` 二态 | 单文件进出；无「目录/整包」、无「多资产输出」、无「3D 渲染」 |
| `Detector` | `registry.py:48-58` | 输入 `Document`，实际只用文本块（`iter_text_blocks`，`registry.py:177-181`） | 对贴图/几何无输入通道（贴图必须先变成 image 页面才可复用） |
| `Replacer` | `udm.py:143-150`；`pipeline.py:208-223` | 策略基于**字符串值**（surrogate/mask/black/drop/keep） | 几何变换（平移/旋转/量化/加噪）不是「值替换」，需要新的 Plan 类型 |
| `Job` | `jobs.py:82-121` | 单 `path`、单 `parser`、单 `doc`、按 page 预览；导出单文件 | 整包任务（多文件/目录）无状态位；`_ext(job.path)` 决定成品扩展名 |
| `Verify` | `pipeline.py:635-758` | 只接受**单个成品路径**并重解析；只查文本/meta/视觉/隐藏数据 | 目录包无法验证；几何/贴图/点云精度**无验证语义**（[文] `docs/research/00` §3 短板 #1 已指出） |
| `PreviewDoc` | `CONTRACT.md:222-241`；`pipeline.py:832` | `kind` 只允许 `raster`/`flow`（契约 L240-241） | 3D 预览需要新 kind/新协议 |
| UI 对照台 | `app.js:1012-1022` | `renderRaster`（img）/`renderFlow`（DOM）二选一；框选坐标是 2D 归一化矩形（`app.js:1438-1500`） | 3D 场景的拾取/标注/相机无落点 |

---

## 3 能力复用判定：格式无关内核 vs 「文本+位图+单文件」硬编码

### 3.1 格式无关、可复用的能力（三维/正射都能吃）

| 能力 | 代码证据 | 复用方式 | 备注 |
|---|---|---|---|
| 规则引擎（YAML 包/编译/优先级/作用域） | [码] `rules.py`；`jobs._load_rules:28-66`；`Rule.scope`（`udm.py:195`） | 新增 scope 值即可（如 `mesh`，已有 13 条规则 [测]） | 规则按 `fmt` 字符串匹配；加新 fmt 零改动 |
| 检测器框架（regex/dict/ner/joined/table/numeric） | [码] `detectors/*.py`；`registry.register_detector`；`pipeline.detect:53-105` | 贴图 OCR 出来的文字可走同一批规则（把贴图当 image/tiff 文档跑） | **这是三维贴图脱敏最大的复用点** |
| 替换/脱敏策略与实体图 | [码] `replacers/surrogate.py`、`mask.py`、`black.py`、`amount_consistency.py`；`pipeline.Session:160-178` | 贴图内文字替换直接复用 | 几何变换需新增策略类型 |
| OCR 与图像扫描内核 | [码] `ocr.py`；`image_parser._ocr_blocks:106`、`_apply_decisions:267`、`_paint_box:218`、`_apply_orientation:328`、`_render_image_bytes:382`；`mediascan.py:65-82,188-244` | 抽出贴图/内嵌图 → 走同一套 OCR+人脸/证件/车牌/码/印章扫描+涂黑 | 扫描器接口是 `fn(image_rgb, ScanContext)`，**天然与载体无关** |
| 加密映射库 | [码] `vault.py:52-66,86-101`；`jobs.py:598-605` | 文字映射 + `extra` 袋已能存 `geo` 参数；三维变换参数可照抄 | human 模式不写映射 |
| 审计链 | [码] `audit.py`；`jobs.py:585-597` | 记录类型/数量/哈希/规则包，与格式无关 | 建议补「资产清单哈希」 |
| 任务框架/进度/取消/淘汰 | [码] `jobs.py:161-393,1064-1113` | 可复用，但需支持「一次 job 一个目录/多文件」 | 单文件假设见 3.2 |
| 对照台交互范式 | [码] `app.js` mark/accept/peek/同步滚动 | 2D 贴图对照可直接复用；3D 场景需新渲染路径 | — |
| 双轨导出/交付编号 | [码] `jobs.py:575-580,622-648`；`audit` | 目录包可沿用（zip 交付 + manifest） | — |
| 离线打包/CI/部署 | [码] `desktop/app.spec`；`.github/workflows/build-macos.yml` | 同仓第二个 spec/构建目标即可 | 见 §7 |

### 3.2 被硬编码为「文本 + 位图 + 单文件」的能力（逐条 file:line）

| # | 硬编码 | 证据 | 对三维/正射的具体影响 |
|---|---|---|---|
| H1 | 单 Document = 单文件 | `udm.py:80-88`（`path`、`fmt` 单值）；`jobs.py:85-87` | 正射无影响；OBJ+MTL、OSGB 目录、3D Tiles 瓦片集无法作为一个任务 |
| H2 | 页面 = 2D 画布 | `udm.py:71-76`；`mesh` 伪造 A4 `595×842`（`mesh_parser.py:48-49,590-591`） | 无场景/LOD/相机/材质；3D 预览只能显示成文本 |
| H3 | Block.kind 仅 text/table/image | `udm.py:50` | 贴图、材质、几何、节点、瓦片无表达位 |
| H4 | Span 仅文本区间/页面矩形 | `udm.py:118-140`；`pipeline._merge:108-146` 按 `(etype,value)` 合并 | 顶点/UV/tile 级定位不可能；三维只能做「贴图矩形」级 |
| H5 | Parser 单文件进出 | `registry.py:24-45`（`parse(path)` / `export(out_path)`） | 整包输入输出、多资产导出无协议 |
| H6 | 渲染只有页面 PNG | `registry.py:44`；`api.py:383-391`；`jobs.render:508-523`（无数据即报「不支持预览渲染」） | 3D 预览无落点 |
| H7 | Job 单路径、成品同扩展名单文件 | `jobs.py:168-187`；`jobs.py:575-580`；`jobs.py:1017-1018` | 目录包/zip 交付无落点 |
| H8 | 预览 kind 仅 raster/flow | `pipeline.py:832`；`CONTRACT.md:240-241`；`app.js:1019-1021` | 需要新增 `scene`（契约+后端+前端三处） |
| H9 | verify 只接受单成品路径 | `pipeline.py:635-653`（`parser.parse(exported)`） | 目录包无法 verify；几何/贴图无检查语义 |
| H10 | 规则按单 fmt 过滤 | `pipeline.detect:66`（`doc.fmt not in rule.scope`） | 需新增 fmt（如 `mesh` 已存在但只有 13 条规则；`tileset`/`pointcloud` 需新增） |
| H11 | 上传单文件、512MB 上限 | `api.py:121-133`；`config.py:34` | 三维整包/大 TIF 需「按路径打开」或 zip 上传；浏览器拖目录不支持 |
| H12 | 三维被「文本化」处理 | `mesh_parser.py:562-587`（parse 只抽文本块）、`707-740`（GLB 只读 JSON chunk）、`1059-1137`（BIN 不动） | 现状能力边界：文本清理 + OBJ/PLY 顶点平移；贴图/内嵌图/几何/OSGB 全无 |
| H13 | `can_render=False` 即无预览 | `mesh_parser.py:549` | 三维任务在对照台显示为文本行（[测] B-3） |
| H14 | 多文件资产无扫描入口 | `mediascan` 输入是 `image_rgb`（`mediascan.py:56,188`），但**没有容器内资产抽取层** | OBJ `mtllib`/`map_Kd`、GLB `images`→`bufferViews`、OSGB 内嵌 JPEG、3D Tiles `content.uri` 都无人解析 |

### 3.3 三维现状实测（补充 [文] `docs/research/03` §1.2/§3.1-3.2 已过时结论需按新 HEAD 更新）

| 格式 | 现状 [码] | 未做 |
|---|---|---|
| `.obj` | 抽 `#`/`mtllib`/`usemtl`/`o`/`g` 文本；顶点流式扫描；导出文本覆盖 + 顶点减包围盒中心（`mesh_parser.py:188-240,590-648,782-852`） | MTL 内容、外部贴图、材质名规则、整包输出 |
| `.ply` | header comment/obj_info 文本 + ASCII/二进制顶点减中心（`651-677,855-1018`；>256MB 只清头） | 带纹理 PLY 扩展 |
| `.stl` | 80 字节头置零 / ASCII 等长覆盖；几何**原样**（`1021-1056`） | 几何重定位 |
| `.glb` | 只 JSON chunk 等长覆盖；BIN/几何不动（`707-740,1059-1137`，实测 BIN 逐字节一致 [测] B-4） | 内嵌图、几何、accessor/bufferView 重算 |
| OSGB / 3D Tiles / LAS-LAZ / `.gltf` / `.mtl` | **无解析器**（[测] `find_parser` 全部 None，见附录 B-2） | 全部 |

实测还发现两个「能力宣传 > 实现」问题：
- 投放台文案已写「三维模型」（`index.html:45`），README 支持表写三维（`README.md:168`），但对照台只能显示文本流；
- 三维规则只有 13 条（`common`+`geo-cn`，[测] B-5）：OBJ 注释里的项目名「XX新区」、`mtllib` 名、瓦片名、GLB 里的「XX测绘院/ContextCapture」**都不在规则里，检测为 0**（[测] B-6）。

---

## 4 正射 P1 与三维各能力在架构上的落点

### 4.1 正射 P1 增强：**全部可原位扩展（A），不需要新抽象**

| 能力 | 现状 | 落点 | 新抽象 | 工期 |
|---|---|---|---|---|
| 多帧 TIFF 也做中性化 | Pillow 回退明示「未做坐标中性化」（[码] `tiff_parser.py:574-580`；[文] `tiff.md:224`） | 扩 `_export_with_pillow` / 分帧 rasterio 写法 | 无 | 1–1.5 人日 |
| 坐标取整/量化、按密级降采样（DESIGN 3.6） | 当前只有平移+旋转+`scale=1`（[文] `tiff.md:16`；`georef.make_params:618-672`） | `georef` 参数与重采样（`YINZHAN_TIFF_*` 已有框架，`tiff.md:66-76`） | 无（Plan 层可加参数） | 1–2 |
| 4+ 波段语义（RGBN/alpha）、16bit 换名限制 | 明示「语义未校验」/「退化为涂黑」（[文] `tiff.md:91-92,225`；[码] `tiff_parser.py:679-682`） | tiff_parser 内 | 无 | 1 |
| 大图预览/框选体验、内存基线 | 400Mpx 峰值 1016MB（[文] `tiff.md:158`）；预览降采样已实现（`tiff_parser.render:521-539`） | 调窗口/预览参数 + 回归基线 | 无 | 0.5–1 |
| 验收自动化（gdalinfo/rasterio 断言进回归） | `test_tiff.py` 33 项 + regression 已含 TIF 探针（[文] `tiff.md:153-195`） | 扩展 `app/tests` | 无 | 1 |
| 敏感地物自动识别 | 明确「不承诺，必须人工框选」（[文] `DESIGN.md:273`），框选通路已具备（`jobs.py:401-451`、`tiff_parser._paint_window`） | 仍不做自动；P1 只做体验 | 无 | 0 |

**判断**：正射 P1 剩余 3–5 人日，是现有域收口，**不应成为「单独立项」的理由**。

### 4.2 三维各能力落点矩阵

图例：**A**=原位扩展（现有单文件/文本/页面模型内可完成）；**B**=需新增抽象但可在同仓独立域模块内完成；**C**=当前架构不适配/需独立域甚至独立项目；**D**=本阶段不做。

| 能力 | 现状 | 落点 | 需要的新抽象形态 | 工期（1 人） | 风险 |
|---|---|---|---|---|---|
| OBJ + MTL + 外部贴图 | OBJ 文本+顶点平移；MTL/贴图不读（`mesh_parser.py:1-23,782-852`；`docs/research/03` §1.2 #8/#9） | **B** | **Package/Asset**：`PackageJob(root)`、`Document.assets[]`（role/kind/path）、`parse_package/export_package`；贴图 → `ImageParser`/`mediascan` 复用 | 3–4 | 中（目录输出、材质路径重写） |
| GLB 内嵌图 | BIN 不解析（`mesh_parser.py:1059-1137`；[测] B-4） | **B** | 二进制重打包器：解析 `images→bufferViews`，抽 JPEG/PNG → 图像脱敏 → 重排 BIN/重算 offset+4 字节对齐；JSON 重写 | 4–6 | 中高（字节级重写，失败必须阻断） |
| GLB/STL 几何重定位 | 明确 warning「暂不支持」（`mesh_parser.py:1054,1137`） | **B** | 几何变换 Plan：解析 accessor → 平移/旋转/量化 → 重写 BIN + 更新 `min/max`/transform；点云同理 | 2–4 | 中（accessor 类型/归一化） |
| 3D Tiles（tileset.json + b3dm/glTF + transform） | 无解析器（[测] B-2；`docs/research/03` §1.2 #12） | **C（B 域内深子项）** | **Scene/TileTree**：tileset 递归 + `content.uri`/`transform`/`region`/`box`；b3dm 拆包（复用 GLB 重打包）；目录整包输出；transform 中性化复用 `georef` 数学 + vault 存参 | 5–7 | 高（目录、瓦片数、外部 tileset） |
| LAS/LAZ 点云 | 无；laspy 未装、CONTRACT 禁 trimesh（[文] `CONTRACT.md:15`；[测] B-1） | **B** | **PointCloudParser**（新 `format_id`）+ 点云几何中性化 Plan（平移+旋转+0.5m 量化+抽稀+加噪）+ VLR/CRS 清理 + header bbox/count 重算；依赖 laspy（纯 Python）+ lazrs（0.42MB wheel） | 4–5 | 中（大文件流式、LAZ 许可血缘） |
| OSGB 贴图/几何 | 无规范、无样例、无测试（[文] `docs/research/03` §8.1 #4；[测] B-1） | **C / D** | 自研 OSG 二进制容器解析（clean-room，禁复制 LGPL 代码）+ 内嵌 JPEG 抽取/等长回写/块重排 + PagedLOD 节点变换中性化 + `metadata.xml SRSOrigin` 清理；整包 job | 纹理 5–8；几何 10–15 | **最高**（无规范/法律/样例/可打开性） |
| 3D 预览（WebGL/查看器） | 无；`can_render=False`，预览=文本流（[测] B-3；`jobs.py:522`） | **C（前端新域）** | 新 `kind="scene"` + WebGL/three.js（离线打包）或内嵌查看器；相机/瓦片流式；框选=3D 拾取或退化为「贴图 2D 对照」；性能与对照台语义重做 | 最小 2–3；完整 8–15 | 高 |
| 工程文件/控制点/照片批量（KML/KMZ/XML/RAW） | 无解析器；JPG 单文件已具备 EXIF 剥离（[文] `docs/research/03` §5.1） | **B（可后置）** | 目录批处理 + text/xml 规则；工作量可控 | 2–3 | 低中 |

**落点结论**：三维中真正「不适配」的只有 **OSGB 容器** 与 **3D 预览交互**；OBJ+MTL、GLB、3D Tiles、LAS/LAZ 都可在**同仓独立域模块**里完成，前提是先立 **Package/Asset 协议** 与 **结构性 Verify**（见 §4.3）。

### 4.3 建议的新抽象（形态，供 CONTRACT v4 讨论）

1. **Package/Asset（最小必做，先于其它三维能力）**
   - `Document.fmt` 扩为 `mesh`/`tileset`/`pointcloud`/`package`；新增 `Document.assets: list[Asset]`（`role: geometry|material|texture|metadata|tile`、`path/rel_path`、`bytes/sha`、`handled: bool`）。
   - `Parser` 加可选方法（不破坏旧协议）：`parse_package(root: Path) -> Document`、`export_package(doc, ..., out_dir) -> ExportResult`（`ExportResult.path` 可为目录/zip）。
   - `Job` 增加 `root: Path`（目录）与 `output_kind: file|dir|zip`；`/api/jobs` 支持 `path` 为目录；上传改 zip（`MAX_UPLOAD` 之外走 `path` 模式）。
2. **Scene/TileTree**：`Document.scenes[]`（根 transform/CRS/瓦片树），每个 tile 挂 assets；供 3D Tiles/OSGB 共用。
3. **Plan 扩展**：`GeometryPlan`（`translate/rotate/quantize/decimate/noise`，参数入 vault 的 `extra["geo3d"]`，与 `geo` 同 AES-GCM 通道）。
4. **Verify 扩展**：`register_structural_verifier(name, fn)`（`pipeline.verify` 内新增钩子，对齐 `docs/red-lines.md` E2 的「可注册复检器」设想），返回 `structural: {...}` + `unverifiable: [...]`。
5. **Preview 扩展**：`PreviewDoc.kind="scene"` + `scene: {format, url/tileset, assets[]}`；前端新增 `<SceneView>`（WebGL）与「贴图对照」子视图。
6. **fmt/scope 扩展**：规则 scope 加 `mesh`（已）、`tileset`、`pointcloud`；新域规则包独立 YAML（复刻 `trade-cn`/`pipl-cn` 模式，[文] `CONTRACT.md:316`）。

---

## 5 四条路线对比与推荐

### 5.1 路线定义

- **A 同仓同应用继续做**：所有能力塞进现有「一文件一任务」应用，发布同一个包/同一个版本号。
- **B 同仓独立域模块（推荐）**：`yinzhan/geo3d/`（或独立顶层包 `yinzhan3d`）+ 可选依赖（`requirements-3d.txt`）+ 功能开关（`YINZHAN_3D`）+ 独立发布目标（第二个 PyInstaller spec / 安装包组件），共享内核（UDM 协议在 `udm.py` 扩、规则/检测/替换/vault/audit/图像扫描/UI 外壳）。
- **C 独立仓库/独立项目**：新建项目，复制或引用核心（submodule/私包），独立产品线、独立 UI、独立发布。
- **D 本阶段不做三维**：只做正射收口；三维仅保留现有 4 格式文本清理并明确「有限支持」；资源投入现有 5 个客户场景（律所/医院/财务）。

### 5.2 对比表（★=优势 / △=中性 / ✗=劣势）

| 维度 | A 同仓同应用 | **B 同仓独立域模块（推荐）** | C 独立仓库/项目 | D 本阶段不做 |
|---|---|---|---|---|
| 技术契合 | 正射 ★；三维 ✗（页面/单文件模型每加一能力都要打补丁） | 正射 ★（走 A）；三维 ★（新抽象隔离在域内，不动主链路） | 三维 ★（自由建模）；正射 ✗（重复实现已有 `georef`/tiff） | — |
| 复用度 | ★（100% 复用，但把三维复杂度灌入内核） | ★（规则/检测/OCR/映射/审计/任务/UI 全部复用，估计 **60–70%** 代码复用） | △（靠复制/引用，长期必然漂移；正射部分 0 复用） | ★ |
| 交付发布 | ★（一个包）；但三维依赖（laspy/lazrs/GLB 工具/3D UI 静态资源）让所有用户为三维买单 | ★（主包不变；三维独立包/组件，按需安装；两个构建目标共享源码） | △（两条发布链、两套 CI/签名/更新） | ★ |
| UI 与交互 | ✗（对照台二态 raster/flow 无法承载场景；强改风险高） | ★（共享外壳；新增 3D 视图组件，两个入口） | ★（自由）但用户要装两个产品、两套学习成本 | ★ |
| 验证与责任 | ✗（三维/几何无法验证却混在同一 `verify` 门禁里，削弱主产品门禁公信力） | ★（域级门禁 `test_geo3d` + 主门禁不变；`unverifiable` 显式声明） | △（独立门禁，但共享内核的回归要两边同步） | ★ |
| 工期（剩余） | 正射 3–5 + 三维 15–25 + 3D 预览 8–15（全部耦合在主链路回归风险中） | 正射 3–5 + 域模块 12–20（OBJ/GLB/3D Tiles/点云）+ 预览最小 2–3 | 正射仍在 A 3–5 + 新项目 25–45（含重搭内核/UI） | 正射 3–5；三维 0 |
| 团队 | 1–2 人可维护（但知识面要求极高） | 1 人可起（域内自包含），后续 2 人 | ≥3 人（前端+引擎+构建/测试） | 现有团队 |
| 商业定位 | 「通用门」被稀释成「什么都能做但每样都不深」 | ★「跨格式 + 地理/三维 + 可举证」的差异化可独立报价 SKU（测绘/应急/审图） | ★ 可做垂直产品，但品牌/销售双线 | ★ 聚焦律所/医院/财务首批场景 |
| 维护 | ✗ 三维依赖/回归拖慢主版本 | ★ 依赖隔离、开关可关、失败不拖垮主产品 | △ 双仓同步成本 | ★ |
| 风险 | 高：主产品发布被三维构建/回归阻塞；`verify` 语义被污染 | 中：需先立 Package/Verify 协议；域内二进制处理风险可控 | 高：重复建设、接口漂移、OSGB 法律/规范风险仍在 | 低：但可能错失测绘客户窗口 |

### 5.3 推荐

> **正射：A（原位收口，3–5 人日）。三维：B（同仓独立域模块）+ 分期；OSGB：D（本阶段不做）/ C（条件触发）；3D 预览只做「最小可用」（贴图/资产 2D 对照 + 可选网格查看）。**

关键理由（三条）：

1. **架构代价最小**：正射已经在现有内核里闭环，三维真正缺的是 **5 类抽象**（Package/Scene/PointCloud/结构 Verify/scene 预览），把它们放进同仓独立域模块可以**隔离复杂度**，同时保住 60–70% 的既有内核复用（规则/检测/OCR/映射/审计/任务/UI 外壳），避免另起炉灶重复实现。
2. **验证责任可分**：当前 `pipeline.verify` 的核心承诺是「重解析成品还能找到原文就拒绝交付」；三维几何/贴图**不可像素级复检**，若混入同一条门禁会让「出口自检」的可信度被稀释。域模块可以设自己的「结构断言 + 贴图重扫 + 人工抽样声明」门禁，主门禁保持 F1–F19 强度（[文] `docs/red-lines.md:22-44`）。
3. **商业与发布可控**：测绘/倾斜摄影是独立客群与独立 SKU，B 允许「主产品不带三维依赖（不背 +100MB 包）」，按需安装/独立发布包，正射用户零成本；C 则在需求未验证前承担双仓/双团队/双发布成本，且 OSGB 的无规范风险并不会因拆项目而消失。

### 5.4 什么可量化条件下才值得「拆项目」（C）

把「拆 C」从偏好变成触发式决策。**满足 ≥3 条硬条件**再开独立项目（否则保持 B）：

| # | 触发条件（可量化） | 阈值 | 数据来源/验证 |
|---|---|---|---|
| T1 | 三维域代码/依赖体量 | 域代码 > 10,000 行且独立依赖解压 > 200MB，或主安装包因三维 +>120MB | `cloc` + PyInstaller 产物对比 |
| T2 | 客户与收入 | 三维付费线索 ≥ 5 家，或 ≥ 1 个测绘/倾斜摄影合同 ≥ 30 万元/年，或三维 SKU 定价 ≥ 主产品 1.5×且成交 ≥ 2 单 | CRM/合同 |
| T3 | 需求结构 | OSGB/倾斜摄影整包占三维工单 ≥ 50%，且需要 ≥ 2 人连续 ≥ 3 个月 | 工单系统 |
| T4 | 发布耦合 | 主产品因三维依赖/构建/回归被拖慢 > 3 天/版，或主回归时长 +>50%，或主安装包体积翻倍 | CI 时长/体积 |
| T5 | 交付/合规形态 | 需要独立离线包、内网部署、测绘资质/保密要求、单独安装器 | 售前需求 |
| T6 | 团队 | 可分出 ≥ 2 名全职（引擎+3D 前端）≥ 3 个月，且主产品不受影响 | 人力规划 |

反向条件（任一条成立就不拆）：三维工单 < 5%/月；三维需求以 OBJ/GLB 查看/单文件为主；OSGB 客户接受「转 3D Tiles 后交付」；主产品仍处功能验证期（无 5 个真实付费用户，[文] `DESIGN.md:622` 的 M0 门槛）。

**当前判断**：T1/T2/T3 均不满足（无三维样例、无测绘合同证据、OSGB 占比 0），因此**不拆 C；也不做 D 一刀切，而是 B 的低成本起步**（先做 OBJ/GLB/点云，OSGB 等触发）。

---

## 6 分期路线（每阶段验收标准 + 工期区间）

> 估算口径：1 名熟悉 Python/PIL/numpy/打包的开发，含自测与文档；不含 3D 前端大改。**与 `docs/research/03` §6 的差异**：该文 P0（11–15.5 人日）已完成（[文] `docs/impl/tiff.md`），故下表是**剩余增量**，且把「口径收敛/门禁建设」作为 P0 显式列出；OSGB 保持条件触发。

### P0 · 口径与门禁先行（0.5–1 人日，必须先做）

| 交付 | 验收 |
|---|---|
| README/投放台/支持表如实标注：三维=「4 种中间格式的文本清理+OBJ/PLY 顶点重定位；贴图/OSGB/3D Tiles/LAS 未支持；预览为文本流」；正射=「P0 已闭环（中性化/strip 可选），P1 增强路线图」 | 文案与实际能力逐条对照通过；不再出现「三维模型」无条件承诺 |
| `CONTRACT.md` v4 起草：Package/Asset、fmt 扩展、verify 结构化钩子、§0 依赖表（rasterio/pyproj 由「可选」改为「运行时可选/桌面构建默认」；新增 laspy/lazrs 可选；trimesh 仍禁用） | Lead/QA 评审通过；[文] `CONTRACT.md:10-16` 与 `requirements.txt:21-24` 的矛盾消除 |
| 三维回归夹具 v0：合成 OBJ/GLB/PLY/STL 各 1（不落真实客户数据），`run_all.py` 增 `mesh` 键 | `python -X utf8 app/tests/run_all.py --only mesh` 可跑（当前三维**零测试**，[测] B-7） |

### P1 · 正射收口（3–5 人日）

| 任务 | 验收标准 |
|---|---|
| 多帧 TIFF 中性化、4+ 波段/RGBN 语义、坐标量化/按密级降采样（`georef`/`tiff_parser`） | ① 每帧无真实 Geo 残留；② `geo_params.dst_transform` 与成品一致、往返误差 <1e-6 像素；③ 16bit/4band/多帧/400Mpx 四类样例全绿；④ 400Mpx 峰值内存 ≤1.5GB、导出 ≤15s |
| 验收脚本进回归（rasterio 断言；无 rasterio 时明确 skip 记录） | `regression.py` 新增 TIF 结构化硬检查；`e2e` 保持 5/5 残留 0 |
| 大图预览/框选体验 + warning 收敛 | 大图预览 ≤3s 出图；降采样扫描有明确 warning；UI 框选落点仍可涂 |

### P2 · 三维中间格式 + Package 抽象 v1（6–10 人日）

| 任务 | 验收标准 |
|---|---|
| Package/Asset 协议 + OBJ+MTL+外部贴图（整包输入/输出目录，zip 交付） | 用合成 `obj+mtl+2 贴图` 整包：贴图内文字残留 0；MTL/OBJ 文本残留 0；原软件/Blender 可打开；目录清单 manifest 完整 |
| GLB 内嵌图抽取→图像脱敏→重打包；GLB/STL 几何重定位 | ① JSON 仍可解析、chunk 长度/对齐不变或重算正确；② BIN 中未处理块哈希不变；③ 顶点数/索引范围/包围盒断言通过；④ 贴图 OCR 残留 0 或声明 skip 并阻断 |
| 纹理/内嵌图复用 `image_parser`+`mediascan`（人脸/证件/车牌/码/印章） | 合成贴图含二维码/人脸：打码率 100%、导出后可解码率 0（[文] `docs/research/02` 验收 A10 同口径） |
| 3D 预览最小版：资产清单 + 贴图 2D 对照 + 可选网格查看（非阻塞） | 用户能看到「哪些贴图被处理/未处理」；无 3D 场景也不影响导出门禁 |
| 域测试 `test_geo3d.py` + 样例 + `run_all` 新键 | 域测试 ≥ 25 项全绿；主 regression 不受影响（204 项基线不降） |

### P3 · 3D Tiles + LAS/LAZ 点云（10–16 人日）

| 任务 | 验收标准 |
|---|---|
| 3D Tiles：tileset 递归、b3dm/glTF 复用 GLB 重打包、`transform/region` 中性化入 vault、目录整包输出 | ① tileset JSON 结构校验通过、所有 `content.uri` 存在；② 真实坐标（region/transform 与源一致）0 残留；③ Cesium/查看器可打开；④ 未修改 tile 哈希不变；⑤ 任一步失败必须阻断导出（不得 fallback 原样复制） |
| LAS/LAZ：laspy+lazrs；平移+旋转+量化+抽稀+加噪；VLR/CRS 清理；header bbox/count 重算 | ① CRS/VLR 中真实 SRS 0；② header min/max 与点集一致（<1e-6）；③ 输出可被 CloudCompare 打开；④ 抽稀比例/噪声参数入 vault 可查；⑤ 原始 WKT 字符串在文件字节中 0 命中 |
| 大目录/性能：单包 ≤ N 文件、断点/取消、进度 | 1000 瓦片目录：取消可中断、内存峰值 ≤2GB、进度可见 |

### P4 · OSGB（条件触发；15–23 人日；建议 D 直到触发）

| 子阶段 | 验收 |
|---|---|
| 纹理 v1（5–8）：内嵌 JPEG 抽取→脱敏→等长回写/块重排；`metadata.xml` SRS/SRSOrigin 清理 | 抽样 ≥20 张贴图 OCR 残留 0；OSGB 查看器可打开；SRSOrigin 真实坐标 0；失败阻断 |
| 几何 v2（10–15）：PagedLOD/根变换/顶点绝对坐标中性化 | 上述 + 结构断言（节点树/变换矩阵）；说明「只对顶层变换/声明边界」的限值；clean-room 评估通过、法务确认 |
| 触发条件 | 见 §5.4 T3/T5；且拿到 ≥1 套真实 OSGB 样例与可运行查看器 |

### 工期汇总（剩余）

| 范围 | 人日 | 结果 |
|---|---|---|
| P0 + P1 | **3.5–6** | 正射真实可交付收口 + 三维口径诚实 |
| MVP（P0+P1+P2 最小预览） | **9.5–16**（≈10–16） | 正射收口 + OBJ 整包/GLB 贴图可脱敏 + 资产对照台 |
| 推荐版本（+P3） | **19.5–32**（≈20–32） | + 3D Tiles/点云 |
| 全量（+OSGB） | **34.5–55**（≈35–55） | 倾斜摄影整包（风险最高，条件触发） |

---

## 7 打包 / 依赖 / 质量保障影响

### 7.1 PyInstaller 与安装包体积

- 现状：`desktop/app.spec:36-43` 对 `rapidocr/onnxruntime/zxingcpp/rasterio/pyproj` 做 `collect_all`；`excludes` 已排除 torch/jax 等（`app.spec:64-70`）。
- 正射 P0 实测：rasterio+pyproj 安装解压 ≈ **90.8MB**（rasterio 15.5 + rasterio.libs 51.1 + pyproj 17.5 + pyproj.libs 6.7，[文] `tiff.md:127-136`）；安装包预计 **+60–100MB**；README 已写「300MB+」（[文] `README.md:388`）。
- 三维新增依赖：**laspy（纯 Python，<1MB）+ lazrs（Linux/Windows/macOS wheel ≈0.42MB，[文] `docs/research/03` §5.4）**；**不引入 trimesh**（[文] `CONTRACT.md:15`），GLB/OBJ/STL/OSGB 解析自研；3D 预览若用 three.js 需打包静态资源（≈1–2MB）。
- 结论：**三维依赖体积可忽略，真正让安装包变重的是正射 rasterio/GDAL（已发生）**；三维的主要风险是代码复杂度与测试门禁，不是体积。B 路线允许把「三维构建目标」独立成第二个安装包/组件，主包不背三维 UI 资源。

### 7.2 macOS / Linux 构建

- `.github/workflows/build-macos.yml` 用 `pip install -r app/server/requirements.txt`（含 rasterio/pyproj）→ macOS arm64/x64 需有对应 wheel；本机无法验证，CI 是唯一事实来源。
- 三维：laspy 纯 Python 无风险；lazrs 需 macOS arm64/x64 wheel（发布方有）；WebGL 预览依赖 pywebview/WebKit，macOS 需验证。
- 建议：新增 `requirements-3d.txt` 与 `desktop/app3d.spec`（或 spec 内 `YINZHAN_3D=1` 条件收集），macOS workflow 增加独立 3D 构建 job，失败不阻塞主 Desktop job。

### 7.3 离线 wheel

- 已有清单与 sha256：`docs/impl/tiff.md:106-125`（rasterio/pyproj + 传递依赖，纯净 venv 离线安装实测通过，[文] `tiff.md:166`）。
- 三维补充：`laspy`、`lazrs` 需补下载与 sha256；**lazrs 血缘含 LASzip（LGPL）**，需法务/许可证评估（[文] `docs/research/03` §8.1 #9），或先只支持 LAS（laspy 写 LAS，无 lazrs）。
- 产品运行时不联网的红线不变（[文] `CONTRACT.md:16`；`docs/red-lines.md:14`）。

### 7.4 CONTRACT 修订清单（v4 草案要点）

1. **§0 依赖**：rasterio/pyproj 语义修正（运行时可选、桌面构建默认、缺失降级 warning）；新增可选 `laspy`/`lazrs`（LAZ）；保持 `trimesh`/`GDAL CLI`/`tesseract` 不可用；新增 3D 域依赖红线（不得联网、许可证白名单）。
2. **§2 UDM**：新增 `Asset`、`PackageKind`；`Document.fmt` 允许 `tileset`/`pointcloud`/`package`；`Document.assets/scenes`；`Span` 扩展（几何/UV/tile 可选字段，保持向后兼容）；`Decision` 不变，新增 `GeometryPlan` 概念。
3. **§3 Parser 协议**：新增可选 `parse_package/export_package/render_scene`；`can_render` 语义升级为 `render_kind: page|scene|none`；目录/zip 输出的 `ExportResult` 约定（path=目录、`manifest`）。
4. **§6 API**：`/api/jobs` 支持目录/zip；`PreviewDoc.kind="scene"`；`/asset/{id}` 预览贴图；导出返回 zip + manifest；`/api/health` 增 `formats`、`geo3d` 能力位。
5. **§7 Verify**：新增结构化复检器注册；`unverifiable` 显式字段；三维/点云专项断言（见 7.6）。
6. **§10 扩展点**：新增「3D/点云域」章节（mediascan 复用、贴图重扫、参数入 vault 的 `extra["geo3d"]`）。
7. **红线**：`docs/red-lines.md` 新增 F20（三维文本/贴图残留 0、几何结构断言、目录清单完整、失败阻断）与 S 系列（原始文件不被修改、vault 存参加密、`unverifiable` 不得静默）。

### 7.5 测试域与回归门禁扩展

- 现状：`run_all.py` 10 个套件（regression + 9 域，[文] `docs/red-lines.md:156`）；`regression.py` 204 项硬检查；**三维没有任何域测试、样例或 regression 探针**（[测] B-7）。
- 新增：
  - `app/tests/test_mesh.py`（OBJ/PLY/STL/GLB 文本残留、顶点平移、GLB chunk 长度、二进制不变性）；
  - `app/tests/test_geo3d.py`（Package/MTL/贴图、3D Tiles、LAS/LAZ、结构断言、`unverifiable` 声明）；
  - `app/tests/fixtures/mesh/` 合成样例（禁止真实客户数据；沿用 `app/tests/fixtures/pipl_public/generate_synthetic.py` 的「可复现合成」模式）；
  - `run_all.py` `_specs()` 增 `mesh`/`geo3d`（[码] `run_all.py:45-69`）；
  - `regression.py` 增硬检查：解析器格式清单含 mesh/pointcloud（按路线推进）、三维样例换名/涂黑 residual 0、GLB BIN 哈希不变、Tiles JSON/URI 完整、LAS CRS VLR=0、导出 manifest 完整；
  - 基线纪律：`baseline.json` 新增三维样例条目，`--update-baseline --reason` 才变（[文] `docs/red-lines.md:67-68`）。
- 性能门禁：三维大包/点云耗时与内存记软基线（F12 精神，[文] `docs/red-lines.md:37`）。

### 7.6 verify 对「几何/贴图不可验证」的应对（必须写进契约，不能静默放行）

`pipeline.verify` 的现有能力是「重解析成品 + 找原文 + 元数据 + 视觉 IoU」，对三维只能验文本（[码] `pipeline.py:635-758`）。建议五层：

| 层 | 做法 | 失败处理 |
|---|---|---|
| L1 结构断言 | 重解析成品：GLB JSON 可解析、chunk 长度/对齐、accessor/bufferView 边界合法、mesh 索引在顶点范围内、顶点数>0、包围盒有限；tileset JSON schema + 所有 `content.uri` 存在；LAS header point_count/bbox/min-max 与点集一致 | 不通过 → 删成品 + `VerifyError` |
| L2 字节不变性 | 未处理的 BIN/BIN chunk、未改 tile、未改贴图与输入哈希逐一对比；任何「本应不变」的字节变了即失败 | 同上 |
| L3 贴图重扫 | 抽取成品中的全部贴图 → `mediascan.analyze` + OCR + 规则复检；残留必须为 0；扫描失败/跳过的贴图必须显式声明 | 残留>0 或未声明 → 阻断；声明 skip 需用户二次确认并写审计 |
| L4 人工抽样声明 | 每批 ≥20 张贴图人工抽检；UI/API 记录 `manual_review: {count, batch, reviewer, at}`；未抽检不得导出「可外发版」 | 无记录 → 阻断（或只允许导出为「待复核」水印版） |
| L5 不可验证声明 | 导出记录 `unverifiable: []`（如「点云轮廓反推无法自动验证」「OSGB 几何中性化边界」）；UI 在交付卡明示 | 缺失 `unverifiable` 字段视为门禁缺陷 |

---

## 8 给 Lead / 用户的关键风险与决策点

| # | 风险/决策 | 建议 | 影响 |
|---|---|---|---|
| R1 | 三维口径已超出实现（投放台/README 宣传） | **先修口径再谈三期**（§6 P0）；不修就是「说了做不到」 | 信任与售后 |
| R2 | 三维验证不可像素级复检 | 结构断言+贴图重扫+人工抽样+`unverifiable` 声明（§7.6）；不承诺「合规」 | 产品责任边界 |
| R3 | OSGB 无规范 + LGPL 血缘 | 本阶段 D；触发时 clean-room + 法务评估；**不复制 OSG 源码** | 法律/工期 |
| R4 | 正射中性化「可还原」= 参数即钥匙 | 维持 vault AES-GCM + TTL/单次使用策略（[文] `DESIGN.md:247`）；话术只写「降密」 | 安全 |
| R5 | 点云/三维大文件内存与性能 | 流式/窗口/取消/进度（P2/P3 验收）；不设虚假「无上限」承诺 | 体验 |
| R6 | 依赖/构建膨胀拖慢主产品 | 3D 独立构建目标；主包保持现状；CI 双通道（§7.2） | 发布节奏 |
| R7 | 现在工作区有并行任务改动（signs） | 本文只读；后续动了 `udm.py/registry.py/pipeline.py/CONTRACT.md` 必须按红线先改契约再写码（[文] `docs/red-lines.md:66`） | 协作 |

---

## 附录 A 证据索引（关键 file:line）

> 行号为用户当前工作区；`pipeline.py` 因并行任务 +2 行，`jobs.py/api.py/visual_targets.py/app.js` 有未提交 signs 改动，引用均为**符号级**可靠、行号 ±5。

| 主题 | 证据 |
|---|---|
| UDM 单文件假设 | [码] `udm.py:71-88`（Document.fmt L83）、`udm.py:48-56`（Block.kind L50）、`udm.py:118-140`（Span） |
| Parser 协议 | [码] `registry.py:24-45`；插件兜底 L104-111；扫描器模块 L116；`find_parser` L158-172 |
| 管线 | [码] `pipeline.py:53-105`（detect）、`108-146`（merge）、`180-205`（decide）、`232-271`（propagate）、`489-498`（export）、`635-758`（verify）、`827-879`（build_preview，kind L832） |
| Job 单文件/单成品 | [码] `jobs.py:82-121`、`168-187`、`218-227`、`545-620`（输出名 L575-580）、`622-648`、`508-523`（render 报错 L522）、`401-451`（mark） |
| API | [码] `api.py:121-163`（upload/jobs）、`184-197`（preview/mark）、`217-225`（export）、`383-408`（render/file）、`89-118`（health） |
| 正射 P0 | [码] `tiff_parser.py:307-341`（parse）、`541-627`（export/中性化/strip/复检）、`631-725`（rasterio 窗口写/金字塔）、`georef.py`（`make_params:618-672`、`verify_exported_geo:787-861`、`read_linked_sidecars:862`）；[文] `docs/impl/tiff.md:10-19,43-63,127-136,149-167,200-233` |
| 正射语义 | [码] `pipeline.has_cleanup_targets:587-608`；[文] `CONTRACT.md:296-312`（§10.4-10.5）；`docs/red-lines.md:29,119-123` |
| 三维现状 | [码] `mesh_parser.py:1-23`（自述边界）、`48-54`、`544-587`、`590-740`、`743-779`、`782-1018`、`1021-1056`、`1059-1137`；[文] `docs/research/03:53-74,174-220` |
| 三维规则/测试缺口 | [测] 附录 B-5/B-6/B-7；[文] `docs/research/00:88-89,131` |
| UI 双态渲染 | [码] `app.js:1012-1022,1040-1093,1157-1261,1438-1500,1522-1552`；`CONTRACT.md:222-241`；`index.html:42-49` |
| 打包/依赖 | [码] `desktop/app.spec:36-43,64-70`；`requirements.txt:4-24`；`desktop/build_win.ps1:15-24`；`.github/workflows/build-macos.yml`；[文] `README.md:388`、`tiff.md:106-136` |
| 门禁/红线 | [文] `docs/red-lines.md:11-20,22-44,49-68,146-158`；[码] `app/tests/run_all.py:45-69`；`regression.py`（Gate 类 L65、check_* 分区 L148-1060） |
| 产品设计 | [文] `DESIGN.md:43-79`（总体架构）、`85-122`（UDM/Span 双语义）、`124-137`（五段管线）、`196-213`（格式→管线）、`260-288`（3.6 地理与三维）、`612-622`（M3） |
| 历史方案/旧进度 | [文] `docs/research/03` §5.2-5.5（技术路线/依赖/打包）、§6（分期）、§7（验收）、§8（风险与 D1–D11） |

## 附录 B 本次只读实测记录（`%TEMP%\arch_check\`，可复跑）

**B-1 依赖环境**（`python -X utf8`）
```
py 3.12.9 · Pillow 12.2.0 (MAX_IMAGE_PIXELS=89,478,485) · numpy 2.4.6
rasterio 1.4.3 (GDAL 3.9.3) · pyproj 3.7.2 (PROJ 9.5.1) · tifffile=True
trimesh=False · laspy=False · lazrs=False
```

**B-2 解析器注册表与判型**
```
parsers: ['docx','image','mesh','pdf','text','tiff','xlsx']
find_parser: .ply/.obj/.glb -> mesh ; .tif -> tiff
             .osgb/.las/.laz/.gltf/.mtl/.b3dm -> None ; 目录 -> None
```

**B-3 三维预览 = 文本流**
```
m.obj    preview: flow 595.0 842.0 ents:1
m.glb    preview: flow 595.0 842.0 ents:0
```

**B-4 GLB 导出不动 BIN**
```
m.glb export: bytes=168（原样复制告警：JSON chunk 内未找到可覆盖敏感文本…；"GLB 该格式暂不支持几何重定位"）
```

**B-5 三维规则仅 13 条**
```
mesh rules: 13 packs: common,geo-cn
ids: cn.private_key, cn.secret, cn.idcard, cn.phone, cn.geo_coord, cn.landline,
     cn.email, cn.map_sheet, cn.control_point, cn.crs, cn.person, cn.address, cn.org
```

**B-6 三维文本检测盲区**（合成 OBJ/GLB）
```
OBJ: "# Project: XX新区 CS: CGCS2000 / mtllib m.mtl / o Tile_+000_+000_L20"
     detect: [('CRS','CGCS2000')]   ← 项目名/瓦片名/材质名未命中
GLB: JSON ["XX测绘院 ContextCapture", "2.0", "Tile_+000_+000_L20"]
     detect: []                    ← 软件名/单位名/瓦片名未命中
```

**B-7 三维零测试/零样例**
```
samples/: 住院病历.docx 名片.png 委托代理合同.pdf 对账单.xlsx 正射影像.tif（无三维）
app/tests/: 无 test_mesh.py；grep 仅 qa_common.ALLOWED_FMTS / finance / table / trade 中出现 "mesh" 字样
```

**复跑命令**（探针脚本为一次性文件，未入库；本次在 `%TEMP%\arch_check\probe.py`，逻辑即「load_plugins → 打印 parsers/find_parser → 合成 OBJ/GLB → parse/detect/build_preview/export」）
```powershell
$env:YINZHAN_DATA_DIR = "$env:TEMP\arch_check\data"
$env:YINZHAN_OUTPUT_DIR = "$env:TEMP\arch_check\out"
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
python -X utf8 "$env:TEMP\arch_check\probe.py"
```

## 附录 C 主要引用文档

- `app/server/CONTRACT.md`（§0/§2/§3/§6/§7/§10）
- `DESIGN.md`（2 总体架构、3.1 UDM、3.2 五段管线、3.6 地理与三维、4.4 对照台、6 里程碑）
- `docs/research/03-三维倾斜摄影与正射TIF脱敏进度与方案.md`（旧进度基线 HEAD fb90233；其 P0 缺口已被 `docs/impl/tiff.md` 补齐，本文按新 HEAD 校正）
- `docs/impl/tiff.md`（正射 P0 实现与实测）
- `docs/research/00-总览与审核清单.md`（§2.2 现状、§3 共性短板、§4 顺序、§5 决策）
- `docs/red-lines.md`（红线与回归门禁）
- `README.md`（支持表 166/168、已知限制 329/330、体积 388）
- `app/tests/run_all.py`、`app/tests/regression.py`、`desktop/app.spec`、`desktop/build_win.ps1`、`desktop/installer.iss`、`.github/workflows/build-macos.yml`
