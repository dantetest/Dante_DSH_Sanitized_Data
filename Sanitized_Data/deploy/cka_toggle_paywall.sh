#!/usr/bin/env bash
# 数据隐站 · CKA 收费门禁开关（内测全功能 / 对外收费）
#
# 用法（在 CKA 服务器 /opt/sanitized-data 下执行）：
#   sudo bash deploy/cka_toggle_paywall.sh status   # 查看当前状态
#   sudo bash deploy/cka_toggle_paywall.sh off      # 内测：全功能、不限量（朋友测试）
#   sudo bash deploy/cka_toggle_paywall.sh on       # 上线：免费额度 + 付费门禁
#
# 原理：1.3.0 源码运行的服务读取环境变量 YINZHAN_PAYWALL / YINZHAN_ENFORCE_FREE_TIER；
# 用 systemd drop-in 设置后重启服务即可，不迁移数据、不重装、不改机器码。
set -euo pipefail

MODE="${1:-status}"
SERVICE="${SERVICE:-sanitized-data}"
APP_DIR="${APP_DIR:-/opt/sanitized-data}"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
DROPIN="${DROPIN_DIR}/paywall.conf"
HEALTH="http://127.0.0.1:8760/api/health"
LICENSE="http://127.0.0.1:8760/api/license/status"

show_status() {
  echo "--- $SERVICE 门禁状态 ---"
  curl -fsS "$LICENSE" 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
q = d.get("quota") or {}
print("state       :", d.get("state"))
print("paywall     :", d.get("paywall_enabled"))
print("enforced    :", q.get("enforced"))
print("limit_today :", q.get("limit_today"))
print("remaining   :", q.get("remaining"))
print("edition     :", d.get("edition"))
print("build       :", d.get("build_channel"))
' || echo "服务未响应，稍后重试：$LICENSE"
  echo "--- /api/health ---"
  curl -fsS "$HEALTH" 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("ok      :", d.get("ok"))
print("version :", d.get("version"))
print("build   :", d.get("build"))
print("parsers :", ", ".join((d.get("engine") or {}).get("parsers") or []))
' || true
}

write_dropin() {
  local content="$1"
  if [ ! -d "$APP_DIR" ]; then
    echo "找不到 APP_DIR：$APP_DIR" >&2
    exit 1
  fi
  mkdir -p "$DROPIN_DIR"
  # 先备份旧配置，再写新配置
  if [ -f "$DROPIN" ]; then
    cp -a "$DROPIN" "${DROPIN}.$(date +%Y%m%d-%H%M%S).bak" 2>/dev/null || true
  fi
  printf '%s\n' "$content" > "$DROPIN"
}

restart_and_check() {
  systemctl daemon-reload
  systemctl restart "$SERVICE"
  sleep 2
  show_status
}

case "$MODE" in
  status)
    show_status
    ;;
  off|test|internal)
    write_dropin "[Service]
Environment=YINZHAN_PAYWALL=0
Environment=YINZHAN_ENFORCE_FREE_TIER=0
Environment=YINZHAN_BUILD_CHANNEL=internal-test"
    echo "已切换为【内测全功能】：不限份数/页数、TIF/三维可用；控制台显示「测试版 · 全功能」"
    restart_and_check
    ;;
  on|release|paid)
    if [ -s "$APP_DIR/app/server/yinzhan/licensing/public_keys.json" ]; then
      echo "公钥文件非空，正式授权可用"
    else
      echo "警告：public_keys.json 仍为空，正式 license 导入会失败；请先完成密钥仪式再对外收费" >&2
    fi
    write_dropin "[Service]
Environment=YINZHAN_PAYWALL=1
Environment=YINZHAN_ENFORCE_FREE_TIER=1
Environment=YINZHAN_BUILD_CHANNEL=release"
    echo "已切换为【对外收费】：免费版 3 份/天、20 页、100MB、TIF 需付费/试用"
    restart_and_check
    ;;
  *)
    echo "用法: $0 status|off|on" >&2
    exit 2
    ;;
esac
