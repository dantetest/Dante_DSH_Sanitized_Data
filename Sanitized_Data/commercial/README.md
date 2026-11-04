# 数据隐站 · L3 商业服务（commercial/）

本目录是「购买 → 扫码支付 → 自动签发 Ed25519 授权 → 本机解锁」链路中的**商业服务端**：
FastAPI + SQLite（WAL），默认 mock 支付通道用于本地演示，真实收款走支付宝（T2 适配器）。

- 本机引擎（`app/server/yinzhan`）仍**零外呼**；只有购买页浏览器 JS 主动访问本服务。
- 商业服务**不接触用户文件**，只收机器码/可选邮箱/订单信息。
- 授权签发复用产品 `yinzhan.licensing.issue.build_payload/sign_license`，签出的
  `license.text` 是**紧凑单行 JSON**，与软件「授权」页粘贴导入的授权码完全一致。

## 目录结构

```text
commercial/
├── CONTRACT.md                 # Lead 维护的实施契约（接口/DB/配置的唯一口径）
├── README.md                   # 本文件
├── requirements.txt            # 运行依赖（fastapi/uvicorn/cryptography）
├── .env.example                # 配置示例（复制为 .env，勿提交）
├── .gitignore                  # var/、*.db、*.pem、.env 等
├── server/
│   ├── __init__.py
│   ├── app.py                  # FastAPI 应用工厂、路由、CORS、限流、错误体
│   ├── config.py               # 环境变量 -> Config（属性名与 env 同名）
│   ├── catalog.py              # SKU/价格/通道（服务端唯一事实来源）
│   ├── db.py                   # SQLite schema + 订单/支付/授权/事件访问层
│   ├── signer.py               # Ed25519 license 签发（懒加载私钥）
│   ├── qr.py                   # vendored Nayuki 纯 Python QR + SVG 输出（MIT）
│   ├── payments/
│   │   ├── __init__.py         # PaymentError/get_provider/available_channels
│   │   ├── mock.py             # 演示支付（T1）
│   │   └── alipay.py           # 支付宝适配器（T2）
│   └── static/                 # 纯本地页面：buy/order/mock_pay + css/js（无 CDN）
├── scripts/
│   ├── run_dev.py              # 本地启动（读 .env、--reload、banner 自检）
│   └── admin.py                # 运维 CLI：stats/orders/order/events/expire/check
├── tests/
│   ├── conftest.py             # 测试配置/临时密钥/客户端夹具
│   ├── test_orders.py          # 目录/下单/查询/mock 支付/幂等/过期/CORS/静态页
│   └── test_signer.py          # 机器码/签发/验签/产品 LicenseService 导入
└── var/                        # 运行时数据（gitignore）：commercial.db、密钥
```

## 快速开始（本地 mock 演示）

```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data

# 1) 依赖（Python 3.12）
python -m pip install -r commercial\requirements.txt

# 2) 配置
copy commercial\.env.example commercial\.env
#   默认 PAYMENT_PROVIDER=mock，LICENSE_PRIVATE_KEY_PATH 指向本机生产私钥；
#   本机没有生产私钥时，用下面的「测试密钥」方式启动（仅演示/测试）。

# 3) 启动（自动读取 commercial/.env，打印自检 banner）
python commercial\scripts\run_dev.py --provider mock
# 浏览器打开 http://127.0.0.1:8787/buy.html
```

也可以直接用 uvicorn（生产同款启动方式）：

```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
$env:PYTHONPATH = "commercial;app/server"
python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8787 --no-access-log
```

> `--no-access-log` 建议保持：订单轮询 URL 带查询令牌，access log 会把 token 写进日志。

### 演示流程（mock，全链路）

1. `/buy.html` 输入/预填机器码（`?machine_code=XXXX-XXXX-XXXX-XXXX`），选择版本；
2. 点击「生成支付二维码」→ 服务端返回 `qr_svg`（内联 SVG，无外链图片）；
3. 订单页出现「演示：模拟支付成功」→ 点击后订单变 `paid` 并返回 `license.text`；
4. 复制授权码到本机软件「授权」页导入即解锁（本机购买页的自动导入由 T3 实现）。

