# 陌生客户交付、试用与授权收费 SOP（v1.3.1）

- 日期：2026-10-29
- 适用：把「数据隐站」以 Windows EXE（安装包/绿色版）和 MCP（`Sanitized_Data_MCP.exe` + DSH 插件）交付给陌生客户
- 依据：`docs/impl/licensing.md`、`docs/impl/free-tier.md`、`docs/impl/mcp-monetization.md`、`docs/research/13-MCP服务定价与收费管理系统调研.md`、`docs/research/14-结果付费方案设计与对比.md`、`deploy/license_issue.py`

---

## 0. 先说结论

1. **「每天 3 份」不需要写新代码**：正式构建（非 TestMode）里 `PAYWALL_ENABLED=True` + 打包后 `quota.is_enforced()` 自动为真，免费版就是 **3 份/天、20 页、OCR 10 页、common 规则包**。
2. **不要给陌生客户发当前 `客户测试版_v1.3.1`**：那是 TestMode 构建（`PAYWALL_ENABLED=False`，全功能、无 3 份限制），只能用于朋友内测。
3. **代码不泄露 ≠ 不可逆向**：PyInstaller 已能做到安装包里没有 `.py` 源码（当前 v1.3.1 绿色版审计：产品源码 0、映射密钥 0、测试数据 0），但 Python 字节码理论上可被解包。陌生客户 + 单客户场景，重点应放在 **私钥不进包 + 机器码授权 + 客户可追溯**，更高要求再上 Nuitka/PyArmor。
4. **收费走离线授权**：客户报机器码 → 你用私钥签发 `license.json`（Ed25519 签名、机器码绑定、有效期/功能位）→ 客户导入即解锁，全程不联网。续费/换机重新签发，到期回免费版，14 天宽限，数据不删。
5. **MCP 不单独卖**：MCP 是专业版功能位（`mcp`）。免费只给只读工具（`list_rules/check_support/redact_text`），文件写操作/Job/导出/还原/审计需要试用或付费授权。

---

## 0.1 当前决策（2026-10-29，按业务方确认）

- 陌生客户先走 **「14 天全功能试用」**：客户端自助启用（`/license` → 「开始 14 天全功能试用」），**不需要发机器码、不需要签发 license 文件**；
- **暂不启用「试用也 3 份/天」的限制**（业务方在协商）；免费版 3 份/天仅作为**试用到期后的兜底**；
- 已用正式构建实测：试用启用后 `state=trial`、`features` 含全部规则包/TIF/3D/MCP/审计导出、`limits=null`（不限份数）；到期自动回 free（3 份/天），数据不删；
- 如果后续需要「机器绑定、防重置」的试用：可签发 **14 天 Pro 授权**（SKU=pro, `--days 14`）。注意年费授权到期后有 14 天宽限，实际会多 14 天；要严格 14 天可把 `--days` 调小或接受宽限；
- 试用状态是本地计数（删数据目录/重装可重置），产品设计上接受该风险；付费正式授权用机器码绑定，不受影响。

---

## 1. 交付物清单（给陌生客户）

| 文件 | 说明 |
|---|---|
| `Sanitized_Data_Setup_<ver>_x64.exe` | 正式安装包（含主程序 + MCP EXE + dsh-plugin + 运行库） |
| `Sanitized_Data-<ver>-win64-portable.zip` | 绿色版（免安装） |
| `Sanitized_Data_MCP.exe` | MCP stdio 服务（在安装目录内，也可单独拷贝配置） |
| `dsh-plugin/` | DSH 桌面插件（可选，随包分发） |
| `license.json`（试用/付费时） | 你签发的离线授权文件（按客户机器码） |
| `客户测试说明.txt` | 安装/试用/功能边界说明（注意：不能再说“测试版全功能”） |
| `SHA256SUMS.txt` | 产物校验值 |

**不要给**：源码目录、`desktop/build_win.ps1`、`deploy/`（签发工具）、`app/tests/`、`docs/`、任何 `*.pem` 私钥、`data/`（映射库/审计/成品）、`.git`。

---

## 2. 三种交付模式（按客户阶段选）

### 模式 A：免费版（每天 3 份，长期可试用）
- **构建**：正式构建（不带 `-TestMode`）。
- **授权**：不需要 license，客户安装即进入 `free`。
- **限制**：3 份/天、单份 ≤20 页、扫描件 ≤10 OCR 页、单文件 ≤100MB、仅 common 规则包；MCP 只给只读小工具；TIF/三维/行业规则包/MCP 文件工具不可用。
- **适合**：陌生客户先体验基础脱敏；作为获客漏斗。
- **注意**：免费额度是本地计数，客户删数据目录/重装可重置（离线产品接受该风险；正式版设计里已明确“不做联网校验”）。想更严就改用模式 B 的**签名试用授权**（有固定到期日）。

