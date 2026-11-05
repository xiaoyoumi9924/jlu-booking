#!/usr/bin/env python3
"""One-command local deployment for the JLU Booking Web version.

The Web version is a normal FastAPI application; a public server deployment
needs Caddy, a domain and systemd, but a single person on one computer only
needs a loopback server plus the booking scheduler.  This tool owns that
lifecycle:

* ``install`` creates an isolated virtual environment, installs the ``web``
  extras, generates the two secret keys, writes a private env file and creates
  the admin account.
* ``start`` / ``stop`` manage the loopback Web server and the scheduler.
* ``autostart on`` installs a daily window (07:25-07:35 by default, "mode B"):
  the scheduler starts at 07:25, drains running tasks, and exits at 07:35.
* ``run-window`` is what launchd / systemd / Task Scheduler actually calls.

Only the Python standard library is used.  Everything that touches the
application goes through the project's own ``jlu-booking-web`` console script.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, time as clock_time
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

try:  # pragma: no cover - Windows only
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

try:  # pragma: no cover - POSIX only
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VENV = ROOT / ".venv-web"
DEFAULT_DATA = ROOT / "runtime" / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_START = "07:25"
DEFAULT_END = "07:35"
DEFAULT_DRAIN_SECONDS = 300
LOG_LIMIT_BYTES = 5 * 1024 * 1024
LAUNCHD_LABEL = "com.jlu-booking.window"
SYSTEMD_PREFIX = "jlu-booking-window"
WINDOWS_TASK_NAME = "JLU Booking Window"
WINDOW_LOG = "window"
TASK_STATUS_LABELS = {
    "scheduled": "已排队",
    "running": "运行中",
    "success": "成功",
    "no_result": "无结果",
    "token_invalid": "Token 失效",
    "account_blocked": "账号受限",
    "daily_limit": "超出限额",
    "submission_unknown": "结果待核对",
    "network_unavailable": "网络异常",
    "stopped": "已停止",
    "error": "错误",
    "cancelled": "已取消",
}


class DeployError(RuntimeError):
    """A recoverable deployment problem with a user-facing message."""


# ---------------------------------------------------------------------------
# Settings and small filesystem helpers


@dataclass(frozen=True)
class Settings:
    venv: Path
    data: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    window_start: str = DEFAULT_START
    window_end: str = DEFAULT_END
    drain: int = DEFAULT_DRAIN_SECONDS

    @property
    def env_file(self) -> Path:
        return self.data / "web.env"

    @property
    def secrets(self) -> Path:
        return self.data / "secrets"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def run_dir(self) -> Path:
        return self.data / "run"

    @property
    def database(self) -> Path:
        return self.data / "web.sqlite3"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    @property
    def web_pid_file(self) -> Path:
        return self.run_dir / "web.pid"

    @property
    def scheduler_pid_file(self) -> Path:
        return self.run_dir / "scheduler.pid"

    @property
    def scheduler_lock(self) -> Path:
        return self.run_dir / "scheduler.lock"

    @property
    def python(self) -> Path:
        if os.name == "nt":
            return self.venv / "Scripts" / "python.exe"
        return self.venv / "bin" / "python"

    @property
    def web_cli(self) -> Path:
        if os.name == "nt":
            return self.venv / "Scripts" / "jlu-booking-web.exe"
        return self.venv / "bin" / "jlu-booking-web"


def _chmod(path: Path, mode: int) -> None:
    if os.name == "nt":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"), mode)


def _atomic_write_bytes(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)
    _chmod(path, mode)


def ensure_layout(settings: Settings) -> None:
    for directory in (settings.data, settings.secrets, settings.logs, settings.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
        _chmod(directory, 0o700)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse the private env file (comments and ``export`` are tolerated)."""

    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def write_env_file(settings: Settings) -> None:
    content = (
        "# Generated by tools/deploy_local.py. Keep this file private.\n"
        "# It contains paths only; the actual keys live in ./secrets.\n"
        f"JLU_BOOKING_WEB_DATA_DIR={settings.data}\n"
        f"JLU_BOOKING_WEB_TOKEN_KEY_FILE={settings.secrets / 'token.key'}\n"
        f"JLU_BOOKING_WEB_BLIND_KEY_FILE={settings.secrets / 'blind.key'}\n"
        "JLU_BOOKING_WEB_COOKIE_SECURE=false\n"
        f"JLU_BOOKING_WEB_TRUSTED_PROXY={settings.host}\n"
        f"JLU_BOOKING_LOCAL_HOST={settings.host}\n"
        f"JLU_BOOKING_LOCAL_PORT={settings.port}\n"
        f"JLU_BOOKING_LOCAL_WINDOW_START={settings.window_start}\n"
        f"JLU_BOOKING_LOCAL_WINDOW_END={settings.window_end}\n"
        f"JLU_BOOKING_LOCAL_DRAIN={settings.drain}\n"
    )
    _atomic_write(settings.env_file, content, 0o600)


def load_settings(args: argparse.Namespace) -> Settings:
    data_dir = getattr(args, "data_dir", None) or os.environ.get(
        "JLU_BOOKING_DEPLOY_DATA_DIR"
    )
    data = Path(data_dir).expanduser().resolve() if data_dir else DEFAULT_DATA

    venv_arg = getattr(args, "venv", None) or os.environ.get("JLU_BOOKING_DEPLOY_VENV")
    venv = Path(venv_arg).expanduser().resolve() if venv_arg else DEFAULT_VENV

    values = read_env_file(data / "web.env")
    port = int(values.get("JLU_BOOKING_LOCAL_PORT", DEFAULT_PORT))
    if getattr(args, "port", None):
        port = int(args.port)
    return Settings(
        venv=venv,
        data=data,
        host=values.get("JLU_BOOKING_LOCAL_HOST", DEFAULT_HOST),
        port=port,
        window_start=values.get("JLU_BOOKING_LOCAL_WINDOW_START", DEFAULT_START),
        window_end=values.get("JLU_BOOKING_LOCAL_WINDOW_END", DEFAULT_END),
        drain=int(values.get("JLU_BOOKING_LOCAL_DRAIN", DEFAULT_DRAIN_SECONDS)),
    )


