# 01 · 离线授权设计（Licensing）

> **状态**：设计规划（未改代码）· 2026-10-20 · 关联 [商业化总览](monetization.md)、[09 定价报告](../research/09-市场定价调研与定价建议.md)
> **核心约束**：产品引擎**零外呼**、无账号、无遥测；授权必须 **100% 离线校验**；私钥只在授权服务器，公钥才进安装包。

---

## 0 结论先行

1. **三态授权**：`free`（默认，无授权/授权失效）→ `trial`（首次启动自动 14 天全功能）→ `paid`（导入授权文件）；优先级 `paid > trial > free`。
2. **授权文件**：`license.json` = `payload`（明文 JSON）+ `sig`（Ed25519 签名，私钥服务端签发）；产品内用**内置公钥**验签，结合**机器码**、**有效期**、**时钟检测**判定。
3. **机器码**：由 OS 稳定硬件标识哈希成 `XXXX-XXXX-XXXX-XXXX`（Crockford Base32），不上传原始标识；个人版 1 台、专业版 2 台、团队版按席位绑定、机构版绑服务器。
4. **到期策略**：到期前 30/14/7/1 天提醒 → 到期后 **14 天宽限**（功能可用 + 横幅）→ 宽限结束回免费版；数据与映射库不删。
5. **不做的**：联网校验、在线吊销、实时并发控制、DRM/反调试、自动续费。接受一定破解/试用重置风险，用「低价 + 持续更新 + 服务」对冲（与 09 报告 §6 一致）。

---

## 1 需求清单

| # | 需求 | 验收 |
|---|---|---|
| R1 | 未授权 = 免费版（按 [02](free-tier.md) 限额）；用户首次启用后获得 14 天全功能试用 | 断网可用 |
| R2 | 付费后凭订单取得授权文件，本机导入立即解锁 | 免重启（或提示重启一次） |
| R3 | 授权可约束：版本、功能、设备数/席位、有效期、更新期 | 逐项可测 |
| R4 | 伪造/篡改/复制到别的机器/改系统时钟 均不能获得付费功能 | 测试用例覆盖 |
| R5 | 续费/升级/换机可重新签发；过期有提醒与宽限 | 流程跑通 |
| R6 | 团队/机构授权可离线分发与部署 | 文件可分发、逐机校验 |
| R7 | 导入/续费/试用开始等事件进本地审计（只记类型/时间，不记敏感值） | 审计可查 |
| R8 | 全程不新增运行时网络调用；不引入不可离线安装的依赖 | 抓包 + 干净环境安装 |

**非目标**：在线撤销（退款后无法远程关停已下载授权）、并发席位实时控制、防逆向、硬件级可信启动。

---

## 2 状态机

```
                   首次启用（启动弹窗/授权页）
   ┌─────────┐   启用 14 天   ┌─────────┐   导入 license   ┌─────────┐
   │  free   │ ─────────────▶ │  trial  │ ───────────────▶ │  paid   │
   └─────────┘                └─────────┘                  └─────────┘
        ▲                          │ 试用到期                    │ 到期
        │                          ▼                            ▼
        │                     ┌─────────┐  宽限 14 天后   ┌─────────┐
        └──────────────────── │ expired │ ◀────────────── │  grace  │
                              └─────────┘                 └─────────┘
                                                  续费→新 license→ paid
```

| 状态 | 触发 | 能力 | 界面 |
|---|---|---|---|
| `free` | 无授权、授权无效/过期、试用结束 | 按 [02](free-tier.md) 矩阵 | 免费版标识 + 额度 + 升级入口 |
| `trial` | 首次启动弹窗引导启用；用户选「先用免费版」则试用资格保留，可在 `/license` 随时启用（**仅一次**）；启用后 14 天 | **全功能**：全部规则包、TIF/3D、MCP、审计导出（不含自定义规则包、团队管理台；批量/管理台为 P1 能力，上线后开放） | 试用剩余天数横幅 + 购买入口 |
| `paid_annual` | 导入有效年度授权 | 按 SKU 功能；不限额 | 授权状态 + 到期日 + 续费入口 |
| `paid_perpetual` | 导入有效买断授权 | 按 SKU 功能；永久可用 | 更新期（updates_until）+ 续费更新入口 |
| `grace` | 年度授权到期后 14 天内 | 付费功能继续可用 | 醒目到期横幅 + 立即续费 |
| `expired` | 宽限结束 | 回 `free` | 授权过期提示 + 续费 |

