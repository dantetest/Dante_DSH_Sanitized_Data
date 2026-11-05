# 离线授权防替换方案：核心公钥 + 签名 keyring

> 状态：**方案设计稿，待负责人确认后实施**
> 日期：2026-11-05
> 目标读者：桌面端 / 授权模块 / 构建发布程序员
> 目标版本：建议 **v1.5.0**（信任链变更）

---

## 0. 一句话

在桌面 `.exe` 里硬编码一个**核心公钥**（core public key），并让 `public_keys.json` 变成由对应**核心私钥**签名的 keyring 清单；客户端只有验签通过后，才信任其中的 license 公钥。这样直接替换 `public_keys.json` 里的公钥不再能伪造授权。

**只硬编码公钥，绝不硬编码/打包核心私钥。**

---

## 1. 当前实现与漏洞

### 1.1 当前信任链

```text
license.json
  ├─ key_id: "prod-2026b"
  └─ sig: ...
        ↓
keys.get_public_key("prod-2026b")
        ↓
app/server/yinzhan/licensing/public_keys.json
        ↓
Ed25519 验签通过 → 授权有效
```

相关文件：

- `app/server/yinzhan/licensing/keys.py`
  - 读取同目录 `public_keys.json`；
  - 支持 `YINZHAN_PUBLIC_KEYS_PATH` 环境变量合并/覆盖公钥；
  - 提供 `add_public_key()` 运行时追加公钥。
- `app/server/yinzhan/licensing/public_keys.json`
  - 当前是未签名的扁平 JSON：`{"key_id": "<base64 raw pub>"}`
- `app/server/yinzhan/licensing/service.py`
  - `_verify_container()` 根据 license 里的 `key_id` 从 `keys` 查公钥并验签。

### 1.2 攻击方式

攻击者只需：

1. 生成自己的 Ed25519 密钥对；
2. 把 `public_keys.json` 中 `prod-2026b` 的公钥替换成自己的；
3. 用自己的私钥签一个 `license.json`，`key_id` 仍写 `prod-2026b`；
4. 客户端验签通过。

另外两个更直接的入口：

- 设置 `YINZHAN_PUBLIC_KEYS_PATH` 指向攻击者 JSON；
- 在可执行环境调用 `keys.add_public_key()`。

**只把 `public_keys.json` 改成签名文件、不关闭这两个入口，防护无效。**

---

## 2. 目标与非目标

### 2.1 目标

- `.exe` 内硬编码 core 公钥；
- `public_keys.json` 变为 core 私钥签名的 keyring；
- 启动/导入授权前验签；验签失败 **fail closed**；
- 关闭生产环境运行时公钥覆盖；
- 现有 `prod-2026` / `prod-2026b` 已签发 license 继续有效；
- keyring 可由 core 私钥重新签名以增删 license 公钥，**不需要重新打包 exe**；
- 支持 core key 轮换。

### 2.2 非目标

- 不能阻止逆向工程直接 patch exe、替换硬编码公钥、hook 验签函数；
- 不能替代 Windows 代码签名/完整性保护；
- 不能实现在线吊销（离线产品）；
- 不改变商业服务签发 license 的流程，只改变客户端信任 keyring 的方式。

---

## 3. 密钥层级

```text
Core 密钥对（离线，最高权限）
  ├─ core 公钥：硬编码进 exe
  └─ core 私钥：只用于给 public_keys.json 签名，绝不进 Git/CI/安装包
        │
        ▼
public_keys.json（已签名 keyring）
  ├─ prod-2026   license 公钥
  └─ prod-2026b  license 公钥
        │
        ▼
license.json（商业服务/人工签发）
```

要求：

- **core 私钥 ≠ license 签发私钥**；
- core 私钥离线保存，口令与私钥分开；
- license 私钥泄露时，core 私钥可重新签一个移除该 license 公钥的 keyring（但离线吊销有局限，见 §11）；
- core 私钥泄露是最高风险，必须按最高等级保护。

---

## 4. 新 `public_keys.json` 格式

文件名保持不变：

