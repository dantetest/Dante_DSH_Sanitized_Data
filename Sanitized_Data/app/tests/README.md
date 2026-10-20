# 数据隐站 · 回归门禁（red-line gate）

> 约束来源：[`docs/red-lines.md`](../../docs/red-lines.md)。
> 本目录是 **qa 的写作用域**：`app/tests/**`（含 `baseline/`、`fixtures/`）。
> 改引擎/规则/前端/依赖/样例的同学请不要改这里；需要加断言找 qa。

## 0. 一条命令给结论

```powershell
# 交付/收口前（含红线门禁 + 各功能域自测）
python -X utf8 app/tests/run_all.py
```

* 退出码 **0** = 全绿（红线未破），可以继续开发/提交；
* 退出码 **1** = 有硬项失败（回归），**禁止提交/交付**，修好或回滚后重跑；
* 退出码 **2** = 基线/环境异常（如基线缺失、服务起不来），先看输出。

输出形如：

```
[PASS] 住院病历.docx 换名残留=0 - residual=0
[FAIL] 委托代理合同.pdf 关键实体不减少 - 案号 0<1
[WARN] 新增规则 - +2 条（允许，不算回归）
...
回归通过：177 项硬检查全绿（含 2 条软提示），耗时 24.3s
```

每次改动**前后都跑**：开工前确认全绿 → 改完再跑，红了立刻修/回滚。
`app/tests/e2e.py` 仍保留作为最短冒烟；regression 会把它作为硬项跑一遍，并额外覆盖
元数据/隐藏数据、安全、契约、HTTP 主路径、MCP、桌面入口，以及 P0 二波新能力
（PDF 隐藏数据、EXIF 朝向、表格列级/数值写回、整图涂黑）与身份证 A 方案档位
（fields 默认 / whole 可选 / 反面/无脸回退）。

## 1. 硬 vs 软：什么算"破坏"

| 类别 | 判定 |
|---|---|
| **硬（HARD）** | 原有必须命中的实体变少；解析/导出失败；`residual > 0`；元数据回来了；**PDF 隐藏数据/EXIF 朝向/数值类型回退**；Geo tag 回来了；服务外呼；只监听范围扩大；审计记原值；规则包加载错误/id 冲突/既有规则被静默删除；解析器检测器缺失；静态站点/图标异常；HTTP 主路径失败；MCP 工具坏；映射库不可还原 → **FAIL，必须修** |
| **软（SOFT）** | 新增识别类型/新增规则/多识别实体；性能波动（>50% 报警）；已知边界现状变化（Office 内嵌图仅告警、PDF 文本层不 OCR、`--no-ocr` 未接线、无实体不可导出等）；已知不修项（83 双人低头侧脸 Haar 上限）→ **只提示，不算回归** |

**新增识别/新增规则导致多出实体永远不算回归**；判断"少了"用基线里的
`required.samples.*.required_types`（每样例"必须至少命中"的类型与最低数量）。
OCR 类样例（image/tiff）留 ±1 容差，避免同名 OCR 抖动误报。

## 2. 检查覆盖（按分组）

