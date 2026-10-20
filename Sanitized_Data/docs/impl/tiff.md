# 正射 TIF P0 实现说明（大图 / 位深 / 坐标中性化 / sidecar / ICC / Orientation）

> 状态：已实现（P0 + P0 缺陷修复 Orientation） · 2026-10-20 · 负责人：researcher-geo（task-11 / task-40）
> 代码：`app/server/yinzhan/georef.py`（纯逻辑）、`app/server/yinzhan/parsers/tiff_parser.py`（解析/导出路径 + Orientation 转正）
> 测试：`app/tests/test_tiff.py`（36 项）+ `app/tests/e2e.py`（5/5）+ `app/tests/regression.py`（**240 项硬检查全绿**）
> 上游决策：方案 A 已批准 D1 中性化必须、D2 可还原、D3 本地任意 CRS、D4 允许 rasterio/pyproj、D7 位深默认保留（见 `docs/research/03-…md` §8.2 与 `docs/research/00-…md`）。

---

## 1. 完成情况（对照 task-11）

| # | 目标 | 状态 | 实现/证据 |
|---|---|---|---|
| 1 | 大图：>1.79 亿像素可打开；峰值内存 ≤ ~1.5GB | ✅ | rasterio 窗口读写；20000×20000（4 亿像素）实测：解析 4.7s、导出 7.0s、峰值 **1016MB**、内部金字塔 5 级（§6） |
| 2 | 16bit/多波段尽量保留，降级必须 warning | ✅ | 保留源 dtype/波段（实测 uint16×3、uint8×4 均保留）；Pillow 回退统一 8bit RGB 时 warning；4+ 波段语义 warning |
| 3 | 坐标中性化 v1：随机平移+旋转，参数入 `doc.extra["geo_params"]`；写本地任意 CRS 或 strip | ✅ | `georef.make_params`（θ∈±3°、dx/dy∈±200~5000m、scale=1）；成品 LOCAL_CS（EPSG=None）；`geo_mode="strip"` 彻底去参考；参数 JSON 序列化后可被 jobs 写入 vault `extra["geo"]` |
| 4 | 导出后复检：Geo/ICC/EXIF 通过 | ✅ | 解析器侧 `georef.verify_exported_geo`（真实 CRS/原 tiepoint/ICC/EXIF/GPS/XMP/IPTC 一律 `ExportError`）；`pipeline.verify` 双保险 |
| 5 | 测试：样例 + 大图/16bit/多帧/注入标签；断言 0 真实 Geo、可还原、大图可处理、e2e 不破 | ✅ | `test_tiff.py` 36 项全绿（§7）；e2e 5/5、残留 0 |
| 6 | 红线：不破坏既有功能、不 push、依赖离线可用 | ✅ | e2e 5/5；依赖离线 wheelhouse 可纯净安装（§4）；未 push |

**行为约定（与 Lead 对齐）**
- `doc.meta["geo_tags"]` 语义 = **成品里仍存在的真实地理参考**。中性化成品写的是 LOCAL_CS，parser 会把本地 CRS 标签归入 `meta["geo_local_tags"]` / `meta["geo_neutralized"]=True`，**不会**把本地 CRS 误报进 `geo_tags`，因此 `pipeline.verify` 不会误拦截。
- `doc.meta["geo_source_tags"]` = 源文件里被剥离的真实标签（审计/复检对照）。
- `doc.extra["geo_transform"]` = 源地理参考（解析时写入，`pipeline.has_cleanup_targets` 据此允许“无文字、只清理”的导出）。
- `doc.extra["geo_params"]` = 本次中性化参数（导出时写入；jobs 在 ai 模式自动写入 vault `extra["geo"]`；human 模式不存）。

---

## 2. 代码结构

