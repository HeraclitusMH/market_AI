"""Tests for multi-factor composite scoring (trader/scoring.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from trader.scoring import (
    compute_composite,
    compute_liquidity_factor,
    compute_momentum_trend_factor,
    compute_optionability_factor,
    compute_risk_factor,
    compute_sentiment_factor,
    _compute_score,
    _age_hours,
    _apply_recency,
)


# ─────────────────────── Helpers ────────────────────────────────────────────

def _make_df(n: int, close_start: float = 100.0, vol: float = 0.01,
             volume: int = 10_000_000) -> pd.DataFrame:
    """Synthetic OHLCV bars. Vol controls daily return std."""
    np.random.seed(42)
    returns = np.random.normal(0, vol, n)
    closes = close_start * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        "date": pd.date_range("2023-01-01", periods=n, freq="B"),
        "open": closes * 0.999,
        "high": closes * 1.005,
        "low": closes * 0.995,
        "close": closes,
        "volume": volume,
    })
    return df


def _snap(score: float, age_hours: float = 0.0):
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    s = MagicMock()
    s.score = score
    s.timestamp = ts.replace(tzinfo=None)
    return s


def _mock_cfg(min_price=5.0, min_dollar_volume=1_000_000):
    cfg = MagicMock()
    cfg.universe.min_price = min_price
    cfg.ranking.min_dollar_volume = min_dollar_volume
    return cfg


# ─────────────────────── ADV$ computation ───────────────────────────────────

def test_liquidity_adv_computed_correctly():
    # 20 bars with close=100 and volume=500_000 → ADV = 50M
    df = _make_df(20, close_start=100.0, vol=0.0, volume=500_000)
    df["close"] = 100.0  # force constant close
    cfg = _mock_cfg(min_price=5.0, min_dollar_volume=1_000_000)
    result = compute_liquidity_factor(df, cfg)
    assert result["status"] == "ok"
    assert result["metrics"]["adv_dollar_20d"] == pytest.approx(50_000_000, rel=0.01)


def test_liquidity_eligible_passes_thresholds():
    df = _make_df(25, close_start=50.0, vol=0.0, volume=1_000_000)
    df["close"] = 50.0
    cfg = _mock_cfg(min_price=5.0, min_dollar_volume=20_000_000)
    result = compute_liquidity_factor(df, cfg)
    # 50 * 1M = 50M ADV → above 20M threshold and price above 5
    assert result["eligible"] is True
    assert result["value_0_1"] is not None
    assert result["reasons"] == []


def test_liquidity_ineligible_low_price():
    df = _make_df(25, close_start=3.0, vol=0.0, volume=10_000_000)
    df["close"] = 3.0
    cfg = _mock_cfg(min_price=5.0, min_dollar_volume=1_000_000)
    result = compute_liquidity_factor(df, cfg)
    assert result["eligible"] is False
    assert any("price_too_low" in r for r in result["reasons"])


def test_liquidity_ineligible_low_adv():
    df = _make_df(25, close_start=100.0, vol=0.0, volume=1_000)
    df["close"] = 100.0
    cfg = _mock_cfg(min_price=5.0, min_dollar_volume=20_000_000)
    result = compute_liquidity_factor(df, cfg)
    assert result["eligible"] is False
    assert any("low_adv_dollar" in r for r in result["reasons"])


def test_liquidity_missing_when_empty():
    result = compute_liquidity_factor(pd.DataFrame(), _mock_cfg())
    assert result["status"] == "missing"
    assert result["eligible"] is True  # don't block when no data
    assert result["value_0_1"] is None


# ─────────────────────── Momentum / Trend score ──────────────────────────────

def test_momentum_trend_missing_when_few_bars():
    df = _make_df(30)  # < 63 required
    result = compute_momentum_trend_factor(df)
    assert result["status"] == "missing"
    assert result["value_0_1"] is None


def test_momentum_trend_ok_with_enough_bars():
    df = _make_df(200)
    result = compute_momentum_trend_factor(df)
    assert result["status"] == "ok"
    assert 0.0 <= result["value_0_1"] <= 1.0


def test_momentum_trend_strong_uptrend():
    # Manufacture a clean uptrend: price increases every day
    n = 250
    closes = np.linspace(50, 200, n)  # strong uptrend
    df = pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="B"),
        "open": closes * 0.99,
        "high": closes * 1.01,
        "low": closes * 0.98,
        "close": closes,
        "volume": 1_000_000,
    })
    result = compute_momentum_trend_factor(df)
    assert result["status"] == "ok"
    assert result["metrics"]["above_sma200"] is True
    assert result["metrics"]["ema_trend_up"] is True
    assert result["value_0_1"] >= 0.7  # strong uptrend → high score


def test_momentum_trend_downtrend():
    n = 250
    closes = np.linspace(200, 50, n)  # strong downtrend
    df = pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="B"),
        "open": closes * 1.01,
        "high": closes * 1.02,
        "low": closes * 0.99,
        "close": closes,
        "volume": 1_000_000,
    })
    result = compute_momentum_trend_factor(df)
    assert result["status"] == "ok"
    assert result["metrics"]["above_sma200"] is False
    assert result["metrics"]["ema_trend_up"] is False
    assert result["value_0_1"] <= 0.3  # downtrend → low score


def test_momentum_trend_returns_present_with_200_bars():
    df = _make_df(250)
    result = compute_momentum_trend_factor(df)
    metrics = result["metrics"]
    assert metrics.get("ret_63d") is not None
    assert metrics.get("ret_126d") is not None
    assert metrics.get("rsi14") is not None


# ─────────────────────── Risk score ─────────────────────────────────────────

def test_risk_missing_when_few_bars():
    df = _make_df(10)  # < 20 required
    result = compute_risk_factor(df)
    assert result["status"] == "missing"
    assert result["value_0_1"] is None


def test_risk_low_vol_high_score():
    # Very low vol (0.2% daily ≈ 3% annualised → well below 15% bucket)
    df = _make_df(260, vol=0.002)
    result = compute_risk_factor(df)
    assert result["status"] == "ok"
    assert result["value_0_1"] >= 0.75  # low vol → high score


def test_risk_high_vol_low_score():
    # High vol (5% daily ≈ 80% annualised)
    df = _make_df(260, vol=0.05)
    result = compute_risk_factor(df)
    assert result["status"] == "ok"
    assert result["value_0_1"] <= 0.5


def test_risk_metrics_present():
    df = _make_df(260, vol=0.01)
    result = compute_risk_factor(df)
    assert "vol_20d_ann" in result["metrics"]
    assert "max_dd_252d" in result["metrics"]
    assert result["metrics"]["vol_20d_ann"] >= 0
    assert 0.0 <= result["metrics"]["max_dd_252d"] <= 1.0


def test_risk_score_in_range():
    df = _make_df(260)
    result = compute_risk_factor(df)
    assert 0.0 <= result["value_0_1"] <= 1.0


# ─────────────────────── Factor weight redistribution ────────────────────────

def test_composite_all_present():
    factors = {
        "sentiment":      {"value_0_1": 0.6},
        "momentum_trend": {"value_0_1": 0.7},
        "risk":           {"value_0_1": 0.8},
        "liquidity":      {"value_0_1": 0.5},
        "fundamentals":   {"value_0_1": 0.4},
    }
    weights = {
        "sentiment": 0.30, "momentum_trend": 0.25,
        "risk": 0.20, "liquidity": 0.15, "fundamentals": 0.10,
    }
    score, wu = compute_composite(factors, weights)
    expected = 0.30*0.6 + 0.25*0.7 + 0.20*0.8 + 0.15*0.5 + 0.10*0.4
    assert score == pytest.approx(expected, abs=0.001)
    assert abs(sum(wu.values()) - 1.0) < 0.001


def test_composite_redistributes_missing():
    factors = {
        "sentiment":      {"value_0_1": 0.6},
        "momentum_trend": {"value_0_1": None},   # missing
        "risk":           {"value_0_1": 0.8},
        "liquidity":      {"value_0_1": None},   # missing
        "fundamentals":   {"value_0_1": None},   # missing
    }
    weights = {
        "sentiment": 0.30, "momentum_trend": 0.25,
        "risk": 0.20, "liquidity": 0.15, "fundamentals": 0.10,
    }
    score, wu = compute_composite(factors, weights)
    # Only sentiment (0.30) and risk (0.20) present → total nominal = 0.50
    # Redistributed: sentiment = 0.30/0.50 = 0.60, risk = 0.20/0.50 = 0.40
    assert wu["sentiment"] == pytest.approx(0.60, abs=0.001)
    assert wu["risk"] == pytest.approx(0.40, abs=0.001)
    assert wu["momentum_trend"] == 0.0
    assert wu["liquidity"] == 0.0
    assert wu["fundamentals"] == 0.0
    expected = 0.60 * 0.6 + 0.40 * 0.8
    assert score == pytest.approx(expected, abs=0.001)


def test_composite_all_missing_returns_neutral():
    factors = {
        "sentiment":      {"value_0_1": None},
        "momentum_trend": {"value_0_1": None},
        "risk":           {"value_0_1": None},
        "liquidity":      {"value_0_1": None},
        "fundamentals":   {"value_0_1": None},
    }
    weights = {
        "sentiment": 0.30, "momentum_trend": 0.25,
        "risk": 0.20, "liquidity": 0.15, "fundamentals": 0.10,
    }
    score, wu = compute_composite(factors, weights)
    assert score == pytest.approx(0.5)
    assert all(w == 0.0 for w in wu.values())


def test_composite_score_clamped_to_unit():
    factors = {name: {"value_0_1": 1.0} for name in ("a", "b")}
    weights = {"a": 0.5, "b": 0.5}
    score, _ = compute_composite(factors, weights)
    assert 0.0 <= score <= 1.0

    factors2 = {name: {"value_0_1": 0.0} for name in ("a", "b")}
    score2, _ = compute_composite(factors2, weights)
    assert 0.0 <= score2 <= 1.0


# ─────────────────────── Optionability — safe-by-default ─────────────────────

def _mock_db_session(first_return):
    """Context manager mock that returns first_return from .query().filter().first()."""
    mock_session = MagicMock()
    mock_session.__enter__ = lambda s: mock_session
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.query.return_value.filter.return_value.first.return_value = first_return
    return mock_session


def test_optionability_no_record_not_eligible():
    with patch("trader.scoring.get_db", return_value=_mock_db_session(None)):
        result = compute_optionability_factor("UNKNOWNSYM")
    assert result["eligible"] is False
    assert result["status"] == "unknown"


def test_optionability_eligible_from_master():
    sm = MagicMock()
    sm.options_eligible = True
    with patch("trader.scoring.get_db", return_value=_mock_db_session(sm)):
        result = compute_optionability_factor("AAPL")
    assert result["eligible"] is True
    assert result["value_0_1"] == 1.0
    assert result["status"] == "ok"


def test_optionability_not_eligible_from_master():
    sm = MagicMock()
    sm.options_eligible = False
    with patch("trader.scoring.get_db", return_value=_mock_db_session(sm)):
        result = compute_optionability_factor("XYZSTOCK")
    assert result["eligible"] is False
    assert result["value_0_1"] == 0.0


def test_optionability_db_exception_returns_not_eligible():
    with patch("trader.scoring.get_db", side_effect=Exception("db down")):
        result = compute_optionability_factor("AAPL")
    assert result["eligible"] is False


# ─────────────────────── Sentiment factor ────────────────────────────────────

def test_sentiment_factor_normalizes_to_0_1():
    mkt = _snap(1.0)   # max bullish → raw≈1 → value_0_1≈1.0
    sec = _snap(1.0)
    tkr = _snap(1.0)
    result = compute_sentiment_factor(mkt, sec, tkr)
    assert result["value_0_1"] == pytest.approx(1.0, abs=0.01)
    assert result["status"] == "ok"


def test_sentiment_factor_all_negative():
    mkt = _snap(-1.0)
    sec = _snap(-1.0)
    tkr = _snap(-1.0)
    result = compute_sentiment_factor(mkt, sec, tkr)
    assert result["value_0_1"] == pytest.approx(0.0, abs=0.01)


def test_sentiment_factor_missing_when_all_stale():
    mkt = _snap(0.5, age_hours=80)
    sec = _snap(0.5, age_hours=80)
    tkr = _snap(0.5, age_hours=80)
    result = compute_sentiment_factor(mkt, sec, tkr)
    assert result["status"] == "missing"
    assert result["value_0_1"] is None