### 模式 B：14 天全功能试用（推荐给认真评估的陌生客户）
- 客户把 `/license` 页面上的**机器码**（`XXXX-XXXX-XXXX-XXXX`）发给你；
- 你用私钥签发 **trial/试用授权**（绑定机器码、固定到期日）；
- 客户导入后 14 天全功能：全部规则包、TIF/三维、MCP、审计导出。
- 到期自动回 `free`（3 份/天），数据/映射不删。
- 如果希望“试用期也每天 3 份”，把 `licensing/tiers.py` 的 `trial.limits` 设为 `FREE_LIMITS`（改一处即可）；否则试用期是“全功能不限份数”。

### 模式 C：付费正式版
- 客户付款 → 你按 SKU 签发 `license.json`（年费/买断、绑定机器码、功能位）；
- 客户在 `/license` 导入即解锁（部分场景提示重启一次）；
- 年费到期前 30/14/7/1 天提醒；到期后 14 天宽限（功能可用+横幅），之后回免费版；数据保留。
- 买断版：永久可用 + `updates_until` 更新期；更新期后可继续用旧版，升级新版本需续更新费（按你的商业规则）。

---

## 3. 构建与发布前审计（必做）

```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data

# 1) 正式构建（收费门禁开；不要在客户版上用 -TestMode）
powershell -ExecutionPolicy Bypass -File desktop\build_win.ps1

# 2) 发布包审计（源码/密钥/测试数据 + paywall/3份每天 + MCP 工具注册）
python -X utf8 desktop\audit_release.py

# 3) 手动复核（可选）
#    - 干净 Windows VM 安装 → /license 状态：paywall_enabled=true、quota.enforced=true、jobs_per_day=3
#    - 连做 4 个任务，第 4 个应被拒（quota_exceeded）
#    - 断网安装/使用应正常；MCP 能被 Claude/Cursor/DSH spawn
```

`desktop/audit_release.py` 会检查：
- **禁止文件**：`_build_flags.py`、`yinzhan/*.py`、`mcp_server.py`、`app/server/`、`app/tests/`、`master.key`、映射库、运行数据、私钥、签发工具、`.git`；
- **必备文件**：主 EXE、MCP EXE、`public_keys.json`、规则包、`web/restore.html/js`（且含左右对照）；
- **冒烟 A**：启动打包 EXE（临时数据目录/端口）→ 检查 `paywall_enabled=true`、`quota.enforced=true`、`jobs_per_day=3`；
- **冒烟 B**：启动 MCP EXE → `initialize` + `tools/list` → 确认 `restore_sanitize_file` 等工具已注册。

