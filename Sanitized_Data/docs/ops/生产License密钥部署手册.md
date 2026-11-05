# 数据隐站 · 生产 License 密钥部署手册（prod-2026b）

> 适用：把**生产授权签名密钥**从本机交接给服务器部署同事，并配置商业服务签发真实 License。
> 目标服务器：`buysd.blctek.com:8443` 背后的商业服务（只监听 `127.0.0.1:8787`）。
> 安全等级：**生产密钥，仅限授权运维人员操作；口令与私钥分开保管、分开传递。**
>
> **2026-11-04 已完成 key rotation：**
> - 新签发使用 **`prod-2026b`**（本手册默认）；
> - 旧 `prod-2026` 公钥继续保留，**旧授权仍可正常验证**；
> - 不要把旧 `prod-2026` 私钥用于新签发；
> - 新私钥、新口令、备份位置见 §2。
>
> **2026-11-05 v1.5.0 新增 core 信任根：** 客户端 `.exe` 硬编码 core 公钥；`public_keys.json` 必须由对应 core 私钥签名（`schema=2` + `sig`）。core 私钥**只在离线环境保存**，与 license 签发私钥分开保管；新增/轮换 license 公钥后必须用 `deploy/sign_keyring.py` 重新签名，构建脚本会用 `deploy/verify_keyring.py` 拒绝未签名 keyring。详见 [keyring-core-signing-plan.md](../impl/keyring-core-signing-plan.md)。

---

## 1. 先回答一个关键问题

**只把口令交给同事，不够；必须同时交接“加密私钥文件 + 原口令”。**

因为：

- 私钥文件是**用口令加密**的；
- 服务器上的 `LICENSE_KEY_PASSWORD` 必须等于**当初生成私钥时设置的那个口令**；
- 服务器重新生成一个新口令，无法解开这把私钥；
- 生产服务器也不需要重新生成密钥，只需要**使用同一把** `prod-2026b` 私钥。

正确交接内容：

| 交付物 | 是否保密 | 说明 |
|---|---|---|
| `license_signing_ed25519.pem` | **绝密** | 生产加密私钥，只能在商业服务机器上 |
| 生成私钥时设置的口令 | **绝密** | 通过密码管理器/一次性秘密链接单独传递 |
| `public_keys.json` | 可公开 | 公钥，客户端安装包已内置 |
| `key_info.txt` | 内部 | key_id / 创建时间 / 公钥指纹，用于核对 |

> **口令不要和私钥放在同一条消息、同一个压缩包、同一个 Git 仓库里。**
> 推荐：私钥通过加密文件/受控跳板机传；口令通过密码管理器共享或一次性秘密链接传，同事接收后立即保存到服务器 Secret。

---

## 2. 本机现有生产密钥

本机路径（Windows，密钥轮换后）：

```text
C:\Users\Dante\.sanitized-data-license-keys\
  prod-2026b\license_signing_ed25519.pem   ← 新加密生产私钥
  prod-2026b\public_keys.json              ← 新公钥片段
  prod-2026b\key_info.txt                  ← key_id / 指纹
  prod-2026b-PASSWORD.txt                  ← 新口令（仅本机 600，转存密码管理器后删除）
  license_signing_ed25519.pem              ← 旧 prod-2026 私钥（保留，仅用于旧授权/应急）
  public_keys.json                         ← 旧公钥（客户端仍保留）
```

核对信息：

```text
key_id=prod-2026b
public_sha256=5AF45CB58A885ECF1F2A40DC744211A2EB35447D14C5B30CAAB97E7D2EEF0872
```

客户端内置公钥（`app/server/yinzhan/licensing/public_keys.json`）同时包含：

- `prod-2026`：旧授权验签，继续保留；
- `prod-2026b`：新授权签发/验签。

**部署时不要删除旧 `prod-2026` 公钥**，否则旧客户授权会失效。