测试密钥生成（**仅开发/测试**，公钥需要注册到产品测试公钥表）：

```powershell
python -X utf8 -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; from cryptography.hazmat.primitives import serialization; import base64, pathlib; p=pathlib.Path('commercial/var/test_keys/license_signing_ed25519.pem'); p.parent.mkdir(parents=True, exist_ok=True); k=Ed25519PrivateKey.generate(); p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())); pub=k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw); print('key_id=test-2026'); print('public_key_b64=' + base64.b64encode(pub).decode())"
# 然后：LICENSE_PRIVATE_KEY_PATH=commercial/var/test_keys/license_signing_ed25519.pem
#       LICENSE_KEY_ID=test-2026
```

> 生产私钥只放商业服务机器（默认 `%USERPROFILE%\.sanitized-data-license-keys\license_signing_ed25519.pem`），
> 绝不进仓库/安装包/日志；口令通过 `LICENSE_KEY_PASSWORD_ENV` 指定的环境变量传入。

## HTTP API

所有 API 路径的错误体统一为 `{"ok": false, "error": {"code": "...", "message": "..."}}`。
金额单位一律为**分（int）**，由服务端 SKU 表决定，客户端传入的金额字段被忽略。

### `GET /api/catalog`

```json
{
  "ok": true,
  "provider": "mock",
  "channels": [
    {"id": "alipay", "label": "支付宝扫码", "enabled": true},
    {"id": "mock", "label": "模拟支付（演示）", "enabled": true}
  ],
  "skus": [
    {"id": "personal", "name": "个人版", "edition": "个人版", "price_cents": 19900,
     "currency": "CNY", "term": "annual", "devices": 1,
     "features": ["all_packs", "tif", "d3d", "ocr"]},
    {"id": "pro", "name": "专业版", "edition": "专业版", "price_cents": 49900,
     "currency": "CNY", "term": "annual", "devices": 2,
     "features": ["all_packs", "custom_packs", "tif", "d3d", "mcp", "batch", "audit_export", "ocr"]}
  ],
  "contact_sales": {"skus": ["team", "org"], "label": "团队版 / 机构版", "message": "...",
                    "reference_prices_cents": {"team": 199900, "org": null}, "url": null},
  "currency": "CNY",
  "mock_available": true,
  "version": "1.0.0"
}
```

- 只卖 `personal` / `pro`；`team`/`org` 在 `contact_sales` 里返回（不建线上单，下单会被 400 `sku_invalid` 拒绝）。
- `PAYMENT_PROVIDER=mock` 的演示模式下 `alipay` 通道也显示可用：实际由 MockProvider 出演示二维码，
  便于本地演示与真实部署共用同一套前端。

### `POST /api/orders`（浏览器跨域，CORS）

请求：`{"sku": "pro", "machine_code": "ZH39-7N5C-98CD-A8GB", "channel": "alipay", "email": "可选"}`

响应 200：

```json
{
  "ok": true,
  "order_no": "ord_20261102180000_ab12cd34",
  "sku": "pro",
  "amount_cents": 49900,
  "subject": "数据隐站 专业版 · 1年",
  "channel": "alipay",
  "provider": "mock",
  "qr_svg": "<svg xmlns=...></svg>",
  "qr_content": "http://127.0.0.1:8787/mock/pay?order_no=...&token=...",
  "pay_url": "http://127.0.0.1:8787/mock/pay?order_no=...&token=...",
  "order_token": "32-byte-urlsafe（仅此一次返回明文）",
  "expires_at": "2026-11-02T18:15:00+00:00",
  "mock_available": true
}
```

- 订单 15 分钟有效；`order_token` 32B urlsafe，DB 只存 sha256。
- 限流：默认 **10 单/分钟/IP**（`COMMERCIAL_RATE_LIMIT_ORDERS_PER_MINUTE` 可调），超限 429。
- 支付宝下单失败（未配置/网络/网关报错）→ 503 `payment_create_failed`。

### `GET /api/orders/{order_no}?token=...`

