# CKA v1.3.4 部署（一键更新首发）

> 源码 Gitea `main`：`9efe922`（v1.3.4）
> Gitea Release：`v1.3.4`（绿色版 + 插件 + 一键安装脚本；**不发布坏掉的 Inno 安装包**）

## 1. 拉源码/重启

```bash
cd /opt/sanitized-data
sudo git pull
sudo systemctl restart sanitized-data
curl -fsS https://cka.blctek.com:8443/api/health
# version=1.3.4
```

## 2. 发布下载清单

```bash
cd /opt/sanitized-data
sudo -u sanitized env GITEA_TOKEN="$(sudo cat /etc/sanitized-data-gitea-token)" \
  python3 deploy/publish_update.py --tag v1.3.4
```

验证：

```bash
curl -fsS https://cka.blctek.com:8443/updates/latest.json
# version=1.3.4；assets.windows_portable 存在（无 windows_installer 是正常的）
```

## 3. 用户首次安装

安装包暂不发布（Inno 启动失败）。让用户：

1. 下载 `Sanitized_Data-1.3.4-win64-portable.zip`；
2. 同时下载 `安装-数据隐站.cmd` + `install.ps1`，放同一目录；
3. 双击 `安装-数据隐站.cmd`。

之后客户端内置 updater，v1.3.5+ 可一键更新。

## 4. 更新链路验收

- `/download` 显示 v1.3.4，绿色版按钮可用；
- 安装 v1.3.4 后启动，顶栏有「检查更新」；
- 当前最新就是 1.3.4，手动检查应提示“已是最新版本”；
- 下一版（v1.3.5）发布同一 `latest.json` 后，客户端会弹「发现新版本」。

> 不要用 `_BROKEN_INNO_NO_RESPONSE` 安装包；不要上传到 Gitea/CKA。