> 状态计算逻辑集中在 `licensing/service.py::status()`；所有 UI/门禁只消费这一个结果，避免各处重复判断。

---

## 3 授权文件格式

### 3.1 容器

文件名：`license.json`（UTF-8，无 BOM）。结构：

```json
{
  "schema": 1,
  "alg": "Ed25519",
  "key_id": "prod-2026",
  "payload": { "...": "见 3.2" },
  "sig": "base64(64 字节签名)"
}
```

- 签名字节：`payload` 的**规范化 JSON**，规则固定为
  `json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`
  （实现必须用同一函数生成测试向量；空格/换行/键序不同会导致验签失败，**不要**对原始文件字节签名）。
- 验签：`cryptography` 的 Ed25519；按 `key_id` 选择公钥；未知 `key_id`/`sig` 错误 → 拒绝并提示「授权文件无效」。
- 兼容：未知字段忽略；`schema` 高于当前支持 → 提示升级软件；`schema` 低于当前 → 正常校验。

### 3.2 payload 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `license_id` | string | ✓ | 授权唯一编号（服务端生成） |
| `order_id` | string | ✓ | 对应订单号（客服/对账用） |
| `sku` | enum | ✓ | `personal` / `pro` / `team` / `org` |
| `edition` | string | ✓ | 展示名：个人版/专业版/团队版/机构版 |
| `term` | enum | ✓ | `annual`（到期失效）/ `perpetual`（永久可用，受 `updates_until` 约束） |
| `seats` | int | ✓ | 席位数；个人 1、专业 1（`max_devices=2`）、团队 N、机构 N |
| `max_devices` | int | ✓ | 可绑定的机器数：个人 1、专业 2、团队=seats、机构=1（服务器） |
| `machine_codes` | string[] | ✓ | 已绑定机器码（见 §4），长度 ≤ `max_devices` |
| `customer` | object | — | `{name, org, note}`，展示用 |
| `issued_at` | ISO8601 | ✓ | 签发时间 |
| `not_before` | ISO8601 | ✓ | 生效时间（防未来授权提前用） |
| `expires_at` | ISO8601/null | ✓ | 到期时间；`perpetual` 为 null |
| `updates_until` | ISO8601/null | — | 买断/年度更新期截止（展示与下载服务使用；产品内软提醒，不硬锁功能） |
| `grace_days` | int | ✓ | 宽限天数（默认 14） |
| `features` | string[] | ✓ | 功能位，见 3.3 |
| `team_id` | string | — | 团队/机构标识（团队版管理能力） |
| `issuer` | string | ✓ | `BLCTEK` |
| `notes` | string | — | 客服备注（不要放敏感信息） |

### 3.3 功能位（features）

| feature | 含义 | free | 个人 | 专业 | 团队 | 机构 |
|---|---|---|---|---|---|---|
| `all_packs` | 全部内置行业规则包 | — | ✓ | ✓ | ✓ | ✓ |
| `custom_packs` | 自定义规则包（YAML 加载） | — | — | ✓ | ✓ | ✓ |
| `tif` / `d3d` | TIF 正射 / 3D 格式 | — | ✓ | ✓ | ✓ | ✓ |
| `mcp` | MCP 接入 | — | — | ✓ | ✓ | ✓ |
| `batch` | 批量处理（文件夹，P1 实现） | — | — | ✓ | ✓ | ✓ |
| `audit_export` | 审计/复检报告导出（P1 新增 `GET /api/audit/export` 后才生效） | — | — | ✓ | ✓ | ✓ |
| `team_admin` | 团队管理台/规则包下发（P1） | — | — | — | ✓ | ✓ |
| `org_deploy` | 机构私有化部署授权 | — | — | — | — | ✓ |
| `ocr` | 扫描件 OCR（免费版有页数上限） | 限额 | ✓ | ✓ | ✓ | ✓ |

> **限量不是 feature**：免费版的「3 份/天、20 页/份、OCR 10 页/份」由 [02](free-tier.md) 的 quota 配置实现；付费授权 = 不限额（`limits=null`）。feature 只控制「有没有」，不控制「多少」。

