# 数据隐站 · Sanitized_Data

> 文件交给 AI 之前，最后一道本地的门。

律师、医生、财务要把文件喂给豆包 / Kimi / WorkBuddy，但文件里有当事人姓名、身份证号、病历号、客户账号。
**数据隐站**在本机把这些内容换掉或涂黑，只把处理过的版本交出去。

- 全程本地：只监听 `127.0.0.1`，引擎不发起任何外呼
- 两个出口：**换名版**（可还原，给 AI 读）、**涂黑版**（不可还原，给人看）
- 出口自检：导出后对成品再跑一遍检测，原文还在就拒绝交付

## 看一眼

| 投放 | 对照台 |
|---|---|
| ![投放](docs/screen-1-drop.png) | ![对照台](docs/screen-2-console.png) |

左边是原件（红框标出识别到的敏感处），右边是可外发版，两栏同步滚动，右侧是隐去清单。
漏了的用「⌗ 框选补漏」补；按住黑条 300 毫秒可透视原值，松手复原。

| 涂黑形态 | 交付 |
|---|---|
| ![涂黑](docs/screen-3-black.png) | ![交付](docs/screen-5-export.png) |

Word / Excel / 文本走**流式排版**（不是页面截图），表格单元格也会按值替换：

![DOCX](docs/screen-4-docx.png)

---

## 快速开始

```powershell
# Windows
pip install -r app\server\requirements.txt
双击「启动.bat」          # 或 start.bat

# Linux / macOS
pip3 install -r app/server/requirements.txt
chmod +x start.sh && ./start.sh
```

浏览器会自动打开 `http://127.0.0.1:8760`（端口被占会顺延到 8840）。

**用法**：首页 →「打开控制台」→ 把文件拖进纸堆 → 等识别 → 左右对照 → 漏了的用「框选补漏」→ 交付。

启动后控制台会打印解析器、OCR 状态、数据目录和实际端口；`Ctrl+C` 退出，退出时自动清掉上传副本和预览中间件。

### 站点结构

| 路径 | 页面 | 说明 |
|---|---|---|
| `/` | 首页 | 介绍、入口、能力概览 |
| `/console` | 控制台 | 主工作区（投放 / 识别 / 对照 / 交付） |
| `/download` | 下载 | Windows / Linux 安装包、校验值、部署说明 |
| `/mcp` | MCP 接入 | 让 Claude Desktop / Cursor 等 Agent 自动脱敏 |

左上角的印章和「数据隐站」是回到首页的链接，控制台顶栏也有「首页 / 下载 / MCP 接入」导航。

### Linux 启动参数

```bash
./start.sh                            # 仅本机访问（默认 127.0.0.1:8760）
./start.sh --host 0.0.0.0             # 监听所有网卡（对外提供服务时用）
PORT=9000 ./start.sh                  # 指定端口
BIND_HOST=0.0.0.0 ./start.sh          # 也可用环境变量
```

---

## MCP 接入

`app/server/mcp_server.py` 是一个 stdio 的 MCP 服务，让支持 MCP 的 Agent
在把文件交给模型之前先调用本机脱敏。

| 工具 | 作用 |
|---|---|
| `sanitize_document` | 脱敏一个本地文件，返回可外发副本路径 |
| `redact_text` | 就地脱敏一段文本（适合发日志/报错前过一遍） |
| `check_support` | 检查格式是否支持 |
| `list_rules` | 列出规则包 |

Claude Desktop 的 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "sanitized-data": {
      "command": "python",
      "args": ["D:\\Sanitized_Data\\app\\server\\mcp_server.py"]
    }
  }
}
```

完整配置（Cursor、通用 stdio、验证步骤）见 `/mcp` 页面。

---

## 服务器部署

仓库里的 `deploy/` 带两个文件：

| 文件 | 用途 |
|---|---|
| `deploy/sanitized-data.service` | systemd 开机自启 |
| `deploy/nginx.conf` | HTTPS 反向代理示例 |

```bash
sudo cp deploy/sanitized-data.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sanitized-data
```

**反向代理必须透传真实域名**，否则服务端的同源校验会把正常请求判成跨站：

```nginx
proxy_set_header Host              $http_host;
proxy_set_header X-Forwarded-Host  $host;
client_max_body_size 512m;
```

服务端做的是「主机名比较」（忽略协议和端口），并优先信任 `X-Forwarded-Host`，
所以 `https://cka.blctek.com:8443` → 后端 `cka.blctek.com` 这种组合是能通过的。
如果还有问题，可以用环境变量显式放行：

