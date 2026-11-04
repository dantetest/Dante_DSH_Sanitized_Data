# L3 付费解锁 · 实施契约（Lead 维护，2026-11-02）

> 目标：用户在本机「购买页」看到支付宝二维码 → 手机扫码支付 → 商业服务收到回调 → 自动签发 **Ed25519 license** → 本机页面轮询拿到授权 → 自动调用本地 `/api/license/import` → **直接解锁**。
> 约束：本机引擎（Python）仍然**零外呼**；只有购买页（浏览器 JS，用户主动点击）访问商业服务。文件处理全程本机。

## 0. 总览

```text
本机 app/web/purchase.html + purchase.js（同源 http://127.0.0.1:<port>）
   │  ① 取本机机器码 /api/license/status
   │  ② POST {COMMERCIAL_URL}/api/orders           （浏览器跨域，CORS）
   │  ③ 展示 qr_svg（服务端生成）
   │  ④ 轮询 GET {COMMERCIAL_URL}/api/orders/{no}?token=
   │  ⑤ paid → POST 同源 /api/license/import        （自动解锁）
   ▼
commercial/ 商业服务（FastAPI + SQLite，独立部署，公网 HTTPS）
   ├─ /api/catalog /api/orders /api/orders/{no} /api/mock/pay
   ├─ /notify/alipay（异步通知：验签/金额/幂等 → 签发）
   ├─ /buy.html /order.html /mock/pay
   └─ payments/（mock / alipay precreate/page）
```

## 1. 文件所有权（避免并行写冲突）

| 区域 | Owner | 说明 |
|---|---|---|
| `commercial/server/app.py`、`config.py`、`db.py`、`catalog.py`、`signer.py`、`qr.py`、`payments/__init__.py`、`payments/mock.py`、`static/**`、`scripts/run_dev.py`、`scripts/admin.py`、`README.md`、`requirements.txt`、`.env.example` | **T1 commercial-core** | 商业服务主程序 |
| `commercial/server/payments/alipay.py`、`commercial/docs/alipay-setup.md`、`commercial/tests/test_alipay.py` | **T2 alipay-adapter** | 只写支付宝适配器与文档；不改 app.py/`payments/__init__.py` |
| `app/web/purchase.html`、`purchase.css`、`purchase.js`、`app/web/license.html/js`、`app/web/pricing.html/js`、`app/server/yinzhan/config.py`、`app/server/yinzhan/api.py`、`app/server/yinzhan/licensing/keys.py` | **T3 client-purchase** | 本机购买页/自动解锁/配置注入 |
| `commercial/scripts/e2e_demo.py`、`commercial/scripts/demo_up.ps1`、`commercial/tests/test_e2e_mock.py`、`commercial/QA.md` | **T4 qa-e2e** | 端到端验收（T1/T2/T3 完成后开工） |
| `commercial/CONTRACT.md`、`commercial/server/payments/mock.py` 的接口定义、最终集成 | **Lead** | 冲突时 Lead 裁决 |

> 任何人不要改别人 scope 的文件；需要改接口时先在共享任务里说明，Lead 裁决。

## 2. 商业服务配置（环境变量）

> **配置对象约定**：`commercial/server/config.py` 暴露 `Config` 实例/类，属性名与下表的 env **同名**（如 `config.ALIPAY_APP_ID`、`config.ALIPAY_PAY_MODE`、`config.COMMERCIAL_BASE_URL`、`config.PAYMENT_PROVIDER`）。Provider 构造函数**无参数**（`get_provider(name, config)` 里实例化），方法签名 `create(order, config)` / `verify_notify(form, config)`。