| 分组 | 硬项要点 |
|---|---|
| 基线自检 | schema、5 个样例文件在、HEAD/工作树/样例哈希软提示 |
| 功能红线 · e2e | e2e 退出码 0、汇总 5/5、无失败行；耗时软报警 |
| 功能红线 · 样例 | 5/5 解析、页数不回退、关键实体至少命中、双导出成功、residual=0、成品存在非空、PDF/Office 成品"独立复核"无原文残留、元数据清理（PDF author/XMP、DOCX core/company/缩略图、XLSX 属性、图片 EXIF、**TIF 无真实地理参考残留**/sidecar；LOCAL_CS 中性化结构按 D3 允许，见 SNAPSHOT §6） |
| 功能红线 · P0 二波新能力 | 构造**虚构**夹具真实跑通：PDF 隐藏数据（附件/批注/表单/JS）默认剥离 + verify 拦截未清理（keep 不绕过文本残留复检）；图片 EXIF Orientation=6 解析/渲染/导出转正且导出无 EXIF；表格列级覆盖 + 纯数值金额导出写回数字（openpyxl 断言）；已知不修项只做软记录 |
| 功能红线 · 身份证 A 方案 | 合成虚构证件 + 引擎 test hook 注入确定性命中，真跑 jobs→导出→verify：**API 默认 fields / 非法值回退 fields / whole 原样**；fields 正面四字段实体（ID_PHOTO/ID_NAME/ID_NUMBER/ID_ADDRESS）且**无 ID_DOCUMENT**，human 导出四区域已遮盖、**性别/民族/出生行未遮**（像素+几何重叠双断言）、residual=0；fields 反面实体 ID_AUTHORITY×1+ID_VALIDITY×1 且无 ID_DOCUMENT，human 两行已遮盖、国徽/标题保留、residual=0；whole 档（正/反）含 ID_DOCUMENT、整页黑覆盖率≈100%、residual=0；无脸正面/两行读不出的反面 fields 均回退整卡+提示 |
| 功能红线 · P1-A 第一波 | 真跑 jobs→导出→verify：聊天合成图 5 类布局均出 CHAT_* 且 human 对应区域黑覆盖、发票/名片负样本 0；招牌合成图 4 kind 均出实体且 human 遮盖；未成年人虚构 <14 身份证号 → `minor_detected=true`、`POST /minor-strict` 后 `minor_strict=true/id_card_mode=whole/全实体 black`、未检出 400；`time_blur` 默认 false，开启后 TIME 实体替换为时段且日期保留；组合风险 categories 含位置/联系人/时间 |
| 功能红线 · P1-B 第一波 | 真跑 jobs→导出→verify：**Office** 内嵌图片计数/隐藏对象计数；默认导出只对隐藏对象返回 `media_confirmation_required`（文案=隐藏对象），确认后图片自动脱敏（重扫 0 命中/verify 通过）、正常图字节不变、strict 后 hidden=0/无命中图空白、audit `counts.office_media`；**CSV/TSV** 列级+多级/合并表头值覆盖、导出行/列/分隔符不变、residual=0、无表头 0 命中；**PDF** 文本层敏感内嵌图产 EMBEDDED_IMAGE 且 human 黑覆盖≥90%、重解析 0 条 embedded_image，正常配图/纯文本/小图 0 新增 |
| 功能红线 · P1.5 | 真跑 jobs→导出→verify：**Office** 内嵌文字 PII 图 → `text_hits>0/text_images_redacted>=1`、成品重扫文字 0 命中/审计 `text_*`、预算跳过 warning、`YINZHAN_OFFICE_TEXT_OCR=0` 只做视觉脱敏；**PDF** text_sensitive.pdf → EMBEDDED_IMAGE（note text=N）+ human 黑覆盖≥90%/重解析 0/verify，开关=0 不产 mark；**6 类证件** jobs → ID_DOCUMENT label（工作证/居住证/港澳通行证/台胞证/外国人永居证/银行卡）且整卡黑、银行卡面积<0.90、发票/合同 0 新增；**护照号** near 命中 PASSPORT+surrogate 假值、无 near/法人不误报；**CSV/TSV** GBK/utf-8-sig/UTF-16/GBK+CRLF 编码/BOM/换行保留+未知回退警告 |
| 安全红线 · 静态 | 无出站网络（`requests`/`urllib.request`/`http.client`/socket 外呼白名单=回环）、无调用上下文外部 URL、默认监听 127.0.0.1、requirements 无网络客户端依赖、PDF `apply_redactions`、图片重编码不带 EXIF/ICC（含临时夹具实测）、TIFF 剥离+复检、DOCX/XLSX 属性清理、审计 INSERT 无原值列 |
| 契约完整性 · 规则/注册表 | 基线规则包仍在、load_errors=0、id 全局唯一/字段/scope/策略合法、已有规则不减少（迁移需白名单+替代规则）、各格式编译数不减少、**10 条规则 scope 已扩 image+tiff**、**pipl-cn(≥21)/table-cn(≥11) 完整且 id 唯一/命名空间正确**、插件/解析器/检测器不回退、替换器注册、OCR 可用性一致 |
| 契约完整性 · 服务/主路径 | health 200/net=blocked、runtime host=127.0.0.1、netstat 监听行只有回环、`/api/packs`、4 页面+3 图标 200+MIME、CSRF 跨站写 403、上传→job→ready→双导出→下载（sha/size 一致/%PDF）、**verify.pdf_hidden 为空**、审计计数含类型且无原值、审计链 verify、预览、PNG 渲染、框选补漏（rect 归一化）、逐条确认、accept-all、**整图涂黑（rect=[0,0,1,1]→black/coverage≈1.0）**、映射库可解密还原 |
| 契约完整性 · MCP | 4 工具齐全；stdio 协议端到端；list_rules / check_support / redact_text（不泄露合成原值）/ sanitize_document（residual=0）；MCP 存映射为软提示 |
| 功能红线 · 桌面/构建 | 桌面入口/服务入口/MCP/rehydrate 语法 OK、spec/iss/前端资源仍在、`app.spec` 仍收集 web |
| 软参考 | e2e/逐样例耗时、已知边界现状是否变化（变化只报告，不判回归）、已知不修项（83 双人低头侧脸 Haar 上限） |

