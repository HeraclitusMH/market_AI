"""Market regime detector with transition smoothing."""
from __future__ import annotations


class RegimeDetector:
    def __init__(self, current_regime: str = "rotation_choppy", transition_threshold: int = 3) -> None:
        self.current_regime = current_regime
        self.candidate_regime: str | None = None
        self.candidate_days = 0
        self.transition_threshold = max(1, int(transition_threshold))

    def update(self, market_data: dict) -> str:
        new_signal = self._detect(market_data or {})
        if new_signal != self.current_regime:
            if new_signal == self.candidate_regime:
                self.candidate_days += 1
            else:
                self.candidate_regime = new_signal
                self.candidate_days = 1
            if self.candidate_days >= self.transition_threshold:
                self.current_regime = new_signal
                self.candidate_regime = None
                self.candidate_days = 0
        else:
            self.candidate_regime = None
            self.candidate_days = 0
        return self.current_regime

    def detect_now(self, market_data: dict) -> str:
        return self._detect(market_data or {})

    def _detect(self, market_data: dict) -> str:
        vix = _num(market_data.get("vix_current"), 20.0)
        vix_20d_ago = _num(market_data.get("vix_20d_ago"), vix)
        vix_trend = (vix - vix_20d_ago) / vix_20d_ago if vix_20d_ago else 0.0
        breadth = _num(market_data.get("sp500_pct_above_50ma"), 50.0)
        spy_price = _num(market_data.get("spy_price"), 0.0)
        spy_ema_50 = _num(market_data.get("spy_ema_50"), spy_price)
        spy_ema_200 = _num(market_data.get("spy_ema_200"), spy_price)

        spy_above_50 = spy_price > spy_ema_50
        spy_above_200 = spy_price > spy_ema_200

        if vix > 25 and not spy_above_50 and breadth < 40:
            return "bear_high_vol"
        if vix > 20 and vix_trend < -0.15 and 40 < breadth < 65:
            return "recovery"
        if vix < 18 and spy_above_50 and spy_above_200 and breadth > 60:
            return "bull_low_vol"
        return "rotation_choppy"


def _num(value: object, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) else default
