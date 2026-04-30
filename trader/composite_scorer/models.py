"""Shared result models for composite scoring."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict


@dataclass
class FactorResult:
    score: float
    components: Dict
    confidence: float
    timestamp: datetime
    data_staleness: str = "fresh"


@dataclass
class CompositeResult:
    symbol: str
    score: float
    regime: str
    breakdown: Dict
    confidence: float
    timestamp: datetime

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "composite_score": round(self.score, 2),
            "regime": self.regime,
            "confidence": round(self.confidence, 2),
            "factors": {
                name: {
                    "score": round(info["raw_score"], 2),
                    "weight": info["weight"],
                    "contribution": round(info["weighted_contribution"], 2),
                    "components": info["components"],
                    "confidence": round(info.get("confidence", 1.0), 2),
                }
                for name, info in self.breakdown.items()
            },
            "timestamp": self.timestamp.isoformat(),
        }
