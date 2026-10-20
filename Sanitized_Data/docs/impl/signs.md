# 门牌 / 路牌 / 店招 / 单位铭牌扫描器（sign_targets，P1-A）

> **状态：P1-A 实现稿 · 2026-10-20**
> 新增文件：`app/server/yinzhan/sign_targets.py`、`app/tests/test_sign_targets.py`、
> `app/tests/fixtures/signs/`、本文件。**未改** visual_targets / jobs / pipeline / registry
> （注册、jobs 映射、UI type 由 Lead 接入，见 §4）。

---

## 1. 范围与承诺

| 项 | 内容 |
|---|---|
| 目标 | OCR 可读的门牌、路牌、店招、单位铭牌，返回**打码框**（不给识别值） |
| kind | `door_address` / `road_sign` / `shop_sign` / `org_plaque` |
| 输入 | `mediascan` 扫描器接口：`fn(image_rgb, ctx) -> list[ScanHit]`；文本来自 `ctx.options["text_boxes"]`（解析器已把 OCR 框换算到同图坐标） |
| 不承诺 | 无 OCR 文本框（纯图像/未开 OCR）→ 直接返回空；手写、艺术字、竖排、强反光/低分辨率照片；近白纸面（见 §3 纸面门控） |
| 输出 | `ScanHit(kind, rect, confidence, note)`；`rect` 为传入图像像素坐标，外扩 6px 并 clamp 到页面内；`note` 只含 `engine=ocr;kind=...;reason=...;anchors=N`，**绝不写识别值** |

注册（import 即注册；Lead 在 `registry._SCANNER_MODULES` 加 `"yinzhan.sign_targets"`）：

```python
mediascan.register_scanner("sign_targets", scan,
                           kinds=("road_sign", "door_address", "shop_sign", "org_plaque"),
                           priority=55)
```

## 2. 算法

1. **文本输入**：`iter_text_boxes(ctx)` 兼容 `[{"text","rect"}]`、`[(text, rect)]`、
   带属性对象；支持 `text_boxes_scale` 与页面边界 clamp；空 → 返回 `[]`。
2. **纸面门控（防名片/发票/合同误报）**：整页近白低饱和像素占比 ≥55% → 整页跳过
   （`cv2` HSV：V≥228 且 S≤42；无 cv2 时用 numpy 近似）。名片/发票/合同正文/文档
   扫描页都在这一步被排除；可通过 `YINZHAN_SIGN_PAPER_GATE=0` 关闭（调试用）。
3. **文本门槛**（同时满足）：
   - 归一化后长度 ≤36；不含句读/冒号（`。；;，,、！!？?：:`）；非「地址/住址/名称/甲方/
     乙方/发票/金额/电话…」等字段标签；
   - 字高 ≥ max(18px, 4% 页高)；框宽 ≥ max(24px, 2% 页宽)；字高 ≤45% 页高；
   - 页面文本框 ≥9（密集文档页）时：字高 ≥ max(24px, 6% 页高) 且 ≥1.25×同页中位字高；
     4–8 个文本框时 ≥1.15×中位字高（排除名片/正文行）。
4. **锚点分类**（`_classify`，优先级从高到低）：

| kind | 规则/关键词 | reason 示例 |
|---|---|---|
| `door_address` | 门牌/门址/楼栋；`数字+（号/栋/幢/单元/室/楼/层/座/院/弄/区/排/组）`；路名（路/街/大街/大道/巷/弄/道）+ 数字 | `unit_room` / `addr_no` / `house_no` |
| `road_sign` | 高速/立交/桥/环线/入口/出口（含 G15 类编号）；路名 + 方向词/公里/km；独立短路名（≤16 字） | `road_direction` / `road_distance` / `road_name` |
| `shop_sign` | 便利店/超市/药房/药店/餐厅/咖啡/酒店/网吧/美发/理发/洗衣/维修/门诊/诊所/商场/市场/银行/营业/招牌/便民/店/馆；含法律实体后缀时改归 org | `shop_term` / `shop_hours` |
| `org_plaque` | 有限公司/有限责任公司/股份有限公司/集团/公司/单位/委员会/学校/学院/医院/事务所/研究院/研究所/设计院/工作室/协会/基金会/中心/局/厂/站 | `org_legal` / `org_unit` |

