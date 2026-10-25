# W3 授权 UI / 价格页 / 状态条（2026-10-25）

> 范围：P1 W3 本地 UI。只调用同源 `/api/license/*`，不加载外部字体/脚本/图片；
> 购买按钮只打开系统浏览器，不在本机页 fetch 公网。

## 1. 已完成

### `/license` 授权档案卡

文件：`app/web/license.html`、`license.css`、`license.js`

- 状态徽章：免费 / 试用 / 付费 / 宽限
- 版本、到期、本机识别码展示 + 复制
- 今日额度展示（免费版 `剩余 X/3`，试用/付费不限）
- 14 天试用启用按钮（仅 free 且未用过）
- 导入授权：
  - 拖拽/点击选择 `license.json`
  - 粘贴授权文本
  - 导入成功显示「已核验」章
- 备份当前授权：下载 `license.json`
- 本机授权事件列表（只显示类型/时间/授权号）
- 常见问题、合规声明

### 控制台状态条 + 拦截弹窗

文件：`app/web/index.html`、`app/web/app.js`、`app/web/style.css`

- 顶栏新增「价格」导航和授权状态 chip：
  - `免费版 · 今日剩余 2/3`
  - `试用 · 剩 9 天`
  - `个人版 · 2027-10-20 到期`
- chip 点击进入 `/license`
- 免费额度/页数/格式/规则包/授权错误触发弹窗：
  - `升级 / 查看价格`
  - `导入授权`
  - `知道了`
- 不遮挡文件对照区，不影响导出；导出仍永不 gate。

### `/pricing` 价格页

文件：`app/web/pricing.html`、`pricing.css`、`pricing.js`

- 免费 / 个人 / 专业 / 团队 / 机构 五档
- 静态价格：个人 ¥199/年、早鸟 ¥99、专业 ¥499/年、团队 ¥1,999/年/5 席、机构 ¥3.88 万起
- **参考价，以官网为准**
- 购买按钮：
  - 配置了 `YINZHAN_PURCHASE_URL` 时打开公网购买页；
  - 未配置时回 `/license`，明确提示“购买入口尚未配置”，不伪造外链。

### API 补充

- `GET /api/license/status` 增加 `purchase_url`、`pricing_url`、`quota`
- `GET /api/license/file`：返回当前 license 内容，供备份
- `GET /api/license/events`：本机授权事件
- `PAGE_ALIASES` 增加 `/license`、`/pricing`

### 配置

`config.PURCHASE_URL` 读取 `YINZHAN_PURCHASE_URL`；未配置时为空。

## 2. 验证

- `/license`、`/pricing`、`/console` 均 200
- `/api/license/status` 200，返回 `quota.remaining=3`、`pricing_url=/pricing`、`purchase_url=''`
- `/api/license/events` 200
- `/api/license/file` 无授权时 404（预期）
- `node --check`：app.js / license.js / pricing.js 通过
- 完整 `run_all`：**33/33 通过，319.31s**

## 3. 未做/待接

1. **静态购买二维码**：当前没有正式购买 URL，先不生成假二维码；W4 接 A 模式购买页后加。
2. **生产公钥**：`public_keys.json` 仍为空；W3 页面可展示/试用/导入，但导入真实授权前置是生成生产密钥并合并公钥。
3. **浏览器截图验收**：本机 HTTP 冒烟已过，未做人工视觉走查。
4. **价格页正式版本**：当前是本地参考价页；公网 `/pricing`/`/buy` 仍归 W4。
5. **DSH 面板升级卡**：W3 只做控制台/授权页，DSH 面板卡在后续接。
