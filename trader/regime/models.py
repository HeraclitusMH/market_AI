"""Regime state dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class RegimeLevel(str, Enum):
    RISK_ON = "risk_on"
    RISK_REDUCED = "risk_reduced"
    RISK_OFF = "risk_off"


@dataclass
class PillarScore:
    name: str
    score: float
    weight: float
    weighted_contribution: float
    components: Dict[str, float] = field(default_factory=dict)
    confidence: float = 1.0
    data_available: bool = True
    reason: str = ""


@dataclass(eq=False)
class RegimeState:
    """Complete regime evaluation result for one cycle."""
    level: RegimeLevel
    composite_score: float
    previous_level: Optional[RegimeLevel] = None
    transition: Optional[str] = None  # "upgraded"|"degraded"|"maintained"|None

    pillars: Dict[str, PillarScore] = field(default_factory=dict)

    raw_suggested_level: RegimeLevel = field(default_factory=lambda: RegimeLevel.RISK_REDUCED)
    consecutive_confirmations: int = 0
    cycles_in_current_state: int = 0
    hysteresis_active: bool = False

    sizing_factor: float = 1.0
    allows_new_equity_entries: bool = True
    allows_new_options_entries: bool = True
    stop_tightening_factor: float = 1.0
    score_threshold_adjustment: float = 0.0

    timestamp: datetime = field(default_factory=datetime.utcnow)
    data_quality: str = "full"
    warnings: List[str] = field(default_factory=list)

    # --- Backward compatibility ---
    @property
    def regime(self) -> str:
        return self.level.value

    @property
    def is_risk_on(self) -> bool:
        return self.level == RegimeLevel.RISK_ON

    @property
    def is_risk_off(self) -> bool:
        return self.level == RegimeLevel.RISK_OFF

    @property
    def is_risk_reduced(self) -> bool:
        return self.level == RegimeLevel.RISK_REDUCED

    def __eq__(self, other):
        if isinstance(other, str):
            return self.level.value == other
        if isinstance(other, RegimeState):
            return self.level == other.level
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __str__(self):
        return self.level.value

    def __repr__(self):
        return f"RegimeState({self.level.value}, score={self.composite_score:.1f})"

    def __hash__(self):
        return hash(self.level)
