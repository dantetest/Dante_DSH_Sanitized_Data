# 签名 keyring 信任根实施报告（v1.5.0）

## 状态

- 版本：**v1.5.0**
- core key：`core-2026`
- 已生成签名 `public_keys.json`（schema=2）
- 已构建 Windows 绿色版/安装包并上传 Gitea Release
- 校验失败提示：**「软件组件可能损坏，请使用官方安装包重新安装」**，不暴露公钥替换细节

## 已实现

### 1. 核心公钥硬编码

`app/server/yinzhan/licensing/keys.py`：

```python
CORE_PUBLIC_KEYS = {
    "core-2026": "dhfTSmtT73EZ2pKTlEY3Qko1hStmRXlBu10j6n7zjWM=",
}
```

只硬编码公钥；core 私钥不进仓库/CI/安装包。

### 2. public_keys.json 签名

```json
{
  "schema": 2,
  "root_key_id": "core-2026",
  "keyring_revision": 1,
  "issued_at": "...",
  "keys": {
    "prod-2026": "...",
    "prod-2026b": "..."
  },
  "sig": "..."
}
```

客户端只有验签通过后才加载 `PUBLIC_KEYS`；失败 fail closed。

### 3. 关闭旧绕过

- 删除 `YINZHAN_PUBLIC_KEYS_PATH` 生产路径；
- 冻结包中 `add_public_key()` 直接拒绝；
- 测试注入仅限源码模式：
  - `YINZHAN_TEST_KEYRING_PATH`
  - `YINZHAN_TEST_CORE_PUBLIC_KEY`
- 打包实测：设置上述变量不影响正式包 keyring。

### 4. 错误提示

授权组件校验失败：

```json
{"error": {"code": "keyring_invalid",
  "message": "软件组件可能损坏，请使用官方安装包重新安装"}}
```

UI 同步显示这句；不提示“替换公钥文件”。

### 5. 工具链 / 构建门禁

- `deploy/core_keygen.py`：离线生成 core 密钥对；
- `deploy/sign_keyring.py`：core 私钥签名 keyring；
- `deploy/verify_keyring.py`：构建/部署校验；
- `desktop/build_win.ps1`：
  - 打包前拒绝未签名/tampered keyring；
  - 打包后 smoke test 检查 `/api/license/status.keyring_ok=true`。

### 6. 测试

- `test_keyring.py`：8 passed
  - 合法 keyring；
  - 改公钥/改签名/旧 flat 格式/文件缺失均拒绝；
  - `keyring_invalid` 阻断并返回指定提示；
  - 冻结态禁止 `add_public_key`；
  - **旧 `prod-2026b` license 仍可正常导入**。
- 商业服务全量：**151 passed, 62 subtests passed**（含 e2e）
- 购买/授权静态测试：16 passed
- v1.5.0 打包版实测：
  - 合法 keyring：`keyring_ok=true`
  - 设置旧覆盖/测试注入变量：仍 `true`（被忽略）
  - 替换为未签名 attacker keyring：`keyring_ok=false`
  - 导入 license：**403 keyring_invalid + 指定提示**

## 产物

Release：<https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases/tag/v1.5.0>

| 产物 | SHA-256 |
|---|---|
| `Sanitized_Data-1.5.0-win64-portable.zip` | `DB42266CE2462A075F81D9EF1A9F64F024B5E7C43BC524042289AF5F7C9414A3` |
| `Sanitized_Data_Setup_1.5.0_x64.exe` | `4E95E8AFCA5E17E70198F52BA21387EEDEF69E610734A131C10452BEBD09424A` |

## Core 私钥保管

本机生成位置（仓库外）：

```text
C:\Users\Dante\.sanitized-data-core-keys\core-2026\
├── core_signing_ed25519.pem      # 加密私钥
├── core_public.b64
├── core_key_info.txt
└── PASSWORD-DO-NOT-SHARE.txt     # 一次性口令文件
```

负责人需：

1. 把口令移入密码管理器/离线密封件；
2. 删除明文 `PASSWORD-DO-NOT-SHARE.txt`；
3. 私钥离线备份至少两份，与口令分开；
4. 不要把私钥/口令发到聊天、Git、CI、安装包。

后续新增 license 公钥：用 core 私钥运行 `deploy/sign_keyring.py` 重签 keyring，**不需要重新打包 exe**。

## 升级注意

- 必须使用 **v1.5.0 完整包**（安装包或完整绿色版目录）；
- 只替换 exe、保留旧未签名 `public_keys.json` 会校验失败；
- 旧 v1.4.1 及以前仍信任未签名 keyring，**必须升级**；
- 现有 `prod-2026` / `prod-2026b` license 继续有效；
- 商业服务端不需要 keyring 改动（它只负责签发 license）。

## 相关文件

- 方案：[docs/impl/keyring-core-signing-plan.md](../impl/keyring-core-signing-plan.md)
- 密钥保管：[docs/ops/core-key-custody.md](core-key-custody.md)
- 信任链说明：[docs/impl/licensing.md](../impl/licensing.md)
