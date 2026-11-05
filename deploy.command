#!/bin/sh
# macOS double-click entry point: deploys on first run, then just opens the
# local Web UI. Keeps the window open on failure so the message is readable.

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 python3。请先安装 Python 3.10 或更高版本。"
    echo
    echo "按回车键关闭窗口。"
    read -r _answer
    exit 1
fi

if [ "$#" -eq 0 ]; then
    python3 tools/deploy_local.py open
else
    python3 tools/deploy_local.py "$@"
fi
status=$?

if [ "$status" -ne 0 ]; then
    echo
    echo "运行失败（退出码 $status）。按回车键关闭窗口。"
    read -r _answer
fi

exit "$status"