> 产物：`dist\Sanitized_Data\`、`dist_installer\Sanitized_Data_Setup_<ver>_x64.exe`、`out\desktop\Sanitized_Data-<ver>-win64-portable.zip`。
> 版本号在 `app/server/yinzhan/config.py` 和 `desktop/installer.iss`，发版前同步。

### 3.1 本机已完成的 v1.3.1 正式构建与验证（2026-10-29）

| 项目 | 结果 |
|---|---|
| 构建命令 | `desktop\build_win.ps1`（**未带 `-TestMode`**） |
| 安装包 | `Sanitized_Data_Setup_1.3.1_x64.exe`，162.3 MB，sha256 `6d359c1f70bd108d988a2d6fc144f450b526ed19aba7d5b6a53bcfa15dd88c09` |
| 绿色版 | `Sanitized_Data-1.3.1-win64-portable.zip`，209.8 MB，sha256 `35c63374747c51b44e653c72d789a8ea3fafac27779640f83d33367c4757a01c` |
| 源码/密钥泄漏审计 | **0**（产品 `.py`、`master.key`、映射库、`data/`、测试、私钥、签发工具、`.git` 均不在包内） |
| 收费门禁 | `paywall_enabled=true`、`build_channel=release`、`quota.enforced=true` |
| 免费额度实测 | 连续建 4 个任务：**1/2/3 成功，第 4 个 403**；`used_today=3, remaining=0` |
| MCP | `Sanitized_Data_MCP.exe` initialize OK，16 个工具，`restore_sanitize_file` 已注册 |
| 回归测试 | `test_restore.py` 18/18、`test_mcp_restore.py` 12/12 |
| 包内控制台 | `restore.html/js` 已含左右对照（`compareBox` / `renderCompare`）与红色下载按钮 |

> 证据文件：`dist_installer\release_verification_v1.3.1.json`、`dist_installer\SHA256SUMS-v1.3.1-release.txt`、`dist_installer\客户交付说明_v1.3.1_正式版.txt`。
> 注意：`dist_installer\客户测试说明_v1.3.1.txt` 是旧 **TestMode** 测试包说明，**不要发给陌生客户**。

> 已知环境限制：在 DSH 本机沙箱里用 Python 子进程启动打包 EXE 做冒烟时，子进程创建数据目录会被沙箱拒绝；本次 paywall/额度/MCP 冒烟是改用 PowerShell/.NET 启动方式手动验证通过的。普通 Windows 客户机/干净 VM 上 `audit_release.py` 预期可全绿。

---

## 4. 代码防泄露：分级方案

| 级别 | 做法 | 成本 | 说明 |
|---|---|---|---|
| **L0（当前，PyInstaller）** | 正式构建；安装包不含 `.py` 源码、不含测试/文档/私钥/运行数据；代码签名；客户专属 license（含客户名/订单号） | 低 | 已能挡住 99% 普通用户；**Python 字节码可被解包**，DLL/模型也可提取，但业务逻辑要还原有成本 |
| **L1（建议至少做）** | 在 L0 基础上：① 每客户独立 license（customer_name/order_id 入库，泄露可追溯）；② 安装包/导出文件名加客户标识（可选）；③ 核心模块用 Cython 编译成 `.pyd`（license/quota/vault/pipeline）；④ 混淆 Web 前端 JS（restore.js/app.js） | 中 | Cython 对纯 Python 模块效果有限但提高逆向成本；不改变功能 |
| **L2（值得做给 Pro/机构版）** | Nuitka 编译成 C（`--standalone`）或 PyArmor 混淆 + 绑定；关键算法/规则库加密加载 | 中高 | 需要重新适配打包（webview/onnxruntime 等依赖要实测）；Nuitka 编译时间较长 |
| **L3（不建议现在做）** | 核心能力云化（规则/模型在服务器，客户端只做 UI） | 高 | 与“本机处理、不联网”卖点冲突；机构版可做私有化服务端 |

**私钥纪律（最重要）**：
- Ed25519 **私钥只在你自己的离线机器/加密介质**，口令通过 `--password-env` 传入，绝不写进安装包、源码仓库、MCP 插件；
- 客户端只内置**公钥**（`app/server/yinzhan/licensing/public_keys.json`，已打包进 EXE）；
- 建议私钥单独备份（离线 U 盘/密码管理器），签发记录进订单台账；
- 每客户一 license_id，泄露可定位；退款/纠纷可依据审计。

> 现实判断：陌生客户 + 单客户，主要风险不是他逆向，而是**他把安装包转发给别人**。对策是：付费功能必须 license（机器码绑定）；免费版转发只能得到 3 份/天；再加客户标识和更新服务，基本够用。真正要做“防逆向”再上 L1/L2。

---

## 5. 签发试用/正式授权（离线）

### 5.1 客户侧
1. 安装正式版（免费模式可用）；
2. 打开 `/license` → 复制**机器码**（形如 `ZH39-7N5C-98CD-A8GB`）发给你；
3. 收到 `license.json` 后在 `/license` 导入。

### 5.2 你侧（签发）
```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
# 试用（示例：14 天，全功能；trial 属早期渠道/内部签发，若 CLI 只支持 4 个 SKU，
# 可用 personal/pro 的 --days 14 作为“试用授权”，到期后自然回 free）
python -X utf8 deploy\license_issue.py `
  --private-key D:\secure\prod_private_key.pem `
  --key-id prod-2026 `
  --sku pro `
  --machine-code "ZH39-7N5C-98CD-A8GB" `
  --order-id "PILOT-20261029-001" `
  --term annual --days 14 `
  --customer-name "张三" --customer-org "XX公司" `
  --notes "陌生客户试用 14 天" `
  --password-env YZ_LICENSE_KEY_PASS `
  --out D:\orders\PILOT-20261029-001\license.json
