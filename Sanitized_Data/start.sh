#!/usr/bin/env bash
# 数据隐站 · Linux / macOS 启动脚本
#
#   ./start.sh                 仅本机访问（默认 127.0.0.1:8760）
#   ./start.sh --host 0.0.0.0  对外提供服务（放在 nginx 反代后面时用）
#   PORT=9000 ./start.sh       指定端口
#
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "  找不到 $PY，请先安装 Python 3.10 及以上版本" >&2
  exit 1
fi

# 版本检查
"$PY" - <<'EOF' || { echo "  Python 版本过低，需要 3.10+" >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
EOF

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1

ARGS=()
if [ -n "${PORT:-}" ]; then ARGS+=(--port "$PORT"); fi
if [ -n "${BIND_HOST:-}" ]; then ARGS+=(--host "$BIND_HOST"); fi
if [ "${NO_BROWSER:-1}" = "1" ]; then ARGS+=(--no-browser); fi

exec "$PY" app/server/run.py "${ARGS[@]}" "$@"
