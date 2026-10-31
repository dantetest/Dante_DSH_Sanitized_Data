# 08 · PDF 栈自研替代生产化路径（去 PyMuPDF）

> 日期：2026-10-31
> 目标：不依赖 Artifex 商业许可，移除 PyMuPDF，改用可闭源商用的 PDF 栈。
> 人员假设：1 名熟悉本项目的工程师全职投入。
> 结论先行：**不是在 PDF 解析器上从零造轮子，而是自研“PDF 引擎适配层 + 写入层”，组合 pdf_oxide / open-redact-pdf / pypdfium2 / pypdf(pikepdf)。**

---

## 1. 先定义“全面替代”

满足以下全部条件才算完成：

1. 运行时不 `import fitz`，Windows / macOS 安装包不再携带 PyMuPDF；
2. 现有 PDF 功能全部对齐：
   - 文本层解析、坐标、旋转页；
   - 扫描件 OCR；
   - 人类版真删除涂黑；
   - AI 换名版 PDF 文本层；
   - 隐藏数据 / JS / 附件 / 批注 / 表单 / 元数据；
   - 加密 PDF；
   - 替换文字/字体、回件还原；
   - 页数配额、预览、独立复检；
3. 现有回归测试 + 新增 50–100 份真实语料通过；
4. 许可证链干净：无 AGPL，NOTICE / SBOM / wheel 打包完整；
5. 有 feature flag、shadow、灰度、回滚和 fail-closed 机制。

---

## 2. 现实判断

### 2.1 ZY_PDF 是“自研整合”，不是“自研 PDF 引擎”

当前方案由这些许可友好的库组成：

| 层 | 库 | 许可 | 状态 |
|---|---|---|---|
| 主解析/涂黑 | pdf_oxide 0.3.78 beta | MIT / Apache-2.0 | beta，API 有限 |
| 第二引擎 | open-redact-pdf 0.6 | Apache-2.0 / MIT | MVP |
| 渲染/复检 | pypdfium2 | Apache-2.0 / BSD-3 | 稳定 |
| OCR | RapidOCR | Apache-2.0 | 已有 |

**优点**：许可链干净，POC 已验证 9/9 真实文档 residual=0。  
**缺口**：pdf_oxide 目前只覆盖“读 + 涂黑 + 保存”，没有产品需要的：

- 插入文字/图片/字体；
- 表单、批注、附件、JS、隐藏对象操作；
- 加密 PDF；
- 精确的旋转坐标和排版；
- AI 版 PDF 文本层；
- 回件还原写回。

所以必须新增一个**自研写入层**，这是整个计划里工作量最大、风险最高的部分。

### 2.2 如果“自研”指从零写 PDF 解析器

不推荐。PDF 规范、字体、内容流、加密、对象流、XFA、线性化等，单人需要 12–24 人月，且可靠性无法保证。  
本计划采用行业通行做法：**permissive 开源引擎 + 自研编排/写入/验证层**。

---

## 3. 目标架构

```text
app/server/yinzhan/pdf/
  api.py              PdfEngine 门面；feature flag
  model.py            PdfDoc/Page/Rect/TextSpan/Op 统一模型
  backend_oxide.py    pdf_oxide：文本提取、人类版 redaction
  backend_pdfium.py   pypdfium2：渲染、复检、独立文本读取
  backend_write.py    自研写入层：pypdf/pikepdf + 内容流
  fonts.py            CJK 字体、度量、ToUnicode、子集化
  hidden.py           JS/附件/批注/表单/元数据/缩略图清理
  secure.py           加密 PDF 认证/保存
  layout.py           旋转、页面坐标、内容流坐标
  verify.py           residual + 可打开性 + 像素/OCR 复核
  adapters/
    legacy_pymupdf.py 现有实现（迁移期）
    zy.py             新实现
```

产品通过统一接口调用：

```python
engine = get_pdf_engine()  # legacy | zy | shadow
doc = engine.open(path)
doc.extract()
doc.redact(ops)
doc.write(hidden_policy=...)
engine.verify(...)
```

环境变量/构建开关：

```text
YINZHAN_PDF_ENGINE=legacy | zy | shadow
```

- `legacy`：当前 PyMuPDF
- `zy`：新引擎
- `shadow`：双跑，不改变输出，只记录差异

---

## 4. 分阶段 WBS

### P0 · 基线与技术验证（2–3 人日）

- 建立 PDF 能力矩阵：当前 `fitz` 每个调用点 → 新引擎 API
- 固化 50–100 份真实语料（脱敏/内部）
- 定义验收指标、基准性能、输出样例
- 确认许可：pdf_oxide、open-redact-pdf、pypdfium2、pypdf/pikepdf、字体 OFL

**交付**：`docs/qa/pdf-parity-matrix.md` + 语料清单 + 基线报告。

### A · 引擎抽象层（3–4 人日）

- `PdfEngine` / `PdfDoc` / `Page` / `TextSpan` / `RedactOp`
- `legacy_pymupdf` 适配器，保证现有行为不变
- 单元测试：同一输入两后端模型字段一致
- 构建开关、依赖注入