```
app/server/yinzhan/
├─ georef.py         # 新增：可选依赖惰性导入、TIFF IFD 扫描、地理参考读取、
│                    #       中性化数学（正/逆变换）、成品复检、sidecar 清单
└─ parsers/tiff_parser.py
   ├─ parse: rasterio 优先（单页/大图）→ 失败或多帧回退 Pillow
   ├─ export: rasterio 窗口化重写（保留 dtype/波段/本地 CRS/金字塔）
   │          或 Pillow 回退（只剥离 + 明确 warning）
   ├─ render: rasterio 降采样读预览（大图不再整图解码）
   └─ mediascan hook（Lead 加的逐帧扫描，保留原逻辑；大图自动降采样扫描）
```

### 2.1 `georef.py` 关键接口

| 接口 | 说明 |
|---|---|
| `scan_tiff(path) -> TiffStructure` | 只读 TIFF 头/IFD 链表：页数、尺寸、tag 集合、首帧关键值；GDAL 内部金字塔 IFD（tag 254）不计入页数 |
| `read_georef(path) -> GeoRef` | rasterio 优先；无 rasterio 用 Pillow tag 解析。`kind` ∈ real/local/none |
| `make_params(src, seed=None, scale=1.0)` | 生成参数：`theta_deg/dx/dy/scale/anchor/crs_before/crs_before_wkt/dst_crs_wkt/src_transform/dst_transform/src_size/invertible` |
| `strip_params(src)` | `geo_mode="strip"` 的审计参数（不可还原） |
| `pixel_to_local` / `local_to_pixel` / `world_to_local` / `local_to_world` | 可还原变换（测试断言往返误差 <1e-6 像素） |
| `verify_exported_geo(path, expect_neutralized, params)` | 解析器侧复检；返回问题列表 |
| `read_linked_sidecars(out_path)` | `.tfw/.prj/.aux.xml/.ovr/.kml/.rpc/.rpb/.imd/*.tif.xml` 清单 |

中性化数学（写入 `dst_transform`，供 GIS 直接打开）：

```
anchor   = 源影像世界坐标包围盒中心
local(x) = R(θ) · scale · (world − anchor) + (dx, dy)
dst_transform = local ∘ src_transform            # 6 元组仿射
还原：world = R(−θ) · (local − (dx,dy)) / scale + anchor；px = src_transform⁻¹ · world
```

### 2.2 可调环境变量

| 变量 | 默认 | 含义 |
|---|---|---|
| `YINZHAN_TIFF_GEO_MODE` | `neutralized` | 默认中性化写 LOCAL_CS；`strip`=彻底去参考 |
| `YINZHAN_TIFF_FULLREAD_MAX_PIXELS` | 40,000,000 | 超过则 OCR/扫描用降采样预览（控内存） |
| `YINZHAN_TIFF_SCAN_MAX_PIXELS` | 50,000,000 | 超过则全分辨率图像扫描改预览 + warning（条码类提示人工复核） |
| `YINZHAN_TIFF_PREVIEW_MAX_SIDE` | 3000 | 大图预览最长边（OCR 内部还会缩到 1600） |
| `YINZHAN_TIFF_WINDOW` | 2048 | 导出窗口尺寸（像素） |
| `YINZHAN_TIFF_BLOCK` | 256 | 输出 TIFF 分块大小（16 的倍数） |
| `YINZHAN_TIFF_COMPRESS` | `deflate` | 输出压缩（`deflate`/`lzw`/`zstd`；JPEG 仅 8bit RGB 可选） |
| `YINZHAN_TIFF_OVERVIEWS` | `1` | 是否生成内部金字塔 |
| `YINZHAN_TIFF_YAW_RANGE` | 3.0 | 旋转角上限（度） |

### 2.3 Orientation（EXIF tag 274）契约（task-40 修复）

CONTRACT §11.1 要求：图片 parse/export/render 在入口按 Orientation 转正像素，导出不得再带
Orientation 标签。TIFF 的落点：

