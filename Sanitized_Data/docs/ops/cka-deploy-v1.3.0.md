# CKA v1.3.0 部署交接（给运维/程序员）

> 源码：Gitea `main`，提交 `e4e5a36`（v1.3.0）
> 产物：Gitea Release `v1.3.0`
> 本次部署的是**官网 + 下载页 + 授权/价格页**；桌面付费版由用户下载安装，不在 CKA 网页里处理文件。

## 1. 部署源码

```bash
cd /opt/sanitized-data
sudo git pull
sudo systemctl restart sanitized-data
```

确认版本：

```bash
curl -fsS https://cka.blctek.com:8443/api/health
# version=1.3.0
```

## 2. 发布下载清单

Gitea Release `v1.3.0` 已有资产：

- `Sanitized_Data_Setup_1.3.0_x64.exe`（195,507,112 B）
- `Sanitized_Data-1.3.0-win64-portable.zip`（219,895,506 B）
- `Sanitized_Data_dsh-plugin_1.3.0.zip`
- `SHA256SUMS-v1.3.0.txt`

在 CKA 上执行：

```bash
cd /opt/sanitized-data

sudo -u sanitized env GITEA_TOKEN="$(sudo cat /etc/sanitized-data-gitea-token)" \
  python3 deploy/publish_update.py --tag v1.3.0
```

然后验证：

```bash
curl -fsS https://cka.blctek.com:8443/updates/latest.json
# version=1.3.0，资产链接指向 CKA 自身
```

## 3. 页面验收

打开/请求：

- `https://cka.blctek.com:8443/` 首页
- `https://cka.blctek.com:8443/download` 下载页：显示 v1.3.0，Windows/macOS 按钮按 manifest 生效
- `https://cka.blctek.com:8443/pricing` 价格页
- `https://cka.blctek.com:8443/license` 授权页

## 4. 重要说明

1. **CKA 网页控制台不是桌面安装版**：不要让朋友在 CKA `/console` 上传真实合同/病历；文件应在本机安装版处理。
2. CKA 以源码运行，`PAYWALL_ENABLED` 的免费额度门禁只在 PyInstaller 打包版按设计生效；**网站本身不做收费限制**。
3. **不要用 `-TestMode` 产物**发布；正式安装包是 Gitea Release v1.3.0 里的 `Sanitized_Data_Setup_1.3.0_x64.exe`。
4. 安装包未代码签名，SmartScreen 可能提示；按既定决策后续补签。
5. 不要覆盖/重传 v1.3.0 资产；后续修复用新版本号。
6. 若需要回滚网站代码，使用上一提交/标签并在 CKA 重启；下载 manifest 也同步回滚。

## 5. 常见问题

| 问题 | 处理 |
|---|---|
| `/updates/latest.json` 404 | 执行第 2 步 publisher；检查 token 文件 |
| 下载按钮不可用 | 先看 manifest；再检查 `data/updates/assets` 权限 |
| 页面还是旧版本 | `systemctl restart sanitized-data` 后强刷 |
| Gitea Release 访问要登录 | CKA publisher 用 token 下载；用户只访问 CKA 下载页 |
