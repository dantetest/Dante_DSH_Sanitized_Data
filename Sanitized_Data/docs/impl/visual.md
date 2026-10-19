# 图像 P0 扫描器实现说明（visual_targets）

> 状态：已实现并接入 mediascan（2026-10-19，与 Lead 的 mediascan 集成同步）
> 代码：`app/server/yinzhan/visual_targets.py`
> 测试：`app/tests/test_visual.py`（35 项，全绿）
> 方案依据：[`docs/research/01-图像实体识别与打码方案.md`](../research/01-图像实体识别与打码方案.md)
> 红线：[`docs/red-lines.md`](../red-lines.md)（不破坏既有功能；不 push）

---

## 1 范围（P0 做了什么）

| kind | 状态 | 说明 |
|---|---|---|
| `face` | ✅ P0 | OpenCV 自带 Haar（frontalface_default + profileface），0 新增模型/包体 |
| `id_document` | ✅ P0 | OCR 文本锚点判定证件类型 → 返回整证件区域；面积 ≥ 页面 25% → 整页 |
| `plate` | ✅ P0 | OCR 文本框 + 中国车牌正则（普通/新能源 8 位/挂学警港澳门后缀）→ 框外扩 8%；局部颜色修正 |
| `road_sign` | ⏸ P1 预留 | 已注册 kind，恒返回空（不产生误报） |
| `door_address` | ⏸ P1 预留 | 已注册 kind，恒返回空（不产生误报） |

不在本模块：印章/签名（`visual.py`，PDF 既有能力）、二维码/条码（`barcodes.py`，条码域）。
本模块只负责「找框」，不画图、不决策、不改文档：命中进 `Document.visual_marks`，
由 `jobs._visual_entities()` 按 `_VISUAL_SPEC` 转成实体并涂黑（P0 决策已批准：默认涂黑不可逆）。

---

## 2 接入方式（已由 Lead 完成，无需再改）

```
registry.load_plugins()
  └─ _SCANNER_MODULES 里 import yinzhan.visual_targets
       └─ 模块底部 mediascan.register_scanner("visual_targets", scan, kinds=KINDS)

image_parser / tiff_parser        pdf_parser
  mediascan.analyze(page_rgb, options={"text_boxes":[{"text","rect"}...], "dpi":...})
  └─ visual_targets.scan(image_rgb, ScanContext) -> list[ScanHit]
  └─ mediascan.to_visual_marks(marks, scale=...) -> doc.visual_marks
  └─ jobs._visual_entities -> Entity(etype, rect span, decision=black)
  └─ pipeline.verify 重解析成品 -> 再扫一次 -> 残留比对（IoU≥0.3 判失败）
```

- **坐标契约**：`ScanHit.rect` 是传入 `image_rgb` 的像素坐标；PDF 侧由解析器
  `to_visual_marks(scale=72/scan_dpi)` 换成 pt。图片/TIFF = 原图像素。
- **OCR 文本框契约**：`ctx.options["text_boxes"]` 中 rect 必须与 `image_rgb` 同坐标系；
  解析器已按此传（image_parser 传原图像素、pdf_parser 按 scan_dpi 放大）。
  可选 `text_boxes_scale` 做整体缩放兜底。
- 重复 import 不会重复注册（`register_scanner` 按 name 幂等替换）。

`jobs._VISUAL_SPEC` 映射（Lead 已落盘）：

| kind | etype | label | 默认 status | level |
|---|---|---|---|---|
| `face` | `FACE` | 人脸 | accepted | L3 |
| `id_document` | `ID_DOCUMENT` | 证件 | accepted | L3 |
| `plate` | `PLATE` | 车牌 | pending | L2 |
| `road_sign` | `ROAD_SIGN` | 路牌 | pending | L2 |
| `door_address` | `DOOR_ADDRESS` | 门牌/门址 | pending | L2 |

---

## 3 实现要点与决策

### 3.1 人脸（face）

