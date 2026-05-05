"""Helper for appending entries to the refresh_log audit table."""
from __future__ import annotations

from common.db import get_db
from common.models import RefreshLog


def log_refresh(action: str, status: str, duration_ms: int, message: str = "") -> None:
    """Append a single refresh event. Swallows errors so logging never breaks a refresh."""
    try:
        with get_db() as db:
            db.add(RefreshLog(
                action=action,
                status=status,
                duration_ms=int(duration_ms),
                message=(message or "")[:1000],
            ))
    except Exception:
        pass
