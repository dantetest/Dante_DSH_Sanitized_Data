#!/usr/bin/env bash
# 数据隐站 · macOS 手工构建脚本（没有 GitHub Actions 时用）
#
# 在有 macOS 的机器上（需要 Xcode Command Line Tools）：
#   bash desktop/build_mac.sh
#
# 产物：
#   dist/Sanitized_Data.app
#   Sanitized_Data-<version>-<arch>.dmg
#   其中 <arch> 由当前机器决定（arm64 / x64）
#
# 首次运行会 pip 安装依赖（含 pywebview / pyobjc），需要网络；
# 之后的 app 本身离线可用。
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
echo "== 构建目录：$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "!! 该脚本只能在 macOS 上运行（PyInstaller 不能跨平台打 macOS 包）" >&2
  exit 1
fi

PY="${PYTHON:-python3}"
VERSION=$("$PY" - <<'PY'
import re
from pathlib import Path
text = Path("app/server/yinzhan/config.py").read_text(encoding="utf-8")
print(re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE).group(1))
PY
)
ARCH="$(uname -m)"
echo "== 版本：$VERSION  架构：$ARCH"

echo "== 安装依赖"
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r app/server/requirements.txt
"$PY" -m pip install pyinstaller pywebview

echo "== PyInstaller 打包"
"$PY" -m PyInstaller --clean --noconfirm desktop/app.spec

echo "== ad-hoc 签名"
codesign --force --deep --sign - "dist/Sanitized_Data.app"
codesign --verify --deep --verbose=2 "dist/Sanitized_Data.app" || true

echo "== 打包 DMG"
hdiutil create -volname "Sanitized_Data" \
  -srcfolder "dist/Sanitized_Data.app" \
  -ov -format UDZO "Sanitized_Data-${VERSION}-${ARCH}.dmg"
shasum -a 256 "Sanitized_Data-${VERSION}-${ARCH}.dmg" | tee "SHA256SUMS-macos-${ARCH}.txt"

echo "== 冒烟测试（启动 app 后断言插件/健康字段）"
"dist/Sanitized_Data.app/Contents/MacOS/Sanitized_Data" &
APP_PID=$!
trap 'kill "$APP_PID" 2>/dev/null || true' EXIT
sleep 15
"$PY" .github/scripts/smoke_check.py

echo "== 完成："
ls -lh "Sanitized_Data-${VERSION}-${ARCH}.dmg" "dist/Sanitized_Data.app"
