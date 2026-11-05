#!/bin/sh
# JLU Booking 快速部署（macOS / Linux）
#
#   curl -fsSL https://raw.githubusercontent.com/xiaoyoumi9924/jlu-booking/main/scripts/quick-start.sh | bash
#
# 第一次运行：下载仓库 → 部署 → 打开页面。
# 之后运行：更新仓库 → 直接打开页面。
#
# 可选环境变量：
#   JLU_BOOKING_HOME       安装目录（默认 ~/.jlu-booking）
#   JLU_BOOKING_BRANCH     分支（默认 main）
#   JLU_BOOKING_REPO_URL   仓库地址（默认 GitHub）
#   JLU_BOOKING_DRY_RUN=1  只下载/更新，不启动

set -eu

REPO_URL="${JLU_BOOKING_REPO_URL:-https://github.com/xiaoyoumi9924/jlu-booking.git}"
BRANCH="${JLU_BOOKING_BRANCH:-main}"
TARGET="${JLU_BOOKING_HOME:-$HOME/.jlu-booking}"
FRESH=0

say() { printf '%s\n' "$*"; }
fail() { printf '错误：%s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 \
    || fail "未找到 git。请先安装 Git（macOS 可运行：xcode-select --install）。"
command -v python3 >/dev/null 2>&1 \
    || fail "未找到 python3。请先安装 Python 3.10 或更高版本：https://www.python.org/downloads/"

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || fail "Python 版本过低，需要 3.10 或更高版本。"

if [ -d "$TARGET/.git" ]; then
    say "更新已有安装：$TARGET"
    if ! git -C "$TARGET" pull --ff-only --quiet; then
        say "（更新失败，使用本地版本继续）"
    fi
elif [ -e "$TARGET" ]; then
    fail "$TARGET 已存在但不是 Git 仓库。请移走它，或设置 JLU_BOOKING_HOME 指向其他目录。"
else
    say "下载 JLU Booking 到 $TARGET"
    git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$TARGET" \
        || fail "下载失败，请检查网络后重试。"
    FRESH=1
fi

cd "$TARGET" || fail "无法进入 $TARGET"

if [ "${JLU_BOOKING_DRY_RUN:-0}" = "1" ]; then
    say "已就绪：${TARGET}（dry-run，未启动）"
    exit 0
fi

# 通过 curl | bash 运行时 stdin 是脚本本身；把交互接回终端，
# 这样管理员密码等提示才能正常输入。
if [ "$FRESH" = "1" ]; then
    say "以后打开：$TARGET/jlu（或重新运行本命令）"
fi

if [ -r /dev/tty ]; then
    exec ./jlu </dev/tty
fi

say "提示：当前没有可用的终端，无法输入管理员密码。请在终端中重新运行。"
exec ./jlu
