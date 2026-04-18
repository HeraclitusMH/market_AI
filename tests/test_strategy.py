"""Tests for strategy scoring determinism."""
import os
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.indicators import compute_indicators


def _make_ohlcv(n=250, seed=42):
    np.random.seed(seed)
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open": close - np.random.rand(n) * 0.3,
        "high": close + np.abs(np.random.randn(n)) * 0.5,
        "low": close - np.abs(np.random.randn(n)) * 0.5,
        "close": close,
        "volume": np.random.randint(100_000, 10_000_000, n),
        "date": pd.date_range("2024-01-01", periods=n),
    })


def test_scoring_determinism():
    """Same input produces same indicators."""
    df = _make_ohlcv()
    r1 = compute_indicators(df)
    r2 = compute_indicators(df)

    for key in ["ema20", "ema50", "rsi14", "macd", "atr14"]:
        assert r1[key] == r2[key], f"{key} differs: {r1[key]} vs {r2[key]}"


def test_scoring_different_seeds():
    """Different data produces different indicators."""
    df1 = _make_ohlcv(seed=1)
    df2 = _make_ohlcv(seed=2)
    r1 = compute_indicators(df1)
    r2 = compute_indicators(df2)

    assert r1["ema20"] != r2["ema20"]


def test_trend_signal_consistency():
    """When EMA20 > EMA50, trend_up should be True."""
    df = _make_ohlcv(250, seed=10)
    result = compute_indicators(df)
    assert result["valid"]
    assert result["trend_up"] == (result["ema20"] > result["ema50"])


def test_rsi_range():
    df = _make_ohlcv(250)
    result = compute_indicators(df)
    assert 0 <= result["rsi14"] <= 100
