# 支付宝接入配置指南（数据隐站 L3 付费解锁）

本文面向**运维 / 开发**，说明商业服务（`commercial/`）接入真实支付宝需要做的全部事情：
申请签约、密钥配置、两种 PayMode（当面付扫码 / 电脑网站支付）的差异与选择、沙箱联调、
真实小额验收清单、回调安全与排错、以及「当面付扫码能否用于线上虚拟商品」的合规提示。

相关文件（只看这三处即可）：

| 文件 | 作用 |
|---|---|
| `commercial/server/payments/alipay.py` | 支付宝适配器：RSA2 签名/验签、`alipay.trade.precreate`、`alipay.trade.page.pay`、`verify_notify` |
| `commercial/server/config.py` | `Config` 对象，属性名与下文环境变量**完全同名** |
| `commercial/tests/test_alipay.py` | 单元测试（自生成 RSA 测试密钥，不联网、不用真实密钥） |

> 安全红线：**应用私钥只放在商业服务机器上**，绝不进仓库/安装包/聊天记录；任何日志、接口响应、
> 错误信息都不得包含私钥或口令。仓库 `.gitignore` 已排除 `commercial/var/**` 与 `*.pem`。

---

## 1. 先搞清楚要签哪个产品（决定 `ALIPAY_PAY_MODE`）

适配器支持两条链路，对应支付宝开放平台的**两个不同产品**，能力与资质要求不同：

| | `precreate`（当面付扫码，推荐） | `page`（电脑网站支付，兜底） |
|---|---|---|
| 支付宝接口 | `alipay.trade.precreate` | `alipay.trade.page.pay` |
| 需要签约 | **当面付 / 扫码支付**（线下收单类产品） | **电脑网站支付**（网页支付类产品） |
| 服务端动作 | POST 网关，返回 `qr_code`（`https://qr.alipay.com/...`） | 本地拼好带签名的收银台 URL，不调网关 |
| 用户动作 | 手机支付宝/钱包**扫二维码**直接付款 | 浏览器**跳转收银台**付款；也可把该 URL 生成二维码给手机扫 |
| 体验 | 桌面端购买页不跳转，最顺 | 多一次页面跳转，或扫码后在手机上打开收银台 |
| 适配器返回 | `qr_content=<qr_code>`、`pay_url=null` | `qr_content=pay_url`（同一 URL） |
| 典型失败 | `ACQ.ACCESS_FORBIDDEN`（未签约当面付） | 未签约电脑网站支付时收银台报无权限 |

**选择建议**

1. 商户已签约**当面付** → `ALIPAY_PAY_MODE=precreate`（推荐，体验最好）。
2. 只有**电脑网站支付** → `ALIPAY_PAY_MODE=page`。服务端返回的 `pay_url` 同时作为
   `qr_content` 交给前端 `qr.py` 渲染二维码，用户扫码后在手机上打开支付宝收银台，
   一样能完成支付（只是体验路径略长）。
3. 未确定签约产品前：本地/沙箱可先用 `PAYMENT_PROVIDER=mock` 演示全链路，不影响真机联调。

> **合规注意**：当面付是面向线下收单场景的产品，线上虚拟商品（软件授权/订阅）能否用
> 「当面付扫码」以商户实际签约的产品与经营类目审核结果为准。若签约不通过，请使用
> `page` 模式（电脑网站支付）并遵守第 8 节合规提示；不要用个人收款码或二次清算绕过。

---

## 2. 申请与签约（一次性，由商户完成）

### 2.1 账号与实名

