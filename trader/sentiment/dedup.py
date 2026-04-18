"""Deduplication for RSS items sent to the LLM.

Stable sha256 hash over normalised (title, snippet, url) — so the same
headline reappearing across runs or feeds is only sent to Claude once per
dedup window.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from common.models import SentimentLlmItem
from common.time import utcnow


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite strips tzinfo when round-tripping; re-attach UTC so comparisons work."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\u2018\u2019\u201c\u201d`'\"\.,;:!\?\(\)\[\]\{\}]")


def _normalise_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def _normalise_url(u: Optional[str]) -> str:
    if not u:
        return ""
    try:
        parts = urlsplit(u.strip().lower())
        # drop fragment + query (trackers)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return u.strip().lower()


def item_hash(title: str, snippet: str, url: Optional[str]) -> str:
    """Return a 16-char sha256 prefix of the normalised fingerprint."""
    fingerprint = f"{_normalise_text(title)}|{_normalise_text(snippet)}|{_normalise_url(url)}"
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass
class RawNewsItem:
    title: str
    snippet: str
    url: Optional[str]
    published_at: Optional[datetime]
    source: str
    language: Optional[str] = None


def upsert_and_filter_new(
    db: Session,
    items: Iterable[RawNewsItem],
    *,
    dedup_window_days: int,
    now: Optional[datetime] = None,
) -> List[tuple[str, RawNewsItem]]:
    """Upsert every item's first/last-seen and return the ones we still need to process.

    An item is considered already-processed (and therefore skipped) if its
    `processed_at` is newer than `now - dedup_window_days`.

    Returns a list of (hash_id, item) pairs in the order they were given.
    """
    now = now or utcnow()
    cutoff = now - timedelta(days=int(dedup_window_days))
    out: List[tuple[str, RawNewsItem]] = []

    for item in items:
        hid = item_hash(item.title, item.snippet, item.url)
        existing = db.get(SentimentLlmItem, hid)
        if existing is None:
            db.add(
                SentimentLlmItem(
                    id=hid,
                    first_seen_at=now,
                    last_seen_at=now,
                    source=item.source[:200],
                    title=(item.title or "")[:1000],
                    url=(item.url or None),
                    processed_at=None,
                )
            )
            out.append((hid, item))
            continue

        existing.last_seen_at = now
        processed = _as_aware(existing.processed_at)
        if processed is not None and processed > cutoff:
            # processed recently — skip
            continue
        out.append((hid, item))

    db.flush()
    return out


def mark_processed(db: Session, ids: Iterable[str], *, now: Optional[datetime] = None) -> None:
    """Mark the given ids as processed at `now`."""
    now = now or utcnow()
    for hid in ids:
        row = db.get(SentimentLlmItem, hid)
        if row is not None:
            row.processed_at = now


def purge_stale(db: Session, *, retention_days: int = 60, now: Optional[datetime] = None) -> int:
    """Delete dedup rows that haven't been seen in `retention_days`.

    Retention is intentionally longer than the dedup window so re-appearing
    items keep their processed-at and stay deduped across feed refreshes.
    """
    now = now or utcnow()
    cutoff = now - timedelta(days=int(retention_days))
    q = db.query(SentimentLlmItem).filter(SentimentLlmItem.last_seen_at < cutoff)
    n = q.count()
    q.delete(synchronize_session=False)
    return n
