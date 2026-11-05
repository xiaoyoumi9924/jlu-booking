#!/bin/sh
# One-command local deployment / launcher for the Web version (macOS / Linux).
# With no arguments: deploy on first run, then open the local UI. Arguments are
# passed straight to tools/deploy_local.py (stop, status, up, ...).
set -e

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 python3。请先安装 Python 3.10 或更高版本。"
    exit 1
fi

if [ "$#" -eq 0 ]; then
    exec python3 tools/deploy_local.py open
fi

exec python3 tools/deploy_local.py "$@"
