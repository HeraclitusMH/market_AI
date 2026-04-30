"""Main 7-factor composite scorer orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import yaml

from trader.composite_scorer.factors.fundamental import GrowthFactor, QualityFactor, ValueFactor
from trader.composite_scorer.factors.momentum import MomentumFactor
from trader.composite_scorer.factors.risk import RiskFactor
from trader.composite_scorer.factors.sentiment import SentimentFactor
from trader.composite_scorer.factors.technical import TechnicalFactor
from trader.composite_scorer.models import CompositeResult, FactorResult
from trader.composite_scorer.normalization.normalizer import clamp
from trader.composite_scorer.regime.regime_detector import RegimeDetector


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config") / "scoring_config.yaml"


class CachedFactor:
    def __init__(self, factor, ttl_seconds: int) -> None:
        self.factor = factor
        self.ttl = int(ttl_seconds)
        self.cache: dict[str, tuple[FactorResult, datetime]] = {}

    def calculate(self, symbol: str, data: dict) -> FactorResult:
        now = datetime.now(timezone.utc)
        cached = self.cache.get(symbol)
        if cached is not None:
            result, cached_at = cached
            if (now - cached_at).total_seconds() < self.ttl:
                return result
        result = self.factor.calculate(symbol, data)
        self.cache[symbol] = (result, now)
        return result


class CompositeScorer:
    def __init__(self, config: dict | None = None, *, use_cache: bool = False) -> None:
        self.config = config or load_scoring_config()
        self.regime_detector = RegimeDetector()
        factors = {
            "quality": QualityFactor(),
            "value": ValueFactor(),
            "momentum": MomentumFactor(),
            "growth": GrowthFactor(),
            "sentiment": SentimentFactor(),
            "technical": TechnicalFactor(),
            "risk": RiskFactor(),
        }
        if use_cache:
            ttls = self.config.get("factor_ttls", {})
            self.factors = {
                name: CachedFactor(factor, int(ttls.get(name, 18 * 3600)))
                for name, factor in factors.items()
            }
        else:
            self.factors = factors

    def score(self, symbol: str, market_data: dict | None, stock_data: dict | None) -> CompositeResult:
        stock_data = stock_data or {}
        regime = self.regime_detector.update(market_data or {})
        weights = self.config.get("regime_weights", {}).get(regime) or self.config["default_weights"]
        factor_results: Dict[str, FactorResult] = {
            name: factor.calculate(symbol, stock_data)
            for name, factor in self.factors.items()
        }

        composite = 0.0
        confidence = 0.0
        breakdown = {}
        for name, result in factor_results.items():
            weight = float(weights.get(name, 0.0))
            contribution = -result.score * weight if name == "risk" else result.score * weight
            composite += contribution
            confidence += result.confidence * weight
            breakdown[name] = {
                "raw_score": result.score,
                "weight": weight,
                "weighted_contribution": contribution,
                "components": result.components,
                "confidence": result.confidence,
                "data_staleness": result.data_staleness,
            }

        final_score = clamp(composite + float(weights.get("risk", 0.0)) * 100.0)
        return CompositeResult(
            symbol=symbol,
            score=final_score,
            regime=regime,
            breakdown=breakdown,
            confidence=max(0.0, min(1.0, confidence)),
            timestamp=datetime.now(timezone.utc),
        )


def load_scoring_config(path: str | Path | None = None) -> dict:
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    _validate_weights(config.get("default_weights", {}), "default_weights")
    for regime, weights in (config.get("regime_weights") or {}).items():
        _validate_weights(weights, f"regime_weights.{regime}")
    return config


def _validate_weights(weights: dict, label: str) -> None:
    total = sum(float(v) for v in weights.values())
    if abs(total - 1.0) > 0.0001:
        raise ValueError(f"{label} must sum to 1.0, got {total:.4f}")
