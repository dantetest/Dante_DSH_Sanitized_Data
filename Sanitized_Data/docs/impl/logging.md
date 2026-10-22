# 本地文件日志与服务器状态查看（v1.1.2 起）

> 目标：项目部署到服务器后，出问题不再只有「页面打不开」——启动到哪一步、
> 有没有请求进来、进程是否还活着、报了什么错，都能在一份本地文件日志里看到。
> 代码：`app/server/yinzhan/logging_setup.py`、`app/server/yinzhan/app.py`、
> `app/server/yinzhan/api.py`、`app/server/status.py`；
> 自测：`app/tests/test_logging.py`。

## 1. 文件布局与格式

默认日志目录 `<数据目录>/logs/`（可用 `YINZHAN_LOG_DIR` 覆盖）：

```text
data/logs/
├─ server.log                 当前正在写
└─ server-2026-10-22.log      每天 0 点翻转出的历史日志（默认保留最近 30 个，约 30 天）
```

每行格式固定，方便 `grep` / `awk` / 监控脚本解析：

```text
[2026-10-22 18:05:03] [INFO] [yinzhan.server] start pid=12345 ... version=1.1.2
[2026-10-22 18:05:03] [INFO] [yinzhan.server] ready pid=12345 url=http://127.0.0.1:8760 ...
[2026-10-22 18:05:03] [INFO] [yinzhan.heartbeat] heartbeat pid=12345 uptime=300.1 active_jobs=0
[2026-10-22 18:05:04] [INFO] [yinzhan.api] req method=GET path=/api/health status=200 bytes=747 ms=8.1
[2026-10-22 18:05:05] [WARNING] [yinzhan.api] 跨站请求被拒 origin=... host=...
[2026-10-22 18:05:06] [INFO] [yinzhan.server] stop pid=12345 reason="收到信号 SIGTERM"
```

## 2. 记什么 / 不记什么

| 事件 | 级别 | 内容 |
|---|---|---|
| 进程启动 | INFO | pid/ppid/版本/构建/平台/数据目录/日志文件/心跳间隔/CLI 参数 |
| 引擎加载 | INFO | 加载耗时、OCR 是否可用、解析器列表、插件错误数 |
| 服务就绪 | INFO | host/port/url/runtime.json 路径 |
| 请求 | INFO | method/path/status/字节数/耗时；**query、请求体、文件名不记** |
| 跨站拦截 | WARNING | Origin/Host/X-Forwarded-Host（现有防 CSRF 诊断信息） |
| 业务/协议错误 | ERROR/WARNING | 接口路径 + 异常堆栈；不打印请求体 |
| 未捕获异常 | ERROR/CRITICAL | 主线程 & 子线程堆栈（避免静默退出） |
| 心跳 | INFO | pid/uptime/线程数/RSS/磁盘剩余/活跃任务数（默认 300s） |
| 退出 | INFO | pid/uptime/退出原因（正常/Ctrl+C/SIGTERM/启动失败） |

隐私边界（对应红线「日志不能成为泄漏源」）：

* 请求行只保留 `path`，`format_request()` 会先丢掉 `?query` 和 `#fragment`，
  控制字符替换为 `?`、单行限长 300 字符（防止换行伪造日志行）。
* 请求体、文档内容、实体原值一律不进日志；请求日志不记上传文件名。
* 异常堆栈会保留，但落盘前把数据目录路径替换成 `<data>`，上传临时名
  （`u_<hex>_原文件名`）替换成 `<upload>`，避免原文件名泄漏。
* 客户端 IP 默认不记；`YINZHAN_LOG_CLIENT_IP=1` 时，反代场景优先取
  `X-Real-IP` / `X-Forwarded-For` 第一跳，仅记录在请求行/错误行。
* HTTP 头、`http.server` 协议错误文本统一清 CR/LF/控制字符与 query，
  防止外部输入伪造多行日志。

## 3. 按日期翻转 / 保留 / 降级

复用标准库 `TimedRotatingFileHandler`（`when="midnight"`），只定制归档命名和清理：

* 每天 0 点后的第一条日志把 `server.log` rename 为 `server-YYYY-MM-DD.log`，
  再开新文件；归档名不使用默认的 `server.log.2026-10-22`。
  翻转是惰性的：`YINZHAN_LOG_HEARTBEAT_SEC=0` 且长时间无请求时，切换会延后到
  下一条达到日志级别的记录（默认 5 分钟心跳下延迟 ≤ 5 分钟）。
* 同名归档已存在（时钟回拨/同日二次 rollover）时加 `-1`、`-2` 后缀，绝不覆盖。
* `YINZHAN_LOG_KEEP_DAYS` 实际是**保留最近 N 个按日归档**（默认 30）；按日写入时
  约等于 30 天，0 = 不清理。
* 进程跨天停机后重启：启动时按 `server.log` 的 mtime 把旧内容归档，
  避免昨天/今天混在同一个文件里。
* 旧版 `<数据目录>/server.log` 若存在且新日志目录为空，启动时自动迁移一次。
* 首选日志目录不可写（只读挂载/磁盘满/权限不足）时先回退 `<数据目录>/logs`；
  仍不可写才只写 stderr，**服务继续启动**。降级原因记在 `runtime.json` 的
  `log.file_error`，并写一条 WARNING。

线程安全由标准库 handler 锁保证；每个进程持有一个文件 handler。
本项目按单实例设计（桌面版有单实例保护，服务器由 systemd 管理）；如果自行启动
第二个实例并共享同一 `data/`，请给它单独指定 `YINZHAN_LOG_DIR`，否则两个进程
会同时写同一个 `server.log`。

## 4. 生命周期接入点

