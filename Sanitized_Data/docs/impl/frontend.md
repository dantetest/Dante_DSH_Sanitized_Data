# 前端实现记录 · 导出媒体二次确认 + 新实体类型展示

> 状态：已实现 · 2026-10-19 · 负责人：designer-icon（task-12）
> 代码文件：`app/web/app.js`（**本批唯一改动**，+95 / −11 行）；`app/web/style.css`、4 个 HTML、`api.py` / `jobs.py` / `pipeline.py` / 规则 / 解析器均未改。
> 契约对齐：Lead 2026-10-19 确认的 `api.py` / `jobs.py` / `pipeline.verify` 实现（media_confirmation_required + verify.visual_residual / verify.media）。
> 红线：`docs/red-lines.md`（S1–S8 / F1–F12）全部保持；无出站网络、无 CDN、无第三方库、无新依赖、未 push。

---

## 1. 改动点

### 1.1 新实体类型映射（`app/web/app.js` L33–58）

| 位置 | 改动 | 说明 |
|---|---|---|
| `TYPE_CN`（L33–42） | 新增 11 个中文名：`SEAL 公章/印章`、`SIGNATURE 手写签名`、`SIG_BLOCK 签署区`、`HANDWRITING 疑似手写`、`FACE 人脸`、`ID_DOCUMENT 证件`、`PLATE 车牌`、`ROAD_SIGN 路牌`、`DOOR_ADDRESS 门牌/门址`、`QRCODE 二维码`、`BARCODE 条形码` | 与后端 `jobs._VISUAL_SPEC` 的 etype/label 对齐；实体自带 `label` 时仍优先用 label；未知 etype 走 `'其他敏感'` 兜底 |
| `TYPE_ORDER`（L43–48） | 文本类 → 视觉身份/车辆/地址类 → 印章/签署类 → 码类 → OTHER | 决定实体清单分组顺序与 `logTypeSummary` 的「识别到 …」顺序；未知类型排在最后 |
| `TYPE_COLOR`（L49–58） | 新增 11 个 muted 档案色（如 FACE `#B0573F`、QRCODE `#2E3A48`、BARCODE `#24211C`） | 只用于实体清单分组前的小圆点；沿用「纸质密档」低饱和色调，不引入高饱和色；分组主体仍以中文名辨识，颜色仅辅助 |

> 说明：`SEAL / SIGNATURE / SIG_BLOCK / HANDWRITING` 不在这批 7 个新类型清单里，但后端 `_VISUAL_SPEC` 已经会产出这些 etype（旧代码只靠实体自带 label 展示，进度日志里会露出英文）。本批一并补全，属于「未知类型有兜底」同一处映射的必要收口。

### 1.2 导出结果 verify 字段兼容（L1466–1513）

`verifyCount(value)` + `readVerify(raw)` + 重写 `normalizeExport(res)`：

- 已知字段：`verify.residual`（int）、`verify.visual_residual`（int）、`verify.media = {count, confirmed}`；`residual` 同时兼容旧的 `raw.residual`。
- **未知字段一律忽略**（例如以后加 `verify.future_field`），不抛错、不影响已成功的交付。
- 空 `verify` / 完全无 `verify` 的旧响应：字段为 `null`，导出卡片按原样显示「自检 已完成」。
- `verifyCount` 兼容 number / 数组 / `{count}` / `{unprocessed}` 几种形状（防御式，当前后端是 number 与 `{count}`）。

### 1.3 导出卡片新增展示（L1532–1538）

在原有「已生成 / 编号 / 指纹 / 自检」之后，**有数据才加行**：

- `视觉残留　0 处`（`verify.visual_residual` 存在时）
- `内嵌媒体　N 处未处理（你已确认导出）`（`verify.media.count > 0` 时；`confirmed` 才带括号）

老后端不返回这两项时，卡片外观与旧版完全一致。

### 1.4 内嵌媒体二次确认（L1550–1605）

| 函数 | 职责 |
|---|---|
| `exportRequestBody(mode, allowMedia)` | 首次导出 `{"mode":"ai"}`（与原逻辑一致）；确认后 `{"mode":"ai","allow_unprocessed_media":true}` |
| `mediaConfirmText(err)` | 优先用后端 `error.message`（已含「N 张…要继续导出吗？」），只在没有 message 时兜底；避免问号重复 |
| `askMediaConfirmation(mode, err)` | 复用现有 `#toast` + `.toast-actions` 样式弹确认条，两个按钮：「仍要导出」重发同一下载 POST；「先不导出」只提示；15 秒无操作自动消失（视为未确认、不导出） |
| `doExport(mode, opts)` | 捕获 `err.code === 'media_confirmation_required'`：非确认态 → 恢复卡片 + 弹确认；确认态仍被拒 → 提示「本机没有接受确认」防死循环；其它错误走原有错误提示；`state.modalBusy` 防并发 |