```json
{"ok": true, "order_no": "ord_...", "status": "pending|paid|expired|closed",
 "sku": "pro", "amount_cents": 49900, "paid_at": null, "license": null,
 "mock_available": true, "expires_at": "2026-11-02T18:15:00+00:00"}
```

已支付时：

```json
{"license": {"license_id": "lic_...", "text": "{...紧凑单行 JSON...}", "sku": "pro",
             "machine_code": "ZH39-...", "expires_at": "2027-11-02T...", "edition": "专业版"}}
```

- token 缺失/错误/订单不存在**统一 404 `order_not_found`**（防枚举）。
- `expires_at` 为 T1 扩展字段（前端倒计时用），不影响契约必选字段。

### `POST /api/mock/pay`

请求 `{"order_no": "ord_...", "token": "..."}`；仅 mock 启用时可用，否则 403 `mock_disabled`。
行为：标记 paid + 签发 + 返回与 GET 订单相同结构（含 license）。重复调用幂等（返回同一 license，不重复入账）。

### `POST /notify/alipay`

支付宝异步通知（`application/x-www-form-urlencoded`，手工解析）。成功返回纯文本 `success`，
失败返回 `failure`（HTTP 200，支付宝会重试；适配器缺失时 503+failure）。
流程：验签 → app_id/seller_id 校验 → 查单 → 金额（元字符串转分）核对 → 状态校验 →
`trade_no` 幂等 → 标记 paid + 签发。非支付成功状态（`WAIT_BUYER_PAY` 等）ack `success` 但不签发。

### 其他

- `GET /healthz` → `{"ok": true, "provider": "mock", "version": "1.0.0"}`
- `GET /buy.html`、`GET /order.html`、`GET /mock/pay`、`GET /static/**`：纯本地静态资源（无 CDN/外部字体）。
- `GET /order.html?order_no=...&token=...` 也支持手工输入订单号/令牌查询。

### 错误码

| HTTP | code | 场景 |
|---|---|---|
| 400 | `machine_code_invalid` | 机器码格式错误 |
| 400 | `sku_invalid` | 套餐不存在 / team/org 需联系商务 |
| 400 | `invalid_request` | JSON 结构/邮箱等参数非法 |
| 400 | `order_expired` | 订单已过期（不可支付） |
| 400 | `order_not_payable` | 订单状态不可支付（closed 等） |
| 403 | `channel_unavailable` | 通道未启用（如 mock 关闭时选 mock） |
| 403 | `mock_disabled` | 非 mock 部署调用 `/api/mock/pay` |
| 404 | `order_not_found` | token 错误/不存在（统一） |
| 429 | `rate_limited` | 下单限流 |
| 500 | `internal_error` | 未预期异常（不泄露细节） |
| 503 | `payment_create_failed` | 支付通道下单失败 |
| 503 | `license_key_missing` / `license_key_invalid` | 签名私钥未配置/读取失败 |
| 503 | `license_issue_failed` | 签发失败 |

## 数据库（SQLite WAL）

默认 `commercial/var/commercial.db`，schema 见 `CONTRACT.md` §5：

- `orders`：订单主表（`order_no` 主键、`order_token_hash` 只存 sha256、`status` 只前进）；
- `payments`：支付流水（`transaction_id` UNIQUE，回调幂等的第一道闸）；
- `licenses`：授权（`order_no` UNIQUE，重复回调/重复点击不重复签发）；
- `events`：审计事件（下单/支付/回调失败/过期等，不含 token/私钥）。

入账是单事务：写 payment + 写 license + 标记 paid 原子提交，签发（签名）在事务外。

## 支付通道

- `PAYMENT_PROVIDER=mock`：本地演示（不联网、不扣款）。`PAYMENT_MOCK_ENABLED` 在 mock 时自动 true。
- `PAYMENT_PROVIDER=alipay`：真实支付宝（T2 适配器）。配置与申请流程见
  [`docs/alipay-setup.md`](docs/alipay-setup.md)（T2 维护）。
  - `ALIPAY_PAY_MODE=precreate`（当面付扫码，推荐）或 `page`（电脑网站支付）；
  - 生产必须 HTTPS；notify URL 默认 `${COMMERCIAL_BASE_URL}/notify/alipay`。
