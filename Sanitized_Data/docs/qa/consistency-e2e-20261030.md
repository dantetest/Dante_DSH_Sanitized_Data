# P1 一致性 + PDF 顺序（阶段 1）落地报告（2026-10-30）

> 范围：P1-1 伪值一致性（身份证年龄/性别、固话区号、USCC 地区码、同值标签归一）；
> P1-2 PDF 阅读顺序阶段 1（替换 op 按视觉顺序插入）；以及复跑中发现的
> **PDF 还原字体缺陷**（回件自带 `china-s` 子集时真值抽取为 `\x00`）修复。
> 上一轮：[upstream-guard-20261029.md](upstream-guard-20261029.md)
> 方案：[07-AI视图上游质量修复方案.md](../plans/07-AI视图上游质量修复方案.md)

## 1. 结论

- D12 假身份证现在**按入院日期 2025-03-17 推导**：出生 1957-11-25、67 岁、女、
  校验位合法；盲读子代理明确确认“年龄/性别一致”。
- D12 同一住院号的两种标签写法（`住院号：` / `住院号 `）**合并为一个实体**，
  AI 视图只出现一个假住院号，不再出现 `WR…`/`HT…` 两个值。
- 固话保留原区号（D08 `028-85033400 → 028-39394186`）；USCC 保留前 8 位
  （D01/D07 地区码不变）。
- D01 AI 视图 raw 文本顺序已与视觉顺序一致（PDF 替换 op 按 y/x 排序）。
- 复跑发现并修复：回件 PDF 自带 `china-s` Type0 子集时，旧还原逻辑插入真值后
  `get_text` 全是 `\x00`（人眼可能看到值，但文本层不可读/不可搜索）；
  修复后 D08 还原稿原值恢复 13/13，文件从 12.5 MB 降到 116 KB。
- 回归 250 项硬检查全绿；新增一致性 18 项、PDF 字体回归 5 项全部通过。

## 2. 代码改动

| 文件 | 改动 |
|---|---|
| `app/server/yinzhan/consistency.py`（新增） | 同值标签归一（`merge_labeled_variants`）、文档事实提取（年龄/性别/事件日期） |
| `app/server/yinzhan/replacers/surrogate.py` | `_fake_id` 使用年龄/性别/事件日期生成一致生日；固话保留区号；USCC 保留前 8 位；`_generate` 给 ID_CARD 传 ctx |
| `app/server/yinzhan/udm.py` | `ReplaceContext.facts` |
| `app/server/yinzhan/pipeline.py` | `Session.facts`；decide 时传入 facts |
| `app/server/yinzhan/jobs.py` | detect 后标签归一 + facts 提取（`id_count` 多证件回退随机）；warning |
| `app/server/yinzhan/parsers/pdf_parser.py` | `_sort_ops_for_text_order`：替换/黑框按页面视觉顺序（y,x）插入 |
| `app/server/yinzhan/restore.py` | `_ensure_restore_font`：回件自带 `china-s` 时注册独立系统 CJK 字体；保存前 `subset_fonts()` 控制体积 |
| `app/tests/test_consistency.py`（新增） | 18 项：事实提取、标签归一、ID 年龄/性别、固话/USCC |
| `app/tests/test_restore_pdf_font.py`（新增） | 5 项：字体冲突时真值可抽取、subset 体积 |
| `app/tests/run_all.py` | 注册 `consistency`、`restorefont` |

安全语义：身份证仍保留原地区码但不复制真实生日；年龄由文档事实推导；
多证件（`id_count>1`）不回退套用文档级年龄；所有一致性只影响假值生成，
不改变替换策略/导出模式。

## 3. 复跑 E2E

环境：`e2e_p1_20261030`，独立数据目录/端口 8763，TestMode；4 份测试集重新脱敏，
4 个新盲读子代理回件，产品 HTTP 还原 API 还原，同一套 compare 脚本。

### 3.1 一致性效果

