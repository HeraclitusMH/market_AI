"""Sentiment factor with contrarian extreme handling."""
from __future__ import annotations

from trader.composite_scorer.factors.base import BaseFactor, combine
from trader.composite_scorer.normalization.normalizer import score_higher_better, score_lower_better


def apply_contrarian_logic(raw_sentiment_score: float) -> float:
    if raw_sentiment_score > 90 or raw_sentiment_score < 10:
        return 100 - raw_sentiment_score
    return raw_sentiment_score


class SentimentFactor(BaseFactor):
    name = "sentiment"
    weights = {
        "analyst_revision_ratio": 0.25,
        "short_interest_change": 0.20,
        "options_put_call_skew": 0.20,
        "news_social_sentiment": 0.20,
        "institutional_ownership_change": 0.15,
    }

    def calculate(self, symbol: str, data: dict):
        upgrades = data.get("upgrades_30d")
        downgrades = data.get("downgrades_30d")
        revision = None
        if isinstance(upgrades, (int, float)) or isinstance(downgrades, (int, float)):
            up = float(upgrades or 0)
            down = float(downgrades or 0)
            revision = up / max(up + down, 1.0) * 100
        si_change = None
        if all(isinstance(data.get(k), (int, float)) for k in ("short_interest_now", "short_interest_20d_ago", "shares_outstanding")):
            shares = float(data["shares_outstanding"])
            if shares > 0:
                si_change = -(float(data["short_interest_now"]) - float(data["short_interest_20d_ago"])) / shares
        skew = None
        if isinstance(data.get("put_25_delta_iv"), (int, float)) and isinstance(data.get("call_25_delta_iv"), (int, float)):
            skew = float(data["put_25_delta_iv"]) - float(data["call_25_delta_iv"])
        news = None
        existing = data.get("sentiment_factor")
        if isinstance(existing, dict) and existing.get("value_0_1") is not None:
            news = float(existing["value_0_1"]) * 100
        elif isinstance(data.get("news_social_sentiment"), (int, float)):
            raw = float(data["news_social_sentiment"])
            news = (raw + 1.0) / 2.0 * 100 if -1.0 <= raw <= 1.0 else raw
        inst = None
        if all(isinstance(data.get(k), (int, float)) for k in ("institutional_shares_latest_13f", "institutional_shares_previous_13f", "shares_outstanding")):
            shares = float(data["shares_outstanding"])
            if shares > 0:
                inst = (float(data["institutional_shares_latest_13f"]) - float(data["institutional_shares_previous_13f"])) / shares

        weights = dict(self.weights)
        if skew is None:
            weights["options_put_call_skew"] = 0.0
        if revision is None:
            weights["analyst_revision_ratio"] = 0.0
        if data.get("institutional_data_stale_days", 0) and data.get("institutional_data_stale_days") > 60:
            weights["institutional_ownership_change"] = min(weights["institutional_ownership_change"], 0.05)

        scores = {
            "analyst_revision_ratio": revision,
            "short_interest_change": score_higher_better(si_change, -0.05, 0.05),
            "options_put_call_skew": score_lower_better(skew, 0.0, 0.30),
            "news_social_sentiment": news,
            "institutional_ownership_change": score_higher_better(inst, -0.05, 0.05),
        }
        raw_score, used = combine(scores, weights)
        final_score = apply_contrarian_logic(raw_score)
        confidence = 0.3 if not any(v is not None for v in scores.values()) else 1.0
        return self.result(final_score, {"raw_score": raw_score, "subscores": scores, "weights_used": used}, confidence)