## 3. 如何新增检查

在 `regression.py` 对应 `check_*` 函数里加一行，不用改框架：

```python
# 硬检查：失败会让整轮回归失败
gate.hard("检查项名称", 条件, "详情（失败时看这里）")

# 软检查：只提示，不影响退出码
gate.soft("软记录项", "详情", changed=是否与基线不同)
```

* 新检查尽量复用 `qa_common.py` 里的探针（`pdf_meta` / `docx_props` /
  `xlsx_props` / `image_meta` / `tiff_geo` / `capture_*` / `http_*`），
  不要直接写第二套 IO 逻辑；
* 需要样例/夹具时：样例放 `samples/`（qa 拥有，改动会影响所有回归），
  一次性夹具在测试运行时生成到临时目录（参考 `_exif_fixture_test`），
  不要往仓库塞二进制；
* 涉及新规则包：基线只要求"已有规则不减少、编译数不减少"，
  新增规则自动放行（软提示 +N 条）。

### 3.1 规则迁移/去重白名单（`RULE_REMOVAL_ALLOWLIST`）

规则从一个包搬到另一个包、或与别的规则合并去重时，"旧 id 消失"是**有意变更**
而不是回归。此时在 `regression.py` 顶部 `RULE_REMOVAL_ALLOWLIST` 登记：

```python
RULE_REMOVAL_ALLOWLIST = {
    "trade.swift_bic": {          # 消失的规则 id
        "reason": "去重：SWIFT/BIC 由 finance-cn/cn.swift_bic 统一覆盖（评审记录…）",
        "replaced_by": "cn.swift_bic",   # 替代规则 id，必须真实存在
    },
}
```

回归会：① 把登记过的"少规则"降为软提示；② **硬校验** `replaced_by` 仍在
当前规则集中（防止"去重"后能力凭空消失）；③ 未登记的规则消失仍是硬 FAIL。
登记属于"削弱一条保护"，必须写清理由并在交接/评审里说明，不能随手加。
下次 `--update-baseline` 后旧基线不再包含该 id，白名单条目可保留（无害）。

## 4. 基线更新流程（`--update-baseline`）

```powershell
python -X utf8 app/tests/regression.py --update-baseline
```

生成/更新：

* `app/tests/baseline/baseline.json` —— 机器可读（可 diff、按字段比较）；
* `app/tests/baseline/SNAPSHOT.md` —— 人读摘要（硬/软断言、每样例最低命中、
  元数据矩阵、规则包、服务与安全、已知边界）。

