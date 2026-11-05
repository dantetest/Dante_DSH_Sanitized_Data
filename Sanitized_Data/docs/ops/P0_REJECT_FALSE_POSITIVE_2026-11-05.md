# P0 误报取消脱敏（v1.5.2 · 2026-11-05）

> 用户确认：先做 P0；批量取消/「全部不脱敏」不做；高风险按 PII 维度二次确认；界面保持简洁。

## 1. 功能

### 单处取消

- 右侧「隐去清单」所有自动识别实体新增 **不脱敏** 操作；
- 按钮默认只在**悬停条目**时出现，保持清单干净；待确认条目始终显示，方便直接拒绝误报；
- 手动框选项保留 **撤销**；
- 预览中的红色/替换文字支持 **双击不脱敏**（黑色也会在双击时取消）。

### 撤销

取消后底部 toast：

> 已设为不脱敏：身份证号，原文会保留。 [撤销]

点「撤销」→ 恢复原脱敏，预览/计数/导出同步恢复。

### 高风险 PII 二次确认

以下类型/标签取消前必须二次确认：

```text
PERSON / ID_CARD / ID_NUMBER / ID_NAME / ID_ADDRESS / ID_PHOTO /
ID_AUTHORITY / ID_VALIDITY / PHONE / EMAIL / ADDRESS / BANK /
BANK_CARD / BANK_ACCOUNT / PASSPORT / LICENSE / SOCIAL_CREDIT /
MEDICAL / INSURANCE / PASSWORD / SECRET / STUDENT_ID / EMPLOYEE_ID
```

提示：`「身份证号」属于高敏感个人信息。不脱敏会保留原文，仍然继续吗？`

低风险（金额/日期/机构等）一键取消 + toast 撤销。

## 2. 不做（按确认）

- 不做批量「本类全部不脱敏」；
- 不做「全部不脱敏」；
- 不做“已忽略”列表（P0 仅 toast 撤销）。

## 3. 后端

- `Entity` 新增 `rejected_decision`：reject 时暂存原 decision，restore 时恢复；
- `update_entity` 支持 `action=restore`；
- `action=reject` 仍把 decision 设为 keep，但不会丢失原 decision；
- 现有预览/导出/计数继续按 `status=rejected` 过滤，无需改导出器。

## 4. 实测

### 4.1 UI 实测（样例 `20220308-设计劳务.docx`）

```text
rows 16
btn_text 不脱敏 visible True
toast1 「身份证号」属于高敏感个人信息。不脱敏会保留原文，仍然继续吗？
toast2 已设为不脱敏：身份证号，原文会保留。
toast3 已恢复「身份证号」的脱敏。
```

### 4.2 导出实测

- 拒绝一个 PHONE 实体（13901827733）后导出 human；
- 成品中仍保留该号码（符合“不脱敏”）；
- 恢复后重新校验通过。

### 4.3 回归

- `test_manual_flow_fix.py`：7 passed
  - 新增 reject → restore decision 保持；
  - 前端包含 `不脱敏`/`requestRejectEntity`/`restoreEntity`/`HIGH_RISK_PII`；
- 其他打包自检：version=1.5.2、keyring/OCR 正常。

## 5. 产物

Release：<https://gitea.blctek.com:8443/baoluo/DSH_DESKTOP_WORKSPACE/releases/tag/v1.5.2>

| 产物 | SHA-256 |
|---|---|
| `Sanitized_Data-1.5.2-win64-portable.zip` | `0AF2A4C3C9638021B3432A6AD62FAE01308A72727CFA076C4063FF8ACFDD6040` |
| `Sanitized_Data_Setup_1.5.2_x64.exe` | `1B006BB956BDBE8DF0DA5A16EEDB90664A37B862C0DCA2587B60A94FE5E3D9E1` |

## 6. 使用方式

1. 处理文件后进入「对照」；
2. 悬停右侧「隐去清单」中的条目；
3. 点 **不脱敏**；
4. 高敏感 PII 会弹确认，选「仍不脱敏」；
5. 误点就在 toast 里点 **撤销**；
6. 也可以直接双击右侧预览里的红字/黑条取消。

> 提醒：取消身份证/手机号等高风险脱敏会保留原文，请只对确定误报的条目使用。
