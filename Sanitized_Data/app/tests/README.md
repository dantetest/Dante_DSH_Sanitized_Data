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
回归通过：132 项硬检查全绿（含 9 条软提示），耗时 38.5s
```

每次改动**前后都跑**：开工前确认全绿 → 改完再跑，红了立刻修/回滚。
`app/tests/e2e.py` 仍保留作为最短冒烟；regression 会把它作为硬项跑一遍，并额外覆盖
元数据、安全、契约、HTTP 主路径、MCP、桌面入口。

## 1. 硬 vs 软：什么算"破坏"

| 类别 | 判定 |
|---|---|
| **硬（HARD）** | 原有必须命中的实体变少；解析/导出失败；`residual > 0`；元数据回来了；Geo tag 回来了；服务外呼；只监听范围扩大；审计记原值；规则包加载错误/id 冲突；解析器检测器缺失；静态站点/图标异常；HTTP 主路径失败；MCP 工具坏；映射库不可还原 → **FAIL，必须修** |
| **软（SOFT）** | 新增识别类型/新增规则/多识别实体；性能波动（>50% 报警）；已知边界现状变化（Office 内嵌图仅告警、PDF 文本层不 OCR、`--no-ocr` 未接线、无实体不可导出等）→ **只提示，不算回归** |

**新增识别/新增规则导致多出实体永远不算回归**；判断"少了"用基线里的
`required.samples.*.required_types`（每样例"必须至少命中"的类型与最低数量）。
OCR 类样例（image/tiff）留 ±1 容差，避免同名 OCR 抖动误报。

## 2. 检查覆盖（按分组）

| 分组 | 硬项要点 |
|---|---|
| 基线自检 | schema、5 个样例文件在、HEAD 软提示 |
| 功能红线 · e2e | e2e 退出码 0、汇总 5/5、无失败行；耗时软报警 |
| 功能红线 · 样例 | 5/5 解析、页数不回退、关键实体至少命中、双导出成功、residual=0、成品存在非空、PDF/Office 成品"独立复核"无原文残留、元数据清理（PDF author/XMP、DOCX core/company/缩略图、XLSX 属性、图片 EXIF、**TIF 无真实地理参考残留**/sidecar；LOCAL_CS 中性化结构按 D3 允许，见 SNAPSHOT §6） |
| 安全红线 · 静态 | 无出站网络（`requests`/`urllib.request`/`http.client`/socket 外呼白名单=回环）、无调用上下文外部 URL、默认监听 127.0.0.1、PDF `apply_redactions`、图片重编码不带 EXIF/ICC（含临时夹具实测）、TIFF 剥离+复检、DOCX/XLSX 属性清理、审计 INSERT 无原值列 |
| 契约完整性 · 规则/注册表 | 5 包仍在、load_errors=0、id 全局唯一/字段/scope/策略合法、已有规则不减少、各格式编译数不减少、插件/解析器/检测器不回退、替换器注册、OCR 可用性一致 |
| 契约完整性 · 服务/主路径 | health 200/net=blocked、runtime host=127.0.0.1、netstat 监听行只有回环、`/api/packs`、4 页面+3 图标 200+MIME、CSRF 跨站写 403、上传→job→ready→双导出→下载（sha/size 一致/%PDF）、审计计数含类型且无原值、审计链 verify、预览、PNG 渲染、框选补漏（rect 归一化）、逐条确认、accept-all、映射库可解密还原 |
| 契约完整性 · MCP | 4 工具齐全；list_rules / check_support / redact_text（不泄露合成原值）/ sanitize_document（residual=0）；MCP 存映射为软提示 |
| 功能红线 · 桌面/构建 | 桌面入口/服务入口/MCP/rehydrate 语法 OK、spec/iss/前端资源仍在、`app.spec` 仍收集 web |
| 软参考 | e2e/逐样例耗时、已知边界现状是否变化（变化只报告，不判回归） |

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

红线门禁之外，各功能域（条码/财务/贸易/图像/TIF）还有开发者自测。
"本波收口/交付前"建议一条命令跑全套：

```powershell
python -X utf8 app/tests/run_all.py
```

执行顺序、内容与预计耗时（单机 CPU，OCR 首次加载模型算在内）：

| # | 测试 | 内容 | 预计耗时 |
|---|---|---|---|
| 1 | `regression.py` | **红线门禁（硬）**：功能/安全/契约/主路径 | 30–90s |
| 2 | `test_barcodes.py` | 条码/二维码识别、不可再解码自检、样例零误报 | 10–30s |
| 3 | `test_finance.py` | finance-cn v2 规则、校验器、金额一致性 | 10–20s |
| 4 | `test_trade.py` | trade-cn 规则、贸易号码校验器、样例零误报 | 5–15s |
| 5 | `test_visual.py` | 人脸/证件/车牌扫描器、样例零误报 | 10–30s |
| 6 | `test_tiff.py` | 正射 TIF P0（大图/位深/坐标中性化，落盘后自动纳入） | 20–90s |

合计约 **1.5–4 分钟**（并行开发/低配机器会更慢；耗时只做软参考，不影响判定）。

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
* `app/tests/test_*.py`：各功能域（条码/财务/贸易/视觉）开发者自己的单测，**不属于红线门禁**；
  regression.py 不 import 它们（避免并行开发时互相卡），域内自测请各自跑；
  它们坏了不阻塞红线门禁，但对应功能域的交付自测由 owner 负责；
* `app/tests/fixtures/`：各功能域测试夹具（二进制/文本），只做测试输入，不进产品；
* `app/tests/make_samples.py`：重新生成 `samples/` 用；改样例后必须重录基线
  （`--update-baseline --reason ...`），并在说明里写原因。

> 写作用域提醒：`app/tests/**` 归 qa；其它角色如需加域内测试，请用 `test_<domain>.py`
> 与 `fixtures/<domain>/` 命名，不要改 `regression.py` / `qa_common.py` / `baseline/`
> 的既有内容（改门禁等于改红线，需要 qa + Lead 评审）。