| 路径 | parse | render | export | 说明 |
|---|---|---|---|---|
| **Pillow 路径**（多帧/无 rasterio/大图拒绝后的回退） | ✅ 转正 | ✅ 转正 | ✅ 转正 + **不写 274** | 单帧 TIFF 才转正（保持多帧帧结构）；`KEEP_TAGS` 已移除 274 |
| **rasterio 路径**（单页大图/正射主场景） | ⚠️ 不转正（已知限制 P1） | 保持原始像素朝向（与 parse 坐标一致） | ⚠️ 不转正 + 不写 274 | parse/export 都发明确 warning，建议先转正源图或强制 Pillow 路径 |

**实现要点**：
- 新增轻量 IFD 读取 `_read_tiff_orientation()`（不依赖 rasterio/PIL 全解码；BigTIFF/损坏按 1 处理）。
- **Pillow 新版本陷阱**：Pillow >=11/12 的 TIFF 解码器在 `Image.open` 时已按 274 自动转正
  （`image.size` 与 tag 256/257 存储尺寸不同）。`_apply_tiff_orientation()` 用“存储尺寸 vs
  当前尺寸”判断，只有确实未转正时才调用 `ImageOps.exif_transpose`，避免**二次旋转**；
  `render` 走 `_render_tiff_pillow()` 同一逻辑（不复用 image_parser 的无条件转正，防止
  TIFF 被旋转两次）。
- 导出 Pillow 路径在涂黑前转正，因此 `entity.spans` 的像素坐标与页面尺寸一致；
  `_safe_tiffinfo()` 只保留 282/283/296，并显式 `pop(274)`。
- 成品重解析 `doc.meta["orientation"]=1`（无标签），页面尺寸保持转正后的像素尺寸。

---

## 3. 行为与降级矩阵

| 场景 | 行为 | 警告 |
|---|---|---|
| rasterio 可用 + 单页 + `neutralized` | 保留 dtype/波段；窗口化重绘；写 LOCAL_CS（随机平移+旋转）；内部金字塔；参数入 `doc.extra["geo_params"]` | “已做坐标中性化…只降密不匿名” |
| rasterio 可用 + 单页 + `strip` | 不写 crs/transform；无 Geo 标签；仍保留 dtype/波段 | “strip 模式：坐标不可还原” |
| 多帧 TIFF | Pillow 回退：逐帧重绘、剥离 Geo/元数据/ICC、sidecar 清理；**不做中性化** | 明确 warning（“未做坐标中性化”） |
| 没装 rasterio/pyproj | Pillow 回退：同上；超大图（>2×Pillow 上限）直接中文 `ParseError` 提示装 rasterio 或切图 | 明确 warning |
| 无地理参考的普通 TIFF | 只剥离元数据（不写 crs、不中性化） | — |
| 源 TIFF 带 ICC/EXIF/XMP/描述/设备 | 一律剥离；成品再扫一遍，残留即 `ExportError` | 源文件 warning 列出待剥离项 |
| 源 TIFF 带 `.tfw/.prj/.aux.xml/.ovr/.kml/.rpc/.rpb/.imd/.xml` | 删除成品派生 sidecar（不碰源文件） | 删除失败时 warning |
| 4+ 波段 | 前 3 波段按 RGB 处理、其余原样；RGBN/alpha 语义另行提示 | √ |
| 非 8bit / 单波段影像上的“换名文本” | 退化为涂黑（否则会破坏 dtype/波段），只 warning 一次 | √ |
| 源 TIFF 带 Orientation=3/6/8（Pillow 路径） | parse/render/export 按 274 转正像素；成品不写 274 | “已按 TIFF Orientation=… 转正像素” |
| 源 TIFF 带 Orientation（rasterio 路径） | 保持原始像素朝向（已知限制 P1），parse/export 明确 warning；成品无 274 | “rasterio 路径暂不转正像素” |
| 多帧 TIFF 带 Orientation | 为保持帧结构不逐帧转正（warning）；单帧才转 | “多帧 TIFF 含 Orientation…未逐帧转正” |

---

## 4. 依赖与离线打包（Lead 统一改 requirements/app.spec）