| 项 | 取值 | 说明 |
|---|---|---|
| 级联 | `haarcascade_frontalface_default.xml`（第一轮） | 随 cv2 wheel 自带，0 体积 |
| 侧脸 | `haarcascade_profileface.xml`（第二轮，含镜像各一次） | 828KB 也是自带；**只在需要时加载** |
| 检测长边 | **640**（`YINZHAN_SCAN_FACE_MAX_SIDE`，默认 640） | 性能红线：别再用 1600 跑多尺度 |
| 参数 | `scaleFactor=1.1, minNeighbors=5, minSize=24` | 640 尺度下最小可检 ≈24px |
| 预处理 | 灰度 + `equalizeHist` | 逆光/低对比提升 |
| 外扩 | **12%**（决策 D2） | 覆盖发际/下巴 |
| 去重 | 置信度优先，IoU≥0.35 或包含度≥0.85 合并 | 正脸/侧脸/多尺度重复命中 |
| 置信度 | 基础 0.92（正脸）/0.78（侧脸）× 人脸原图像素尺寸系数 | <32px ×0.72、<48px ×0.85、<64px ×0.95；侧脸 ×0.96；clamp 0.35~0.98 |
| 跳过条件 | 图像灰阶 std<2（纯色/黑页）；`auto` 模式下白底文档页不跑侧脸 | 文档页侧脸级联纯浪费 |

性能取舍（已获 Lead 确认）：
- 640 长边意味着 **大图里的小脸会漏**：4000px 照片缩到 640 后，原图小于 ~150px 的脸检测不到；
  1000×620 名片类图片等价于原图 ≥37px 的脸。需要更高召回时用
  `YINZHAN_SCAN_FACE_MAX_SIDE=1600`（代价：单页慢 3~8 倍）。
- `YINZHAN_SCAN_FACE_PROFILE=auto`（默认）：正脸 0 命中且非文档页才跑侧脸；
  合影里「有正脸也有侧脸」时侧脸会漏。要全跑设 `always`。

### 3.2 证件整图（id_document）

判定原则：**宁可漏，不可把合同正文误判成证件整页**（红线条：既有样例不得破坏）。

| 类型 | 强锚点（可单独触发） | 中/弱锚点（辅助+扩区域） | 触发条件 |
|---|---|---|---|
| 居民身份证 | 居民身份证、公民身份号码 | 签发机关、有效期限、住址、出生、民族、性别 | ≥1 强 |
| 护照 | 中华人民共和国护照、护照号码、MRZ（`P<CHN…` / `[A-Z0-9<]{30,44}`） | 签发日期、出生日期、签发机关 | ≥1 强（MRZ 算强） |
| 驾驶证 | 机动车驾驶证、准驾车型 | 证号、有效期限、初次领证日期 | ≥1 强 |
| 营业执照 | 营业执照编号；标题「营业执照」**且** ≥1 其它字段 | 统一社会信用代码、注册资本、法定代表人、经营范围、成立日期、营业期限、名称、住所 | 短标题（≤14 字）+ ≥1 其它锚点 |
| 社保卡/医保卡 | 社会保障卡、医保卡、社会保障号码 | 卡号、有效期 | ≥1 强 |

区域算法：命中锚点并集 → 按宽 30%/高 35% 扩张并入邻近字段框 → 外扩 2.5% 短边 →
**面积 ≥ 页面 25% 时返回整页**（决策 D4，已获用户批准）。
"营业执照"必须以**短文本框**（标题）出现：正文句子「请提供营业执照复印件…」不触发
（有测试用例覆盖）；合同头部的「统一社会信用代码」单独不触发。

已知漏报（P0 接受，P1 补）：
- 证件照片文字太糊/反光 → OCR 无锚点 → 不命中（整图仍可人工框选）；
- 护照 MRZ 是 OCR-B 小字，低分辨率下 OCR 读不出 → 靠「护照号码/国名」锚点兜底；
- 银行卡/居住证/港澳通行证/军官证等不在 P0 锚点表；银行卡号仍由既有 `cn.bankcard` 规则处理。

### 3.3 车牌（plate）

- 主路径：OCR 文本框（长度 ≤12，NFKC + 去 `· . -` 空格 + 大写 + `I→1/O→0` 纠错）→
  中国车牌正则 → 返回 OCR 框外扩 8%，置信度 0.95。
