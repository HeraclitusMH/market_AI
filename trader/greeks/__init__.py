"""Greeks sub-package — re-exports all public names for backwards compatibility.

Callers can continue using:
    from trader.greeks import GreeksService, GreeksSnapshot, ...
    from trader.greeks import GreeksGate, GateResult
    from trader.greeks import GreeksLogger
    from trader.greeks import StrikeSelector, SpreadSelection, ...
"""
from trader.greeks.service import GreeksSnapshot, OptionChainGreeks, GreeksService, _sanitize_price
from trader.greeks.gate import GateResult, GreeksGate
from trader.greeks.logger import GreeksLogger
from trader.greeks.strike_selector import (
    StrikeSelectionCriteria,
    SpreadSelection,
    StrikeSelector,
    calculate_limit_price,
)

__all__ = [
    "GreeksSnapshot",
    "OptionChainGreeks",
    "GreeksService",
    "_sanitize_price",
    "GateResult",
    "GreeksGate",
    "GreeksLogger",
    "StrikeSelectionCriteria",
    "SpreadSelection",
    "StrikeSelector",
    "calculate_limit_price",
]