**当前基线（2026-10-20 P1.5 收口）**：8 个规则包 / 自有规则 104 条；5 个既有样例
实体 52 个、双导出 residual 0（本轮无聊天/招牌/证件样例，功能基线数值不变）；`regression.py` **241 项检查全绿（硬项失败 0）**；
`run_all.py` **11 套件**全绿（新增 signs）；身份证 A 方案（fields 默认/whole 可选；正面 4 字段 + 反面 2 字段）、
P1-A（聊天逐项/招牌/未成年/时间模糊/组合风险）已记录到 SNAPSHOT §6。
基线在 `engine_clean=false` 的 P1-A 工作树上采集（SNAPSHOT 已标注），提交后如需干净
HEAD 基线，再跑一次 `--update-baseline`。

**什么时候可以重录**：确认是**改进**而不是回归（例如新增规则让实体变多、
P0 修复了某条已知边界并同步调整软记录）。流程：

1. 先跑一次普通回归，确认没有硬项失败；
2. 跑 `--update-baseline --reason "原因"`，看输出里的差异
   （实体数/规则数/残留/主路径/样例内容变化）；
3. `git diff app/tests/baseline/` 人工过一遍：**只应出现预期变化**；
4. 理由会写进 `baseline.json` 的 `meta.update_reason/update_history` 和
   `SNAPSHOT.md` 的「本次基线更新理由 / 更新记录」，交接/评审时一并说明。

```powershell
# 例：Lead 批准的样例校验位修复（非法执业许可证号 → 合法 USCC），不是功能回归
python -X utf8 app/tests/regression.py --update-baseline --reason "样例修复：非法执业许可证号改为合法 USCC（Lead 批准，非回归）"
```

**禁止**：为了让回归变绿而更新基线（那是掩盖回归）。任何"关键实体变少 /
residual>0 / 元数据回来 / 外呼"在重录后依然会在新基线里体现出来。

## 5. 环境与排查

* Python 3.12；离线 OCR（rapidocr PP-OCRv6）可用；无 GDAL/trimesh。
* 端口：默认从 `18763` 起自动找空闲端口，服务只绑 `127.0.0.1`；
  跑完自动杀进程。
* 数据目录：**全部用系统临时目录**（`YINZHAN_DATA_DIR` 指向临时路径），
  不污染仓库 `data/`；`--keep` 可保留临时目录排查，`--workdir D:\tmp\xxx` 可固定位置。
* 中文乱码/`UnicodeEncodeError`：没加 `-X utf8`，重跑即可。
* 首次跑 OCR 约 2–5 秒加载模型；整轮约 30–60 秒（含 e2e 子进程）。
* `--update-baseline` 运行期间也会起一次服务、走一次主路径并做 MCP 调用，
  所以它会真正验证"采集到的基线本身是可用的"。

## 7. 全套测试怎么跑（regression + 各域自测）

红线门禁之外，各功能域（条码/财务/贸易/图像/判决书/PIPL/表格/TIF/PDF 隐藏数据）都有开发者自测。
"本轮收口/交付前"建议一条命令跑全套：

```powershell
python -X utf8 app/tests/run_all.py
```

执行顺序、内容与预计耗时（单机 CPU，OCR 首次加载模型算在内；本机实测 2026-10-20）：