1. 注册企业支付宝账号 → [支付宝开放平台](https://open.alipay.com/) 完成**企业实名认证**。
2. 需要营业执照、法人信息、对公账户；虚拟商品/软件服务类目可能额外要求资质。
3. **商户号（2088 开头的 PID，例如你提供的 `2088780974864744`）不是 APPID**：
   - 开放平台「应用」的 APPID 形如 `2021xxxxxxxxxxxx`，要单独创建应用才得到；
   - 用**同一个企业支付宝账号**登录开放平台；若商户号属于另一个账号，需在开放平台完成
     商户关联/授权，否则应用无法绑定该商户的「电脑网站支付」。

### 2.2 创建应用，拿到 APP_ID

1. 开放平台控制台 →「控制台 → 产品与服务 → 应用」→ **创建应用** → 选择
   **网页应用**（电脑网站支付对应网页应用；不要选移动应用/小程序）。
2. 填写应用名称、图标、简介；按控制台提示提交审核（一般 1 个工作日；部分场景自动通过）。
3. 应用详情页顶部即可看到 **APPID**（形如 `2021xxxxxxxxxxxx`）→ 配置到 `ALIPAY_APP_ID`。
4. 应用详情 →「产品绑定」→ 添加/绑定「**电脑网站支付**」（你已签约，正常情况下可直接绑定生效）；
   若列表里没有，先去「产品中心 → 电脑网站支付」确认签约状态，或联系支付宝客服。

### 2.3 绑定「电脑网站支付」（`page` 模式必需）

开放平台 → 你的应用 →「产品绑定 → 电脑网站支付」→ 绑定：
- 需要已上线的网站域名（建议已完成 ICP 备案）、可访问的商品页/购买页；
- 绑定生效后应用才能调用 `alipay.trade.page.pay`；
- 正式收款前按控制台提示完成**应用上线/审核**，确认应用状态可用。

### 2.4 签约「当面付（扫码）」（`precreate` 模式必需）

开放平台 →「产品中心 → 当面付 / 当面付收单」→ 立即签约：
- 通常要求有**线下经营场景/门店信息**，审核比网页支付更严格；
- 审核通过后应用才能调用 `alipay.trade.precreate`，否则会返回
  `ACQ.ACCESS_FORBIDDEN`（无权限使用接口）。
- 若无法签约当面付，请退回 `page` 模式（第 1 节）。

### 2.5 记下商户 PID / seller_id（可选校验）

- PID 即商户支付宝账号的 2088 开头 ID，在商家中心可查；
- 配置 `ALIPAY_SELLER_ID` 后，适配器会校验异步通知里的 `seller_id` 是否一致，
  多商户/代运营场景建议开启；单商户可不填。

---

## 3. 密钥配置

### 3.1 生成 RSA2（2048 位）密钥对

1. 下载官方 **支付宝开放平台开发助手**（密钥工具）：
   <https://opendocs.alipay.com/common/02kipk>（Windows/Mac 客户端）。
2. 选择「RSA2」+「2048 位」→ 生成**应用私钥**与**应用公钥**。
3. 应用私钥自己保存；应用公钥下一步上传。

> 没有开发助手/OpenSSL 时，可直接用仓库脚本生成（等价 RSA2 2048 PKCS8）：
> ```powershell
> python commercial\scripts\gen_alipay_keys.py
> ```
> 产物：`commercial/var/keys/alipay_app_private.pem`（应用私钥，保密）+
> `commercial/var/keys/alipay_app_public.pem`（应用公钥，上传开放平台）。
> 脚本会打印应用公钥；上传后页面展示的「支付宝公钥」另存为 `alipay_public.pem`。

> 适配器签名算法固定为 RSA2（SHA256withRSA / PKCS#1 v1.5），不支持 RSA(1024)；
> 不要用 Ed25519 等其它密钥。

### 3.2 上传应用公钥，换取「支付宝公钥」（最容易搞混的一步）

1. 开放平台应用详情 →「开发设置 → 接口加签方式」→ 选择「公钥模式」；
2. 粘贴上一步生成的**应用公钥**；
3. 保存后页面会展示 **支付宝公钥**（这是支付宝平台的公钥，不是你生成的公钥！）；
4. 把 **支付宝公钥** 配置到 `ALIPAY_PUBLIC_KEY_PATH`。

> 两把公钥必须分清：
> - **应用公钥**：上传给支付宝，让支付宝验你发出的请求签名；商业服务本地一般**不需要**它
>   （只有单测/自检会用应用私钥推导公钥）。
> - **支付宝公钥**：配置到商业服务，用来**验支付宝的异步通知签名**和**同步响应签名**。
> 若在 `ALIPAY_PUBLIC_KEY_PATH` 里误填应用公钥，会出现「异步通知验签失败 / 响应验签失败」。

### 3.3 放置密钥文件

默认路径（`config.py` 已把相对路径解析到 `Sanitized_Data/` 根目录）：

```text
commercial/var/keys/alipay_app_private.pem   # 应用私钥（RSA2，PKCS8）
commercial/var/keys/alipay_public.pem        # 支付宝公钥（SPKI）
```

支持格式（适配器自动识别，无需转换）：

- 应用私钥：PEM（`-----BEGIN PRIVATE KEY-----` PKCS8 或 `BEGIN RSA PRIVATE KEY` PKCS1）；
  也支持**裸 base64**（密钥工具里「复制私钥」不带页眉页脚的串，适配器自动补 PKCS8 头）。
- 支付宝公钥：PEM（`-----BEGIN PUBLIC KEY-----`，或 PKCS1 `BEGIN RSA PUBLIC KEY`），
  或裸 base64，或 X.509 证书 PEM（`BEGIN CERTIFICATE`）。

文件权限（建议）：

```powershell
# Windows：仅允许运行商业服务的账号读取（按实际账号调整）
icacls "commercial\var\keys\alipay_app_private.pem" /inheritance:r /grant:r "$env:USERNAME:(R)"
```

```bash
# Linux
chmod 600 commercial/var/keys/alipay_app_private.pem
chown svc-commercial commercial/var/keys/alipay_app_private.pem
```

> 不要把 `commercial/var/keys/` 放进安装包；服务器重装时重新下发密钥文件。

### 3.4 环境变量（`Config` 属性同名）

在 `commercial/.env` 或进程环境中配置（详见 `commercial/.env.example`）：

```dotenv
# 支付通道：真实支付宝
PAYMENT_PROVIDER=alipay

# APPID（开放平台应用详情可见）
ALIPAY_APP_ID=2021000000000001

# 应用私钥 / 支付宝公钥（绝对路径或相对 Sanitized_Data 的路径）
ALIPAY_APP_PRIVATE_KEY_PATH=commercial/var/keys/alipay_app_private.pem
ALIPAY_PUBLIC_KEY_PATH=commercial/var/keys/alipay_public.pem

# 网关：正式默认 https://openapi.alipay.com/gateway.do
# 沙箱：https://openapi-sandbox.dl.alipaydev.com/gateway.do
ALIPAY_GATEWAY=https://openapi.alipay.com/gateway.do

# 支付模式：precreate=当面付扫码（推荐）；page=电脑网站支付（兜底）
ALIPAY_PAY_MODE=precreate

# 异步通知地址：必须公网 HTTPS、能被支付宝服务器访问（不填则用 COMMERCIAL_BASE_URL + /notify/alipay）
ALIPAY_NOTIFY_URL=https://sd.blctek.com:8443/notify/alipay

# 公网基础地址（用于拼默认 notify_url 与回跳页）
COMMERCIAL_BASE_URL=https://sd.blctek.com:8443

# 可选：校验异步通知 app_id（默认 1=校验，务必保持开启）
ALIPAY_APP_ID_CHECK=1
# 可选：校验 seller_id（多商户/代运营时填）
ALIPAY_SELLER_ID=
```

| 变量 | 必填 | 说明 |
|---|---|---|
| `PAYMENT_PROVIDER` | 是 | `alipay` 才会走本适配器；`mock` 为演示通道 |
| `ALIPAY_APP_ID` | 是 | 开放平台 APPID；异步通知会校验（`ALIPAY_APP_ID_CHECK=1`） |
| `ALIPAY_APP_PRIVATE_KEY_PATH` | 是 | RSA2 应用私钥（PEM 或裸 base64） |
| `ALIPAY_PUBLIC_KEY_PATH` | 是 | **支付宝公钥**（验通知/响应签名） |
| `ALIPAY_GATEWAY` | 是 | 正式/沙箱网关；也接受 `sandbox` / `prod` 别名 |
| `ALIPAY_PAY_MODE` | 是 | `precreate`（默认）或 `page` |
| `ALIPAY_NOTIFY_URL` | 建议 | 不填自动用 `${COMMERCIAL_BASE_URL}/notify/alipay`；生产必须 HTTPS |
| `COMMERCIAL_BASE_URL` | 是 | 公网 HTTPS 基础地址 |
| `ALIPAY_SELLER_ID` | 否 | 配置后校验通知 `seller_id` |
| `ALIPAY_APP_ID_CHECK` | 否 | 默认 `1`；关闭仅用于特殊调试（不推荐） |
| `ALIPAY_QUERY_FALLBACK` | 否 | 默认 `false`；设为 `true` 后，本机购买页轮询订单时服务端会按节流调用 `alipay.trade.query` **主动查单**。notify_url 暂时不可达/延迟时，付款后约 ≤7 秒也能自动确认、签发、解锁 |
| `ALIPAY_QUERY_MIN_INTERVAL_SECONDS` | 否 | 查单节流间隔，默认 `5`（最小 2） |
| `ALIPAY_QUERY_TIMEOUT` | 否 | 查单网关超时秒数，默认 `10`（1–60） |

### 3.5 自检（不打印密钥内容）

```bash
python - <<'PY'
import sys
sys.path.insert(0, "commercial/server")
from payments.alipay import load_private_key, load_public_key

priv = load_private_key("commercial/var/keys/alipay_app_private.pem")
pub = load_public_key("commercial/var/keys/alipay_public.pem")
print("private key type:", type(priv).__name__, "bits:", priv.key_size)
print("public  key type:", type(pub).__name__, "bits:", pub.key_size)
print("OK（只打印类型/位数，不打印密钥内容）")
PY
```

Windows PowerShell 等价写法：

```powershell
@'
import sys
sys.path.insert(0, r"commercial/server")
from payments.alipay import load_private_key, load_public_key
priv = load_private_key(r"commercial/var/keys/alipay_app_private.pem")
pub = load_public_key(r"commercial/var/keys/alipay_public.pem")
print("private:", type(priv).__name__, priv.key_size)
print("public :", type(pub).__name__, pub.key_size)
'@ | python -X utf8 -
```

### 3.6 `notify_url` 要求（回调收不到 90% 是这里）

- **必须公网可访问**，生产用 **HTTPS**（支付宝要求；自签证书/证书链不完整会导致回调失败）；
- 域名建议已备案，能通过浏览器/`curl` 从外网访问；
- 路径固定为商业服务路由 `POST /notify/alipay`（即 `https://<域名>/notify/alipay`）；
- 不要带多余 query 参数（长度上限 256 字符）；
- 不要 301/302 跳转，不要登录鉴权，不要 WAF 拦截 `POST`；
- 处理成功必须返回**纯文本 `success`**（HTTP 200），失败返回 `failure`，支付宝会重试；
- 反向代理要透传 `Content-Type: application/x-www-form-urlencoded` 与原始 body。

---

## 4. 两种 PayMode 的数据流差异

### 4.1 `precreate`（当面付扫码，推荐）

```text
购买页 --POST /api/orders--> 商业服务
  商业服务: 组装 biz_content(out_trade_no/total_amount/subject)
          + 公共参数(app_id/method/format/charset/sign_type/timestamp/version/notify_url)
          + RSA2 签名 → POST 支付宝网关 alipay.trade.precreate
  支付宝: 返回 {"alipay_trade_precreate_response":{"code":"10000","qr_code":"https://qr.alipay.com/..."},"sign":"..."}
  商业服务: 支付宝公钥验证响应签名 → 取 qr_code → qr_svg 返回给购买页
购买页: 展示二维码 → 用户手机扫码支付
支付宝 --> POST /notify/alipay（异步通知）--> 商业服务验签/金额/幂等 → 签发 license
购买页轮询 /api/orders 拿到 paid → POST /api/license/import 自动解锁
```

适配器返回：

```json
{"qr_content": "https://qr.alipay.com/bavh4wjlxf12tper3a", "pay_url": null, "provider_ref": null}
```

### 4.2 `page`（电脑网站支付，兜底）

```text
购买页 --POST /api/orders--> 商业服务
  商业服务: 组装 biz_content(out_trade_no/total_amount/subject/product_code=FAST_INSTANT_TRADE_PAY)
          + 公共参数 + return_url/notify_url + RSA2 签名 → 拼出收银台 URL（不调网关）
  商业服务: 返回 pay_url（同时作为 qr_content）→ 前端渲染 qr_svg
购买页: 展示二维码；手机扫码后打开支付宝收银台（或浏览器直接跳转 pay_url）
支付宝 --> POST /notify/alipay --> 商业服务验签/金额/幂等 → 签发 license
```

适配器返回：

```json
{"qr_content": "<同一收银台URL>", "pay_url": "<收银台URL>", "provider_ref": null}
```

> `page` 模式没有「响应验签」环节（URL 是本地签名生成的）；风险点在于
> `notify_url` 的可达性与签名配置是否正确，务必走完第 6 节验收清单。

### 4.3 切换方式

只改 `ALIPAY_PAY_MODE` 并重启商业服务即可，无需改代码：
- 已签约当面付 → `precreate`；
- 仅电脑网站支付 → `page`；
- 非法值（如 `h5`）会在下单时抛 `payment_create_failed`，并提示合法取值。

---

## 5. 沙箱联调步骤

### 5.1 进入沙箱环境

1. 登录 [支付宝开放平台](https://open.alipay.com/) → 右上角「控制台 → 沙箱」；
2. 沙箱环境有独立 **沙箱 APPID**、独立密钥配置、**沙箱买家账号**（含登录密码/支付密码）；
3. 在沙箱应用详情里按第 3 节方式配置「公钥模式」，换到沙箱 **支付宝公钥**；
4. 沙箱不支持真实资金，买家账号余额是虚拟的。

### 5.2 沙箱网关

```dotenv
ALIPAY_GATEWAY=https://openapi-sandbox.dl.alipaydev.com/gateway.do
```

- 以上为当前官方沙箱网关（新沙箱）；老文档/老账号可能仍使用
  `https://openapi.alipaydev.com/gateway.do`，如沙箱域名解析/证书异常可直接用
  `ALIPAY_GATEWAY` 覆盖为实际可用的网关（以开放平台沙箱页面展示为准）。
- 请求中的时间戳按北京时间生成（适配器已固定 +08:00），与服务器时区无关。

### 5.3 配置并在本地/测试服务器启动

```dotenv
PAYMENT_PROVIDER=alipay
ALIPAY_APP_ID=<沙箱 APPID>
ALIPAY_APP_PRIVATE_KEY_PATH=commercial/var/keys/sandbox_app_private.pem
ALIPAY_PUBLIC_KEY_PATH=commercial/var/keys/sandbox_alipay_public.pem
ALIPAY_GATEWAY=https://openapi-sandbox.dl.alipaydev.com/gateway.do
ALIPAY_PAY_MODE=precreate
COMMERCIAL_BASE_URL=http://127.0.0.1:8787
ALIPAY_NOTIFY_URL=http://127.0.0.1:8787/notify/alipay
```

```powershell
# 启动商业服务（按 T1 的 run_dev 脚本）
python commercial/scripts/run_dev.py
```

> 沙箱联调阶段 `notify_url` 可以用本机/内网地址，但**支付宝服务器无法回调内网**，
> 因此建议：
> 1) 用 `curl`（或用官方「沙箱通知模拟」工具）手工 POST 一条签名正确的通知到
>    `http://127.0.0.1:8787/notify/alipay`，验证验签/金额/签发链路；
> 2) 或使用内网穿透（frp/ngrok 等）把本地 8787 暴露成公网 HTTPS，再把该地址填进
>    `ALIPAY_NOTIFY_URL` 做真实回调联调。**生产必须公网 HTTPS，不能靠内网穿透。**

