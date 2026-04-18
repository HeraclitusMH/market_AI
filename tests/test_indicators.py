"""Tests for indicator calculations."""
import numpy as np
import pandas as pd
import pytest

from trader.indicators import ema, sma, rsi, macd, atr, compute_indicators


def _make_ohlcv(n=60, start_price=100.0):
    np.random.seed(42)
    close = start_price + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "open": close - np.random.rand(n) * 0.3,
        "high": close + np.abs(np.random.randn(n)) * 0.5,
        "low": close - np.abs(np.random.randn(n)) * 0.5,
        "close": close,
        "volume": np.random.randint(100_000, 10_000_000, n),
        "date": pd.date_range("2024-01-01", periods=n),
    })
    return df


def test_ema_length():
    s = pd.Series(range(100), dtype=float)
    result = ema(s, 20)
    assert len(result) == 100


def test_sma_values():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = sma(s, 3)
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_rsi_bounds():
    df = _make_ohlcv(100)
    r = rsi(df["close"], 14)
    valid = r.dropna()
    assert (valid >= 0).all()
    assert (valid <= 100).all()


def test_macd_keys():
    df = _make_ohlcv(60)
    result = macd(df["close"])
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result


def test_atr_positive():
    df = _make_ohlcv(60)
    a = atr(df, 14)
    valid = a.dropna()
    assert (valid > 0).all()


def test_compute_indicators_valid():
    df = _make_ohlcv(250)
    result = compute_indicators(df)
    assert result["valid"] is True
    assert "ema20" in result
    assert "rsi14" in result
    assert "macd" in result
    assert "atr14" in result
    assert "realized_vol" in result


def test_compute_indicators_insufficient_data():
    df = _make_ohlcv(10)
    result = compute_indicators(df)
    assert result["valid"] is False


def test_indicators_deterministic():
    df = _make_ohlcv(250)
    r1 = compute_indicators(df)
    r2 = compute_indicators(df)
    assert r1["ema20"] == r2["ema20"]
    assert r1["rsi14"] == r2["rsi14"]
    assert r1["macd"] == r2["macd"]