测试密钥（`commercial/var/test_keys/`）无口令，**不能用于生产**，也不要拷到生产服务器。

---

## 3. 服务器目录与权限

以下以 Linux 为例：

```text
/etc/sanitized-data/
  license_signing_ed25519.pem   # 生产加密私钥，600
  env                            # 口令等环境变量，600
```

创建目录并设置权限：

```bash
sudo install -d -m 700 -o <服务用户> -g <服务用户组> /etc/sanitized-data
```

上传私钥后：

```bash
sudo install -m 600 -o <服务用户> -g <服务用户组> \
  license_signing_ed25519.pem /etc/sanitized-data/license_signing_ed25519.pem
```

把 `<服务用户>` 换成商业服务实际运行用户；不要用 root 运行商业服务。

> Windows 服务器：可放 `C:\ProgramData\SanitizedData\license_signing_ed25519.pem`，只给服务账号读权限。

---

## 4. 服务器环境变量

### 4.1 写入受保护的 env 文件

```bash
sudo tee /etc/sanitized-data/env >/dev/null <<'EOF'
LICENSE_KEY_PASSWORD=这里替换成当年生成私钥时设置的原口令
EOF
sudo chmod 600 /etc/sanitized-data/env
sudo chown <服务用户>:<服务用户组> /etc/sanitized-data/env
```

> 不要把真实口令提交到 Git，也不要写进仓库里的 `.env`、聊天记录、工单截图。
> `env` 文件只在服务器上，权限 600，由 systemd/服务读取。

### 4.2 商业服务配置

`commercial/.env`（服务器本地，不提交 Git）至少包含：

```env
COMMERCIAL_BASE_URL=https://buysd.blctek.com:8443
PAYMENT_PROVIDER=alipay
PAYMENT_MOCK_ENABLED=false

# License 签发
LICENSE_PRIVATE_KEY_PATH=/etc/sanitized-data/license_signing_ed25519.pem
LICENSE_KEY_ID=prod-2026b
LICENSE_KEY_PASSWORD_ENV=LICENSE_KEY_PASSWORD
```

注意：

- `LICENSE_KEY_PASSWORD_ENV` 是**变量名**，不是口令本身；
- 真正的口令由 systemd `EnvironmentFile` 注入；
- 不要把本机 `.env` 里的 `%USERPROFILE%\...` 原样复制到 Linux，必须改成服务器路径。

---

## 5. systemd 服务示例

```ini
[Unit]
Description=Sanitized Data Commercial Service
After=network.target

[Service]
Type=simple
User=<服务用户>
Group=<服务用户组>
WorkingDirectory=/opt/sanitized-data
EnvironmentFile=/etc/sanitized-data/env
Environment=PYTHONPATH=/opt/sanitized-data/commercial
Environment=COMMERCIAL_BASE_URL=https://buysd.blctek.com:8443
ExecStart=/opt/sanitized-data/.venv/bin/python -m uvicorn \
  server.app:create_app --factory --host 127.0.0.1 --port 8787
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sanitized-commercial
sudo systemctl status sanitized-commercial --no-pager
```

> 反向代理、TLS、防火墙见：
> [生产商业服务部署说明](生产商业服务部署-buysd.blctek.com-8443.md)
> 不要把 8787 直接暴露公网。

---

## 6. 部署后验证

### 6.1 服务健康

```bash
curl -I https://buysd.blctek.com:8443/healthz
```

应返回 200，证书有效。

### 6.2 验证私钥能否解密

在商业服务代码目录，用 `deploy/issue_order.py` 做一次**不交付的测试签发**：

```bash
cd /opt/sanitized-data
python deploy/issue_order.py \
  --private-key /etc/sanitized-data/license_signing_ed25519.pem \
  --key-id prod-2026b \
  --sku personal \
  --machine-code "TEST-0000-0000-0000" \
  --order-id TEST-LICENSE-001 \
  --out-dir /tmp/license-test
```