> **feature 与 trial 的关系**：试用不使用 license 文件；`tiers.py` 的 trial 配置授予「专业版级」features（含 `all_packs/tif/d3d/mcp/audit_export`，不含 `custom_packs/team_admin`；批量/管理台 P1 上线后开放）。**各文档的 trial 定义以本句 + [02 §1](free-tier.md) 为准**，W1/W2 不得各写一套。

### 3.4 两个示例

年度个人版：

```json
{"schema":1,"alg":"Ed25519","key_id":"prod-2026","payload":{
  "license_id":"lic_20261020_7f3a9c","order_id":"ord_20261020_00123",
  "sku":"personal","edition":"个人版","term":"annual",
  "seats":1,"max_devices":1,"machine_codes":["K7Q2-9WXM-4TRB-EFGH"],
  "customer":{"name":"张三","org":null,"note":""},
  "issued_at":"2026-10-20T18:30:00+08:00","not_before":"2026-10-20T18:30:00+08:00",
  "expires_at":"2027-10-20T23:59:59+08:00","updates_until":"2027-10-20T23:59:59+08:00",
  "grace_days":14,
  "features":["all_packs","tif","d3d"],
  "team_id":null,"issuer":"BLCTEK","notes":""},
 "sig":"<base64>"}
```

机构版（服务器绑定）：

```json
{"...": "...","payload":{
  "sku":"org","edition":"机构版","term":"annual","seats":50,"max_devices":1,
  "machine_codes":["ORG1-2J5K-7HXD-9WQP"],
  "features":["all_packs","custom_packs","tif","d3d","mcp","batch","audit_export","team_admin","org_deploy"],
  "team_id":"org_20261020_blctek_hospital",
  "...": "同年度字段"}}
```

---

## 4 机器码（machine code）

### 4.1 目标与原则

- **稳定**：重装系统/升级软件/换网卡不失效（尽力而为）；
- **可用**：Windows 优先，macOS/Linux 变体兼容（桌面版已有三平台打包）；
- **不泄露**：只输出哈希后的短码，不显示原始硬件序列号；
- **离线**：生成只需要本机信息，不联网。

### 4.2 算法 v1（实现见提议文件 `licensing/machine.py`）

```
raw = "machine-v1|" + component_a + "|" + component_b
digest = SHA-256(raw.encode("utf-8"))          # 32 字节
code   = CrockfordBase32(digest[:10])           # 80 bit → 16 字符
display = code[0:4]-code[4:8]-code[8:12]-code[12:16]   # 例：K7Q2-9WXM-4TRB-EFGH
```

各平台组件：

| 平台 | component_a | component_b | 兜底（缺一项时） |
|---|---|---|---|
| Windows | `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`（`winreg`） | 系统盘卷序列号（`ctypes` `GetVolumeInformationW`） | `uuid.getnode()` 的 MAC + hostname（标注 fallback） |
| macOS | `IOPlatformUUID`（`ioreg -rd1 -c IOPlatformExpertDevice` 解析） | 启动盘卷 UUID（`diskutil info /` 解析） | hostname + MAC |
| Linux | `/etc/machine-id`（或 `/var/lib/dbus/machine-id`） | 根文件系统 UUID（`/proc/self/mountinfo` 解析或 `/sys/class/dmi/id/product_uuid`） | hostname + MAC |

**不要**用：主机名单独、IP、CPU 型号、安装盐（重装会变）、全部 MAC 列表（虚拟网卡会变）。

### 4.3 展示与复制

- 授权状态页显示「本机识别码：K7Q2-9WXM-4TRB-EFGH」+「复制」按钮；
- 购买/绑定页提示用户回应用内复制；**不要**让用户手动抄（可用二维码把机器码编码进去，供手机端购买时扫）。
- 机器码只用于绑定与客服核对；服务端只存机器码，不存原始硬件标识。

### 4.4 换机/硬件变更

- 重装系统/重装软件：机器码不变 → 授权可直接复用（授权文件需重新导入或留在数据目录）。
- 换主板/换机：机器码变 → 需重新签发（政策见 §8.3）；团队由管理员在后台重绑；机构按合同。
- 多 OS 用户（同一台机）：**双系统（Windows/macOS/Linux 任一组合）视为两台设备**——各平台机器码取自不同系统标识，不可比；同一 OS 下不同用户机器码相同，个人版按「设备」计数，不按用户计数（可接受）。

---

## 5 试用（trial）