- 正则覆盖：普通 7 位、新能源 8 位、`挂/学/警/港/澳` 后缀 7 位、警车 8 位变体；
  中英分隔符/大小写/全角/常见 OCR 混淆都做了归一。
- 颜色修正：**只在已读出车牌文字的框附近局部窗口**（宽 ×0.8、高 ×1.2）做蓝/绿底色连通域，
  用物理车牌底色框收紧 OCR 框；不做全图 HSV 扫描（12MP 全图要几十 ms，且蓝底路牌
  会被算进来）。没有可读车牌文字时**不猜测**（宁漏不误报）。
- 黄牌（黑字黄底）、脏污/夜间/倾斜读不出文字 → P0 漏报，P2 检测模型再补。

### 3.4 路牌 / 门址（P1 预留）

当前实现恒返回 `[]`，只注册 kind 以便 `_VISUAL_SPEC`/UI/开关就绪。
P1 计划（见方案文档 §3.3/§3.4）：路名模式 + 色块整块涂黑；无行政区"XX路XX号"正则
+ 跨行合并。**P0 不产生任何 road_sign/door_address 命中，因此不会引入误报。**

---

## 4 性能（实测，2026-10-19 本机）

> 本机：AMD Ryzen 5 4500U（6C/6T）、Python 3.12.9、cv2 4.13、单线程 CPU。
> 并行开发时机器有负载，数字有 ±30% 波动。

| 场景 | 耗时 | 备注 |
|---|---|---|
| **名片 1000×620（目标 ≤250ms）** | **首次 ~30–45ms，热态 ~19–31ms** | 白底文档页 → 侧脸级联不加载；达标 |
| 合同页渲染 200dpi 1653×2339 | 首次 ~45–54ms | 纯文本页 Lead 已跳过；扫描页才跑 |
| 正射影像 800×600 | ~109–140ms | 非文档页 → 正脸 + 侧脸（两遍） |
| 最坏情况 1600×1200 随机噪声 | ~130–450ms | 纹理噪声会拖慢 Haar；真实照片/影像没有这么糟 |
| import 预热（一次性） | ~0.4–1.2s（受机器负载影响） | 放在 `registry.load_plugins()`，不计入单文件扫描 |

**关键优化（对 e2e 的影响）**：
1. 人脸长边 1600 → **640**，多尺度循环成本约降 6 倍；
2. 侧脸级联 **按需加载**：白底文档页不加载 828KB XML、不跑两遍镜像检测；
3. 车牌 **不做全图颜色扫描**，只在 OCR 框附近局部修正；无文字直接跳过；
4. Haar 首次 `detectMultiScale` 有 300–500ms 的一次性并行池初始化 →
   在模块 import 时用 96×128 灰图**预热**（`YINZHAN_SCAN_PRELOAD=1` 默认开），
   把成本从「每个文件解析」挪到「服务启动一次」。

对比优化前（Lead 实测）：名片解析 2.1s→3.8s、TIF 0.78s→2.25s；
优化后按上表估算：名片扫描 ≤45ms、TIF ≤140ms、扫描 PDF 页 ≤55ms。

---

## 5 开关与环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `YINZHAN_SCAN` | 1 | mediascan 总开关（Lead 的） |
| `YINZHAN_SCAN_VISUAL` | 1 | 视觉类扫描器开关（mediascan 按 kind 判断） |
| `YINZHAN_SCAN_DISABLE` | 空 | 逗号分隔禁用：`face,plate,id_document,road_sign,door_address` |
| `YINZHAN_SCAN_FACE` | 1 | 人脸开关 |
| `YINZHAN_SCAN_FACE_MAX_SIDE` | **640** | 人脸检测长边上限（召回/性能旋钮，可设 1600） |
| `YINZHAN_SCAN_FACE_PROFILE` | **auto** | 侧脸策略：`auto`（正脸 0 且非文档页）/`always`/`0` |
| `YINZHAN_SCAN_ID_DOC` | 1 | 证件锚点开关 |
| `YINZHAN_SCAN_PLATE` | 1 | 车牌开关 |
| `YINZHAN_SCAN_PLATE_COLOR` | 1 | 车牌局部颜色修正确开关 |
| `YINZHAN_SCAN_PRELOAD` | 1 | import 时预热 Haar（一次性 ~0.4–1.2s） |
| `YINZHAN_SCAN_MAX_SIDE` | 3000 | 通用长边上限（与人脸 cap 取小） |