### 5.4 联调步骤（逐条执行）

1. `GET http://127.0.0.1:8787/healthz` → `{"ok":true,"provider":"alipay"}`；
2. `GET http://127.0.0.1:8787/api/catalog` → `channels` 里 `alipay.enabled=true`；
3. `POST /api/orders`（JSON：`sku=personal`、`machine_code`、`channel=alipay`）→ 应返回
   `qr_content`（`precreate` 下为 `https://qr.alipay.com/...`）与 `qr_svg`；
4. 用**沙箱钱包 APP / 沙箱买家账号**扫码支付（真实支付宝 APP 扫沙箱码会失败）；
5. 观察商业服务日志：`/notify/alipay` 返回 `success`，订单状态变 `paid`；
6. `GET /api/orders/{order_no}?token=...` → `status=paid` 且带 `license`；
7. 本机购买页轮询到 `paid` 后自动导入 license，显示「已解锁」。

### 5.5 沙箱常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 扫沙箱二维码提示「订单不存在/无效」 | 用了真实支付宝 APP 扫沙箱码 | 用沙箱钱包/沙箱买家账号扫码 |
| `isv.invalid-app-id` | 沙箱 APPID 与密钥/网关环境不匹配 | 确认沙箱 APPID + 沙箱网关 + 沙箱密钥三者同一环境 |
| `isv.invalid-signature` / `sign check fail` | 应用私钥与上传的应用公钥不配对；或密钥被换行破坏 | 用 3.5 自检 + 重新上传应用公钥 |
| 回调收不到 | `notify_url` 内网/HTTP/被防火墙拦 | 内网穿透或手工模拟通知；生产公网 HTTPS |
| `ALIPAY_PAY_MODE=precreate` 报 `ACQ.ACCESS_FORBIDDEN` | 沙箱应用未开通当面付 | 沙箱控制台开通对应产品，或临时用 `page` 联调 |
| 时间戳错误 / 签名偶发失败 | 服务器时间偏差过大 | 校准系统时间（NTP） |

