"""Momentum factor."""
from __future__ import annotations

import pandas as pd

from trader.composite_scorer.factors.base import BaseFactor, combine
from trader.composite_scorer.normalization.normalizer import score_higher_better


class MomentumFactor(BaseFactor):
    name = "momentum"
    weights = {"price_momentum_12_1": 0.40, "earnings_momentum": 0.35, "relative_strength": 0.25}

    def calculate(self, symbol: str, data: dict):
        df = data.get("bars")
        scores = {"price_momentum_12_1": None, "earnings_momentum": None, "relative_strength": None}
        metrics = {}
        if isinstance(df, pd.DataFrame) and not df.empty and "close" in df:
            close = df["close"].astype(float)
            n = len(close)
            if n >= 253:
                ret_12m = close.iloc[-1] / close.iloc[-253] - 1.0
                ret_1m = close.iloc[-1] / close.iloc[-22] - 1.0 if n >= 22 else 0.0
                mom = ret_12m - ret_1m
                scores["price_momentum_12_1"] = score_higher_better(mom, -0.5, 0.8)
                metrics["momentum_12_1"] = round(float(mom), 4)
            elif n >= 64:
                annualized = (close.iloc[-1] / close.iloc[0]) ** (252 / max(n - 1, 1)) - 1.0
                scores["price_momentum_12_1"] = score_higher_better(annualized, -0.5, 0.8)
                metrics["annualized_available_return"] = round(float(annualized), 4)
            if n >= 64:
                stock_3m = close.iloc[-1] / close.iloc[-64] - 1.0
                sector_3m = data.get("sector_return_3m", 0.0)
                rel = stock_3m - float(sector_3m if isinstance(sector_3m, (int, float)) else 0.0)
                scores["relative_strength"] = score_higher_better(rel, -0.25, 0.25)
                metrics["relative_strength_3m"] = round(float(rel), 4)

        cur = data.get("current_eps_estimate")
        prev = data.get("eps_estimate_90d_ago")
        if isinstance(cur, (int, float)) and isinstance(prev, (int, float)) and prev != 0:
            eps_rev = (float(cur) - float(prev)) / abs(float(prev))
            scores["earnings_momentum"] = score_higher_better(eps_rev, -0.3, 0.3)
            metrics["eps_revision_pct"] = round(eps_rev, 4)

        if scores["price_momentum_12_1"] is None:
            existing = self.from_existing(data.get("momentum_trend_factor"), "momentum_trend")
            if existing is not None:
                return existing
        score, used = combine(scores, self.weights)
        confidence = 0.6 if len([v for v in scores.values() if v is not None]) == 1 else 1.0
        return self.result(score, {"subscores": scores, "weights_used": used, "metrics": metrics}, confidence)
