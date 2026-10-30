# 上游质量研究脚本（2026-10-29）

这些脚本只做**只读分析/原型**，不改产品数据；输出用于
[`docs/plans/07-AI视图上游质量修复方案.md`](../../plans/07-AI视图上游质量修复方案.md)。

运行环境：Windows + 本机 Python（与产品同一环境）。
脚本里的路径按 `D:\DSH_DESKTOP_WORKSPACE` 编写，换机器需改 `BASE`。

| 脚本 | 用途 | 运行方式 |
|---|---|---|
| `repro_entities.py` | 复现 D07/D12 实体来源（origin/confidence）与归并组；会创建 `.tmp_research` 临时数据目录 | `python -X utf8 repro_entities.py`（D07 扫描件 OCR 约 40s） |
| `research_analyze.py` | 映射库全量统计：类型分布、同假值多原值、脏实体启发式、ID/电话/USCC 统计、`_fake_id` 分布、一致性原型 | `$env:YINZHAN_DATA_DIR='...\Sanitized_Data\data'; python -X utf8 research_analyze.py` |
| `research_overmerge.py` | 归并异质组/大组统计与样例 | 同上（真实数据目录） |
| `research_more2.py` | D07 归并组真值对照 + D01 AI 视图 raw/sort 文本顺序量化 | `$env:YINZHAN_DATA_DIR='...\e2e_restore_20261029\data'; python -X utf8 research_more2.py`（需 e2e 测试产物） |
| `prototype_fixes.py` | 质量闸门、一致身份证、USCC 地区码、固话区号原型（纯函数，无数据依赖） | `python -X utf8 prototype_fixes.py` |

注意：

- `repro_entities.py` 会重新跑检测管线，只写临时数据目录，不碰用户真实数据；
- 统计信号（如同假值多原值组、脏实体启发式）是**研究用**，不是产品运行时行为；
- 不要把脚本输出里的任何真实值贴到外部系统。
