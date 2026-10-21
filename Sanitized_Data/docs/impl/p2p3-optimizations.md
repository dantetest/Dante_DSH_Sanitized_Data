# P2/P3 优化实现说明（真实测试集 §7 五项）

> 状态：已实现 · 2026-10-21 · 负责人：Lead（集成）+ 3 个并行子任务（PDF verify / 视觉与任务层 / 本条汇总）
> 上游：真实测试集 QA 报告 `docs/qa/real-testset-v1.1.0.md` §7.1–§7.5
> 回归：`app/tests/run_all.py`（19 套），新增套件 `test_pdf_verify_fast.py` / `test_id_wholepage.py` / `test_warnings_pending.py`
> 版本：v1.1.1（相对 v1.1.0，均为性能/误报/可用性优化，不降低安全红线）

---

## 1. 扫描件 PDF 的 OCR 双跑成本（§7.1）

### 问题
纯扫描 PDF（无文本层）的 parse 已经逐页 OCR；导出后 `pipeline.verify()` 又把成品整册
重新 `parser.parse()` → 再 OCR 一遍。10 页扫描件 verify≈50s，全链路 60–112s。

### 修复
新增 `PdfParser.fast_verify()` 钩子（`app/server/yinzhan/parsers/pdf_parser.py`），
`pipeline.verify()` 在 `doc` 可用时优先调用；**仅当整册页面都是扫描页**才启用，
否则自动回退原完整重解析：

1. 不跑 OCR：打开成品 PDF，只提取文本层、文档元数据、PDF 隐藏数据（附件/批注/表单/JS）。
2. 低 DPI（默认 96，`YINZHAN_PDF_FAST_VERIFY_DPI`）渲染成品页，按导出时**同一套
   `_collect_ops`/`_merge_ops` 矩形**逐项复检：
   - 黑框：框内黑像素（单通道 ≤45）占比必须 ≥98%；
   - 换名替换框：原图墨迹在成品同位置仍为深色的比例必须 ≤80%（即原图确实被白底
     redaction 盖掉、假值写回）。
3. 覆盖证明：parse 阶段 OCR 文本里出现的每个待处理实体值，其字符中心必须落在某个
   导出矩形内；否则说明“检出但没涂到”，**直接回退慢路径**，绝不因为跳过 OCR 而漏检。
4. 文本层残留、元数据、PDF 隐藏数据检查仍走与慢路径完全相同的代码。
5. 开关：`YINZHAN_PDF_FAST_VERIFY=0` 可强制回退慢路径（排障用）。

### 证据
- 合成 2 页扫描件：verify **2 次 OCR / 10 802ms → 0 次 OCR / 348ms**；
  含替换写回路径 0 OCR / 984ms。
- 故障注入（硬拦截）：漏涂黑 → “仍有 1 处扫描页实体区域未涂黑”；替换漏盖 →
  “仍有 1 处扫描页替换区域仍保留原图内容”；文本层 PDF 残留仍被慢路径拦住。
- 新测试 `app/tests/test_pdf_verify_fast.py` 5/5 通过；PDF 相关 regression 检查全 PASS。

---

## 2. warning 重复噪声（§7.2）

### 问题
`照片较大，小脸可能漏检，请人工复核/整图涂黑` 等提示按页重复（清单里合计 64 条），
把真正的错误提示淹没。

### 修复
`app/server/yinzhan/jobs.py` 新增 `_dedupe_warnings()`：

- 只合并**完全相同的字符串**（不同错误/不同导出失败原因绝不合并）；
- 保留首次出现顺序，重复项输出 `原 warning（共 N 次）`；
- 已带计数的条目再次处理保持幂等（不会重复叠加）；
- 接入：`Job.dedupe_warnings()`（ready 前 + finally）、`Job.snapshot()`（只读副本）、
  导出结果 `info["warnings"]` / `record["warnings"]`、未成年严格模式追加提示后。

### 证据
`test_warnings_pending.py` 覆盖去重计数/顺序/幂等/不误合并、导出结果层去重等 9 项全绿。
任务/导出 warnings 不再按页堆叠；`doc.warnings` 原始列表保留（解析器内部排查用）。

---

## 3. 整页证件过判（§7.4）

### 问题
R07/R11 等手机实拍/整页图里 `_detect_id_document()` 的 `id_document` 框面积=1.0，
成品整页涂黑，不可用；而同一页其实已命中身份证字段。

### 修复
`app/server/yinzhan/visual_targets.py`：