| 项 | 设计 |
|---|---|
| 时长 | **14 天**（决策点 D2 可改 7 天） |
| 开始 | 首次启动弹窗引导；用户可选「先用免费版」→ **试用资格保留**，在 `/license` 随时启用（**仅一次**）；启用后计时 14 天；不联网 |
| 范围 | 全功能：全部规则包、TIF/3D、**MCP**、审计导出；**不含**自定义规则包、团队管理台（批量/管理台为 P1 能力，上线后开放） |
| 结束 | 到期自动回 `free`；已导出文件/映射库/审计保留 |
| 提醒 | 剩余 7/3/1 天横幅；到期日提示 |
| 状态文件 | 数据根目录下 `license/trial.json`（源码模式 `Sanitized_Data/data/license/`；桌面版 `%LOCALAPPDATA%\Sanitized_Data\license/`）：`first_run_utc / expires_utc / last_seen_utc / machine_code / sig`；`sig = HMAC-SHA256(derived_key, canonical_json)`，`derived_key = HKDF(build_secret, salt=machine_code)`；`build_secret` 以常量/混淆形式编译进程序（非私钥，仅防手改） |
| 冗余 | 除 `license/trial.json` 外，另写一份到：Windows `HKCU\Software\BLCTEK\SanitizedData`（值名 `trial`）；macOS/Linux `~/.config/sanitized-data/trial.json`。读取时取「最早 first_run / 最晚 last_seen」，避免删一处就重置 |
| 时钟 | 见 §6.3；回拨系统时钟**不能延长**试用（`effective_now = max(system_now, last_seen)`） |
| 防重置 | 尽力而为：三处冗余 + 时钟检测 + **复用既有审计哈希链**（`audit.py` 记一条 `trial_started` 事件作为第 4 处冗余）；`POST /api/trial/start` 仅在四处都无试用记录时允许。接受卸载重装/清注册表可重置——**不做联网校验**，价格低，不值得为此破坏产品承诺 |

> 试用与免费版的关系：试用期内**不执行**免费限额；试用结束后按免费版限额。已在试用期完成的工作不受影响。

---

## 6 本地校验与状态

### 6.1 状态文件

数据根目录下 `license/state.json`（路径同 §5 说明）：

```json
{
  "schema": 1,
  "last_seen_utc": "2026-10-20T12:00:00Z",
  "trial": {"first_run_utc": "...", "expires_utc": "..."},
  "usage": {"date": "2026-10-20", "jobs": 2, "ocr_pages": 7},
  "last_license_id": "lic_...",
  "flags": {"clock_suspect": false},
  "sig": "hex(HMAC-SHA256(derived_key, canonical_json_without_sig))"
}
```

- 每次启动、每次限额变更、每次导入授权后更新并重签；
- HMAC key 由 `build_secret + machine_code` 派生，与试用文件共用；
- **损坏/缺失**：不惩罚成「完全不能用」；策略为：视为「今日额度已用 1 份」并写 `flags.state_recovered=true`，界面提示「本机状态异常，已重置今日额度」，同时保留付费授权校验（授权文件本身有签名，不受 state 影响）。**防止删 state 无限白嫖**：损坏时只给 1 份而不是 3 份。
- 系统时间的记录：`last_seen_utc` 只在时间**前进**时更新（`max`）。

### 6.2 校验顺序（导入与启动共用）

> 所有时间判断统一用 `effective_now = max(system_now, last_seen_utc)`：**回拨系统时钟既不能延长试用，也不能让已到期授权"复活"**；前拨会按事实提前到期（可接受，提示校正时间）。
>
> **一致性约束（验签通过后先做，任一不满足 → `license_invalid`）**：`expires_at > not_before`（年度授权）；`term=perpetual` 时 `expires_at` 必须为 `null`；`1 ≤ len(machine_codes) ≤ max_devices`；`seats ≥ 1`；`team`/`org` 必填 `team_id`；`features` 必须是已知枚举子集；`grace_days ∈ [0, 60]`。

```
读取 license 文件
 ├─ 文件不存在/读取失败 → 无授权（继续 trial/free 判定）
 ├─ schema/alg/key_id 不支持 → invalid（原因可读）
 ├─ Ed25519 验签失败 → invalid
 ├─ payload 字段缺失/类型错/一致性约束不满足 → invalid
 ├─ effective_now < not_before → `license_not_yet_valid`（提示校正系统时间；偏差 >48h 同时置 `clock_suspect`）
 ├─ machine_codes 不含本机机器码 → machine_mismatch
 ├─ term=annual 且 effective_now > expires_at + grace_days → expired
 ├─ term=annual 且 effective_now > expires_at → grace（可用 + 横幅）
 └─ 通过 → paid（features 生效；限额解除）
```

