"""Quality, value, and growth factors backed by existing fundamentals data."""
from __future__ import annotations

from typing import Optional

from trader.composite_scorer.factors.base import BaseFactor, combine
from trader.composite_scorer.normalization.normalizer import score_higher_better, score_lower_better


class QualityFactor(BaseFactor):
    name = "quality"
    weights = {
        "roe_stability": 0.25,
        "gross_margin_trend": 0.20,
        "debt_ebitda": 0.20,
        "fcf_consistency": 0.20,
        "earnings_quality": 0.15,
    }

    def calculate(self, symbol: str, data: dict):
        metrics = data.get("fundamental_metrics", {})
        scores = {
            "roe_stability": score_lower_better(_cv(metrics.get("roe_history")), 0.05, 0.75),
            "gross_margin_trend": score_higher_better(_slope(metrics.get("gross_margin_history")), -0.02, 0.02),
            "debt_ebitda": score_lower_better(_ratio(metrics.get("total_debt"), metrics.get("ebitda_ttm")), 0.0, 10.0),
            "fcf_consistency": score_higher_better(_positive_ratio(metrics.get("fcf_history")), 0.0, 1.0),
            "earnings_quality": score_lower_better(_accruals(metrics), 0.0, 0.15),
        }
        score, used = combine(scores, self.weights)
        existing = _pillar_score(data, "profitability")
        if all(v is None for v in scores.values()) and existing is not None:
            score = existing
            used = {"profitability_pillar": 1.0}
        confidence = _coverage(used)
        return self.result(score, {"subscores": scores, "weights_used": used}, confidence)


class ValueFactor(BaseFactor):
    name = "value"
    weights = {"ev_ebitda": 0.30, "fcf_yield": 0.30, "peg": 0.20, "price_relative": 0.20}

    def calculate(self, symbol: str, data: dict):
        metrics = data.get("fundamental_metrics", {})
        ev_ebitda = metrics.get("enterprise_to_ebitda", metrics.get("ev_ebitda"))
        sector_ev = metrics.get("sector_median_ev_ebitda")
        relative_ev = None if ev_ebitda is None or ev_ebitda < 0 else _relative_discount(ev_ebitda, sector_ev)
        fcf_yield = _ratio(metrics.get("free_cash_flow_ttm"), metrics.get("market_cap"))
        peg = _ratio(metrics.get("forward_pe"), metrics.get("eps_growth_next_year"))
        pb = metrics.get("price_to_book")
        ps = metrics.get("price_to_sales")
        sector_pb = metrics.get("sector_median_price_to_book")
        sector_ps = metrics.get("sector_median_price_to_sales")
        rel_price = _relative_discount(pb, sector_pb) if pb is not None else _relative_discount(ps, sector_ps)
        scores = {
            "ev_ebitda": score_higher_better(relative_ev, -1.0, 1.0),
            "fcf_yield": score_higher_better(fcf_yield, -0.05, 0.12),
            "peg": score_lower_better(peg, 0.5, 3.0),
            "price_relative": score_higher_better(rel_price, -1.0, 1.0),
        }
        score, used = combine(scores, self.weights)
        existing = _pillar_score(data, "valuation")
        if all(v is None for v in scores.values()) and existing is not None:
            score = existing
            used = {"valuation_pillar": 1.0}
        confidence = 0.3 if sum(1 for v in scores.values() if v is not None) < 2 and existing is None else _coverage(used)
        return self.result(score, {"subscores": scores, "weights_used": used}, confidence)


