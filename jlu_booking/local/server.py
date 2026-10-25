"""Local HTTP server, scheduler thread, and lifecycle helpers."""

from __future__ import annotations

import logging
import socket
import threading
import time
from pathlib import Path

import uvicorn

from ..web.scheduler import Scheduler
from ..web.worker import WorkerAdapter


LOG = logging.getLogger("jlu_booking.local")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LocalRuntime:
    """Own the loopback server and the one immediate-booking scheduler."""

    def __init__(self, app, settings, *, log_path: Path | None = None):
        self.app = app
        self.settings = settings
        self.log_path = Path(log_path) if log_path is not None else None
        self.server: uvicorn.Server | None = None
        self.server_thread: threading.Thread | None = None
        self.scheduler: Scheduler | None = None
        self.scheduler_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.url = ""

    def start(self, *, port: int | None = None, timeout: float = 20.0) -> str:
        selected_port = int(port) if port else _free_loopback_port()
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=selected_port,
            log_level="warning",
            access_log=False,
            lifespan="on",
        )
        self.server = uvicorn.Server(config)
        self.server_thread = threading.Thread(
            target=self.server.run,
            name="jlu-local-uvicorn",
            daemon=True,
        )
        self.server_thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server.started:
                break
            if not self.server_thread.is_alive():
                raise RuntimeError("本机服务启动失败，请查看日志。")
            time.sleep(0.05)
        else:
            self.stop()
            raise RuntimeError("本机服务启动超时。")

        self.url = f"http://127.0.0.1:{selected_port}/"
        self._start_scheduler()
        return self.url

    def _start_scheduler(self) -> None:
        services = self.app.state.services
        worker = WorkerAdapter(services.credentials, self.settings)
        self.scheduler = Scheduler(
            services.connection,
            worker,
            daily_plans=None,
        )
        self.scheduler_thread = threading.Thread(
            target=self.scheduler.run_forever,
            kwargs={"stop_event": self.stop_event},
            name="jlu-local-scheduler",
            daemon=True,
        )
        self.scheduler_thread.start()

    def wait(self) -> None:
        """Block until interrupted (used by the browser-only fallback)."""

        try:
            self.stop_event.wait()
        except KeyboardInterrupt:
            pass

    def stop(self) -> None:
        self.stop_event.set()
        if self.scheduler_thread is not None:
            self.scheduler_thread.join(timeout=12.0)
            self.scheduler_thread = None
        if self.server is not None:
            self.server.should_exit = True
            if self.server_thread is not None:
                self.server_thread.join(timeout=10.0)
            self.server = None
            self.server_thread = None
        try:
            self.app.state.services.connection.close()
        except Exception:  # pragma: no cover - best-effort shutdown
            LOG.debug("closing local database failed", exc_info=True)
