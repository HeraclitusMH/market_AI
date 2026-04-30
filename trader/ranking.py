"""Symbol ranking: composite score plus eligibility gates."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import SentimentSnapshot, SymbolRanking
from common.time import utcnow
from trader.sentiment.scoring import get_latest_ticker_score
from trader.universe import UniverseItem

# Re-export internal helpers so existing tests can import them from here
from trader.scoring import (
    _compute_score,
    _age_hours,
    _apply_recency,
    compute_sentiment_factor,
    compute_liquidity_factor,
    compute_optionability_factor,
    compute_momentum_trend_factor,
    compute_risk_factor,
    compute_fundamentals_factor,
)

log = get_logger(__name__)

_COMPOSITE_SCORER = None


@dataclass
class RankedSymbol:
    symbol: str
    sector: str
    score_total: float
    components: Dict   # full factor breakdown JSON
    eligible: bool     # overall gate (contract verified + liquidity)
    reasons: List[str]
    sources: List[str]
    bias: Optional[str] = None          # "bullish" | "bearish" | None
    equity_eligible: bool = True        # passes liquidity gate → can trade equity
    options_eligible: bool = False      # passes options gate (safe-by-default: False)
    name: str = ""                      # human-readable company/fund name


# ── Sentiment snapshot helpers ────────────────────────────────────────────────

def _get_market_snap() -> Optional[SentimentSnapshot]:
    with get_db() as db:
        return (
            db.query(SentimentSnapshot)
            .filter(SentimentSnapshot.scope == "market")
            .order_by(SentimentSnapshot.id.desc())
            .first()
        )


def _get_sector_snap(sector: str) -> Optional[SentimentSnapshot]:
    if not sector:
        return None
    with get_db() as db:
        return (
            db.query(SentimentSnapshot)
            .filter(SentimentSnapshot.scope == "sector", SentimentSnapshot.key == sector)
            .order_by(SentimentSnapshot.id.desc())
            .first()
        )


# ── Eligibility gate ──────────────────────────────────────────────────────────

def _check_eligibility(item: UniverseItem) -> Tuple[bool, List[str]]:
    """Hard-reject filters before ranking. Returns (eligible, rejection_reasons)."""
    reasons: List[str] = []
    if not item.verified:
        reasons.append("contract_not_verified")
        return False, reasons

    with get_db() as db:
        from common.models import Universe
        row = db.query(Universe).filter(Universe.symbol == item.symbol).first()
        if row is not None and not row.active:
            try:
                metrics = json.loads(row.liquidity_metrics_json or "{}")
                adv = metrics.get("avg_dollar_volume", 0)
                price = metrics.get("last_price", 0)
                cfg = get_config()
                if price < cfg.universe.min_price:
                    reasons.append(f"price_too_low_{price:.2f}")
                if adv < cfg.ranking.min_dollar_volume:
                    reasons.append(f"low_dollar_volume_{adv:.0f}")
            except Exception:
                reasons.append("liquidity_data_unavailable")
            return False, reasons

    return True, reasons


# ── Persistence ───────────────────────────────────────────────────────────────

def _persist_rankings(results: List[RankedSymbol], now: datetime) -> None:
    with get_db() as db:
        for r in results:
            db.add(SymbolRanking(
                ts=now.replace(tzinfo=None),
                symbol=r.symbol,
                score_total=r.score_total,
                components_json=json.dumps(r.components),
                eligible=r.eligible,
                reasons_json=json.dumps(r.reasons),
            ))


# ── Main ranking function ─────────────────────────────────────────────────────

def rank_symbols(
    universe: List[UniverseItem],
    now: Optional[datetime] = None,
    client=None,
) -> List[RankedSymbol]:
    """Score and rank universe symbols with the 7-factor composite scorer.

    Existing sentiment, momentum/trend, risk, and fundamentals helpers feed the
    scorer as adapter inputs.

    Liquidity is a hard eligibility gate and does not contribute to score_total.
    Score is in [0, 1].
    """
    from trader.market_data import get_latest_bars

    cfg = get_config()
    rc = cfg.ranking
    now = now or datetime.now(timezone.utc)

    market_snap = _get_market_snap()

    results: List[RankedSymbol] = []
    for item in universe:
        sector_snap = _get_sector_snap(item.sector)
        ticker_snap = get_latest_ticker_score(item.symbol)

        # ── Sentiment factor ──────────────────────────────────────────────
        sent_factor = compute_sentiment_factor(
            market_snap, sector_snap, ticker_snap,
            rc.w_market, rc.w_sector, rc.w_ticker,
        )

        # ── Bar-based factors (safe: empty df → factor missing) ───────────
        try:
            df = get_latest_bars(item.symbol, "1D", client)
        except Exception:
            df = __import__("pandas").DataFrame()

        liq_factor = compute_liquidity_factor(df, cfg)
        mt_factor = compute_momentum_trend_factor(df)
        risk_factor = compute_risk_factor(df)

        # ── Options eligibility ───────────────────────────────────────────
        opt_factor = compute_optionability_factor(item.symbol, client)

        # ── Fundamentals (stub) ───────────────────────────────────────────
        fund_factor = compute_fundamentals_factor(item.symbol, cfg, client)

        # ── Composite ─────────────────────────────────────────────────────
        factors = {
            "sentiment":      sent_factor,
            "momentum_trend": mt_factor,
            "risk":           risk_factor,
            "fundamentals":   fund_factor,
        }
        composite_7factor = _score_7factor(item.symbol, df, factors)
        total_score = round(composite_7factor.score / 100.0, 4)
        weights_used = {
            name: info["weight"]
            for name, info in composite_7factor.breakdown.items()
        }

        # ── Eligibility gates ─────────────────────────────────────────────
        base_eligible, base_reasons = _check_eligibility(item)
        liq_reasons = liq_factor.get("reasons", [])
        eligible = base_eligible and liq_factor.get("eligible", True)
        reasons = base_reasons + liq_reasons
        equity_eligible = eligible
        options_eligible = opt_factor.get("eligible", False)

        # ── Bias ──────────────────────────────────────────────────────────
        bias: Optional[str] = None
        bearish_threshold = 1.0 - rc.enter_threshold
        if eligible:
            if total_score >= rc.enter_threshold:
                bias = "bullish"
            elif total_score <= bearish_threshold:
                bias = "bearish"

        # ── Full components blob ──────────────────────────────────────────
        components = {
            "sentiment":      sent_factor,
            "liquidity":      liq_factor,
            "optionability":  opt_factor,
            "momentum_trend": mt_factor,
            "risk":           risk_factor,
            "fundamentals":   fund_factor,
            "weights_used":   weights_used,
            "total_score":    total_score,
            "composite_7factor": composite_7factor.to_dict(),
            "eligibility": {
                "equity_eligible":   equity_eligible,
                "options_eligible":  options_eligible,
                "reasons":           reasons,
            },
        }

        results.append(RankedSymbol(
            symbol=item.symbol,
            sector=item.sector,
            score_total=total_score,
            components=components,
            eligible=eligible,
            reasons=reasons,
            sources=list(item.sources),
            bias=bias,
            equity_eligible=equity_eligible,
            options_eligible=options_eligible,
            name=item.name,
        ))

    results.sort(key=lambda r: r.score_total, reverse=True)
    _persist_rankings(results, now)
    log.info(
        "Ranked %d symbols (%d equity_eligible, %d options_eligible, %d with bias).",
        len(results),
        sum(1 for r in results if r.equity_eligible),
        sum(1 for r in results if r.options_eligible),
        sum(1 for r in results if r.bias),
    )
    return results

def _score_7factor(symbol: str, df, factors: Dict[str, dict]):
    global _COMPOSITE_SCORER
    from trader.composite_scorer import CompositeScorer

    cfg = get_config()
    scoring_cfg = getattr(cfg, "scoring", None)
    config_path = getattr(scoring_cfg, "config_path", None)
    use_cache = getattr(scoring_cfg, "use_cache", False)
    use_cache = use_cache if isinstance(use_cache, bool) else False
    if _COMPOSITE_SCORER is None:
        if config_path:
            from trader.composite_scorer.composite_scorer import load_scoring_config

            _COMPOSITE_SCORER = CompositeScorer(load_scoring_config(config_path), use_cache=use_cache)
        else:
            _COMPOSITE_SCORER = CompositeScorer(use_cache=use_cache)
    stock_data = {
        "bars": df,
        "sentiment_factor": factors.get("sentiment"),
        "momentum_trend_factor": factors.get("momentum_trend"),
        "risk_factor": factors.get("risk"),
        "fundamentals_factor": factors.get("fundamentals"),
    }
    return _COMPOSITE_SCORER.score(symbol, {}, stock_data)


# ── Candidate selection ───────────────────────────────────────────────────────

def select_candidates(
    ranked: List[RankedSymbol],
    max_total: Optional[int] = None,
    threshold: Optional[float] = None,
    fallback_broad_etf: Optional[bool] = None,
) -> List[RankedSymbol]:
    """Select up to max_total candidates split between bullish and bearish.

    Requires r.eligible and r.bias set. Options bot should additionally filter
    by r.options_eligible after calling this function.
    """
    cfg = get_config()
    rc = cfg.ranking
    max_total = max_total if max_total is not None else rc.max_candidates_total
    fallback = fallback_broad_etf if fallback_broad_etf is not None else rc.fallback_trade_broad_etf

    eligible = [r for r in ranked if r.eligible and r.bias is not None]

    bullish = [r for r in eligible if r.bias == "bullish"]
    bearish = [r for r in eligible if r.bias == "bearish"]

    n_bull = min(len(bullish), max(1, max_total - 1))
    n_bear = min(len(bearish), max_total - n_bull)
    if n_bull + n_bear < max_total and len(bullish) > n_bull:
        n_bull = min(len(bullish), max_total - n_bear)

    selected = bullish[:n_bull] + bearish[:n_bear]
    selected = selected[:max_total]

    if not selected and fallback:
        market_snap = _get_market_snap()
        mkt_score = market_snap.score if market_snap else 0.0
        fallback_sym = "SPY" if mkt_score >= 0 else "QQQ"
        fallback_bias = "bullish" if mkt_score >= 0 else "bearish"
        for r in ranked:
            if r.symbol == fallback_sym and r.eligible:
                r_copy = RankedSymbol(
                    symbol=r.symbol, sector=r.sector, score_total=r.score_total,
                    components=r.components, eligible=r.eligible, reasons=["fallback_broad_etf"],
                    sources=r.sources, bias=fallback_bias,
                    equity_eligible=r.equity_eligible,
                    options_eligible=r.options_eligible,
                )
                selected = [r_copy]
                log.info("No candidates above threshold — falling back to %s (%s)",
                         fallback_sym, fallback_bias)
                break

    log.info("Selected %d candidates (%d bullish, %d bearish).",
             len(selected),
             sum(1 for c in selected if c.bias == "bullish"),
             sum(1 for c in selected if c.bias == "bearish"))
    return selected
