# 生产商业服务部署说明（buysd.blctek.com:8443）

> 目标：把 `commercial/` 商业服务部署到公网 `https://buysd.blctek.com:8443`，供本机客户端购买/查单使用。
> 注意：商业服务只处理订单/授权，不接收用户文件；文件处理仍在本机。

## 1. DNS 与证书

- DNS：`buysd.blctek.com` A/AAAA 记录指向服务器公网 IP。
- TLS 证书：覆盖 `buysd.blctek.com` 的有效证书（Let's Encrypt/阿里云等，不能自签）。
- 防火墙：放行 8443（如改用 443，则放行 443）。

## 2. 服务器进程

建议用 systemd / Windows 服务托管，只监听回环地址：

```bash
cd /opt/sanitized-data
export PYTHONPATH="$PWD/commercial"
export COMMERCIAL_BASE_URL="https://buysd.blctek.com:8443"
export LICENSE_KEY_PASSWORD="<从密钥生成时设置的密码>"
export LICENSE_KEY_PASSWORD_ENV="LICENSE_KEY_PASSWORD"
python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8787
```

`LICENSE_KEY_PASSWORD` 必须由**运行环境/密钥管理**注入，不要写进仓库、`.env` 或聊天记录。

Windows 服务可用 NSSM 的 `AppEnvironmentExtra` 注入同样的环境变量。

## 3. Nginx 反向代理（8443）

```nginx
server {
    listen 8443 ssl;
    server_name buysd.blctek.com;

    ssl_certificate     /etc/letsencrypt/live/buysd.blctek.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/buysd.blctek.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

> 如改 443：`listen 443 ssl;`，并把 `COMMERCIAL_BASE_URL` 改为 `https://buysd.blctek.com`。
> 不要直接对公网暴露 8787。

## 4. 商业服务环境变量

```env
COMMERCIAL_BASE_URL=https://buysd.blctek.com:8443
ALIPAY_APP_ID=2021007114621412
ALIPAY_APP_PRIVATE_KEY_PATH=commercial/var/keys/alipay_app_private.pem
ALIPAY_PUBLIC_KEY_PATH=commercial/var/keys/alipay_public.pem
ALIPAY_PAY_MODE=page
ALIPAY_QR_PAY_MODE=2
ALIPAY_QUERY_FALLBACK=true
PAYMENT_PROVIDER=alipay
PAYMENT_MOCK_ENABLED=false
COMMERCIAL_SELF_SERVE_PRO_ENABLED=false
LICENSE_PRIVATE_KEY_PATH=<生产私钥路径>
LICENSE_KEY_ID=prod-2026
LICENSE_KEY_PASSWORD_ENV=LICENSE_KEY_PASSWORD
```

`ALIPAY_NOTIFY_URL` 可留空，代码会推导为 `${COMMERCIAL_BASE_URL}/notify/alipay`。

> **密钥不随 Git/桌面安装包发布**：`commercial/var/` 被 `.gitignore` 忽略；服务器必须单独上传
> `alipay_app_private.pem`、`alipay_public.pem` 和 license 私钥。缺文件时下单会报
> `payment_create_failed`；部署后先跑 `python commercial/scripts/check_deploy.py`。
> 完整排查/上传步骤见 [KEY_DEPLOY_FIX_2026-11-05.md](KEY_DEPLOY_FIX_2026-11-05.md)。

## 5. 客户端默认地址

安装目录/数据目录放置 `commercial.json`：

```json
{"commercial_url": "https://buysd.blctek.com:8443"}
```

或设置 `YINZHAN_COMMERCIAL_URL=https://buysd.blctek.com:8443`。

## 6. 验收

```bash
curl -I https://buysd.blctek.com:8443/healthz       # 200
curl -s https://buysd.blctek.com:8443/api/catalog | head
```

然后在测试客户端跑一次真实 0.01 元：
下单 → 打开支付宝收银台 → 扫码 → 支付 → 主动查单 → 自动发授权 → 导入解锁。

## 7. 注意

- `LICENSE_KEY_PASSWORD` 丢失：无法解密生产私钥；需要重新生成 Ed25519 keypair、更新 `app/server/yinzhan/licensing/public_keys.json`、重建客户端并重新签发授权。
- `.env`、密钥、订单数据库不要提交 Git；`.gitignore` 已覆盖。
- 如使用 8443，Alipay 的 `notify_url` 必须带 `:8443` 且公网可达；如审核/回调异常，优先改 443。