| 检查项 | 上一轮 | 本轮 |
|---|---|---|
| D12 假身份证生日/性别 | 1995-11-17，与“67 岁/女”矛盾 | 1957-11-25，入院日 67 岁/女，校验位合法 |
| D12 住院号 | `WR…` 与 `HT…` 两个假值 | 只出现 `WR…` 一个（标签变体合并） |
| D08 固话 | 025-…（与成都地址不一致） | 028-39394186（保留原区号） |
| D01/D07 USCC 地区码 | 随机 6 位 | 保留原前 8 位（如 `91540000…`、`91440300…`） |
| D01 PDF raw 文本顺序 | USCC→金额→人名 | 与视觉顺序一致（买方名→卖方名→USCC→金额…） |

### 3.2 还原指标（本轮，最新 D08 回件重跑后）

| 文档 | 映射实体 | 命中实体/处 | 残留假值 | 原值恢复 | 真值覆盖 |
|---|---|---|---|---|---|
| D01 | 9 | 9/9 | **0** | 9/9 | 100% |
| D07 | 16 | 16/27 | **0** | 16/16 | 100% |
| D08 | 14 | 14/20 | **0** | 14/14 | 100% |
| D12 | 7 | 5/6 | **0** | 5/5 | 71.4%* |

\* D12 回件只覆盖 11 个字段，未写主治医师/责任护士，这两个实体在真值里存在但
回件未出现；不是映射丢失。映射的 5 个命中实体全部换回真值，残留 0。

### 3.3 回归中发现并修复的 PDF 字体缺陷

- 现象：D08 回件 PDF 由子代理用 `china-s` Type0 子集生成；还原时
  `insert_text(fontname="china-s")` 复用了这个子集，`get_text` 出来是 `\x00`；
  compare 显示 `orig_recovered=0/13`，但 `residual=0`（假值已被抹掉，真值不可读）。
- 修复：检测页面已有 `china-s` 时，注册独立系统字体 `restorecjk`（msyh/simsun
  候选），保存前 `subset_fonts()` 只嵌入用到的字形。
- 结果：D08 最新回件原值恢复 14/14、真值覆盖 100%，还原稿约 114 KB（修复前 12.5 MB）。
- 新增 `test_restore_pdf_font.py` 锁定该行为。

## 4. 测试

| 项目 | 结果 |
|---|---|
| `test_consistency.py` | 18/18 |
| `test_restore_pdf_font.py` | 5/5 |
| `test_restore.py` | 22/22 |
| `test_upstream_guard.py` | 42/42 |
| `test_ai_consistency.py` | 74/74 |
| `test_ai_view_export.py` | 通过 |
| `regression.py` | 250 项硬检查全绿 |

## 5. 仍未完成/下一轮

1. **P1-2 阶段 2/3**：AI 视图附「阅读顺序文本」sidecar；或重建隐藏文本层。
   本轮只做阶段 1（排序），未改导出交付物形态。
2. **多证件/多年龄文档**：facts 目前只在唯一身份证 + 唯一年龄/性别时启用；
   多 ID 文档走随机回退（安全但不一致），后续可按实体邻域配对。
3. **D12 回件字段覆盖**：盲读任务未要求主治医师/责任护士，导致 truth_cover 71.4%；
   不是产品问题，E2E 任务模板可补字段。
4. **过合并真实判决书源文件**仍缺失，无法重跑；继续依赖单测 + 历史映射扫描。
5. D08 测试集实际是委托代理协议而非判决书（测试集标注问题，已在上轮报告注明）。

## 6. 复现

```powershell
python -X utf8 app/tests/test_consistency.py
python -X utf8 app/tests/test_restore_pdf_font.py
python -X utf8 app/tests/regression.py

# E2E 产物
D:\DSH_DESKTOP_WORKSPACE\e2e_p1_20261030\
  manifest.json / sanitized\ / replies\ / restored\ / report\compare.json
```
