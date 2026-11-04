# 数据隐站 L3 付费解锁 · 端到端验收报告（T4 / QA）

| 项目 | 内容 |
|---|---|
| 任务 | `task-4` L3 端到端验收 + 一键演示 + QA 报告（团队任务板） |
| 执行人 | `qa-e2e`（T4 QA/集成工程师） |
| 日期 | 2026-11-02 |
| 工作目录 | `D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data` |
| 依据 | `commercial/CONTRACT.md`（§2/§3/§4/§8/§9）、`commercial/README.md`、`commercial/docs/alipay-setup.md` |
| 被测对象 | T1 商业服务（mock 全链路/DB/静态页）、T2 支付宝适配器、T3 本机购买页/自动解锁 |
| 结论 | **通过**：E2E 23/23 检查点 PASS；全量 pytest 139 passed（含 62 subtests）；支付宝异步通知 6 类场景 PASS；未发现 T1/T2/T3 功能缺陷；真实支付宝联调与真实浏览器交互未覆盖（见 §6/§7 风险）。 |

---

## 0. 结论摘要

| # | 验收项 | 命令/方式 | 结果 |
|---|---|---|---|
| 1 | 测试密钥生成（Ed25519 `test-2026` + 应用/支付宝两组 RSA） | `gen_test_keys.py` | ✅ 6 个文件；幂等（二次 0 新增 6 复用）；`--force` 可轮换；不打印私钥 |
| 2 | mock 全链路（真实 HTTP 子进程） | `e2e_demo.py` 23 检查点 | ✅ 23/23 PASS，约 6.2s |
| 3 | 本机自动解锁（`/api/license/status` → 下单 → 模拟支付 → `/api/license/import`） | `e2e_demo.py` | ✅ `state=paid` / `edition=专业版` |
| 4 | 支付宝 notify 真实 HTTP（page 模式 + RSA2 独立签名） | `e2e_demo.py` 第二段 | ✅ 成功签发/重复幂等/金额篡改 failure/WAIT_BUYER_PAY 不签发 |
| 5 | 安全/边界（CORS、token 404、过期订单、mock 禁用） | `e2e_demo.py` | ✅ 全部符合契约 |
| 6 | pytest 回归（T1/T2/T3 + T4） | `pytest commercial/tests app/tests/test_purchase_static.py` | ✅ 139 passed / 62 subtests / 10.69s |
| 7 | JS 语法 | `node --check`（7 个文件） | ✅ 全部 OK |
| 8 | 一键演示脚本 | `demo_up.ps1 -ValidateOnly` / `-NoBrowser -DurationSeconds 8` | ✅ 校验通过；真实起两服务→健康→`/purchase` 200→清理，退出码 0 |
| 9 | 安全/卫生 | git / ignore / 外呼 / 公钥 env 检查 | ✅ 见 §5（含沙箱环境 2 个已知坑） |
| 10 | 缺陷 | — | 未发现 T1/T2/T3 缺陷；T4 自查修复 1 个自身脚本缺陷（§7.1）；2 个环境限制（§7.2） |

---

## 1. 交付物（T4 只写这些文件）

| 文件 | 说明 |
|---|---|
| `commercial/scripts/gen_test_keys.py` | 生成/复用 L3 测试密钥：Ed25519 license 私钥 + 产品公钥表 JSON + 应用 RSA 对 + 支付宝 RSA 对；幂等、`--force`、`--json`、不打印私钥 |
| `commercial/scripts/e2e_demo.py` | 真实 HTTP 端到端验收/演示：起商业 mock 服务 + 本机 app + 支付宝 page 服务，跑 23 个检查点；`--keep-tmp` / `--json`；可被 pytest 调用（`run_e2e()`） |
| `commercial/scripts/demo_up.ps1` | Windows 一键演示：自动补测试密钥 → 起两服务 → 等健康 → 打开 `/purchase` → 打印 PID/停止方法；Ctrl+C 清理；`-ValidateOnly` / `-NoBrowser` / `-DurationSeconds` 自动化参数 |
| `commercial/tests/test_e2e_mock.py` | pytest 版 E2E：复用 `e2e_demo.run_e2e()`，子进程真实 HTTP；模块级只起一次服务；另含 `gen_test_keys` 幂等/格式单测 |
| `commercial/QA.md` | 本报告 |

> 未改动 T1/T2/T3 任何文件、未改产品 `app/` 业务代码、未提交 git。
> 证据：会话开始（18:16）后 `commercial/`、`app/` 下仅上述 4 个代码文件 mtime 更新（§5.1）。

---

## 2. 环境

