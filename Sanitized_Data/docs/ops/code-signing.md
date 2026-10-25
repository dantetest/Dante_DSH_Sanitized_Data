# Windows 代码签名申请与落地指南

> 状态：操作指南（2026-10-25）· 适用于「数据隐站 v1.2.2」及后续版本
> 目标：让 Windows 安装包/主程序有可信发布者签名，减少「未知发布者」与 SmartScreen 拦截；**不承诺首次下载零警告**。

## 0. 先给结论

- 我们买 **OV Code Signing（组织验证代码签名）**，优先选 **云 HSM 签名**，不要为了 SmartScreen 单独多花钱买 EV。
- Microsoft 官方 2026 文档明确：**EV 证书已不再绕过 SmartScreen**；OV/EV 首次下载都可能显示“无法识别的应用”，需要靠持续签名 + 下载量积累发布者声誉。
- **Microsoft Artifact Signing**（原 Trusted Signing，$9.99/月起）目前 Public Trust 只对部分国家/地区组织开放，**中国大陆主体不在列表内**，不要作为首选。
- 正式购买前必须确认：证书主体 = 软件发布主体（四川步络驿科技有限公司），安装包 Publisher 必须一致。
- 证书有效期目前主流 CA 最长 **459 天**（DigiCert 等 2026 规则），需要按年续费/续签。

## 1. 买哪种

| 方案 | 是否推荐 | 说明 |
|---|---|---|
| **OV Code Signing + 云 HSM** | **推荐** | 有组织验证、发布者名称可见；云签名适合 CI，不用插 USB token |
| EV Code Signing | 非必要 | 更贵、审核更严；Microsoft 已明确 EV 不再绕过 SmartScreen，除非客户/招投标要求 |
| Microsoft Store（MSIX） | 值得评估 | Store 分发的应用由 Microsoft 证书签名，**不会出现下载 SmartScreen 警告**；需要 MSIX 打包与商店上架 |
| 自签名证书 | 不可用 | 对外发布没有信任价值 |
| 无签名 | 现状 | 每个文件/每个版本从零积累信誉，用户体验最差 |

