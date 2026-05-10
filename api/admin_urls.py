"""Helpers for presenting local admin URLs."""

from __future__ import annotations

from config.settings import Settings


def local_admin_url(settings: Settings) -> str:
    """Return a browser-friendly URL for the localhost-only admin UI."""

    host = settings.host.strip() if settings.host else "127.0.0.1"
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{settings.port}/admin"


def admin_launch_message(settings: Settings) -> str:
    """Return the startup message shown by supported launch commands."""

    return f"Admin UI: {local_admin_url(settings)} (local-only)"