```dotenv
# 基础
COMMERCIAL_HOST=127.0.0.1
COMMERCIAL_PORT=8787
COMMERCIAL_BASE_URL=http://127.0.0.1:8787      # 公网部署改成 https://buy.xxx.com
COMMERCIAL_DATA_DIR=commercial/var
COMMERCIAL_DB=commercial/var/commercial.db
COMMERCIAL_CORS_EXTRA_ORIGINS=                  # 逗号分隔，额外允许（默认允许 127.0.0.1/localhost 任意端口）

# 支付通道：mock（本地演示）或 alipay
PAYMENT_PROVIDER=mock
PAYMENT_MOCK_ENABLED=true                        # provider=mock 时自动 true

# 支付宝（真实接单时填）
ALIPAY_APP_ID=
ALIPAY_APP_PRIVATE_KEY_PATH=commercial/var/keys/alipay_app_private.pem
ALIPAY_PUBLIC_KEY_PATH=commercial/var/keys/alipay_public.pem
ALIPAY_GATEWAY=https://openapi.alipay.com/gateway.do
# 沙箱：https://openapi-sandbox.dl.alipaydev.com/gateway.do
ALIPAY_PAY_MODE=precreate                        # precreate=扫码（推荐，需当面付）；page=电脑网站支付
ALIPAY_NOTIFY_URL=                               # 不填则 ${COMMERCIAL_BASE_URL}/notify/alipay
ALIPAY_SELLER_ID=                                # 可选校验
ALIPAY_APP_ID_CHECK=1

# 授权签发（私钥只放商业服务机器；不要进仓库/安装包）
LICENSE_PRIVATE_KEY_PATH=%USERPROFILE%\.sanitized-data-license-keys\license_signing_ed25519.pem
LICENSE_KEY_PASSWORD_ENV=LICENSE_KEY_PASSWORD
LICENSE_KEY_ID=prod-2026

# 价格（分；默认正式价，早鸟价改这里）
COMMERCIAL_PRICE_PERSONAL_CENTS=19900
COMMERCIAL_PRICE_PRO_CENTS=49900
COMMERCIAL_PRICE_TEAM_CENTS=199900

# 管理令牌（admin CLI/接口用）
COMMERCIAL_ADMIN_TOKEN=
```

## 3. HTTP API 契约（必须一致）

### GET `/api/catalog`
```json
{
  "ok": true,
  "provider": "mock",
  "channels": [{"id": "alipay", "label": "支付宝扫码", "enabled": true},
               {"id": "mock", "label": "模拟支付（演示）", "enabled": true}],
  "skus": [
    {"id": "personal", "name": "个人版", "edition": "个人版", "price_cents": 19900,
     "currency": "CNY", "term": "annual", "devices": 1,
     "features": ["all_packs", "tif", "d3d", "ocr"]},
    {"id": "pro", "name": "专业版", "edition": "专业版", "price_cents": 49900,
     "currency": "CNY", "term": "annual", "devices": 2,
     "features": ["all_packs", "custom_packs", "tif", "d3d", "mcp", "batch", "audit_export", "ocr"]}
  ]
}
```
- 只卖 `personal` / `pro` 自助单；`team`/`org` 返回 `contact_sales`（前端给联系方式，不建线上单）。
- 价格是 int 分，以服务端为准。

### POST `/api/orders`
请求（JSON，CORS）：
```json
{"sku": "pro", "machine_code": "ZH39-7N5C-98CD-A8GB", "channel": "alipay", "email": "optional@example.com"}
```
响应 200：
```json
{
  "ok": true,
  "order_no": "ord_20261102180000_ab12cd34",
  "sku": "pro",
  "amount_cents": 49900,
  "subject": "数据隐站 专业版 · 1年",
  "channel": "alipay",
  "provider": "alipay",
  "qr_svg": "<svg ...>...</svg>",
  "qr_content": "https://qr.alipay.com/...",
  "pay_url": null,
  "order_token": "32-byte-urlsafe",
  "expires_at": "2026-11-02T18:15:00+00:00",
  "mock_available": false
}
```
错误：`{"ok": false, "error": {"code": "...", "message": "..."}}`，HTTP 400/403/429/503。
- 机器码格式错误 `machine_code_invalid`；sku 非法 `sku_invalid`；通道未启用 `channel_unavailable`；限流 `rate_limited`；支付宝下单失败 `payment_create_failed`。