| 项 | 值 |
|---|---|
| OS | Windows 10 Pro（`Microsoft Windows NT 10.0.19045.0`，中文系统） |
| Python | `C:\Users\Dante\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe` → Python 3.12.14（MSC v.1944 64-bit） |
| 依赖 | fastapi 0.136.1 / uvicorn 0.46.0 / cryptography 48.0.0 / httpx 0.28.1 / pytest 9.1.0 |
| Node | `C:\nvm4w\nodejs\node.exe` → v22.22.3 |
| PowerShell | Windows PowerShell 5.1.19041.6456（`powershell -ExecutionPolicy Bypass -File ...`） |
| 浏览器 | 手动演示用系统默认浏览器；**测试未做浏览器自动化**（无 Playwright/Selenium 依赖） |
| 端口 | 商业 mock **8788**、商业 alipay **8789**、本机 app **8799**（避开产品默认 8787/8760；被占用时 `e2e_demo.py` 自动顺延到 8788-8839 空闲端口） |
| 临时目录 | 仓库内 `commercial/var/_e2e_tmp/`（gitignore；系统临时目录在受限沙箱下不可写） |

### 2.1 沙箱/环境已知坑（本次均绕过，记录给后续维护者）

1. **系统 tmp 不可写**：`pytest tmp_path` / `%TEMP%` 在受限沙箱下可能拒绝访问 → T1 conftest 与 T4 脚本统一使用仓库内 `commercial/var/_pytest_tmp`、`commercial/var/_e2e_tmp`（gitignore）。
2. **不能用 `--reload`**：uvicorn reload 依赖 multiprocessing 命名管道，受限沙箱报 `CreateNamedPipe PermissionError` → T4 起服务一律不带 `--reload`。
3. **`.pytest_cache` 只读告警**：`Sanitized_Data/.pytest_cache` 被 ACL 拒绝，pytest 报 1 条 `PytestCacheWarning`；**不影响测试结果**（可用 `-p no:cacheprovider` 消除）。
4. **进程环境块大小写重复**：本机 shell 环境同时存在 `NO_PROXY`/`no_proxy`、`HTTP_PROXY`/`http_proxy` 等；Windows PowerShell 5.1 的 `Start-Process` 复制这种环境块会抛 `Item has already been added. Key in dictionary: 'NO_PROXY'`。`demo_up.ps1` 内置 `Remove-CaseDuplicateEnvVars` 去重后启动（§4.8）；Python 的 `os.environ` 天生按大小写不敏感去重，`e2e_demo.py` 不受影响。
5. **8787 被其它服务占用**：T4 所有脚本避开 8787，默认 8788/8789/8799。

---

## 3. 复现命令（从 `Sanitized_Data` 目录执行）

```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
$py = "C:\Users\Dante\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

# ① 生成/复用测试密钥（幂等；--force 才轮换）
& $py -X utf8 commercial\scripts\gen_test_keys.py

# ② 真实 HTTP 端到端（起 3 个进程，23 检查点；约 6-10s，自动清理）
& $py -X utf8 commercial\scripts\e2e_demo.py

# ③ 全量回归（T1/T2/T4 商业测试 + T3 购买页静态/契约测试）
& $py -X utf8 -m pytest commercial\tests app\tests\test_purchase_static.py -q

# ④ JS 语法
& "C:\nvm4w\nodejs\node.exe" --check app\web\purchase.js
#（commercial/server/static/*.js 一并检查，见 §4.2）

# ⑤ 一键演示（交互；浏览器打开 /purchase）
powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1

# ⑥ 演示脚本非交互自检
powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1 -ValidateOnly
powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1 -NoBrowser -DurationSeconds 8
```

> 若脚本从其它 cwd 运行也可：路径全部基于 `__file__`/`$PSScriptRoot` 推导（已用「工作区根目录」实测 `e2e_demo.py --quiet --json` 通过）。

---

## 4. 测试结果

### 4.1 全量 pytest（真实结果）

```text
$ python -X utf8 -m pytest commercial/tests app/tests/test_purchase_static.py -q
139 passed, 1 warning, 62 subtests passed in 10.69s   [exit 0]
```

| 测试文件 | 用例数 | 归属 |
|---|---:|---|
| `commercial/tests/test_orders.py` | 38 | T1 商业服务（目录/下单/查询/mock 支付/幂等/过期/CORS/静态页） |
| `commercial/tests/test_signer.py` | 15 | T1 签发 + 产品 `LicenseService` 导入 |
| `commercial/tests/test_alipay.py` | 61 | T2 支付宝 RSA2/预下单/验签/通知 |
| `commercial/tests/test_e2e_mock.py` | **10（T4 新增）** | T4 真实 HTTP E2E（内部跑 23 检查点）+ 测试密钥单测 |
| `app/tests/test_purchase_static.py` | 15 | T3 购买页/路由/公钥 env 覆盖 |