### 4.1 requirements 建议新增

```text
# 地理影像（可选依赖；import 失败自动回退 Pillow + 明确 warning）
rasterio>=1.4.3          # BSD-3；Windows wheel 自带 GDAL 3.9.x
pyproj>=3.7.2            # MIT；wheel 自带 PROJ 数据
```

### 4.2 离线 wheel 清单（本机 2026-10-20 实测，Windows 10/11 + CPython 3.12.9）

下载命令（**注意**：本机 pip 默认走 `127.0.0.1:4780` 代理会挂起；须先清代理环境变量）：

```powershell
cmd /c "set HTTPS_PROXY=&& set HTTP_PROXY=&& set https_proxy=&& set http_proxy=&& ^
  python -m pip download --only-binary=:all: --no-cache-dir -d %TEMP%\tiff_wheels ^
  rasterio==1.4.3 pyproj==3.7.2"
# 离线安装（产品运行不需要联网）：
python -m pip install --no-index --find-links %TEMP%\tiff_wheels rasterio==1.4.3 pyproj==3.7.2
```

| wheel | 大小 | SHA-256 |
|---|---|---|
| `rasterio-1.4.3-cp312-cp312-win_amd64.whl` | 25,430,154 B | `a962ad4c29feaf38b1d7a94389313127de3646a5b9b734fbf9a04e16051a27ff` |
| `pyproj-3.7.2-cp312-cp312-win_amd64.whl` | 6,312,176 B | `11614a054cd86a2ed968a657d00987a86eeb91fdcbd9ad3310478685dc14a128` |

传递依赖（同样离线下载，版本为实测）：`affine-3.0.1`、`attrs-26.1.0`、`certifi-2026.10.18`、`click-8.5.0`、`cligj-0.7.2`、`click-plugins-1.1.1.2`、`pyparsing-3.3.3`、`numpy-2.5.4`（主环境已有 numpy 2.4.6 时**不要**升级，安装用 `--no-deps` 或允许 pip 判定已满足）。

纯净 venv 离线安装实测：`--no-index --find-links` 安装成功（50.9s，含解压 90MB）。许可证：rasterio BSD-3、pyproj MIT、传递依赖均为 MIT/BSD/Apache-2.0，离线可分发。

### 4.3 PyInstaller（`desktop/app.spec`）

Lead 已把 `rasterio`/`pyproj` 加入第 36 行的 `collect_all(pkg)` 循环（无需我再改）。构建前确认：

- `rasterio` 需要 `rasterio.libs/*.dll`（GDAL 18.2MB、PROJ、GEOS、sqlite/spatialite 等共 51.1MB）；
- `pyproj` 需要 `pyproj.libs/*.dll`（6.7MB）+ `pyproj/proj_dir/share/proj/proj.db`（8.8MB）；
- 代码里 rasterio/pyproj 都是**惰性 import**（`georef.py` 模块级 try/except + 函数内使用），静态分析会漏，`collect_all` 覆盖；
- 安装后体积：rasterio 15.5MB + rasterio.libs 51.1MB + pyproj 17.5MB + pyproj.libs 6.7MB ≈ **90.8MB 解压**；安装包预计 **+60~100MB**（需构建实测，与 onedir 压缩率有关）；
- 若需瘦身：可对 `rasterio.libs` 删掉不用的驱动依赖（hdf5/netcdf/spatialite 等）后再打包，但需回归 GeoTIFF 读写；P0 建议先不裁剪。

---

## 5. 与上层（jobs/vault/pipeline）的接口

