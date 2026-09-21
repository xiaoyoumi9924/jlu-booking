"""Atomic, privacy-safe last-run state for unattended automation."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .paths import RUN_STATUS_FILE


ALLOWED_STATUSES = {
    "starting",
    "running",
    "success",
    "no_result",
    "token_invalid",
    "network_unavailable",
    "account_blocked",
    "daily_limit",
    "submission_unknown",
    "stopped",
    "error",
}
ALLOWED_FIELDS = {"target_date", "venue", "sport", "phase", "detail"}
RUN_STATUS_TIMEZONE = ZoneInfo("Asia/Shanghai")


class RunStatusError(RuntimeError):
    """Raised when the run-status file cannot be read or written safely."""


def write_run_status(status, *, path=RUN_STATUS_FILE, **fields):
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"不支持的运行状态：{status}")
    unexpected = sorted(set(fields) - ALLOWED_FIELDS)
    if unexpected:
        raise ValueError(f"运行状态不允许写入字段：{', '.join(unexpected)}")

    payload = {
        "status": status,
        "updated_at": datetime.now(RUN_STATUS_TIMEZONE).isoformat(),
    }
    payload.update(
        {
            key: str(value)
            for key, value in fields.items()
            if value is not None and str(value)
        }
    )

    target = Path(path)
    temporary_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        if os.name != "nt":
            temporary_path.chmod(0o600)
        os.replace(temporary_path, target)
        temporary_path = None
    except OSError as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise RunStatusError(f"无法写入运行状态：{target}（{exc}）") from exc

    return target


def load_run_status(path=RUN_STATUS_FILE):
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RunStatusError(f"无法读取运行状态：{target}（{exc}）") from exc

    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RunStatusError(f"运行状态文件损坏：{target}") from exc
    if not isinstance(payload, dict) or payload.get("status") not in ALLOWED_STATUSES:
        raise RunStatusError(f"运行状态文件损坏：{target}")
    return payload