取消路径不产生第二次请求；卡片由 `resetCard(card)` 恢复模板（涂黑卡的「再点一次」armed 状态一并清除）。

---

## 2. 接口契约（实测确认）

`POST /api/jobs/{job_id}/export`，`Content-Type: application/json`

**请求**

```json
{"mode": "ai"}                                  // 首次导出（body 与原逻辑相同）
{"mode": "ai", "allow_unprocessed_media": true} // 用户在二次确认里点「仍要导出」后重发
```

**成功 200**

```json
{
  "out": "...\\out\\xxx_换名_20261019-0002.docx",
  "name": "xxx_换名_20261019-0002.docx",
  "seal_no": "20261019-0002",
  "sha256": "...",
  "mode": "ai",
  "verify": {
    "verified": true,
    "residual": 0,
    "checked": 4,
    "visual_residual": 0,
    "media": {"count": 1, "confirmed": true},
    "issues": []
  },
  "warnings": ["…"],
  "bytes": 79127
}
```

**失败（检测到未处理内嵌媒体且未确认）`400`**

```json
{"error": {
  "code": "media_confirmation_required",
  "message": "检测到 1 张未脱敏的内嵌图片（DOCX/XLSX 内的图片/图表），当前版本不会自动处理这些图片。要继续导出吗？"
}}
```

- 前端只按 `error.code === "media_confirmation_required"` 捕获，**不依赖 400/409 具体状态码**（`verify_failed` 走 409，不影响本流程）。
- 后端二次确认放行后，`verify.media.confirmed = true`、`verify.media.count = N`；前端据此在卡片上写明「N 处未处理（你已确认导出）」，与红线 S8「话术诚实」一致。
- 未处理媒体数量上限、`parts` 明细由后端决定，前端不解析 `parts`。

---

## 3. 手动测试步骤（真实浏览器，约 2 分钟）

前置：`cd app/server && python run.py --port 8760 --no-browser --no-ocr`（或双击 `启动.bat`）；准备一个**含内嵌图片的 DOCX**（用 python-docx `add_picture()` 插一张图即可；本机用 `samples/名片.png` 造了 `内嵌图片样例.docx`）。

1. 浏览器打开 `http://127.0.0.1:8760/console`，把 DOCX 拖进投放台，等识别完成进入对照台。
2. 点右上「交 付 →」打开交付弹层。
3. 点「给 AI 读」卡片 **一次**。预期：底部出现确认条，文案含「检测到 N 张未脱敏的内嵌图片（DOCX/XLSX 内的图片/图表）…要继续导出吗？」，两个按钮「仍要导出 / 先不导出」；卡片恢复成「点击导出 →」，**不**出现下载链接。
4. 点「先不导出」。预期：提示「已取消导出，原件没有被改动。」，卡片仍未导出、无下载链接。打开 DevTools → Network，确认只有 1 次 `POST .../export`，body 为 `{"mode":"ai"}`。
5. 再点一次「给 AI 读」→ 确认条 → 点「仍要导出」。预期：卡片变「已 隐 去」，出现「视觉残留 0 处」「内嵌媒体 N 处未处理（你已确认导出）」两行和「下载这一份 ↓」；底部提示「已交付 · 换名版生成好了…」。
6. DevTools → Network 第 2 次 `POST .../export` 的 body 应为 `{"mode":"ai","allow_unprocessed_media":true}`；`GET /api/jobs/{id}` 的 `exports.ai.verify.media` 为 `{count:N, confirmed:true}`。
7. 涂黑卡（「给人看」）路径相同：第一次点击只提示「再点一次，确认导出」，第二次点击才会触发导出与媒体确认；取消后 armed 状态清除、卡片恢复。
8. 无内嵌媒体的普通文件：首次导出**不应**出现确认条，直接成功（原功能不回退）。
9. 新类型展示：用带人脸/二维码的图片（`YINZHAN_SCAN_VISUAL` / `YINZHAN_SCAN_BARCODE` 默认开）→ 实体清单分组应显示「人脸 / 证件 / 车牌 / 路牌 / 门牌/门址 / 二维码 / 条形码」，未知类型显示「其他敏感」；进度日志的「识别到 X × N」为中文。

---

## 4. 自动化验证结果

环境：Windows、Python 3.12.14、Node v24.21.0、Edge 155.0.4283.62；服务用临时数据目录 `%TEMP%\yz_frontend_test\data`，端口 18765，`--no-browser --no-ocr`。

### 4.1 语法