```bash
YINZHAN_ALLOWED_ORIGINS="https://cka.blctek.com:8443" ./start.sh
```

---

## 支持的文件

| 格式 | 解析 | 涂黑 | 换名 | 说明 |
|---|---|---|---|---|
| PDF（有文本层） | ✓ | ✓ 真删除 | ✓ | `apply_redactions` 重写内容流 |
| PDF（扫描件） | ✓ OCR | ✓ | ✓ | 无文本层自动转 OCR |
| Word `.docx` | ✓ | ✓ | ✓ | 清修订/批注/作者 |
| Excel `.xlsx` | ✓ | ✓ | ✓ | 清隐藏表/定义名/批注 |
| 图片 `.png/.jpg` | ✓ OCR | ✓ | ✓ | 重编码，剥离 EXIF/GPS |
| TIF 正射影像 | ✓ | ✓ | ✓ | 剥离地理标签，坐标可中性化 |
| 纯文本 `.txt/.md/.csv/.log` | ✓ | ✓ | ✓ | |
| 三维 `.obj/.ply/.glb/.stl` | ✓ | 部分 | — | 去工程原点、清注释 |

### OCR 读不出来的东西

扫描合同里最要命的三样东西，文字识别是抓不到的，靠图像特征单独处理：

| 类型 | 怎么找 | 处理方式 |
|---|---|---|
| **红色公章 / 私章** | 印泥是彩色的、扫描字是灰的：算 `R-(G+B)/2`，块级聚合后按印章尺寸过滤 | 直接涂黑（19/19 检出） |
| **落款签署区** | 页面上出现「单位名称 / 盖章 / 代表人 / 签字」等短标签，且不少于两条 | 整块涂黑，含公司名、印章、签名、日期 |
| **手写签名** | 抠掉 OCR 已识别的文字框，剩下的深色墨迹团块 | 标为「手写签名」，待确认 |
| **手写批注/编号** | OCR 置信度 < 0.85 的短行 | 标为「疑似手写」，待确认 |

后两类默认进「待确认」，因为墨迹判断可能误报；印章和签署区默认直接处理。


---

## 两种导出，别选错

| | 给 AI 读 | 给人看 |
|---|---|---|
| 样子 | 张三 → 陈志远 | ██████ |
| 能不能还原 | 能（映射表加密存在本机） | 不能 |
| 用在哪 | 豆包、Kimi、WorkBuddy | 发给对家、法院、保司 |
| 文件位置 | `out/xxx_换名_日期-序号.pdf` | `out/xxx_已隐去_日期-序号.pdf` |

**涂黑不是画黑框。** 导出时会真正删除内容流里的文字，再用 `PyMuPDF` 重写整页 —— 复制粘贴拿不到原文。

---

## 规则包

`app/server/yinzhan/rules/*.yaml` 里放规则，启动时自动加载，**改完不用重启**（按文件 mtime 热更新）。

```yaml
pack: legal-cn
name: 法律 · 民事诉讼
rules:
  - id: cn.idcard
    entity: ID_CARD
    label: 身份证
    level: L3
    scope: [pdf, docx, xlsx, text, image, tiff]
    detect:
      - {type: regex, pattern: '(?<!\d)[1-9]\d{5}(19|20)\d{2}...', validate: cn_id_checksum}
    replace: {strategy: mask, keep: [head6, tail4]}
```

新增一个实体类型：

1. 在任意 `*.yaml` 里加一条规则
2. 如果要用新的检测方式，在 `detectors/` 里加一个类并 `register_detector`
3. 如果要新的替换方式，在 `replacers/` 里加一个函数并 `register_replacer`

