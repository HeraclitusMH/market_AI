"""Equity stock order placement for EquitySwingBot.

All orders are tagged with portfolio_id='equity_swing' for isolation.
Dry-run and approve-mode are fully supported.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import BotState, EquitySnapshot, Order, Position, TradeManagement
from common.time import utcnow
from trader.risk import log_event

log = get_logger(__name__)

_PORTFOLIO_ID = "equity_swing"


def place_equity_order(
    intent: "TradeIntent",  # bots.base_bot.TradeIntent
    client=None,
    approve: bool = True,
) -> Optional[str]:
    """Place (or queue) a stock order for a TradeIntent.

    Returns the intent_id on success, None on skip/failure.
    """
    cfg = get_config()

    if intent.quantity is None or intent.quantity < 1:
        log.warning("[equity] %s: invalid quantity %s", intent.symbol, intent.quantity)
        return None
    if intent.limit_price is None or intent.limit_price <= 0:
        log.warning("[equity] %s: invalid limit_price %s", intent.symbol, intent.limit_price)
        return None

    intent_id = _make_intent_id(intent.symbol, intent.direction)

    # Duplicate guard
    if _is_duplicate(intent_id):
        log.info("[equity] Duplicate intent %s — skipping.", intent_id)
        return None

    # Risk checks
    allowed, reason = _check_equity_risk(intent)
    if not allowed:
        log_event("INFO", "equity_risk_block", f"{intent.symbol}: {reason}", {"intent_id": intent_id})
        log.info("[equity] Risk check blocked %s: %s", intent.symbol, reason)
        return None

    max_loss = round(intent.max_risk_usd, 2)  # = stop_dist * qty
    order_payload = {
        "direction": intent.direction,
        "quantity": intent.quantity,
        "limit_price": intent.limit_price,
        "stop_price": intent.stop_price,
        "score": intent.score,
        "atr": intent.atr,
        "regime": intent.regime,
        "components": intent.components,
    }

    if approve:
        with get_db() as db:
            order = Order(
                intent_id=intent_id,
                symbol=intent.symbol,
                direction=intent.direction,
                instrument="stock",
                quantity=intent.quantity,
                order_type=cfg.bots.equity_swing.entry_order_type,
                limit_price=intent.limit_price,
                status="pending_approval",
                max_loss=max_loss,
                payload_json=json.dumps(order_payload),
                portfolio_id=_PORTFOLIO_ID,
            )
            db.add(order)
            db.flush()
            _create_trade_management(db, intent, intent_id, order.id)
        log_event(
            "INFO", "equity_signal",
            f"Equity order for {intent.symbol} pending approval (qty={intent.quantity})",
            order_payload,
        )
        log.info(
            "[equity] Approve mode: %s queued (qty=%d lim=%.2f max_loss=%.2f)",
            intent.symbol, intent.quantity, intent.limit_price, max_loss,
        )
        return intent_id

    # Live submission path
    if client is None:
        log.warning("[equity] No IBKR client — cannot submit %s", intent.symbol)
        return None

    try:
        from ib_insync import Stock, Order as IBOrder
        contract = Stock(intent.symbol, "SMART", "USD")
        ib_order = IBOrder()
        ib_order.action = "BUY" if intent.direction == "long" else "SELL"
        ib_order.orderType = cfg.bots.equity_swing.entry_order_type
        ib_order.totalQuantity = intent.quantity
        ib_order.lmtPrice = intent.limit_price
        ib_order.tif = "DAY"

        trade = client.place_order(contract, ib_order)

        with get_db() as db:
            order = Order(
                intent_id=intent_id,
                symbol=intent.symbol,
                direction=intent.direction,
                instrument="stock",
                quantity=intent.quantity,
                order_type=cfg.bots.equity_swing.entry_order_type,
                limit_price=intent.limit_price,
                status="submitted",
                ibkr_order_id=trade.order.orderId if trade else None,
                max_loss=max_loss,
                payload_json=json.dumps(order_payload),
                portfolio_id=_PORTFOLIO_ID,
            )
            db.add(order)
            db.flush()
            _create_trade_management(db, intent, intent_id, order.id)

        log_event(
            "INFO", "equity_order_submitted",
            f"Submitted equity {intent.direction} {intent.symbol}: "
            f"{intent.quantity}x @ ${intent.limit_price}",
            order_payload,
        )
        log.info(
            "[equity] Order submitted: %s %s %dx @ $%.2f (stop=$%.2f)",
            intent.symbol, intent.direction, intent.quantity,
            intent.limit_price, intent.stop_price or 0.0,
        )
        return intent_id

    except Exception as e:
        log.error("[equity] Failed to place order for %s: %s", intent.symbol, e)
        log_event("ERROR", "equity_order_failed", f"Failed {intent.symbol}: {e}", order_payload)
        return None


# ── Risk checks ─────────────────────────────────────────────────────────────


def _check_equity_risk(intent) -> tuple[bool, str]:
    cfg = get_config()
    equity_cfg = cfg.bots.equity_swing

    with get_db() as db:
        state = db.query(BotState).first()
        if state is None:
            return False, "no bot_state row"
        if state.kill_switch:
            return False, "kill switch active"
        if state.paused:
            return False, "bot paused"

        # Per-bot position cap (equity_swing only)
        pos_count = (
            db.query(Position)
            .filter(Position.portfolio_id == _PORTFOLIO_ID)
            .count()
        )
        if pos_count >= equity_cfg.max_positions:
            return False, f"equity_swing max_positions ({equity_cfg.max_positions}) reached"

        # Drawdown guard (shared account-level)
        snap = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
        if snap and snap.drawdown_pct >= cfg.risk.max_drawdown_pct:
            return False, f"drawdown {snap.drawdown_pct:.1f}% >= limit {cfg.risk.max_drawdown_pct}%"

        if snap and cfg.risk.require_positive_cash and snap.cash <= 0:
            return False, "negative cash — no-debt constraint"

    return True, "OK"


def close_equity_position(
    symbol: str,
    quantity: int,
    direction: str,
    urgency: str,
    limit_price: Optional[float],
    client,
    session,
    approve: bool,
    exit_rule: str,
    exit_reason: str,
    management_id: Optional[int] = None,
) -> Optional[Order]:
    """Place a closing order for an equity position.

    direction="long" → SELL to close; "short" → BUY to close.
    urgency: "immediate" → MKT, "end_of_day" → MOC, else → LMT.
    """
    action = "SELL" if direction == "long" else "BUY"
    date_str = utcnow().strftime("%Y%m%d")
    intent_id = f"{symbol}_exit_{direction}_{date_str}_{uuid.uuid4().hex[:8]}"

    if _is_duplicate_in_session(intent_id, session):
        log.warning("[equity] Duplicate exit intent %s — skipping.", intent_id)
        return None

    if urgency == "immediate":
        order_type = "MKT"
        lmt = None
    elif urgency == "end_of_day":
        order_type = "MOC"
        lmt = None
    else:
        order_type = "LMT"
        lmt = limit_price

    payload = {
        "exit_rule": exit_rule,
        "exit_reason": exit_reason,
        "action": action,
        "urgency": urgency,
        "is_exit": True,
    }
    order = Order(
        intent_id=intent_id,
        symbol=symbol,
        direction=f"close_{direction}",
        instrument="stock",
        portfolio_id=_PORTFOLIO_ID,
        quantity=quantity,
        order_type=order_type,
        limit_price=lmt,
        status="pending_approval" if approve else "submitted",
        max_loss=0.0,
        payload_json=json.dumps(payload),
    )
    session.add(order)

    if not approve and client is not None:
        try:
            from ib_insync import Stock, Order as IBOrder
            contract = Stock(symbol, "SMART", "USD")
            ib_order = IBOrder()
            ib_order.action = action
            ib_order.orderType = order_type
            ib_order.totalQuantity = quantity
            if order_type == "LMT" and lmt:
                ib_order.lmtPrice = lmt
            ib_order.tif = "DAY"
            trade = client.place_order(contract, ib_order)
            session.flush()
            order.ibkr_order_id = trade.order.orderId if trade else None
            order.status = "submitted"
            log_event("INFO", "equity_exit_submitted",
                      f"{symbol}: {exit_rule} — {exit_reason}",
                      {"intent_id": intent_id, "quantity": quantity, "order_type": order_type})
        except Exception as e:
            log.error("[equity] Failed to submit exit for %s: %s", symbol, e)
            order.status = "failed"
            log_event("ERROR", "equity_exit_failed", str(e), {"symbol": symbol})
    else:
        log_event("INFO", "equity_exit_pending_approval",
                  f"{symbol}: {exit_rule}",
                  {"intent_id": intent_id, "exit_reason": exit_reason})

    return order


def _create_trade_management(db, intent, intent_id: str, order_id: int) -> None:
    """Create a TradeManagement row when a new equity order is placed."""
    if intent.limit_price is None or intent.limit_price <= 0:
        return
    stop = intent.stop_price or (intent.limit_price * 0.95)
    risk_per_share = abs(intent.limit_price - stop)
    db.add(TradeManagement(
        symbol=intent.symbol,
        portfolio_id=_PORTFOLIO_ID,
        instrument_type="equity",
        entry_price=intent.limit_price,
        entry_date=utcnow(),
        entry_atr=intent.atr,
        entry_score=intent.score,
        entry_regime=getattr(intent, "regime", None),
        direction=intent.direction,
        quantity=intent.quantity,
        current_quantity=intent.quantity,
        initial_stop=stop,
        current_stop=stop,
        risk_per_share=max(risk_per_share, 0.01),
        highest_price_since_entry=intent.limit_price,
        lowest_price_since_entry=intent.limit_price,
        current_r_multiple=0.0,
        intent_id=intent_id,
        order_id=order_id,
    ))


def _make_intent_id(symbol: str, direction: str) -> str:
    date_str = utcnow().strftime("%Y%m%d")
    return f"{symbol}_{direction}_{date_str}_{uuid.uuid4().hex[:8]}"


def _is_duplicate(intent_id: str) -> bool:
    with get_db() as db:
        return db.query(Order).filter(Order.intent_id == intent_id).first() is not None


def _is_duplicate_in_session(intent_id: str, session) -> bool:
    return session.query(Order).filter(Order.intent_id == intent_id).first() is not None
