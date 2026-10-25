"""Private local state and the single-account bootstrap for the desktop shell."""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from ..config import AUTO_CONFIG_FILE
from ..paths import PROJECT_DIR, TOKEN_FILE, USER_CONFIG_DIR
from ..token_store import TokenStoreError, load_saved_token
from ..web.app import create_app
from ..web.db import transaction
from ..web.security import CredentialCipher
from ..web.settings import WebSettings


BEIJING = ZoneInfo("Asia/Shanghai")
LOCAL_USER_DISPLAY_NAME = "本机用户"
LOCAL_DIR_NAME = "local"
TOKEN_KEY_NAME = "token.key"
BLIND_KEY_NAME = "blind.key"
PENDING_LIFETIME = timedelta(days=3650)


@dataclass(frozen=True)
class LocalIdentity:
    """Facts about the automatically managed local account."""

    user_id: int
    status: str
    has_token: bool
    has_companion: bool
    imported_token: bool
    imported_companion: bool


def local_data_dir() -> Path:
    """Directory for the desktop shell's database, secrets and logs.

    Source checkouts keep local state in the git-ignored ``runtime/local``
    folder, which keeps preview and normal launches on the same data. Packaged
    builds store it under the platform's per-user configuration directory.
    """

    override = os.getenv("JLU_BOOKING_LOCAL_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if not getattr(sys, "frozen", False):
        return Path(PROJECT_DIR) / "runtime" / LOCAL_DIR_NAME
    return Path(USER_CONFIG_DIR) / LOCAL_DIR_NAME


def _restrict_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            path.chmod(0o700)
        except OSError:
            pass


def _write_private_file(path: Path, content: bytes) -> None:
    """Create a secret or key file once with owner-only permissions."""

    _restrict_directory(path.parent)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.write(b"\n")
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _read_private_file(path: Path) -> bytes:
    value = path.read_bytes().strip()
    if not value:
        raise ValueError(f"本机密钥文件为空：{path}")
    return value


def _generate_keys(base: Path) -> tuple[Path, Path]:
    token_key_file = base / "secrets" / TOKEN_KEY_NAME
    blind_key_file = base / "secrets" / BLIND_KEY_NAME
    _write_private_file(token_key_file, Fernet.generate_key())
    blind_key = base64.urlsafe_b64encode(secrets.token_bytes(32))
    _write_private_file(blind_key_file, blind_key)
    return token_key_file, blind_key_file


def ensure_local_settings(base_dir: Path | str | None = None) -> WebSettings:
    """Build validated settings for the local app, creating keys if needed."""

    base = Path(base_dir) if base_dir is not None else local_data_dir()
    _restrict_directory(base)
    token_key_file, blind_key_file = _generate_keys(base)
    token_key = _read_private_file(token_key_file)
    blind_key_encoded = _read_private_file(blind_key_file)
    try:
        blind_key = base64.b64decode(blind_key_encoded, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"本机 Token 索引密钥无效：{blind_key_file}") from exc
    if len(blind_key) != 32:
        raise ValueError(f"本机 Token 索引密钥必须是 32 字节：{blind_key_file}")

    return WebSettings(
        data_dir=base,
        database_path=base / "local.sqlite3",
        runtime_root=base / "runtime",
        backup_dir=base / "backups",
        token_key_file=token_key_file,
        blind_key_file=blind_key_file,
        token_key=token_key,
        blind_key=blind_key,
        # The server only listens on loopback and uses no cookies.
        cookie_secure=False,
        user_limit=1,
        pending_limit=1,
        daily_task_limit=100,
    )


def _read_saved_token() -> str:
    if not Path(TOKEN_FILE).is_file():
        return ""
    try:
        return load_saved_token(TOKEN_FILE).strip()
    except TokenStoreError:
        return ""


def _read_companion_number() -> str:
    """Read the newest saved desktop config that contains a companion.

    Source checkouts may still use the legacy project-level config while a
    packaged build uses the per-user config. Whichever file was saved most
    recently wins, so the local app does not silently lose the companion when
    both files exist.
    """

    candidates = {
        Path(AUTO_CONFIG_FILE),
        Path(USER_CONFIG_DIR) / "auto_booking.json",
    }
    newest: tuple[float, str] | None = None
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            modified = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        number = str(payload.get("companion_student_number", "")).strip()
        if number and (newest is None or modified > newest[0]):
            newest = (modified, number)
    return newest[1] if newest is not None else ""


def _user_row(connection):
    return connection.execute(
        "SELECT * FROM users WHERE role = 'user' ORDER BY id LIMIT 1"
    ).fetchone()


def _ensure_user(connection, *, token_present: bool, now: datetime) -> int:
    row = _user_row(connection)
    if row is None:
        status = "active" if token_present else "pending_token"
        with transaction(connection, immediate=True):
            cursor = connection.execute(
                "INSERT INTO users "
                "(username, password_hash, role, status, created_at, "
                "activated_at, pending_expires_at) "
                "VALUES (?, ?, 'user', ?, ?, ?, ?)",
                (
                    LOCAL_USER_DISPLAY_NAME,
                    "local-mode-no-password",
                    status,
                    now.isoformat(),
                    now.isoformat() if token_present else None,
                    None
                    if token_present
                    else (now + PENDING_LIFETIME).isoformat(),
                ),
            )
        return int(cursor.lastrowid)

    user_id = int(row["id"])
    credential = connection.execute(
        "SELECT 1 FROM user_credentials WHERE user_id = ?", (user_id,)
    ).fetchone()
    if credential is not None or token_present:
        desired = "active"
    else:
        desired = "pending_token"
    if row["status"] != desired:
        with transaction(connection, immediate=True):
            connection.execute(
                "UPDATE users SET status = ?, activated_at = "
                "COALESCE(activated_at, ?), pending_expires_at = ? WHERE id = ?",
                (
                    desired,
                    now.isoformat(),
                    None
                    if desired == "active"
                    else (now + PENDING_LIFETIME).isoformat(),
                    user_id,
                ),
            )
    return user_id


def _ensure_token(
    connection,
    settings: WebSettings,
    *,
    user_id: int,
    token: str,
    now: datetime,
) -> bool:
    if not token:
        return False
    existing = connection.execute(
        "SELECT 1 FROM user_credentials WHERE user_id = ?", (user_id,)
    ).fetchone()
    if existing is not None:
        return False

    cipher = CredentialCipher(settings.token_key, settings.blind_key)
    ciphertext = cipher.encrypt(token)
    blind_index = cipher.token_index(token)
    with transaction(connection, immediate=True):
        connection.execute(
            "INSERT INTO user_credentials "
            "(user_id, token_ciphertext, token_blind_index, verified_at, "
            "last_status, updated_at) VALUES (?, ?, ?, ?, 'valid', ?)",
            (
                user_id,
                ciphertext,
                blind_index,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            "UPDATE users SET status = 'active', activated_at = "
            "COALESCE(activated_at, ?), pending_expires_at = NULL WHERE id = ?",
            (now.isoformat(), user_id),
        )
    return True


def _ensure_companion(
    connection,
    settings: WebSettings,
    *,
    user_id: int,
    student_number: str,
    now: datetime,
) -> bool:
    """Import the desktop config's already-saved companion without network I/O.

    The school system re-validates the student number on every manual precheck
    and automatic worker run, so this import never bypasses a real check.
    """

    if not student_number:
        return False
    existing = connection.execute(
        "SELECT 1 FROM companions WHERE user_id = ?", (user_id,)
    ).fetchone()
    if existing is not None:
        return False

    cipher = CredentialCipher(settings.token_key, settings.blind_key)
    with transaction(connection, immediate=True):
        connection.execute(
            "INSERT INTO companions "
            "(user_id, student_number_ciphertext, name_ciphertext, "
            "verified_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                user_id,
                cipher.encrypt(student_number),
                cipher.encrypt("已保存同行人"),
                now.isoformat(),
                now.isoformat(),
            ),
        )
    return True


def ensure_local_identity(
    services,
    settings: WebSettings,
    *,
    now: datetime | None = None,
    token: str | None = None,
    companion_number: str | None = None,
) -> LocalIdentity:
    """Create or repair the one local account and import existing credentials."""

    local_now = now or datetime.now(BEIJING)
    token = _read_saved_token() if token is None else str(token).strip()
    if companion_number is None:
        companion_number = _read_companion_number()
    companion_number = str(companion_number).strip()

    user_id = _ensure_user(
        services.connection,
        token_present=bool(token),
        now=local_now,
    )
    imported_token = _ensure_token(
        services.connection,
        settings,
        user_id=user_id,
        token=token,
        now=local_now,
    )
    imported_companion = _ensure_companion(
        services.connection,
        settings,
        user_id=user_id,
        student_number=companion_number,
        now=local_now,
    )
    user = services.accounts.get(user_id)
    has_token = (
        services.connection.execute(
            "SELECT 1 FROM user_credentials WHERE user_id = ?", (user_id,)
        ).fetchone()
        is not None
    )
    has_companion = (
        services.connection.execute(
            "SELECT 1 FROM companions WHERE user_id = ?", (user_id,)
        ).fetchone()
        is not None
    )
    return LocalIdentity(
        user_id=user_id,
        status=user.status,
        has_token=has_token,
        has_companion=has_companion,
        imported_token=imported_token,
        imported_companion=imported_companion,
    )


def create_local_app(
    *,
    base_dir: Path | str | None = None,
    token: str | None = None,
    companion_number: str | None = None,
):
    """Create the single-account app and attach its in-process session."""

    settings = ensure_local_settings(base_dir)
    app = create_app(settings, local_mode=True)
    now = datetime.now(BEIJING)
    identity = ensure_local_identity(
        app.state.services,
        settings,
        now=now,
        token=token,
        companion_number=companion_number,
    )
    app.state.services.connection.execute(
        "DELETE FROM web_sessions WHERE user_id = ?", (identity.user_id,)
    )
    grant = app.state.services.sessions.create(identity.user_id, now)
    session = app.state.services.sessions.resolve(grant.raw_token, now)
    if session is None:  # pragma: no cover - defensive only
        raise RuntimeError("无法创建本机会话。")
    app.state.local_session = session
    app.state.local_user_id = identity.user_id
    return app, settings, identity
