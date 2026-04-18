"""Time helpers."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_stale(ts: datetime | None, max_age_minutes: int) -> bool:
    if ts is None:
        return True
    delta = utcnow() - ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else utcnow() - ts
    return delta > timedelta(minutes=max_age_minutes)