```json
{
  "schema": 2,
  "root_key_id": "core-2026",
  "keyring_revision": 1,
  "issued_at": "2026-11-05T12:00:00+00:00",
  "keys": {
    "prod-2026": "ukCCU+OZM9wSPF1FxJ2VTmMRhq0WQVOsV+DWHLKVpFI=",
    "prod-2026b": "+7p1RknkRttqusSrfqvNtJl58q5nlVigZtGX4zqv3bQ="
  },
  "sig": "<base64 Ed25519 signature>"
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | int | 固定 `2`；旧 flat 格式没有此字段，直接拒绝 |
| `root_key_id` | str | 用哪把 core 公钥验签；必须在 `CORE_PUBLIC_KEYS` 中存在 |
| `keyring_revision` | int | 从 1 递增；仅展示/审计，不作为安全边界 |
| `issued_at` | ISO8601 str | 签名时间 |
| `keys` | object | key_id → base64 raw Ed25519 公钥（32 字节） |
| `sig` | str | core 私钥对 canonical payload 的 Ed25519 签名 |

### 4.1 签名原文

对以下对象做规范 JSON（**不包含 `sig`**）：

```python
payload = {
    "schema": 2,
    "root_key_id": "core-2026",
    "keyring_revision": 1,
    "issued_at": "...",
    "keys": {...},
}
raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                 separators=(",", ":"), allow_nan=False).encode("utf-8")
sig = base64.b64encode(core_private_key.sign(raw)).decode("ascii")
```

注意：

- 必须与现有 `service.canonical_payload()` 使用同一规则；
- 不要按文件原始字节签名（格式化/换行会变）；
- 不要允许 NaN/浮点字段；
- `keys` 中每个值必须是 **32 字节 raw Ed25519 公钥**，不是 PEM、不是 SHA256。

---

## 5. 客户端代码改造

### 5.1 `keys.py`

新增：

```python
CORE_PUBLIC_KEYS = {
    "core-2026": "<base64 raw Ed25519 公钥>",
}
```

建议同时预留下一把 core key，便于轮换：

```python
CORE_PUBLIC_KEYS = {
    "core-2026": "...",
    # "core-2027": "...",   # 轮换时加入
}
```

启动时逻辑：

```text
load_default_keyring():
  read public_keys.json
  if schema != 2: fail
  core = CORE_PUBLIC_KEYS.get(root_key_id)
  if not core: fail
  verify ed25519(sig, canonical(payload_without_sig), core)
  if invalid: fail
  decode each keys value: base64 -> exactly 32 bytes
  if any invalid: fail
  PUBLIC_KEYS = decoded keys
  KEYRING_OK = True
```

失败时：

- 不保留旧 `PUBLIC_KEYS`；
- 不覆盖/删除文件；
- `KEYRING_OK = False`，记录 `KEYRING_ERROR`；
- 不抛出到应用启动最外层，避免整个 App 打不开；但授权相关功能必须拒绝。

### 5.2 关闭运行时覆盖

生产/冻结包：

- **忽略** `YINZHAN_PUBLIC_KEYS_PATH`；
- 不再暴露 `add_public_key()` 给运行时；
- 不提供 CLI/环境变量注入公钥的路径。

测试/源码模式：

- 可提供受控测试入口，例如：
  - `YINZHAN_TEST_KEYRING_PATH`；
  - 或测试 fixture 直接 patch `CORE_PUBLIC_KEYS` + 注入签名的测试 keyring；
- 必须加 `not paths.FROZEN` 条件；
- 生产构建审计检查不得包含 test key/test core 公钥。

### 5.3 `service.py`

`_verify_container()` 顺序建议：

1. 如果 `keys.KEYRING_OK` 为 false：
   - 抛 `LicenseError("keyring_invalid", "授权组件校验失败，请使用官方安装包重新安装", 403)`；
2. 再解析 license；
3. 查 `key_id`；
4. 验签；
5. 其余校验不变。

`status()` 建议增加只读字段：

```json
{
  "keyring_ok": true/false,
  "keyring_error": null / "keyring_invalid"
}
```

用于授权页提示，不改变现有 API 必填字段。

### 5.4 UI

授权页在 `keyring_ok=false` 时显示明确提示：

> 授权组件校验失败。请使用官方安装包重新安装；不要替换程序目录中的公钥文件。

试用功能可继续；付费授权导入失败；不要静默降级为“未授权”而不解释。

---

## 6. 工具链

### 6.1 `deploy/core_keygen.py`（新增）

职责：离线生成 core 密钥对。

要求：

- 输出加密 Ed25519 PEM；
- 交互输入口令或 `CORE_KEY_PASSWORD` 环境变量；
- 默认拒绝覆盖；
- 输出 `core_public.b64` 和 `core_key_info.txt`（含公钥指纹，不含私钥）；
- 不打印私钥/口令；
- 明确提示：私钥不进 Git/CI/安装包。

建议命令：

```powershell
python deploy/core_keygen.py `
  --key-id core-2026 `
  --out-dir D:\secure\core-keys