1. **解析**：`doc.extra["geo_transform"]` 非空 → `pipeline.has_cleanup_targets(doc)` 为真 → 没有文字实体的纯正射 TIF 也能从 UI/API 导出（Lead 已实现，本解析器负责写入）。
2. **导出**：`doc.extra["geo_params"]` 写入后，`jobs` 在 ai 模式自动 `vault.save_mapping(..., extra={"geo": geo_params})`；human 模式不存（不可逆）。
3. **还原**：拿到 vault 中 `geo` 参数 + 成品 → 用 `georef.local_to_pixel()` 把成品局部坐标还原为原图像素坐标；或用 `crs_before_wkt + src_transform` 回到真实世界坐标。
4. **复检**：解析器导出后先跑 `georef.verify_exported_geo`，再由 `pipeline.verify` 重解析成品（本地 CRS 不计入 `geo_tags`）；任一失败抛 `ExportError`/`VerifyError`，成品删除（jobs 负责）。
5. **ICC**：成品已剥离；解析器不会再在 `doc.meta` 暴露 icc/exif/gps 等真值键，避免 Lead 的 verify 误拦。

---

## 6. 实测数字（2026-10-20，本机 Windows + Python 3.12.9）

| 用例 | 结果 |
|---|---|
| **红线 regression**（`app/tests/regression.py`） | **240 项硬检查全绿**（耗时 94.5s；含 e2e、TIF local-CRS 语义、安全红线、MCP、HTTP、规则、桌面入口） |
| **TIF 域内单测**（`app/tests/test_tiff.py`） | **36/36 通过**（27.5s，0 skipped） |
| **Orientation=6 修复用例（task-40）** | Pillow 路径：parse 页面 60×120（转正）、render PNG 60×120、导出成品 60×120 且 `274 not in tag_v2`、像素与“查看器应看到”逐像素相等；rasterio 路径：明确 warning、页面保持 120×60 原始像素、成品无 274 |
| **PLY 二进制修复（task-40）** | 见 `docs/impl/mesh.md`；`test_mesh.py` 7/7 通过（三种 PLY 子格式数据段等长、顶点重定位/面数据一致、自检拦截损坏） |
| `samples/正射影像.tif`（800×600）e2e | 5/5 通过、残留 0；tif 解析 **1048ms**、检测 2ms、识别 3 处（图幅号/控制点号/坐标系名称）；导出 换名 33774B / 涂黑 26353B |
| 样例中性化后标签 | `[256…339, 34264, 34735, 34737]`；`rasterio` 判定 `LOCAL_CS[...]`、`to_epsg()=None`；无 33550/33922 原始 tiepoint、无 34675/EXIF/GPS/XMP |
| 样例可还原 | 随机像素 `pixel_to_local → local_to_pixel` 往返误差 <1e-6 像素；成品 transform == `geo_params.dst_transform` |
| 400Mpx 大图（20000×20000） | 解析 **4.7s**（`full_read=False`、预览 0.15×）；导出 **7.0s**；输出 **0.8MB**；内部金字塔 5 级；**峰值 1016MB**；中性化复检通过 |
| PIL 对照 | 同一 400Mpx 文件 `PIL.Image.open/load` 抛 `DecompressionBombError` |
| 16bit 3 波段 | 输出 dtype `uint16`、count 3；值域保留（无损窗口拷贝）；LOCAL_CS |
| 4 波段 | 输出 count 4；前 3 波段 RGB、第 4 波段原样 |
| 多帧 2 页 | 解析 Pillow 回退（frames=2）；导出 2 页、0 Geo 标签、warning 明示“未做坐标中性化” |
| ICC 注入 | 输入含 34675 → 输出无 34675 |
| 元数据注入（EXIF/XMP/描述/设备） | 输出全部无 |
| sidecar 注入（9 种） | 导出后全部删除 |
| 纯净 venv 离线安装 | rasterio 1.4.3 + GDAL 3.9.3、pyproj 3.7.2 + PROJ 9.5.1 安装成功 |

### 测试命令

```powershell
# 域内单测（36 项；大图用例想跳过加 $env:YINZHAN_TIFF_SKIP_BIG='1'）
python -X utf8 app\tests\test_tiff.py

# 红线 e2e（必须 5/5、残留 0）
cd app\server
$env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py

# 保留夹具人工查看
$env:YINZHAN_WRITE_FIXTURES='1'; python -X utf8 app\tests\test_tiff.py
```