- 基线 114（T1+T2）+ T3 15 + T4 10 = **139 passed**，无失败/错误/跳过。
- 1 条 warning 为沙箱 `pytest cache` 写权限警告（见 §2.1-3），非测试失败。
- `test_e2e_mock.py` 的 10 个用例共享一次模块级 E2E（只起一轮服务），避免重复启动进程。

### 4.2 JS 语法检查

```text
node --check app/web/purchase.js                OK
node --check app/web/license.js                 OK
node --check app/web/pricing.js                 OK
node --check commercial/server/static/buy.js    OK
node --check commercial/server/static/order.js  OK
node --check commercial/server/static/mock_pay.js OK
node --check commercial/server/static/common.js OK
```

### 4.3 `gen_test_keys.py` 验收

产物（`commercial/var/test_keys/`，已被 `Sanitized_Data/commercial/.gitignore:2:var/` 忽略）：

| 文件 | 类型 | 用途 |
|---|---|---|
| `license_signing_ed25519.pem` | Ed25519 PKCS8 PEM（无口令） | 商业服务签发 license |
| `license_public_keys_test.json` | `{"test-2026": "<base64 raw 32B 公钥>"}` | 本机 app `YINZHAN_PUBLIC_KEYS_PATH` 覆盖公钥表 |
| `app_private.pem` / `app_public.pem` | RSA 2048（PKCS8 私钥 / SPKI 公钥，均为 PEM） | 商业服务「应用密钥对」：page/precreate 请求签名（生产替换为开放平台密钥） |
| `alipay_private.pem` / `alipay_public.pem` | RSA 2048 | 测试里扮演「支付宝」：用 alipay_private 给 notify 表单签名，服务端用 alipay_public 验签 |

- 首次运行：`新增文件 : 6 个；复用已有 : 0 个`；公钥指纹（仅公钥 SHA256）示例：
  `license SHA256:39a2be4476dfaca9…`、`app RSA SHA256:3198f48825936433…`、`alipay RSA SHA256:9beaee03ba1c63db…`。
- 幂等实测（`--json --quiet`，同一 `--out-dir` 连续两次）：
  `run1 created=6 reused=0` → `run2 created=0 reused=6`，`fingerprints_equal=True`，`license_public_b64_len=44`（32B base64）。
- `--force` 实测：两次均 `新增文件 : 6 个`（轮换成功）；输出仅路径/指纹，**无任何私钥正文**（泄漏检查 `BEGIN ... PRIVATE KEY`/`MIIE` 均未命中）。
- 程序化接口：`ensure_test_keys(out_dir=None, force=False, key_id="test-2026", quiet=False) -> dict`，被 `e2e_demo.py`/`demo_up.ps1` 复用。
- 相对 `--out-dir` 按 cwd 解析（修复记录见 §7.1）。

### 4.4 `e2e_demo.py`：mock 全链路（真实 HTTP，23/23 PASS）

运行输出（最终轮，约 6.2s；完整 PASS 清单见 §9.1）：

```text
L3 付费解锁 · 真实 HTTP 端到端验收
  临时目录 : commercial\var\_e2e_tmp\e2e-20261102-182552-1100
  端口     : 商业 mock=8788 / 商业 alipay=8789 / 本机 app=8799
...
[PASS] 启动商业服务（mock）并等待 /healthz  — http://127.0.0.1:8788/healthz provider=mock
[PASS] 启动本机 app（--no-browser --no-ocr）并等待 /api/health  — version=1.3.4 net=blocked
[PASS] 本机 /api/license/status 提供机器码/commercial_url  — machine_code=ZH39-7N5C-98CD-A8GB state=free
[PASS] 本机购买页 /purchase 可访问（200，纯本地资源）
[PASS] 商业服务 /api/catalog（SKU/价格/通道）  — pro=49900 分，alipay/mock 可用
[PASS] POST /api/orders（pro，qr_svg/order_token）  — qr_svg≈7.4KB
[PASS] POST /api/mock/pay → paid + license.text（单行 JSON）  — edition=专业版
[PASS] 订单查询 + 重复模拟支付幂等（同一 license_id）  — DB payments=1 licenses=1
[PASS] 本机 POST /api/license/import（测试 license → paid）
[PASS] 本机 /api/license/status 复核 paid/专业版
[PASS] CORS 预检：允许本机 Origin（204 + allow-origin）
[PASS] CORS：无关 Origin 不返回 allow-origin
[PASS] token 错误统一 404 order_not_found
[PASS] 过期订单（expires_at 已过）不可支付 → 400 order_expired
...
E2E 结果：23/23 检查点 PASS，耗时 6.2s ✅   [exit 0]
[INFO] 临时目录已清理：True
```

