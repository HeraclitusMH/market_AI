"""Regime state machine with hysteresis."""
from __future__ import annotations

import logging
from typing import Optional

from trader.regime.models import RegimeLevel

log = logging.getLogger(__name__)

_LEVEL_ORDER = {RegimeLevel.RISK_OFF: 0, RegimeLevel.RISK_REDUCED: 1, RegimeLevel.RISK_ON: 2}
_ORDER_LEVEL = {v: k for k, v in _LEVEL_ORDER.items()}


class RegimeStateMachine:
    """
    Manages 3-state transitions with asymmetric hysteresis.
    Fast to protect (fewer confirmations to degrade), slow to re-risk (more to recover).
    No state skipping: risk_on -> risk_reduced -> risk_off (never direct jump).
    """

    def __init__(self, thresholds, hysteresis):
        self.thresholds = thresholds
        self.hysteresis = hysteresis
        self._current_level: RegimeLevel = RegimeLevel.RISK_ON
        self._cycles_in_state: int = 0
        self._consecutive_toward_target: int = 0
        self._pending_target: Optional[RegimeLevel] = None

    def load_state(self, last_snapshot) -> None:
        if last_snapshot is not None:
            try:
                self._current_level = RegimeLevel(last_snapshot.level)
                self._cycles_in_state = last_snapshot.cycles_in_current_state or 0
                self._consecutive_toward_target = last_snapshot.consecutive_confirmations or 0
                if last_snapshot.raw_suggested_level:
                    self._pending_target = RegimeLevel(last_snapshot.raw_suggested_level)
            except Exception:
                pass  # corrupt snapshot — start fresh
        else:
            self._current_level = RegimeLevel.RISK_ON
            self._cycles_in_state = 0

    def evaluate_transition(self, composite_score: float) -> dict:
        raw_level = self._score_to_level(composite_score)
        direction = self._compare_levels(raw_level, self._current_level)
        self._cycles_in_state += 1

        if direction == "maintain":
            self._consecutive_toward_target = 0
            self._pending_target = None
            return {
                "level": self._current_level,
                "raw_suggested_level": raw_level,
                "transition": "maintained",
                "consecutive_confirmations": 0,
                "cycles_in_current_state": self._cycles_in_state,
                "hysteresis_active": False,
            }

        required = (
            self.hysteresis.degrade_confirmations
            if direction == "degrade"
            else self.hysteresis.recover_confirmations
        )
        adjacent_target = self._get_adjacent_level(self._current_level, direction)

        if self._pending_target == adjacent_target:
            self._consecutive_toward_target += 1
        else:
            self._pending_target = adjacent_target
            self._consecutive_toward_target = 1

        if self._cycles_in_state < self.hysteresis.min_hold_cycles:
            return {
                "level": self._current_level,
                "raw_suggested_level": raw_level,
                "transition": None,
                "consecutive_confirmations": self._consecutive_toward_target,
                "cycles_in_current_state": self._cycles_in_state,
                "hysteresis_active": True,
            }

        if self._consecutive_toward_target >= required:
            old_level = self._current_level
            self._current_level = adjacent_target
            self._cycles_in_state = 0
            self._consecutive_toward_target = 0
            self._pending_target = None
            transition_type = "degraded" if direction == "degrade" else "upgraded"
            log.info(
                "[REGIME] Transition: %s -> %s (score=%.1f, %s)",
                old_level.value, self._current_level.value, composite_score, transition_type,
            )
            return {
                "level": self._current_level,
                "raw_suggested_level": raw_level,
                "transition": transition_type,
                "consecutive_confirmations": 0,
                "cycles_in_current_state": 0,
                "hysteresis_active": False,
            }

        return {
            "level": self._current_level,
            "raw_suggested_level": raw_level,
            "transition": None,
            "consecutive_confirmations": self._consecutive_toward_target,
            "cycles_in_current_state": self._cycles_in_state,
            "hysteresis_active": True,
        }

    def _score_to_level(self, score: float) -> RegimeLevel:
        if score >= self.thresholds.risk_on_min:
            return RegimeLevel.RISK_ON
        if score <= self.thresholds.risk_off_max:
            return RegimeLevel.RISK_OFF
        return RegimeLevel.RISK_REDUCED

    def _compare_levels(self, suggested: RegimeLevel, current: RegimeLevel) -> str:
        s, c = _LEVEL_ORDER[suggested], _LEVEL_ORDER[current]
        if s > c:
            return "upgrade"
        if s < c:
            return "degrade"
        return "maintain"

    def _get_adjacent_level(self, current: RegimeLevel, direction: str) -> RegimeLevel:
        idx = _LEVEL_ORDER[current]
        if direction == "degrade":
            return _ORDER_LEVEL[max(0, idx - 1)]
        return _ORDER_LEVEL[min(2, idx + 1)]

    @property
    def current_level(self) -> RegimeLevel:
        return self._current_level