> `perpetual` 不看 `expires_at`；`updates_until` 只用于「更新已过期」提示与下载服务，不做功能硬锁。

### 6.3 时钟策略

| 场景 | 处理 |
|---|---|
| 系统时间正常前进 | 正常 |
| 回拨（`now < last_seen - 48h`） | `clock_suspect=true`；**所有时间判断用 `effective_now = max(now, last_seen)`**：试用/试用剩余/授权到期/宽限都按此计算——回拨不能延长任何期限，已到期授权不会复活；显示「系统时间异常，请校正」 |
| 前拨（`now > expires`） | 按到期处理（可能提前失效）；客服可说明；不建议用户改时间 |
| 时区变化 | 全部内部用 UTC；展示用本地时间 |

### 6.4 执行点（enforcement points）

| 位置 | 现状 | 需要加什么 | 失败行为 |
|---|---|---|---|
| `api.create_job`（`POST /api/jobs`） | 直接 `MANAGER.create` | 调 `licensing.gate_job(path, fmt)`：格式/规则包/页数/OCR/日限额 | 返回 `403` + 错误码 + 可读文案 + 升级链接 |
| `jobs.JobManager.create` | 被 API 与 MCP 调用 | 防御性再校验（防绕过 API 直调）；detect 前断言规则非空 | 抛 `LicenseError`（API 层转 403）；空规则 → job=`error` |
| **`POST /api/jobs/{jid}/export` / `GET /api/jobs/{jid}/file`** | 成品导出（换名/涂黑） | **永不 gate**（产品底线：免费/试用到期后，已识别结果仍可导出；限额只发生在创建任务时） | — |
| `api.packs` / `GET /api/packs` / **`/api/health` 的 packs 字段** | 返回全部包 | 免费版过滤为通用包；付费全量；**不得在 health 里泄露付费包** | 过滤 + 升级提示（见 [02 §4.1](free-tier.md)） |
| `mcp_server.py` 四个工具 | 直接执行 | 工具入口 `licensing.require_paid("mcp")`；免费返回错误 JSON-RPC 结果 | 工具错误「需要专业版授权」 |
| `api.license/*`（新增） | — | `GET /api/license/status`、`POST /api/license/import`、`GET /api/license/machine-code`、`POST /api/license/backup` | 见 §6.5 |
| `GET /api/audit`、`POST /api/audit/verify` | 可看/可验 | **免费可看/可验**（不 gate） | — |
| `GET /api/audit/export`（**P1 新增**） | 当前不存在 | 新增后才 gate `audit_export`（导出审计/复检报告） | 403 |
| 前端 `app.js` | — | 状态条/额度/拦截弹窗/授权页 | 只做展示，安全靠后端 |

> **不 gate 的东西**：换名/涂黑（两种出口）、本地处理、出口自检、框选补漏、严格度三档——这些是产品底线，免费版也必须有（见 09 §5.1 原则 3）。

### 6.5 新增 API 契约（建议）

| 方法/路径 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `GET /api/license/status` | — | `{state, sku, edition, term, expires_at, days_left, grace_days_left, machine_code, features, limits, trial:{...}, license_id, can_import:true}` | UI 唯一数据源 |
| `GET /api/license/machine-code` | — | `{code, display, source}` | 绑定页/客服 |
| `POST /api/license/import` | `{content: string}` 或 multipart 文件 | `{ok, state, edition, expires_at}` / `400 {error:{code,message}}` | 支持粘贴与拖文件 |
| `POST /api/license/backup` | — | `{path}` 或文件流 | 备份授权文件到用户选择位置（可选 P1） |
| `POST /api/trial/start` | — | 同 status | 手动启用试用；**仅当三处冗余 + 审计链均无试用记录时允许**（防重复试用） |
| `POST /api/license/refresh` | — | 同 status | 导入后/换文件后重读（可选） |

错误码（统一 `error.code`）：`license_invalid`、`license_machine_mismatch`、`license_expired`、`license_not_yet_valid`、`clock_suspect`、`quota_exceeded`、`feature_required`、`trial_expired`、`state_recovered`。

