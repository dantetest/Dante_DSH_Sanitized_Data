# 下载页配置化与离线包部署（2026-11-05）

## 1. 下载页现在读什么

`app/web/download.html` 不再写死文件名/大小/链接，也不访问 Gitea。

页面只读取：

```text
/updates/latest.json
```

由该文件填充：

- 版本号；
- 文件名；
- 文件大小；
- 下载链接；
- SHA-256。

没有配置时按钮不显示，页面提示“下载配置暂不可用”，不会再出现灰色死按钮。

私有化部署联系：`contact@blctek.com`。

## 2. 当前离线版配置

已生成：

```text
Sanitized_Data/out/desktop/latest.json
```

内容：

```json
{
  "version": "1.5.2",
  "tag": "v1.5.2",
  "assets": {
    "windows_installer": {
      "name": "Sanitized_Data_Setup_1.5.2_x64.exe",
      "url": "/updates/Sanitized_Data_Setup_1.5.2_x64.exe",
      "size": 173108165,
      "sha256": "1B006BB956BDBE8DF0DA5A16EEDB90664A37B862C0DCA2587B60A94FE5E3D9E1"
    },
    "windows_portable": {
      "name": "Sanitized_Data-1.5.2-win64-portable.zip",
      "url": "/updates/Sanitized_Data-1.5.2-win64-portable.zip",
      "size": 223978438,
      "sha256": "0AF2A4C3C9638021B3432A6AD62FAE01308A72727CFA076C4063FF8ACFDD6040"
    }
  }
}
```

`url` 是服务器相对路径，不是 Gitea 地址。部署到 `sd.blctek.com` 后实际访问：

```text
https://sd.blctek.com:8443/updates/Sanitized_Data_Setup_1.5.2_x64.exe
https://sd.blctek.com:8443/updates/Sanitized_Data-1.5.2-win64-portable.zip
```

## 3. 离线包放服务器哪里

假设网站部署根目录是 `/opt/sanitized-data`（CKA 当前目录；`sd` 若不同请换实际目录）：

```text
/opt/sanitized-data/
  app/web/download.html          # 页面（git pull 更新）
  data/
    updates/
      latest.json                # 配置文件
      Sanitized_Data_Setup_1.5.2_x64.exe
      Sanitized_Data-1.5.2-win64-portable.zip
```

即：

```bash
cd /opt/sanitized-data
mkdir -p data/updates

# 上传两个包到 data/updates/
# 上传 latest.json 到 data/updates/latest.json

sudo systemctl restart sanitized-data
```

`data/updates/` 会通过服务自带的 `/updates/*` 静态路由对外提供，不需要 Nginx 额外配置，也不经过 Gitea。

## 4. 页面部署

```bash
cd /opt/sanitized-data
git pull
sudo systemctl restart sanitized-data
```

验收：

```bash
curl -fsS https://sd.blctek.com:8443/updates/latest.json
curl -fsS https://sd.blctek.com:8443/download | grep -E 'v1.5.2|下载安装包'
```

浏览器强刷 `/download` 应看到：

- 版本 `v1.5.2`；
- 安装包/绿色版文件名和大小；
- 两个按钮可点击；
- 两行 SHA-256。

## 5. 以后发版

1. 把新安装包/绿色版放到 `data/updates/`；
2. 更新 `data/updates/latest.json` 的 `version`、`name`、`url`、`size`、`sha256`；
3. 重启服务；
4. 页面自动读取新配置，不用改 `download.html`。

也可以用仓库自带脚本生成配置：

```bash
python3 deploy/publish_update.py --tag v1.5.2 \
  --installer /path/Sanitized_Data_Setup_1.5.2_x64.exe \
  --portable /path/Sanitized_Data-1.5.2-win64-portable.zip \
  --dest data/updates \
  --url-prefix /updates
```

> 注意：`latest.json` 同时被桌面版更新检查使用；这里的 version/资产信息必须与实际包一致。

## 6. 为什么不放 Gitea

`https://gitea.blctek.com:8443/...` 是代码仓库地址，不是公司服务器下载源。现在下载页只依赖 `sd.blctek.com` 自己的 `/updates/`，访问和权限都留在公司服务器。
