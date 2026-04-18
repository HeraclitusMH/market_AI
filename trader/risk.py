"""Risk engine: drawdown tracking, position limits, cash checks."""
from __future__ import annotations

import json
from typing import Optional, Tuple

from common.config import get_config
from common.db import get_db
from common.logging import get_logger
from common.models import BotState, EquitySnapshot, Position, Order, EventLog
from common.time import utcnow
from trader.strategy import SignalIntent

log = get_logger(__name__)


def get_bot_state() -> BotState:
    with get_db() as db:
        state = db.query(BotState).first()
        if state is None:
            state = BotState(id=1)
            db.add(state)
            db.flush()
    return state


def record_equity_snapshot(net_liq: float, cash: float, unrealized: float, realized: float) -> float:
    """Record equity and compute drawdown. Returns drawdown_pct."""
    with get_db() as db:
        # find peak net_liq
        peak_row = (
            db.query(EquitySnapshot)
            .order_by(EquitySnapshot.net_liquidation.desc())
            .first()
        )
        peak = peak_row.net_liquidation if peak_row else net_liq
        peak = max(peak, net_liq)

        drawdown_pct = ((peak - net_liq) / peak * 100) if peak > 0 else 0.0
        drawdown_pct = round(drawdown_pct, 2)

        db.add(EquitySnapshot(
            timestamp=utcnow(),
            net_liquidation=net_liq,
            cash=cash,
            unrealized_pnl=unrealized,
            realized_pnl=realized,
            drawdown_pct=drawdown_pct,
        ))

    return drawdown_pct


def check_can_trade(intent: SignalIntent) -> Tuple[bool, str]:
    """Run all risk checks. Returns (allowed, reason)."""
    cfg = get_config()

    with get_db() as db:
        state = db.query(BotState).first()
        if state is None:
            return False, "No bot_state row"

        # Kill switch
        if state.kill_switch:
            return False, "Kill switch active"

        # Paused
        if state.paused:
            return False, "Bot is paused"

        # Approve mode — don't block, but flag
        # (caller should handle approve_mode separately)

        # Max drawdown
        latest_eq = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
        if latest_eq and latest_eq.drawdown_pct >= cfg.risk.max_drawdown_pct:
            return False, f"Drawdown {latest_eq.drawdown_pct:.1f}% >= limit {cfg.risk.max_drawdown_pct}%"

        # Max positions
        open_positions = db.query(Position).count()
        if open_positions >= cfg.risk.max_positions:
            return False, f"Max positions ({cfg.risk.max_positions}) reached"

        # Cash check
        if cfg.risk.require_positive_cash and latest_eq:
            if latest_eq.cash <= 0:
                return False, "Negative cash — no-debt constraint"

        # Max risk per trade
        if latest_eq and intent.max_risk_usd > 0:
            max_allowed = latest_eq.net_liquidation * (cfg.risk.max_risk_per_trade_pct / 100)
            if intent.max_risk_usd > max_allowed:
                return False, (
                    f"Trade risk ${intent.max_risk_usd:.2f} > "
                    f"max ${max_allowed:.2f} ({cfg.risk.max_risk_per_trade_pct}% of equity)"
                )

        # Cash reservation check
        if latest_eq and intent.max_risk_usd > 0:
            # Sum reserved cash from pending/submitted orders
            reserved = (
                db.query(Order)
                .filter(Order.status.in_(["pending", "submitted"]))
                .with_entities(Order.max_loss)
                .all()
            )
            total_reserved = sum(r.max_loss for r in reserved)
            available_cash = latest_eq.cash - total_reserved
            if intent.max_risk_usd > available_cash:
                return False, (
                    f"Insufficient cash: need ${intent.max_risk_usd:.2f}, "
                    f"available ${available_cash:.2f} (cash ${latest_eq.cash:.2f} - reserved ${total_reserved:.2f})"
                )

    return True, "OK"


def compute_max_risk_for_trade(intent: SignalIntent) -> float:
    """Compute the max USD risk for a trade based on equity."""
    cfg = get_config()
    with get_db() as db:
        latest_eq = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
        if latest_eq is None:
            return 0.0
    return round(latest_eq.net_liquidation * (cfg.risk.max_risk_per_trade_pct / 100), 2)


def is_approve_mode() -> bool:
    with get_db() as db:
        state = db.query(BotState).first()
        return state.approve_mode if state else True


def log_event(level: str, etype: str, message: str, payload: dict | None = None) -> None:
    with get_db() as db:
        db.add(EventLog(
            timestamp=utcnow(),
            level=level,
            type=etype,
            message=message,
            payload_json=json.dumps(payload or {}),
        ))


def check_duplicate_intent(intent_id: str) -> bool:
    """Return True if this intent_id already has an order."""
    with get_db() as db:
        return db.query(Order).filter(Order.intent_id == intent_id).first() is not None
