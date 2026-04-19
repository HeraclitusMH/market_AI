"""Options execution shim for the bot plugin interface.

Routes TradeIntent(instrument_type='options') to the existing
trader.execution pipeline, preserving all existing behaviour.
"""
from __future__ import annotations

from typing import Optional

from common.logging import get_logger
from bots.base_bot import TradeIntent
from trader.strategy import SignalIntent

log = get_logger(__name__)


def execute_options_intent(intent: TradeIntent, client=None) -> Optional[str]:
    """Convert a TradeIntent to a SignalIntent and execute via existing pipeline."""
    from trader.execution import execute_signal  # lazy: avoids circular import at module load
    signal = SignalIntent(
        symbol=intent.symbol,
        direction=intent.direction,
        instrument=intent.components.get("strategy", "debit_spread"),
        score=intent.score,
        max_risk_usd=intent.max_risk_usd,
        explanation=intent.explanation,
        components=intent.components,
        regime=intent.regime,
    )
    return execute_signal(signal, client)