### GET `/api/orders/{order_no}?token=...`
```json
{
  "ok": true,
  "order_no": "ord_...",
  "status": "pending|paid|expired|closed",
  "sku": "pro",
  "amount_cents": 49900,
  "paid_at": null,
  "license": null,
  "mock_available": false
}
```
已支付时 `license`：
```json
{"license_id":"lic_...","text":"{...compact json...}","sku":"pro","machine_code":"ZH39-...","expires_at":"2027-11-02T...","edition":"专业版"}
```
- token 错误/不存在统一 404 `order_not_found`（防枚举）。

### POST `/api/mock/pay`
请求 `{"order_no": "ord_...", "token": "..."}`；仅 `PAYMENT_PROVIDER=mock` 可用；标记 paid + 签发；响应同 GET 订单（含 license）。
- 非 mock 返回 403 `mock_disabled`。

### POST `/notify/alipay`
- `application/x-www-form-urlencoded`（支付宝异步通知）；手工解析 `request.body()` + `urllib.parse.parse_qs`，不依赖 python-multipart。
- 成功返回纯文本 `success`；失败返回 `failure`（HTTP 200，支付宝会重试）。
- 必须：`sign` 验签（RSA2，剔除 `sign`/`sign_type` 后按字典序 `k=v&...`）→ `app_id` 校验 → `out_trade_no` 查单 → `total_amount` 元字符串转 int 分核对 → `trade_status` ∈ `TRADE_SUCCESS/TRADE_FINISHED` → 幂等（`trade_no` 唯一）→ 标记 paid → 签发。
- 重复通知：直接返回 `success`，不重复签发。

### GET `/order.html?order_no=...&token=...` 与 GET `/buy.html?sku=...&machine_code=...`
- 纯静态页（T1 提供 HTML/JS）；页面用上面 API；外网购买/回跳用。
- `/mock/pay?order_no=...&token=...`：模拟支付页，按钮 POST `/api/mock/pay`。
- `GET /healthz` → `{"ok":true,"provider":"mock","version":"..."}`。

## 4. 支付提供者接口（T1 调 T2，固定）

`commercial/server/payments/__init__.py`（T1）：
```python
from .mock import MockProvider          # T1 写 mock.py
# alipay 延迟导入：from .alipay import AlipayProvider  # T2 写 alipay.py

def get_provider(name: str, config): ...
def available_channels(config) -> list[dict]: ...
```
Provider 协议（鸭子类型，T1/T2 都按此实现）：
```python
class PaymentProvider:
    name: str
    label: str
    def create(self, order: dict, config) -> dict:
        """返回 {"qr_content": str|None, "pay_url": str|None, "provider_ref": str|None}
        order 含: order_no, sku, amount_cents, subject, machine_code, token, return_url
        失败抛 PaymentError(code, message)"""
    def verify_notify(self, form: dict, config) -> dict | None:
        """校验签名和金额由调用方核对；返回
        {"order_no": str, "transaction_id": str, "amount_cents": int,
         "raw": dict, "trade_status": str}；验签失败抛 PaymentError。"""
```
- MockProvider（T1）：`create` 返回 `qr_content = f"{base}/mock/pay?order_no={no}&token={token}"`、`pay_url` 同；`verify_notify` 不使用。
- AlipayProvider（T2）：`create` 按 `ALIPAY_PAY_MODE`：
  - `precreate`：签名请求 `alipay.trade.precreate`（biz: out_trade_no/total_amount/subject/notify_url），解析 `alipay_trade_precreate_response.qr_code`；返回 `qr_content=qr_code`；同时校验响应签名（支付宝公钥）。
  - `page`：签名 `alipay.trade.page.pay`（biz: out_trade_no/total_amount/subject/product_code=FAST_INSTANT_TRADE_PAY, return_url, notify_url），返回 `pay_url` + `qr_content=pay_url`（二维码扫码兜底）。
  - `verify_notify`：RSA2 验签（剔除 sign/sign_type）、校验 `app_id`/`seller_id`（配置了才校验）、`total_amount` 转分；返回交易字段。