脚本会提示输入私钥口令（不回显）。能成功输出 License，说明：

- 私钥文件正确；
- 口令正确；
- `prod-2026b` key_id 正确。

如果报：

```text
SignerError: license_key_invalid
口令与私钥不匹配
```

说明口令不对，或私钥文件不是同一把；**不要反复猜，找持有备份的人核对**。

### 6.3 验证公钥匹配

签发出来的 License 在测试客户端导入应显示“已授权”；或核对：

```bash
python -X utf8 -c "import json; print(json.load(open('app/server/yinzhan/licensing/public_keys.json'))['prod-2026b'])"
```

应包含 `ukCCU+OZM9wSPF1FxJ2VTmMRhq0WQVOsV+DWHLKVpFI=`。

### 6.4 真实 0.01 元验收

最后做一次真实链路：

```text
客户端购买页 → 支付宝收银台 → 扫码支付 0.01 元
→ 商业服务主动查单 → 签发 License → 客户端自动导入 → 显示已授权
```

验收通过后，再切正式价格/正式商品。

---

## 7. 备份与恢复

至少保留以下三份文件：

```text
license_signing_ed25519.pem
public_keys.json
key_info.txt
```

- 私钥和口令**分开**保存；
- 至少两份离线备份，异地保存；
- 每 6 个月做一次恢复演练；
- 服务器坏了：在新机器恢复私钥 + env 口令即可，不需要重新生成密钥。

---

## 8. 口令丢失怎么办

先确认密码管理器、离线密封件、之前的交接记录。  
如果确实找不到：

1. **不要覆盖旧私钥**；
2. 再轮换时用新 key_id（例如 `prod-2026c`）生成新密钥、新口令；
3. 在 `app/server/yinzhan/licensing/public_keys.json` **追加**新公钥，保留 `prod-2026b` 与 `prod-2026`；
4. 重新打包客户端；
5. 商业服务切换到新 key_id / 新私钥 / 新口令；
6. 新授权用新 key 签发，旧授权继续用旧公钥验证。

这个操作需要开发配合，不建议运维自行删除旧密钥。

---

## 9. 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `license_key_invalid 口令与私钥不匹配` | 口令错/私钥错 | 核对密码管理器与私钥来源，不要猜 |
| 启动正常但发码失败 | 服务未注入 `LICENSE_KEY_PASSWORD` | 检查 systemd `EnvironmentFile` |
| 客户端“授权无效” | 客户端公钥与签发 key 不匹配 | 确认打包公钥为 `prod-2026b`，未设置测试公钥环境变量 |
| 签出来的 License 过期 | key_id/有效期不对 | 检查 `--days`/`--term` 和系统时间 |
| 服务日志出现密钥内容 | 配置错误/调试代码 | 立即停服、轮换密钥，检查日志权限 |

---

## 10. 安全红线

- 私钥、口令不进入 Git、不进安装包、不进日志；
- 不通过微信/邮件同一条消息同时发送私钥和口令；
- 服务器 env 文件权限 600；
- 商业服务只监听 127.0.0.1；
- 不把 `commercial/var/test_keys/` 的测试私钥用于生产；
- 不在聊天/工单中粘贴真实口令或私钥内容。

---

## 11. 交付给同事的最短清单

```text
1. 加密私钥 license_signing_ed25519.pem（安全通道）
2. 原口令（密码管理器/一次性链接，单独通道）
3. 本手册
4. 商业服务部署说明（buysd.blctek.com:8443）
5. 测试客户端/机器码，用于 0.01 元验收
```

同事按本手册配置后，把以下结果回传：

```text
healthz: 200
issue_order 测试签发: 成功
真实 0.01 元: 成功，客户端已授权
```

---

> 本手册是运维文档，不包含真实口令或私钥内容。
> 如口令来源不确定，先暂停部署并核对，不要重新生成密钥覆盖旧密钥。



