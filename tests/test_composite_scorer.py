"""Tests for the 7-factor composite scorer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trader.composite_scorer import CompositeScorer
from trader.composite_scorer.factors.sentiment import apply_contrarian_logic
from trader.composite_scorer.factors.technical import TechnicalFactor
from trader.composite_scorer.normalization.normalizer import (
    min_max_normalize,
    normalize_inverted,
    percentile_rank_normalize,
)
from trader.composite_scorer.regime.regime_detector import RegimeDetector


def _bars(n: int = 260, start: float = 50.0, end: float = 120.0, volume: int = 1_000_000) -> pd.DataFrame:
    close = np.linspace(start, end, n)
    return pd.DataFrame(
        {
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": volume,
        }
    )


def test_percentile_rank_normalize_handles_edges():
    assert percentile_rank_normalize(5, [5, 5, 5]) == pytest.approx(50.0)
    assert 0 <= percentile_rank_normalize(-10, [-10, 0, 10]) <= 100
    assert normalize_inverted(10, [0, 5, 10]) < normalize_inverted(0, [0, 5, 10])
    assert min_max_normalize(5, 5, 5) == pytest.approx(50.0)


def test_regime_detector_classifies_known_shapes_without_smoothing():
    detector = RegimeDetector(transition_threshold=1)
    bear = detector.update(
        {
            "vix_current": 60,
            "vix_20d_ago": 30,
            "sp500_pct_above_50ma": 15,
            "spy_price": 240,
            "spy_ema_50": 280,
            "spy_ema_200": 300,
        }
    )
    assert bear == "bear_high_vol"

    bull = detector.update(
        {
            "vix_current": 12,
            "vix_20d_ago": 14,
            "sp500_pct_above_50ma": 75,
            "spy_price": 460,
            "spy_ema_50": 440,
            "spy_ema_200": 410,
        }
    )
    assert bull == "bull_low_vol"


def test_regime_detector_requires_three_days_by_default():
    detector = RegimeDetector()
    market_data = {
        "vix_current": 12,
        "vix_20d_ago": 14,
        "sp500_pct_above_50ma": 75,
        "spy_price": 460,
        "spy_ema_50": 440,
        "spy_ema_200": 410,
    }
    assert detector.update(market_data) == "rotation_choppy"
    assert detector.update(market_data) == "rotation_choppy"
    assert detector.update(market_data) == "bull_low_vol"


def test_sentiment_contrarian_extremes_flip():
    assert apply_contrarian_logic(95) == pytest.approx(5)
    assert apply_contrarian_logic(5) == pytest.approx(95)
    assert apply_contrarian_logic(60) == pytest.approx(60)


def test_technical_factor_outputs_valid_score():
    result = TechnicalFactor().calculate("AAPL", {"bars": _bars()})
    assert 0 <= result.score <= 100
    assert result.confidence == pytest.approx(1.0)
    assert "trend_alignment" in result.components["subscores"]


def test_composite_scorer_subtracts_risk_and_returns_breakdown():
    cfg = {
        "default_weights": {
            "quality": 0.20,
            "value": 0.15,
            "momentum": 0.15,
            "growth": 0.15,
            "sentiment": 0.15,
            "technical": 0.10,
            "risk": 0.10,
        },
        "regime_weights": {
            "rotation_choppy": {
                "quality": 0.20,
                "value": 0.15,
                "momentum": 0.15,
                "growth": 0.15,
                "sentiment": 0.15,
                "technical": 0.10,
                "risk": 0.10,
            }
        },
    }
    scorer = CompositeScorer(cfg)
    stock_data = {
        "bars": _bars(),
        "sentiment_factor": {"value_0_1": 0.8, "status": "ok"},
        "momentum_trend_factor": {"value_0_1": 0.7, "status": "ok"},
        "risk_factor": {"value_0_1": 0.2, "status": "ok"},
        "days_to_earnings": 3,
        "fundamentals_factor": {
            "value_0_1": 0.6,
            "status": "ok",
            "metrics": {
                "pillars": {
                    "valuation": {"score": 60},
                    "profitability": {"score": 70},
                    "growth": {"score": 65},
                }
            },
        },
    }
    result = scorer.score("AAPL", {}, stock_data)
    assert 0 <= result.score <= 100
    assert result.breakdown["risk"]["weighted_contribution"] < 0
    assert set(result.breakdown) == {"quality", "value", "momentum", "growth", "sentiment", "technical", "risk"}
    assert result.to_dict()["composite_score"] == round(result.score, 2)
