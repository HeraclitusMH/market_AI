"""Sentiment provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class SentimentResult:
    scope: str          # "market" or "sector"
    key: str            # e.g. "market", "Technology", "Financial"
    score: float        # -1.0 to 1.0
    summary: str
    sources: List[str]


class SentimentProvider(ABC):
    @abstractmethod
    def fetch_market_sentiment(self) -> SentimentResult:
        ...

    @abstractmethod
    def fetch_sector_sentiment(self) -> List[SentimentResult]:
        ...