关键断言（全部实测通过）：

| 断言 | 实测证据 |
|---|---|
| 下单响应 `qr_svg` 含 `<svg` | 约 7.4KB 内联 SVG（服务端生成，无外链） |
| license.text 为**单行紧凑 JSON**、`alg=ed25519`、`key_id=test-2026` | `json.loads` 成功且不含换行；payload.order_id=订单号、机器码一致 |
| `/api/license/import` → `state=paid`、`edition=专业版`、`sku=pro` | 本机真实 app 进程（测试数据目录） |
| 幂等：重复 `POST /api/mock/pay` / 重复 GET 同一 `license_id` | DB `payments=1`、`licenses=1` |
| token 错误 → 404 `order_not_found`（防枚举） | 错误 token 请求 |
| 过期订单不可支付 | DB 把 `expires_at` 置为过去 → GET 变 `expired`、mock 支付 400 `order_expired`（等价 15 分钟 TTL 到期，未真实等待） |
| CORS | 允许 `http://127.0.0.1:8760`（204 + `Access-Control-Allow-Origin`）；`https://evil.example.com` 不返回该头 |
| 本机引擎零外呼声明 | `/api/health` 返回 `net="blocked"`（产品自带声明，E2E 断言） |

`--json` 验证（从工作区根目录运行，验证 cwd 无关 + 机器可读摘要 + 敏感字段脱敏）：

```text
[e2e exit=0]
ok=True checkpoints=23 passed=23 duration=6.25s tmp_removed=True
artifacts.license_text=<745 chars>   artifacts.order_token=<43 chars>
```

### 4.5 支付宝异步通知（真实 HTTP）：6 类场景全 PASS

**第二个商业服务（独立进程/独立 DB）配置**（`e2e_demo._start_alipay_service`，`commercial/scripts/e2e_demo.py:507`）：

```text
PAYMENT_PROVIDER=alipay            ALIPAY_PAY_MODE=page
ALIPAY_APP_ID=test-app             ALIPAY_APP_ID_CHECK=1
ALIPAY_APP_PRIVATE_KEY_PATH=commercial/var/test_keys/app_private.pem
ALIPAY_PUBLIC_KEY_PATH=commercial/var/test_keys/alipay_public.pem
ALIPAY_NOTIFY_URL=http://127.0.0.1:8789/notify/alipay
COMMERCIAL_BASE_URL=http://127.0.0.1:8789   PAYMENT_MOCK_ENABLED=false
LICENSE_PRIVATE_KEY_PATH=.../license_signing_ed25519.pem   LICENSE_KEY_ID=test-2026
```

**签名方式（关键设计）**：E2E 用 `commercial/scripts/e2e_demo.py:341` `_rsa2_sign_params()` **独立实现**支付宝 RSA2 规则
（剔除 `sign`/`sign_type`、空值不参与、key 字典序、`k=v&...`、PKCS1v15+SHA256 → base64），
**不调用**被测服务的 `server/payments/alipay.py` 签名函数，避免「自签自验」互相掩盖错误。

通知表单字段：`notify_time / notify_type=trade_status_sync / notify_id / app_id=test-app / charset / version / sign_type=RSA2 / trade_no / out_trade_no / trade_status / total_amount / seller_id / buyer_logon_id`。

| # | 场景 | 请求 | 期望 | 实测 |
|---|---|---|---|---|
| 1 | page 模式下单 | `POST /api/orders`（channel=alipay） | 200，`pay_url` 含 `gateway.do`/`sign=`/`app_id=test-app`，`qr_svg` 有 `<svg>`，`mock_available=false` | ✅ `pay_url≈1031B` 已签名 |
| 2 | 非 mock 部署调用 mock 支付 | `POST /api/mock/pay` | 403 `mock_disabled` | ✅ |
| 3 | 签名后篡改 `total_amount` | 签名后改 `total_amount=0.01` | 200 `failure`（验签失败），订单仍 pending、无 license | ✅ |
| 4 | 签名正确但金额不符 | 用正确密钥签 `total_amount=0.01`（应付 499.00） | 200 `failure`（金额核对失败），订单仍 pending、`licenses=0` | ✅ |
| 5 | `WAIT_BUYER_PAY` | 正确签名、状态 WAIT_BUYER_PAY | 200 `success`（ack），**不签发**，订单仍 pending | ✅ |
| 6 | `TRADE_SUCCESS` | 正确签名、金额 499.00、trade_no 唯一 | 200 `success`，订单 `paid` + `license`（`key_id=test-2026`、机器码一致、`edition=专业版`） | ✅ |
| 7 | 重复通知（幂等） | 原样重发场景 6 | 200 `success`，同一 `license_id`，不重复签发 | ✅ DB `payments=1`、`licenses=1` |
| 8 | 支付宝通道 license 跨通道导入 | 把场景 6 的 `license.text` POST 本机 `/api/license/import` | `state=paid`、`edition=专业版`、`license_id` 一致 | ✅ |

