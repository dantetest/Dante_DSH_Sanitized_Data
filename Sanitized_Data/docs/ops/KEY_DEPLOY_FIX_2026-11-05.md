# 生产支付失败排查：密钥未部署（2026-11-05）

## 结论

**不是桌面安装包的问题，也不是 Git 打包漏了私钥。**

私钥按设计**不应该**进 Git、也不应该进桌面安装包：

- `commercial/.gitignore` 忽略了整个 `commercial/var/`，所以 `commercial/var/keys/*.pem` 不在 Git；
- 桌面绿色版/安装包只包含客户端，商业服务（`commercial/`）不打包进桌面版；
- 生产服务器上的 `commercial/var/` 截图里只有 `commercial.db`，没有 `keys/` 目录。

所以支付宝适配器加载不到应用私钥 → 下单失败。日志里只显示 `code=payment_create_failed`，是因为 `AlipayProvider.create()` 把底层 `alipay_key_invalid` 统一包装成了下单失败；v1.4.0 商业服务已改为在日志里带 `detail=...`，下次会直接看到具体原因。

另外发现两个必须一起修的问题：

1. **生产 `/api/catalog` 的 personal 仍是 19900 分**：服务器 `.env` 还没改新价；桌面价格页虽然是 99，但购买页金额以服务端为准，会按 ¥199 下单。
2. **生产 `.env` 的 `LICENSE_PRIVATE_KEY_PATH` 很可能还是 Windows 路径**（本地 `.env` 是 `C:\Users\Dante\...`）。Linux 服务上这个路径不存在；即使支付修好，回调签发 License 也会失败（`license_issue_failed`）。

## 需要部署的文件

本机现有：

```text
D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\commercial\var\keys\alipay_app_private.pem
D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\commercial\var\keys\alipay_public.pem
C:\Users\Dante\.sanitized-data-license-keys\prod-2026b\license_signing_ed25519.pem
```

服务器目标（按截图路径）：

```text
/opt/cornkernel_alpha/DSH_DESKTOP_WORKSPACE/Sanitized_Data/commercial/var/keys/
├── alipay_app_private.pem
├── alipay_public.pem
└── license_signing_ed25519.pem   # 也可放 /etc/sanitized-data/，推荐
```

**不要**把私钥提交 Git、发群、发安装包，或写进工单截图。

## 上传/权限

如果本机能直连服务器（把 `<user>` 换成实际 SSH 用户，不能用 root 跑服务就换成服务用户）：

```bash
ssh <user>@buysd.blctek.com
sudo mkdir -p /opt/cornkernel_alpha/DSH_DESKTOP_WORKSPACE/Sanitized_Data/commercial/var/keys
```

在本机 PowerShell：

```powershell
scp "D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\commercial\var\keys\alipay_app_private.pem" <user>@buysd.blctek.com:/opt/cornkernel_alpha/DSH_DESKTOP_WORKSPACE/Sanitized_Data/commercial/var/keys/
scp "D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\commercial\var\keys\alipay_public.pem" <user>@buysd.blctek.com:/opt/cornkernel_alpha/DSH_DESKTOP_WORKSPACE/Sanitized_Data/commercial/var/keys/
scp "C:\Users\Dante\.sanitized-data-license-keys\prod-2026b\license_signing_ed25519.pem" <user>@buysd.blctek.com:/opt/cornkernel_alpha/DSH_DESKTOP_WORKSPACE/Sanitized_Data/commercial/var/keys/
```

服务器上：

```bash
cd /opt/cornkernel_alpha/DSH_DESKTOP_WORKSPACE/Sanitized_Data
chmod 700 commercial/var/keys
chmod 600 commercial/var/keys/alipay_app_private.pem
chmod 600 commercial/var/keys/license_signing_ed25519.pem
chmod 644 commercial/var/keys/alipay_public.pem
```

如果使用 WinSCP/宝塔文件管理器，上传后同样要把两个私钥权限改成 `600`，公钥 `644`。

## 修正 `.env`

服务器上的 `commercial/.env`（绝对路径更稳）：

```env
ALIPAY_APP_ID=2021007114621412
ALIPAY_APP_PRIVATE_KEY_PATH=/opt/cornkernel_alpha/DSH_DESKTOP_WORKSPACE/Sanitized_Data/commercial/var/keys/alipay_app_private.pem
ALIPAY_PUBLIC_KEY_PATH=/opt/cornkernel_alpha/DSH_DESKTOP_WORKSPACE/Sanitized_Data/commercial/var/keys/alipay_public.pem
ALIPAY_PAY_MODE=page
ALIPAY_QR_PAY_MODE=2

LICENSE_PRIVATE_KEY_PATH=/opt/cornkernel_alpha/DSH_DESKTOP_WORKSPACE/Sanitized_Data/commercial/var/keys/license_signing_ed25519.pem
LICENSE_KEY_ID=prod-2026b
LICENSE_KEY_PASSWORD_ENV=LICENSE_KEY_PASSWORD

# 2026-11-05 价格
COMMERCIAL_PRICE_PERSONAL_CENTS=9900
COMMERCIAL_PRICE_PERSONAL_RENEWAL_CENTS=12900
COMMERCIAL_PRICE_PERSONAL_PERPETUAL_CENTS=39900
COMMERCIAL_SELF_SERVE_PRO_ENABLED=false
```

注意：

- `LICENSE_KEY_PASSWORD_ENV` 是**变量名**，不是口令本身。
- 私钥是加密的（`BEGIN ENCRYPTED PRIVATE KEY`），必须把生成私钥时的口令通过 systemd `EnvironmentFile` 注入 `LICENSE_KEY_PASSWORD`；没有口令无法签发。
- 不要猜口令；如果确实丢失，按 `生产License密钥部署手册.md` 轮换新 key_id，不要覆盖旧私钥。
- `.env` 权限建议 `600`，且不要提交 Git。

## 重启与验证

```bash
sudo systemctl daemon-reload
sudo systemctl restart sanitized-commercial
sudo systemctl status sanitized-commercial --no-pager
```

在 `Sanitized_Data` 目录运行（需要服务器代码包含 v1.4.0 的脚本）：

```bash
python commercial/scripts/check_deploy.py
```

期望：

```json
"prices_cents": {
  "personal": 9900,
  "personal_renewal": 12900,
  "personal_perpetual": 39900
},
"ok": true
```

再验证 catalog：

```bash
curl -s https://buysd.blctek.com/api/catalog | python -m json.tool | head -40
```

应看到 `personal.price_cents = 9900`、`renewal_price_cents = 12900`、`personal_perpetual.price_cents = 39900`。

最后用**桌面 v1.4.0 购买页**走一次真实 0.01 元流程；成功后再改正式价。

## 为什么桌面版不需要这些私钥

- `alipay_app_private.pem`：只给商业服务调用支付宝下单/签名用；客户端拿到它等于把商户密钥发给所有客户。
- `license_signing_ed25519.pem`：只给商业服务签发授权用；客户端只内置**公钥**验签。
- 因此桌面绿色版/安装包里没有私钥是正确的；需要在商业服务器上单独部署。

## 桌面端当前状态

- 最新桌面版：**v1.4.0**，已打包并上传 Gitea Release；
- 桌面版本身不修复生产支付；生产支付需要服务器商业服务修好密钥与 `.env` 后立即生效；
- 服务器修好后，桌面端不需要重新下载，只要刷新购买页即可。
