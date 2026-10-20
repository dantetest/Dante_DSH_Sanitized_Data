# raw-C · 海外同类产品定价对标（数据隐站 / Sanitized_Data）

> 调研员 C ｜ 采集日期：2026-10-20 ｜ 用途：给「数据隐站」定价决策提供海外市场依据
> 汇率口径（2026-10-20，exchangerate-api.com 实时表）：**1 USD ≈ 6.69 CNY**，**1 CHF ≈ 8.10 CNY**（由 USD/CHF=0.8264 折算）。
> 所有价格均为公开页面抓取快照，不含税、不含中国区渠道费。**未公开的价格一律标注「未公开」并给出推断依据，不虚构数字。**

## 0. 一句话结论

- **个人桌面工具带「涂黑」能力的天花板：¥500–¥1,500/年**（Adobe Pro ¥134/月、PDFelement ¥535/年、PDF Expert ¥535/年、Foxit ¥870–1,071/年，买断 ¥870–1,405）。涂黑只是 Pro 档里的一个功能，**没有单独溢价**。
- **「本地处理、无外呼」在海外桌面市场不是加价点，是标配**：Redact.dev 明确标注 "All processing happens on your device" 仍只卖 $7.99/月。
- **云端按量 PII 检测已便宜到接近免费**：GCP ¥20/GiB、AWS ¥6.7/百万字符、Azure ¥6.7/千条记录。100 页合同（约 50 万字符）用 Comprehend 检测不到 ¥4。
- **企业级静态脱敏与个人工具之间隔着 3–4 个数量级**：企业产品按 core/TB/席位/数据主体计价，普遍不公开价格；K2view 生产环境起步 ¥5.35 万/月、Redgate 经销商 100 用户 24 个月约 ¥132 万。
- **2025–2026 行业大整合**（Gretel→NVIDIA、Mostly AI→Syntho、Prompt Security→SentinelOne、Private AI→Limina、Delphix→Perforce），独立单品窗口期有限。

---

## 1. 企业级静态数据脱敏 / 隐私工程（11 条）