**交付**：抽象层 + legacy 适配器 + 20 个契约测试。

### B · 读取、渲染、坐标（4–6 人日）

- pdf_oxide 文本/行/坐标提取
- pypdfium2 渲染、页面尺寸、旋转
- 统一坐标：PDF 空间、页面空间、图像空间
- 扫描页识别、页数、元数据

**交付**：文本层/扫描件/旋转页的页面模型；与 legacy 对拍 20 份。

### C · 人类版真删除（3–5 人日）

- pdf_oxide `add_redaction` + `apply_redactions_destructive`
- 多行/跨行/全角/空白匹配
- 涂黑区域合并、重叠、部分覆盖
- 输出后独立复检：pypdfium2 文本 + 重新 OCR

**交付**：文本层 20 份 + 扫描件 10 份 residual=0。

### D · 自研写入层（7–10 人日，最高风险）

这是决定项目成败的模块。

- 用 pypdf 或 pikepdf 读写 PDF 对象
- 用内容流生成替换文字/图片：
  - `BT/ET/Tf/Td/Tj/TJ` 操作符
  - 字体资源、编码、宽度、ToUnicode
- CJK 字体：Noto Sans CJK / Source Han Sans（OFL）子集化
- 文本替换、AI 版文本层、回件还原写回
- 页旋转下的变换矩阵

**决策门 D：3 人日 spike**
- 在一份中文文本层 PDF 上成功插入替换文字，且可被 PDFium/Acrobat 打开；
- 如果 spike 失败，暂停并重新评估“买许可 or 图像化输出”，不继续硬写。

### E · 隐藏数据/表单/加密（6–9 人日）

- JS、OpenAction、附件、批注、表单字段、缩略图、元数据
- 表单域与 XFA 的边界（先做 AcroForm，XFA 明确降级）
- 加密 PDF：user/owner 密码认证、解密、输出保存策略
- 与现有 `_strip_pdf_hidden` / `_pdf_hidden_stats` 行为对齐

**交付**：hidden/encrypted 夹具全部通过；残留检查脚本。

### F · 回件还原/预览/配额（3–5 人日）

- PDF 预览图、页数、文本抽取
- 回件还原：span 定位、字体、颜色、宽度
- 配额页数统计
- 与现有 `restore.py` / `quota.py` 对接

### G · 产品接入与迁移（4–6 人日）

- `pdf_parser.py` 改为调用 `PdfEngine`
- `restore.py`、`quota.py`、MCP、导出路径全部接入
- 双跑 shadow：记录 legacy vs zy 差异
- 失败关闭：复检不通过 → 阻止导出

### H · 语料回归与可靠性（7–10 人日）

- 50–100 份真实样本，按类别分层：
  - 文本层：中文/英文/混排/多栏/表格
  - 扫描件：1/12/42 页、低质量、旋转
  - 表单/批注/附件/JS/隐藏
  - 加密 user/owner
  - Office 转 PDF
  - 大文件/性能
  - 截断/损坏/零页
- 每类 residual=0；输出可打开；页数/旋转/隐藏数据对齐
- 性能：不慢于基线 1.5–2 倍
- 影子运行、差异归零

### I · 打包与许可（3–5 人日）

- requirements / app.spec：移除 PyMuPDF，加入新依赖
- Windows / macOS wheel、离线安装
- 字体打包、NOTICE、SBOM、第三方许可清单
- 安装包体积/首启性能
- 源码扫描：仓库无 `fitz` 运行依赖（测试夹具可保留，单独 dev 依赖）

### 工作量合计

| 阶段 | 人日 |
|---|---:|
| P0 | 2–3 |
| A | 3–4 |
| B | 4–6 |
| C | 3–5 |
| D | 7–10 |
| E | 6–9 |
| F | 3–5 |
| G | 4–6 |
| H | 7–10 |
| I | 3–5 |
| **合计** | **42–63 人日** |
| 加 10–15% 缓冲 | **46–72 人日** |

单人日历：**9–14 周**；考虑线上支持/返工，现实 **3–4 个月**。

---

## 5. 关键路径与周排期

| 周 | 工作 | 里程碑 |
|---|---|---|
| 1 | P0 + A | 能力矩阵、抽象层、legacy 适配 |
| 2 | B + D spike | 读取/渲染；写入层 go/no-go |
| 3–4 | C + D | 文本层涂黑；字体/内容流插入 |
| 5–6 | D + E | AI 文本层、隐藏数据、表单、加密 |
| 7 | F + G | 回件、配额、产品接入、shadow |
| 8–9 | H | 50–100 份回归、差异收敛 |
| 10 | I | 打包、许可、SBOM |
| 11–13 | 灰度/可靠性 | 10% → 50% → 100% |

**第 2 周末是硬决策点**：写入层 spike 不过，停止自研切换，改买许可或接受图像化降级。

---

## 6. 可靠性计划

### 6.1 验收门槛

