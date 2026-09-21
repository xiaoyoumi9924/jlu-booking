"""Command-line management for the per-user saved token."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from .paths import TOKEN_FILE
from .token_store import (
    TokenStoreError,
    clear_saved_token,
    extract_token_input,
    load_saved_token,
    resolve_token,
    save_token,
)
from .token_validation import validate_token_online


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="保存、查看状态或清除 JLU Booking Token（不会回显 Token 内容）。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("set", help="隐藏输入并保存 Token；再次执行即可修改")
    subparsers.add_parser("status", help="查看保存状态和文件位置，不显示 Token")
    subparsers.add_parser("verify", help="联网验证当前生效的 Token")
    clear_parser = subparsers.add_parser("clear", help="清除本机保存的 Token")
    clear_parser.add_argument(
        "--yes",
        action="store_true",
        help="不再询问，直接清除",
    )
    return parser


def _show_status(token_path: Path) -> None:
    saved = bool(load_saved_token(token_path))
    environment_active = bool(os.environ.get("JLU_BOOKING_TOKEN", "").strip())

    print(f"Token 文件：{token_path}")
    print("本机保存状态：" + ("已保存" if saved else "未保存"))
    if environment_active:
        print("当前优先来源：环境变量 JLU_BOOKING_TOKEN")
        if saved:
            print("说明：环境变量会暂时覆盖本机保存的 Token。")
    elif saved:
        print("当前优先来源：本机保存的 Token")
    else:
        print("当前优先来源：未配置；下次 GUI 或交互式自动任务会提示输入")


def main(argv=None, *, token_path: Path | str = TOKEN_FILE):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    resolved_path = Path(token_path)

    try:
        if args.command == "status":
            _show_status(resolved_path)
            return

        if args.command == "verify":
            token, source = resolve_token(resolved_path)
            if not token:
                print("当前没有可验证的 Token。")
                return
            source_text = (
                "环境变量 JLU_BOOKING_TOKEN"
                if source == "environment"
                else "本机保存"
            )
            print(f"Token 来源：{source_text}")
            print("正在向学校系统验证…")
            result = validate_token_online(token)
            if result.status == "valid":
                print("✓ Token 有效。")
            elif result.status == "invalid":
                print("✗ Token 已失效，请重新设置。")
            else:
                print("⚠ 暂时无法验证 Token，请检查网络后重试。")
            return

        if args.command == "set":
            try:
                raw_token = getpass.getpass("请输入新的 Token（输入不会回显）：")
            except (EOFError, KeyboardInterrupt):
                print("\n已取消，Token 未修改。", file=sys.stderr)
                return
            token = extract_token_input(raw_token)
            print("正在向学校系统验证 Token…")
            result = validate_token_online(token)
            if result.status == "invalid":
                print("Token 无效或登录状态已失效；原有 Token 未修改。")
                return
            if result.status != "valid":
                print("暂时无法验证 Token；原有 Token 未修改。")
                return
            saved_path = save_token(token, resolved_path)
            print("Token 已保存或更新。")
            print(f"保存位置：{saved_path}")
            print("已在运行的 GUI 或自动任务需重启后才会读取新 Token。")
            return

        if not args.yes:
            if not sys.stdin.isatty():
                raise SystemExit("非交互环境清除 Token 时请添加 --yes。")
            answer = input("确定清除本机保存的 Token？输入 y 确认：").strip().lower()
            if answer not in {"y", "yes"}:
                print("已取消，Token 未清除。")
                return

        removed = clear_saved_token(resolved_path)
        print("Token 已清除。" if removed else "本机没有已保存的 Token。")
        if os.environ.get("JLU_BOOKING_TOKEN", "").strip():
            print("注意：当前环境变量 JLU_BOOKING_TOKEN 仍会继续生效。")
    except (TokenStoreError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