管线、界面、导出都不用改。

---

## 架构

```
浏览器控制台  ──同源 HTTP──▶  本地引擎 (Python)
                                │
                     Parse → Detect → Plan → Apply → Verify
                                │
                     ┌──────────┴──────────┐
                  解析器插件            规则包 YAML
                  (parsers/)            (rules/)
                                │
                     加密映射库 / 审计链 / 出口自检
```

- 界面和引擎在同一个进程里，但**文件永不经过浏览器**：浏览器只拿渲染好的页面图和实体坐标。
- 引擎没有外呼代码；上传只写本机临时目录，任务结束即删。
- 审计只记「类型 + 数量」，不记原值 —— 审计日志本身不能变成泄露源。

---

## 安全边界（要说清楚）

**能保证**

- 处理过程不发一个网络包
- 导出是破坏性重写，不是盖黑框
- 元数据（作者、设备、GPS、地理参考）会被剥离
- 审计记录带 HMAC 链，改一条后续全废

**不能保证**

- 同机恶意软件、内存抓取、屏幕录制
- 「去标识化」不等于「匿名化」。国标语境下去标识化数据仍属个人信息，本产品只说「已隐去」，不说「已合规」
- 地理数据只能「降密」：坐标移了，河流道路的形状还在

---

## 自检

```powershell
# 生成样例
python app\tests\make_samples.py

# 端到端：解析 → 检测 → 导出 → 复检
cd app\server
python ..\tests\e2e.py
```

最近一次自检结果（5 个样例，全部通过）：

| 文件 | 格式 | 识别 | 换名导出 | 涂黑导出 |
|---|---|---|---|---|
| 委托代理合同.pdf | pdf · 2 页 | 15 处 | 残留 0 | 残留 0 |
| 住院病历.docx | docx | 9 处 | 残留 0 | 残留 0 |
| 对账单.xlsx | xlsx | 13 处 | 残留 0 | 残留 0 |
| 名片.png | image（OCR） | 6 处 | 残留 0 | 残留 0 |
| 正射影像.tif | tiff | 3 处 | 残留 0 | 残留 0 · 地理标签已剥离 |

界面也在真实浏览器里跑通了完整链路：拖入 → 识别 → 对照 → 换名/涂黑切换 → 交付 → 成品落盘并自检。

---

## 已知限制

| 限制 | 说明 |
|---|---|
| 人名/机构识别靠启发式 | 没有内置大模型，宁少报不误报；漏掉的用框选补 |
| 三维只做有限处理 | OBJ/PLY 会重定位顶点、清注释；STL/GLB 目前只透传 + 告警 |
| TIF 坐标中性化还没做随机平移 | 当前版本只剥离地理标签和元数据，坐标变换在路线图上 |
| 扫描件 OCR 精度 | 清晰扫描件够用，手写体和低分辨率照片会漏 |
| XLSX 宏 | `.xlsm` 导出后不保留宏（会告警） |
| 无安装包 | 当前是源码 + Python 运行；PyInstaller 打包在路线图上 |

---

## 目录

```
Sanitized_Data/
├─ 启动.bat / start.bat     Windows 双击启动
├─ start.sh                 Linux / macOS 启动
├─ deploy/
│  ├─ sanitized-data.service  systemd 单元
│  └─ nginx.conf              HTTPS 反代示例
├─ app/
│  ├─ server/               引擎
│  │  ├─ CONTRACT.md        模块契约（改代码先看这个）
│  │  ├─ run.py             入口
│  │  ├─ mcp_server.py      MCP 服务（stdio）
│  │  └─ yinzhan/
│  │     ├─ udm.py          统一文档模型
│  │     ├─ pipeline.py     五段管线
│  │     ├─ jobs.py         任务状态机
│  │     ├─ api.py          HTTP 接口
│  │     ├─ parsers/        格式插件
│  │     ├─ detectors/      检测插件
│  │     ├─ replacers/      替换插件
│  │     └─ rules/          规则包
│  ├─ web/                  站点（离线，无 CDN）
│  │  ├─ home.html          首页
│  │  ├─ index.html         控制台
│  │  ├─ download.html      下载页
│  │  ├─ mcp.html           MCP 接入页
│  │  └─ downloads/         安装包（Windows zip / Linux tar.gz）
│  └─ tests/                自检脚本
├─ samples/                 测试文件
├─ out/                     导出的成品
└─ data/                    运行时数据（密钥、映射、审计库）
```