### 4.6 `demo_up.ps1`：Windows 一键演示

**用法**

```powershell
# 交互演示（自动打开浏览器；Ctrl+C 停止并清理子进程）
powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1

# 只做环境自检（不启动服务）
powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1 -ValidateOnly

# 自动化冒烟（起真实服务、等健康、8 秒后自动停止）
powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1 -NoBrowser -DurationSeconds 8
```

**脚本做的事**：自动补测试密钥（缺才生成）→ 校验 Python/依赖/文件/端口 → 重置演示数据目录 →
起商业 mock 服务（8788，测试 license 私钥）→ 起本机 app（8799，`YINZHAN_DATA_DIR` 仓库内临时目录 +
`YINZHAN_PUBLIC_KEYS_PATH` 测试公钥 + `YINZHAN_COMMERCIAL_URL=http://127.0.0.1:8788` + `YINZHAN_PAYWALL=1`）→
等 `/healthz`、`/api/health` → 校验 `/purchase` 200 且含 `purchase.js` → 打开浏览器 →
打印 PID/日志目录/停止命令 → 本窗口 Ctrl+C 时 `finally` 停止两个子进程并删除 PID 文件。

**实测结果**：

- `-ValidateOnly` → 退出码 0，输出「校验通过（-ValidateOnly，未启动任何服务）」。
- `-NoBrowser -DurationSeconds 8` → 退出码 0：两服务 PID 打印、`/healthz` 200、`/api/health` 200、`/purchase` 200、自动停止后 8788/8799 端口均释放（`free`）。
- `-DurationSeconds` 是给自动化验收用的附加开关；交互演示不传即可一直运行到 Ctrl+C。
- 真实生产演示切换说明（脚本内置注释/输出）：商业服务改用生产私钥 `LICENSE_KEY_ID=prod-2026`，本机 app **不要**设置 `YINZHAN_PUBLIC_KEYS_PATH`（用安装包内置 prod-2026 公钥）。

**手动演示步骤（给 Lead/验收人）**

1. 运行 `powershell -ExecutionPolicy Bypass -File commercial\scripts\demo_up.ps1`；
2. 浏览器打开 `http://127.0.0.1:8799/purchase`（脚本自动打开）；
3. 页面显示机器码/未授权状态与 SKU 卡片（专业版 ¥499.00）→ 点「支付宝扫码」；
4. 出现服务端生成的二维码（mock 模式实际由 MockProvider 出演示码）→ 点「演示：模拟支付」；
5. 页面每 2 秒轮询订单 → `paid` 后自动 `POST /api/license/import` → 显示「已解锁 / 专业版」；
6. 回「授权」页 `/license` 可见已授权；Ctrl+C 停止演示。

### 4.7 静态页与合同字段（T1/T3 面）

E2E 顺带验证：`/healthz`、`/api/catalog`、`/api/orders`、`/api/orders/{no}`、`/api/mock/pay`、`/notify/alipay`、
`/purchase`（200、purchase.js/css、SKU 容器、未配置兜底文案）、CORS、错误体 `{ok:false,error:{code,message}}`。
T1 静态页 `/buy.html`、`/order.html`、`/mock/pay` 由 `test_orders.py` 覆盖（38 用例全绿）。

---

## 5. 安全 / 卫生检查

### 5.1 只写了 T4 允许的文件

会话开始（2026-11-02 18:16）后，`commercial/`、`app/` 下（排除 `var/`、`__pycache__`）仅以下文件 mtime 更新：

```text
commercial\scripts\demo_up.ps1       2026/11/2 18:23:53
commercial\scripts\e2e_demo.py       2026/11/2 18:20:37
commercial\scripts\gen_test_keys.py  2026/11/2 18:25:09
commercial\tests\test_e2e_mock.py    2026/11/2 18:21:22
（本 QA.md 随后新增）
```

T1/T2/T3 的代码/页面/测试文件均未被 T4 修改。

`git status --short`（`Sanitized_Data`，核对时点）预期构成：

