"""Risk penalty factor."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from trader.composite_scorer.factors.base import BaseFactor, combine
from trader.composite_scorer.normalization.normalizer import score_higher_better


class RiskFactor(BaseFactor):
    name = "risk"
    weights = {
        "realized_vs_implied_vol": 0.25,
        "max_drawdown": 0.25,
        "downside_beta": 0.20,
        "liquidity": 0.15,
        "event_risk": 0.15,
    }

    def calculate(self, symbol: str, data: dict):
        df = data.get("bars")
        if not isinstance(df, pd.DataFrame) or df.empty or len(df) < 20:
            legacy = data.get("risk_factor")
            if isinstance(legacy, dict) and legacy.get("value_0_1") is not None:
                penalty = (1.0 - float(legacy["value_0_1"])) * 100
                return self.result(penalty, {"legacy_risk": legacy}, 0.7)
            return self.result(50.0, {"status": "missing"}, 0.3, "stale_1d")
        close = df["close"].astype(float)
        volume = df["volume"].astype(float) if "volume" in df else pd.Series([0.0] * len(close))
        returns = close.pct_change().dropna()
        realized = float(returns.tail(20).std() * math.sqrt(252)) if len(returns) >= 20 else None
        implied = data.get("implied_vol_30d")
        vol_ratio = realized / float(implied) if realized is not None and isinstance(implied, (int, float)) and implied > 0 else None
        vol_score = score_higher_better(vol_ratio, 0.8, 1.8)
        if vol_score is None and realized is not None:
            vol_score = score_higher_better(realized, 0.15, 0.80)

        window = close.tail(min(126, len(close)))
        peak = window.cummax()
        mdd = abs(float((window / peak - 1.0).min()))
        dd_score = score_higher_better(mdd, 0.05, 0.60)
        downside_beta = _downside_beta(returns, data.get("market_returns"))
        beta_score = score_higher_better(downside_beta, 0.5, 2.5)
        adv = float((close.tail(min(20, len(close))) * volume.tail(min(20, len(volume)))).mean())
        liquidity_score = _liquidity_penalty(adv, data.get("average_bid_ask_spread"))
        event_score = _event_risk(data.get("next_earnings_date"), data.get("days_to_earnings"))
        scores = {
            "realized_vs_implied_vol": vol_score,
            "max_drawdown": dd_score,
            "downside_beta": beta_score,
            "liquidity": liquidity_score,
            "event_risk": event_score,
        }
        score, used = combine(scores, self.weights)
        confidence = 0.7 if len(close) < 126 else 1.0
        return self.result(score, {"subscores": scores, "weights_used": used, "metrics": {"realized_vol_20d": realized, "max_drawdown_6m": mdd, "adv_20d": adv}}, confidence)


def _downside_beta(stock_returns: pd.Series, market_returns) -> float | None:
    if market_returns is None:
        return None
    market = pd.Series(market_returns, dtype=float).dropna()
    stock = stock_returns.tail(len(market)).reset_index(drop=True)
    market = market.tail(len(stock)).reset_index(drop=True)
    mask = market < 0
    if mask.sum() < 5:
        return None
    market_neg = market[mask]
    stock_neg = stock[mask]
    var = float(np.var(market_neg, ddof=1))
    if var == 0:
        return None
    return float(np.cov(stock_neg, market_neg)[0, 1] / var)


def _liquidity_penalty(avg_dollar_volume: float, spread: object = None) -> float:
    if avg_dollar_volume > 50_000_000:
        base = 0.0
    elif avg_dollar_volume > 10_000_000:
        base = 25.0
    elif avg_dollar_volume > 1_000_000:
        base = 60.0
    else:
        base = 100.0
    if isinstance(spread, (int, float)) and spread > 0.02:
        base = min(100.0, base + 15.0)
    return base


def _event_risk(next_earnings_date, days_to_earnings) -> float:
    days = days_to_earnings
    if days is None and next_earnings_date is not None:
        if isinstance(next_earnings_date, datetime):
            target = next_earnings_date.date()
        elif isinstance(next_earnings_date, date):
            target = next_earnings_date
        else:
            try:
                target = datetime.fromisoformat(str(next_earnings_date)).date()
            except ValueError:
                target = None
        if target is not None:
            days = (target - datetime.now(timezone.utc).date()).days
    if days is None:
        return 0.0
    if days <= 7:
        return 100.0
    if days <= 14:
        return 50.0
    return 0.0