| # | 测试 | 内容 | 实测耗时 |
|---|---|---|---|
| 1 | `regression.py` | **红线门禁（硬）**：功能/安全/契约/主路径/P0/P1 新能力 | 65.4s |
| 2 | `test_barcodes.py` | 条码/二维码识别、不可再解码自检、样例零误报 | 4.3s |
| 3 | `test_finance.py` | finance-cn v2 规则、校验器、金额一致性 | 5.6s |
| 4 | `test_trade.py` | trade-cn 规则、贸易号码校验器、样例零误报 | 5.4s |
| 5 | `test_visual.py` | 人脸分块/整头/告警、车牌完整牌面、样例零误报 | 5.2s |
| 6 | `test_legal_detect.py` | 判决书类文本检测（跨行案号/地址/标签姓名/带空格金额） | 1.6s |
| 7 | `test_pipl_rules.py` | pipl-cn 规则 + XLSX 纯数值单元格（金额写回数字） | 5.6s |
| 8 | `test_table_column.py` | table-cn 表头列级检测（xlsx/docx 整列覆盖） | 5.7s |
| 9 | `test_pdf_hidden.py` | PDF 隐藏数据（附件/批注/表单/JS/缩略图）剥离与复检 | 3.1s |
| 10 | `test_office_media.py` | P1-B Office 内嵌图自动脱敏/隐藏对象/strict；P1.5 文字 OCR | 42.7s |
| 11 | `test_csv_table.py` | P1-B CSV/TSV 列语义与多级表头；P1.5 编码保留 | 6.0s |
| 12 | `test_pdf_embedded.py` | P1-B PDF 内嵌图；P1.5 文字图检测/涂黑 | 10.7s |
| 13 | `test_tiff.py` | 正射 TIF P0（大图/位深/坐标中性化） | 26.9s |

合计约 **1.5–3 分钟**（并行开发/低配机器会更慢；耗时只做软参考，不影响判定）。

常用参数：

```powershell
python -X utf8 app/tests/run_all.py --list              # 看要跑哪些（缺失的会标注跳过）
python -X utf8 app/tests/run_all.py --skip domain       # 只跑红线门禁
python -X utf8 app/tests/run_all.py --only finance,trade
python -X utf8 app/tests/run_all.py -v                  # 透传各测试输出（排查用）
python -X utf8 app/tests/run_all.py --keep --list       # 配合排查
```

规则：
* **顺序执行**（不并行）：各测试都要加载 OCR/插件，并行会抢 CPU 造成性能类误报；
* 每个子测试用**独立系统临时数据目录**（`YINZHAN_DATA_DIR`/`YINZHAN_OUTPUT_DIR`），
  不写仓库 `data/`；`run_all.py --keep` 可保留临时目录；
* 退出码：0=全部通过；1=有失败（禁止提交/交付）；2=执行器自身错误；
* 各域测试单独跑也支持：`python -X utf8 app/tests/test_finance.py` 等；
* **红线判定只认 `regression.py`**：域测试失败代表对应功能域自测未过，
  由该域 owner 修；red-line 被破则整批不可交付。

## 6. 与其它测试的关系

* `app/tests/e2e.py`：最短冒烟（5 样例 → 解析/检测/双导出/复检），开发时可以单独跑；
* `app/tests/regression.py`：本文档描述的完整门禁，**交付/提交前必跑**；
* `app/tests/test_*.py`：各功能域开发者自己的单测（条码/财务/贸易/视觉/判决书/PIPL/表格/PDF 隐藏数据/TIF），
  **不属于红线门禁**；regression.py 不 import 它们（避免并行开发时互相卡），域内自测请各自跑；
  它们坏了不阻塞红线门禁，但对应功能域的交付自测由 owner 负责；
* `app/tests/eval_real_media.py`：真实素材评测脚本（非门禁，按需手动跑）；
* `app/tests/fixtures/`：各功能域测试夹具（二进制/文本），只做测试输入，不进产品；
* `app/tests/make_samples.py`：重新生成 `samples/` 用；改样例后必须重录基线
  （`--update-baseline --reason ...`），并在说明里写原因。

> 写作用域提醒：`app/tests/**` 归 qa；其它角色如需加域内测试，请用 `test_<domain>.py`
> 与 `fixtures/<domain>/` 命名，不要改 `regression.py` / `qa_common.py` / `baseline/`
> 的既有内容（改门禁等于改红线，需要 qa + Lead 评审）。
