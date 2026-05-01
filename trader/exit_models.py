"""Dataclasses for the position exit management system."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ExitIntent:
    """Instruction to close (fully or partially) an open position."""
    symbol: str
    portfolio_id: str          # "equity_swing" | "options_swing"
    instrument_type: str       # "equity" | "debit_spread"
    direction: str             # "long" | "short"
    quantity: int
    is_partial: bool = False
    exit_rule: str = ""        # e.g. "trailing_stop", "profit_target"
    exit_reason: str = ""
    urgency: str = "normal"    # "immediate" (MKT) | "normal" (LMT) | "end_of_day" (MOC)
    limit_price: Optional[float] = None
    management_id: Optional[int] = None
    priority: int = 0
    metadata: Dict = field(default_factory=dict)


@dataclass
class ExitEvaluation:
    """Result of evaluating all exit rules for one position."""
    symbol: str
    portfolio_id: str
    management_id: int
    should_exit: bool = False
    exit_intents: List[ExitIntent] = field(default_factory=list)
    stop_updated: bool = False
    new_stop_price: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
    rules_evaluated: List[str] = field(default_factory=list)
    rules_triggered: List[str] = field(default_factory=list)
