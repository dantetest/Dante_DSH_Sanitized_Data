# CKA 测试服：内测全功能 ↔ 对外收费 切换手册

> 适用：CKA `/opt/sanitized-data`（systemd `sanitized-data.service`，源码运行）。
> 目标：内测期朋友随便测（不限 3 份/天、可测 TIF/正射）；对外宣发前一条命令切回
> “免费额度 + 付费门禁”，不改数据、不换机器码、不重新安装。

## 0. 先确认

```bash
cd /opt/sanitized-data
git branch --show-current          # 期望 release/v1.3.0
git log --oneline -1
curl -s http://127.0.0.1:8760/api/health | python3 -m json.tool | head
curl -s http://127.0.0.1:8760/api/license/status | python3 -m json.tool | head -30
```

`/api/license/status` 中关键字段：

- `paywall_enabled`：收费门禁是否开启（1.3.0 新增）
- `quota.enforced`：本次进程是否真的执行免费额度（源码运行默认 false，必须配 `YINZHAN_ENFORCE_FREE_TIER=1`）
- `quota.limit_today / remaining`：免费额度
- `state`：free / trial / paid

## 1. 升级到 1.3.0（含今天航拍/正射修复）并切内测全功能

```bash
cd /opt/sanitized-data
# 先确认没有本地未提交改动；有的话先人工处理
git status --short
sudo bash deploy/cka_upgrade_internal_test.sh
```

脚本会：

1. 备份 `data/license`、`data/updates`、`data/logs` 到 `/root/`；
2. `git fetch` + `checkout release/v1.3.0` + `pull --ff-only`；
3. 检查 `rasterio` / `pyproj`（大正射 TIFF 依赖，缺失会中止并给安装命令）；
4. 写入 systemd drop-in 并重启：

```ini
# /etc/systemd/system/sanitized-data.service.d/paywall.conf
[Service]
Environment=YINZHAN_PAYWALL=0
Environment=YINZHAN_ENFORCE_FREE_TIER=0
Environment=YINZHAN_BUILD_CHANNEL=internal-test
```

5. 打印 `/api/license/status` 与 `/api/health`。

预期结果：

- `/api/health`：`version=1.3.0`，`build=20261026-ortho-meta`
- `/api/license/status`：`paywall_enabled=false`，`quota.enforced=false`
- 控制台右上角：**「测试版 · 全功能」**（不再是「免费版 · 今日剩余 3/3」）
- 朋友测试：不限份数/页数，TIF 正射可上传、可处理

## 2. 公网验收（在开发机执行）

```powershell
python deploy/smoke_cka.py --base https://cka.blctek.com:8443 --expect internal
# 深度验收（会真实上传/导出，建议内测实例）：
python deploy/smoke_cka.py --base https://cka.blctek.com:8443 --expect internal --deep `
  --tif "C:\Users\Dante\Desktop\测试集\网络脱敏测试集_20261025\06_tiff\T01_ortho_utm_baker_st.tif" `
  --jpeg "C:\Users\Dante\Desktop\测试集\无人机航拍\DJI_0865.JPG"
```

## 3. 对外收费当天：切正式门禁

前置条件（缺一不可）：

- [ ] `public_keys.json` 已放入生产公钥（当前仓库为空，直接收费会导致 license 导入失败）
- [ ] `YINZHAN_PURCHASE_URL` 配置好；`/pricing`、购买/开票流程可用
- [ ] 免费额度/试用策略已确认（3 份/天、20 页、100MB、TIF 付费）
- [ ] 内测产生的 `data/uploads`、`data/out`、日志/审计已清理或换新实例
- [ ] 低峰期、通知测试用户（重启会清空内存中任务和 `/console?job=` 深链）

执行：

```bash
cd /opt/sanitized-data
sudo bash deploy/cka_toggle_paywall.sh on
```

程序会切换为：

```ini
[Service]
Environment=YINZHAN_PAYWALL=1
Environment=YINZHAN_ENFORCE_FREE_TIER=1
Environment=YINZHAN_BUILD_CHANNEL=release
```

验收：

```bash
python deploy/smoke_cka.py --base https://cka.blctek.com:8443 --expect release
# 浏览器：免费版 · 今日剩余 3/3；上传 TIF 应提示升级；/api/trial/start 可开 3 天试用
```

## 4. 回滚

```bash
# 回到内测全功能
sudo bash deploy/cka_toggle_paywall.sh off

# 回到旧版本（如需）
cd /opt/sanitized-data
git checkout v1.2.2
systemctl restart sanitized-data
```

## 5. 注意事项

1. **不要手改** `data/license/state.json` / `.state_seen`；额度/试用状态有 HMAC 与历史防刷。
2. 内测全功能期间 `state.json` 不会扣免费额度（`consume_job` 在未启用门禁时直接返回），所以上线后免费额度从 0 开始，不需要手工重置。
3. 试用（3 天，全功能、不限量）是**整机一次**；对外前可以保留给客户体验，但不宜在内测期间用掉。
4. 朋友测试数据只在内测期存在；对外前务必清理上传件/成品/日志中的客户数据。
5. 每次切换都要重启服务；正在跑的任务会丢失，选不测试的时间点。
6. 切正式后先自己走一遍：免费上传、TIF 拦截、试用、签发 license 导入，再发公告。
