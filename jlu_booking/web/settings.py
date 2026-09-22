"""Validated runtime settings for the optional Web application."""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet
from platformdirs import user_state_path


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false。")


def _secret_file(
    path: Path,
    *,
    label: str,
    strict_permissions: bool,
) -> bytes:
    if not path.is_file():
        raise ValueError(f"{label} 文件不存在：{path}")
    if strict_permissions and os.name != "nt":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise ValueError(f"{label} 文件权限不安全：{path}，必须禁止组和其他用户访问。")
    try:
        value = path.read_bytes().strip()
    except OSError as exc:
        raise ValueError(f"无法读取 {label} 文件：{path}") from exc
    if not value:
        raise ValueError(f"{label} 文件不能为空：{path}")
    return value


def _validate_fernet_key(value: bytes, path: Path) -> bytes:
    try:
        Fernet(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Token 密钥不是有效的 Fernet 密钥：{path}") from exc
    return value


def _decode_blind_key(value: bytes, path: Path) -> bytes:
    try:
        decoded = base64.b64decode(value, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Token 索引密钥不是有效的 URL-safe base64：{path}") from exc
    if len(decoded) != 32:
        raise ValueError(f"Token 索引密钥解码后必须是 32 字节：{path}")
    return decoded


@dataclass(frozen=True)
class WebSettings:
    """Filesystem, secret, and capacity settings shared by Web processes."""

    data_dir: Path
    database_path: Path
    runtime_root: Path
    backup_dir: Path
    token_key_file: Path
    blind_key_file: Path
    token_key: bytes
    blind_key: bytes
    cookie_secure: bool = True
    trusted_proxy: str = "127.0.0.1"
    user_limit: int = 30
    pending_limit: int = 100
    daily_task_limit: int = 10

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str],
        *,
        strict_permissions: bool = True,
    ) -> "WebSettings":
        default_data_dir = Path(user_state_path("jlu-booking")) / "web"
        data_dir = Path(
            environ.get("JLU_BOOKING_WEB_DATA_DIR", str(default_data_dir))
        ).expanduser().resolve()
        token_key_file = Path(
            environ.get(
                "JLU_BOOKING_WEB_TOKEN_KEY_FILE",
                str(data_dir / "secrets" / "token.key"),
            )
        ).expanduser().resolve()
        blind_key_file = Path(
            environ.get(
                "JLU_BOOKING_WEB_BLIND_KEY_FILE",
                str(data_dir / "secrets" / "blind.key"),
            )
        ).expanduser().resolve()
        token_key = _validate_fernet_key(
            _secret_file(
                token_key_file,
                label="Token 加密密钥",
                strict_permissions=strict_permissions,
            ),
            token_key_file,
        )
        blind_key = _decode_blind_key(
            _secret_file(
                blind_key_file,
                label="Token 索引密钥",
                strict_permissions=strict_permissions,
            ),
            blind_key_file,
        )
        cookie_secure = _parse_bool(
            environ.get("JLU_BOOKING_WEB_COOKIE_SECURE", "true"),
            name="JLU_BOOKING_WEB_COOKIE_SECURE",
        )
        trusted_proxy = environ.get(
            "JLU_BOOKING_WEB_TRUSTED_PROXY",
            "127.0.0.1",
        ).strip()
        if not trusted_proxy:
            raise ValueError("JLU_BOOKING_WEB_TRUSTED_PROXY 不能为空。")

        return cls(
            data_dir=data_dir,
            database_path=data_dir / "web.sqlite3",
            runtime_root=data_dir / "runtime",
            backup_dir=data_dir / "backups",
            token_key_file=token_key_file,
            blind_key_file=blind_key_file,
            token_key=token_key,
            blind_key=blind_key,
            cookie_secure=cookie_secure,
            trusted_proxy=trusted_proxy,
        )
