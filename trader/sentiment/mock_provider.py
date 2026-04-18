"""Mock sentiment provider — deterministic fallback."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List

from trader.sentiment.base import SentimentProvider, SentimentResult


class MockProvider(SentimentProvider):
    """Returns deterministic sentiment based on day-of-year for testing."""

    def _day_score(self) -> float:
        day = datetime.now(timezone.utc).timetuple().tm_yday
        return round(math.sin(day * 0.1) * 0.5, 4)

    def fetch_market_sentiment(self) -> SentimentResult:
        return SentimentResult(
            scope="market",
            key="market",
            score=self._day_score(),
            summary="Mock sentiment (deterministic)",
            sources=["mock"],
        )

    def fetch_sector_sentiment(self) -> List[SentimentResult]:
        base = self._day_score()
        sectors = ["Technology", "Financial", "Energy", "Healthcare"]
        return [
            SentimentResult(
                scope="sector",
                key=s,
                score=round(base + (i * 0.1 - 0.15), 4),
                summary="Mock",
                sources=["mock"],
            )
            for i, s in enumerate(sectors)
        ]
