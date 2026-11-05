# Core 签名密钥保管与 keyring 更新（v1.5.0）

> 适用：持有 core 私钥的负责人/运维。core 私钥是最高信任根，泄露可伪造整条 keyring。
> 本文件不包含私钥、口令或密钥内容。

## 当前 core key

| 项 | 值 |
|---|---|
| key_id | `core-2026` |
| 公钥（base64 raw Ed25519） | `dhfTSmtT73EZ2pKTlEY3Qko1hStmRXlBu10j6n7zjWM=` |
| 公钥 SHA-256 指纹 | 由 `core_key_info.txt` 记录 |
| 硬编码位置 | `app/server/yinzhan/licensing/keys.py` → `CORE_PUBLIC_KEYS` |
| 当前签名 keyring | `app/server/yinzhan/licensing/public_keys.json` |

本次实施生成的离线私钥位于开发机（**不在仓库内**）：

```text
C:\Users\Dante\.sanitized-data-core-keys\core-2026\
├── core_signing_ed25519.pem        # 加密私钥，600
├── core_public.b64                 # 公钥
├── core_key_info.txt               # 公钥指纹/元数据
└── PASSWORD-DO-NOT-SHARE.txt       # 一次性口令文件，需移入密码管理器后删除
```

> 负责人应尽快把 `PASSWORD-DO-NOT-SHARE.txt` 中的口令移入密码管理器/离线密封件，
> 删除该明文文件；不要把私钥或口令发到聊天、工单、Git、CI 或安装包。

## 新增/轮换 license 公钥

1. 用 `deploy/license_keygen.py` 生成新 license 密钥对（只产生公钥片段）；
2. 把新公钥合并到未签名 keyring；
3. 用 core 私钥签名：

```powershell
$env:CORE_KEY_PASSWORD = '<从密码管理器读取>'
python deploy/sign_keyring.py `
  --core-private-key C:\Users\Dante\.sanitized-data-core-keys\core-2026\core_signing_ed25519.pem `
  --root-key-id core-2026 `
  --in <未签名 public_keys.json> `
  --out app\server\yinzhan\licensing\public_keys.json `
  --revision 2
Remove-Item Env:CORE_KEY_PASSWORD
```

4. 运行 `python deploy/verify_keyring.py`；
5. 提交签名后的 `public_keys.json`；
6. 不需要重新打包 exe，客户端下次启动会加载新 keyring。

## 轮换 core key

1. 离线生成新 core 密钥对（`deploy/core_keygen.py --key-id core-2027`）；
2. 把新公钥追加到 `CORE_PUBLIC_KEYS`（保留 `core-2026`）；
3. 用新 core 私钥签 keyring，`root_key_id` 改为 `core-2027`；
4. 重新构建 v1.5.x+；
5. 旧 core 公钥保留至少一个发布周期，确认旧 keyring 不再需要后再移除。

## 构建门禁

`desktop/build_win.ps1` 会执行：

```powershell
python deploy/verify_keyring.py --manifest app\server\yinzhan\licensing\public_keys.json
```

- 未签名/被篡改/含 test key → 退出非 0，拒绝打包；
- 打包后 smoke test 还会检查 `/api/license/status` 的 `keyring_ok=true`。

## 安全边界

- 能挡：直接替换 `public_keys.json` 公钥、旧 flat 格式、运行时 `YINZHAN_PUBLIC_KEYS_PATH`。
- 挡不住：逆向 patch exe/替换硬编码 core 公钥；如需更强，另加代码签名/完整性校验。
- 不要用 core 私钥替代 license 签发私钥；两者必须分开。