### 5.6 本地单元测试（不需要真实密钥/网络）

```powershell
cd Sanitized_Data
C:\Users\Dante\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe `
  -X utf8 -m pytest commercial/tests/test_alipay.py -q
```

覆盖：RSA2 请求签名/篡改失败、异步通知验签/篡改金额或签名失败、precreate 响应验签
（伪造签名失败）、金额「元↔分」、`app_id`/`seller_id` 校验、page 模式、密钥格式与边界。

---

## 6. 真实小额验收清单（上线前逐项打勾）

### 6.1 配置

- [ ] `PAYMENT_PROVIDER=alipay`，`ALIPAY_PAY_MODE` 与实际签约产品一致；
- [ ] `ALIPAY_APP_ID` = 正式应用 APPID（不是沙箱、不是 PID）；
- [ ] 应用私钥为**正式环境**应用私钥；`ALIPAY_PUBLIC_KEY_PATH` 为**正式支付宝公钥**；
- [ ] `ALIPAY_GATEWAY=https://openapi.alipay.com/gateway.do`（正式）；
- [ ] `ALIPAY_NOTIFY_URL` 是公网 HTTPS 地址，外网 `curl -X POST` 能到达且返回 `success`；
- [ ] `ALIPAY_APP_ID_CHECK=1`；`ALIPAY_SELLER_ID` 按需开启；
- [ ] 服务器时间已校时；商业服务日志级别合理且不打印密钥。

