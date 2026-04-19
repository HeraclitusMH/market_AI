"""Order construction and execution — debit spreads only.

Strike selection is delta-based via GreeksService + StrikeSelector, and every
trade passes through GreeksGate before an order is built. Limit prices come
from real bid/ask midpoints of the selected legs.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from ib_insync import Contract, ComboLeg, Order as IBOrder, Option

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import Order
from common.time import utcnow
from trader.greeks import (
    GreeksService, GreeksGate, GreeksLogger,
    StrikeSelectionCriteria, StrikeSelector, SpreadSelection, calculate_limit_price,
)
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
    right: str             # "C" or "P"
    spread_type: str       # "bull_call_spread" or "bear_put_spread"
    net_debit: float
    max_loss: float        # per-contract max loss in USD (= net_debit * 100)
    quantity: int


def _generate_intent_id(symbol: str, direction: str) -> str:
    date_str = utcnow().strftime("%Y%m%d")
    return f"{symbol}_{direction}_{date_str}_{uuid.uuid4().hex[:8]}"


def select_expiry(chains: list, dte_min: int, dte_max: int) -> Optional[str]:
    """Pick an expiry between dte_min and dte_max days from today."""
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
                diff = abs(dte - (dte_min + dte_max) // 2)
                if diff < best_diff:
                    best_diff = diff
                    best = exp_str

    return best


def build_combo_order(
    spec: SpreadSpec,
    client: IBKRClient,
) -> Tuple[Contract, IBOrder]:
    """Build IBKR BAG combo contract + limit order for a debit spread."""
    cfg = get_config()

    if spec.spread_type == "bull_call_spread":
        long_opt = Option(spec.symbol, spec.expiry, spec.long_strike, "C", "SMART")
        short_opt = Option(spec.symbol, spec.expiry, spec.short_strike, "C", "SMART")
    else:  # bear_put_spread
        long_opt = Option(spec.symbol, spec.expiry, spec.long_strike, "P", "SMART")
        short_opt = Option(spec.symbol, spec.expiry, spec.short_strike, "P", "SMART")

    long_opt = client.qualify_contract(long_opt)
    short_opt = client.qualify_contract(short_opt)

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
    if check_duplicate_intent(intent_id):
        log.warning("Duplicate intent %s — skipping.", intent_id)
        return None

    intent.max_risk_usd = compute_max_risk_for_trade(intent)
    if intent.max_risk_usd <= 0:
        log_event("WARNING", "risk", f"Cannot compute max risk for {intent.symbol}", {"intent_id": intent_id})
        return None

    allowed, reason = check_can_trade(intent)
    if not allowed:
        log_event("INFO", "risk_block", f"{intent.symbol}: {reason}", {"intent_id": intent_id})
        log.info("Risk check blocked %s: %s", intent.symbol, reason)
        return None

    with get_db() as db:
        from common.models import BotState
        state = db.query(BotState).first()
        options_on = state.options_enabled if state else False
    if not options_on:
        log.info("Options disabled — skipping %s", intent.symbol)
        return None

    try:
        chains = client.option_chains(intent.symbol)
    except Exception as e:
        log.error("Failed to get option chains for %s: %s", intent.symbol, e)
        return None
    if not chains:
        log.warning("No option chains for %s", intent.symbol)
        return None

    expiry = select_expiry(chains, cfg.options.dte_min, cfg.options.dte_max)
    if not expiry:
        log.warning("No suitable expiry for %s (DTE %d-%d)",
                    intent.symbol, cfg.options.dte_min, cfg.options.dte_max)
        return None

    # ── Greeks pipeline ─────────────────────────────────────
    greeks_service = GreeksService(client)
    strike_selector = StrikeSelector(greeks_service)
    greeks_gate = GreeksGate()
    greeks_logger = GreeksLogger()

    try:
        chain_greeks = greeks_service.fetch_chain_greeks(intent.symbol, expiry)
    except Exception as e:
        log.error("Greeks fetch failed for %s: %s", intent.symbol, e)
        log_event("ERROR", "greeks_fetch_failed", f"{intent.symbol}: {e}", {"intent_id": intent_id})
        return None

    greeks_logger.log_chain_fetch(chain_greeks)

    candidates = len(chain_greeks.valid_legs("C")) + len(chain_greeks.valid_legs("P"))
    if candidates == 0:
        log.warning("No Greeks data received for %s. Check IBKR market data subscription.", intent.symbol)
        log_event("WARNING", "greeks_empty", f"{intent.symbol}: no Greeks data", {"intent_id": intent_id})
        return None

    # Direction mapping: strategy uses "long"/"bearish"; selector uses "bull"/"bear".
    selector_direction = "bull" if intent.direction == "long" else "bear"

    base_criteria = StrikeSelectionCriteria()
    criteria = strike_selector.adjust_delta_for_iv(base_criteria, chain_greeks.iv_rank)

    spread = strike_selector.select_debit_spread_strikes(
        chain_greeks, selector_direction, criteria
    )

    if spread is None:
        greeks_logger.log_strike_selection(
            None, criteria, candidates, reason_if_none="no valid delta-matched strikes"
        )
        log.warning("No delta-matched strikes for %s exp=%s", intent.symbol, expiry)
        log_event("INFO", "no_strikes", f"{intent.symbol}: no delta-matched strikes", {
            "intent_id": intent_id, "expiry": expiry, "iv_rank": chain_greeks.iv_rank,
        })
        return None

    greeks_logger.log_strike_selection(spread, criteria, candidates)

    gate_result = greeks_gate.evaluate(spread, chain_greeks, spread.strategy_type)
    greeks_logger.log_gate_result(gate_result, spread)
    if not gate_result.approved:
        log_event("INFO", "greeks_gate_reject", f"{intent.symbol}: {gate_result.reason}", {
            "intent_id": intent_id,
            "checks_failed": gate_result.checks_failed,
            "greeks_summary": gate_result.greeks_summary,
        })
        return None

    # ── Build the spec from real prices ─────────────────────
    limit_price = calculate_limit_price(spread)
    per_contract_max_loss = round(limit_price * 100, 2)
    if per_contract_max_loss > intent.max_risk_usd:
        log.info("Per-contract max loss $%.2f > allowed $%.2f for %s",
                 per_contract_max_loss, intent.max_risk_usd, intent.symbol)
        return None

    qty = max(1, int(intent.max_risk_usd // per_contract_max_loss))
    total_max_loss = per_contract_max_loss * qty

    spread_type = "bull_call_spread" if selector_direction == "bull" else "bear_put_spread"
    spec = SpreadSpec(
        symbol=intent.symbol,
        expiry=spread.expiration,
        long_strike=spread.long_strike,
        short_strike=spread.short_strike,
        right=spread.right,
        spread_type=spread_type,
        net_debit=limit_price,
        max_loss=total_max_loss,
        quantity=qty,
    )

    order_payload = {
        "spread_type": spread_type,
        "expiry": spread.expiration,
        "long_strike": spread.long_strike,
        "short_strike": spread.short_strike,
        "limit_price": limit_price,
        "quantity": qty,
        "score": intent.score,
        "components": intent.components,
        "greeks": gate_result.greeks_summary,
        "iv_environment": criteria.iv_environment,
        "gate_warnings": gate_result.warnings,
    }

    if is_approve_mode():
        with get_db() as db:
            db.add(Order(
                intent_id=intent_id,
                symbol=intent.symbol,
                direction=intent.direction,
                instrument=spread_type,
                quantity=qty,
                order_type=cfg.execution.order_type,
                limit_price=limit_price,
                status="pending_approval",
                max_loss=total_max_loss,
                payload_json=json.dumps(order_payload),
            ))
        greeks_logger.log_greeks_at_entry(spread, intent_id)
        log_event("INFO", "signal", f"Signal for {intent.symbol} pending approval", order_payload)
        log.info("Approve mode: %s signal saved for approval (max_loss=$%.2f)",
                 intent.symbol, total_max_loss)
        return intent_id

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
                limit_price=limit_price,
                status="submitted",
                ibkr_order_id=trade.order.orderId if trade else None,
                max_loss=total_max_loss,
                payload_json=json.dumps(order_payload),
            ))

        greeks_logger.log_greeks_at_entry(spread, intent_id)
        log_event("INFO", "order_submitted",
                  f"Submitted {spread_type} for {intent.symbol}: {qty}x "
                  f"{spread.long_strike}/{spread.short_strike} @ ${limit_price}",
                  order_payload)
        log.info("Order submitted: %s %s %dx %s/%s exp=%s debit=$%.2f",
                 intent.symbol, spread_type, qty, spread.long_strike, spread.short_strike,
                 spread.expiration, limit_price)
        return intent_id

    except Exception as e:
        log.error("Failed to place order for %s: %s", intent.symbol, e)
        log_event("ERROR", "order_failed", f"Failed to place {intent.symbol}: {e}", order_payload)
        return None