- `node --check app/web/app.js` → **exit 0**。

### 4.2 后端契约（真实 HTTP，不经过 UI）

`内嵌图片样例.docx`（81 896 B，含 `word/media/image1.png`）：

| 步骤 | 结果 |
|---|---|
| 上传 / 建任务 | 200；任务 ready，4 处实体（手机号/当事人/地址/授信额度） |
| `POST export {"mode":"ai"}` | **400** + `error.code=media_confirmation_required` + 「检测到 1 张…要继续导出吗？」 |
| `POST export {"mode":"ai","allow_unprocessed_media":true}` | **200**；`verify={verified:true, residual:0, checked:4, visual_residual:0, media:{count:1, confirmed:true}, issues:[]}`；生成成品 79 127 B |
| `GET /api/jobs/{id}` | 快照里 `exports.ai.verify` 与导出一致 |

### 4.3 浏览器端到端（CDP 驱动真实 Edge，26 项断言全 PASS）

| 断言 | 实测 |
|---|---|
| 上传含图 DOCX → 对照台就绪 | 共 4 处，清单已生成 |
| 涂黑卡第一次点击只提示、第二次才导出 | armed=true → 触发 POST |
| 首次导出被拦并弹出确认条（后端文案） | 「检测到 1 张未脱敏的内嵌图片（DOCX/XLSX 内的图片/图表）…要继续导出吗？」，按钮 `["仍要导出","先不导出"]` |
| 取消后不导出、卡片恢复 | `done=false`，按钮文案回到「点击导出 →」，请求总数仍为 1 |
| 首次请求 body 无 allow 标记 | `{"mode":"human"}` / `{"mode":"ai"}` |
| 确认后重发带 allow 标记 | `{"mode":"ai","allow_unprocessed_media":true}` |
| 卡片展示 verify 新字段 | 「自检 残留 0 处 · 视觉残留 0 处 · 内嵌媒体 1 处未处理（你已确认导出）」，下载链接 `…/file?mode=ai` |
| 新类型映射 | FACE/ID_DOCUMENT/PLATE/ROAD_SIGN/DOOR_ADDRESS/QRCODE/BARCODE → 中文名齐全、进入 TYPE_ORDER、各有分组色 |
| 实体清单渲染 | 注入 8 组合成实体 → 8 个分组、中文组名正确、未知类型兜底「其他敏感」、待确认行保留 |
| normalizeExport 兼容 | 新字段可读；未知字段 `future_field` 被忽略；空/无 verify 不抛错 |
| 4 页面 | `/`、`/download`、`/mcp`、`/console` 全部加载完成，**全程 0 个 JS 异常 / console.error** |

证据截图（临时目录，供复核）：`%TEMP%\yz_frontend_test\ui_media_confirm.png`（确认条）、`ui_export_done.png`（导出成功卡片）。

### 4.4 回归门禁

命令：`python -X utf8 app/tests/regression.py`（qa 产出后执行）。本批前端改动后共跑两次：

- **第 1 次（23:56，改动完成后立即跑）：全绿** —— 157 项硬检查全过（含 2 条软提示，耗时 55.16s），脚本结论「红线未破，可以继续开发/提交。」e2e 5/5、5 样例导出/复检 10/10、安全静态红线、规则/注册表、HTTP 主路径、MCP、桌面入口全部 PASS。
- **第 2 次（23:58，复核用）：156 PASS / 1 FAIL / 2 WARN** —— 唯一红项是 **`[契约完整性 · 规则/注册表] 已有规则不减少`：`trade-cn 15<16、少规则 ['trade.swift_bic']`**。该失败来自**同期其他批次的规则文件改动**（`rules/trade-cn.yaml` 为新增文件、最后修改 23:57:45，注释里写明「SWIFT/BIC 已由 finance-cn 的 cn.swift_bic 覆盖，本包不重复定义」），与本批前端改动无关；`app/web/app.js` 不在该检查的判定链路上。已把该红项原样报给 Lead/规则负责人处理（按红线流程：减少规则算回归，应补回 `trade.swift_bic` 或经 Lead 批准后重录基线，不能带病提交）。
- 两次运行中与本批相关的检查全部 PASS（没有任何一次因前端改动变红）：「4 页面 + 3 图标 200 且 MIME 正确」、`app/web/app.js`/`style.css` 存在、主路径导出/下载/审计、`verify` 字段（`{"verified":true,"residual":0,"visual_residual":0,"media":{"count":0,"confirmed":false}}`）。
- 软提示均与本批无关：e2e 耗时 15.1s/23.27s（基线 15.86s 附近波动）；名片.png、正射影像.tif 单样例解析耗时高于基线（OCR 冷启动/机器负载），功能结果一致。
- 注：两次运行期间工作区都有其他批次的未提交后端改动，regression 基线自检据此给出 WARN（「开发进行中；此时失败可能是暂态」）。