### 6.2 支付链路（用 0.01 元或最低价商品实测）

- [ ] `POST /api/orders` 成功拿到 `qr_content`/`qr_svg`，前端二维码可被支付宝扫出；
- [ ] 支付 0.01 元 → 异步通知到达 → 日志包含验签通过、金额核对、`trade_no`；
- [ ] `GET /api/orders/{no}?token=` 返回 `paid` 且 `license` 可被本机导入解锁；
- [ ] 同一通知重复 POST → 依旧返回 `success`，不重复签发（幂等，`payments.transaction_id` 唯一）；
- [ ] 用错误签名/篡改金额的通知 POST → 返回 `failure`，订单保持 `pending`；
- [ ] 订单 15 分钟过期后再支付/通知 → 不签发（按商业服务订单过期策略处理）；
- [ ] `page` 模式额外验证：跳转/扫码打开的收银台能完成支付并回调。

### 6.3 回滚与客服

- [ ] 已支付订单的退款走支付宝商家后台（本系统只负责收款与签发，不做自动退款）；
- [ ] 明确客服流程：用户重复支付/换机/重装时，按订单号人工补发或重新导入 license；
- [ ] 备份 `commercial/var/commercial.db` 与 license 私钥；确认私钥丢失=无法续发授权。

### 6.4 数据核查（SQLite）

