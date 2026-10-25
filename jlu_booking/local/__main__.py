"""Launch the single-user desktop workspace."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from collections.abc import Sequence

from .bootstrap import LOCAL_USER_DISPLAY_NAME, create_local_app, local_data_dir
from .server import LocalRuntime
from .window import open_app_window


LOG = logging.getLogger("jlu_booking.local")


def _configure_logging() -> None:
    base = local_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(
            logging.FileHandler(base / "local.log", encoding="utf-8")
        )
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jlu-booking-local")
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="只启动本机服务并打印地址，不打开窗口",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="使用系统默认浏览器而不是应用窗口",
    )
    parser.add_argument("--port", type=int, default=0, help="固定本机端口")
    return parser


def _report_startup_failure(exc: BaseException) -> None:
    message = (
        f"启动失败：{exc}\n\n"
        f"详细日志：{local_data_dir() / 'local.log'}"
    )
    LOG.exception("local desktop startup failed", exc_info=True)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("JLU Booking", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr, flush=True)


def _run(args: argparse.Namespace) -> int:
    app, settings, identity = create_local_app()
    runtime = LocalRuntime(app, settings, log_path=local_data_dir() / "local.log")
    try:
        runtime.start(port=args.port or None)

        print(f"JLU Booking 本机版已启动：{runtime.url}", flush=True)
        print(f"账号：{LOCAL_USER_DISPLAY_NAME}", flush=True)
        if not identity.has_token:
            print("尚未绑定 Token，请在打开的页面中完成一次验证。", flush=True)
        if identity.imported_token:
            print("已从旧版本地配置导入 JLU Token。", flush=True)
        if identity.imported_companion:
            print("已从旧版本地配置导入同行人学工号。", flush=True)
        print(
            "关闭应用窗口即可退出；日志位于 "
            + str(local_data_dir() / "local.log"),
            flush=True,
        )

        if args.no_window:
            runtime.wait()
        elif args.browser:
            webbrowser.open(runtime.url)
            runtime.wait()
        elif not open_app_window(url=runtime.url):
            runtime.wait()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure_logging()
    try:
        return _run(args)
    except Exception as exc:
        _report_startup_failure(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