- `M app/server/yinzhan/{api,config}.py`、`app/server/yinzhan/licensing/keys.py`、`app/web/{license.html,license.js,pricing.js}` → **T3 交付**（本次未动）；
- `?? app/web/purchase.{html,css,js}`、`?? app/tests/test_purchase_static.py` → **T3 新增**；
- `?? commercial/` → T1/T2/T4 新增目录（`git status -uall -- commercial` 共 34 个文件：T1/T2 交付 + T4 的 4 个脚本/测试；本 QA.md 随后新增为第 5 个 T4 交付文件，合计 35 个；`commercial/var/**` 被忽略不在列表）；
- `M docs/ops/*`、`docs/plans/README.md`、`?? deploy/issue_order.py`、`?? docs/ops/正式收款…`、`?? docs/plans/09-…` → **T4 开始前已存在的改动/未跟踪文件**（mtime 13:45–14:15），非本次 L3 范围、T4 未触碰。

### 5.2 `commercial/var/` 与测试密钥不进 git

```text
$ git check-ignore -v commercial/var/test_keys/license_signing_ed25519.pem
Sanitized_Data/commercial/.gitignore:2:var/  commercial\var\test_keys\license_signing_ed25519.pem
$ git check-ignore -v commercial/var/test_keys/app_private.pem          → ignored（同上）
$ git check-ignore -v commercial/var/test_keys/alipay_private.pem       → ignored（同上）
$ git check-ignore -v commercial/var/test_keys/license_public_keys_test.json → ignored（同上）
$ git check-ignore -v commercial/var/demo_logs/app_stdout.log           → ignored（同上）
```

- `commercial/.gitignore` 自带 `var/ *.db *.db-wal *.db-shm *.pem *.key .env __pycache__/ *.py[cod] .pytest_cache/ .pytest_tmp/ *.log out/`；
- `git ls-files commercial/var` 为空（且 `git status` 不显示任何 `commercial/var` 条目）。

### 5.3 不读取/打印真实私钥、口令

- T4 脚本只使用 `commercial/var/test_keys/` 下**测试密钥**；`gen_test_keys.py` 不生成、不读取生产目录；`e2e_demo.py`/`demo_up.ps1` 不引用 `%USERPROFILE%\.sanitized-data-license-keys`（grep 无命中）；
- `e2e_demo.py` 子进程环境显式剔除 `LICENSE_KEY_PASSWORD`（避免继承生产口令）与 `LICENSE_PRIVATE_KEY_PATH`/`LICENSE_KEY_ID` 等，再注入测试值（`_child_env()`）；
- 日志/输出脱敏：`e2e_demo.py` 只打印订单号/`license_id`/字节数/指纹，不打印 `order_token`、license 全文、私钥；`--json` 摘要还会把 `license_text`/`order_token` 替换为 `<N chars>`；
- 测试断言：`test_alipay_notify_success_duplicate_and_import` 反查检查点输出不含订单 token 与 license 全文；
- `gen_test_keys.py` 输出实测无 `BEGIN ... PRIVATE KEY`/`MIIE` 等私钥正文。

### 5.4 产品侧仍零外呼 / 公钥 env 行为不变

```text
$ git diff --stat app/server
 app/server/yinzhan/api.py        |  9 ++++--
 app/server/yinzhan/config.py     |  4 +++
 app/server/yinzhan/licensing/keys.py | 33 ++++++++++++++++++++--
 3 files changed, 42 insertions(+), 4 deletions(-)        ← 仅 T3 改动（路由别名/commercial_url/测试公钥 env）
```

- `app/server/yinzhan` 全树外呼检索：只有**既有**的 `updater.py`（`urllib.request`，产品更新功能，用户主动触发）命中；
  L3 改动未新增任何 `urllib.request`/`http.client`/`requests` 外呼；E2E 断言本机 `/api/health` 的 `net="blocked"`。
- `YINZHAN_PUBLIC_KEYS_PATH` 行为实测（`commercial/var/test_keys/license_public_keys_test.json`）：

```text
未设置 env：key_ids= ['prod-2026']                default_unchanged=True
设置 env  ：key_ids= ['prod-2026', 'test-2026']  test_key_len=32  prod_kept=True（合并/覆盖，不破坏默认公钥）
```

### 5.5 清理与端口

- `e2e_demo.py` 正常/异常均 `finally` 停止全部子进程并删除 `commercial/var/_e2e_tmp/<run>`（实测 `tmp_removed=True`）；
- 全部测试/演示结束后 8787/8788/8789/8790/8799/8801 端口均 `free`；
- 收尾清理了 T4 排障用的临时 probe 文件、演示数据/日志目录（`commercial/var` 仅保留 `test_keys/`、`_e2e_tmp/`、`_pytest_tmp/`）。

---

## 6. 已知限制

