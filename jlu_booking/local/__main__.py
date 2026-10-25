"""Launch the single-user desktop workspace."""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import traceback
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from ..streams import ensure_standard_streams
from .bootstrap import LOCAL_USER_DISPLAY_NAME, create_local_app, local_data_dir
from .server import LocalRuntime
from .window import open_app_window


LOG = logging.getLogger("jlu_booking.local")
LOG_FILE_NAME = "local.log"
ERROR_LOG_FILE_NAME = "startup-error.log"


def _configure_logging() -> Path:
    base = local_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    log_path = base / LOG_FILE_NAME
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    LOG.info(
        "JLU Booking 启动 | Python %s | %s | frozen=%s",
        sys.version.split()[0],
        sys.platform,
        bool(getattr(sys, "frozen", False)),
    )
    LOG.info("本机数据目录：%s", base)
    return log_path


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


def _safe_log_path() -> Path | None:
    try:
        return local_data_dir() / LOG_FILE_NAME
    except Exception:
        return None


def _write_startup_error(exc: BaseException) -> Path | None:
    """Keep the traceback somewhere the user can send us."""

    try:
        path = local_data_dir() / ERROR_LOG_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "JLU Booking startup failure\n"
            f"Python: {sys.version}\n"
            f"Platform: {sys.platform}\n"
            f"Frozen: {bool(getattr(sys, 'frozen', False))}\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        return path
    except Exception:
        return None


def _show_error_dialog(message: str) -> None:
    """Show an error without requiring a console or a working Tk install."""

    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("JLU Booking", message)
        root.destroy()
        return
    except Exception:
        pass

    if os.name == "nt":
        try:
            message_box = ctypes.windll.user32.MessageBoxW  # type: ignore[attr-defined]
            message_box.argtypes = (
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
            )
            message_box.restype = ctypes.c_int
            message_box(None, message, "JLU Booking", 0x00000010)
            return
        except Exception:
            pass

    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:
        pass


def _report_startup_failure(exc: BaseException) -> None:
    log_path = _safe_log_path()
    error_path = _write_startup_error(exc)
    message = f"启动失败：{exc}"
    if log_path is not None:
        message += f"\n\n详细日志：{log_path}"
    if error_path is not None:
        message += f"\n错误详情：{error_path}"
    message += "\n\n请把日志文件发给开发者，或提交到项目 Issue。"
    try:
        LOG.critical(
            "local desktop startup failed",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    except Exception:
        pass
    _show_error_dialog(message)


def _run(args: argparse.Namespace) -> int:
    app, settings, identity = create_local_app()
    runtime = LocalRuntime(app, settings, log_path=local_data_dir() / LOG_FILE_NAME)
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
            + str(local_data_dir() / LOG_FILE_NAME),
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
    # Must run before any print(): windowed Windows builds have no console.
    ensure_standard_streams()
    try:
        args = _parser().parse_args(argv)
        _configure_logging()
        return _run(args)
    except SystemExit:
        raise
    except Exception as exc:
        _report_startup_failure(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
