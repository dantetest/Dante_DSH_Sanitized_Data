# W4a 人工签发与购买入口 A（操作手册）

> **2026-11-04 密钥轮换**：新签发使用 `prod-2026b`；旧 `prod-2026` 公钥继续保留，仅用于旧授权验签。
> 新私钥/新口令部署见 [生产License密钥部署手册](生产License密钥部署手册.md)。

> **2026-11-02 更新**：ICP 已下证；「正式收款 + 试用→付费→发码」全流程见
> [正式收款与发码SOP-ICP后-v1.0.md](正式收款与发码SOP-ICP后-v1.0.md)（含一键发码脚本 `deploy/issue_order.py`）。
> 本文保留为密钥仪式/签发命令的底层参考。

> 目标：在**没有微信/支付宝商户号、没有公网购买服务**的情况下，先能真实收款和交付：
> 客户看价格 → 对公/静态码付款 → 把本机识别码发给交付 → 人工签发 `license.json` → 客户导入。
>
> 这是 P0a 过渡闭环，不是 W4b 自动支付；W4b 等 ICP/商户号后做。

## 0. 先做一次密钥仪式（只做一次）

### 0.1 口令怎么定

推荐：

- 用 **密码管理器**（1Password/Bitwarden/KePass 等）生成 **24–32 位随机口令**；
- 不要用公司名、生日、`123456`、和软件登录密码相同的口令；
- 口令**只存密码管理器**，再写一份纸质密封件放保险柜/抽屉；
- **不要把口令写进**：仓库、`.env`、微信、邮件、脚本注释、截图、工单。

### 0.2 生成密钥（交互输入口令，不进命令历史）

```powershell
python deploy/license_keygen.py --key-id prod-2026b
```

它会提示：

```text
设置私钥口令（至少 8 位，建议 24 位以上；不会回显）:
再输入一次:
```

输出目录默认 `%USERPROFILE%\.sanitized-data-license-keys\`，生成：

- `license_signing_ed25519.pem`：**加密私钥**；
- `public_keys.json`：公钥片段；
- `key_info.txt`：key_id、创建时间、公钥 SHA-256 指纹（备份核对用）。

> 已有私钥时脚本默认拒绝覆盖；确需重建才用 `--force`。  
> **不要为了“重新生成一下”随手覆盖**：旧授权仍有效，但以后新授权只能用新 key_id。

### 0.3 备份怎么做（至少两份，异地）

需要备份的只有三个文件：

```text
license_signing_ed25519.pem   # 加密私钥
public_keys.json              # 公钥，可公开
key_info.txt                  # 指纹/创建时间，便于核对
```

推荐方案：

1. **副本 A**：U 盘/移动硬盘，放在家里/办公室安全处；
2. **副本 B**：另一块 U 盘或离线加密盘，放在不同地点（父母家/保险柜/银行保管箱）；
3. 口令单独存密码管理器 + 一份纸质密封件；
4. **不要把口令和私钥放在同一个 U 盘/同一个压缩包里**；
5. 私钥本身已用口令加密；如介质丢失，仍有口令保护。

Windows 上建议把目录权限收紧（当前用户可读写，其他用户不可读）：

```powershell
$keydir = "$env:USERPROFILE\.sanitized-data-license-keys"
icacls $keydir /inheritance:r
icacls $keydir /grant:r "$env:USERNAME:(OI)(CI)F"
```

### 0.4 做一次恢复演练（卖第一单前必须做）

```powershell
# 1) 把备份的 3 个文件复制到一个临时目录
$restore = "$env:TEMP\license-key-restore"
New-Item -ItemType Directory -Force $restore | Out-Null
Copy-Item "E:\SanitizedDataKeys\prod-2026b\*" $restore

# 2) 用恢复出来的私钥签一张测试 license
python deploy/license_issue.py `
  --private-key "$restore\license_signing_ed25519.pem" `
  --key-id prod-2026b --sku personal `
  --machine-code K7Q2-9WXM-4TRB-EFGH `
  --order-id ord_restore_drill `
  --out "$restore\drill-license.json"

# 3) 核对 key_info.txt 的公钥指纹是否一致
Get-Content "$restore\key_info.txt"
```

能签出 license、指纹一致，说明备份可用。演练完成后删除临时目录里的测试 license。

### 0.5 合并公钥并重新打包

1. 把 `public_keys.json` 内容合并到
   `app/server/yinzhan/licensing/public_keys.json`（只放公钥）；
2. 重新构建 v1.3.0 正式包；
3. 用测试 license 验证导入，再用 `prod-2026b` 签第一张真实 license；
4. 把 `key_info.txt` 的指纹记录到运营台账。

### 0.6 丢失/泄露预案

- **私钥丢失**：已发出的 license 仍可用；但无法再签发/续费。需要生成新 key_id、更新产品公钥、重新签发后续授权。
- **口令丢失**：私钥无法解密，等同丢失。
- **私钥泄露**：停用旧 key_id，生成新 key_id，重新签发；离线无法远程吊销旧 license，只能靠后续更新/服务约束。
- 建议每 **6 个月**做一次恢复演练，并检查备份介质是否可读。

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
# 运行后会提示“私钥口令（不会回显）”，输入即可；口令不进入命令历史
python deploy/license_issue.py `
  --private-key "$env:USERPROFILE\.sanitized-data-license-keys\license_signing_ed25519.pem" `
  --key-id prod-2026b `
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
  --key-id prod-2026b --sku pro `
  --machine-code K7Q2-9WXM-4TRB-EFGH `
  --machine-code P8R3-1ABC-2DEF-3GHJ `
  --order-id ord_20261025_0002 `
  --out out/licenses/ord_20261025_0002.json
```

### 团队版（5 席，机器码列表）

```powershell
python deploy/license_issue.py `
  --private-key "$env:USERPROFILE\.sanitized-data-license-keys\license_signing_ed25519.pem" `
  --key-id prod-2026b --sku team --seats 5 --team-id team_acme_001 `
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