| 位置 | 行为 |
|---|---|
| `app.main()` 开头 | `setup_logging()` → `install_exception_hooks()` → `log_startup()`；先落日志再加载 OCR |
| `preload()` 前后 | `正在加载解析插件与 OCR…` / `引擎加载完成 load_s=...`（卡住时能看出停在哪） |
| `serve()` + `write_runtime()` 后 | `log_ready()`；`runtime.json` 增加 `log_dir/log_file/log` 字段 |
| `serve_forever()` 前 | `start_heartbeat()`（默认 5 分钟；`YINZHAN_LOG_HEARTBEAT_SEC` 可调/关闭）；HTTP 循环放子线程，SIGTERM 时主线程可安全关停 |
| `Handler.do_*()` | `_begin_request()` / `_end_request()` 包住分发，`_send()` 记录 status/bytes |
| 信号/SIGTERM | 自定义 handler 写 `stop reason=收到信号 SIGTERM`，再走 finally 清理 |
| `finally` | `stop_heartbeat()` → `log_shutdown()` → 关 HTTP → 清临时文件 → 关日志（stop 保证最后一行） |

桌面版 `desktop/main.py` 同样接入：无控制台时启动失败、退出也能从
`<数据目录>/logs/server.log` 查到。

## 5. 服务器端怎么看

```bash
# 实时日志（推荐长期开一个终端）
tail -f data/logs/server.log

# 只看错误 / 今天
grep -E '\[(ERROR|CRITICAL)\]' data/logs/server.log
grep '^\[' data/logs/server.log | tail -50

# 状态摘要：进程是否在 + /api/health + 最近心跳 + 今日错误/警告 + 日志尾部
python app/server/status.py
python app/server/status.py --json          # 给 cron / 监控系统；健康=退出码 0
python app/server/status.py --no-http       # 反代/本机 health 不通时只看进程与日志

# systemd 的 stdout/stderr（与文件日志同步，含启动 banner）
journalctl -u sanitized-data -f
```

`status.py` 不 import `config`（避免初始化时建目录/写探测文件），直接按
`YINZHAN_DATA_DIR` 或项目位置计算路径；只读 `runtime.json` / 日志文件，最多向
回环地址的 `/api/health` 发一次 GET（禁用代理与重定向，302 不跟随）。
显式拒绝非回环地址。退出码：`0` 健康、`1` 不健康（进程不在或 health 失败）、
`2` 采集异常。
建议以服务运行用户执行（如 `sudo -u sanitized python3 app/server/status.py`）。
若服务通过 `YINZHAN_DATA_DIR` 自定义了数据目录，`status.py` 运行时也要带同一个
环境变量，或用 `--data-dir` / `--log-dir` 显式指定。

cron 示例（每分钟检查，异常时返回非 0 可接告警系统；JSON 与错误分开重定向）：

```cron
* * * * * cd /opt/sanitized-data && python3 app/server/status.py --json >/var/tmp/sanitized-status.json 2>/var/tmp/sanitized-status.err || logger -t sanitized-data "sanitized-data unhealthy"
```

## 6. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `YINZHAN_LOG_DIR` | `<数据目录>/logs` | 日志目录 |
| `YINZHAN_LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |
| `YINZHAN_LOG_KEEP_DAYS` | `30` | 按日归档保留个数（约 30 天）；0 = 不清理 |
| `YINZHAN_LOG_REQUESTS` | `1` | 0 = 关闭逐请求日志 |
| `YINZHAN_LOG_HEARTBEAT_SEC` | `300` | 心跳间隔；0 = 关闭 |
| `YINZHAN_LOG_CLIENT_IP` | `0` | 1 = 请求日志记客户端 IP |
| `YINZHAN_LOG_STDERR` | 有 stderr 就开 | 0 = 关闭控制台同步输出 |

`runtime.json` 的 `log` 字段记录本进程实际生效的配置（供 `status.py` 使用）。
systemd 单元已显式设置 `YINZHAN_LOG_LEVEL/KEEP_DAYS/HEARTBEAT_SEC`，日志写入
`ReadWritePaths` 覆盖的 `data/`，与 `ProtectSystem=strict` 不冲突。
若把 `YINZHAN_LOG_DIR` 改到 `data/` 之外，需要同步把该目录加进 systemd 的
`ReadWritePaths`，否则会先回退 `<数据目录>/logs`，再不行才降级 stderr。

## 7. 验证与已知边界

自测（9 项，约 4–8 秒）：

```powershell
python -X utf8 app/tests/test_logging.py
# 或由全套执行器带跑
python -X utf8 app/tests/run_all.py --only logging
```

覆盖：基础落盘/格式、请求行丢 query/控制字符、错误与警告脱敏（数据目录/上传名）、
按日期翻转、跨天重启归档、保留清理、心跳线程、IP 开关、日志目录不可写回退。

已知边界：

* `SIGKILL` / 断电不会留下 `stop` 行；用最后一行时间 + 心跳缺失判断异常退出。
* 极端 CPU 密集且长时间占住 GIL 时，心跳可能延迟；`status.py` 不把单次延迟直接判死。
* 逐请求日志在恶意高频请求下会放大日志体积；需要时 `YINZHAN_LOG_REQUESTS=0` 关闭，
  或用 `YINZHAN_LOG_KEEP_DAYS` 控制保留。
* 时钟大幅回拨/前拨时归档名以本地时间为准；同名冲突会自动加序号，不会覆盖已有归档
  （保留策略仍会按个数清理更老的归档）。
* Windows 下外部 tail/编辑器若持有 `server.log` 句柄，轮转 rename 可能失败并延后重试；
  服务本身不受影响。