| Gate | 条件 | 放行 |
|---|---|---|
| G0 | POC 9/9 | 已完成 |
| G1 | 契约测试 + 20 份样本 | 开发自测 |
| G2 | 50 份真实文档 residual=0 | 内部可用 |
| G3 | 100 份 + shadow 差异 <1% | 小范围付费 |
| G4 | 300 份 + 2 周灰度无 P1 | 默认切换 |
| G5 | 4 周线上稳定 | 完全移除 PyMuPDF |

> 统计口径：0/50 失败，95% 置信上限约 6%；0/100 约 3%；0/300 约 1%。隐私产品应至少到 100–300 份再谈“可靠”。

### 6.2 可靠性机制

1. **独立复检**：输出必须用 pypdfium2 文本层 + 重新 OCR 验证 residual；不能只看输入/输出 diff。
2. **fail-closed**：复检不通过、页面打不开、页数/旋转不一致 → 阻止导出，不静默放行。
3. **shadow**：双引擎跑，只记录差异，不用新引擎输出；差异逐条归零。
4. **canary**：先 10% 用户/任务，再 50%，再全量；保留回滚到上一版。
5. **版本锁定**：pdf_oxide/open-redact-pdf 锁定精确版本，wheel 入离线包。
6. **性能基线**：文本提取、OCR、涂黑、导出四项，不得超过基线 2 倍；大文件内存峰值设上限。
7. **故障演练**：损坏 PDF、加密 PDF、零页、超大页、字体缺失、写入中断。

### 6.3 已知可靠性薄弱点

- pdf_oxide beta：API/行为可能变；
- open-redact-pdf MVP：只做交叉验证，不做唯一主引擎；
- 旋转页向量坐标：必须专项验证，不能沿用 POC 的栅格化；
- 字体/内容流：最容易出现“能打开但显示异常”；
- XFA 动态表单：先明确不支持，降级提示；
- 加密 PDF：认证失败/保存策略必须明确。

---

## 7. 迁移策略

1. **不直接改生产**：先加抽象层，legacy 行为不变；
2. **shadow 双跑**：新引擎输出只落日志，不交付；
3. **按格式分批**：先文本层，再扫描件，再复杂表单/加密；
4. **灰度开关**：`PDF_ENGINE=legacy|zy`，按用户/任务比例；
5. **回滚**：上一版安装包保留 legacy；出问题切回旧版本；
6. **最终剥离**：G5 后删除 `fitz` 依赖，源码扫描 + 打包验证无 PyMuPDF。

---

## 8. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| 写入层做不出来 | 中 | 高 | 第 2 周 spike；失败则买许可/图像化降级 |
| 旋转/表单/加密边缘失败 | 高 | 高 | 专项语料 + fail-closed |
| pdf_oxide beta 变更 | 中 | 中 | 锁版本、vendor wheel、适配层 |
| 字体嵌入显示异常 | 中 | 高 | Noto CJK 子集 + Acrobat/PDFium/macOS Preview 三端验收 |
| 回归语料不足 | 高 | 高 | 先收集 100 份再开工大规模替换 |
| 性能下降 | 中 | 中 | 基准 + 缓存 + 分页 |
| 许可链/打包问题 | 中 | 中 | SBOM + 法务/许可清单 + 离线 wheel |
| 单人排期被打断 | 高 | 中 | 3–4 个月缓冲，不压首单 |

---

## 9. 决策点

### 立即决策（本周）

1. 是否接受“permissive 库 + 自研写入层”的路线；
2. 是否接受 3–4 个月、不阻塞首单；
3. 是否先投入 3 人日做写入层 spike（推荐）。

### 第 2 周末（go/no-go）

- 写入层 spike 成功 → 继续；
- 失败 → 回到“买许可”或“人类版图像化 + AI 版文本化”的降级产品决策。

### 第 8 周（内部可用）

- 50 份 residual=0 → 继续；
- 出现系统性残留 → 停止扩大范围，先修。

### 第 12 周（默认切换）

- 100 份 + shadow + 灰度通过 → 默认；
- 否则保持 legacy，不强行切。

---

## 10. 本周可以立即开工的 5 件事

1. 建 `docs/qa/pdf-parity-matrix.md`：把 `fitz` 每个调用点映射到新引擎；
2. 收集 50–100 份真实 PDF（脱敏），分 8 类；
3. 在 `pdf/` 下建抽象层和 `legacy_pymupdf` 适配器；
4. 做 3 人日 spike：pypdf/pikepdf + Noto CJK 往一页 PDF 插中文，PDFium/Acrobat 能打开；
5. 把 `PYTHON` 测试和打包依赖切到可替换：requirements/app.spec 不再硬编码 PyMuPDF。

---

## 11. 一句话

**可行，但不是“把 ZY_PDF 搬进产品”这么简单。**
真正的工作量在**自研写入层 + 隐藏数据/表单/加密对齐 + 100 份真实语料**。
**1 人可以做到：3–4 个月、45–65 人日；先做 3 人日写入层 spike 再决定是否全押。**