class GrowthFactor(BaseFactor):
    name = "growth"
    weights = {"revenue": 0.30, "eps_forward": 0.30, "tam_share": 0.20, "guidance": 0.20}

    def calculate(self, symbol: str, data: dict):
        metrics = data.get("fundamental_metrics", {})
        rev_hist = metrics.get("revenue_history")
        yoy = _yoy_growth(rev_hist)
        accel = _revenue_acceleration(rev_hist)
        revenue_signal = None if yoy is None and accel is None else ((yoy or 0.0) * 0.5 + (accel or 0.0) * 0.5)
        eps_fy1 = metrics.get("eps_fy1_estimate")
        eps_fy2 = metrics.get("eps_fy2_estimate")
        if isinstance(eps_fy1, (int, float)) and eps_fy1 < 0 and isinstance(eps_fy2, (int, float)) and eps_fy2 > 0:
            eps_score = 80.0
        else:
            eps_score = score_higher_better(_ratio_delta(eps_fy2, eps_fy1), -0.2, 0.5)
        share_gain = None
        if metrics.get("revenue_growth_rate") is not None and metrics.get("sector_revenue_growth_rate") is not None:
            share_gain = float(metrics["revenue_growth_rate"]) - float(metrics["sector_revenue_growth_rate"])
        guidance = None
        if metrics.get("company_guidance_midpoint") is not None and metrics.get("consensus_estimate") not in (None, 0):
            guidance = (float(metrics["company_guidance_midpoint"]) - float(metrics["consensus_estimate"])) / abs(float(metrics["consensus_estimate"]))
        scores = {
            "revenue": score_higher_better(revenue_signal, -0.1, 0.4),
            "eps_forward": eps_score,
            "tam_share": score_higher_better(share_gain, -0.1, 0.2),
            "guidance": 50.0 if guidance is None else score_higher_better(guidance, -0.1, 0.1),
        }
        score, used = combine(scores, self.weights)
        existing = _pillar_score(data, "growth")
        if all(name == "guidance" or v is None for name, v in scores.items()) and existing is not None:
            score = existing
            used = {"growth_pillar": 1.0}
        confidence = max(0.1, _coverage(used) - (0.1 if guidance is None else 0.0))
        return self.result(score, {"subscores": scores, "weights_used": used}, confidence)


def _pillar_score(data: dict, pillar: str) -> Optional[float]:
    fund = data.get("fundamentals_factor")
    try:
        value = fund["metrics"]["pillars"][pillar]["score"]
        return float(value)
    except Exception:
        return None


def _coverage(weights_used: dict[str, float]) -> float:
    if not weights_used:
        return 0.3
    used = sum(w for w in weights_used.values() if w > 0)
    return max(0.3, min(1.0, used))


def _ratio(num, den) -> Optional[float]:
    if not isinstance(num, (int, float)) or not isinstance(den, (int, float)) or den == 0:
        return None
    return float(num) / float(den)


def _ratio_delta(new, old) -> Optional[float]:
    if not isinstance(new, (int, float)) or not isinstance(old, (int, float)) or old == 0:
        return None
    return float(new) / abs(float(old)) - 1.0


def _relative_discount(value, sector_median) -> Optional[float]:
    if not isinstance(value, (int, float)) or not isinstance(sector_median, (int, float)) or sector_median == 0:
        return None
    return (float(sector_median) - float(value)) / float(sector_median)


def _cv(values) -> Optional[float]:
    vals = [float(v) for v in values or [] if isinstance(v, (int, float))]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    variance = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    return (variance ** 0.5) / abs(mean)


def _slope(values) -> Optional[float]:
    vals = [float(v) for v in values or [] if isinstance(v, (int, float))]
    if len(vals) < 2:
        return None
    n = len(vals)
    xs = list(range(n))
    xbar = sum(xs) / n
    ybar = sum(vals) / n
    den = sum((x - xbar) ** 2 for x in xs)
    return None if den == 0 else sum((xs[i] - xbar) * (vals[i] - ybar) for i in range(n)) / den


def _positive_ratio(values) -> Optional[float]:
    vals = [float(v) for v in values or [] if isinstance(v, (int, float))]
    if not vals:
        return None
    return sum(1 for v in vals if v > 0) / len(vals)


def _accruals(metrics: dict) -> Optional[float]:
    ni = metrics.get("net_income")
    ocf = metrics.get("operating_cash_flow")
    assets = metrics.get("total_assets")
    if not isinstance(ni, (int, float)) or not isinstance(ocf, (int, float)) or not isinstance(assets, (int, float)) or assets == 0:
        return None
    return abs((float(ni) - float(ocf)) / float(assets))


def _yoy_growth(values) -> Optional[float]:
    vals = [float(v) for v in values or [] if isinstance(v, (int, float))]
    if len(vals) < 5 or vals[-5] == 0:
        return None
    return vals[-1] / vals[-5] - 1.0


def _revenue_acceleration(values) -> Optional[float]:
    vals = [float(v) for v in values or [] if isinstance(v, (int, float))]
    if len(vals) < 6 or vals[-5] == 0 or vals[-6] == 0:
        return None
    return (vals[-1] / vals[-5] - 1.0) - (vals[-2] / vals[-6] - 1.0)
