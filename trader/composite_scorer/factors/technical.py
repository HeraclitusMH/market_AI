"""Technical structure factor."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from trader.composite_scorer.factors.base import BaseFactor, combine
from trader.composite_scorer.normalization.normalizer import clamp, score_higher_better
from trader.indicators import ema


class TechnicalFactor(BaseFactor):
    name = "technical"
    weights = {"trend_alignment": 0.30, "volume_confirmation": 0.25, "support_proximity": 0.25, "volatility_regime": 0.20}

    def calculate(self, symbol: str, data: dict):
        df = data.get("bars")
        if not isinstance(df, pd.DataFrame) or df.empty or len(df) < 20 or "close" not in df:
            return self.result(50.0, {"status": "missing"}, 0.3, "stale_1d")
        close = df["close"].astype(float).reset_index(drop=True)
        volume = df["volume"].astype(float).reset_index(drop=True) if "volume" in df else pd.Series([0.0] * len(close))
        trend = _trend_alignment(close, data.get("direction", "long"))
        volume_score = _volume_confirmation(close, volume)
        support = _support_proximity(close)
        volatility = _volatility_regime(close)
        weights = dict(self.weights)
        avg_volume = float(volume.tail(min(20, len(volume))).mean()) if len(volume) else 0.0
        if avg_volume < 100_000:
            weights["volume_confirmation"] = 0.10
        scores = {
            "trend_alignment": trend,
            "volume_confirmation": volume_score,
            "support_proximity": support,
            "volatility_regime": volatility,
        }
        score, used = combine(scores, weights)
        confidence = 0.7 if len(close) < 200 else 1.0
        return self.result(score, {"subscores": scores, "weights_used": used}, confidence)


def _trend_alignment(close: pd.Series, direction: str) -> float:
    score = 100.0
    ema8 = float(ema(close, 8).iloc[-1])
    ema21 = float(ema(close, 21).iloc[-1])
    ema50 = float(ema(close, 50).iloc[-1])
    last = float(close.iloc[-1])
    ema200 = float(ema(close, 200).iloc[-1]) if len(close) >= 200 else None
    if direction == "short":
        if not (ema8 < ema21):
            score -= 25
        if not (ema21 < ema50):
            score -= 25
        if ema200 is not None and not (ema50 < ema200):
            score -= 25
        if not (last < ema8):
            score -= 25
    else:
        if not (ema8 > ema21):
            score -= 25
        if not (ema21 > ema50):
            score -= 25
        if ema200 is not None and not (ema50 > ema200):
            score -= 25
        if not (last > ema8):
            score -= 25
    return clamp(score)


def _volume_confirmation(close: pd.Series, volume: pd.Series) -> float:
    if len(close) < 20:
        return 50.0
    delta_sign = np.sign(close.diff().fillna(0.0).to_numpy())
    obv = np.cumsum(volume.to_numpy() * delta_sign)
    obv_slope = _slope(obv[-20:])
    price_slope = _slope(close.to_numpy()[-20:])
    magnitude = score_higher_better(abs(obv_slope), 0.0, max(abs(obv).mean(), 1.0) * 0.1) or 50.0
    if math.copysign(1, obv_slope or 1) == math.copysign(1, price_slope or 1):
        return magnitude
    return clamp(50.0 - magnitude * 0.5)


def _support_proximity(close: pd.Series) -> float:
    values = close.to_numpy()
    last = float(values[-1])
    lookback = values[-60:] if len(values) >= 60 else values
    swing_lows = [float(lookback[i]) for i in range(1, len(lookback) - 1) if lookback[i] <= lookback[i - 1] and lookback[i] <= lookback[i + 1]]
    swing_highs = [float(lookback[i]) for i in range(1, len(lookback) - 1) if lookback[i] >= lookback[i - 1] and lookback[i] >= lookback[i + 1]]
    support = max([s for s in swing_lows if s < last], default=last * 0.95)
    resistance = min([r for r in swing_highs if r > last], default=last * 1.05)
    if resistance <= support:
        return 50.0
    position = 1.0 - (last - support) / (resistance - support)
    return clamp(position * 100)


def _volatility_regime(close: pd.Series) -> float:
    if len(close) < 21:
        return 50.0
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    width = ((mid + 2 * std) - (mid - 2 * std)) / mid.replace(0, np.nan)
    hist = width.dropna().tail(100)
    if len(hist) < 5:
        return 50.0
    current = float(hist.iloc[-1])
    pct = float((hist < current).sum() + 0.5 * (hist == current).sum()) / len(hist)
    score = (1.0 - pct) * 100
    if pct < 0.20 and len(hist) >= 2 and current > float(hist.iloc[-2]):
        score += 15
    return clamp(score)


def _slope(values) -> float:
    arr = np.asarray(values, dtype=float)
    if len(arr) < 2:
        return 0.0
    x = np.arange(len(arr), dtype=float)
    return float(np.polyfit(x, arr, 1)[0])
