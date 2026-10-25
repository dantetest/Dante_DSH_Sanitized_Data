# Licensing W1 实现与自测（2026-10-25）

> 范围：P1 工程第一项「离线授权内核 W1」。目标是把授权从设计推进到可测试的本地内核；
> 不含免费额度门禁（W2）、授权页 UI（W3）、在线支付（W4/W5）。

## 1. 已实现

| 模块 | 文件 | 内容 |
|---|---|---|
| 机器码 | `app/server/yinzhan/licensing/machine.py` | Windows MachineGuid+卷序列号；macOS IOPlatformUUID+卷 UUID；Linux machine-id+product_uuid；SHA-256 + Crockford Base32，显示 `XXXX-XXXX-XXXX-XXXX` |
| 公钥 | `licensing/keys.py` + `public_keys.json` | 只读公钥注册表；公钥可进包，私钥不进仓库/包 |
| 套餐 | `licensing/tiers.py` | free/trial/personal/pro/team/org 功能位与免费限额（单一事实来源） |
| 授权内核 | `licensing/service.py` | license.json 验签、机器绑定、年度/买断、到期/14 天宽限、trial 14 天、时钟回拨、状态文件 HMAC、本地事件 |
| API | `yinzhan/api.py` | `GET /api/license/status`、`GET /api/license/machine-code`、`POST /api/license/import`、`POST /api/trial/start` |
| 打包 | `desktop/app.spec` | 将 `public_keys.json` 作为数据文件收进安装包 |
| 密钥工具 | `deploy/license_keygen.py` | 生成 Ed25519 密钥对；私钥加密写用户目录，公钥输出到独立 json |
| 测试 | `app/tests/test_licensing.py` | 12 个用例；已加入 `run_all.py` |

## 2. 安全/行为要点

- **零外呼**：所有校验/状态读写均本地完成；未新增网络库或请求。
- **私钥隔离**：仓库内只有空 `public_keys.json`；测试密钥只存在于测试进程（临时生成），不落仓库。
- **时钟回拨**：所有时间判断用 `max(now, last_seen)`；回拨 48h 以上标 `clock_suspect`，不能延长试用。
- **状态文件 HMAC**：`data/license/state.json` 带 HMAC；损坏/被改不恢复试用资格，也不影响已导入 license 的验签。
- **错误机**：license 的 `machine_codes` 不匹配 → `license_machine_mismatch`，不写 license 文件。
- **过期**：年度授权到期后 14 天宽限（`grace`），宽限结束回 free；导入已过期文件被拒绝。
- **导入备份**：旧 `license.json` 移到 `license.json.bak`；不删除用户数据。
- **本地事件**：`data/license/events.jsonl` 只记事件类型/时间/授权号/SKU，不记授权文件内容或 PII。

## 3. API 契约

```http
GET /api/license/status
```
返回：
```json
{
  "state": "free|trial|paid|grace",
  "sku": "free|trial|personal|pro|team|org",
  "edition": "免费版/试用版/...",
  "features": ["all_packs", "..."],
  "limits": {"jobs_per_day": 3, "max_pages": 20, "...": "..."},
  "machine_code": "K7Q29WXM4TRBEFGH",
  "machine_display": "K7Q2-9WXM-4TRB-EFGH",
  "trial": {"used": false, "active": false, "expires_at": null, "days_left": 0},
  "license": null,
  "license_error": null,
  "clock_suspect": false
}
```

```http
GET  /api/license/machine-code
POST /api/license/import      # {"content": "{...license.json...}"} 或直接 license JSON
POST /api/trial/start
```

## 4. 测试结果

- `python -X utf8 app/tests/test_licensing.py`：**12/12 通过**
- `python -X utf8 app/tests/run_all.py --only licensing`：**1/1 通过**
- HTTP 冒烟：status/machine-code/trial 均返回 200；trial 进入 14 天试用
- 完整 `run_all`：**32/32 通过**，耗时 312.6s；打包后的安装包加载公钥留到 W3 生成生产公钥后验证

## 5. 暂未做

1. **W2 免费额度门禁**：3 份/天、20 页/份、OCR 10 页/份、格式白名单；拦截顺序与 UI 状态条。
2. **W3 授权页/购买入口**：`/license` 页面、购买二维码、导入 UI、DSH 面板升级卡。
3. **生产公钥**：当前 `public_keys.json` 为空；W3 前用 `deploy/license_keygen.py` 生成，私钥离线保管，只把公钥合并进包。
4. **签发侧**：订单/支付/签名服务在商业化服务端，不属于本仓库。
5. **团队/机构管理台**：P1 后续。

## 6. 复现命令

```powershell
# 测试
python -X utf8 app/tests/test_licensing.py
python -X utf8 app/tests/run_all.py --only licensing

# 启动本地服务后
curl http://127.0.0.1:8760/api/license/status
curl http://127.0.0.1:8760/api/license/machine-code
curl -X POST http://127.0.0.1:8760/api/trial/start
```

## 7. 下一步（P1）

1. 生成生产密钥对并保管私钥（只把公钥并入 `public_keys.json`）；
2. W2：免费额度计数/门禁（先后端，UI 后做）；
3. W3：`/license` 页面 + 升级入口；
4. 接入签发服务/人工签发，签出第一张测试 license 做端到端；
5. v1.3.0 打包前回归 `run_all`。
