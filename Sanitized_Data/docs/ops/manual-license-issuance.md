# W4a 人工签发与购买入口 A（操作手册）

> 目标：在**没有微信/支付宝商户号、没有公网购买服务**的情况下，先能真实收款和交付：
> 客户看价格 → 对公/静态码付款 → 把本机识别码发给交付 → 人工签发 `license.json` → 客户导入。
>
> 这是 P0a 过渡闭环，不是 W4b 自动支付；W4b 等 ICP/商户号后做。

## 0. 先做一次密钥仪式（只做一次）

```powershell
# 口令自己设置并离线保管；不要写进仓库、聊天记录
$env:LICENSE_KEY_PASSWORD = '<至少 8 位，自己记住>'
python deploy/license_keygen.py --key-id prod-2026 --out-dir "$env:USERPROFILE\.sanitized-data-license-keys"
```

会生成：

- `license_signing_ed25519.pem`：**私钥**，加密保存，只能在本机/签发机使用；
- `public_keys.json`：公钥片段，格式 `{ "prod-2026": "..." }`。

然后：

1. 把 `public_keys.json` 的内容**合并**到
   `app/server/yinzhan/licensing/public_keys.json`（只放公钥）；
2. 备份私钥到离线介质（U 盘/密码管理器），记录口令位置；
3. 重新打包安装包（公钥才会进产品）；
4. **私钥丢失 = 无法再签发/续费**；私钥泄露 = 需要停用 `prod-2026`、换新 key_id 并重签。

> 仓库里的 `public_keys.json` 默认为空，拒绝任何真实授权导入；这是故意的安全默认。

## 1. 配置购买入口

产品内 `/pricing` 和控制台「升级」按钮读取：

```
YINZHAN_PURCHASE_URL=https://你的购买页地址
```

- 可以是微店/有赞/淘宝/企业微信收款页/官网 `/buy`；
- 配置后，购买链接自动带上：
  - `sku`（personal/pro/team）
  - `machine_code`（本机识别码，便于交付绑定）
  - `from=license|pricing`
- 未配置时页面明确显示“购买入口尚未配置”，不伪造链接。

> A 模式是**静态链接/二维码 + 回填授权**，不是扫码自动解锁。二维码可以用购买平台自带能力生成（指向上面 URL），本产品不加载外部二维码服务。

## 2. 客户下单（人工/对公）

1. 客户在 `/pricing` 选版本，点击购买（或直接联系交付）；
2. 客户在 `/license` 复制「本机识别码」；
3. 客户付款（对公转账/静态码/平台订单），把以下信息发给交付：
   - 订单号/支付截图
   - 本机识别码
   - 版本（个人/专业/团队/机构）
   - 开票信息（如需）
4. 交付在订单台账登记，确认到账后签发。

订单台账模板：[order-ledger-template.csv](order-ledger-template.csv)

## 3. 签发 license

### 个人版（1 台，1 年）

```powershell
$env:LICENSE_KEY_PASSWORD = '<口令>'
python deploy/license_issue.py `
  --private-key "$env:USERPROFILE\.sanitized-data-license-keys\license_signing_ed25519.pem" `
  --key-id prod-2026 `
  --sku personal `
  --machine-code K7Q2-9WXM-4TRB-EFGH `
  --order-id ord_20261025_0001 `
  --customer-name "张三" `
  --out out/licenses/ord_20261025_0001.json
```

### 专业版（2 台，含 MCP）

```powershell
python deploy/license_issue.py `
  --private-key "$env:USERPROFILE\.sanitized-data-license-keys\license_signing_ed25519.pem" `
  --key-id prod-2026 --sku pro `
  --machine-code K7Q2-9WXM-4TRB-EFGH `
  --machine-code P8R3-1ABC-2DEF-3GHJ `
  --order-id ord_20261025_0002 `
  --out out/licenses/ord_20261025_0002.json
```

### 团队版（5 席，机器码列表）

```powershell
python deploy/license_issue.py `
  --private-key "$env:USERPROFILE\.sanitized-data-license-keys\license_signing_ed25519.pem" `
  --key-id prod-2026 --sku team --seats 5 --team-id team_acme_001 `
  --machine-code AAAA-BBBB-CCCC-DDDD `
  --machine-code EEEE-FFFF-GGGG-HHHH `
  --machine-code JJJJ-KKKK-LLLL-MMMM `
  --order-id ord_20261025_0003 `
  --out out/licenses/ord_20261025_0003.json
```

### 买断 / 自定义到期

```powershell
python deploy/license_issue.py ... --term perpetual --out ...
python deploy/license_issue.py ... --expires-at 2027-12-31T23:59:59+08:00 --out ...
```

### 续费 / 升级 / 换机

- 续费：同机器码、新订单号、新到期日，重新签发；旧文件自动备份 `.json.bak`；
- 升级：新 SKU + 新订单号 + 差价记录，重新签发；
- 换机：新机器码重新签发；订单台账备注“换机/重签”。

## 4. 交付与导入

1. 把生成的 `license.json` 发给客户（邮件/企业微信/网盘）；
2. 客户打开 `/license` → 拖入/选择/粘贴 → 「导入授权」；
3. 状态变「已授权」，功能位生效；
4. 客户可点「备份当前授权」保存副本；
5. 交付在台账登记 `license_id`、签发时间、到期时间。

## 5. 验收清单

- [ ] 私钥已生成、离线备份、口令已保管
- [ ] 公钥已合并到 `public_keys.json` 并重新打包
- [ ] 安装包内公钥可验签（用测试 license 导入）
- [ ] `/pricing` 购买入口 URL 已配置（如使用）
- [ ] 订单台账已建立
- [ ] 发票/退款流程有人负责
- [ ] 私钥不进入仓库、安装包、CI 日志、聊天记录

## 6. 明确不做（W4b）

- 微信/支付宝自动支付、回调、自动解锁
- 自动开票/红冲/对账
- 订单后台、客户自助门户、团队管理台
- 购买页自动绑定/下载 license

这些等 ICP/商户号/公网购买服务到位后再做；当前人工闭环可以先收对公/早鸟。