```sql
-- 最近订单
SELECT order_no, sku, amount_cents, status, provider_ref, created_at, paid_at
FROM orders ORDER BY created_at DESC LIMIT 20;
-- 支付流水（transaction_id 唯一，防重复签发）
SELECT order_no, provider, transaction_id, amount_cents, created_at
FROM payments ORDER BY id DESC LIMIT 20;
-- 授权签发
SELECT license_id, order_no, machine_code, sku, issued_at FROM licenses ORDER BY issued_at DESC LIMIT 20;
```

---

## 7. 回调安全与排错

### 7.1 服务端验签流程（`AlipayProvider.verify_notify`）

1. 从通知 form 中**剔除 `sign` 与 `sign_type`**，其余参数按 key 字典序拼
   `k=v&...`（值使用**原始解码值**，不 URL 编码）；
2. 用**支付宝公钥**做 RSA2（SHA256withRSA）验签；失败 → `invalid_signature`；
3. 校验 `app_id == ALIPAY_APP_ID`（`ALIPAY_APP_ID_CHECK=1`，默认开启）；失败 → `app_id_mismatch`；
4. 如配置了 `ALIPAY_SELLER_ID`，校验通知 `seller_id`；失败 → `seller_id_mismatch`；
5. `total_amount` 用 Decimal 精确转 int 分（拒绝非整分、越界金额）；
6. `trade_status` 只接受 `TRADE_SUCCESS` / `TRADE_FINISHED`；
7. 返回 `{"order_no","transaction_id","amount_cents","raw","trade_status"}`；
   由 app.py 继续做：订单存在性、金额与 DB 一致、`trade_no` 幂等、标记 paid、签发 license；
