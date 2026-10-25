"""Beginner-friendly source launcher for JLU Booking.

Double-click launchers call this file. It creates an isolated environment,
installs the local project when needed, and starts the desktop app.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"


def venv_python_path() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=PROJECT_DIR, check=True)


def environment_is_ready(python: Path) -> bool:
    result = subprocess.run(
        [
            str(python),
            "-c",
            "import jlu_booking, platformdirs, requests, fastapi, uvicorn, "
            "jinja2, argon2, cryptography, multipart, webview",
        ],
        cwd=PROJECT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def prepare_environment() -> Path:
    if sys.version_info < (3, 10):
        raise RuntimeError("需要 Python 3.10 或更高版本。请先升级 Python。")

    python = venv_python_path()
    if not python.exists():
        print("[1/3] 第一次启动：正在创建独立运行环境…", flush=True)
        run([sys.executable, "-m", "venv", str(VENV_DIR)])

    if not environment_is_ready(python):
        print("[2/3] 正在安装 JLU Booking 及必要组件…", flush=True)
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-e",
                f"{PROJECT_DIR}[desktop]",
            ]
        )

    return python


def main() -> int:
    try:
        python = prepare_environment()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"\n启动准备失败：{exc}", file=sys.stderr)
        print("请查看 docs/usage.md，或把这段错误信息提交到项目 Issue。", file=sys.stderr)
        return 1

    print("[3/3] 正在打开 JLU Booking…", flush=True)
    try:
        return subprocess.call(
            [str(python), "-m", "jlu_booking"],
            cwd=PROJECT_DIR,
        )
    except OSError as exc:
        print(f"无法启动图形界面：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
