#!/bin/sh

cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
    python3 start.py
    status=$?
else
    echo "未找到 Python 3。请先安装 Python 3.10 或更高版本。"
    status=1
fi

if [ "$status" -ne 0 ]; then
    echo
    echo "启动失败。按回车键关闭窗口。"
    read -r _answer
fi
exit "$status"
