"""RSS-based sentiment provider with lexicon scoring."""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List

import feedparser

from common.logging import get_logger
from trader.sentiment.base import SentimentProvider, SentimentResult

log = get_logger(__name__)

# Configurable RSS feeds
DEFAULT_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?region=US&lang=en-US",
]

# Simple lexicon for scoring
POSITIVE_WORDS = {
    "rally", "surge", "gain", "jump", "rise", "bull", "bullish", "soar",
    "upbeat", "optimism", "growth", "record", "high", "beat", "exceed",
    "upgrade", "strong", "recovery", "positive", "boost", "profit",
}

NEGATIVE_WORDS = {
    "crash", "plunge", "drop", "fall", "decline", "bear", "bearish",
    "slump", "recession", "fear", "loss", "sell-off", "selloff", "risk",
    "warning", "downgrade", "weak", "crisis", "inflation", "cut", "layoff",
    "miss", "negative", "concern", "uncertainty", "volatile",
}

# Sector keywords for tagging
SECTOR_KEYWORDS: Dict[str, List[str]] = {
    "Technology": ["tech", "software", "ai", "cloud", "chip", "semiconductor", "nvidia", "apple", "microsoft", "google", "meta"],
    "Financial": ["bank", "finance", "fed", "rate", "interest", "credit", "loan", "jpmorgan", "goldman"],
    "Energy": ["oil", "gas", "energy", "crude", "opec", "exxon", "chevron"],
    "Healthcare": ["health", "pharma", "drug", "fda", "biotech", "vaccine", "hospital"],
    "Consumer Discretionary": ["retail", "consumer", "amazon", "tesla", "auto"],
    "Consumer Staples": ["food", "beverage", "grocery", "walmart", "costco"],
}


def _score_text(text: str) -> float:
    text_lower = text.lower()
    words = set(re.findall(r'\b\w+\b', text_lower))
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def _recency_weight(published: datetime | None) -> float:
    if published is None:
        return 0.5
    now = datetime.now(timezone.utc)
    age_hours = max(0, (now - published).total_seconds() / 3600)
    if age_hours < 4:
        return 1.0
    elif age_hours < 24:
        return 0.7
    elif age_hours < 72:
        return 0.4
    return 0.2


def _parse_date(entry) -> datetime | None:
    pub = entry.get("published_parsed") or entry.get("updated_parsed")
    if pub:
        try:
            return datetime(*pub[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _detect_sectors(text: str) -> List[str]:
    text_lower = text.lower()
    sectors = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            sectors.append(sector)
    return sectors


class RSSProvider(SentimentProvider):
    def __init__(self, feeds: List[str] | None = None):
        self.feeds = feeds or DEFAULT_FEEDS

    def _fetch_entries(self) -> list:
        entries = []
        for url in self.feeds:
            try:
                feed = feedparser.parse(url)
                entries.extend(feed.entries)
            except Exception as e:
                log.warning("Failed to fetch RSS %s: %s", url, e)
        return entries

    def fetch_market_sentiment(self) -> SentimentResult:
        entries = self._fetch_entries()
        if not entries:
            return SentimentResult(scope="market", key="market", score=0.0, summary="No data", sources=[])

        weighted_scores = []
        sources = []
        for entry in entries[:50]:  # cap processing
            title = entry.get("title", "")
            desc = entry.get("summary", "")
            text = f"{title} {desc}"
            pub = _parse_date(entry)
            score = _score_text(text)
            weight = _recency_weight(pub)
            weighted_scores.append(score * weight)
            if abs(score) > 0.3:
                sources.append(title[:100])

        avg_score = sum(weighted_scores) / len(weighted_scores) if weighted_scores else 0.0
        avg_score = max(-1.0, min(1.0, avg_score))

        return SentimentResult(
            scope="market",
            key="market",
            score=round(avg_score, 4),
            summary=f"Analysed {len(entries)} headlines",
            sources=sources[:5],
        )

    def fetch_sector_sentiment(self) -> List[SentimentResult]:
        entries = self._fetch_entries()
        sector_scores: Dict[str, List[float]] = {}
        sector_sources: Dict[str, List[str]] = {}

        for entry in entries[:50]:
            title = entry.get("title", "")
            desc = entry.get("summary", "")
            text = f"{title} {desc}"
            pub = _parse_date(entry)
            score = _score_text(text)
            weight = _recency_weight(pub)
            sectors = _detect_sectors(text)

            for sector in sectors:
                sector_scores.setdefault(sector, []).append(score * weight)
                if abs(score) > 0.3:
                    sector_sources.setdefault(sector, []).append(title[:100])

        results = []
        for sector, scores in sector_scores.items():
            avg = sum(scores) / len(scores) if scores else 0.0
            avg = max(-1.0, min(1.0, avg))
            results.append(SentimentResult(
                scope="sector",
                key=sector,
                score=round(avg, 4),
                summary=f"{len(scores)} mentions",
                sources=sector_sources.get(sector, [])[:3],
            ))

        return results