### 6.6 授权状态页（产品内，新页面/面板）

> **重要**：仓库现状 `audit.py` 只有 `append/recent/verify_chain/stats`，**没有导出接口**；`POST /api/jobs/{jid}/export` 与 `/file` 是**成品导出**（换名/涂黑），不是审计导出，**永远不要 gate**——否则免费版/试用到期用户会「识别完却导不出」，产品直接不可用。审计导出能力等 `GET /api/audit/export` 实现后再挂 `audit_export` 功能位。

- 当前状态徽章：免费版 / 试用（剩 9 天）/ 个人版 / 专业版 / 团队版 / 机构版 / 已过期；
- 到期日、更新期（买断）、宽限剩余；
- 本机识别码（复制/二维码）；
- 「导入授权文件」（选择文件/粘贴文本）「备份授权」「购买/续费」「开始 14 天试用」；
- 使用条款/退款说明/客服入口；
- 审计事件：导入成功/失败原因（只记类型）。

---

## 7 团队版与机构版

### 7.1 团队版（¥1,999/年/5 席）

- 授权文件 `sku=team`，`seats=5`，`machine_codes` 含 **5 个机器码**；管理员（购买人）在购买后台逐个录入成员机器码 → 生成一个团队授权文件；
- 分发：管理员把同一个 `license.json` 发给成员；每台机器只校验「本机机器码在列表内」，列表外的机器拒绝；
- 授权文件本身不含私钥/敏感信息，可安全分发；文件内 `team_id` 用于 P1 管理台；
- 增席：后台重新签发（`machine_codes` 增加）；减席：重新签发（旧文件仍可能被旧机器使用——离线限制，靠合同/管理约束，接受）；
- 管理台（P1）：席位列表、机器码、到期、续费；**P0 可先用「后台人工批量绑定」**。

### 7.2 机构版（¥3.88 万起，私有化）

- `sku=org`，绑定**服务器机器码**（部署机），`max_devices=1`，`seats` 为合同席位；
- 功能位含 `org_deploy`、`team_admin`；授权文件由交付工程师放到数据根目录的 `license/license.json`（`YINZHAN_DATA_DIR/license/license.json`）；
- 并发/席位控制：产品无账号体系，不做强制并发限制；靠**合同 + 审计日志 + 部署约定**（如只在内网、由信息科管理）；
- 交付清单：授权文件、部署说明、验收单、SLA、续费提醒；
- 机构续费/升级：后台重新签发（同机器码）；服务器更换按合同重签。

---

## 8 生命周期与运维

### 8.1 到期与续费

| 节点 | 行为 |
|---|---|
| 到期前 30/14/7/1 天 | 界面横幅 + 授权页提示；产品内不弹窗骚扰（每天最多一次） |
| 到期日 | 状态 → `grace`（14 天）：功能继续，横幅标红「已到期，宽限至 X」 |
| 宽限结束 | 状态 → `free`：限额生效；已有文件/映射库/审计不删；导出成品不受影响 |
| 续费 | 官网购买续费 → 重新签发（同机器码、新到期）→ 用户导入 → 状态回 `paid`；旧文件自动备份 `.bak` |
| 升级 SKU | 差价购买 → 重新签发（features 增加）→ 导入 |
| 买断更新期 | `updates_until` 到期后显示「更新期已过，可继续用当前版本；续费更新获取后续版本下载资格」；**产品内不硬锁功能**。更新下载资格由公网下载服务强制执行：下载新版本安装包/新授权需提供订单号 + 机器码/下载 token，且 `now ≤ updates_until`（否则只给当前版本）；产品端只做软提醒 |
| 升级/换机/加席 | 需要独立 SKU/订单字段（差价、换机服务费、加席），见 [03 §3](payment-invoice.md)；P0 先支持「客服人工报价 + 人工签发」，后台登记 `upgrade_from`/`reissue_fee`/`seat_delta` |

### 8.2 授权找回

- 官网「找回授权」：订单号 + **短信验证码或邮件 magic link** + 机器码 → 重新下载（限次数，如 12 个月 10 次）；**不能只凭手机号查询订单**；
- 客服兜底：核对订单后手工重发；
- 团队：管理员后台自助重发/重绑；机构：交付工程师。

### 8.3 换机政策

