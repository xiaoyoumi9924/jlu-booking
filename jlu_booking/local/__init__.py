"""Single-user desktop shell built on the existing Web user workspace.

The public Web app remains multi-user. This package boots the same FastAPI
routes, templates, CSS and booking services locally, with one auto-authenticated
account and without the daily automatic-booking module.
"""

from .bootstrap import (
    LocalIdentity,
    create_local_app,
    ensure_local_identity,
    ensure_local_settings,
    local_data_dir,
)

__all__ = [
    "LocalIdentity",
    "create_local_app",
    "ensure_local_identity",
    "ensure_local_settings",
    "local_data_dir",
]
