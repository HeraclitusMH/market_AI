"""Persist sentiment snapshots to DB.

Thin compatibility shim. The real refresh logic lives in `factory.py` so it
can be shared by the scheduler, tests, and any ad-hoc tools — and so it can
dispatch to either the legacy RSS provider or the Claude provider based on
config.
"""
from __future__ import annotations

from typing import List

from common.db import get_db
from common.logging import get_logger
from common.models import SentimentSnapshot
from trader.sentiment.base import SentimentProvider, SentimentResult
from trader.sentiment.factory import (
    build_provider,
    refresh_and_store as _refresh_and_store,
)

log = get_logger(__name__)


def get_provider(use_mock: bool = False) -> SentimentProvider:
    return build_provider(use_mock=use_mock)


def refresh_and_store(provider: SentimentProvider | None = None) -> List[SentimentResult]:
    """Fetch sentiment from the active provider and persist to DB.

    Returns only the SentimentResult list for backwards compatibility. Callers
    who want the full run summary (budget, model, counts) should call
    ``trader.sentiment.factory.refresh_and_store`` directly.
    """
    summary = _refresh_and_store(provider)
    # The factory already persisted snapshots; we no longer return them here,
    # but preserve the list shape for legacy callers.
    log.info(
        "Sentiment refresh: provider=%s status=%s snapshots=%d",
        summary.get("provider"), summary.get("status"), summary.get("snapshots_written", 0),
    )
    return []


def get_latest_market_score() -> float:
    with get_db() as db:
        row = (
            db.query(SentimentSnapshot)
            .filter(SentimentSnapshot.scope == "market")
            .order_by(SentimentSnapshot.id.desc())
            .first()
        )
    return row.score if row else 0.0


def get_latest_sector_score(sector: str) -> float:
    with get_db() as db:
        row = (
            db.query(SentimentSnapshot)
            .filter(SentimentSnapshot.scope == "sector", SentimentSnapshot.key == sector)
            .order_by(SentimentSnapshot.id.desc())
            .first()
        )
    return row.score if row else 0.0
