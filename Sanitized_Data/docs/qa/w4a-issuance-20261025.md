# W4a 人工签发与购买入口 A（2026-10-25）

> 范围：P0a 过渡闭环——没有微信/支付宝商户号时，先支持对公/静态码收款 + 人工签发 license。
> 不包含 W4b：自动支付、回调、自动解锁、自动开票、订单后台。

## 1. 已完成

| 能力 | 文件 | 说明 |
|---|---|---|
| 签发库 | `app/server/yinzhan/licensing/issue.py` | payload 构造、机器码校验、默认功能位、Ed25519 签名、写文件 |
| 签发 CLI | `deploy/license_issue.py` | individual/pro/team/org、annual/perpetual、多机器码、客户/订单/team_id |
| 密钥工具 | `deploy/license_keygen.py`（W1） | 生成 Ed25519 密钥对，私钥加密保存 |
| 操作手册 | `docs/ops/manual-license-issuance.md` | 密钥仪式、购买入口、下单、签发、交付、验收 |
| 订单台账 | `docs/ops/order-ledger-template.csv` | 人工订单/授权/发票登记 |
| 购买入口 A | W3 `/pricing` `/license` | `YINZHAN_PURCHASE_URL` 配置后，链接自动带 `sku/machine_code/from` |
| 测试 | `app/tests/test_license_issue.py` | 7 个用例；已加入 `run_all` |

## 2. 验证

- `test_license_issue.py`：**7/7 通过**
- `run_all --only licensing,licenseissue,quota`：**3/3 通过**
- CLI E2E：
  - `license_keygen.py` 生成测试密钥；
  - `license_issue.py --sku pro` 签发；
  - 用生成的公钥导入 → `state=paid`、`sku=pro`、features 含 `mcp`。
- 完整 `run_all`：**34/34 通过，325.45s**。

- 购买入口配置冒烟：`YINZHAN_PURCHASE_URL=https://example.com/buy` → `/api/license/status` 返回该 URL；`/license` 200。

## 3. 生产密钥仪式（**尚未执行**）

仓库内 `public_keys.json` 仍为空；真实签发前必须：

```powershell
$env:LICENSE_KEY_PASSWORD='<自己保管的口令>'
python deploy/license_keygen.py --key-id prod-2026 --out-dir "$env:USERPROFILE\.sanitized-data-license-keys"
```

然后：

1. 将生成的 `public_keys.json` 内容合并到
   `app/server/yinzhan/licensing/public_keys.json`；
2. **离线备份私钥** + 记录口令；
3. 重新打包，公钥才会进安装包；
4. 用测试 license（测试密钥）验证导入链路；
5. 再用 `prod-2026` 签发第一张真实 license。

> 未做生产密钥的原因：私钥保管/备份是业务决策，必须由你确认位置和口令；不能由代码静默生成后丢失。

## 4. 购买入口 A

- 配置环境变量 `YINZHAN_PURCHASE_URL=https://...` 后：
  - `/license` 的购买按钮打开该 URL，带 `sku/machine_code/from=license`；
  - `/pricing` 的购买按钮带 `sku/machine_code/from=pricing`。
- 未配置时明确显示“购买入口尚未配置”，不伪造外链。
- 静态二维码由购买平台生成（指向该 URL）；本产品不加载外部二维码服务。

## 5. 未做（W4b，等资质/服务端）

- 微信/支付宝自动支付、回调验签、自动解锁；
- 公网 `/buy` `/bind` `/order` 与订单后台；
- 自动开票/红冲/对账；
- 客户自助门户/团队管理台；
- 自动续费/到期提醒服务。

这些需要 ICP/商户号/公网购买服务，当前人工闭环不阻塞早鸟对公收款。