- 切换到真实支付后请把 `PAYMENT_MOCK_ENABLED=false`，`/api/mock/pay` 会返回 403。

## 安全说明

- 私钥（license 私钥 / 支付宝应用私钥）只在商业服务机器；仓库 `.gitignore` 排除 `*.pem`、`var/`、`.env`。
- 日志不打印 token/私钥/口令；不记录查询串；`run_dev.py` 默认关闭 uvicorn access log。
- 订单 token 库内只存 sha256；token 错误统一 404，防订单枚举。
- 金额/SKU/有效期全部服务端计算，忽略客户端金额；回调做金额核对 + 幂等。
- CORS 只默认放行 `http://127.0.0.1:<任意端口>`、`http://localhost:<任意端口>` 与
  `COMMERCIAL_CORS_EXTRA_ORIGINS` 显式配置项；不带 credentials。
- 限流按键（IP）内存计数：单进程有效；多 worker/多实例部署需在反代层再做限流。
- 生产建议：HTTPS + 反向代理、数据目录最小权限、定期备份 `commercial.db`（WAL 下用
  `sqlite3 commercial.db ".backup ..."` 或停机拷贝 `*.db*`）。

## 测试

```powershell
cd D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data
$env:PYTHONPATH = "commercial;app/server"
python -X utf8 -m pytest commercial/tests -q
```

- 测试用临时目录 + 会话级生成的 Ed25519 测试密钥（`key_id=test-2026`），把测试公钥注册进
  `yinzhan.licensing.keys`，因此可直接验证「mock 支付 → 签发 → 产品 `LicenseService` 导入为 paid」。
- 不依赖本机生产私钥；不会访问网络。

## 管理 CLI

```powershell
python commercial\scripts\admin.py check                    # 配置/DB/密钥自检
python commercial\scripts\admin.py stats                    # 订单/收入/授权统计
python commercial\scripts\admin.py orders --status paid     # 列订单
python commercial\scripts\admin.py order ord_... --show-license   # 看订单+授权全文（敏感）
python commercial\scripts\admin.py events --limit 30        # 审计事件
python commercial\scripts\admin.py expire                   # 把过期未支付订单置 expired
```

CLI 以「能读本地 DB 文件」为管理员权限；`COMMERCIAL_ADMIN_TOKEN` 预留给未来远程管理接口。

## 故障排查

| 现象 | 排查 |
|---|---|
| `/api/catalog` 502/连不上 | 服务是否启动、端口/防火墙、`COMMERCIAL_BASE_URL` 是否正确 |
| 下单 503 `payment_create_failed` | 看服务日志：支付宝配置/密钥/网络，或 `PAYMENT_PROVIDER` 指向 alipay 但 `payments/alipay.py` 缺失 |
| 模拟支付 503 `license_key_missing` | 配置 `LICENSE_PRIVATE_KEY_PATH`（生产）或测试私钥（测试）；`admin.py check` 可看 `signer` 状态 |
| 扫码后一直 pending | 支付宝回调是否可达（公网 HTTPS/域名/防火墙），`admin.py order` 看 events |
| 客户导入授权提示签名无效 | 签发 key_id 与产品内置公钥是否匹配（生产密钥签 → `prod-2026b`） |
| 页面 404 | 是否用 `run_dev.py` 启动（静态目录随仓库提供）；是否部署了 `server/static/` |
| `run_dev.py --reload` 报 `multiprocessing ... CreateNamedPipe PermissionError` | 受限沙箱（如 DSH 工具沙箱）禁止命名管道；用**不带 `--reload`** 的启动方式即可，正常本地终端可用 `--reload` |

## 边界与已知限制

- 商业服务只负责「卖授权」；本机自动导入、轮询解锁由 T3 的购买页实现，端到端演示脚本由 T4 提供。
- 单机 SQLite + 内存限流适合当前规模（日均几十单）；多实例部署需换共享 DB/限流/任务队列。
- 不支持退款/续费/发票流程的自动化（人工处理）。

