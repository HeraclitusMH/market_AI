"""Order construction and execution — debit spreads only."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from ib_insync import (
    Contract, ComboLeg, Order as IBOrder, Stock, Option, TagValue,
)

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import Order, EventLog
from common.time import utcnow
from trader.ibkr_client import IBKRClient, get_ibkr_client
from trader.risk import (
    check_can_trade, compute_max_risk_for_trade, is_approve_mode,
    check_duplicate_intent, log_event,
)
from trader.strategy import SignalIntent

log = get_logger(__name__)


@dataclass
class SpreadSpec:
    symbol: str
    expiry: str            # YYYYMMDD
    long_strike: float
    short_strike: float
    right: str             # "C" for call, "P" for put
    spread_type: str       # "bull_call_spread" or "bear_put_spread"
    net_debit: float       # estimated debit per spread
    max_loss: float        # net_debit * 100 (per contract)
    quantity: int


def _generate_intent_id(symbol: str, direction: str) -> str:
    """Unique intent ID to prevent duplicate orders."""
    date_str = utcnow().strftime("%Y%m%d")
    return f"{symbol}_{direction}_{date_str}_{uuid.uuid4().hex[:8]}"


def select_expiry(chains: list, dte_min: int, dte_max: int) -> Optional[str]:
    """Pick an expiry between dte_min and dte_max days from now."""
    today = datetime.now().date()
    best = None
    best_diff = 999

    for chain in chains:
        for exp_str in chain.expirations:
            try:
                exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
            except ValueError:
                continue
            dte = (exp_date - today).days
            if dte_min <= dte <= dte_max:
                diff = abs(dte - (dte_min + dte_max) // 2)  # prefer middle
                if diff < best_diff:
                    best_diff = diff
                    best = exp_str

    return best


def find_strikes_for_spread(
    chain: list,
    expiry: str,
    direction: str,
    cfg_options,
) -> Optional[Tuple[float, float]]:
    """Find suitable long/short strikes from the chain.

    For bull call spread: long lower strike, short higher strike.
    For bear put spread: long higher strike, short lower strike.
    """
    # Find strikes for this expiry
    strikes = None
    exchange = None
    for c in chain:
        if expiry in c.expirations:
            strikes = sorted(c.strikes)
            exchange = c.exchange
            break

    if not strikes or len(strikes) < 3:
        return None

    # For simplicity in v1, pick strikes around ATM
    # We'll refine with delta-based selection when we have option Greeks
    mid_idx = len(strikes) // 2
    width = cfg_options.max_spread_width

    if direction == "long":
        # Bull call spread: buy lower, sell higher
        long_strike = strikes[mid_idx - 1] if mid_idx > 0 else strikes[0]
        # Find short strike that's within max_spread_width
        short_strike = None
        for s in strikes[mid_idx:]:
            if s - long_strike <= width and s > long_strike:
                short_strike = s
        if short_strike is None and mid_idx + 1 < len(strikes):
            short_strike = strikes[mid_idx + 1]
        if short_strike is None:
            return None
        return (long_strike, short_strike)

    else:  # bearish
        # Bear put spread: buy higher put, sell lower put
        long_strike = strikes[mid_idx + 1] if mid_idx + 1 < len(strikes) else strikes[-1]
        short_strike = None
        for s in reversed(strikes[:mid_idx + 1]):
            if long_strike - s <= width and s < long_strike:
                short_strike = s
        if short_strike is None and mid_idx - 1 >= 0:
            short_strike = strikes[mid_idx - 1]
        if short_strike is None:
            return None
        return (long_strike, short_strike)


def build_combo_order(
    spec: SpreadSpec,
    client: IBKRClient,
) -> Tuple[Contract, IBOrder]:
    """Build IBKR BAG combo contract + limit order for a debit spread."""
    cfg = get_config()

    # Qualify individual legs
    if spec.spread_type == "bull_call_spread":
        long_opt = Option(spec.symbol, spec.expiry, spec.long_strike, "C", "SMART")
        short_opt = Option(spec.symbol, spec.expiry, spec.short_strike, "C", "SMART")
    else:  # bear_put_spread
        long_opt = Option(spec.symbol, spec.expiry, spec.long_strike, "P", "SMART")
        short_opt = Option(spec.symbol, spec.expiry, spec.short_strike, "P", "SMART")

    long_opt = client.qualify_contract(long_opt)
    short_opt = client.qualify_contract(short_opt)

    # Build BAG contract
    combo = Contract()
    combo.symbol = spec.symbol
    combo.secType = "BAG"
    combo.currency = "USD"
    combo.exchange = "SMART"

    leg1 = ComboLeg()
    leg1.conId = long_opt.conId
    leg1.ratio = 1
    leg1.action = "BUY"
    leg1.exchange = "SMART"

    leg2 = ComboLeg()
    leg2.conId = short_opt.conId
    leg2.ratio = 1
    leg2.action = "SELL"
    leg2.exchange = "SMART"

    combo.comboLegs = [leg1, leg2]

    # Limit order for the net debit
    order = IBOrder()
    order.action = "BUY"
    order.orderType = cfg.execution.order_type
    order.totalQuantity = spec.quantity
    order.lmtPrice = round(spec.net_debit, 2)
    order.tif = cfg.execution.tif

    return combo, order


def execute_signal(intent: SignalIntent, client: IBKRClient | None = None) -> Optional[str]:
    """Full execution pipeline for a signal intent.

    Returns the intent_id if order created/submitted, None if skipped.
    """
    cfg = get_config()
    if client is None:
        client = get_ibkr_client()

    intent_id = _generate_intent_id(intent.symbol, intent.direction)

    # Check for duplicate
    if check_duplicate_intent(intent_id):
        log.warning("Duplicate intent %s — skipping.", intent_id)
        return None

    # Fill in max risk
    intent.max_risk_usd = compute_max_risk_for_trade(intent)
    if intent.max_risk_usd <= 0:
        log_event("WARNING", "risk", f"Cannot compute max risk for {intent.symbol}", {"intent_id": intent_id})
        return None

    # Risk checks
    allowed, reason = check_can_trade(intent)
    if not allowed:
        log_event("INFO", "risk_block", f"{intent.symbol}: {reason}", {"intent_id": intent_id})
        log.info("Risk check blocked %s: %s", intent.symbol, reason)
        return None

    # Options enabled?
    with get_db() as db:
        from common.models import BotState
        state = db.query(BotState).first()
        options_on = state.options_enabled if state else False

    if not options_on:
        log.info("Options disabled — skipping %s", intent.symbol)
        return None

    # Fetch option chains
    try:
        chains = client.option_chains(intent.symbol)
    except Exception as e:
        log.error("Failed to get option chains for %s: %s", intent.symbol, e)
        return None

    if not chains:
        log.warning("No option chains for %s", intent.symbol)
        return None

    # Select expiry
    expiry = select_expiry(chains, cfg.options.dte_min, cfg.options.dte_max)
    if not expiry:
        log.warning("No suitable expiry for %s (DTE %d-%d)", intent.symbol, cfg.options.dte_min, cfg.options.dte_max)
        return None

    # Find strikes
    strikes = find_strikes_for_spread(chains, expiry, intent.direction, cfg.options)
    if not strikes:
        log.warning("No suitable strikes for %s spread on %s", intent.direction, intent.symbol)
        return None

    long_strike, short_strike = strikes
    spread_width = abs(short_strike - long_strike)

    # Estimate debit (conservative: use spread_width * 0.4 as estimate)
    # In production, we'd use actual option prices
    est_debit = round(spread_width * 0.4, 2)
    max_loss = est_debit * 100  # per contract

    # Check max loss fits
    if max_loss > intent.max_risk_usd:
        log.info("Spread max loss $%.2f > allowed $%.2f for %s", max_loss, intent.max_risk_usd, intent.symbol)
        return None

    # Determine quantity
    qty = max(1, int(intent.max_risk_usd / max_loss))
    total_max_loss = max_loss * qty

    spread_type = "bull_call_spread" if intent.direction == "long" else "bear_put_spread"
    spec = SpreadSpec(
        symbol=intent.symbol,
        expiry=expiry,
        long_strike=long_strike,
        short_strike=short_strike,
        right="C" if intent.direction == "long" else "P",
        spread_type=spread_type,
        net_debit=est_debit,
        max_loss=total_max_loss,
        quantity=qty,
    )

    # Create order record
    order_payload = {
        "spread_type": spread_type,
        "expiry": expiry,
        "long_strike": long_strike,
        "short_strike": short_strike,
        "est_debit": est_debit,
        "quantity": qty,
        "score": intent.score,
        "components": intent.components,
    }

    # If approve mode, save as pending and don't submit
    if is_approve_mode():
        with get_db() as db:
            db.add(Order(
                intent_id=intent_id,
                symbol=intent.symbol,
                direction=intent.direction,
                instrument=spread_type,
                quantity=qty,
                order_type=cfg.execution.order_type,
                limit_price=est_debit,
                status="pending_approval",
                max_loss=total_max_loss,
                payload_json=json.dumps(order_payload),
            ))
        log_event("INFO", "signal", f"Signal for {intent.symbol} pending approval", order_payload)
        log.info("Approve mode: %s signal saved for approval (max_loss=$%.2f)", intent.symbol, total_max_loss)
        return intent_id

    # Build and submit combo order
    try:
        combo_contract, ib_order = build_combo_order(spec, client)
        trade = client.place_order(combo_contract, ib_order)

        with get_db() as db:
            db.add(Order(
                intent_id=intent_id,
                symbol=intent.symbol,
                direction=intent.direction,
                instrument=spread_type,
                quantity=qty,
                order_type=cfg.execution.order_type,
                limit_price=est_debit,
                status="submitted",
                ibkr_order_id=trade.order.orderId if trade else None,
                max_loss=total_max_loss,
                payload_json=json.dumps(order_payload),
            ))

        log_event("INFO", "order_submitted",
                   f"Submitted {spread_type} for {intent.symbol}: {qty}x {long_strike}/{short_strike} @ ${est_debit}",
                   order_payload)
        log.info("Order submitted: %s %s %dx %s/%s exp=%s debit=$%.2f",
                 intent.symbol, spread_type, qty, long_strike, short_strike, expiry, est_debit)
        return intent_id

    except Exception as e:
        log.error("Failed to place order for %s: %s", intent.symbol, e)
        log_event("ERROR", "order_failed", f"Failed to place {intent.symbol}: {e}", order_payload)
        return None