- 身份证正面字段命中 ≥4、反面签发机关/有效期命中 ≥2 时，`whole_page` 不再无条件
  整页扩张：
  - 页面长宽比本身≈身份证（紧裁卡面/卡面扫描）→ 仍保留整页兜底；
  - 否则用几何拟合的整卡框（外扩 10%，面积必须 <90% 页面、长宽比合理）；
  - 拟合不出可信整卡框 → 只返回字段级 hits（头像/姓名/号码/住址/签发机关/有效期），
    不返回整页 `id_document`。
- 字段读不出、无法定向、`fallback=1` 分支保持不变（仍整卡/整页，宁可多遮不可漏）。
- 新增常量为可调阈值，便于后续按真实集继续收紧。

### 证据
`test_id_wholepage.py` 8/8：字段命中时无面积≈1.0 的整页框、字段 hits 保留、
卡形页面仍整页兜底、不可信拟合只回字段、读不出字段仍回退整卡/整页。
真实 R07/R11 复测见 `docs/qa/p2p3-fix-verification.md`。

---

## 4. 异常大面积码类框进入人工确认（§7.3）

### 问题
二维码/条码 `/detect` 候选面积 >5% 页面时（R17 实测 0.353），旧逻辑只写 warning 后
丢弃，用户在待确认列表里看不到、也无法处理。

### 修复
`app/server/yinzhan/jobs.py`：

- 该类候选不再丢弃，生成 `status="pending"`、`decision=None` 的实体
  （etype=QRCODE/BARCODE，label 标注“面积异常，待确认”），列表可见；
- 用户 `accept` / `accept-all` 时，若实体有 rect span 且无 decision，补
  `Decision(strategy="black", black=True)`；
- `_export_to()` 只导出 `status!=rejected` **且 `decision is not None`** 的实体，
  未确认项不进入导出、不自动涂黑（`pipeline.export` 自身也有 decision 过滤兜底）。
- 小面积码保持原行为（accepted + black）。

### 证据
`test_warnings_pending.py` 覆盖：大码框 pending 且无 decision、小码不变、accept 补
black、确认前导出不含、确认后 black 进入导出。风险说明：若希望“出现待确认项时阻止
导出/给导出提示”，属于产品策略，需另行确认。

---

## 5. TIF rasterio 大图 Orientation（§7.5）

### 问题
rasterio 路径此前不按 tag 274 转正像素，parse/render/export 都按原始朝向，只发 warning。

### 修复
`app/server/yinzhan/parsers/tiff_parser.py`：

- 支持 Orientation 1–8：解析时按 PIL `exif_transpose` 等价规则转正预览/OCR/扫描图，
  页面尺寸/坐标统一为查看器方向；
- 地理变换 `transform' = src_transform ∘ M`（M 为查看器像素→存储像素仿射），
  `doc.extra["geo_transform"]` 与中性化参数均基于 `transform'`；
- 导出按查看器尺寸建目标 GTiff，逐目标窗口反查源窗口、读源像素→转正→在查看器坐标
  涂黑/换名→写回；保留 dtype/波段/压缩/内部金字塔，成品不写 274；
- 中性化 `LOCAL_CS + dst_transform` 同步旋转/交换宽高，`verify_exported_geo` 复检通过。

### 证据
`test_tiff.py` 39 项全绿；覆盖 2–8 全部方向（parse/render/export 像素与 PIL 一致）、
查看器坐标涂黑、地理参考+Orientation 中性化 transform 复检。详见 `docs/impl/tiff.md`
§2.3 / §3。

---

## 6. 开关与回滚

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `YINZHAN_PDF_FAST_VERIFY` | `1` | `0` 强制扫描件 verify 回退整册重解析（慢但保守） |
| `YINZHAN_PDF_FAST_VERIFY_DPI` | `96` | 快速复检渲染 DPI（72–150） |
| 其余 TIF 参数 | 见 `docs/impl/tiff.md` §2.2 | 不变 |

## 7. 验收与版本

- `app/tests/run_all.py`：**19 套**全绿（515.22s，新增 3 套）。
- `app/tests/regression.py`：**250 项**硬检查全绿（TIF 检查文案/断言随修复更新）。
- 真实测试集抽测（R04 10 页 / R17 17 页，human 全链路）：
  - R04：**85.4s → 34.9s（2.4×）**，verify **42.6s → 4.26s**，0 OCR，residual=0；
  - R17：**112.1s → 61.4s（1.8×）**，verify **~56s → 3.15s**，0 OCR，residual=0；
  - R07/R11 的 `id_document` 面积 1.000 → **0.467 / 0.796**，字段实体不变；
  - R17 面积 0.353 的码类候选进入**待确认**（1 个 pending，decision=None）。
  明细见 `docs/qa/p2p3-fix-verification.md`。
- 版本 v1.1.1；安装包/绿色版/源码包与 SHA-256 见 `docs/release-notes-v1.1.1.md`。