| 场景 | 处理 | 收费 |
|---|---|---|
| 重装系统/软件 | 机器码不变，重新导入原文件即可 | 免费 |
| 个人/专业版换机 | 客服重签（旧授权文件在旧机仍可能可用，离线限制，接受） | 12 个月内首次免费，之后 ¥50/次或按客服政策 |
| 团队换人 | 管理员后台重绑 | 免费（在席位内） |
| 机构换服务器 | 按合同重签 | 按合同 |

> 原则：**不为难真实用户**，但避免「一份授权满世界用」。离线产品的现实是「旧文件不可远程回收」，用低价与人工政策平衡。

### 8.4 密钥管理

- 生成：离线环境生成 Ed25519 密钥对；**私钥只在授权服务器**（文件权限 600 / Windows ACL 仅服务账号），异地加密备份 1 份；
- 公钥：以 `licensing/public_key.pem`（或代码常量）进产品；**测试密钥对**单独放 `app/tests/fixtures/licensing/`，生产包只信任生产公钥；
- 轮换：`key_id` 支持多公钥并存；新密钥上线后旧授权仍可验；私钥泄露时按「停用旧 key_id + 重新签发」流程；
- 纪律：私钥绝不进仓库/安装包/CI 日志/聊天记录；`.gitignore` 拒绝 `*.key`/`*.pem`（公钥例外需显式白名单）；CI 加 secret scanning。

---

## 9 威胁模型（接受与对策）

| # | 威胁 | 后果 | 对策 | 残留风险 |
|---|---|---|---|---|
| T1 | 伪造 license（自签/改 payload） | 白嫖付费功能 | Ed25519 验签；公钥内置；payload 全字段校验 | 极低 |
| T2 | 合法 license 复制到未绑定机器 | 多机使用 | 机器码绑定 + `max_devices` | 同机多用户可用；克隆虚拟机/克隆系统盘可能同机器码（接受） |
| T3 | 修改系统时钟延长试用/授权 | 白嫖 | `effective_now=max(now,last_seen)`；回拨标记；到期按事实处理 | 前拨可能提前失效（可接受） |
| T4 | 删除 state/trial 文件重置 | 重复试用/额度 | 三处冗余 + 损坏降级（只给 1 份）+ 时钟检测 | 彻底清理仍可重置（接受） |
| T5 | 逆向/补丁绕过 Python 校验 | 白嫖 | 不做重成本混淆；低价 + 持续更新 | 存在（接受） |
| T6 | 私钥泄露 | 大规模伪造 | 服务器隔离/权限/备份/轮换预案/审计签发 | 低（需运维纪律） |
| T7 | 钓鱼/假授权文件 | 用户被骗 | 官方文档说明导入来源；导入失败提示；客服核验 | 低 |
| T8 | 授权文件被恶意替换 | 功能异常 | 验签失败 → 回退到无授权/free；保留备份 | 低 |
| T9 | 团队文件外传 | 列表外机器不可用 | 机器码列表校验 | 列表内机器可转让（合同约束） |
| T10 | 机构绕过席位 | 超员使用 | 合同 + 审计 + 部署约定 | 中（接受，不为此加联网） |

---

## 10 测试计划

### 10.1 单元测试（`app/tests/test_licensing.py`）

- 规范化 JSON 签名字节稳定（固定测试向量：payload → canonical → 预期 sig 校验通过）；
- 验签：正确/篡改 payload/篡改 sig/错误 key_id/未知 schema；
- 字段校验：缺字段、类型错、负数 seats、machine_codes 空；
- 机器码：格式（16 位 Crockford）、确定性（同输入同输出）、跨平台桩（注入固定组件）；
- 状态机：free→trial→expired→free；paid→grace→expired；renewal；perpetual；
- 时钟：回拨 49h → suspect；试用不延长；到期 + 回拨 → 不回 grace；
- 限额与 feature 门禁（与 02 联测）：免费/试用/付费三态矩阵。

### 10.2 集成/E2E（离线）