def app_environment(settings: Settings) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(read_env_file(settings.env_file))
    environment.update(
        {
            "JLU_BOOKING_WEB_DATA_DIR": str(settings.data),
            "JLU_BOOKING_WEB_TOKEN_KEY_FILE": str(settings.secrets / "token.key"),
            "JLU_BOOKING_WEB_BLIND_KEY_FILE": str(settings.secrets / "blind.key"),
            "JLU_BOOKING_WEB_COOKIE_SECURE": "false",
            "JLU_BOOKING_WEB_TRUSTED_PROXY": settings.host,
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


# ---------------------------------------------------------------------------
# Process helpers


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows only
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if not pid_alive(pid):
        path.unlink(missing_ok=True)
        return None
    return pid


def terminate(pid: int, *, timeout: float = 40.0) -> None:
    """Ask nicely, then kill the whole process group after the deadline."""

    if not pid_alive(pid):
        return
    if os.name == "nt":  # pragma: no cover - Windows only
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.2)
    if os.name == "nt":  # pragma: no cover - Windows only
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def stop_owned_child(child: subprocess.Popen, *, timeout: float = 45.0) -> None:
    """Stop a child we spawned and reap it, so no zombie keeps us waiting."""

    if child.poll() is not None:
        return
    if os.name == "nt":  # pragma: no cover - Windows only
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            child.terminate()
        except OSError:
            return
    try:
        child.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":  # pragma: no cover - Windows only
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def spawn_background(
    command: list[str],
    *,
    environment: dict[str, str],
    log_path: Path,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab", buffering=0)
    options: dict[str, object] = {
        "cwd": str(ROOT),
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":  # pragma: no cover - Windows only
        options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **options)  # type: ignore[arg-type]
    finally:
        handle.close()
    return int(process.pid)


def write_pid(path: Path, pid: int) -> None:
    _atomic_write(path, f"{pid}\n", 0o600)


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def server_reachable(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 500
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False


def wait_until_reachable(url: str, *, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_reachable(url):
            return True
        time.sleep(0.25)
    return False


def rotate_logs(settings: Settings) -> None:
    for path in settings.logs.glob("*.log"):
        try:
            if path.stat().st_size <= LOG_LIMIT_BYTES:
                continue
            backup = path.with_name(path.name + ".1")
            backup.unlink(missing_ok=True)
            path.rename(backup)
        except OSError:
            continue


def log_message(settings: Settings, name: str, message: str) -> None:
    settings.logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}\n"
    with open(settings.logs / f"{name}.log", "a", encoding="utf-8") as handle:
        handle.write(line)
    try:
        print(line.rstrip(), flush=True)
    except (OSError, ValueError):
        pass


def tail_lines(path: Path, count: int) -> list[str]:
    if not path.is_file():
        return []
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        block = 8192
        data = b""
        while size > 0 and data.count(b"\n") <= count:
            step = min(block, size)
            size -= step
            handle.seek(size)
            data = handle.read(step) + data
    return data.decode("utf-8", "replace").splitlines()[-count:]


# ---------------------------------------------------------------------------
# Time window


def parse_clock(value: str) -> clock_time:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value))
    if not match:
        raise DeployError(f"时间格式应为 HH:MM，例如 07:25：{value!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise DeployError(f"时间超出范围：{value!r}")
    return clock_time(hour, minute)


def validate_window(start: str, end: str) -> tuple[clock_time, clock_time]:
    start_at = parse_clock(start)
    end_at = parse_clock(end)
    if end_at <= start_at:
        raise DeployError("窗口结束时间必须晚于开始时间。")
    return start_at, end_at


# ---------------------------------------------------------------------------
# Scheduler lock (one scheduler per data directory)


def acquire_scheduler_lock(settings: Settings):
    ensure_layout(settings)
    handle = open(settings.scheduler_lock, "a+b")
    try:
        if msvcrt is not None:  # pragma: no cover - Windows only
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def scheduler_active(settings: Settings) -> bool:
    """A scheduler counts as running if it owns the lock, even without a pid file."""

    if read_pid(settings.scheduler_pid_file) is not None:
        return True
    try:
        lock = acquire_scheduler_lock(settings)
    except OSError:
        return False
    if lock is None:
        return True
    lock.close()
    return False


def open_tasks(settings: Settings) -> int:
    if not settings.database.is_file():
        return 0
    try:
        connection = sqlite3.connect(
            f"file:{settings.database}?mode=ro", uri=True, timeout=2.0
        )
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM booking_tasks "
                "WHERE status IN ('scheduled', 'running')"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def recent_tasks(settings: Settings, limit: int = 5) -> list[tuple]:
    if not settings.database.is_file():
        return []
    try:
        connection = sqlite3.connect(
            f"file:{settings.database}?mode=ro", uri=True, timeout=2.0
        )
        try:
            return list(
                connection.execute(
                    "SELECT id, status, venue, sport, execution_date, updated_at "
                    "FROM booking_tasks ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            )
        finally:
            connection.close()
    except sqlite3.Error:
        return []


# ---------------------------------------------------------------------------
# Install


def _run(command: list[str], *, env: dict[str, str] | None = None, capture: bool = False):
    return subprocess.run(
        command,
        cwd=str(ROOT),
        env=env,
        check=False,
        text=True,
        capture_output=capture,
    )


def ensure_python_version() -> None:
    if sys.version_info < (3, 10):
        raise DeployError(
            f"需要 Python 3.10 或更高版本，当前是 {sys.version.split()[0]}。"
        )


def ensure_venv(settings: Settings) -> None:
    if settings.python.exists():
        print(f"[1/5] 运行环境已就绪：{settings.venv}")
        return
    print(f"[1/5] 创建独立运行环境：{settings.venv}")
    result = _run([sys.executable, "-m", "venv", str(settings.venv)])
    if result.returncode != 0 or not settings.python.exists():
        raise DeployError("创建虚拟环境失败，请检查 Python 安装是否完整。")


WEB_IMPORTS = (
    "import fastapi, uvicorn, jinja2, argon2, cryptography, multipart, httpx, "
    "platformdirs, requests"
)


def web_dependencies_ready(settings: Settings) -> bool:
    if not settings.python.is_file() or not settings.web_cli.is_file():
        return False
    result = _run([str(settings.python), "-c", WEB_IMPORTS], capture=True)
    return result.returncode == 0


def install_web_dependencies(settings: Settings, *, force: bool, index_url: str | None) -> None:
    if web_dependencies_ready(settings) and not force:
        print("[2/5] Web 依赖已就绪")
        return
    print("[2/5] 安装 Web 依赖（首次需要联网下载，可能要几分钟）…")
    command = [
        str(settings.python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-e",
        ".[web]",
    ]
    if index_url:
        command.extend(["--index-url", index_url])
    result = _run(command)
    if result.returncode != 0 or not settings.web_cli.is_file():
        raise DeployError(
            "安装依赖失败。可检查网络后重试，或加 --index-url 使用国内镜像："
            "python3 tools/deploy_local.py install --index-url https://pypi.tuna.tsinghua.edu.cn/simple"
        )


def ensure_keys(settings: Settings) -> None:
    token_key = settings.secrets / "token.key"
    blind_key = settings.secrets / "blind.key"
    if token_key.is_file() and blind_key.is_file():
        print("[3/5] 加密密钥已存在")
        _chmod(token_key, 0o600)
        _chmod(blind_key, 0o600)
        return
    if token_key.exists() or blind_key.exists():
        raise DeployError(
            f"密钥目录不完整（{settings.secrets}）。请先备份并删除该目录后重试。"
        )
    print("[3/5] 生成加密密钥")
    result = _run(
        [str(settings.web_cli), "generate-keys", "--directory", str(settings.secrets)],
        env=app_environment(settings),
        capture=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise DeployError(f"生成密钥失败：{detail}")
    _chmod(settings.secrets, 0o700)
    _chmod(token_key, 0o600)
    _chmod(blind_key, 0o600)


def invite_code() -> str:
    auth_file = ROOT / "jlu_booking" / "web" / "routes" / "auth.py"
    try:
        text = auth_file.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'REGISTRATION_INVITE_CODE\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


def print_next_steps(settings: Settings) -> None:
    print()
    print("部署完成。")
    print(f"  数据目录：{settings.data}")
    print(f"  虚拟环境：{settings.venv}")
    print(f"  访问地址：{settings.url}")
    print()
    print("本机单用户模式：不需要登录或注册，打开就是本机账号。")
    print("首次使用：")
    print("  1. 按页面提示粘贴场馆网址，完成 Token 绑定")
    print("  2. 到“个人中心”验证同行人学工号")
    print("  3. 在“自动预约”里打开每日开关并保存配置")
    print()
    print(
        f"每日窗口：{settings.window_start}–{settings.window_end}。"
        "up 会自动启用；单独 install 后可用 autostart on 启用。"
    )
    print("查看状态：./jlu status")
    if os.name != "nt" and shutil.which("jlu") is None:
        print("提示：运行 ./jlu link 可把 jlu 安装到 PATH，以后直接敲 jlu。")


def cmd_install(args: argparse.Namespace) -> int:
    ensure_python_version()
    data_arg = (
        getattr(args, "data_dir", None)
        or os.environ.get("JLU_BOOKING_DEPLOY_DATA_DIR")
        or DEFAULT_DATA
    )
    venv_arg = (
        getattr(args, "venv", None)
        or os.environ.get("JLU_BOOKING_DEPLOY_VENV")
        or DEFAULT_VENV
    )
    data = Path(data_arg).expanduser().resolve()
    venv = Path(venv_arg).expanduser().resolve()
    existing = read_env_file(data / "web.env")

    host = getattr(args, "host", None) or existing.get(
        "JLU_BOOKING_LOCAL_HOST", DEFAULT_HOST
    )
    port = getattr(args, "port", None) or int(
        existing.get("JLU_BOOKING_LOCAL_PORT", DEFAULT_PORT)
    )
    start = getattr(args, "start", None) or existing.get(
        "JLU_BOOKING_LOCAL_WINDOW_START", DEFAULT_START
    )
    end = getattr(args, "end", None) or existing.get(
        "JLU_BOOKING_LOCAL_WINDOW_END", DEFAULT_END
    )
    drain = getattr(args, "drain", None)
    if drain is None:
        drain = int(existing.get("JLU_BOOKING_LOCAL_DRAIN", DEFAULT_DRAIN_SECONDS))
    if int(drain) < 0:
        raise DeployError("--drain 不能是负数。")
    validate_window(start, end)

    settings = Settings(
        venv=venv,
        data=data,
        host=host,
        port=int(port),
        window_start=start,
        window_end=end,
        drain=int(drain),
    )
    ensure_layout(settings)
    ensure_venv(settings)
    install_web_dependencies(
        settings,
        force=bool(getattr(args, "force_install", False)),
        index_url=getattr(args, "index_url", None),
    )
    ensure_keys(settings)
    print("[4/5] 单机模式：无需登录注册，打开时自动创建本机账号")
    write_env_file(settings)
    print("[5/5] 私有配置已写入")
    print_next_steps(settings)
    return 0


# ---------------------------------------------------------------------------
# Start / stop / status


def require_installed(settings: Settings) -> None:
    if not settings.python.is_file() or not settings.web_cli.is_file():
        raise DeployError(
            "尚未安装。先运行：python3 tools/deploy_local.py install"
            "（或双击 deploy.command / deploy.bat）"
        )
    if not settings.env_file.is_file():
        raise DeployError("缺少私有配置，请重新运行 install。")


def start_web(settings: Settings, *, open_browser: bool = False) -> int:
    pid = read_pid(settings.web_pid_file)
    if pid is not None:
        print(f"Web 已在运行：{settings.url}（pid {pid}）")
        if open_browser:
            import webbrowser

            webbrowser.open(settings.url)
        return pid

    if port_in_use(settings.host, settings.port):
        raise DeployError(
            f"端口 {settings.port} 已被占用。请先停止占用进程，"
            "或在 install 时用 --port 换一个端口。"
        )

    rotate_logs(settings)
    command = [
        str(settings.web_cli),
        "serve",
        "--local",
        "--host",
        settings.host,
        "--port",
        str(settings.port),
    ]
    pid = spawn_background(
        command,
        environment=app_environment(settings),
        log_path=settings.logs / "web.log",
    )
    write_pid(settings.web_pid_file, pid)
    if not wait_until_reachable(settings.url):
        terminate(pid, timeout=10)
        settings.web_pid_file.unlink(missing_ok=True)
        detail = "\n".join(tail_lines(settings.logs / "web.log", 20))
        raise DeployError(f"Web 启动失败。日志：\n{detail}")
    print(f"Web 已启动：{settings.url}（pid {pid}）")
    if open_browser:
        import webbrowser

        webbrowser.open(settings.url)
    return pid


def start_scheduler(settings: Settings) -> int:
    pid = read_pid(settings.scheduler_pid_file)
    if pid is not None:
        print(f"调度器已在运行（pid {pid}）")
        return pid
    if scheduler_active(settings):
        print("已有调度器在运行（可能来自每日窗口），本次不重复启动。")
        return 0
    rotate_logs(settings)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--data-dir",
        str(settings.data),
        "--venv",
        str(settings.venv),
        "serve-scheduler",
    ]
    pid = spawn_background(
        command,
        environment=app_environment(settings),
        log_path=settings.logs / "scheduler.log",
    )
    write_pid(settings.scheduler_pid_file, pid)
    time.sleep(0.5)
    if not pid_alive(pid):
        settings.scheduler_pid_file.unlink(missing_ok=True)
        if scheduler_active(settings):
            print("已有调度器在运行（可能来自每日窗口），本次不重复启动。")
            return 0
        detail = "\n".join(tail_lines(settings.logs / "scheduler.log", 20))
        raise DeployError(f"调度器启动失败。\n{detail}")
    print(f"调度器已启动（pid {pid}）")
    return pid


def cmd_start(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    require_installed(settings)
    ensure_layout(settings)
    start_web(settings, open_browser=bool(getattr(args, "open", False)))
    if not getattr(args, "web_only", False):
        start_scheduler(settings)
    return 0


def stop_web(settings: Settings) -> bool:
    pid = read_pid(settings.web_pid_file)
    if pid is None:
        settings.web_pid_file.unlink(missing_ok=True)
        return False
    print(f"正在停止 Web（pid {pid}）…")
    terminate(pid, timeout=20)
    settings.web_pid_file.unlink(missing_ok=True)
    return True


def stop_scheduler(settings: Settings) -> bool:
    pid = read_pid(settings.scheduler_pid_file)
    if pid is None:
        settings.scheduler_pid_file.unlink(missing_ok=True)
        return False
    print(f"正在停止调度器（pid {pid}）…")
    terminate(pid, timeout=45)
    settings.scheduler_pid_file.unlink(missing_ok=True)
    return True


def cmd_stop(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    stopped = stop_scheduler(settings)
    stopped = stop_web(settings) or stopped
    print("已停止。" if stopped else "当前没有运行中的进程。")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    cmd_stop(args)
    time.sleep(0.5)
    return cmd_start(args)


def cmd_status(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    installed = settings.web_cli.is_file() and settings.env_file.is_file()
    web_pid = read_pid(settings.web_pid_file)
    scheduler_pid = read_pid(settings.scheduler_pid_file)
    scheduler_running = bool(scheduler_pid) or scheduler_active(settings)

    print("JLU Booking 本地 Web 版")
    print(f"  数据目录：{settings.data}")
    print(f"  运行环境：{'已安装' if installed else '未安装'}")
    if installed:
        web_state = (
            f"运行中 {settings.url}（pid {web_pid}）" if web_pid else "未运行"
        )
        print(f"  Web：{web_state}")
        if web_pid and not server_reachable(settings.url):
            print("        （进程在，但页面暂不可访问，稍等几秒）")
        if scheduler_pid:
            scheduler_state = f"运行中（pid {scheduler_pid}）"
        elif scheduler_running:
            scheduler_state = "运行中（每日窗口）"
        else:
            scheduler_state = "未运行"
        print(f"  调度器：{scheduler_state}")
        print(
            f"  每日窗口：{settings.window_start}–{settings.window_end}"
            f"（结束后最多再等 {settings.drain // 60} 分钟排空运行中的任务）"
        )
        keys_ok = (settings.secrets / "token.key").is_file() and (
            settings.secrets / "blind.key"
        ).is_file()
        print(f"  加密密钥：{'已生成' if keys_ok else '缺失'}")
        _print_autostart_status(settings, indent="  ")
        tasks = recent_tasks(settings)
        if tasks:
            print("  最近任务：")
            for task_id, status, venue, sport, day, _updated in tasks:
                label = TASK_STATUS_LABELS.get(status, status)
                print(f"    #{task_id} {day} {venue} {sport}：{label}")
    else:
        print("  提示：运行 install 完成安装。")
    return 0


# ---------------------------------------------------------------------------
# Logs and backup


def cmd_logs(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    names = ["web", "scheduler", "window"]
    if getattr(args, "name", None):
        names = [args.name]
    elif not getattr(args, "all", False):
        names = ["web"]
    if getattr(args, "follow", False) and len(names) != 1:
        raise DeployError("--follow 只能配合单个 --name 使用。")

    count = int(getattr(args, "lines", 50) or 50)
    for name in names:
        path = settings.logs / f"{name}.log"
        print(f"== {name} ({path}) ==")
        lines = tail_lines(path, count)
        if lines:
            print("\n".join(lines))
        else:
            print("（暂无日志）")
        print()

    if getattr(args, "follow", False):
        path = settings.logs / f"{names[0]}.log"
        print(f"正在跟踪 {path}（Ctrl-C 退出）")
        position = path.stat().st_size if path.is_file() else 0
        try:
            while True:
                if path.is_file():
                    with open(path, "rb") as handle:
                        handle.seek(position)
                        chunk = handle.read()
                        position = handle.tell()
                    if chunk:
                        sys.stdout.write(chunk.decode("utf-8", "replace"))
                        sys.stdout.flush()
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    require_installed(settings)
    result = _run(
        [str(settings.web_cli), "backup"],
        env=app_environment(settings),
        capture=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise DeployError(f"备份失败：{detail}")
    print((result.stdout or "").strip())
    return 0


# ---------------------------------------------------------------------------
# Daily window supervision


def cmd_serve_scheduler(args: argparse.Namespace) -> int:
    """Hold the single-scheduler lock, then run the real scheduler."""

    settings = load_settings(args)
    require_installed(settings)
    lock = acquire_scheduler_lock(settings)
    if lock is None:
        log_message(settings, WINDOW_LOG, "已有调度器在运行，本次窗口跳过。")
        return 0

    environment = app_environment(settings)
    rotate_logs(settings)
    log_path = settings.logs / "scheduler.log"
    handle = open(log_path, "ab", buffering=0)
    process = subprocess.Popen(
        [str(settings.web_cli), "scheduler"],
        cwd=str(ROOT),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    handle.close()

    stopping = False

    def request_stop(signum, _frame):  # noqa: ANN001 - signal signature
        nonlocal stopping
        if not stopping:
            stopping = True
            try:
                process.terminate()
            except OSError:
                pass

    previous = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, request_stop)
    try:
        code = process.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        try:
            lock.close()
        except Exception:
            pass
    return int(code or 0)


def cmd_run_window(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    require_installed(settings)
    ensure_layout(settings)

    now = datetime.now()
    start_at, end_at = validate_window(settings.window_start, settings.window_end)
    drain_seconds = settings.drain
    if getattr(args, "drain", None) is not None:
        drain_seconds = max(0, int(args.drain))

    if getattr(args, "now", False):
        end = now + timedelta(minutes=max(1, int(getattr(args, "minutes", 2) or 2)))
        log_message(settings, WINDOW_LOG, f"手动窗口：立即开始，{end:%H:%M} 停止。")
    else:
        start = datetime.combine(now.date(), start_at)
        end = datetime.combine(now.date(), end_at)
        if now < start:
            log_message(
                settings,
                WINDOW_LOG,
                f"{start:%H:%M} 前不启动，等待中。",
            )
            while datetime.now() < start:
                time.sleep(min(5.0, (start - datetime.now()).total_seconds()))
        if datetime.now() > end:
            log_message(
                settings,
                WINDOW_LOG,
                f"已错过 {settings.window_start}-{settings.window_end} 窗口，跳过本次。",
            )
            return 0
        log_message(
            settings,
            WINDOW_LOG,
            f"窗口开始（{settings.window_start}-{settings.window_end}）。",
        )

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--data-dir",
        str(settings.data),
        "--venv",
        str(settings.venv),
        "serve-scheduler",
    ]
    rotate_logs(settings)
    log_path = settings.logs / "scheduler.log"
    handle = open(log_path, "ab", buffering=0)
    options: dict[str, object] = {
        "cwd": str(ROOT),
        "env": app_environment(settings),
        "stdin": subprocess.DEVNULL,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":  # pragma: no cover - Windows only
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    child = subprocess.Popen(command, **options)  # type: ignore[arg-type]
    handle.close()

    try:
        while True:
            if child.poll() is not None:
                log_message(
                    settings,
                    WINDOW_LOG,
                    "调度器已自行退出（可能有另一个调度器在运行）。",
                )
                return 0
            remaining = (end - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(5.0, remaining))

        drain_deadline = datetime.now() + timedelta(seconds=drain_seconds)
        while datetime.now() < drain_deadline:
            if open_tasks(settings) == 0:
                break
            time.sleep(5.0)

        log_message(settings, WINDOW_LOG, "窗口结束，正在停止调度器。")
    finally:
        stop_owned_child(child)
    log_message(settings, WINDOW_LOG, "调度器已退出。")
    return 0


# ---------------------------------------------------------------------------
# Autostart: launchd / systemd --user / Task Scheduler


def build_launchd_plist(settings: Settings, python: Path, script: Path) -> bytes:
    start_at, _end_at = validate_window(settings.window_start, settings.window_end)
    payload = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            str(python),
            str(script),
            "--data-dir",
            str(settings.data),
            "--venv",
            str(settings.venv),
            "run-window",
        ],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": {"Hour": start_at.hour, "Minute": start_at.minute},
        "RunAtLoad": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(settings.logs / "launchd.log"),
        "StandardErrorPath": str(settings.logs / "launchd.log"),
    }
    return plistlib.dumps(payload)


def build_systemd_units(settings: Settings, python: Path, script: Path) -> dict[str, str]:
    start_at, _end_at = validate_window(settings.window_start, settings.window_end)
    service = (
        "[Unit]\n"
        "Description=JLU Booking scheduler window\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={ROOT}\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        f'ExecStart="{python}" "{script}" --data-dir "{settings.data}" '
        f'--venv "{settings.venv}" run-window\n'
        "TimeoutStartSec=1800\n"
    )
    timer = (
        "[Unit]\n"
        f"Description=Run the JLU Booking scheduler at {settings.window_start}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {start_at.hour:02d}:{start_at.minute:02d}:00\n"
        "Persistent=true\n"
        f"Unit={SYSTEMD_PREFIX}.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {
        f"{SYSTEMD_PREFIX}.service": service,
        f"{SYSTEMD_PREFIX}.timer": timer,
    }


def build_windows_task_xml(
    settings: Settings, python: Path, script: Path, user: str
) -> str:
    start_at, _end_at = validate_window(settings.window_start, settings.window_end)
    command = xml_escape(str(python))
    arguments = xml_escape(
        f'"{script}" --data-dir "{settings.data}" --venv "{settings.venv}" run-window'
    )
    description = xml_escape("JLU Booking scheduler window (07:25-07:35)")
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        f"    <Description>{description}</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <CalendarTrigger>\n"
        f"      <StartBoundary>2026-01-01T{start_at.hour:02d}:{start_at.minute:02d}:00</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <ScheduleByDay>\n"
        "        <DaysInterval>1</DaysInterval>\n"
        "      </ScheduleByDay>\n"
        "    </CalendarTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{xml_escape(user)}</UserId>\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{command}</Command>\n"
        f"      <Arguments>{arguments}</Arguments>\n"
        f"      <WorkingDirectory>{xml_escape(str(ROOT))}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def _autostart_targets(settings: Settings) -> Path | None:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    if os.name == "posix":
        return Path.home() / ".config" / "systemd" / "user" / f"{SYSTEMD_PREFIX}.timer"
    return None


def autostart_enabled(settings: Settings) -> bool:
    try:
        if sys.platform == "darwin":
            plist = _autostart_targets(settings)
            if plist is None or not plist.is_file():
                return False
            result = _run(
                ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                capture=True,
            )
            return result.returncode == 0
        if os.name == "posix":
            result = _run(
                ["systemctl", "--user", "is-enabled", f"{SYSTEMD_PREFIX}.timer"],
                capture=True,
            )
            return result.returncode == 0
        result = _run(["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME], capture=True)
        return result.returncode == 0
    except OSError:
        return False


def _print_autostart_status(settings: Settings, indent: str = "") -> None:
    detail = f"每天 {settings.window_start} 自动运行"
    if autostart_enabled(settings):
        print(f"{indent}自动运行：已启用（{detail}）")
    else:
        print(f"{indent}自动运行：未启用（可用 autostart on 开启）")


def install_autostart(settings: Settings) -> None:
    ensure_layout(settings)
    python = settings.python
    script = Path(__file__).resolve()
    if sys.platform == "darwin":
        plist = _autostart_targets(settings)
        try:
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_bytes(build_launchd_plist(settings, python, script))
        except OSError as exc:
            raise DeployError(
                f"无法写入 {plist}：{exc}\n"
                "请确认当前用户对 ~/Library/LaunchAgents 有写权限。"
            ) from exc
        _chmod(plist, 0o644)
        uid = os.getuid()
        try:
            _run(["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"], capture=True)
            result = _run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture=True
            )
            if result.returncode != 0:
                result = _run(["launchctl", "load", "-w", str(plist)], capture=True)
        except OSError as exc:
            raise DeployError(f"无法运行 launchctl：{exc}") from exc
        if result.returncode != 0:
            raise DeployError(
                "launchd 加载失败：" + (result.stderr or result.stdout or "").strip()
            )
        print(f"已启用自动运行：每天 {settings.window_start} 启动，{settings.window_end} 停止。")
        print(f"  LaunchAgent：{plist}")
        return

    if os.name == "posix":
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        try:
            unit_dir.mkdir(parents=True, exist_ok=True)
            for name, content in build_systemd_units(settings, python, script).items():
                _atomic_write(unit_dir / name, content, 0o644)
        except OSError as exc:
            raise DeployError(
                f"无法写入 systemd 用户单元 {unit_dir}：{exc}"
            ) from exc
        try:
            _run(["systemctl", "--user", "daemon-reload"], capture=True)
            result = _run(
                ["systemctl", "--user", "enable", "--now", f"{SYSTEMD_PREFIX}.timer"],
                capture=True,
            )
        except OSError as exc:
            raise DeployError(
                f"未找到 systemctl，无法安装用户定时器：{exc}"
            ) from exc
        if result.returncode != 0:
            raise DeployError(
                "systemd 启用失败：" + (result.stderr or result.stdout or "").strip()
            )
        print(f"已启用自动运行：每天 {settings.window_start} 启动，{settings.window_end} 停止。")
        print(f"  用户定时器：{SYSTEMD_PREFIX}.timer")
        print("  提示：注销后仍需运行，可执行 sudo loginctl enable-linger $USER")
        return

    # Windows
    xml_path = settings.run_dir / "scheduled-task.xml"
    task_python = python
    pythonw = python.with_name("pythonw.exe")
    if pythonw.is_file():
        task_python = pythonw  # no console window at 07:25
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    account = f"{domain}\\{user}" if domain else user
    content = build_windows_task_xml(settings, task_python, script, account)
    # Task Scheduler expects UTF-16 with a BOM.
    try:
        _atomic_write_bytes(xml_path, content.encode("utf-16"), 0o600)
    except OSError as exc:
        raise DeployError(f"无法写入计划任务定义 {xml_path}：{exc}") from exc
    try:
        result = _run(
            ["schtasks", "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", str(xml_path), "/F"],
            capture=True,
        )
    except OSError as exc:
        raise DeployError(f"无法运行 schtasks：{exc}") from exc
    if result.returncode != 0:
        raise DeployError(
            "创建计划任务失败：" + (result.stderr or result.stdout or "").strip()
        )
    print(f"已启用自动运行：每天 {settings.window_start} 启动，{settings.window_end} 停止。")
    print(f"  计划任务：{WINDOWS_TASK_NAME}")


def remove_autostart(settings: Settings) -> None:
    if sys.platform == "darwin":
        plist = _autostart_targets(settings)
        try:
            _run(
                ["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                capture=True,
            )
        except OSError:
            pass
        if plist is not None:
            try:
                plist.unlink(missing_ok=True)
            except OSError as exc:
                raise DeployError(f"无法删除 {plist}：{exc}") from exc
        print("已关闭自动运行。")
        return

    if os.name == "posix":
        try:
            _run(
                ["systemctl", "--user", "disable", "--now", f"{SYSTEMD_PREFIX}.timer"],
                capture=True,
            )
        except OSError:
            pass
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        for name in (f"{SYSTEMD_PREFIX}.service", f"{SYSTEMD_PREFIX}.timer"):
            try:
                (unit_dir / name).unlink(missing_ok=True)
            except OSError as exc:
                raise DeployError(f"无法删除 {unit_dir / name}：{exc}") from exc
        _run(["systemctl", "--user", "daemon-reload"], capture=True)
        print("已关闭自动运行。")
        return

    try:
        _run(
            ["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"],
            capture=True,
        )
    except OSError:
        pass
    print("已关闭自动运行。")


def cmd_autostart(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    action = getattr(args, "action", "status") or "status"

    if getattr(args, "dry_run", False):
        python = settings.python
        script = Path(__file__).resolve()
        if sys.platform == "darwin":
            sys.stdout.write(build_launchd_plist(settings, python, script).decode())
        elif os.name == "posix":
            for name, content in build_systemd_units(settings, python, script).items():
                print(f"# {name}")
                print(content)
        else:
            user = os.environ.get("USERNAME", "")
            domain = os.environ.get("USERDOMAIN", "")
            print(build_windows_task_xml(settings, python, script, f"{domain}\\{user}"))
        return 0

    if action == "status":
        _print_autostart_status(settings)
        return 0

    require_installed(settings)
    validate_window(settings.window_start, settings.window_end)
    if action == "on":
        install_autostart(settings)
    elif action == "off":
        remove_autostart(settings)
    else:
        raise DeployError(f"未知动作：{action}")
    return 0


# ---------------------------------------------------------------------------
# Uninstall


def cmd_uninstall(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    stop_scheduler(settings)
    stop_web(settings)
    try:
        remove_autostart(settings)
    except DeployError:
        pass
    if getattr(args, "purge", False):
        if settings.data.exists():
            shutil.rmtree(settings.data)
            print(f"已删除数据目录：{settings.data}")
    if getattr(args, "remove_venv", False):
        if settings.venv.exists():
            shutil.rmtree(settings.venv)
            print(f"已删除虚拟环境：{settings.venv}")
    print("卸载完成。" + ("（数据目录已保留）" if not getattr(args, "purge", False) else ""))
    return 0


# ---------------------------------------------------------------------------
# Global `jlu` command


def _path_contains(directory: Path) -> bool:
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        if not raw:
            continue
        try:
            if Path(raw).expanduser().resolve() == directory:
                return True
        except OSError:
            continue
    return False


def _prepare_directory(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(directory, os.W_OK)


def _launcher_candidates() -> list[Path]:
    override = os.environ.get("JLU_BOOKING_BIN_DIR")
    if override:
        return [Path(override).expanduser().resolve()]
    home = Path.home()
    ordered = [home / ".local" / "bin", home / "bin"]
    if sys.platform == "darwin":
        ordered.append(Path("/opt/homebrew/bin"))
    ordered.append(Path("/usr/local/bin"))
    return [path for path in ordered if _path_contains(path)]


def _shell_rc_path() -> Path:
    shell = os.path.basename(os.environ.get("SHELL", ""))
    home = Path.home()
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "bash":
        return home / ".bashrc" if sys.platform != "darwin" else home / ".bash_profile"
    return home / ".profile"


def _append_path_block(directory: Path, rc_path: Path | None = None) -> bool:
    """Add the directory to PATH once, guarded by markers."""

    rc = rc_path or _shell_rc_path()
    marker = "# >>> JLU Booking launcher >>>"
    try:
        existing = rc.read_text(encoding="utf-8") if rc.is_file() else ""
    except OSError:
        return False
    if marker in existing:
        return True
    home_bin = Path.home() / ".local" / "bin"
    shown = "$HOME/.local/bin" if directory == home_bin else str(directory)
    block = (
        f"\n{marker}\n"
        f'export PATH="{shown}:$PATH"\n'
        "# <<< JLU Booking launcher <<<\n"
    )
    try:
        with rc.open("a", encoding="utf-8") as handle:
            handle.write(block)
    except OSError:
        return False
    return True


def _install_posix_launcher() -> tuple[Path, bool, bool]:
    source = ROOT / "jlu"
    if not source.is_file():
        raise DeployError("找不到 jlu 启动器，无法安装命令。")
    _chmod(source, 0o755)

    for directory in _launcher_candidates():
        if not _prepare_directory(directory):
            continue
        destination = directory / "jlu"
        if destination.is_symlink() or destination.exists():
            if destination.is_symlink() and destination.resolve() == source.resolve():
                return destination, _path_contains(directory), False
            continue
        try:
            destination.symlink_to(source)
        except OSError:
            continue
        _chmod(destination, 0o755)
        return destination, _path_contains(directory), True

    fallback = Path.home() / ".local" / "bin"
    if not _prepare_directory(fallback):
        raise DeployError("找不到可写目录来安装 jlu 命令。")
    destination = fallback / "jlu"
    if destination.is_symlink() or destination.exists():
        if destination.is_symlink() and destination.resolve() == source.resolve():
            return destination, _path_contains(fallback), False
        raise DeployError(f"{destination} 已存在且不是本项目的链接。")
    destination.symlink_to(source)
    _chmod(destination, 0o755)
    return destination, _path_contains(fallback), True


def cmd_link(args: argparse.Namespace) -> int:
    """Install a global ``jlu`` command that opens the local Web app."""

    if os.name == "nt":
        print("Windows 请双击 deploy.bat，或重新运行快速部署命令。")
        return 0

    destination, in_path, created = _install_posix_launcher()
    quiet = bool(getattr(args, "quiet", False))
    if created or not quiet:
        print(f"已安装命令：{destination}")
        print("以后在终端直接运行：jlu")

    if not in_path:
        rc = _shell_rc_path()
        if getattr(args, "auto_path", False) and _append_path_block(
            destination.parent, rc
        ):
            print(f"已把 {destination.parent} 加入 PATH（{rc}），重新打开终端后生效。")
        else:
            print(f"把下面一行加到 {rc} 后重新打开终端：")
            print(f'export PATH="{destination.parent}:$PATH"')
    return 0


# ---------------------------------------------------------------------------
# One-command entry point


def cmd_open(args: argparse.Namespace) -> int:
    """Everyday entry point: start what is missing, then open the browser."""

    settings = load_settings(args)
    if not (settings.web_cli.is_file() and settings.env_file.is_file()):
        print("尚未部署，开始首次部署…")
        return cmd_up(args)
    ensure_layout(settings)
    start_web(settings, open_browser=True)
    if not getattr(args, "web_only", False):
        start_scheduler(settings)
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    code = cmd_install(args)
    if code != 0:
        return code
    settings = load_settings(args)
    if not getattr(args, "no_autostart", False):
        validate_window(settings.window_start, settings.window_end)
        try:
            install_autostart(settings)
        except DeployError as exc:
            # A locked-down machine can still use the Web UI on demand even if
            # the system scheduler refuses the job; do not abort the launch.
            print(f"警告：每日自动运行未安装：{exc}")
            print("Web 仍会启动；稍后可重试：python3 tools/deploy_local.py autostart on")
    args.open = not bool(getattr(args, "no_open", False))
    code = cmd_start(args)
    if code == 0:
        print()
        print("当前 Web 与调度器已启动，方便你完成注册和配置。")
        print("配置完成后可执行 stop；之后每天 07:25 由系统自动启动，07:35 退出。")
    return code


# ---------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deploy_local.py",
        description="JLU Booking Web 版本地一键部署（默认每天 07:25-07:35 运行）。",
    )
    parser.add_argument("--data-dir", type=Path, help="数据目录（默认 runtime/web）")
    parser.add_argument("--venv", type=Path, help="虚拟环境目录（默认 .venv-web）")
    sub = parser.add_subparsers(dest="command", required=True)

    open_cmd = sub.add_parser(
        "open", help="打开本机 Web 版（首次运行会自动部署）"
    )
    open_cmd.add_argument("--port", type=int, help="临时覆盖端口")
    open_cmd.add_argument("--web-only", action="store_true", help="只启动 Web")
    open_cmd.set_defaults(handler=cmd_open)

    up = sub.add_parser("up", help="一键部署并启动")
    _install_arguments(up)
    up.add_argument("--no-autostart", action="store_true", help="不安装每日自动运行")
    up.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    up.add_argument("--web-only", action="store_true", help="只启动 Web，不启动调度器")
    up.set_defaults(handler=cmd_up)

    install = sub.add_parser("install", help="只做安装，不启动")
    _install_arguments(install)
    install.set_defaults(handler=cmd_install)

    start = sub.add_parser("start", help="启动 Web 与调度器")
    start.add_argument("--open", action="store_true", help="启动后打开浏览器")
    start.add_argument("--web-only", action="store_true", help="只启动 Web")
    start.add_argument("--port", type=int, help="临时覆盖端口")
    start.set_defaults(handler=cmd_start)

    stop = sub.add_parser("stop", help="停止 Web 与调度器")
    stop.set_defaults(handler=cmd_stop)

    restart = sub.add_parser("restart", help="重启")
    restart.add_argument("--open", action="store_true")
    restart.add_argument("--web-only", action="store_true")
    restart.add_argument("--port", type=int)
    restart.set_defaults(handler=cmd_restart)

    status = sub.add_parser("status", help="查看运行状态")
    status.add_argument("--port", type=int)
    status.set_defaults(handler=cmd_status)

    logs = sub.add_parser("logs", help="查看日志")
    logs.add_argument("--name", choices=["web", "scheduler", "window", "launchd"])
    logs.add_argument("--lines", type=int, default=50)
    logs.add_argument("--all", action="store_true", help="显示全部日志")
    logs.add_argument("--follow", action="store_true", help="持续跟踪")
    logs.set_defaults(handler=cmd_logs)

    backup = sub.add_parser("backup", help="创建一次数据库备份")
    backup.set_defaults(handler=cmd_backup)

    autostart = sub.add_parser("autostart", help="每日自动运行（默认 07:25-07:35）")
    autostart.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    autostart.add_argument("--dry-run", action="store_true", help="只打印配置，不安装")
    autostart.set_defaults(handler=cmd_autostart)

    window = sub.add_parser("run-window", help="运行一次每日窗口（供系统定时器调用）")
    window.add_argument("--now", action="store_true", help="立即开始，用于手动测试")
    window.add_argument("--minutes", type=int, default=2, help="配合 --now 的运行分钟数")
    window.add_argument("--drain", type=int, default=None, help="结束后排空秒数")
    window.set_defaults(handler=cmd_run_window)

    scheduler = sub.add_parser("serve-scheduler", help="内部：带锁运行调度器")
    scheduler.set_defaults(handler=cmd_serve_scheduler)

    link = sub.add_parser("link", help="把 jlu 命令安装到 PATH")
    link.add_argument("--quiet", action="store_true", help="已安装时不重复输出")
    link.add_argument(
        "--auto-path",
        action="store_true",
        help="需要时自动写入 shell 配置",
    )
    link.set_defaults(handler=cmd_link)

    uninstall = sub.add_parser("uninstall", help="停止并移除自动运行")
    uninstall.add_argument("--purge", action="store_true", help="同时删除数据目录")
    uninstall.add_argument("--remove-venv", action="store_true", help="同时删除虚拟环境")
    uninstall.set_defaults(handler=cmd_uninstall)

    return parser


def _install_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", type=int, help="Web 端口（默认 8000）")
    parser.add_argument("--host", default=None, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--start", default=None, help="每日窗口开始（默认 07:25）")
    parser.add_argument("--end", default=None, help="每日窗口结束（默认 07:35）")
    parser.add_argument("--drain", type=int, default=None, help="结束后排空秒数（默认 300）")
    parser.add_argument("--index-url", default=None, help="pip 镜像地址")
    parser.add_argument("--force-install", action="store_true", help="强制重装依赖")


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        os.umask(0o077)
    # Keep our progress lines in order with pip/getpass output when piped.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except DeployError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except subprocess.CalledProcessError as exc:
        print(f"命令失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
