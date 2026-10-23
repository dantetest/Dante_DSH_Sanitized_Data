# 更新资产部署（运维 / CKA）

> 目标：复用现有“Gitea push → CKA pull → 部署”流程，不要求客户登录 Gitea。
> 产品自身的 `/updates/` 静态路由直接从 `data/updates/` 提供文件，nginx 继续全量反代即可。

## 1. 架构

```text
开发机：build + git push + Gitea Release（安装包/绿色版）
   │
   ▼
CKA：git pull → systemctl restart → publish_update.py
   │                                   │
   │  从 Gitea Release 下载资产        │ 写入 /opt/sanitized-data/data/updates/
   ▼                                   ▼
nginx 8443（已是 proxy_pass 127.0.0.1:8760）
   │
   ▼
https://cka.blctek.com:8443/updates/latest.json
https://cka.blctek.com:8443/updates/Sanitized_Data_Setup_<ver>_x64.exe
```

**不需要改 nginx 配置**：当前 `location /` 已全部反代到产品服务；`/updates/` 由产品
`api.py` 的静态路由从 `data/updates/` 读取。

## 2. CKA 上的一次性准备

当前部署目录是 `/opt/sanitized-data`（见 `deploy/sanitized-data.service`）。

1. 确认服务用户可写 `data/updates`：
   ```bash
   sudo install -d -o sanitized -g sanitized /opt/sanitized-data/data/updates
   ```
2. 在 Gitea 创建 **只读 Release** token（或使用运维已有 token），放到服务器：
   ```bash
   sudo install -m 600 -o root -g root /dev/null /etc/sanitized-data-gitea-token
   sudo editor /etc/sanitized-data-gitea-token   # 只放 token，不要提交仓库
   ```
3. 拉取包含本功能的代码并重启：
   ```bash
   cd /opt/sanitized-data
   sudo git pull
   sudo systemctl restart sanitized-data
   curl -s http://127.0.0.1:8760/api/health | head
   ```

## 3. 每次发版的部署命令

```bash
cd /opt/sanitized-data
sudo git pull
sudo systemctl restart sanitized-data

# 只读 token 从文件读入，不打印
sudo -u sanitized env GITEA_TOKEN="$(sudo cat /etc/sanitized-data-gitea-token)" \
  python3 deploy/publish_update.py --tag v1.3.0

# 校验公网（应 200 JSON，不带登录）
curl -i https://cka.blctek.com:8443/updates/latest.json
```

脚本会：

1. 调 Gitea Release API 读取 `v1.3.0` 的资产；
2. 下载 `Sanitized_Data_Setup_*.exe` 与 `*-win64-portable.zip` 到 `data/updates/`；
3. 校验文件大小、计算 SHA-256；
4. 原子写入 `data/updates/latest.json`。

## 4. 资产已手动上传时

如果运维不想让服务器访问 Gitea，可以在发布机把文件传到 CKA，然后：

```bash
python3 deploy/publish_update.py --tag v1.3.0 \
  --installer /opt/sanitized-data/data/updates/Sanitized_Data_Setup_1.3.0_x64.exe \
  --portable /opt/sanitized-data/data/updates/Sanitized_Data-1.3.0-win64-portable.zip
```

脚本仍会计算 SHA-256、写 `latest.json`。

## 5. 检查更新源

```bash
curl -s https://cka.blctek.com:8443/updates/latest.json
```

应返回：

```json
{
  "version": "1.3.0",
  "tag": "v1.3.0",
  "release_url": "https://cka.blctek.com:8443/download",
  "assets": {
    "windows_installer": {"name": "...", "url": "...", "size": 0, "sha256": "..."},
    "windows_portable":  {"name": "...", "url": "...", "size": 0, "sha256": "..."}
  }
}
```

## 6. 安全/边界

- `data/updates/` 只放公开安装包/清单，不放客户数据；产品其余路径不暴露该目录。
- `latest.json` 响应 `Cache-Control: no-store`；安装包可缓存一天。
- 安装包 SHA-256 由部署脚本计算；客户端下载后必须再校验。
- Gitea token 只在 CKA 服务器上，权限只到 release 读取；不要写进仓库/客户端。
- 更新源是公开只读；客户不需要 Gitea 账号。

## 7. 客户端侧（下一步 v1.3.0）

更新源就绪后，客户端使用：

```text
GET https://cka.blctek.com:8443/updates/latest.json
```

实现：默认关闭自动检查、首次启动询问、顶部提醒、立即更新（下载→SHA-256→静默安装→重启）、
稍后 24h。设计见 [../impl/update-check.md](../impl/update-check.md)。

## 8. 与下载页的关系

建议后续把 CKA `/download` 的“下载按钮”从 Gitea Release 改成 `/updates/` 当前版本资产；
这样即使客户端没开自动更新，客户手动下载也不需要 Gitea 账号。