```

### 6.2 `deploy/sign_keyring.py`（新增）

职责：用 core 私钥给 keyring JSON 签名。

输入：

- 未签名 keyring 的 `keys` 内容；
- `--core-private-key`；
- `--password-env CORE_KEY_PASSWORD`；
- `--out`。

要求：

- 拒绝把 core 私钥放在仓库目录内；
- 拒绝 `test-*` key_id，除非显式 `--allow-test`；
- 校验每个公钥 32 字节；
- 输出最终 `public_keys.json`（含 schema/root/revision/issued_at/sig）；
- 只打印公钥指纹和输出路径。

建议命令：

```powershell
python deploy/sign_keyring.py `
  --core-private-key D:\secure\core-keys\core_signing_ed25519.pem `
  --root-key-id core-2026 `
  --in app\server\yinzhan\licensing\public_keys.unsigned.json `
  --out app\server\yinzhan\licensing\public_keys.json
```

### 6.3 `deploy/verify_keyring.py`（新增）

职责：构建前/服务器验证，不需要 core 私钥。

校验：

- schema=2；
- `root_key_id` 在 `CORE_PUBLIC_KEYS` 中；
- 签名有效；
- 无 test key；
- 所有公钥 32 字节；
- 至少包含 `prod-2026` / `prod-2026b`（按当前发布要求）。

`build_win.ps1` 在 PyInstaller 前调用，失败直接终止构建。

### 6.4 `deploy/license_keygen.py`（改造）

保留生成 license 密钥对功能，但：

- 不再让用户手工合并到 `public_keys.json`；
- 输出一个 keyring 片段/未签名清单；
- 提示用 `sign_keyring.py` 重新签名；
- 文档更新。

---

## 7. 构建与发布流程

### 7.1 首次上线

1. 离线生成 core 密钥对；
2. 把 core 公钥写入 `keys.py` 的 `CORE_PUBLIC_KEYS`（提交）；
3. 用 core 私钥签名生产 keyring（提交 `public_keys.json`）；
4. 改代码/测试；
5. `build_win.ps1` 自动跑 `verify_keyring.py`；
6. 构建 v1.5.0；
7. 测试正常 license + 攻击模拟；
8. 发布完整包（安装包/绿色版）。

### 7.2 以后新增/轮换 license 公钥

- 更新 keyring 的 `keys`；
- 用当前 core 私钥重新签名；
- 提交新的 `public_keys.json`；
- **不需要重新打包 exe**（客户端会在下次启动加载）。

### 7.3 core 密钥轮换

- 新 core 公钥加入 `CORE_PUBLIC_KEYS`；
- 用新 core 私钥签 keyring（`root_key_id` 指向新 key）；
- 发布新 exe；
- 旧 core 公钥保留一段时间，确保旧 keyring 可验；
- 旧 core 私钥退役，不删除备份，直到确认无回滚需求。

---

## 8. 兼容性与迁移

- 新 keyring 必须包含现有 `prod-2026`、`prod-2026b`，已签发 license 继续有效；
- 旧 flat `public_keys.json` **拒绝加载**；
- 升级必须使用完整安装包/完整绿色包目录，不能只替换 exe；
- 安装器覆盖 `public_keys.json`；
- 发布说明明确：
  - 旧版 keyring 未签名；
  - 仅替换 exe 会导致授权组件校验失败；
  - 请使用 v1.5.0 完整包。
- 若检测到旧 flat 格式，可提示“请重新安装官方 v1.5.0”，但不要自动信任旧内容。

---

## 9. 测试清单

### 9.1 正常路径

- [ ] 使用签名生产 keyring，`prod-2026` license 导入成功；
- [ ] `prod-2026b` license 导入成功；
- [ ] 试用/免费状态不受 keyring 影响；
- [ ] 买断 perpetual license 正常；
- [ ] `status.keyring_ok = true`。

### 9.2 攻击/失败路径

- [ ] 替换 `public_keys.json` 中的公钥 + 自签 license → 拒绝，`keyring_invalid`；
- [ ] 删除 `public_keys.json` → 拒绝，App 不崩；
- [ ] 篡改 `sig` → 拒绝；
- [ ] 把 schema 改成旧 flat 格式 → 拒绝；
- [ ] `root_key_id` 未知 → 拒绝；
- [ ] keys 中公钥非 32 字节 → 拒绝；
- [ ] 生产冻结包设置 `YINZHAN_PUBLIC_KEYS_PATH` → 被忽略；
- [ ] 构建时使用未签名/test keyring → `verify_keyring.py` 返回非 0，构建失败。

### 9.3 回归

- [ ] `test_licensing.py`；
- [ ] `test_license_issue.py`；
- [ ] `test_purchase_static.py` 中 keys 相关测试改造；
- [ ] 商业服务 e2e 使用签名测试 keyring；
- [ ] 打包版安装/绿色版实际导入生产 license。

---

## 10. 需要修改的文件清单

### 核心

- `app/server/yinzhan/licensing/keys.py`
- `app/server/yinzhan/licensing/public_keys.json`
- `app/server/yinzhan/licensing/service.py`
- `app/server/yinzhan/api.py`（如需暴露 keyring 状态）
- `app/web/license.html` / `license.js`（错误提示）

### 工具

- `deploy/core_keygen.py`（新增）
- `deploy/sign_keyring.py`（新增）
- `deploy/verify_keyring.py`（新增）
- `deploy/license_keygen.py`（改造）
- `deploy/license_issue.py`（仅文档/提示，一般不改逻辑）

### 构建/审计

- `desktop/app.spec`
- `desktop/build_win.ps1`
- `desktop/audit_release.py`

### 测试

- `app/tests/test_licensing.py`
- `app/tests/test_license_issue.py`
- `app/tests/test_purchase_static.py`（删除/改写 env 覆盖测试）
- `commercial/tests/conftest.py`
- `commercial/scripts/gen_test_keys.py`
- `commercial/scripts/e2e_demo.py`
- `commercial/tests/test_e2e_mock.py`

### 文档

- `docs/impl/licensing.md`
- `docs/ops/生产License密钥部署手册.md`
- `README.md` 相关密钥章节
- 新增本文档

---

## 11. 安全边界与已知限制

1. **能挡**：直接替换 `public_keys.json`、伪造 key_id、环境变量注入公钥。
2. **挡不住**：逆向 patch exe、替换硬编码 core 公钥、hook 验签。
3. 若需要更强保护，另加：
   - Windows Authenticode 代码签名；
   - 启动时文件完整性校验；
   - 关键路径混淆/校验点分散；
   - 在线激活（与离线定位冲突）。
4. 离线无法可靠吊销已签发的 license 公钥：
   - 旧签名 keyring 可被保留/回滚；
   - 可增加 keyring 的 `issued_at`/过期策略，但本地时间可被调整；
   - 真正吊销需要 core key 轮换 + 新 exe，或联网校验。
5. core 私钥泄露 = 攻击者可签任意 keyring；必须离线、加密、双备份、口令分离。

---

## 12. 程序员实施顺序（建议）

1. 负责人确认本方案；
2. 离线生成 core 密钥对（不由程序员/仓库代持）；
3. 加 `CORE_PUBLIC_KEYS` + `keys.py` 验签与 fail-closed；
4. 加 `core_keygen.py` / `sign_keyring.py` / `verify_keyring.py`；
5. 改造测试与商业 e2e；
6. 签生产 keyring，跑构建门禁；
7. 构建 v1.5.0，做攻击模拟与真实 license 导入；
8. 发布完整包，更新部署文档。

---

## 13. 待确认项

实施前必须确认：

1. 是否采用“core 公钥硬编码 + 签名 keyring + fail closed”方案；
2. core 私钥由谁生成/保管（建议负责人离线生成，程序只接收公钥）；
3. 私有化部署：
   - A. 统一由公司 core 私钥签 keyring（推荐）；
   - B. 客户定制 exe，硬编码客户 core 公钥；
4. keyring 无效时：
   - A. App 可打开，仅授权功能失败（推荐）；
   - B. 直接拒绝启动；
5. 版本号用 `v1.5.0` 还是 `v1.4.2`；
6. 是否同时加 Windows 代码签名/完整性校验（不属于本方案最小范围）。

---

## 14. 明确禁止

- ❌ 不把 core 私钥、license 私钥写进仓库/安装包/CI/聊天记录；
- ❌ 不把 core 私钥硬编码进 exe；
- ❌ 不用 `YINZHAN_PUBLIC_KEYS_PATH` 在生产覆盖公钥；
- ❌ 不信任旧 flat `public_keys.json`；
- ❌ 不在验签失败时回退到旧的未签名 keyring；
- ❌ 不让 `add_public_key()` 在生产运行时可用。
