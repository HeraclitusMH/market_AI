"""Symbol ranking: combine market + sector + ticker sentiment into ranked candidates."""
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

log = get_logger(__name__)


@dataclass
class RankedSymbol:
    symbol: str
    sector: str
    score_total: float
    components: Dict   # {market, sector, ticker} each with {raw, age_hours, weight, contribution, status}
    eligible: bool
    reasons: List[str]
    sources: List[str]
    bias: Optional[str] = None   # "bullish" | "bearish" | None (within threshold)


def _age_hours(snap: Optional[SentimentSnapshot]) -> Optional[float]:
    if snap is None:
        return None
    ts = snap.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600


def _apply_recency(score: float, age_h: Optional[float]) -> Tuple[float, str]:
    """Apply recency penalty or mark as missing/stale."""
    if age_h is None:
        return 0.0, "missing"
    if age_h > 72:
        return 0.0, "stale"
    if age_h > 24:
        return score * 0.5, "penalized"
    return score, "ok"


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


def _compute_score(
    market_snap: Optional[SentimentSnapshot],
    sector_snap: Optional[SentimentSnapshot],
    ticker_snap: Optional[SentimentSnapshot],
    w_market: float,
    w_sector: float,
    w_ticker: float,
) -> Tuple[float, Dict]:
    mkt_age = _age_hours(market_snap)
    sec_age = _age_hours(sector_snap)
    tkr_age = _age_hours(ticker_snap)

    mkt_raw = market_snap.score if market_snap else 0.0
    sec_raw = sector_snap.score if sector_snap else 0.0
    tkr_raw = ticker_snap.score if ticker_snap else 0.0

    mkt_val, mkt_status = _apply_recency(mkt_raw, mkt_age)
    sec_val, sec_status = _apply_recency(sec_raw, sec_age)
    tkr_val, tkr_status = _apply_recency(tkr_raw, tkr_age)

    have_market = mkt_status in ("ok", "penalized")
    have_sector = sec_status in ("ok", "penalized")
    have_ticker = tkr_status in ("ok", "penalized")

    # Weight redistribution per spec
    if not have_ticker and not have_sector:
        w_m, w_s, w_t = 1.0, 0.0, 0.0
    elif not have_ticker and have_sector:
        w_m, w_s, w_t = 0.35, 0.65, 0.0
    elif have_ticker and not have_sector:
        w_m, w_s, w_t = 0.30, 0.0, 0.70
    else:
        w_m, w_s, w_t = w_market, w_sector, w_ticker

    total = (
        w_m * (mkt_val if have_market else 0.0)
        + w_s * (sec_val if have_sector else 0.0)
        + w_t * (tkr_val if have_ticker else 0.0)
    )
    total = max(-1.0, min(1.0, round(total, 4)))

    components = {
        "market": {
            "raw": round(mkt_raw, 4) if market_snap else None,
            "age_hours": round(mkt_age, 1) if mkt_age is not None else None,
            "weight": round(w_m, 3),
            "contribution": round(w_m * (mkt_val if have_market else 0.0), 4),
            "status": mkt_status,
        },
        "sector": {
            "raw": round(sec_raw, 4) if sector_snap else None,
            "age_hours": round(sec_age, 1) if sec_age is not None else None,
            "weight": round(w_s, 3),
            "contribution": round(w_s * (sec_val if have_sector else 0.0), 4),
            "status": sec_status,
        },
        "ticker": {
            "raw": round(tkr_raw, 4) if ticker_snap else None,
            "age_hours": round(tkr_age, 1) if tkr_age is not None else None,
            "weight": round(w_t, 3),
            "contribution": round(w_t * (tkr_val if have_ticker else 0.0), 4),
            "status": tkr_status,
        },
    }
    return total, components


def _check_eligibility(item: UniverseItem) -> Tuple[bool, List[str]]:
    """Hard-reject filters before ranking. Returns (eligible, rejection_reasons)."""
    reasons: List[str] = []
    if not item.verified:
        reasons.append("contract_not_verified")
        return False, reasons

    # Check cached liquidity from Universe table
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


def rank_symbols(
    universe: List[UniverseItem],
    now: Optional[datetime] = None,
) -> List[RankedSymbol]:
    """Score and rank all verified universe symbols using sentiment components."""
    cfg = get_config()
    rc = cfg.ranking
    now = now or datetime.now(timezone.utc)

    market_snap = _get_market_snap()

    results: List[RankedSymbol] = []
    for item in universe:
        sector_snap = _get_sector_snap(item.sector)
        ticker_snap = get_latest_ticker_score(item.symbol)

        score, components = _compute_score(
            market_snap, sector_snap, ticker_snap,
            rc.w_market, rc.w_sector, rc.w_ticker,
        )

        eligible, reasons = _check_eligibility(item)

        bias: Optional[str] = None
        if eligible:
            if score >= rc.enter_threshold:
                bias = "bullish"
            elif score <= -rc.enter_threshold:
                bias = "bearish"

        results.append(RankedSymbol(
            symbol=item.symbol,
            sector=item.sector,
            score_total=score,
            components=components,
            eligible=eligible,
            reasons=reasons,
            sources=list(item.sources),
            bias=bias,
        ))

    results.sort(key=lambda r: r.score_total, reverse=True)
    _persist_rankings(results, now)
    log.info("Ranked %d symbols (%d eligible, %d with bias).",
             len(results),
             sum(1 for r in results if r.eligible),
             sum(1 for r in results if r.bias))
    return results


def select_candidates(
    ranked: List[RankedSymbol],
    max_total: Optional[int] = None,
    threshold: Optional[float] = None,
    fallback_broad_etf: Optional[bool] = None,
) -> List[RankedSymbol]:
    """Select up to max_total candidates split between bullish and bearish.

    Applies threshold filter; optionally falls back to SPY/QQQ when no symbol qualifies.
    """
    cfg = get_config()
    rc = cfg.ranking
    max_total = max_total if max_total is not None else rc.max_candidates_total
    threshold = threshold if threshold is not None else rc.enter_threshold
    fallback = fallback_broad_etf if fallback_broad_etf is not None else rc.fallback_trade_broad_etf

    eligible = [r for r in ranked if r.eligible and r.bias is not None]

    bullish = [r for r in eligible if r.bias == "bullish"]
    bearish = [r for r in eligible if r.bias == "bearish"]

    # Allocate: prefer 2 bullish + 1 bearish; adjust if supply is limited
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
            if r.symbol == fallback_sym:
                r_copy = RankedSymbol(
                    symbol=r.symbol, sector=r.sector, score_total=r.score_total,
                    components=r.components, eligible=True, reasons=["fallback_broad_etf"],
                    sources=r.sources, bias=fallback_bias,
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