---

## 5. 红线检查

| 红线 | 检查方式 | 结果 |
|---|---|---|
| S2 引擎无出站网络 | 静态扫描 `app/web/app.js`：全文只有 **1 处** `fetch(path)`（相对路径，走同源 `/api/*`）；无 `http(s)://` 字面量、无 `WebSocket`、无 `XMLHttpRequest`、无 `import` | 通过 |
| 无 CDN / 无第三方库 | 未新增 `<script>`、未引外部资源、未使用框架；确认条复用现有 `#toast` / `.toast-actions` 样式，`style.css` 零改动 | 通过 |
| 无新依赖 | 纯原生 JS（ES2017 语法，与文件其他部分一致）；未改 `app/server/requirements.txt` | 通过 |
| F10 站点 4 页面 | 真实服务下 `/`、`/download`、`/mcp`、`/console` 均 200 且加载无 JS 错误 | 通过 |
| F1–F9 既有导出能力 | `doExport` 的首次请求 body 与成功渲染路径保持原逻辑；仅增加「被拦截时弹确认」分支；取消不改任何后端状态 | 通过 |
| 不破坏图标/HTML | 4 个 HTML 未改（图标行保持上批状态）；`git status` 中本任务只出现 `app/web/app.js` + 本文档 | 通过 |
| 不 push | 未执行任何 git 写操作 | 通过 |

---

## 6. 兼容性与风险

1. **后端未落地时的降级**：若旧版后端没有 `media_confirmation_required`，`doExport` 走原有错误提示，行为与改动前一致。
2. **后端忽略确认标记（版本错配）**：已确认仍返回该 code 时，前端提示「本机没有接受『仍要导出』的确认，请重启隐站后再试」，不会进入无限确认循环。
3. **状态码变化**：只按 `error.code` 捕获，后端把 400 改成 409 也不影响。
4. **verify 字段演进**：只读已知字段，未知字段忽略；`media` 也兼容 number / `{count}` 两种形状。
5. **确认条是 transient toast**：15 秒自动消失即视为未确认，需用户重新点卡片；不会静默导出（安全默认）。
6. **文案长度**：后端 message 约 60 字，`cleanMessage` 截断阈值 120 字，当前不会被截断；`#toast` 最大宽 600px，实测 3 行换行正常。
7. **颜色仅为前端展示**：新增 11 个 muted 分组色只在实体清单小圆点上用；不影响后端数据、图标资产或品牌令牌。
8. **手动框选菜单未扩展**：框选补漏菜单仍是 5 类（人名/电话/地址/金额/其他敏感）。新类型目前来自自动检测；若后续要手动框人脸/条码，只需在 `MENU_ETYPES` 加项（但菜单高度/定位上限 `maxTop` 需一起调，本批不做）。
9. **并发点击**：`state.modalBusy` 防重复导出；确认按钮点击后 toast 立即隐藏，不会二次触发。
10. **回归结果**：改动后首跑 157 项全绿；复核跑因**同期规则文件改动**（`trade-cn` 少 `trade.swift_bic`）出现唯一红项，与前端改动无关（详见 §4.4），已上报 Lead/规则负责人。本批前端的行为面只有「媒体确认」与「类型映射」，页面加载、4 页面 HTTP、既有导出/下载/审计路径均 PASS。

---

## 7. 未做 / 后续可选项

- P1（自动脱敏 Office 内嵌图片）落地后，本二次确认可从「阻断」降级为「默认自动处理 + 结果说明」，`allow_unprocessed_media` 字段保留向后兼容。
- 手动框选菜单的新类型扩展（见 §6.8）。
- 若用户希望确认条更「重」（模态确认而不是 toast），可复用 `#modalExport` 的弹层结构另做一版；本批按任务要求优先用现有 toast 样式。

---

# 第二轮（P0）：整图涂黑一键按钮

> 状态：已实现 · 2026-10-20 · 负责人：designer-icon（task-19）
> 背景：用户已批准 P0「头/整图」策略——人脸大图、场景照片等自动识别不到时，用户可一键把**当前页**整页不透明涂黑。
> 代码：`app/web/app.js`（+150/−3）、`app/web/style.css`（+13）；**HTML / 后端 / 规则 / 解析器全部未改**；未 push。

## 8.1 改动点