5. **组合/外扩/去重**：把与命中框相邻（中心落在 1.8×字高 / 1.0×字高 扩框内）、
   字高 0.4–2.0×、文本短无句读的邻框并入（如「示例大道」+「东行 2 km」、
   「示例便利店」+「24小时营业」、「路名」+「88号」）；union 后 **外扩 6px**；
   同 kind 重叠（IoU≥0.5 或包含 ≥0.75）合并；不同 kind 高度重叠保留更具体/高置信度
   一个（door > road > shop > org）；整页框（>60% 页面积）丢弃。
6. **置信度**：强锚点 0.86 / 弱锚点 0.76；合并 ≥2 框 +0.04，door 吸收相邻 road +0.03，
   上限 0.95。
7. **调试**：`YINZHAN_SIGN_DEBUG=1` 时向 `ctx.warnings` 写「尺寸/文本框数/耗时/命中数
   （不含识别值）」，默认不产生 warning，避免影响既有告警基线。

## 3. 已知限制

- **白底招牌会被纸面门控跳过**（如白纸打印的招聘牌、白墙上的白色门牌特写）；
  P1 选择「宁可不报，不误报」，后续可按版面/边缘特征细化。
- 竖排招牌、手写/艺术字、远距离小字、强反光/遮挡不承诺；OCR 拆字后无法形成锚点则漏。
- 同一画面里紧邻的两块不同 kind 招牌，可能被合并为高优先级一个（door>road>shop>org）。
- 与 `visual_targets` 的 `road_sign`/`door_address` 占位实现并存：**集成时需二选一**
  （建议 Lead 删除 visual_targets 里恒返回空的 `_detect_road_sign/_detect_door_address`
  对应 kind/调用，只保留 sign_targets，避免同 kind 重复注册与 UI 重复条目）。

## 4. Lead 接的点

1. `app/server/yinzhan/registry.py`：`_SCANNER_MODULES += ("yinzhan.sign_targets",)`。
2. `app/server/yinzhan/jobs.py`：
   - `_VISUAL_SPEC` 增加 `"shop_sign": ("SHOP_SIGN", "店招/门头", "pending", "L2")`、
     `"org_plaque": ("ORG_PLAQUE", "单位铭牌", "pending", "L2")`（或合并到 ORG，按产品口径）；
   - `_TYPE_LABEL` 同步加中文名；`road_sign`/`door_address` 已有映射。
3. 前端类型标签/颜色：按新增 etype 展示（可沿用 pending 样式）。
4. `verify` 的视觉残留：`_RECT_ETYPE_KIND` 增加 SHOP_SIGN→shop_sign、ORG_PLAQUE→org_plaque
   （与 Lead 既有 QRCODE/BARCODE/FACE…一致）。
5. 可选：删除 visual_targets 中 P1 占位的 `road_sign`/`door_address` 空实现（见 §3）。

## 5. 测试与实测

```powershell
# 自测（真实 OCR + mediascan.analyze；约 10s，OCR 首次加载另计）
python -X utf8 app/tests/test_sign_targets.py
# 结果：PASS 39 / FAIL 0 / SKIP 0
```

覆盖：注册/无 OCR 返回空；4 类正样本（PIL 合成图 → 真实 OCR → mediascan.analyze，
断言 kind、牌面 ROI 重叠、note 格式且不含识别值）；负样本 0 命中（`samples/名片.png`、
合成发票、合成合同正文、合成名片、风景、`samples/委托代理合同.pdf` 文本层页）；
单元防护（“地址：”标签、正文小字、白纸页门控、重复框去重、路名+方向合并、G15 高速→road_sign）；
规则扫描 <50ms/页（实测约 1.2ms，OCR 才是主成本）。

**误报实测（真实材料，只读）**：`%TEMP%\pipl_public_testset` 28 张公开图片、
用户测试集 7 张图片、`samples/正射影像.tif` → 招牌类命中 **0**（无新增误报）。

## 6. 后续（P2）

- 按真实街景/门头照片采样调参（当前仅合成正样本 + 合成/真实负样本）；
- 白底招牌：加边缘/版式特征后再放开纸面门控；
- 与 OCR 文本框的视觉排版（字号一致性、行距）结合，降低相邻不同招牌合并误判。