**没有**运行时下载、写盘、网络请求；模型全部来自 `cv2.data.haarcascades`（随包，Apache-2.0）。

---

## 6 测试

### 6.1 命令

```powershell
# 仓库根目录
python -X utf8 app/tests/test_visual.py

# 或 app/server 下
cd app\server
python -X utf8 ..\tests\test_visual.py

# 想把合成夹具落到 app/tests/fixtures/visual/ 人工看一眼（默认写临时目录，不入库）
$env:YINZHAN_WRITE_FIXTURES='1'; python -X utf8 app/tests/test_visual.py
```

35 项测试覆盖：注册、人脸缩放下采样/外扩 12%/去重/置信度/侧脸按需、证件 5 类锚点与
整页阈值、合同页/正文提及营业执照/路牌**不误报**、车牌普通/新能源/OCR 噪声/后缀变体、
真实 `samples/名片.png`（真 OCR 文本框）与 `samples/正射影像.tif` 零误报、畸形输入、
确定性、耗时上限。

当前结果：**全部通过（35/35）**；`名片` 扫描首次 ~22ms / 热态 ~19ms。

### 6.2 真实测试集（待用户提供）

人脸正样本：仓库内没有可再分发的真人照片，P0 用「注入假级联」验证几何/去重/置信度逻辑
（`_FACE_CASCADES_OVERRIDE`，只建议测试用）。**真实照片的召回/误报尚未实测**，
需要用户提供/授权一批照片（正脸/侧脸/小脸/遮挡/合影/逆光 + 反例），按方案文档 §6.2 的
指标验收（正脸 ≥48px 召回 ≥90% 等）。

---

## 7 已知限制与风险

| 限制 | 影响 | 缓解 |
|---|---|---|
| 640 长边检测 | 大图小脸漏（4000px 照片 <~150px 的脸） | 调高 `FACE_MAX_SIDE`；P1 分块检测 |
| 侧脸 auto 策略 | 合影里与正脸并存的侧脸漏 | `FACE_PROFILE=always`；P1 YuNet |
| 无 OCR 文本 = 无证件/车牌 | 反光/糊/低分辨率证件照、脏污车牌不命中 | 框选补漏（既有）；P2 MRZ/车牌检测模型 |
| 证件锚点误判 | 少见排版可能把证件名当正文 | 已用短标题+多锚点约束；测试覆盖合同页/正文提及；误判仍可 reject（pending 由 UI 确认） |
| 整页 25% 阈值 | 整页证件照直接整页黑 | 已获用户批准（决策 D4）；human 模式不可逆，导出前 UI 可对照 |
| 未做真实集标定 | 召回/误报指标未知 | 用户提供测试集后按 §6 标定；调 `FACE_MAX_SIDE`/锚点阈值 |
| verify 复检 | 只验证"已检出的框被涂掉"，不证明"没漏检" | 测试集指标 + 人工框选补漏；产品文案不承诺 100% 检出 |

失败降级：`cv2` 缺失时人脸/车牌颜色路径跳过（文本类路径仍工作），异常只写
`ctx.warnings`，不阻断解析（mediascan 统一兜底）。

---

## 8 后续（P1/P2，需要 Lead 排期）

1. P1：`road_sign`（路名模式 + 色块整块涂黑）、`door_address`（无行政区门牌 + 跨行合并）；
2. P1：人脸分块检测/更高 cap 的可选模式，写真集召回；
3. P2：YuNet（227KB，MIT）随包提升侧脸/小脸；护照 MRZ 解析；车牌检测小模型；
4. 集成侧待办（**非本模块**）：UI 类型表补 FACE/ID_DOCUMENT/PLATE 等（`app/web/app.js`）；
   README 支持表补充"照片目标识别"；`CONTRACT.md` 补 origin=visual/verify 说明。
