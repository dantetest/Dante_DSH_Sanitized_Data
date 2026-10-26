#!/usr/bin/env bash
# 数据隐站 · CKA 测试服升级到 1.3.0 并开启【内测全功能】门禁
#
# 在 CKA 服务器上执行（需要 sudo）：
#   sudo bash deploy/cka_upgrade_internal_test.sh
#
# 步骤：备份 license/updates/logs → git fetch/checkout release/v1.3.0
#       → 检查 rasterio/pyproj → 切内测门禁 → 重启 → 打印状态
# 回滚：sudo bash deploy/cka_toggle_paywall.sh on（或 git checkout 旧 tag 后重启）
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/sanitized-data}"
REF="${REF:-release/v1.3.0}"
SERVICE="${SERVICE:-sanitized-data}"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/sanitized-data-backup-${TS}.tgz"

if [ ! -d "$APP_DIR/.git" ]; then
  echo "找不到 $APP_DIR/.git，请确认部署目录" >&2
  exit 1
fi
cd "$APP_DIR"

if [ -n "$(git status --porcelain)" ]; then
  echo "部署目录有未提交改动，先确认再升级：" >&2
  git status --short | head -20 >&2
  exit 1
fi

echo "== 1/4 备份 data/license、data/updates、data/logs =="
tar czf "$BACKUP" data/license data/updates data/logs 2>/dev/null || true
echo "备份：$BACKUP"

echo "== 2/4 拉取 $REF =="
git fetch origin --prune
git checkout "$REF"
git pull --ff-only origin "$REF"
echo "HEAD: $(git log --oneline -1)"

echo "== 3/4 检查 TIFF 可选依赖（rasterio/pyproj） =="
if ! python3 -c 'import rasterio, pyproj' 2>/dev/null; then
  echo "缺少 rasterio/pyproj：大正射 TIFF 会退化为 Pillow（>1.79 亿像素打不开）。" >&2
  echo "请先安装后再执行本脚本：" >&2
  echo "  sudo python3 -m pip install --no-index --find-links deploy/wheels -r app/server/requirements.txt" >&2
  exit 2
fi
echo "rasterio/pyproj OK"

echo "== 4/4 切内测全功能门禁并重启 =="
bash deploy/cka_toggle_paywall.sh off

echo
echo "升级完成。公网验收（本机执行）："
echo "  python deploy/smoke_cka.py --base https://cka.blctek.com:8443 --expect internal"
