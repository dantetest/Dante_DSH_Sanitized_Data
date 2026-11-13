# 授权机器码 v2 + 离线转移/重绑（实施 1–3）

> 日期：2026-11-13
> 范围：生产授权模块；不改文件处理引擎。
> 长期账号/席位/在线设备方案：仅设计，不实施。

## 0. 根因

旧机器码（v1）：

```text
Windows: MachineGuid（注册表，OS 安装态）
       + C: 卷序列号（格式化/重装会变）
```

重装系统 / VM 重建后两者都会变，旧 `license.json` 的 `machine_codes` 不再匹配，返回：

```text
license_machine_mismatch
```

## 1. 机器码 v2（已实施）

新增稳定硬件指纹，不再使用 MachineGuid/卷序列号：

| 平台 | v2 组件 |
|---|---|
| Windows | SMBIOS UUID + 主板序列号 + CPU ProcessorId |
| macOS | IOPlatformUUID + 硬件序列号 |
| Linux | DMI product_uuid + product_serial |

算法：

```text
raw = "machine-v2|" + stable_components
code = CrockfordBase32(SHA256(raw)[:10])
```

Windows 组件获取：

- `Get-CimInstance Win32_ComputerSystemProduct`
- `Get-CimInstance Win32_BaseBoard`
- `Get-CimInstance Win32_Processor`

硬件不可得时回退旧指纹，并标记 `source=fallback-*`。

### 兼容旧授权

`machine_info()` 现在返回：

```json
{
  "code": "v2 稳定码",
  "display": "v2 展示码",
  "legacy_code": "v1 旧码",
  "legacy_display": "v1 展示码",
  "codes": ["v2", "v1"],
  "source": "windows-smbios+baseboard+cpu",
  "legacy_source": "windows-machineguid+volume-serial"
}
```

授权校验改为：

```text
license.payload.machine_codes 包含 v2 或 v1 任意一个即可
```

效果：

- 老授权在老机器继续可用；
- 新签发使用 v2；
- 以后同硬件重装系统，机器码不变。

注意：

> 已经因重装丢失 v1 指纹的机器，v1 旧授权无法自动恢复，仍需重签一次；  
> v2 解决的是“以后不再因重装失效”。

## 2. 立即恢复同事（P0）

1. 新系统打开授权页，复制新的 v2 机器码；
2. 找到旧 `license.json`；
3. 运行重签工具：

```powershell
python -X utf8 deploy\license_transfer.py `
  --old-license old-license.json `
  --new-machine-code XXXX-XXXX-XXXX-XXXX `
  --private-key "$env:USERPROFILE\.sanitized-data-license-keys\license_signing_ed25519.pem" `
  --key-id prod-2026 `
  --out out\licenses\reissued.json
```

4. 把 `reissued.json` 发给用户导入；
5. 在订单台账标记“设备转移/重装重绑”。

如果没有旧 `license.json`，按订单号用 `deploy/license_issue.py` 重新签发新机器码。

## 3. 离线转移/重绑（已实施）

### 3.1 产品端：生成转移请求（不含私钥）

```powershell
python -X utf8 deploy\license_transfer.py --request-only `
  --old-license old-license.json `
  --new-machine-code XXXX-XXXX-XXXX-XXXX `
  --out out\transfer_request.json
```

请求包含：

```text
旧 license（原样，供验签）
旧 license_id / order_id / machine_codes
新机器码
nonce / created_at
```

API（后端，前端后续再接）：

```text
POST /api/license/transfer-request
{
  "new_machine_code": "XXXX-XXXX-XXXX-XXXX",
  "license": "<旧 license JSON 字符串，可选>"
}
```

### 3.2 销售端：重签

```powershell
python -X utf8 deploy\license_transfer.py `
  --old-license old-license.json `
  --new-machine-code XXXX-XXXX-XXXX-XXXX `
  --private-key ... `
  --key-id prod-2026 `
  --out out\licenses\reissued.json
```

`reissue_payload` 保留：

- `license_id` / `order_id`
- `sku` / `term` / `seats`
- `features`
- 到期时间 / 更新期
- 客户信息

只替换：

- `machine_codes`

并增加签名审计字段：

- `reissued_from`
- `transfer_count`
- `transfer_history`（旧机器码 → 新机器码）
- `notes`

### 3.3 限制

离线模式无法远程吊销旧设备：

- 旧 license 文件在旧设备上仍可能继续用到到期；
- 销售台账必须记录转移/撤销；
- 长期在线设备表才能在服务端真正撤销旧设备。

## 4. 代码改动

| 文件 | 改动 |
|---|---|
| `app/server/yinzhan/licensing/machine.py` | v2 稳定指纹 + legacy code + codes |
| `app/server/yinzhan/licensing/service.py` | 接受 v2/legacy；`transfer_request()` |
| `app/server/yinzhan/licensing/issue.py` | `reissue_payload()` |
| `app/server/yinzhan/licensing/transfer.py` | 生成请求/重签 |
| `app/server/yinzhan/api.py` | `POST /api/license/transfer-request` |
| `deploy/license_transfer.py` | 离线转移 CLI |
| `app/tests/test_licensing.py` | v2/legacy 测试 |
| `app/tests/test_license_transfer.py` | 转移/重绑测试 |

## 5. 测试

```powershell
cd Sanitized_Data
python -X utf8 app\tests\test_licensing.py
python -X utf8 app\tests\test_license_issue.py
python -X utf8 app\tests\test_license_transfer.py
```

结果：

```text
test_licensing.py       14 passed
test_license_issue.py    7 passed
test_license_transfer.py 3 passed
```

覆盖：

- 重装系统（MachineGuid/卷序列号变化）v2 不变；
- v1 legacy 机器码可导入；
- 转移请求 → 重签 → 新机器导入；
- 旧订单/license_id/到期时间保留；
- transfer_count/history 签名。

## 6. 长期账号/席位/设备（仅规划，不实施）

```text
账号/订单
  └── 席位 max_devices
        ├── 设备 A：active
        ├── 设备 B：revoked
        └── 激活/解绑审计
```

能力：

- 账号登录/订单绑定；
- 设备注册、解绑、转移；
- 重装/VM 重建后自助重新激活；
- 旧设备服务端撤销；
- 离线签名 license 作为 fallback；
- 设备数/转移频率风控。

实施前提：

- 需要账号/授权服务；
- 与当前“零外发文件处理”不冲突，只有授权网络请求；
- 需要产品/运营确定设备表、订单系统、客服流程。

## 7. 回滚

基线：

- git tag：`pre-license-v2-transfer-20261113`
- 备份：
  - `ZY_PDF/out/licensing_backup/machine.py.bak`
  - `service.py.bak`
  - `issue.py.bak`
  - `test_licensing.py.bak`

回滚恢复上述文件即可；`deploy/license_transfer.py` 可保留为工具。