1. **真实支付宝未联调**：E2E 的 alipay 服务为 `page` 模式（本地签名拼 `pay_url`，不访问网关），notify 由测试 RSA 密钥模拟；
   **未**调用支付宝网关、未验证真实支付宝公钥/签名、未验证公网 HTTPS notify 可达性与支付宝重试策略。
2. **浏览器 UI 未自动化**：无 Playwright/Selenium；已验证 `/purchase` 200 + 页面静态契约（T3 单测）+ 页面用到的全部 HTTP 接口（T4 E2E），
   但「点击→二维码→模拟支付→自动解锁」的 JS 交互需按 §4.6 手动步骤验收。
3. **测试密钥仅限测试**：`test-2026` 密钥在 `commercial/var`（gitignore）；生产必须换生产密钥，且本机发布包**不得**设置 `YINZHAN_PUBLIC_KEYS_PATH`。
4. **过期订单用 DB 改 `expires_at` 加速**（等价 15 分钟 TTL 到期），未真实等待 15 分钟；未覆盖「过期后支付宝仍支付成功 → notify `allow_expired` 补签发」的端到端场景。
5. **端口约定**：E2E 默认 8788/8789/8799，被占用会自动顺延（打印实际端口）；`demo_up.ps1` 端口被占用时直接报错并提示 `-MockPort/-AppPort`。
6. **mock 模式语义**：`PAYMENT_PROVIDER=mock` 时 `alipay` 通道也显示可用，实际由 MockProvider 出演示二维码（CONTRACT/README 既定行为，非缺陷）。
7. **仅 Windows 实测**：`demo_up.ps1` 为 Windows 专用；`e2e_demo.py` 逻辑跨平台但只在 Windows 10 验证。
8. **演示数据保留**：`demo_up.ps1` 的 `commercial/var/demo_*`（DB/日志）为演示证据保留（gitignore），可手动删除。

---

## 7. 缺陷 / 风险清单

### 7.1 缺陷（T4 范围内发现并修复，未改他人文件）

**D1（已修复，T4 自身脚本）`gen_test_keys.py` 相对 `--out-dir` 解析错误**

- 文件:行号：`commercial/scripts/gen_test_keys.py:70`（修复前 `default_paths()` 把相对路径拼到 `COMMERCIAL_DIR` 下）
- 复现：`python commercial/scripts/gen_test_keys.py --out-dir commercial/var/_e2e_tmp/x`（cwd=`Sanitized_Data`）
- 期望：写入 `Sanitized_Data/commercial/var/_e2e_tmp/x`
- 实际（修复前）：写入 `Sanitized_Data/commercial/commercial/var/_e2e_tmp/x`（路径重复 `commercial/`）
- 修复：相对路径按 **cwd** 解析（标准 CLI 语义）；复测 `created=6/reused=0` → `created=0/reused=6`，指纹不变；默认路径（绝对）行为不变。

> 未发现 T1/T2/T3 的功能缺陷：E2E 23 检查点、全量 139 用例均通过。

### 7.2 环境问题（非产品缺陷，已在脚本内规避）

- **D2 Windows PowerShell 5.1 + 大小写重复 env**：见 §2.1-4；`demo_up.ps1` 已内置去重修复，报错前会清理并提示「已清理…大小写重复项 N 个」。
  若其它 PowerShell 脚本也要 `Start-Process`，建议同样处理或改用 Python `subprocess`（Python `os.environ` 天生大小写不敏感）。

### 7.3 风险（上线前需 Lead 决策/补测）

| ID | 等级 | 风险 | 建议 |
|---|---|---|---|
| R1 | **P1（上线阻塞）** | 真实支付宝未联调：网关请求/响应验签、真实公钥、HTTPS notify、`seller_id`、退款/重试均未验证 | 按 `docs/alipay-setup.md` §6.2 用 0.01 元沙箱/真实小额实测；确认 notify URL 公网 HTTPS 可达；`PAYMENT_MOCK_ENABLED=false`；`admin.py check` |
| R2 | P2 | 浏览器购买/自动解锁流程未自动化 | 按 §4.6 手动走一遍；如需回归可后续加 Playwright（当前无依赖） |
| R3 | P2 | 生产密钥切换流程未演练（生成 prod 私钥/公钥表、安装包内置公钥、签发与导入） | 在测试机用 `prod-2026` 完整演练一次；确认发布包不含 `test-2026`、未设 `YINZHAN_PUBLIC_KEYS_PATH` |
| R4 | P3 | 过期/宽限/退款后授权作废等边缘流程未端到端覆盖 | 上线后按客服 SOP 人工验证；必要时补自动化 |
| R5 | P3 | 单机 SQLite + 内存限流：多实例部署不适用 | 日均单量较小时可接受；扩容前改共享 DB/反代限流（README 已注明） |
| R6 | P3 | `demo_up.ps1` 演示数据保留在 `commercial/var/demo_*` | gitignore；需要干净环境时手动删除或不用 `-KeepData`（默认每次重置） |