8. 处理成功返回纯文本 `success`，失败返回 `failure`（HTTP 200，支付宝会按策略重试）。

### 7.2 常见错误对照表

| 现象 / 错误码 | 常见原因 | 处理 |
|---|---|---|
| `isv.invalid-app-id` | APPID 填错；沙箱/正式混用；应用未上线 | 核对开放平台 APPID 与网关环境 |
| `isv.missing-method` / `isv.invalid-parameter` | 缺少公共参数；`biz_content` 非法 | 检查适配器版本；确认 `method`/`charset`/`biz_content` |
| `isv.invalid-signature` / `sign check fail` | 应用私钥与上传的应用公钥不配对；私钥被换行/空格破坏；用错环境密钥 | 重新用密钥工具核对；用 3.5 自检 |
| `hash sign check fail`（异步通知） | `ALIPAY_PUBLIC_KEY_PATH` 填了应用公钥或错误环境的支付宝公钥 | 换成**当前环境**的支付宝公钥 |
| `ACQ.ACCESS_FORBIDDEN` | 未签约「当面付」或「电脑网站支付」 | 签约对应产品；或切换 PayMode |
| `ACQ.PARTNER_ERROR` | APPID 与商户账号状态异常/不匹配 | 商家中心确认应用归属与状态 |
| `ACQ.TRADE_HAS_SUCCESS` | 同一 `out_trade_no` 重复下单 | 检查下单重试逻辑；订单号必须唯一 |
| 金额相关报错 / 回调金额对不上 | 把「分」当「元」或反之 | 商业服务内部一律 int 分；适配器自动转 `x.xx` 元 |
| 回调一直未收到 | `notify_url` 不可达/非 HTTPS/证书链不完整/被 WAF 拦/返回非 success | 外网 curl 自测；查看支付宝通知重试记录；修正后重新触发 |
| 回调验签通过但订单不 paid | 金额与 DB 不一致、订单已过期、`trade_no` 重复 | 查商业服务日志与 `orders/payments` 表 |
| `isv.invalid-timestamp` | 服务器时间偏差过大 | 校时（NTP）|
| 网络超时 | 本机到支付宝网关出网受限 | 放通出网 443；检查代理/DNS |