```
> 试用用 `--days 14` + 明确 notes；正式年费用 `--term annual --days 365`；买断用 `--term perpetual`（不填 days），更新期按你的规则。
> 签发前先让客户确认机器码；**一单一 license_id**；签发记录（客户/订单/机器码/到期/功能）入订单台账。

### 5.3 验证
- 客户导入后 `/license` 显示 edition/到期/功能位；
- 用付费功能（行业包/TIF/MCP 文件工具）确认解锁；
- 到期后确认回 free 且 3 份/天生效。

---

## 6. 定价与收费（沿用现有调研结论，建议首发起步价）

| 版本 | 价格 | 设备/席位 | 功能 |
|---|---|---|---|
| 免费版 | ¥0 | 1 台 | 3 份/天、20 页、common 包；MCP 只读工具 |
| 试用 | 免费 14 天 | 1 台 | 全功能（全规则包/TIF/3D/MCP/审计导出） |
| 个人版 | **¥199/年**（早鸟 ¥99；买断 ¥499） | 1 台 | 全规则包、TIF/3D、OCR；不含 MCP |
| 专业版 | **¥499/年**（早鸟 ¥299；买断 ¥999） | 2 台 | 个人版 + MCP（文件脱敏/复核/导出/还原/审计）+ 批量 |
| 团队版 | **¥1,999/年/5 席**（建议加 10 席 ¥3,999） | 按席位 | 专业版 + 团队管理台 + 团队审计导出 |
| 机构版 | **¥3.88 万起**（含私有化/对接） | 1 台服务器 + 合同席位 | 团队版 + 机构部署 + 对接；MCP 私有化接入包 ¥1.5–3 万 |

- **规则**：不做按次/按文件计费；不做自动续费；MCP 不单独卖 SKU。
- **促销**：早鸟价限首批（如 300 份）；老客户续费恢复标准价；教育/公益可申请折扣。
- **发票/合同**：个人版可开电子普票（视主体资质）；团队/机构签合同、对公转账、开专票。

### 收款流程（先手动，跑通 10–20 单再自动化）
1. 客户下单（微信/闲鱼/淘宝/官网表单）→ 收集机器码 + 开票信息；
2. 收款（微信/支付宝/对公）→ 订单台账登记（order_id、客户、SKU、金额、机器码、license_id、到期）；
3. `license_issue.py` 签发 → 发 `license.json` + 安装包/使用说明；
4. 到期前 30/14/7 天提醒续费（可用台账脚本）；续费重新签发新 license；
5. 售后：换机（每年 1 次免费，需说明原因/旧机器码）、退款（未使用 7 天内可按比例，写进条款）、授权找回（凭订单+机器码）。

### 后期自动化（有量后再做）
- **授权服务端**：私钥在服务器（HSM/加密机更好），`/api/license/issue` 由订单支付回调触发，自动签发 license，客户后台自助下载/换机；
- **支付**：微信/支付宝商户号（需个体户/公司资质，申请周期 1–2 周）或国际 Paddle/LemonSqueezy；
- **官网购买入口**：`/pricing` 静态页 + 支付二维码/链接（当前为占位，`YINZHAN_PURCHASE_URL` 可配）；
- **MCP 分发**：DSH 插件市场/GitHub Release 发插件 zip + 说明“需 Pro 授权”；安装包不自带 license。

---

## 7. 风险边界（要接受 & 要防的）

| 风险 | 现状/对策 | 是否接受 |
|---|---|---|
| 客户反编译 Python 字节码 | PyInstaller 可被解包；L1/L2 提高成本；核心是 license 与更新服务 | **接受**（低客单价，不值得重投入） |
| 客户转发安装包 | 免费版转发只得 3 份/天；付费功能绑机器码 | 接受 |
| 删除数据目录重置免费额度 | 离线计数可重置；改用签名试用授权（固定到期日）更稳 | 接受（免费是漏斗） |
| 伪造 license | Ed25519 签名 + 公钥内置 + payload 全字段校验 | 风险极低 |
| 改系统时钟续试用 | `effective_now=max(now,last_seen)`、回拨标记、到期按事实处理 | 接受 |
| 私钥泄露 | 私钥离线+口令+备份；泄露则换 key_id/发版更新公钥 | 必须防 |
| 安装包被篡改 | 代码签名（Authenticode）/SHA256 校验和/HTTPS 下载；后续可加签名更新清单 | 建议做（证书约 ¥1–3k/年） |

---

## 8. 给陌生客户的第一封交付话术（模板）

> 您好，附件是「数据隐站」安装包（Windows 10/11 x64）和使用说明。
> 1）安装后可直接用**免费版**：每天 3 份、单份 20 页，仅通用规则包；
> 2）如需完整体验（全部行业规则包/TIF/三维/MCP），把软件「授权」页的**机器码**发我，我给您开通 **14 天全功能试用**；
> 3）软件全部在本机处理、不上传文件；试用/付费授权是离线文件，导入即可；
> 4）安装包未做代码签名，SmartScreen 提示时点「更多信息→仍要运行」；SHA256 校验值见附件。
> 有问题随时发我日志/截图。

---

## 9. 待办清单（交付前）

- [ ] 用正式构建（**不要 -TestMode**）重新打包，版本号与 installer.iss 同步；
- [ ] 跑 `desktop\audit_release.py` 全绿（源码 0、密钥 0、paywall 开、3 份/天、MCP 工具全）；
- [ ] 干净 Windows VM 实测安装/卸载/断网/额度/MCP spawn；
- [ ] 给安装包做代码签名（可选但强烈建议）；
- [ ] 准备订单台账模板、license 签发脚本路径、私钥离线备份；
- [ ] 更新「客户测试说明」为正式版文案（去掉“测试版关闭收费门禁”）。
