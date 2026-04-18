"""Technical indicators: EMA, SMA, RSI, MACD, ATR."""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def compute_indicators(df: pd.DataFrame) -> Dict[str, object]:
    """Compute all indicators for a DataFrame with OHLCV columns.

    Returns a dict with latest values and helper signals.
    """
    if df.empty or len(df) < 50:
        return {"valid": False, "reason": "insufficient data"}

    close = df["close"]

    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    sma200 = sma(close, 200)
    rsi14 = rsi(close, 14)
    macd_vals = macd(close)
    atr14 = atr(df, 14)

    latest = {
        "valid": True,
        "close": float(close.iloc[-1]),
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "sma200": float(sma200.iloc[-1]) if not np.isnan(sma200.iloc[-1]) else None,
        "rsi14": float(rsi14.iloc[-1]),
        "macd": float(macd_vals["macd"].iloc[-1]),
        "macd_signal": float(macd_vals["signal"].iloc[-1]),
        "macd_histogram": float(macd_vals["histogram"].iloc[-1]),
        "atr14": float(atr14.iloc[-1]),
    }

    # helper signals
    latest["trend_up"] = latest["ema20"] > latest["ema50"]
    latest["above_sma200"] = (
        latest["sma200"] is not None and latest["close"] > latest["sma200"]
    )
    latest["rsi_overbought"] = latest["rsi14"] > 70
    latest["rsi_oversold"] = latest["rsi14"] < 30
    latest["macd_bullish"] = latest["macd_histogram"] > 0

    # realised volatility (annualised, rolling 20 day)
    returns = close.pct_change().dropna()
    if len(returns) >= 20:
        rv = float(returns.tail(20).std() * np.sqrt(252) * 100)
    else:
        rv = 0.0
    latest["realized_vol"] = rv

    return latest
