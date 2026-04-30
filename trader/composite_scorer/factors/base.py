"""Base factor helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from trader.composite_scorer.models import FactorResult
from trader.composite_scorer.normalization.normalizer import clamp, weighted_average


class BaseFactor:
    name = "base"

    def calculate(self, symbol: str, data: dict) -> FactorResult:
        raise NotImplementedError

    def result(
        self,
        score: float,
        components: dict,
        confidence: float = 1.0,
        data_staleness: str = "fresh",
    ) -> FactorResult:
        return FactorResult(
            score=clamp(score),
            components=components,
            confidence=max(0.0, min(1.0, float(confidence))),
            timestamp=datetime.now(timezone.utc),
            data_staleness=data_staleness,
        )

    def from_existing(self, factor: Optional[dict], component_name: str = "legacy") -> Optional[FactorResult]:
        if not isinstance(factor, dict) or factor.get("value_0_1") is None:
            return None
        score = clamp(float(factor["value_0_1"]) * 100)
        return self.result(score, {component_name: factor}, _confidence_from_status(factor.get("status")))


def _confidence_from_status(status: object) -> float:
    if status == "ok":
        return 1.0
    if status in ("penalized", "stale_1d"):
        return 0.7
    if status in ("missing", "disabled", "unknown"):
        return 0.3
    return 0.8


def combine(scores: dict[str, Optional[float]], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    return weighted_average(scores, weights)
