"""Persist sentiment snapshots to DB."""
from __future__ import annotations

import json
from typing import List

from common.db import get_db
from common.logging import get_logger
from common.models import SentimentSnapshot
from common.time import utcnow
from trader.sentiment.base import SentimentProvider, SentimentResult
from trader.sentiment.rss_provider import RSSProvider
from trader.sentiment.mock_provider import MockProvider

log = get_logger(__name__)


def get_provider(use_mock: bool = False) -> SentimentProvider:
    if use_mock:
        return MockProvider()
    return RSSProvider()


def refresh_and_store(provider: SentimentProvider | None = None) -> List[SentimentResult]:
    """Fetch sentiment from provider and persist to DB."""
    if provider is None:
        provider = get_provider()

    results: List[SentimentResult] = []

    try:
        market = provider.fetch_market_sentiment()
        results.append(market)
    except Exception as e:
        log.error("Market sentiment fetch failed: %s", e)

    try:
        sectors = provider.fetch_sector_sentiment()
        results.extend(sectors)
    except Exception as e:
        log.error("Sector sentiment fetch failed: %s", e)

    now = utcnow()
    with get_db() as db:
        for r in results:
            db.add(SentimentSnapshot(
                timestamp=now,
                scope=r.scope,
                key=r.key,
                score=r.score,
                summary=r.summary,
                sources_json=json.dumps(r.sources),
            ))

    log.info("Stored %d sentiment snapshots.", len(results))
    return results


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