> 参考：[Microsoft SmartScreen 声誉文档](https://learn.microsoft.com/zh-cn/windows/apps/package-and-deploy/smartscreen-reputation)（2026-08 更新）、[Artifact Signing 文档](https://learn.microsoft.com/en-us/azure/artifact-signing/overview)。

## 2. 推荐采购路线

### 2.1 证书形态

选 **OV Code Signing Certificate**，优先支持以下任一云签名方式：

- DigiCert **KeyLocker**
- SSL.com **eSigner**
- Sectigo **Certificate Manager / 云签名**
- 其他 CA 的 **Cloud HSM / 云签名** 服务

不建议只买 USB token 版本：

- token 不能直接进 CI；
- 每次发版要人工插 token；
- token 丢失/损坏补办慢。
- 如果只能 token，固定在发布机签名，保留备份和交接人。

### 2.2 询价对象（至少 3 家）

优先找**有中国主体、能开增值税发票**的授权服务商/代理商：

- DigiCert / SSL.com / Sectigo / GlobalSign / Certum 的中国区授权代理；
- 国内证书服务商（如沃通、天威诚信、锐成信息、亚洲诚信等）的 OV 代码签名 + 云签名方案。

询价时必须问清：

1. 是否 **OV Code Signing**，不是 SSL/邮件/文档签名；
2. 是否支持 **云 HSM / CI 签名**，API/SDK 怎么接；
3. 报价是否含 **token/云签名年费**、是否含发票；
4. 有效期（459 天还是 1 年）、续费价格；
5. 需要哪些材料、审核周期、是否电话回访/视频核验；
6. 证书 Subject 中的公司名/地址是否可与我们营业执照完全一致；
7. 是否支持 **SHA-256 + RFC3161 时间戳**。

### 2.3 预算参考

> 以下为 2026-10-25 抓取的公开页面价格，仅作预算参考；最终以合同/发票为准。

| 方案 | 公开价格 | 折合人民币/年（估算） | 说明 |
|---|---:|---:|---|
| **Certum OV 云签名（中国区商店）** | €329/年 | **约 ¥2,600–2,800** | 含云签名；5000 次/月；适合小团队/CI |
| **Sectigo OV（中国区官方）** | ¥5,190/年 | **¥5,190** | 含每年 FIPS 硬件 token；EV 同页 ¥5,880 |
| **DigiCert OV + KeyLocker** | $996/年 | **约 ¥7,000** | 云 HSM，1000 次签名/年；无需 token |
| **DigiCert OV HSM/自备 token** | $696/年 | **约 ¥5,000** | 自备 HSM/token；不含 token 硬件 |
| **DigiCert OV 官方 token 套餐** | $840/年 | **约 ¥6,000** | 含 token；不含 KeyLocker |
| **Azure Artifact Signing** | ≈$9.99/月 | 约 ¥850/年 | **Public Trust 不含中国大陆主体，不能用** |
| **Microsoft Store（MSIX）** | 免费 | ¥0 | 商店重新签名，无 SmartScreen；需上架/打包 |
| **EV** | $400+/年 或 ¥5,880/年 | ¥3,000–7,000 | **不再绕过 SmartScreen，不建议为它加钱** |
| 无签名/自签名 | 0 | 0 | 不适合对外分发 |

**实操预算建议：**

- 只做 OV + 云签名：**¥3,000–6,000/年**是合理区间；
- 推荐先拿 **Certum 中国区 OV 云签名** 和 **Sectigo 中国区 OV** 两家报价，再决定；
- 如果要做 CI 自动签名，优先云签名；USB token 只适合固定发布机；
- 证书有效期 459 天，按年续签；多签/续费价格另问；
- 不要把“买证书”当成“首次安装零警告”：首次仍可能有 SmartScreen，只是显示已验证发布者并开始积累声誉。

## 3. 申请材料清单（中国公司）

提前准备扫描件/照片，全部加盖公章或按服务商要求：

- [ ] 营业执照副本（最新、清晰、在有效期内）
- [ ] 法定代表人身份证/护照
- [ ] 经办人身份证 + **授权委托书**（盖公章）
- [ ] 企业信息表：中英文公司名、注册地址、实际办公地址、电话、邮箱、官网
- [ ] 官网/域名所有权证明（域名注册截图、网站底部公司名/备案信息）
- [ ] 对公电话/邮箱验证（能接听/收邮件）
- [ ] 发票信息（专票/普票、税号、开户行）
- [ ] 可能补充：邓白氏编码（DUNS）、企业信用报告、银行证明、视频核验
- [ ] 证书 Subject 确认：`CN=四川步络驿科技有限公司, O=..., C=CN`（以营业执照为准）

> 注意：公司名/地址/电话必须与公开记录一致；不一致会被 CA 打回。若官网/域名还没 ICP，先用现有可验证域名/企业邮箱，向服务商确认是否可替代。

## 4. 申请流程与时间

| 步骤 | 动作 | 预计 |
|---|---|---|
| 1 | 向 2–3 家服务商询价，确认 OV + 云签名 + 发票 | 1–2 天 |
| 2 | 提交订单、营业执照/法人/经办人/授权书/企业信息 | 1 天 |
| 3 | CA 组织验证、电话/邮箱/视频核验，补材料 | 3–7 个工作日（可能更长） |
| 4 | 签发证书、开通云 HSM/KeyLocker/eSigner | 1–2 天 |
| 5 | 在构建机接入签名，签 v1.2.2/v1.3.0 测试包 | 0.5–1 天 |
| 6 | 验证签名、时间戳、安装包属性；归档证书/续费提醒 | 0.5 天 |

**结论：现在（10/26 左右）启动，最快 11 月上旬拿到；赶不上 v1.2.2，目标 v1.3.0（12/15）签名。**

## 5. 签名落地（Windows）

### 5.1 签名对象

至少签：

- `dist\Sanitized_Data\Sanitized_Data.exe`
- `dist\Sanitized_Data\Sanitized_Data_MCP.exe`
- `dist_installer\Sanitized_Data_Setup_x.y.z_x64.exe`

绿色版 zip 本身不签，里面 exe 已签即可。第三方 DLL 不需要我们签。

### 5.2 命令示例（USB token/本地证书）

```powershell
# 先签主程序，再打包/编译安装包，最后签安装包
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a "dist\Sanitized_Data\Sanitized_Data.exe"
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a "dist\Sanitized_Data\Sanitized_Data_MCP.exe"
# 编译 Inno 安装包后
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a "dist_installer\Sanitized_Data_Setup_1.2.2_x64.exe"
signtool verify /pa /all "dist_installer\Sanitized_Data_Setup_1.2.2_x64.exe"
```

云签名（KeyLocker/eSigner/云 HSM）按服务商文档使用其 `signtool` 插件/凭据；私钥不出云，CI 只拿签名权限。

### 5.3 构建集成

- 增加 `desktop/sign.ps1`（或 build_win.ps1 可选步骤）：读取 `SIGN_CERT_*` 环境变量/云签名配置；
- 证书/密码/云 token **绝不入库**；发布机用受限账号和密钥管理；
- CI 可按「构建 → 签 exe → 打包 → 签 installer → verify」顺序；
- 每次发版记录签名证书 Subject/序列号/时间戳，便于支持排查。

## 6. 预期效果与边界

- 已签名文件首次下载仍可能被 SmartScreen 提示“无法识别的应用”，但会显示**已验证发布者名称**；
- 随着同一证书签名的下载量增加，信誉会积累；不要每次换证书/换主体；
- 若必须首次安装零警告：优先 **Microsoft Store（MSIX）**；否则接受首次提示，用文档/客服引导；
- EV 不再保证绕过 SmartScreen，不要为它支付溢价；
- 代码签名不替代杀软误报处理；如遇误报，走 Microsoft Security Intelligence 提交。

## 7. 我可以继续推进的事

1. 按本清单生成 **RFQ 询价邮件/表格**（中文，含材料清单）；
2. 证书到位后接入 `build_win.ps1`，对 v1.3.0 做签名 + 验证；
3. 评估 **Microsoft Store/MSIX** 作为“首次安装零 SmartScreen”的备选；
4. 建立证书续费提醒（459 天有效期）。

> 本文件不记录任何证书私钥/密码/token。

## 8. 询价邮件模板（可直接发）

主题：咨询 OV 代码签名证书（云 HSM，Windows 桌面软件）

正文：
> 您好，我们是四川步络驿科技有限公司，产品是 Windows 本地桌面软件「数据隐站」。
> 需要采购 **OV Code Signing 证书**，用于 Windows exe/Inno Setup 安装包签名，申请主体与营业执照一致。
> 请报价并说明：
> 1. 是否支持云 HSM/云签名（DigiCert KeyLocker、SSL.com eSigner、Sectigo 或其他），CI 如何接入；
> 2. 证书有效期、续费价格、是否含时间戳；
> 3. 需要提交的材料清单、审核周期、是否电话/视频核验；
> 4. 是否可开增值税专用发票；
> 5. 证书 Subject 中的公司名/地址能否与营业执照完全一致；
> 6. 若首次下载仍可能触发 SmartScreen，是否有配合的声誉/Store 建议。
> 请附产品页/报价单和合同条款，谢谢。
