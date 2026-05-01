"""RegimeEngine — orchestrates pillar computation, state machine, persistence."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from trader.regime.indicators import (
    compute_trend_score,
    compute_breadth_score,
    compute_volatility_score,
    compute_credit_stress_score,
)
from trader.regime.models import PillarScore, RegimeLevel, RegimeState
from trader.regime.state_machine import RegimeStateMachine

log = logging.getLogger(__name__)


class RegimeEngine:
    """
    Computes market regime by evaluating 4 indicator pillars, combining them
    into a composite score, and running the result through a hysteresis state machine.

    Note: VIX fetching via IBKR market data (using Stock("VIX", ...)) will fail
    silently — the volatility pillar will use only realized vol (reduced confidence).
    VIX data would require an index contract type. This is acceptable for paper trading.
    """

    def __init__(self, config, session=None):
        self.cfg = config
        self.session = session
        self.state_machine = RegimeStateMachine(
            thresholds=config.thresholds,
            hysteresis=config.hysteresis,
        )
        self._initialized = False

    def initialize(self, session) -> None:
        self.session = session
        from common.models import RegimeSnapshot
        last = (
            session.query(RegimeSnapshot)
            .order_by(RegimeSnapshot.timestamp.desc())
            .first()
        )
        self.state_machine.load_state(last)
        self._initialized = True
        log.info("[REGIME] Initialized. Current state: %s", self.state_machine.current_level.value)

    def evaluate(
        self,
        spy_bars: Optional[pd.DataFrame] = None,
        vix_bars: Optional[pd.DataFrame] = None,
        hyg_bars: Optional[pd.DataFrame] = None,
        lqd_bars: Optional[pd.DataFrame] = None,
        universe_bars: Optional[Dict[str, pd.DataFrame]] = None,
        client=None,
    ) -> RegimeState:
        if not self._initialized:
            raise RuntimeError("RegimeEngine.initialize() must be called before evaluate()")

        if client:
            spy_bars = spy_bars if spy_bars is not None else self._fetch_bars(self.cfg.trend_symbol, client)
            vix_bars = vix_bars if vix_bars is not None else self._fetch_bars(self.cfg.volatility_symbol, client)
            hyg_bars = hyg_bars if hyg_bars is not None else self._fetch_bars(
                self.cfg.credit_stress_symbols.get("high_yield", "HYG"), client
            )
            lqd_bars = lqd_bars if lqd_bars is not None else self._fetch_bars(
                self.cfg.credit_stress_symbols.get("investment_grade", "LQD"), client
            )

        warnings: List[str] = []

        trend_pillar = compute_trend_score(spy_bars, self.cfg.trend)
        breadth_pillar = compute_breadth_score(universe_bars or {}, self.cfg.breadth)
        volatility_pillar = compute_volatility_score(spy_bars, vix_bars, self.cfg.volatility)
        credit_pillar = compute_credit_stress_score(hyg_bars, lqd_bars, self.cfg.credit_stress)

        pillars: Dict[str, PillarScore] = {
            "trend": trend_pillar,
            "breadth": breadth_pillar,
            "volatility": volatility_pillar,
            "credit_stress": credit_pillar,
        }
        weight_map = {
            "trend": self.cfg.weights.trend,
            "breadth": self.cfg.weights.breadth,
            "volatility": self.cfg.weights.volatility,
            "credit_stress": self.cfg.weights.credit_stress,
        }

        total_effective_weight = 0.0
        weighted_sum = 0.0
        for name, pillar in pillars.items():
            w = weight_map[name]
            pillar.weight = w
            if pillar.data_available and pillar.confidence > 0:
                ew = w * pillar.confidence
                pillar.weighted_contribution = pillar.score * ew
                weighted_sum += pillar.weighted_contribution
                total_effective_weight += ew
            else:
                pillar.weighted_contribution = 0.0
                warnings.append(f"Pillar '{name}' unavailable (confidence={pillar.confidence:.2f})")

        if total_effective_weight > 0:
            composite_score = weighted_sum / total_effective_weight
        else:
            composite_score = 50.0
            warnings.append("All pillars unavailable; using neutral fallback score")

        composite_score = max(0.0, min(100.0, composite_score))

        available_count = sum(1 for p in pillars.values() if p.data_available)
        if available_count == 4:
            data_quality = "full"
        elif available_count >= 2:
            data_quality = "partial"
        else:
            data_quality = "fallback"

        previous_level = self.state_machine.current_level
        sm_result = self.state_machine.evaluate_transition(composite_score)
        resolved_level = sm_result["level"]

        if data_quality == "fallback":
            fallback_level = RegimeLevel(self.cfg.fallback.on_insufficient_data)
            if _level_order(fallback_level) < _level_order(resolved_level):
                resolved_level = fallback_level
                warnings.append(f"Data quality=fallback; overriding to {fallback_level.value}")

        effects = self._get_effects(resolved_level)

        state = RegimeState(
            level=resolved_level,
            composite_score=composite_score,
            previous_level=previous_level if previous_level != resolved_level else None,
            transition=sm_result["transition"],
            pillars=pillars,
            raw_suggested_level=sm_result["raw_suggested_level"],
            consecutive_confirmations=sm_result["consecutive_confirmations"],
            cycles_in_current_state=sm_result["cycles_in_current_state"],
            hysteresis_active=sm_result["hysteresis_active"],
            sizing_factor=effects.sizing_factor,
            allows_new_equity_entries=effects.allows_new_equity_entries,
            allows_new_options_entries=effects.allows_new_options_entries,
            stop_tightening_factor=effects.stop_tightening_factor,
            score_threshold_adjustment=effects.score_threshold_adjustment,
            timestamp=datetime.utcnow(),
            data_quality=data_quality,
            warnings=warnings,
        )

        self._persist_snapshot(state)
        self._log_regime(state)
        return state

    def _get_effects(self, level: RegimeLevel):
        if level == RegimeLevel.RISK_ON:
            return self.cfg.effects.risk_on
        if level == RegimeLevel.RISK_REDUCED:
            return self.cfg.effects.risk_reduced
        return self.cfg.effects.risk_off

    def _fetch_bars(self, symbol: str, client) -> Optional[pd.DataFrame]:
        try:
            from trader.market_data import fetch_bars
            return fetch_bars(symbol, "1D", client=client)
        except Exception as e:
            log.warning("[REGIME] Failed to fetch bars for %s: %s", symbol, e)
            return None

    def _persist_snapshot(self, state: RegimeState) -> None:
        if self.session is None:
            return
        try:
            import json
            from common.models import RegimeSnapshot
            snap = RegimeSnapshot(
                timestamp=state.timestamp,
                level=state.level.value,
                composite_score=state.composite_score,
                previous_level=state.previous_level.value if state.previous_level else None,
                transition=state.transition,
                trend_score=state.pillars.get("trend", PillarScore("trend", 0, 0, 0)).score,
                breadth_score=state.pillars.get("breadth", PillarScore("breadth", 0, 0, 0)).score,
                volatility_score=state.pillars.get("volatility", PillarScore("volatility", 0, 0, 0)).score,
                credit_stress_score=state.pillars.get("credit_stress", PillarScore("credit_stress", 0, 0, 0)).score,
                raw_suggested_level=state.raw_suggested_level.value,
                consecutive_confirmations=state.consecutive_confirmations,
                cycles_in_current_state=state.cycles_in_current_state,
                hysteresis_active=state.hysteresis_active,
                components_json=json.dumps({
                    name: {
                        "score": p.score,
                        "weight": p.weight,
                        "weighted_contribution": p.weighted_contribution,
                        "confidence": p.confidence,
                        "components": p.components,
                        "reason": p.reason,
                    }
                    for name, p in state.pillars.items()
                }),
                data_quality=state.data_quality,
                warnings_json=json.dumps(state.warnings) if state.warnings else None,
            )
            self.session.add(snap)
            self.session.commit()
        except Exception as e:
            log.warning("[REGIME] Failed to persist snapshot: %s", e)

    def _log_regime(self, state: RegimeState) -> None:
        t = state.pillars.get("trend", PillarScore("", 0, 0, 0)).score
        b = state.pillars.get("breadth", PillarScore("", 0, 0, 0)).score
        v = state.pillars.get("volatility", PillarScore("", 0, 0, 0)).score
        c = state.pillars.get("credit_stress", PillarScore("", 0, 0, 0)).score
        transition_str = f" [{state.transition}]" if state.transition else ""
        hysteresis_str = " [HYSTERESIS]" if state.hysteresis_active else ""
        log.info(
            "[REGIME] %s (score=%.1f) T=%.0f B=%.0f V=%.0f C=%.0f%s%s",
            state.level.value, state.composite_score, t, b, v, c,
            transition_str, hysteresis_str,
        )


def _level_order(level: RegimeLevel) -> int:
    return {RegimeLevel.RISK_OFF: 0, RegimeLevel.RISK_REDUCED: 1, RegimeLevel.RISK_ON: 2}[level]