- 断网（或 monkeypatch `socket.socket` 禁止外连）下跑完整链路；**新增代码静态扫描不得出现 `urllib.request`/`http.client`/`socket.connect`**（购买跳转只允许前端 `<a target="_blank">` 或 `webbrowser.open`）；
- `desktop/app.spec`/Inno 安装包：`public_key.pem`、`pricing.html`、`license.html` 必须可读；安装后断网导入授权成功；
- 免费额度：3 份后第 4 份被拒；页数超限被拒；OCR 超限被拒；规则包/TIF/MCP/审计导出被拒；
- 导入个人版 → 全部解锁；到期模拟 → 宽限横幅 → 回免费；
- 团队授权：正确机器通过、列表外机器拒绝；加席重签；
- 机构授权：服务器机器码通过；
- 篡改 state/trial → 降级逻辑；
- 审计事件：导入成功/失败、试用开始、到期提醒各 1 条。

### 10.3 测试环境与生产隔离

- 源码模式（`not paths.FROZEN`）允许 `YINZHAN_DEV_EDITION=pro|team|org` 环境变量用于现有测试/e2e；**打包冻结后该变量无效**；
- 或（更干净）测试启动前把「测试授权文件」放进临时 `YINZHAN_DATA_DIR/license/`，产品信任测试公钥仅在源码模式。二选一，实施时定（建议后者，少一个环境变量后门）。

### 10.4 手工验收清单

- 断网安装 → 首次启动 → 试用横幅 → 处理 1 份文件 → 导出；
- 官网购买（测试单）→ 绑定机器码 → 下载 license → 导入 → 重启后仍为付费；
- 换一台机器导入同一文件 → 拒绝并提示；
- 改系统时间 → 提示/行为符合 §6.3；
- 删除数据根目录下 `license/state.json` → 额度降级为 1 份，付费授权仍有效。

---

## 11 实施拆分（W1，估算 5–7 人日）

| # | 任务 | 人日 |
|---|---|---|
| 1 | `licensing` 包骨架 + 规范化 JSON + 验签 + key 管理 | 1.0 |
| 2 | 机器码（Win/macOS/Linux + 单测） | 1.0 |
| 3 | trial/state（HMAC、冗余、时钟、降级） | 1.0 |
| 4 | service 状态机 + 门禁接口 + 审计事件 | 1.0 |
| 5 | API 3–6 个路由 + 授权状态页数据 | 0.5 |
| 6 | 前端授权状态/导入/横幅（与 W2 合并做） | 1.0 |
| 7 | 单测/集成/E2E + 文档 | 1.0–1.5 |
| 8 | 联调（支付签发/购买页）+ 修缺陷 | 0.5–1.0 |

**新增文件（提议）**

```
app/server/yinzhan/licensing/__init__.py
app/server/yinzhan/licensing/keys.py          # 公钥表、key_id
app/server/yinzhan/licensing/machine.py       # 机器码
app/server/yinzhan/licensing/license.py       # 解析/验签/规范化
app/server/yinzhan/licensing/trial.py         # 试用
app/server/yinzhan/licensing/state.py         # HMAC 状态文件
app/server/yinzhan/licensing/service.py       # 状态机/门禁 API
app/server/yinzhan/licensing/quota.py         # 免费限额（W2）
app/server/yinzhan/licensing/public_key.pem   # 生产公钥（只读资源）
app/tests/test_licensing.py
app/tests/test_quota.py
```

**打包注意**：`desktop/app.spec` 需要把 `licensing/public_key.pem` 作为 `datas` 一并收集（参照现有 rules 打包方式）；`cryptography` 已在 `requirements.txt`，无需新增依赖；测试密钥对放 `app/tests/fixtures/licensing/`，**不进生产包**。

---

## 12 开放问题

| # | 问题 | 建议 | 需谁定 |
|---|---|---|---|
| L1 | 试用 14 天还是 7 天 | 14 天 | 用户 |
| L2 | 试用期是否含 MCP | 含（全功能口径一致） | 用户/产品 |
| L3 | 买断是否永久可运行、仅更新期受限 | 是（`updates_until` 软提醒） | 用户 |
| L4 | 老用户/内测用户赠送政策 | 送 1 年个人版 | 用户/市场 |
| L5 | 换机免费次数 | 12 个月 1 次免费，之后 ¥50/次 | 运营 |
| L6 | 机构版是否加「并发会话软限制」 | P0 不做，合同约束 | 用户 |
| L7 | 是否做「二维码机器码」方便手机购买 | 做（**纯前端生成**，不新增依赖；或 P1 再做） | 前端 |
| L8 | 授权文件是否允许公开分发（团队） | 允许（不含私钥；机器码列表限制） | 安全/后端 |