---

## 7. 验收对照（task-11 / 研究文档 §7）

| 验收项 | 结果 |
|---|---|
| A1 成品无真实地理参考 | ✅ 中性化：LOCAL_CS + EPSG=None；strip：0 Geo 标签 |
| A2 无 EXIF/GPS/ICC/描述 | ✅ 成品 tag 扫描 + rasterio 复检 |
| A3 sidecar 清理（含 .ovr/.kml/.rpc） | ✅ 9 种派生 sidecar 全删 |
| A4 多帧全帧剥离 | ✅ 多帧输出 2 页均无 Geo（注：多帧不做中性化，另有 warning） |
| A5 大文件/位深/波段 | ✅ 4 亿像素可处理；16bit/4 波段保留 |
| A6 内存上限 | ✅ 实测峰值 1016MB ≤ 1.5GB（4 亿像素、单页 uint8） |
| A7 变换参数可还原 | ✅ 参数入 `doc.extra["geo_params"]` → jobs 入 vault `extra["geo"]`；往返误差 <1e-6 像素 |
| A8 导出后复检 | ✅ 解析器侧 `verify_exported_geo` + `pipeline.verify` |
| 既有 e2e 不破 | ✅ 5/5、残留 0 |

---

## 8. 剩余风险与边界（诚实声明）

### 8.1 LOCAL_CS 是已批准 D3 的**预期行为**（不是残留）

按已批准决策 D3（`docs/research/00-…md` §8.2 / 03 §8.2）：

- 中性化成品的 georeference 是 **LOCAL_CS 本地任意直角系**（GeoAsciiParams 含
  `Sanitized_Data local arbitrary`、rasterio `to_epsg()=None`），因此物理上仍带
  `33550/33922` 或 `34264` + `34735/34737` 等 **Geo 标签结构**——这是 GIS 能打开
  成品的必要前提，**不是真实地理参考残留**。
- 判断“中性化干净”的正确口径（解析器/复检/QA 探针统一）：
  1. 成品 CRS 为 LOCAL_CS/ENGCRS 且 `EPSG=None`；
  2. 源文件的真实 CRS/EPSG、源 `ModelTiepoint`（33922）、RPC(50844) 已消失；
  3. 无 ICC/EXIF/GPS/XMP/IPTC/描述/设备等泄漏元数据；
  4. 成品 geotransform == 记录在 vault 的 `geo_params.dst_transform`（可复算）。
- `doc.meta["geo_tags"]` 仍按“**成品里仍存在的真实地理参考**”语义填写，
  中性化成品为 `[]`，本地结构标签记入 `geo_local_tags` / `geo_neutralized=True`。
- 若调用方需要“物理上 0 Geo 标签”的成品（例如只给人看、不再进 GIS），
  用 `geo_mode="strip"`：成品不含任何 Geo 标签，但坐标不可还原（与 D2 的可还原模式二选一）。

### 8.2 其余边界