---

## 8. 附录：原始输出摘录

### 8.1 `e2e_demo.py` 23 检查点 PASS 清单（2026-11-02 最终轮）

```text
[PASS] 生成/复用测试密钥（Ed25519 test-2026 + 两对 RSA）
[PASS] 启动商业服务（mock）并等待 /healthz
[PASS] 启动本机 app（--no-browser --no-ocr）并等待 /api/health
[PASS] 本机 /api/license/status 提供机器码/commercial_url
[PASS] 本机购买页 /purchase 可访问（200，纯本地资源）
[PASS] 商业服务 /api/catalog（SKU/价格/通道）
[PASS] POST /api/orders（pro，qr_svg/order_token）
[PASS] POST /api/mock/pay → paid + license.text（单行 JSON）
[PASS] 订单查询 + 重复模拟支付幂等（同一 license_id）
[PASS] 本机 POST /api/license/import（测试 license → paid）
[PASS] 本机 /api/license/status 复核 paid/专业版
[PASS] CORS 预检：允许本机 Origin（204 + allow-origin）
[PASS] CORS：无关 Origin 不返回 allow-origin
[PASS] token 错误统一 404 order_not_found
[PASS] 过期订单（expires_at 已过）不可支付 → 400 order_expired
[PASS] 启动第二个商业服务（provider=alipay，page 模式）
[PASS] 支付宝 page 模式下单 → pay_url（含签名）
[PASS] 支付宝服务上 /api/mock/pay → 403 mock_disabled
[PASS] 支付宝 notify 篡改金额/签名 → failure 且订单 pending
[PASS] 支付宝 notify WAIT_BUYER_PAY → success 且不签发
[PASS] 支付宝 notify TRADE_SUCCESS → paid + license
[PASS] 重复异步通知幂等（success，不重复签发）
[PASS] 支付宝通道签发的 license 可被本机导入解锁（跨通道）

清理：停止子进程…
[INFO] 临时目录已清理：True
E2E 结果：23/23 检查点 PASS，耗时 6.2s ✅
```

### 8.2 pytest 尾部

```text
......................................................................................... [100%]
139 passed, 1 warning, 62 subtests passed in 10.69s
```

### 8.3 `demo_up.ps1` 实测输出摘录

```text
[OK] Python 版本 3.12.14；fastapi/uvicorn/cryptography 可用
[!] 已清理进程环境变量中的大小写重复项 3 个（Windows PowerShell Start-Process 兼容处理）
[OK] 测试密钥已存在（复用）：...\commercial\var\test_keys
[OK] 端口可用：商业服务 8788 / 本机 app 8799
[OK] 商业服务已启动：PID=…    [OK] 本机 app 已启动：PID=…
[OK] 商业服务就绪：{"ok":true,"provider":"mock","version":"1.0.0"}
[OK] 本机 app 就绪：http://127.0.0.1:8799/api/health
[OK] /purchase 200（含 purchase.js）
  本机购买页 : http://127.0.0.1:8799/purchase
  停止演示：在本窗口按 Ctrl+C … 或 Stop-Process -Id <pid1>,<pid2>
[OK] 演示已停止（数据与日志保留在 commercial/var/demo_*，可复查）
[demo exit=0]
```

### 8.4 关键文件/行号索引

| 内容 | 位置 |
|---|---|
| 测试密钥生成/幂等 | `commercial/scripts/gen_test_keys.py:70`（`default_paths`）、`ensure_test_keys()` |
| E2E 独立 RSA2 签名 | `commercial/scripts/e2e_demo.py:341` |
| E2E mock/本机 app/alipay 启动 | `commercial/scripts/e2e_demo.py:482` / `:546` / `:507` |
| E2E 主流程 23 检查点 | `commercial/scripts/e2e_demo.py:615`（`run_e2e`） |
| pytest E2E 模块 | `commercial/tests/test_e2e_mock.py` |
| 一键演示脚本 | `commercial/scripts/demo_up.ps1`（`-ValidateOnly`/`-NoBrowser`/`-DurationSeconds`） |

---

## 9. 变更记录

| 日期 | 变更 | 备注 |
|---|---|---|
| 2026-11-02 | T4 初版：新增 gen_test_keys/e2e_demo/test_e2e_mock/demo_up.ps1，跑通 23/23 E2E 与 139 用例回归，输出本报告 | 未改 T1/T2/T3 文件；未提交 git |
