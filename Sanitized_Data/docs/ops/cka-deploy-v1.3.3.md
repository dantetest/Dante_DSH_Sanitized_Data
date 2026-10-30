# CKA v1.3.3 部署交接（2026-10-30）

> 源码：Gitea `main`，tag `v1.3.3`
> 产物：Gitea Release `v1.3.3`（安装包 + 绿色版 + 插件 + SHA256SUMS）
> 本版为**正式构建**（未使用 `-TestMode`，收费门禁开启）。

## 1. 产物

| 文件 | 大小 | SHA-256 |
|---|---|---|
| `Sanitized_Data_Setup_1.3.3_x64.exe` | 170,220,749 B（162.3 MiB） | `dd150397882a4ff5a33acce2e7a4a695989d782f5077661f083484c1e14856a3` |
| `Sanitized_Data-1.3.3-win64-portable.zip` | 220,062,075 B（209.9 MiB） | `472dbfcdae2475aa1fdd04d42ff7a14fb067d18bfd5aeb7b84f03bea807140b2` |
| `Sanitized_Data_dsh-plugin_1.3.3.zip` | 12,963 B | `5515edbd52bcba38fb60810a44899f81950c2dbed33dff490369ffceb2751ac4` |

## 2. CKA 升级网站代码

```bash
cd /opt/sanitized-data
sudo git pull
sudo systemctl restart sanitized-data
curl -fsS https://cka.blctek.com:8443/api/health
# 期望 version=1.3.3
```

## 3. 发布下载清单（下载页绿色版链接的关键）

下载页 `/download` 的 Windows 安装包/绿色版按钮读取 `/updates/latest.json`；
清单缺失时页面会显示“本服务器尚未发布安装包”，不会出现绿色版下载链接。

在 CKA 上执行：

```bash
cd /opt/sanitized-data
sudo -u sanitized env GITEA_TOKEN="$(sudo cat /etc/sanitized-data-gitea-token)" \
  python3 deploy/publish_update.py --tag v1.3.3
```

脚本会从 Gitea Release `v1.3.3` 拉取安装包与 `*-win64-portable.zip` 到
`data/updates/`，写入 `latest.json`（含两个资产的 name/url/size/sha256）。

若运维不想让 CKA 访问 Gitea，可先把文件传到 CKA 的 `data/updates/`，再执行：

```bash
cd /opt/sanitized-data
python3 deploy/publish_update.py --tag v1.3.3 \
  --installer data/updates/Sanitized_Data_Setup_1.3.3_x64.exe \
  --portable data/updates/Sanitized_Data-1.3.3-win64-portable.zip
```

## 4. 验收

```bash
curl -fsS https://cka.blctek.com:8443/updates/latest.json
curl -fsS https://cka.blctek.com:8443/download | grep -E '1\.3\.3|绿色版|portable'
```

浏览器打开 `https://cka.blctek.com:8443/download`：

- 版本 chip 显示 v1.3.3；
- 「Windows · x64 · 绿色版」显示 `Sanitized_Data-1.3.3-win64-portable.zip`；
- 「下载绿色版 ↓」可点击并下载（CKA 自身资产，不经过 Gitea 登录）；
- SHA-256 表格显示安装包/绿色版两个哈希。

## 5. 注意

1. 不要覆盖 `v1.3.0`/`v1.3.1`/`v1.3.2` 的历史 Release 资产；
2. CKA 网页控制台只做展示/试用，真实合同/病历请用桌面安装版在本机处理；
3. 安装包未代码签名，SmartScreen 可能提示「未知发布者」；
4. 若只需要临时测试，可在开发机用 `-TestMode` 另打一版；**正式下载页必须使用本正式包**。