1. **只降密不匿名**：坐标被随机平移+旋转后，河流/道路/建筑轮廓与纹理仍在，仍可能通过公开影像比对反推方位；产品话术不得写“合规/匿名”。
2. **可还原 = 参数即钥匙**：`geo_params` 存在 vault，参数泄漏即可还原；strip 模式不可还原但也不再是 GIS 可用数据。
3. **多帧 TIFF 只剥离、不中性化**（Pillow 回退路径，显式 warning）——真实倾斜摄影正射通常是单页，多帧多为扫描/堆栈类；如业务需要多帧中性化，P2 再做。
4. **4+ 波段语义未校验**：RGBN 的第 4 波段按原样保留，但“它到底是 NIR 还是 alpha”由源文件决定；换名文本只画在前 3 波段。
5. **非常规色彩空间**（CMYK/YCbCr）预览/OCR 按近似 RGB 处理，成品按原波段保留（warning 提示）。
6. **旋转后的 GIS 兼容性**：成品 geotransform 含旋转分量（GDAL 写 ModelTransformationTag），QGIS/ArcGIS 可打开；个别只支持 north-up 的旧工具可能提示旋转，属预期。
7. **大图内存仍随窗口/波段增长**：400Mpx uint8 单页峰值 ~1GB；更大影像或多波段（如 4 波段 16bit）会线性增加，P1 可再降窗口/流式写 overview。
8. **压缩变化**：源 JPEG 压缩 TIF 会重编码为 DEFLATE（体积可能变大、但无损）；可用 `YINZHAN_TIFF_COMPRESS` 调整。
9. **Pillow 回退无中性化**：未装 rasterio 的机器只能剥离；文档/UI 明确 warning，不静默。
10. **PIL 回退大图上限**：>2×Pillow 上限（约 1.79 亿像素）直接拒绝并提示装 rasterio/切图。
11. **依赖体积**：desktop 安装包 +60~100MB 待构建实测；若打包体积不可接受，可改为“按需安装可选依赖”。
12. **rasterio 路径不转正 Orientation（已知限制 P1）**：GDAL 读到的是原始像素朝向，OCR/框选/成品方向按原像素处理；parse/export 均明确 warning。若源图带 Orientation 且用户要求方向正确，需先转正源图或强制 Pillow 路径（大图时 Pillow 可能超上限，需切图）。

---

## 10. 红线门禁协同事项（已解决：QA 已更新探针，regression 166/166）

**结论：已解决。** `app/tests/regression.py` 现 **166 项硬检查全绿（含 2 条软提示）**，
e2e 5/5、残留 0。QA 已按本方案更新 `qa_common.py` 的 `tiff_geo()` / `regression.py`
的 TIF 判定：新增 `LOCAL_CRS_MARKER = "sanitized_data local arbitrary"`、
`local_crs` 真值、`raw_geo_tags` 供审计，`identity_cleared` 与 “Geo tag=0” 判定改为
**真实参考残留才失败，本地中性化 CRS 允许并记录**。

**口径（Lead 批准，已与 CONTRACT 语义同步）**：
成品为 LOCAL_CS（GeoAsciiParams 含 `Sanitized_Data local arbitrary`、EPSG=None、
源 33550/33922/EPSG 已消失）视为“中性化干净”；若还有源 CRS/RPC/ICC/EXIF 才是残留。

**历史记录（供追溯）**：修复前 regression 154 项中 2 项硬失败，根因是旧探针
`qa_common.py:380 tiff_geo()` 用 tag id 把 LOCAL_CS 的 `34264/34735/34737`
误判成真实地理参考残留（当时输出实测：`34737='Sanitized_Data local arbitrary|'`、
rasterio `LOCAL_CS`/`to_epsg()=None`、源 tiepoint 已消失、ICC/EXIF 均无）。
该问题已由 QA 落 3 行补丁修复，未改动 `qa_common.py`（写域纪律：QA 拥有）。

替代验证手段（不推荐长期使用）：`YINZHAN_TIFF_GEO_MODE=strip` 可让成品物理 0 Geo
标签跑旧回归，但那就不是 D3 批准的“可还原中性化”模式。

---

## 11. 变更记录

| 日期 | 变更 | 负责人 |
|---|---|---|
| 2026-10-20 | 新增 `georef.py`；重写 TIF parse/export（rasterio 大图/位深/中性化/sidecar/ICC）；新增 `test_tiff.py` 33 项；e2e 保持 5/5；QA 更新探针后 regression **166/166 全绿** | researcher-geo |
| 2026-10-20 | **task-40 修复 Orientation 契约缺口**：Pillow 路径 parse/render/export 按 274 转正（兼容新版 Pillow 自动转正，避免二次旋转）；`KEEP_TAGS` 移除 274、导出显式 `pop(274)`；rasterio 路径明确 warning 并保持原始像素朝向；`test_tiff.py` 扩到 36 项；e2e 5/5、regression 240/240 全绿 | researcher-geo |