## 5. 数据库（T1）

`commercial/var/commercial.db`（SQLite WAL），schema：
```sql
orders(order_no TEXT PRIMARY KEY, sku TEXT, amount_cents INTEGER, machine_code TEXT,
       email TEXT NULL, channel TEXT, provider TEXT, status TEXT, order_token_hash TEXT,
       created_at TEXT, paid_at TEXT NULL, expires_at TEXT, license_id TEXT NULL, provider_ref TEXT NULL);
payments(id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT, provider TEXT,
         transaction_id TEXT UNIQUE, amount_cents INTEGER, raw TEXT, created_at TEXT);
licenses(license_id TEXT PRIMARY KEY, order_no TEXT UNIQUE, machine_code TEXT, sku TEXT,
         text TEXT, issued_at TEXT, expires_at TEXT NULL);
events(id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT, kind TEXT, detail TEXT, created_at TEXT);
```
- `order_token` 32B urlsafe；库内存 sha256；下单返回明文。
- 订单 15 分钟过期；过期后不可支付；`status` 只前进（pending→paid/expired）。
- 签发在事务外调用（签名耗时短），但 `licenses.order_no UNIQUE` 保证幂等；重复回调先查 `payments.transaction_id`。

## 6. 授权签发（T1）

- 复用 `app/server/yinzhan/licensing/issue.py`：把 `Sanitized_Data/app/server` 加入 `sys.path`，`from yinzhan.licensing.issue import build_payload, sign_license`。
- `build_payload(sku=order.sku, machine_codes=[order.machine_code], order_id=order.order_no, days=365, notes="online")`；`sign_license(payload, key, LICENSE_KEY_ID)`；`license.text = json.dumps(container, ensure_ascii=False, separators=(",", ":"))`。
- 私钥读取：`serialization.load_pem_private_key(path.read_bytes(), password=os.environ.get(env_var).encode() or None)`；失败给明确错误，不打印私钥/口令。
- 测试用 `test-2026` 测试密钥；生产用 `prod-2026`。

## 7. 本机客户端改动（T3）

- `config.py`：新增 `COMMERCIAL_URL = (os.environ.get("YINZHAN_COMMERCIAL_URL") or "").strip()`；`/api/license/status` 增加 `commercial_url` 字段。
- `api.py`：`PAGE_ALIASES` 增加 `"purchase": "purchase.html"`、`"purchase/": "purchase.html"`。
- `licensing/keys.py`：新增**仅供测试/私有化**的 `YINZHAN_PUBLIC_KEYS_PATH` 覆盖（JSON 格式同 public_keys.json）；不设置时行为不变。
- 新页面 `app/web/purchase.html` + `purchase.css` + `purchase.js`：
  - 读取 `/api/license/status` 拿机器码/状态；`commercial_url` 为空时显示「购买入口未配置」+ 现有 `purchase_url`/人工联系兜底；
  - `GET {commercial_url}/api/catalog` 渲染 SKU 卡片（价格/设备/权益）；
  - 点击「支付宝扫码」→ `POST /api/orders`；把 `qr_svg` 插入容器（`innerHTML`，仅信任商业服务）；显示金额/订单号/倒计时；
  - 每 2 秒 `GET /api/orders/{no}?token=`；`paid` 后调用同源 `POST /api/license/import`（`{content: license.text}`），成功后展示「已解锁」并刷新状态；失败给出「复制授权码手动导入」兜底；
  - 若 `mock_available`，显示「演示：模拟支付」按钮 → `POST {commercial}/api/mock/pay`；
  - 所有外部字符串必须 `esc()`；页面不加载任何外部脚本/字体/CDN；不引入第三方 JS。
