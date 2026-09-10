"""Persist the user's authentication token outside the project directory."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote

from .paths import TOKEN_FILE


class TokenStoreError(RuntimeError):
    """Raised when the saved token cannot be read or changed."""


def normalize_token(token: str) -> str:
    """Validate a token without ever including its value in an error message."""

    normalized = str(token).strip()
    if not normalized:
        raise ValueError("Token 不能为空。")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("Token 格式无效：不能包含换行符。")
    return normalized


_TOKEN_PARAMETER_RE = re.compile(r"(?:^|[?&])token=([^&\s#]*)", re.IGNORECASE)


def extract_token_input(value: str) -> str:
    """Accept either a raw token or a copied request URL containing ``token``.

    The GUI deliberately performs this parsing locally.  It never sends the
    pasted URL anywhere and stores only the extracted token value.
    """

    text = str(value).strip()
    if not text:
        raise ValueError("Token 不能为空。")
    if "\n" in text or "\r" in text:
        raise ValueError("Token 或请求地址不能包含换行符。")

    # This also supports copying only the query string, for example
    # ``shopNum=...&token=...`` from the browser's Network panel.  ``unquote``
    # intentionally preserves a literal plus sign in token values.
    match = _TOKEN_PARAMETER_RE.search(text)
    if match:
        return normalize_token(unquote(match.group(1)))

    return normalize_token(text)


def _restrict_permissions(path: Path, mode: int) -> None:
    """Apply restrictive POSIX permissions when the platform supports them."""

    if os.name == "nt":
        return
    try:
        path.chmod(mode)
    except OSError:
        # Some mounted filesystems do not implement chmod. Saving is still useful,
        # and the CLI documents the exact location so users can inspect it.
        pass


def load_saved_token(path: Path | str = TOKEN_FILE) -> str:
    """Load a previously saved token, returning an empty string when absent."""

    token_path = Path(path)
    try:
        content = token_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise TokenStoreError(f"无法读取本机保存的 Token：{token_path}（{exc}）") from exc

    if not content.strip():
        return ""
    try:
        return normalize_token(content)
    except ValueError as exc:
        raise TokenStoreError(f"本机 Token 文件格式无效：{token_path}（{exc}）") from exc


def save_token(token: str, path: Path | str = TOKEN_FILE) -> Path:
    """Atomically save a token in the current user's configuration directory."""

    normalized = normalize_token(token)
    token_path = Path(path)
    temporary_path: Path | None = None

    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        _restrict_permissions(token_path.parent, 0o700)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=token_path.parent,
            prefix=f".{token_path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(f"{normalized}\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        _restrict_permissions(temporary_path, 0o600)
        os.replace(temporary_path, token_path)
        temporary_path = None
        _restrict_permissions(token_path, 0o600)
    except (OSError, ValueError) as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, ValueError):
            raise
        raise TokenStoreError(f"无法保存 Token：{token_path}（{exc}）") from exc

    return token_path


def clear_saved_token(path: Path | str = TOKEN_FILE) -> bool:
    """Delete the saved token and report whether a file was removed."""

    token_path = Path(path)
    try:
        token_path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TokenStoreError(f"无法删除本机保存的 Token：{token_path}（{exc}）") from exc
    return True


def resolve_token(
    path: Path | str = TOKEN_FILE,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Resolve environment first, then the per-user saved token."""

    environment = os.environ if environ is None else environ
    environment_token = environment.get("JLU_BOOKING_TOKEN", "").strip()
    if environment_token:
        try:
            return normalize_token(environment_token), "environment"
        except ValueError as exc:
            raise TokenStoreError(f"环境变量 JLU_BOOKING_TOKEN 格式无效（{exc}）") from exc

    saved_token = load_saved_token(path)
    if saved_token:
        return saved_token, "saved"
    return "", "none"