| 位置 | 改动 |
|---|---|
| `mountWholePageButton()` | 在 `init()` 里动态创建 `<button id="btnWholePage" class="btn danger" type="button">⊘ 整图涂黑</button>`，用 `insertAdjacentElement('afterend')` 插到「框选补漏」按钮后面（**不改 index.html**）；带 `title` 与 `aria-label` |
| `onWholePageClick()` | 二次确认：复用现有 `#toast` + `.toast-actions` 两个按钮「确认涂黑整页 / 取消」，12 秒无操作自动消失（视为未确认，不涂黑）。文案：不可还原 + 图片模式提示 + 「本页已有 N 处标记，整页涂黑会覆盖本页所有内容」（N>0 时） |
| `doWholePageMark()` | `POST /api/jobs/{id}/mark`，body 精确为 `{page, rect:[0,0,1,1], etype:"OTHER", label:"整图脱敏", propagate:false}`；成功 toast「整页已加入涂黑，可在右侧清单撤销」，随后复用现有 `refreshLive()`（刷新实体/清单/预览）并 `flashEntity()` 高亮 |
| `rejectEntity()` + `buildRail()` | 给 `origin === "manual"` 的条目（整图涂黑、框选补漏）加「撤销」小按钮 → 调现有 `POST /api/jobs/{id}/entities/{eid} {"action":"reject"}` → 刷新。这样 toast 里「可在右侧清单撤销」是**真的能撤销**，不是空话 |
| `wholePageAvailable()` / `syncWholePageButton()` | **格式门禁**：按当前页预览的 `kind`（raster/flow）判断——图片/TIFF/PDF 可用；DOCX/XLSX/TXT 是文字排版（flow），整页矩形盖不到文字，按钮**禁用**并在 title/aria 里说明原因，避免「点了没效果」的静默失效 |
| `syncFootHint()` | 进入对照台时：image/tiff 文件的底栏提示换成「照片/扫描件：自动识别不到的内容，用「整图涂黑」一次盖掉 · 按住黑条 300 毫秒可透视」 |
| `updateConsoleHead()` / `readCounts()` / `activeEntityCount()` | 撤销后计数一致：`total/done` 扣除 `rejected`，rail 计数只算未撤销条目（既有流程不产生 rejected，结果不变） |
| `style.css` | `.btn.danger`（虚线朱红边，与普通按钮区分）、`.btn.danger.busy`、`.row .row-undo`（清单里的撤销小按钮） |

**未改**：`index.html`（按钮由 JS 动态插入）、`api.py` / `jobs.py` / `pipeline.py` / 规则包 / 解析器；未新增依赖、未外呼、未 push。

## 8.2 接口契约（均为现有接口，后端零改动）

```jsonc
// POST /api/jobs/{id}/mark  （整图涂黑）
{"page": 0, "rect": [0, 0, 1, 1], "etype": "OTHER", "label": "整图脱敏", "propagate": false}
// → 200 {"added": 1, "propagated": 0, "entity": {id, etype:"OTHER", label:"整图脱敏", origin:"manual",
//        status:"accepted", spans:[{"rect":[0,0,W,H],"page":0}]}}
```

- 服务端把归一化 rect 换算成页面坐标，实体 `origin="manual"`、`status="accepted"`；`GET /preview/{page}?mode=out` 的 `entities[].boxes = [[0,0,W,H]]`。
- 撤销：`POST /api/jobs/{id}/entities/{eid}` body `{"action":"reject"}` → `entity.status="rejected"`（后端已有能力，本批只补前端入口）。
- 导出：human 模式整页填充 `--black #0D0B09` 不透明块（导出器统一黑块色），成品整页不可读。

## 8.3 实测（真实 HTTP + CDP 驱动真实 Edge，28 项断言 ALL PASS）

| 断言 | 实测 |
|---|---|
| 按钮挂载 | 位于「框选补漏」之后、`.console-foot` 内；`type=button` + title + aria-label |
| 键盘可访问 | 真实 CDP `Input.dispatchKeyEvent`：Enter / Space 均能打开二次确认（配 `Emulation.setFocusEmulationEnabled` 模拟聚焦） |
| 二次确认 | 文案含「导出后不可还原」「照片 / 场景图…用这一招最稳」；按钮 `["确认涂黑整页","取消"]`；取消后 **0 次 /mark**、清单不变 |
| 请求体 | `{"page":0,"rect":[0,0,1,1],"etype":"OTHER","label":"整图脱敏","propagate":false}` 逐字段匹配 |
| 成功 toast | 「整页已加入涂黑，可在右侧清单撤销」 |
| 清单 | 出现「整图脱敏 ×1」+「撤销」按钮；撤销后回到 0 处 |
| 预览 | 「可外发版」出现覆盖整页（≈100%）的框；切到「涂黑」模式为不透明整页黑框（99.6% × 99.5%，背景 `rgb(13,11,9)`、opacity 1）；原件侧也有整页虚框 |
| 后端数据 | 实体 `type=OTHER / origin=manual / label=整图脱敏 / status=accepted`；预览整页框 `[0,0,1200,900]` |
| **导出成品不可读** | 下载 human 成品 PNG：1200×900，**100% 像素亮度 < 40**（均值 11.28/255，唯一颜色 1 种，alpha 全 255，四角/中央均为 `#0D0B09`） |
| 格式门禁 | DOCX（文字排版）→ 按钮禁用 + title 说明原因 + 点击不发起 /mark；PDF（按页渲染）→ 可用 |
| 多实体提示 | 名片 6 实体时：「本页已有 6 处标记，整页涂黑会覆盖本页所有内容。」 |
| 页面回归 | `/`、`/download`、`/mcp`、`/console` 全部加载，全程 **0 JS 异常** |
| 语法 | `node --check app/web/app.js` → exit 0 |