- `license.html/js`：购买卡片增加「扫码升级（自动解锁）」按钮 → `/purchase`；保留原「查看价格/购买」和导入。
- `pricing.html/js`：若 status.commercial_url 有值，购买按钮改为 `/purchase?sku=<sku>`；否则保持现有 purchase_url 逻辑。
- 不改动引擎/处理链路；`/purchase` 页面只是 UI。

## 8. 测试与验收

### 单元（T1/T2）
- 目录/金额/状态机/过期/幂等；token 错误 404；机器码非法 400；mock 支付后 license 可被 `LicenseService.import_license`（临时目录 + 测试公钥）接受，状态 paid。
- 支付宝：用测试 RSA 密钥生成请求签名（自验）、模拟 notify 签名（Alipay 私钥签）→ 服务端验签成功；篡改金额/签名失败；precreate 响应签名验证（伪造测试响应）。

### 端到端（T4）
1. 生成测试密钥：`commercial/scripts/gen_test_keys.py` → `commercial/var/test_keys/`（RSA app/alipay + Ed25519 license）；测试用 `test-2026`。
2. 启动商业服务（mock provider，测试密钥），`POST /api/orders` → `qr_svg` 含 `<svg`；`POST /api/mock/pay` → `GET /api/orders` 返回 license；`LicenseService` 测试公钥导入 → state paid。
3. 启动本机 app（测试数据目录 + `YINZHAN_PUBLIC_KEYS_PATH` + `YINZHAN_COMMERCIAL_URL`）：`GET /api/license/status` 有 `commercial_url`；`GET /purchase` 200 且含 `purchase.js`；`POST /api/license/import` 测试 license → state paid。
4. 支付宝 notify 模拟：测试 RSA 私钥签 form → POST `/notify/alipay` → `success`；重复返回 success；金额不符 → failure 且不 paid。
5. CORS：`OPTIONS /api/orders` 带 Origin `http://127.0.0.1:8760` 返回允许头；外部无关 Origin 不返回。
6. 静态检查：`node --check app/web/purchase.js`、`node --check commercial/server/static/*.js`。

### 手动演示（T4 提供）
- `powershell -File commercial/scripts/demo_up.ps1`：生成测试密钥（如缺失）→ 起商业服务 mock → 起本机 app（临时数据目录/测试公钥/COMMERCIAL_URL）→ 打开 `/purchase`；点「模拟支付」→ 页面自动导入 → 显示已解锁。
- 真实支付宝：按 `commercial/docs/alipay-setup.md` 填 APP_ID/密钥，`PAYMENT_PROVIDER=alipay`，沙箱/真实小额验证。

## 9. 安全红线
- 私钥（license 私钥、支付宝应用私钥）只在商业服务/本机；仓库 `.gitignore` 排除 `commercial/var/**`、`*.pem`；任何日志/响应不得输出私钥或口令。
- 商业服务不能访问用户文件；只收机器码/邮箱（可选）/订单信息。
- 回调必须 HTTPS（生产）；开发可 http。
- 金额、sku、有效期以 DB/SKU 表为准，禁止从 HTTP 入参拼授权内容。
- 不做个人收款码、二次清算；只走支付宝商户号。
- 本机引擎零外呼保持不变：`grep -R "urllib.request\|http.client\|requests" app/server/yinzhan` 不应因本次新增购买页而出现外呼（新增外呼只允许在 `app/web` 浏览器 JS 和 `commercial/`）。

## 10. 里程碑
1. **M1**（T1）：商业服务骨架 + mock 全链路 + DB + 静态页 + 单测。
2. **M2**（T2）：支付宝适配器 + 验签/回调单测 + 配置文档。
3. **M3**（T3）：本机购买页 + 轮询自动导入 + 配置/路由 + 页面单测。
4. **M4**（T4）：E2E + demo 脚本 + QA 报告。
5. **M5**（Lead）：集成、跑全量测试、最终验收与说明。
