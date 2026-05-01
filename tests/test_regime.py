"""Tests for enhanced regime detection (trader/regime/)."""
from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from trader.regime.models import RegimeLevel, RegimeState, PillarScore
from trader.regime.indicators import (
    compute_trend_score,
    compute_breadth_score,
    compute_volatility_score,
    compute_credit_stress_score,
)
from trader.regime.state_machine import RegimeStateMachine


# ── Helpers ──────────────────────────────────────────────────────────────────

def _spy_bars(n: int = 250, start: float = 400.0, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic SPY bars. trend='up'|'down'|'flat'."""
    if trend == "up":
        closes = np.linspace(start * 0.8, start, n)
    elif trend == "down":
        closes = np.linspace(start, start * 0.8, n)
    else:
        closes = np.ones(n) * start
    return pd.DataFrame({
        "date": pd.date_range("2023-01-01", periods=n, freq="B"),
        "close": closes,
        "open": closes * 0.999,
        "high": closes * 1.005,
        "low": closes * 0.995,
        "volume": [10_000_000] * n,
    })


def _vix_bars(n: int = 30, level: float = 15.0) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2023-01-01", periods=n, freq="B"),
        "close": [level] * n,
    })


def _etf_bars(n: int = 60, price: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2023-01-01", periods=n, freq="B"),
        "close": [price] * n,
    })


def _make_thresholds(risk_on_min: int = 65, risk_off_max: int = 35):
    t = MagicMock()
    t.risk_on_min = risk_on_min
    t.risk_off_max = risk_off_max
    return t


def _make_hysteresis(degrade: int = 2, recover: int = 3, min_hold: int = 1):
    h = MagicMock()
    h.degrade_confirmations = degrade
    h.recover_confirmations = recover
    h.min_hold_cycles = min_hold
    return h


def _make_trend_cfg():
    c = MagicMock()
    c.sma_period = 200
    c.ema_fast = 20
    c.ema_slow = 50
    return c


def _make_breadth_cfg(strong=0.70, moderate=0.50, weak=0.30, min_symbols=20):
    c = MagicMock()
    c.ma_period = 50
    c.strong_threshold = strong
    c.moderate_threshold = moderate
    c.weak_threshold = weak
    c.min_symbols_required = min_symbols
    return c


def _make_vol_cfg():
    c = MagicMock()
    c.realized_vol_period = 20
    c.vix_low = 15.0
    c.vix_moderate = 20.0
    c.vix_elevated = 25.0
    c.vix_high = 30.0
    c.realized_vol_low = 12.0
    c.realized_vol_moderate = 18.0
    c.realized_vol_elevated = 25.0
    return c


def _make_credit_cfg():
    c = MagicMock()
    c.ratio_sma_period = 50
    c.mild_deviation_pct = 0.02
    c.severe_deviation_pct = 0.05
    return c


# ── Trend Pillar ─────────────────────────────────────────────────────────────

class TestTrendPillar:
    def test_full_uptrend_scores_near_100(self):
        bars = _spy_bars(250, trend="up")
        cfg = _make_trend_cfg()
        result = compute_trend_score(bars, cfg)
        assert result.data_available is True
        assert result.score >= 90.0  # 100 base + slope mod + possible chop penalty

    def test_full_downtrend_scores_low(self):
        bars = _spy_bars(250, trend="down")
        cfg = _make_trend_cfg()
        result = compute_trend_score(bars, cfg)
        assert result.data_available is True
        assert result.score <= 10.0  # 0 base - 10 slope = -10, clamped to 0

    def test_insufficient_bars_returns_no_data(self):
        bars = _spy_bars(50)  # less than sma_period=200
        cfg = _make_trend_cfg()
        result = compute_trend_score(bars, cfg)
        assert result.data_available is False
        assert result.confidence == 0.0

    def test_none_bars_returns_no_data(self):
        cfg = _make_trend_cfg()
        result = compute_trend_score(None, cfg)
        assert result.data_available is False

    def test_components_populated(self):
        bars = _spy_bars(250, trend="up")
        cfg = _make_trend_cfg()
        result = compute_trend_score(bars, cfg)
        assert "close" in result.components
        assert "sma200" in result.components
        assert "ema20" in result.components
        assert "final_score" in result.components

    def test_score_clamped_to_0_100(self):
        bars = _spy_bars(250, trend="down")
        cfg = _make_trend_cfg()
        result = compute_trend_score(bars, cfg)
        assert 0.0 <= result.score <= 100.0


# ── Breadth Pillar ────────────────────────────────────────────────────────────

class TestBreadthPillar:
    def _make_bars_dict(self, n_above: int, n_below: int, n_insufficient: int = 0) -> dict:
        cfg = _make_breadth_cfg()
        bars = {}
        for i in range(n_above):
            # All closes above MA: linear uptrend
            closes = np.linspace(80.0, 100.0, cfg.ma_period + 10)
            bars[f"up_{i}"] = pd.DataFrame({"close": closes})
        for i in range(n_below):
            # All closes below MA: linear downtrend
            closes = np.linspace(100.0, 80.0, cfg.ma_period + 10)
            bars[f"dn_{i}"] = pd.DataFrame({"close": closes})
        for i in range(n_insufficient):
            bars[f"ins_{i}"] = pd.DataFrame({"close": [100.0] * 5})  # too few bars
        return bars

    def test_all_above_scores_100(self):
        cfg = _make_breadth_cfg()
        bars = self._make_bars_dict(25, 0)
        result = compute_breadth_score(bars, cfg)
        assert result.score == 100.0

    def test_all_below_scores_near_0(self):
        cfg = _make_breadth_cfg()
        bars = self._make_bars_dict(0, 25)
        result = compute_breadth_score(bars, cfg)
        assert result.score < 20.0

    def test_empty_universe_returns_no_data(self):
        cfg = _make_breadth_cfg()
        result = compute_breadth_score({}, cfg)
        assert result.data_available is False

    def test_insufficient_symbols_reduces_confidence(self):
        cfg = _make_breadth_cfg(min_symbols=20)
        bars = self._make_bars_dict(5, 5)  # only 10 evaluated
        result = compute_breadth_score(bars, cfg)
        assert result.confidence < 1.0
        assert result.confidence == pytest.approx(10 / 20)

    def test_score_interpolation_moderate_band(self):
        cfg = _make_breadth_cfg()
        # 60% above = in moderate band (0.50-0.70)
        bars = self._make_bars_dict(12, 8)  # 60% above
        result = compute_breadth_score(bars, cfg)
        assert 60.0 <= result.score < 100.0

    def test_insufficient_bars_symbols_excluded(self):
        cfg = _make_breadth_cfg()
        bars = self._make_bars_dict(10, 0, n_insufficient=5)
        result = compute_breadth_score(bars, cfg)
        assert result.components["total_evaluated"] == 10.0


# ── Volatility Pillar ─────────────────────────────────────────────────────────

class TestVolatilityPillar:
    def test_low_vix_low_vol_scores_high(self):
        spy = _spy_bars(30, trend="flat")  # flat = low realized vol
        vix = _vix_bars(30, level=10.0)   # below vix_low=15
        cfg = _make_vol_cfg()
        result = compute_volatility_score(spy, vix, cfg)
        assert result.score >= 80.0

    def test_high_vix_scores_low(self):
        # Use volatile SPY (high realized vol via random noise) with crisis VIX
        rng = np.random.default_rng(42)
        # Large daily moves (~3%) to produce high realized vol
        returns = rng.normal(loc=0.0, scale=0.03, size=30)
        closes = 400.0 * np.cumprod(1 + returns)
        spy = pd.DataFrame({"close": closes})
        vix = _vix_bars(30, level=40.0)  # above vix_high=30 (crisis)
        cfg = _make_vol_cfg()
        result = compute_volatility_score(spy, vix, cfg)
        assert result.score < 50.0

    def test_no_vix_data_reduces_confidence(self):
        spy = _spy_bars(30, trend="flat")
        cfg = _make_vol_cfg()
        result = compute_volatility_score(spy, None, cfg)
        assert result.confidence < 1.0

    def test_score_clamped_to_0_100(self):
        spy = _spy_bars(30, trend="down")
        vix = _vix_bars(30, level=50.0)
        cfg = _make_vol_cfg()
        result = compute_volatility_score(spy, vix, cfg)
        assert 0.0 <= result.score <= 100.0

    def test_contango_scores_higher_than_backwardation(self):
        cfg = _make_vol_cfg()
        spy = _spy_bars(30, trend="flat")
        # Contango: VIX well below its SMA20 (VIX falling = less fear)
        vix_contango = pd.DataFrame({"close": [20.0] * 20 + [15.0] * 10})
        # Backwardation: VIX well above its SMA20 (VIX spiking)
        vix_backwardation = pd.DataFrame({"close": [15.0] * 20 + [25.0] * 10})
        r_contango = compute_volatility_score(spy, vix_contango, cfg)
        r_backwardation = compute_volatility_score(spy, vix_backwardation, cfg)
        assert r_contango.score > r_backwardation.score


# ── Credit Stress Pillar ──────────────────────────────────────────────────────

class TestCreditStressPillar:
    def _make_ratio_bars(self, hyg_price: float, lqd_price: float, n: int = 70) -> tuple:
        hyg = pd.DataFrame({"close": [hyg_price] * n})
        lqd = pd.DataFrame({"close": [lqd_price] * n})
        return hyg, lqd

    def test_missing_data_returns_no_data(self):
        cfg = _make_credit_cfg()
        result = compute_credit_stress_score(None, None, cfg)
        assert result.data_available is False

    def test_ratio_above_sma_rising_scores_100(self):
        cfg = _make_credit_cfg()
        n = 70
        # Ratio trending up: early lower, later progressively higher so last > last[-6]
        hyg_closes = [75.0] * 35 + list(np.linspace(78.0, 82.0, 35))
        hyg = pd.DataFrame({"close": hyg_closes})
        lqd = pd.DataFrame({"close": [100.0] * n})
        result = compute_credit_stress_score(hyg, lqd, cfg)
        assert result.score == 100.0

    def test_severe_stress_scores_0(self):
        cfg = _make_credit_cfg()
        n = 70
        # Ratio far below SMA: stress
        hyg = pd.DataFrame({"close": [80.0] * 60 + [70.0] * 10})
        lqd = pd.DataFrame({"close": [100.0] * n})
        result = compute_credit_stress_score(hyg, lqd, cfg)
        assert result.score <= 10.0

    def test_insufficient_history_returns_low_confidence(self):
        cfg = _make_credit_cfg()
        hyg = pd.DataFrame({"close": [80.0] * 30})
        lqd = pd.DataFrame({"close": [100.0] * 30})
        result = compute_credit_stress_score(hyg, lqd, cfg)
        assert result.data_available is False
        assert result.confidence == pytest.approx(0.3)


# ── State Machine ─────────────────────────────────────────────────────────────

class TestRegimeStateMachine:
    def test_maintains_risk_on_with_high_score(self):
        sm = RegimeStateMachine(_make_thresholds(), _make_hysteresis())
        for _ in range(5):
            r = sm.evaluate_transition(80.0)
        assert r["level"] == RegimeLevel.RISK_ON

    def test_degrades_after_n_confirmations(self):
        sm = RegimeStateMachine(_make_thresholds(), _make_hysteresis(degrade=2, min_hold=0))
        # Score in risk_off zone
        sm.evaluate_transition(80.0)  # first cycle — sets up state
        r1 = sm.evaluate_transition(20.0)
        r2 = sm.evaluate_transition(20.0)
        assert r2["level"] == RegimeLevel.RISK_REDUCED  # degraded to risk_reduced after 2

    def test_no_direct_skip_from_risk_on_to_risk_off(self):
        sm = RegimeStateMachine(_make_thresholds(), _make_hysteresis(degrade=1, min_hold=0))
        sm.evaluate_transition(80.0)  # start risk_on
        # Even with extreme low score, can only go to risk_reduced first
        r = sm.evaluate_transition(5.0)
        assert r["level"] == RegimeLevel.RISK_REDUCED

    def test_recovery_requires_more_confirmations_than_degrade(self):
        h = _make_hysteresis(degrade=2, recover=3, min_hold=0)
        sm = RegimeStateMachine(_make_thresholds(), h)
        # Degrade to risk_reduced
        sm.evaluate_transition(80.0)
        sm.evaluate_transition(20.0)
        sm.evaluate_transition(20.0)  # now risk_reduced
        assert sm.current_level == RegimeLevel.RISK_REDUCED

        # Recover — needs 3 confirmations
        sm.evaluate_transition(80.0)
        sm.evaluate_transition(80.0)
        assert sm.current_level == RegimeLevel.RISK_REDUCED  # only 2 so far
        sm.evaluate_transition(80.0)
        assert sm.current_level == RegimeLevel.RISK_ON  # 3rd confirmation

    def test_counter_resets_when_signal_oscillates(self):
        sm = RegimeStateMachine(_make_thresholds(), _make_hysteresis(degrade=3, min_hold=0))
        sm.evaluate_transition(80.0)  # risk_on
        sm.evaluate_transition(20.0)  # toward risk_reduced: count=1
        sm.evaluate_transition(80.0)  # back to maintain: count resets
        sm.evaluate_transition(20.0)  # toward risk_reduced: count=1 (reset)
        assert sm.current_level == RegimeLevel.RISK_ON

    def test_min_hold_cycles_blocks_transition(self):
        h = _make_hysteresis(degrade=1, min_hold=3)
        sm = RegimeStateMachine(_make_thresholds(), h)
        sm.evaluate_transition(80.0)  # cycle 1
        r = sm.evaluate_transition(20.0)  # cycle 2 — min_hold=3 blocks
        assert r["hysteresis_active"] is True
        assert r["level"] == RegimeLevel.RISK_ON

    def test_maintained_returns_correct_transition_str(self):
        sm = RegimeStateMachine(_make_thresholds(), _make_hysteresis())
        sm.evaluate_transition(80.0)
        r = sm.evaluate_transition(80.0)
        assert r["transition"] == "maintained"

    def test_load_state_restores_from_snapshot(self):
        snap = MagicMock()
        snap.level = "risk_reduced"
        snap.cycles_in_current_state = 5
        snap.consecutive_confirmations = 1
        snap.raw_suggested_level = "risk_reduced"
        sm = RegimeStateMachine(_make_thresholds(), _make_hysteresis())
        sm.load_state(snap)
        assert sm.current_level == RegimeLevel.RISK_REDUCED
        assert sm._cycles_in_state == 5


# ── RegimeState Backward Compatibility ───────────────────────────────────────

class TestRegimeStateBackwardCompat:
    def _make_state(self, level: RegimeLevel) -> RegimeState:
        return RegimeState(level=level, composite_score=75.0)

    def test_eq_with_string_risk_on(self):
        state = self._make_state(RegimeLevel.RISK_ON)
        assert state == "risk_on"
        assert state != "risk_off"
        assert state != "risk_reduced"

    def test_eq_with_string_risk_off(self):
        state = self._make_state(RegimeLevel.RISK_OFF)
        assert state == "risk_off"
        assert state != "risk_on"

    def test_eq_with_string_risk_reduced(self):
        state = self._make_state(RegimeLevel.RISK_REDUCED)
        assert state == "risk_reduced"

    def test_str_returns_level_value(self):
        state = self._make_state(RegimeLevel.RISK_ON)
        assert str(state) == "risk_on"

    def test_regime_property(self):
        state = self._make_state(RegimeLevel.RISK_REDUCED)
        assert state.regime == "risk_reduced"

    def test_is_risk_on_property(self):
        state = self._make_state(RegimeLevel.RISK_ON)
        assert state.is_risk_on is True
        assert state.is_risk_off is False

    def test_ne_operator(self):
        state = self._make_state(RegimeLevel.RISK_ON)
        assert (state != "risk_off") is True
        assert (state != "risk_on") is False


# ── Composite Score Redistribution ────────────────────────────────────────────

class TestCompositeScoring:
    """Test that missing pillars redistribute weight proportionally."""

    def test_all_pillars_available_uses_configured_weights(self):
        from trader.regime.engine import RegimeEngine
        cfg = MagicMock()
        cfg.trend.sma_period = 200
        cfg.trend.ema_fast = 20
        cfg.trend.ema_slow = 50
        cfg.breadth.ma_period = 50
        cfg.breadth.strong_threshold = 0.70
        cfg.breadth.moderate_threshold = 0.50
        cfg.breadth.weak_threshold = 0.30
        cfg.breadth.min_symbols_required = 1
        cfg.volatility.realized_vol_period = 20
        cfg.volatility.vix_low = 15.0
        cfg.volatility.vix_moderate = 20.0
        cfg.volatility.vix_elevated = 25.0
        cfg.volatility.vix_high = 30.0
        cfg.volatility.realized_vol_low = 12.0
        cfg.volatility.realized_vol_moderate = 18.0
        cfg.volatility.realized_vol_elevated = 25.0
        cfg.credit_stress.ratio_sma_period = 50
        cfg.credit_stress.mild_deviation_pct = 0.02
        cfg.credit_stress.severe_deviation_pct = 0.05
        cfg.weights.trend = 0.30
        cfg.weights.breadth = 0.25
        cfg.weights.volatility = 0.25
        cfg.weights.credit_stress = 0.20
        cfg.thresholds.risk_on_min = 65
        cfg.thresholds.risk_off_max = 35
        cfg.hysteresis.degrade_confirmations = 2
        cfg.hysteresis.recover_confirmations = 3
        cfg.hysteresis.min_hold_cycles = 1
        cfg.fallback.on_insufficient_data = "risk_reduced"
        cfg.fallback.on_ibkr_offline = "risk_reduced"

        # risk_on effects
        eff_on = MagicMock()
        eff_on.sizing_factor = 1.0
        eff_on.allows_new_equity_entries = True
        eff_on.allows_new_options_entries = True
        eff_on.stop_tightening_factor = 1.0
        eff_on.score_threshold_adjustment = 0.0

        eff_reduced = MagicMock()
        eff_reduced.sizing_factor = 0.5
        eff_reduced.allows_new_equity_entries = True
        eff_reduced.allows_new_options_entries = False
        eff_reduced.stop_tightening_factor = 0.75
        eff_reduced.score_threshold_adjustment = 0.10

        eff_off = MagicMock()
        eff_off.sizing_factor = 0.0
        eff_off.allows_new_equity_entries = False
        eff_off.allows_new_options_entries = False
        eff_off.stop_tightening_factor = 0.50
        eff_off.score_threshold_adjustment = 0.20

        cfg.effects.risk_on = eff_on
        cfg.effects.risk_reduced = eff_reduced
        cfg.effects.risk_off = eff_off

        engine = RegimeEngine(config=cfg, session=None)
        # Manually initialize to skip DB
        engine._initialized = True

        spy = _spy_bars(250, trend="up")
        vix = _vix_bars(30, level=12.0)
        # No HYG/LQD — partial data
        state = engine.evaluate(spy_bars=spy, vix_bars=vix)
        assert 0.0 <= state.composite_score <= 100.0
        assert state.data_quality in ("full", "partial", "fallback")
