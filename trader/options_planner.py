"""Options trade planner — pure planning with no order submission.

Produces TradePlan DB rows for ranked candidates. The execution layer
consumes approved plans separately. This module is safe to call even
in dry-run or approve mode; it never touches IBKR order submission.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import EquitySnapshot, Order, TradePlan
from common.time import utcnow
from trader.greeks import (
    GreeksService,
    GreeksGate,
    GreeksLogger,
    StrikeSelectionCriteria,
    StrikeSelector,
    calculate_limit_price,
)
from trader.ranking import RankedSymbol

log = get_logger(__name__)

_STRATEGY_MAP = {
    "bullish": "bull_call_debit_spread",
    "bearish": "bear_put_debit_spread",
}
_DIRECTION_MAP = {
    "bullish": "bull",
    "bearish": "bear",
}


def _select_expiry(chains: list, dte_min: int, dte_max: int, dte_target: int,
                   dte_fallback_min: int) -> Optional[str]:
    """Pick expiry nearest to dte_target within [dte_min, dte_max]; fallback to [dte_fallback_min, dte_min)."""
    today = datetime.now().date()
    best: Optional[str] = None
    best_diff = 9999

    for chain in chains:
        for exp_str in chain.expirations:
            try:
                exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
            except ValueError:
                continue
            dte = (exp_date - today).days
            if dte_min <= dte <= dte_max:
                diff = abs(dte - dte_target)
                if diff < best_diff:
                    best_diff = diff
                    best = exp_str

    if best:
        return best

    # Fallback to nearest >= dte_fallback_min
    best_diff = 9999
    for chain in chains:
        for exp_str in chain.expirations:
            try:
                exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
            except ValueError:
                continue
            dte = (exp_date - today).days
            if dte_fallback_min <= dte < dte_min:
                diff = abs(dte - dte_target)
                if diff < best_diff:
                    best_diff = diff
                    best = exp_str
    return best


def _dte(expiry_str: str) -> int:
    exp_date = datetime.strptime(expiry_str, "%Y%m%d").date()
    return (exp_date - datetime.now().date()).days


def _check_cooldown(symbol: str, cooldown_hours: int) -> Optional[str]:
    """Return skip reason if a recent plan exists within cooldown window, else None."""
    cutoff = utcnow() - timedelta(hours=cooldown_hours)
    with get_db() as db:
        recent = (
            db.query(TradePlan)
            .filter(
                TradePlan.symbol == symbol,
                TradePlan.status.in_(["proposed", "approved", "submitted"]),
                TradePlan.ts >= cutoff.replace(tzinfo=None),
            )
            .first()
        )
    if recent:
        return f"cooldown: last plan {recent.ts} within {cooldown_hours}h"
    return None


def _check_max_trades_today(max_trades: int) -> Optional[str]:
    """Return skip reason if max daily trades already reached, else None."""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    with get_db() as db:
        count = (
            db.query(TradePlan)
            .filter(
                TradePlan.status.in_(["approved", "submitted"]),
                TradePlan.ts >= today_start,
            )
            .count()
        )
    if count >= max_trades:
        return f"max_trades_per_day: {count}/{max_trades} reached"
    return None


def _get_nav() -> float:
    """Return latest net liquidation value, or 0."""
    with get_db() as db:
        row = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
    return row.net_liquidation if row else 0.0


def _save_plan(
    *,
    symbol: str,
    bias: str,
    strategy: str,
    status: str,
    skip_reason: Optional[str] = None,
    expiry: Optional[str] = None,
    dte: Optional[int] = None,
    legs: Optional[dict] = None,
    pricing: Optional[dict] = None,
    rationale: Optional[dict] = None,
) -> TradePlan:
    plan = TradePlan(
        ts=utcnow().replace(tzinfo=None),
        symbol=symbol,
        bias=bias,
        strategy=strategy,
        expiry=expiry,
        dte=dte,
        legs_json=json.dumps(legs or {}),
        pricing_json=json.dumps(pricing or {}),
        rationale_json=json.dumps(rationale or {}),
        status=status,
        skip_reason=skip_reason,
    )
    with get_db() as db:
        db.add(plan)
    return plan


def plan_trade(candidate: RankedSymbol, client=None) -> Optional[TradePlan]:
    """Build a TradePlan for a ranked candidate without submitting any orders.

    Returns the persisted TradePlan (status "proposed" or "skipped"), or None
    on unrecoverable errors.
    """
    cfg = get_config()
    rc = cfg.ranking
    sym = candidate.symbol
    bias = candidate.bias or "bullish"
    strategy = _STRATEGY_MAP[bias]

    # ── Cadence guards ──────────────────────────────────────────
    cooldown_reason = _check_cooldown(sym, rc.cooldown_hours)
    if cooldown_reason:
        log.info("Skipping %s: %s", sym, cooldown_reason)
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason=cooldown_reason,
                          rationale={"score": candidate.score_total, "components": candidate.components})

    daily_reason = _check_max_trades_today(rc.max_trades_per_day)
    if daily_reason:
        log.info("Skipping %s: %s", sym, daily_reason)
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason=daily_reason,
                          rationale={"score": candidate.score_total})

    # ── IBKR option chain ───────────────────────────────────────
    if client is None:
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason="no_ibkr_client",
                          rationale={"score": candidate.score_total, "components": candidate.components})

    try:
        chains = client.option_chains(sym)
    except Exception as e:
        log.error("Option chain fetch failed for %s: %s", sym, e)
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason=f"chain_fetch_error: {e}",
                          rationale={"score": candidate.score_total})

    if not chains:
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason="no_option_chains",
                          rationale={"score": candidate.score_total})

    expiry = _select_expiry(chains, rc.dte_min, rc.dte_max, rc.dte_target, rc.dte_fallback_min)
    if not expiry:
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason=f"no_suitable_expiry_dte_{rc.dte_min}_{rc.dte_max}",
                          rationale={"score": candidate.score_total})

    dte_val = _dte(expiry)

    # ── Greeks pipeline ─────────────────────────────────────────
    greeks_service = GreeksService(client)
    strike_selector = StrikeSelector(greeks_service)
    greeks_gate = GreeksGate()
    greeks_logger = GreeksLogger()

    try:
        chain_greeks = greeks_service.fetch_chain_greeks(sym, expiry)
    except Exception as e:
        log.error("Greeks fetch failed for %s: %s", sym, e)
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason=f"greeks_fetch_error: {e}",
                          rationale={"score": candidate.score_total})

    direction = _DIRECTION_MAP[bias]
    right = "C" if direction == "bull" else "P"
    if not chain_greeks.valid_legs(right):
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason="no_greeks_data",
                          rationale={"score": candidate.score_total, "expiry": expiry})

    base_criteria = StrikeSelectionCriteria()
    criteria = strike_selector.adjust_delta_for_iv(base_criteria, chain_greeks.iv_rank)

    spread = strike_selector.select_debit_spread_strikes(chain_greeks, direction, criteria)
    if spread is None:
        greeks_logger.log_strike_selection(None, criteria, 0, reason_if_none="no_delta_match")
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason="no_delta_matched_strikes",
                          rationale={"score": candidate.score_total, "expiry": expiry,
                                     "iv_rank": chain_greeks.iv_rank})

    greeks_logger.log_strike_selection(spread, criteria, 0)

    # ── Liquidity + pricing checks ───────────────────────────────
    limit_price = calculate_limit_price(spread)
    if limit_price <= 0:
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason="invalid_limit_price",
                          rationale={"score": candidate.score_total})

    gate_result = greeks_gate.evaluate(spread, chain_greeks, spread.strategy_type)
    greeks_logger.log_gate_result(gate_result, spread)
    if not gate_result.approved:
        return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                          skip_reason=f"greeks_gate: {gate_result.reason}",
                          rationale={"score": candidate.score_total,
                                     "checks_failed": gate_result.checks_failed,
                                     "greeks": gate_result.greeks_summary})

    # ── Risk sizing ──────────────────────────────────────────────
    max_loss_per_contract = round(limit_price * 100, 2)
    nav = _get_nav()
    if nav > 0:
        max_allowed_usd = nav * (cfg.risk.max_risk_per_trade_pct / 100)
        if max_loss_per_contract > max_allowed_usd:
            return _save_plan(symbol=sym, bias=bias, strategy=strategy, status="skipped",
                              skip_reason=f"max_loss_${max_loss_per_contract:.0f}_exceeds_limit_${max_allowed_usd:.0f}",
                              rationale={"score": candidate.score_total})
        qty = max(1, int(max_allowed_usd // max_loss_per_contract))
    else:
        qty = 1

    total_max_loss = max_loss_per_contract * qty
    width = abs(spread.short_strike - spread.long_strike)
    max_profit = (width - limit_price) * 100 * qty

    legs = {
        "long_strike": spread.long_strike,
        "short_strike": spread.short_strike,
        "right": spread.right,
        "expiry": spread.expiration,
        "long_delta": getattr(spread.long_greeks, "delta", None),
        "short_delta": getattr(spread.short_greeks, "delta", None),
        "iv_rank": chain_greeks.iv_rank,
    }
    pricing = {
        "debit_per_contract": round(limit_price, 4),
        "max_loss_per_contract": round(max_loss_per_contract, 2),
        "max_loss_total": round(total_max_loss, 2),
        "max_profit_total": round(max_profit, 2),
        "spread_width": round(width, 2),
        "quantity": qty,
    }
    rationale = {
        "score_total": candidate.score_total,
        "components": candidate.components,
        "sources": candidate.sources,
        "iv_environment": getattr(criteria, "iv_environment", "unknown"),
        "gate_warnings": gate_result.warnings,
    }

    log.info(
        "Trade plan: %s %s exp=%s %s/%s debit=%.4f max_loss=$%.0f qty=%d",
        sym, strategy, expiry, spread.long_strike, spread.short_strike,
        limit_price, total_max_loss, qty,
    )

    return _save_plan(
        symbol=sym,
        bias=bias,
        strategy=strategy,
        status="proposed",
        expiry=expiry,
        dte=dte_val,
        legs=legs,
        pricing=pricing,
        rationale=rationale,
    )