---

## 常见问题

**为什么 OCR 慢？**
首次识别要加载模型（约 2 秒），之后常驻内存。扫描件多的文件会明显慢于文本层 PDF。

**换名之后模型答错了怎么办？**
换名版是可还原的。`data/vault/` 里存着加密映射，把模型回答存成文本后：

```powershell
python app\server\rehydrate.py --list                      # 看有哪些换名版
python app\server\rehydrate.py --job j_ab12cd34 回答.txt    # 还原成 回答_还原.txt
```

**能不能不装 Python？**
可以打包成单文件 exe（PyInstaller），但体积会到 300MB+（OCR 模型占大头）。当前版本先保证源码可跑与可维护。

**端口被占了？**
自动从 8760 往后找，实际端口写在 `data/runtime.json`。

**部署到服务器后，上传文件报「跨站请求被拒绝」？**
这是服务端的防 CSRF 校验：它比对浏览器发来的 `Origin` 和请求的 `Host`。
反向代理把 `Host` 改写掉之后两者对不上，就会被拦。

服务端现在的判定逻辑是「只比主机名，忽略协议和端口」，并优先看 `X-Forwarded-Host`，
所以常见的三种情况都能过：

| 场景 | 结果 |
|---|---|
| 直连 `http://127.0.0.1:8760` | 通过 |
| 反代 `Host $host`（端口被剥掉） | 通过 |
| 反代 `Host` 被改写，但带 `X-Forwarded-Host` | 通过 |
| 真正的跨站请求 | 拦截并写入 `data/server.log` |

还不行就显式放行：

```bash
YINZHAN_ALLOWED_ORIGINS="https://cka.blctek.com:8443" ./start.sh
```

对应的 nginx 配置必须包含：

```nginx
proxy_set_header Host              $http_host;
proxy_set_header X-Forwarded-Host  $host;
client_max_body_size 512m;
```

**对外提供服务怎么启动？**
`./start.sh --host 127.0.0.1`（只监听本机，由 nginx 提供 HTTPS）。
不要把引擎直接绑 `0.0.0.0` 暴露到公网——它没有账号体系。

**扫描件为什么慢？能中途停吗？**
扫描件没有文本层，只能一页一页跑离线 OCR。当前默认用 **PP-OCRv6 small**，
现场实测（6 核 CPU、A4 扫描页、42 页测试集）**约 2.1 秒/页**，整套 87 秒。
界面上会显示「正在识别第 3/17 页（扫描件）· 约剩 1 分钟」，**随时可以点「换一个文件」中断**，
后端会立刻停止当前页之后的处理。

几个可调项：

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `YINZHAN_OCR_MAX_SIDE` | 1600 | OCR 输入最长边。调小更快，但小字可能丢 |
| `YINZHAN_OCR_DESKEW` | 0 | 纠偏（实测对 PP-OCR 无提升，默认关） |
| `YINZHAN_OCR_CLAHE` | 1 | 低对比度页自动增强 |

**想再快/再准还能做什么？**
- 快：换 `rapidocr-openvino` 走 OpenVINO（Intel CPU 才有效，AMD 无收益）
- 准：把 `rec_small` 换成 server 版模型（体积 80MB+，速度慢 3–5 倍）
- 手写：PP-OCR 系列对连笔手写无能为力，目前靠"墨迹检测 + 人工确认"兜底

**浏览器报 WinError 10054？**
那是浏览器先断开（刷新、关页、等不及）造成的，不是服务出错。已静音处理，
真正的服务端异常会写到 `data/server.log`。