证据截图（临时目录 `%TEMP%\yz_wholepage_test\`，供复核）：

- `ui_wholepage_confirm.png`：二次确认条 + 底栏按钮位置
- `ui_wholepage_black.png`：涂黑模式下右栏整页黑、左栏整页虚框、清单「整图脱敏 / 撤销」
- `ui_wholepage_multi.png`：多实体覆盖提示
- `ui_wholepage_export.png`：导出成功卡片
- `evidence_before_after.png`：原图 vs 导出成品（整页全黑）

## 8.4 手动测试步骤

1. 起服务（`cd app/server && python run.py --port 8760`），把任意照片/场景图 PNG（或 `samples/名片.png`）拖进投放台，等识别完成进入对照台。
2. 点底栏「⊘ 整图涂黑」→ 出现确认条；若本页已有 N 处标记，会显示「整页涂黑会覆盖本页所有内容」。
3. 点「取消」：无任何变化；再点按钮 →「确认涂黑整页」：右侧清单出现「整图脱敏」，可切到「涂黑」模式查看右栏整页黑、左栏整页虚框。
4. 「交付 → 给人看」导出，下载成品：整页不可读（可再用图片查看器确认纯黑）。
5. 右侧清单「整图脱敏」行 →「撤销」：回到 0 处、可重新导出（未涂黑版）。
6. 打开一个 DOCX：按钮为禁用态，悬停提示「整页涂黑只对图片、扫描件、PDF 有效…」；PDF 则可用。
7. 键盘：Tab 聚焦按钮 → Enter 或 Space 打开确认 → Tab 到「确认涂黑整页」→ Enter。

## 8.5 红线 / 兼容与风险

- **无外呼 / 无 CDN / 无第三方库 / 无新依赖**：纯原生 JS + 少量 CSS；新代码只调同源 `/api/*`；toast 文案为固定字符串或后端 message（经 `cleanMessage`），无 HTML 注入。
- **不改后端/规则/HTML**：复用现有 `/mark` 与 `/entities/{eid}`；按钮动态插入，图标行与页面结构未动。
- **既有功能不回退**：首次导出、媒体二次确认、框选补漏、类型映射、4 页面加载均不受影响；`regression.py` 实测 **164 项硬检查全绿**（e2e 5/5、4 页面+3 图标 200、主路径导出/下载/审计/还原全过），仅 5 条与机器负载有关的软提示（e2e 37.05s、名片解析 9933ms 等，功能结果一致）。
- 兼容风险：
  1. **格式门禁依赖预览 `kind`**：若未来新解析器返回 flow 但实际可渲染，按钮会保守禁用（不会造成误涂）；在 parser 上声明 `can_render` 后自动放开。
  2. **撤销只对 `origin="manual"` 的条目显示**（整图涂黑 / 框选补漏）；自动识别条目仍保持原行为（无撤销 UI）。
  3. **计数口径**：`readCounts` 现在扣除 `rejected`——只有通过本功能撤销时才会出现 rejected，既有流程数字不变。
  4. **单页操作**：整图涂黑只作用于当前页；多页文件需逐页点击（符合 P0「头/整图」的最小实现）。
  5. **黑块颜色**为导出器统一的 `--black #0D0B09`（非纯 #000），与品牌一致且同样不可读。
  6. **PDF / 图片重编码**：沿用现有导出路径（PDF apply_redactions、图片重编码去 EXIF），未新增改动。

---

# 第三轮（P0）：证件脱敏选择器（关键字段 / 整卡）

> 状态：已实现 · 2026-10-20 · 负责人：designer-icon（task-23）
> 背景：用户选用方案 A —— 身份证默认「关键字段」脱敏（只遮头像/姓名/身份证号/住址），整卡涂黑可选。Lead 已在 `POST /api/jobs` 增加 `id_card_mode: "fields" | "whole"`（默认 fields）。
> 代码：`app/web/app.js`（+106/−2）、`app/web/style.css`（+19）；**HTML / 后端 / 规则全部未改**；未 push。

## 9.1 改动点

| 位置 | 改动 |
|---|---|
| `state.idCardMode`（默认 `'fields'`） | 新增档位状态；`normalizeIdCardMode(v)` 只接受 `whole`，其它一律回 `fields`（非法值兜底） |
| `mountIdCardMode()` / `buildIdModeBlock()` | **不改 index.html**，动态挂两处：①投放台「选择文件」按钮下方（完整版：标题 + 两档按钮 + 说明文案）；②对照台头部「严格度」左侧（紧凑版：`证件脱敏 [关键字段][整卡涂黑] 本次任务：…`） |
| `setIdCardMode()` / `syncIdCardModeUI()` | 两处选择器同步（`aria-pressed`）；任务已创建后再改**只 toast 提示「已识别的任务不会重跑，重新投放文件后生效」**，不自动重跑；任务未创建时不打扰 |
| `createJob()` | `POST /api/jobs` body 增加 `id_card_mode: normalizeIdCardMode(state.idCardMode)`；响应里回填的 `id_card_mode` 也同步回状态 |
| `startFlow()` | 识别日志加一行「证件脱敏：关键字段/整卡涂黑（含义）」，让用户在扫描页就知道当前档位 |
| `updateConsoleHead()` | 每次刷新时同步头部紧凑选择器与「本次任务：X」提示（来自 `job.id_card_mode`） |
| `TYPE_CN / TYPE_ORDER / TYPE_COLOR` | 新增 `ID_PHOTO 证件头像`、`ID_NAME 证件姓名`、`ID_NUMBER 证件号码`、`ID_ADDRESS 证件住址`，排在 `ID_DOCUMENT` 之后；各配 muted 分组色（`#8E4A3C / #5A4A3F / #2F4F66 / #7A6A4A`），清单/日志不再落英文或兜底「其他敏感」 |
| `style.css` | `.idmode`（投放台完整版：标题/分段按钮/说明）与 `.idmode.compact`（对照台头部紧凑版）、`.idmode-job`；沿用朱红选中态与纸面风格 |

**未改**：`index.html`、`api.py`、`jobs.py`、`visual_targets.py`、规则包、解析器；未新增依赖、未外呼、未 push。

## 9.2 接口契约

```jsonc
// POST /api/jobs（创建任务，现有接口，Lead 已加字段）
{ "upload_id": "u_xxx", "strictness": "standard", "id_card_mode": "fields" }   // 默认
{ "upload_id": "u_xxx", "strictness": "standard", "id_card_mode": "whole" }    // 整卡
// → {"job_id":"j_xxx","name":"…","fmt":"image","id_card_mode":"fields"}
```

- 前端 `normalizeIdCardMode()` 对非法/缺省值兜底 `"fields"`；后端同样兜底（双保险）。
- fields（后端实测，用户测试集 #85 正脸身份证）：实体为 `ID_PHOTO / ID_NAME / ID_NUMBER / ID_ADDRESS` 四类（+ 原有 OCR 地址等），预览框面积分别约 21.3% / 6.1% / 16.5% / 20.7%，**无整页框**。
- whole：实体为 `ID_DOCUMENT` 整卡/整页（面积 100%），预览整页覆盖。
- 任务快照 `GET /api/jobs/{id}` 含 `id_card_mode`，前端据此显示「本次任务：X」。

## 9.3 实测（真实 HTTP + CDP 驱动真实 Edge，25 项断言 ALL PASS）

测试样本：`C:\Users\Dante\Desktop\测试集\微信图片_20261009205811_85_29.jpg`（用户测试集 #85 正脸身份证，**只读**，未修改）。服务：临时数据目录 + `python run.py --port 18765 --no-browser`。

| 断言 | 实测 |
|---|---|
| 投放台选择器 | 位于「选择文件」下方；标题「证件脱敏（身份证）」；两个按钮「关键字段（默认）」「整卡涂黑」；默认 `aria-pressed` 关键字段=true；说明文案含「只遮头像 / 姓名 / 身份证号 / 住址」「整卡不透明覆盖（公开 / 不确定场景推荐）」 |
| 键盘可访问 | CDP 真实按键：Enter / Space 均能切换档位（`aria-pressed` 同步） |
| 默认 fields 创建 | `POST /api/jobs` body = `{"upload_id":"u_…","strictness":"standard","id_card_mode":"fields"}`；job 快照 `id_card_mode=fields`；头部显示「本次任务：关键字段」 |
| 新类型显示（真实后端） | 清单分组：`证件头像×1 / 证件姓名×1 / 证件号码×1 / 证件住址×1`（另有地址×1），**无「其他敏感」兜底** |
| fields 预览 | 4 个证件字段框（面积 21.3%/6.1%/16.5%/20.7%），**0 个整页框** |
| fields 导出 | human 成品 1024×1821：黑像素占比 **50.9%**、平均亮度 88.5 → 关键字段被涂、卡面其余仍可读 |
| 创建后切整卡 | toast「…已识别的任务不会重跑，重新投放文件后生效」；`POST /api/jobs` 次数不变（0 次新请求）；job 快照仍 fields |
| 重新投放 → whole | body 带 `"id_card_mode":"whole"`；快照=whole；头部「本次任务：整卡涂黑」 |
| whole 预览/导出 | 预览出现整页级覆盖框；human 成品 1024×1821：黑像素占比 **100%**、平均亮度 11.07 → 整页不可读 |
| 类型映射 | ID_* 中文名齐全、进入 TYPE_ORDER、各有分组色；`normalizeIdCardMode('bogus')` 与 `undefined` 均兜底 `fields` |
| 页面回归 | `/`、`/download`、`/mcp`、`/console` 全部加载，全程 **0 JS 异常**；`node --check app/web/app.js` exit 0 |

证据（临时目录 `%TEMP%\yz_idmode_test\`）：

- `ui_idmode_drop.png`：投放台选择器（默认关键字段）
- `ui_idmode_fields_real.png`：fields 真实任务——头部档位「本次任务：关键字段」、清单 4 类证件字段、预览 4 个字段框
- `ui_idmode_switch_hint.png`：创建后切整卡的「重新投放生效」提示
- `ui_idmode_whole.png`：whole 任务整卡/整页覆盖
- `evidence_fields_vs_whole.png`：原图 / fields 成品 / whole 成品 三联图（附黑像素占比）

## 9.4 手动测试步骤

1. 起服务（`cd app/server && python run.py --port 8760`），打开 `http://127.0.0.1:8760/console`。
2. 投放台「证件脱敏（身份证）」保持「关键字段（默认）」（或点「整卡涂黑」），选一张身份证照片拖入。
3. 识别完成进对照台：头部应显示「本次任务：关键字段」；右侧清单应有「证件头像 / 证件姓名 / 证件号码 / 证件住址」等分组；左栏原件上 4 个虚线字段框，右栏相应位置被涂（换名模式为白块，涂黑模式为黑块），**整页不黑**。
4. 「交付 → 给人看」导出，成品只有关键字段不可读，卡面其余仍可读。
5. 对照台头部把档位切到「整卡涂黑」：只弹提示「已识别的任务不会重跑，重新投放文件后生效」，当前任务不变。
6. 刷新页面回投放台 → 选「整卡涂黑」→ 重新拖入同一张身份证 → 预览整卡/整页黑；导出后整页不可读。
7. 键盘：Tab 到档位按钮，Enter / Space 可切换。

## 9.5 红线 / 兼容与风险

- **无外呼 / 无 CDN / 无第三方库 / 无新依赖**；只调同源 `/api/*`；文案固定字符串，无 HTML 注入。
- **不改后端/规则/HTML**：`id_card_mode` 由 Lead 的后端实现，前端只透传与展示；选择器由 JS 动态挂载（投放台 + 对照台头部），页面结构未动。
- **非法值双保险**：前端 `normalizeIdCardMode` 兜底 fields；后端 api/jobs 也会兜底（已核对）。
- 兼容风险：
  1. **已创建的任务不重跑**（按需求）：切档位只影响下一次投放；如果用户在同一任务里改了选择器，需要重新投放/刷新页面重新拖入才生效（提示已写明）。
  2. **后端字段定位失败时**会按整卡涂黑并给 warning（`jobs._visual_entities` 的 fallback 逻辑），前端无需特殊处理，清单会显示「证件」整卡框——预期降级，不报错。
  3. **ID_* 为新 etype**：旧版后端不返回这些类型时，前端映射多出来的键无害；旧数据只显示原有类型。
  4. **紧凑选择器占用对照台头部空间**：`console-head` 本身 `flex-wrap:wrap`，窄屏会自动换行；1360px 实测布局正常。
  5. **strictness 变更会重跑任务**：`changeStrictness` 走原有重新建任务路径，此时会把当前 `id_card_mode` 一起带上（与档位选择一致，属合理行为）。
  6. **证件反面**：关键字段模式下后端会 warning 提示「反面无字段可脱敏，如需整卡请切换」，前端照常展示 warning（入口在导出前 toast / 扫描日志），无额外改动。