| 产品/厂商 | 类别 | 形态 | 计价单位与价格 | 折算人民币参考(1 USD≈6.69) | 来源URL | 采集日期 | 可信度 |
|---|---|---|---|---|---|---|---|
| Informatica Persistent Data Masking | 企业静态数据脱敏 | 本地/私有云软件 | **未公开**官方价目（官网只有 datasheet）。PeerSpot 多位用户口径：按 **CPU 核数**计费，超出额度另收；用户评价"价格偏高、按合同周期买断" | 推断：按 core 计费、年费 10 万美元级（≈¥67 万/年）——依据：PeerSpot 用户评述「charges based on the number of cores」+ 企业 TDM 惯例，仅供量级参考 | [PeerSpot 定价页](https://www.peerspot.com/products/informatica-test-data-management-tdm-pricing) | 2026-10-20 | 中（计费口径）/低（金额） |
| Delphix（Perforce）Continuous Compliance / Masking | 企业静态数据脱敏 | 本地设备/云 | **未公开**。官方支持门户的许可证页以 **Per TB** 为计价单元（页面需登录，价格不对外） | 推断：按 TB 年费、5–6 位数美元/年——依据：官方「Per TB」单元 + 企业 TDM 年费区间，量级推断 | [Delphix 支持门户 Per TB 页](<https://support.delphix.com/Versions/Continuous_Compliance_(Per_TB)>) | 2026-10-20 | 低 |
| IBM InfoSphere Optim Data Masking | 企业静态数据脱敏 | 本地 | **未公开**美元单价；IBM 公开价目以 **PVU（Processor Value Unit）**许可，实际价格随 GSA / Passport Advantage 合同 | 推断：按 PVU 折算、年费 10 万美元级（≈¥67 万/年）——依据：IBM 价目册 PVU SKU + 大型机/数据库脱敏惯例 | [IBM 价目册（含 Optim PVU SKU）](https://www.ibm.com/downloads/cas/YG5O1AO6) | 2026-10-20 | 低 |
| Redgate Data Masker for SQL Server | 企业测试数据脱敏 | 本地 Windows | 官方无美元标价；瑞士经销商 SparkDynamic 明码：**24 个月新订阅 100 用户 CHF 162,845.90**（合 CHF 1,628.46/用户/24 个月） | **≈¥1,319,000/100 用户/24 个月**；≈**¥6,595/用户/年**（1 CHF≈8.10） | [SparkDynamic 经销商价目](https://sparkdynamic.com/en/shop/product/data-masker-for-sql-server?vid=7334c364-45bf-49af-8128-20b3f227c42a) ｜ [官方产品页](https://www.red-gate.com/products/data-masker/) | 2026-10-20 | 中（经销商明码） |
| Tonic Structural | 企业结构化脱敏 | SaaS；自托管仅 Enterprise | **未公开**。官方：Professional 上限「10TB 源数据 / 10 用户 / 2 类数据源」，按连接的源数据 GB 年费递减；自托管仅 Enterprise 版 | 推断：中型（≤10TB）年费 5–6 位数美元（≈¥33 万–¥数百万元/年）——依据：官方按源 TB 阶梯定价 + 自托管限企业版 | [Tonic 定价页](https://www.tonic.ai/pricing) | 2026-10-20 | 中（结构）/低（金额） |
| Tonic Textual | 非结构化文档脱敏（TXT/DOCX/PDF/TIFF 等） | SaaS / 自托管 | **未公开**具体单价；官方明确「按处理词数（per 1,000 words）阶梯计价、量越大单价越低，需询价」 | 未公开、不折算；推断：低于云 API 的按字符价 × 本地化溢价，量级 $0.x–$x/1,000 词（依据：官方按词计价说明，无公开数字） | [Tonic 定价页](https://www.tonic.ai/pricing) | 2026-10-20 | 中 |
| Tonic Fabricate（对照：轻量自助档） | 合成数据（AI 生成） | SaaS | **Free $0**（含 $5 额度/月）；**Plus $29/月**（含 $25 额度，超额按量） | Free ¥0；Plus **≈¥194/月** | [Tonic 定价页](https://www.tonic.ai/pricing) | 2026-10-20 | 高（官方明码） |
| K2view Data Product Platform | 数据虚拟化 + 脱敏 | 云 SaaS；本地需报价 | 官方明码：微库**写 $4/10 万次**、**读 $2/10 万次**、存储 **$0.50/GB/月**、数据传出 $0.11/GB/月；开发环境 $100/月起，**生产环境 $8K/月起**；本地/混合部署需单独报价 | 写 **≈¥27/10 万次**、读 **≈¥13/10 万次**、存储 **≈¥3.35/GB/月**、生产 **≈¥5.35 万/月** | [K2view 定价页](https://www.k2view.com/platform/pricing) | 2026-10-20 | 高（官方明码） |
| Gretel.ai | 合成数据 / 隐私工程 | SaaS（已停独立售卖） | **未公开**；pricing 页已 404，2025-03 被 NVIDIA 收购 | 不折算（原独立订阅已不可购） | [Seedtable: Gretel acquired by NVIDIA](https://seedtable.com/exits/gretel-labs-inc) ｜ [gretel.ai/pricing（404）](https://gretel.ai/pricing) | 2026-10-20 | 中 |
| MOSTLY AI | 合成数据 | SaaS | **未公开**；pricing 页 404，官方站点声明品牌/资产已被 Syntho 收购（2025-12） | 不折算（独立订阅已不可购） | [mostly.ai/pricing（404 + 收购声明）](https://mostly.ai/pricing) | 2026-10-20 | 中 |
| DATPROF | 测试数据脱敏 | 本地 | **未公开**；2026-10 核查发现 dataprof.com 域名已挂牌出售、原官网停止运营 | 不折算 | [dataprof.com（域名待售）](https://www.dataprof.com/) | 2026-10-20 | 中 |

---

## 2. 云 PII 检测 / 脱敏按量 API（8 条）

| 产品/厂商 | 类别 | 形态 | 计价单位与价格 | 折算人民币参考(1 USD≈6.69) | 来源URL | 采集日期 | 可信度 |
|---|---|---|---|---|---|---|---|
| AWS Comprehend · Detect PII | 云 PII 检测/脱敏 | REST API | **$0.0001 / 100 字符单元**（每次请求最低 3 单元=300 字符）；≥1,000 万单元 $0.00005、≥5,000 万 $0.000025；Contains PII $0.000002/单元 | **≈¥6.69/百万字符**；短文本受 300 字符下限影响实际约 ¥13.4/百万字符 | [AWS Comprehend 定价](https://aws.amazon.com/comprehend/pricing/) | 2026-10-20 | 高 |
| Google Cloud Sensitive Data Protection（原 DLP） | 云 PII 检测/脱敏 | REST API / 存储扫描 | 内容检查 **$3.00/GiB**（1 GiB–1 TiB 档），内容转换 **$2.00/GiB**，每请求最低 1 KiB；存储检查 $1.00/GiB·月；数据发现 $0.03/GB；首 1 GiB 免费 | 检查 **≈¥20.1/GiB**；转换 **≈¥13.4/GiB**；发现 **≈¥0.20/GB** | [GCP Sensitive Data Protection 定价](https://cloud.google.com/sensitive-data-protection/pricing) | 2026-10-20 | 高 |
| Azure AI Language · Text PII | 云 PII 检测/脱敏 | REST API | **$1.00 / 1,000 条文本记录**（每条 ≤1,000 字符）；量价 $0.75（>50 万）、$0.30（>250 万）、$0.25（>1,000 万）；**每条消息至少按 1 条记录计**；Document PII redaction 按页另计 | **≈¥6.69/千条**（150 字符短消息因按 1,000 字符计，实际约 ¥45/百万字符） | [Azure 语言服务定价](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/language-service/) ｜ [第三方从 Azure Retail Prices API 读数](https://www.beri.net/article/pii-redaction-pricing-comprehend-azure-google-presidio-cost-per-million-characters) | 2026-10-20 | 高（官方页 + 第三方读数交叉核对） |
| AWS Bedrock Guardrails · 敏感信息过滤器 | AI 网关内置 PII 过滤 | 托管 API | **$0.10 / 1,000 文本单元**（每单元 ≤1,000 字符），纯正则过滤器免费 | **≈¥0.67/千条** | [AWS Bedrock 定价](https://aws.amazon.com/bedrock/pricing/) ｜ [第三方比价](https://www.beri.net/article/pii-redaction-pricing-comprehend-azure-google-presidio-cost-per-million-characters) | 2026-10-20 | 中（第三方读数） |
| Microsoft Presidio（开源） | 本地 PII 检测（可离线） | 自托管开源（MIT） | **$0 许可费**；成本 = 自备算力 + 工程调优（社区维护） | **¥0 许可**；仅为服务器/人力成本 | [microsoft/presidio](https://github.com/microsoft/presidio) | 2026-10-20 | 高 |
| Skyflow | 隐私保险库 / 数据脱敏 | SaaS / 私有部署 | **未公开**。第三方拆解：按 **data subjects（数据主体数）+ 区域数 + 功能（PII/PCI/PHI/分析）** 报价，无公开价目表 | 推断：年费 5–6 位数美元（≈¥33 万起/年）——依据：无公开价、按主体规模阶梯 + 区域/合规功能加价 | [Skyflow 官网](https://www.skyflow.com/) ｜ [第三方定价拆解](https://codeables.dev/article/skyflow-pricing-how-is-cost-calculated-by-number-of-data-subjects) | 2026-10-20 | 中（结构）/低（金额） |
| Private AI（现更名 Limina） | 本地/私有容器脱敏 API | 私有容器 / 云 / Marketplace | **未公开**。**Free：75 次 API/日、10 次/分**（txt/json/csv 250KB，其他 4MB）；Volume/Custom 需报价；官方强调可部署在本地/ VPC，数据不出环境 | 未公开、不折算；推断：企业承诺量年费 5 位数美元起（依据：本地化部署 + 合规场景，具体需询价） | [Limina / Private AI 定价](https://www.private-ai.com/pricing/) ｜ [批量处理报价页](https://www.private-ai.com/en/batch) | 2026-10-20 | 中（结构）/低（金额） |
| Nightfall AI | DLP / AI 网关（含 MCP、Claude/Cursor 钩子） | SaaS + 端点代理 | **未公开**：按 **user/year** 计费，Foundation / Premier 两档，页面价格数字未渲染，最终按用户数与数据量报价；每用户含 2 台设备、150GB 存量扫描 | 推断：同类 DLP 约 $30–$100/用户/年（≈¥200–¥670）——依据：其按用户年的计量方式 + 行业惯例，量级推断 | [Nightfall 定价](https://www.nightfall.ai/pricing) | 2026-10-20 | 中（结构）/低（金额） |

---

## 3. 桌面 / 个人脱敏与 PDF 工具（5 条，均已明码）

| 产品/厂商 | 类别 | 形态 | 计价单位与价格 | 折算人民币参考(1 USD≈6.69) | 来源URL | 采集日期 | 可信度 |
|---|---|---|---|---|---|---|---|
| Redact.dev | 个人隐私清理（社媒删除/编辑），**本地处理** | Win / Mac / iOS / Android | Free $0；**Premium $7.99/月，年付 $95.88**；**Ultimate $14.99/月，年付 $179.88**；企业/项目另计 | Premium **≈¥53/月、¥642/年**；Ultimate **≈¥100/月、¥1,204/年** | [Redact.dev 定价](https://redact.dev/pricing) | 2026-10-20 | 高 |
| Adobe Acrobat Pro（含 Redact 涂黑） | PDF 编辑 + 涂黑 | Win / Mac / Web | **$19.99/月**（年付月缴）；Acrobat Standard $14.99/月；Acrobat Studio（含 AI）$24.99/月；**无买断版**（2020 后停售永久授权） | **≈¥134/月**；Standard ≈¥100/月；Studio ≈¥167/月 | [Adobe Acrobat 定价](https://www.adobe.com/acrobat/pricing.html) | 2026-10-20 | 高 |
| Wondershare PDFelement | PDF 编辑（含涂黑） | Win / Mac | **年付 $79.99**（原价 $89.99）；2 年 $109.99；**买断 $129.99**（PDFelement 13 永久） | **≈¥535/年**；≈¥736/2 年；**≈¥870 买断** | [PDFelement 官方商店](https://pdf.wondershare.com/store/windows-individuals.html) | 2026-10-20 | 高 |
| PDF Expert（Readdle） | PDF 编辑 / OCR | Mac / iOS | **年付 $79.99**；**买断 $139.99**（仅 Mac） | **≈¥535/年**；**≈¥937 买断** | [PDF Expert 定价](https://pdfexpert.com/pricing) | 2026-10-20 | 高 |
| Foxit PDF Editor / Editor+（含 AI Smart Redact） | PDF 编辑 + 智能脱敏 | Win / Mac / Web | **PDF Editor $129.99/用户/年**；**PDF Editor+ $159.99/用户/年**（含 AI Smart Redact）；买断版 PDF Editor 14 **$209.99**；30 席以上最高 30% 折扣 | **≈¥870/年**；**≈¥1,071/年**；**≈¥1,405 买断** | [Foxit PDF Editor 定价](https://www.foxit.com/pdf-editor/pricing/) | 2026-10-20 | 高 |

---

## 4. AI 时代本地隐私网关 / 提示词脱敏（2 条）

| 产品/厂商 | 类别 | 形态 | 计价单位与价格 | 折算人民币参考(1 USD≈6.69) | 来源URL | 采集日期 | 可信度 |
|---|---|---|---|---|---|---|---|
| Prompt Security（SentinelOne） | AI 使用网关 / 提示词防护（含 DLP、影子 AI） | 网关 / 端点 | **未公开**；站点已并入 SentinelOne 品牌，无独立定价页（/pricing 404），随 SentinelOne 企业合同销售 | 推断：并入 SentinelOne 企业合同后按端点/席位打包（依据：收购后停售独立 SKU，无公开价） | [prompt.security](https://prompt.security/) | 2026-10-20 | 中 |
| Nightfall Premier Edition（AI agent / MCP 覆盖） | AI 网关 + 端点 DLP | SaaS + 端点 | 同 Nightfall：**按 user/year**，Premier 覆盖 Cursor / Claude Code / VS Code / MCP 网关，价格需询价；**未公开** | 推断同 Nightfall：$30–$100/用户/年（≈¥200–¥670） | [Nightfall 定价](https://www.nightfall.ai/pricing) | 2026-10-20 | 中（结构）/低（金额） |

> 合计 **26 条**，其中 13 条带明确公开数字、13 条为「未公开 + 推断/结构」。

### 附：PII 检测精度基准（影响「出口自检」的对外表述）

第三方评测（Tonic 2025-10 在客服通话转写上的逐词打分，来源：[beri.net 汇总](https://www.beri.net/article/pii-redaction-pricing-comprehend-azure-google-presidio-cost-per-million-characters)）：

| 工具 | Precision | Recall | F1 |
|---|---|---|---|
| AWS Comprehend | 0.83 | 0.90 | 0.86 |
| Google Cloud DLP | 0.57 | 0.52 | 0.54 |
| Azure AI Language | 0.43 | 0.65 | 0.52 |
| Microsoft Presidio | 0.40 | 0.46 | 0.43 |

注意：这是**厂商自测且有利益冲突**（Tonic 卖竞品检测器），只能当方向性参考；产品对外不宜承诺"100% 检出"，与数据隐站 README 的安全边界表述一致。

---

## 5. 对 Sanitized_Data 的启示（10 条）

1. **个人版年费天花板 ≈ ¥500–¥1,500，涂黑能力本身不值溢价。** 海外桌面 PDF 工具的 Pro 档（含 redaction）是 ¥535–¥1,071/年、买断 ¥870–¥1,405；而单独卖"脱敏"的 Redact.dev 只要 ¥53/月。→ 数据隐站个人版定 **¥199–¥499/年**有充分海外依据；定到 ¥700 以上必须捆绑企业/合规叙事，否则会被用户拿 PDFelement/Foxit 比价。
2. **「本地处理、无外呼」不是涨价理由，是入场券。** Redact.dev 在付费页明写 "All processing happens on your device" 却只收 $7.99/月，PDF 工具全部本地运行。→ 建议把"全程本地"用作信任与差异化叙事，收费点放在**批量、可还原映射、MCP 接入、审计链、专用规则包**。
3. **按量云 API 已把"检测"打到白菜价，别按量卖个人版。** GCP 检查 ¥20/GiB、AWS ¥6.7/百万字符、Azure ¥6.7/千条记录；100 页合同约 50 万字符，Comprehend 检测成本 <¥4。→ 个人版走**订阅制**而非按页/按文件计费，用户对"份数"敏感、对"年费"接受度更高。
4. **企业级与个人级之间是 3–4 个数量级的鸿沟，中间带是空的。** 企业产品按 core/TB/席位/数据主体计价，普遍不公开；K2view 生产环境起步 ¥5.35 万/月，Redgate 经销商 100 用户 24 个月 ~¥132 万，而个人 PDF 工具 ¥535/年。→ 国内定价可做"个人 ¥199–499/年 + 团队 ¥500–2,000/人/年 + 私有化部署 5–20 万/年"三档，避开海外企业产品的正面价格带。
5. **本地化部署在海外是"企业版特权"，这给了 To B 定价空间。** Tonic 自托管只在 Enterprise、K2view 本地需报价、Limina 的 hybrid/regional 部署属 Custom。→ 数据隐站对律所/医院/财务团队的"内网/离线"卖点可以支撑 **¥500–2,000/席位/年**，但个人版不要拿它加价。
6. **行业整合期，独立单品要尽快绑定垂直场景。** 2025–2026：Gretel→NVIDIA、Mostly AI→Syntho、Prompt Security→SentinelOne、Private AI→Limina、Delphix→Perforce。→ 通用隐私平台会被并购/淘汰，数据隐站应把"法律卷宗/病历/对账单/测绘成果"做成开箱模板与行业话术，形成迁移成本。
7. **AI 时代的隐私计价正在转向"按主体/按人"，不是按文件。** Skyflow 按 data subjects、Nightfall/Prompt Security 按 user/year、K2view 按微库读写。→ 若做订阅，优先**席位年费**；"按文件/按页"对律师医生不可预测，容易压低客单。
8. **免费额度是海外获客标准动作。** Limina 免费 75 次 API/日、Comprehend 首年每月 150 万字符免费、Tonic Fabricate 每月 $5 额度。→ 建议"每月 3–5 份免费 + 水印/限制格式"，付费点设在批量、还原、MCP 与审计导出。
9. **渠道与税会吃掉 30%，定价先算到手价。** 上述价格均不含税；若走微信/App Store 等渠道，分成后个人版 ¥199 实收约 ¥139。→ 官网直购/授权码优先，渠道价与直售价分层设计。
10. **精度是不能省的差异化，但宣传要克制。** 第三方基准显示云端 PII 检测 F1 仅 0.43–0.86，中文与非结构化文档更差；数据隐站的"框选补漏 + 出口复检"是真正差异点。→ 对外只说"已隐去/残留复检"，避免"100% 检出"或"已合规"表述（与 README 安全边界一致）。

---

## 6. 数据缺口与后续建议

1. **企业级厂商无公开美元价**：Informatica、Delphix、IBM Optim、Skyflow、Nightfall、Prompt Security、DATPROF 均需询价；表中金额均为量级推断（已标注低可信度），**不可用于对外报价引用**。
2. **Delphix 的 per-TB 单价拿不到**：官方支持门户页需登录（HTTP 401/验证码），只能确认「Per TB」计价单元；如需精确对标，建议通过 Perforce 代理商或 GSA Advantage 询价。
3. **IBM 价目册只确认了 PVU 计价单元**：`ibm.com/downloads/cas/YG5O1AO6` 为大型 PDF 价目册，本次未逐条提取具体美元价；如需精确数字可让有 GSA 合同经验的人核对。
4. **Tonic Textual 的 per-1,000-word 单价、Limina 批量单价、Redgate 官方美元价**均需销售询价；经销商价（CHF）只代表瑞士市场。
5. **iMyFone 没有找到"文档脱敏/涂黑"专门产品**：其 PDF 线是 PDFacademy（编辑/转换，含免费档）与 Passper（解锁），非脱敏竞品，故未单列。
6. **区域差异**：以上为美国/欧洲公开价；中国区同类产品价格由国内团队另行调研（见 raw-B 等文件），本文件只做海外锚点。
7. **时间敏感性**：所有价格为 2026-10-20 抓取快照，厂商随时调价；引用时请连同 URL 与日期一起标注。

## 7. 方法与可信度说明

- 检索：`web_search` 找 pricing 页/经销商/媒体报价；`web_fetch` 核对原文。
- 对 JS 渲染、AWS/GCP/Azure 等"抓不到数字"的页面，使用 **r.jina.ai 只读渲染代理** 取正文；Azure 在页面上以 `$-` 占位（脚本渲染）时，采用第三方 2026-10-13 从 **Azure Retail Prices API** 的读数交叉验证（beri.net），并注明。
- 可信度定义：**高** = 官方定价页原文数字；**中** = 官方文档/经销商明码/第三方交叉核对；**低** = 媒体、用户评述或量级推断。
- 汇率：exchangerate-api.com `https://open.er-api.com/v6/latest/USD`（2026-10-20 00:02 UTC 更新）。