### 7.3 日志与脱敏

- 允许记录：订单号、金额、`trade_no`、`trade_status`、错误码、验签结果；
- **禁止记录**：应用私钥、支付宝公钥全文、`sign` 原文之外的密钥材料、买家敏感信息；
- 适配器的异常消息只包含类型/字段名/错误码，不含密钥正文；排查时优先看
  `payment_create_failed` 的具体消息（含支付宝 `sub_code`/`sub_msg`）。

### 7.4 订单与二维码有效期

- 商业服务订单默认 15 分钟过期；用户过期后扫码支付可能仍成功，但 app 侧
  会按订单状态拒绝签发，需要在客服流程中人工核对/退款；
- 如需让支付宝二维码同步短时失效，可在 `biz_content` 里加 `timeout_express`
  （如 `15m`），该能力需按实际订单策略评估后由开发调整适配器；
- 用户支付成功但页面关闭：重新打开订单查询页/购买页轮询即可补拿 license。

---

## 8. 合规提示：「当面付扫码」能否用于线上虚拟商品

1. **以签约产品为准**：`precreate`（当面付）是线下收单类产品，线上虚拟商品
   （软件授权/年度订阅/下载类）是否允许，取决于商户经营的类目与支付宝审核结论；
   未签约当面付时接口会返回 `ACQ.ACCESS_FORBIDDEN`，此时**只能用 `page` 模式**
   （电脑网站支付）生成收银台，再把收银台 URL 渲染成二维码让用户扫码打开。
2. **不得使用个人收款码 / 个人账户代收**；不得二次清算、拆单、跑分；不得用
   客户端传入的金额生成授权（金额一律以服务端 DB/SKU 为准）。
3. **类目与资质**：虚拟商品/软件服务类目可能需要额外资质；请按支付宝要求提交，
   并在购买页展示服务内容、价格、有效期、退款政策与联系方式。
4. **发票与税务**：按实际销售主体开票、申报纳税；保留订单与支付流水至少 3 年。
5. **退款**：本系统只做收款与授权签发，退款建议在支付宝商家后台人工处理，
   并同步作废对应 license（客服 SOP 中明确）。
6. 正式上线前建议与支付宝服务商/客户经理确认「线上虚拟商品 + 扫码支付」的可用产品，
   避免因产品错配导致拒付率/风控问题。

---

## 9. 参考链接（官方）

- 支付宝开放平台：<https://open.alipay.com/>
- 沙箱环境说明：<https://opendocs.alipay.com/common/02kkv7>
- 当面付预下单 `alipay.trade.precreate`：<https://opendocs.alipay.com/open/02ekfg>
- 电脑网站支付 `alipay.trade.page.pay`：<https://opendocs.alipay.com/open/270/105899>
- 异步通知验签说明：<https://opendocs.alipay.com/common/02mse7>
- 密钥/签名工具（开发助手）：<https://opendocs.alipay.com/common/02kipk>
- 公共错误码：<https://opendocs.alipay.com/common/02km9f>

> 文档中的产品入口与链接可能随开放平台改版变化；以登录后控制台实际展示为准。
> 真实支付宝尚未在本项目联调（当前只有基于测试密钥的单元测试），首次上线请严格按
> 第 6 节清单完成沙箱 + 真实 0.01 元验收。
