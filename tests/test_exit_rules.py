"""Unit tests for individual exit rule functions."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from trader.exit_rules import (
    compute_r_multiple,
    compute_current_atr,
    check_hard_stop,
    check_max_holding_days,
    check_profit_target_full,
    check_partial_profit,
    check_regime_exit,
    check_score_degradation,
    update_trailing_stop,
    check_options_max_loss,
    check_dte_exit,
    check_options_profit_target,
    check_options_regime_exit,
    check_iv_crush_exit,
    check_delta_drift_exit,
    check_options_score_degradation,
    check_theta_bleed,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_equity_tm(**overrides):
    defaults = dict(
        id=1,
        symbol="AAPL",
        portfolio_id="equity_swing",
        instrument_type="equity",
        entry_price=100.0,
        entry_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
        entry_atr=2.0,
        entry_score=0.70,
        entry_regime="risk_on",
        direction="long",
        quantity=100,
        current_quantity=100,
        initial_stop=96.0,
        current_stop=96.0,
        risk_per_share=4.0,
        highest_price_since_entry=100.0,
        lowest_price_since_entry=100.0,
        current_r_multiple=0.0,
        trailing_activated=False,
        partial_profit_taken=False,
        days_held=0,
        consecutive_below_threshold=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_options_tm(**overrides):
    defaults = dict(
        id=2,
        symbol="MSFT",
        portfolio_id="options_swing",
        instrument_type="debit_spread",
        entry_price=3.0,          # debit paid
        entry_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
        entry_iv=0.30,
        entry_net_delta=0.25,
        expiry_date=datetime(2026, 5, 16, tzinfo=timezone.utc),
        direction="long",
        quantity=5,
        current_quantity=5,
        initial_stop=3.0,
        current_stop=3.0,
        risk_per_share=3.0,
        max_profit=2.0,           # spread_width - debit = 5 - 3
        max_loss=3.0,
        long_strike=200.0,
        short_strike=205.0,
        consecutive_below_threshold=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _equity_exit_cfg(**overrides):
    from common.config import EquityExitConfig
    return EquityExitConfig(**overrides)


def _options_exit_cfg(**overrides):
    from common.config import OptionsExitConfig
    return OptionsExitConfig(**overrides)


def _now():
    return datetime(2026, 4, 21, tzinfo=timezone.utc)


# ── compute_r_multiple ───────────────────────────────────────────────────────


def test_r_multiple_long_positive():
    tm = _make_equity_tm(entry_price=100.0, risk_per_share=4.0, direction="long")
    assert compute_r_multiple(tm, 112.0) == pytest.approx(3.0)


def test_r_multiple_long_negative():
    tm = _make_equity_tm(entry_price=100.0, risk_per_share=4.0, direction="long")
    assert compute_r_multiple(tm, 96.0) == pytest.approx(-1.0)


def test_r_multiple_zero_risk():
    tm = _make_equity_tm(risk_per_share=0.0)
    assert compute_r_multiple(tm, 120.0) == 0.0


# ── check_hard_stop ──────────────────────────────────────────────────────────


def test_hard_stop_long_triggers_below():
    tm = _make_equity_tm(current_stop=96.0, direction="long")
    intent = check_hard_stop(tm, 95.5)
    assert intent is not None
    assert intent.exit_rule == "hard_stop"
    assert intent.urgency == "immediate"
    assert intent.priority == 0


def test_hard_stop_long_does_not_trigger_above():
    tm = _make_equity_tm(current_stop=96.0, direction="long")
    assert check_hard_stop(tm, 97.0) is None


def test_hard_stop_at_exact_stop_price_triggers():
    tm = _make_equity_tm(current_stop=96.0, direction="long")
    # At exactly the stop level we exit (not strictly below)
    assert check_hard_stop(tm, 96.0) is not None


# ── check_max_holding_days ───────────────────────────────────────────────────


def test_max_holding_days_triggers_when_due():
    tm = _make_equity_tm(entry_date=datetime(2026, 3, 31, tzinfo=timezone.utc))
    cfg = _equity_exit_cfg(max_holding_days=20)
    intent = check_max_holding_days(tm, cfg, _now())
    assert intent is not None
    assert intent.exit_rule == "max_holding_days"
    assert intent.priority == 1


def test_max_holding_days_no_trigger_when_early():
    tm = _make_equity_tm(entry_date=datetime(2026, 4, 15, tzinfo=timezone.utc))
    cfg = _equity_exit_cfg(max_holding_days=20)
    assert check_max_holding_days(tm, cfg, _now()) is None


# ── check_profit_target_full ─────────────────────────────────────────────────


def test_profit_target_full_triggers_at_3r():
    tm = _make_equity_tm(entry_price=100.0, risk_per_share=4.0, current_quantity=100)
    cfg = _equity_exit_cfg(profit_target_enabled=True, profit_target_r=3.0)
    intent = check_profit_target_full(tm, 112.0, cfg)
    assert intent is not None
    assert intent.exit_rule == "profit_target_full"
    assert intent.priority == 2


def test_profit_target_full_no_trigger_below_target():
    tm = _make_equity_tm(entry_price=100.0, risk_per_share=4.0)
    cfg = _equity_exit_cfg(profit_target_enabled=True, profit_target_r=3.0)
    assert check_profit_target_full(tm, 107.0, cfg) is None


def test_profit_target_full_disabled():
    tm = _make_equity_tm(entry_price=100.0, risk_per_share=4.0)
    cfg = _equity_exit_cfg(profit_target_enabled=False, profit_target_r=3.0)
    assert check_profit_target_full(tm, 120.0, cfg) is None


# ── check_partial_profit ─────────────────────────────────────────────────────


def test_partial_profit_triggers_at_2r():
    tm = _make_equity_tm(
        entry_price=100.0, risk_per_share=4.0, current_quantity=100,
        partial_profit_taken=False,
    )
    cfg = _equity_exit_cfg(
        partial_profit_enabled=True, partial_profit_r=2.0, partial_profit_pct=0.5
    )
    intent = check_partial_profit(tm, 108.0, cfg)
    assert intent is not None
    assert intent.is_partial is True
    assert intent.quantity == 50
    assert intent.exit_rule == "partial_profit"
    assert intent.priority == 3


def test_partial_profit_does_not_repeat():
    tm = _make_equity_tm(
        entry_price=100.0, risk_per_share=4.0, current_quantity=100,
        partial_profit_taken=True,
    )
    cfg = _equity_exit_cfg(partial_profit_enabled=True, partial_profit_r=2.0)
    assert check_partial_profit(tm, 110.0, cfg) is None


def test_partial_profit_no_trigger_below_threshold():
    tm = _make_equity_tm(
        entry_price=100.0, risk_per_share=4.0, partial_profit_taken=False
    )
    cfg = _equity_exit_cfg(partial_profit_enabled=True, partial_profit_r=2.0)
    assert check_partial_profit(tm, 104.0, cfg) is None


# ── check_regime_exit ────────────────────────────────────────────────────────


def test_regime_exit_close_triggers_on_risk_off():
    tm = _make_equity_tm(entry_regime="risk_on", current_quantity=100)
    cfg = _equity_exit_cfg(regime_exit_enabled=True, regime_exit_action="close")
    intent = check_regime_exit(tm, "risk_off", cfg)
    assert intent is not None
    assert intent.exit_rule == "regime_change"


def test_regime_exit_no_trigger_if_entered_during_risk_off():
    tm = _make_equity_tm(entry_regime="risk_off")
    cfg = _equity_exit_cfg(regime_exit_enabled=True, regime_exit_action="close")
    assert check_regime_exit(tm, "risk_off", cfg) is None


def test_regime_exit_tighten_returns_none_no_order():
    tm = _make_equity_tm(entry_regime="risk_on")
    cfg = _equity_exit_cfg(regime_exit_enabled=True, regime_exit_action="tighten")
    assert check_regime_exit(tm, "risk_off", cfg) is None


def test_regime_exit_no_trigger_during_risk_on():
    tm = _make_equity_tm(entry_regime="risk_on")
    cfg = _equity_exit_cfg(regime_exit_enabled=True, regime_exit_action="close")
    assert check_regime_exit(tm, "risk_on", cfg) is None


# ── check_score_degradation ──────────────────────────────────────────────────


def test_score_degradation_triggers_after_consecutive_cycles():
    tm = _make_equity_tm(consecutive_below_threshold=1, current_quantity=100)
    cfg = _equity_exit_cfg(
        score_exit_enabled=True, score_exit_threshold=0.40, score_exit_consecutive_cycles=2
    )
    # Second cycle below — should trigger
    intent = check_score_degradation(tm, 0.35, cfg)
    assert intent is not None
    assert intent.exit_rule == "score_degradation"
    assert tm.consecutive_below_threshold == 2


def test_score_degradation_resets_on_recovery():
    tm = _make_equity_tm(consecutive_below_threshold=2)
    cfg = _equity_exit_cfg(
        score_exit_enabled=True, score_exit_threshold=0.40, score_exit_consecutive_cycles=3
    )
    check_score_degradation(tm, 0.65, cfg)
    assert tm.consecutive_below_threshold == 0


def test_score_degradation_no_trigger_when_none():
    tm = _make_equity_tm(consecutive_below_threshold=5)
    cfg = _equity_exit_cfg(score_exit_enabled=True, score_exit_consecutive_cycles=2)
    assert check_score_degradation(tm, None, cfg) is None


def test_score_degradation_disabled():
    tm = _make_equity_tm(consecutive_below_threshold=10)
    cfg = _equity_exit_cfg(score_exit_enabled=False)
    assert check_score_degradation(tm, 0.10, cfg) is None


# ── update_trailing_stop ─────────────────────────────────────────────────────


def _make_bars(n: int = 30) -> pd.DataFrame:
    import numpy as np
    closes = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    highs = closes + np.abs(np.random.randn(n) * 0.3)
    lows = closes - np.abs(np.random.randn(n) * 0.3)
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes})


def test_trailing_stop_ratchets_up():
    tm = _make_equity_tm(
        entry_price=100.0, risk_per_share=4.0, current_stop=96.0,
        highest_price_since_entry=108.0, trailing_activated=True,
    )
    cfg = _equity_exit_cfg(
        trailing_stop_enabled=True, trailing_method="percent",
        trailing_percent=0.05, trailing_activation_r=1.0,
        stop_never_moves_down=True,
    )
    bars = _make_bars()
    # Price at 108 gives R = 2.0 >= activation 1.0
    new_stop = update_trailing_stop(tm, 108.0, bars, cfg)
    assert new_stop is not None
    expected = 108.0 * 0.95
    assert new_stop == pytest.approx(expected, rel=1e-3)
    assert new_stop > tm.current_stop  # ratcheted up


def test_trailing_stop_no_update_below_activation():
    tm = _make_equity_tm(
        entry_price=100.0, risk_per_share=4.0, current_stop=96.0,
        highest_price_since_entry=102.0,
    )
    cfg = _equity_exit_cfg(
        trailing_stop_enabled=True, trailing_activation_r=1.5,
        trailing_method="percent", trailing_percent=0.05,
    )
    # R = (102 - 100) / 4 = 0.5 < activation 1.5
    new_stop = update_trailing_stop(tm, 102.0, _make_bars(), cfg)
    assert new_stop is None


def test_trailing_stop_never_moves_down():
    tm = _make_equity_tm(
        entry_price=100.0, risk_per_share=4.0, current_stop=98.0,
        highest_price_since_entry=102.0, trailing_activated=True,
    )
    cfg = _equity_exit_cfg(
        trailing_stop_enabled=True, trailing_method="percent",
        trailing_percent=0.05, trailing_activation_r=0.5,
        stop_never_moves_down=True,
    )
    # 102 * 0.95 = 96.9, which is below current_stop=98
    new_stop = update_trailing_stop(tm, 102.0, _make_bars(), cfg)
    assert new_stop is None


# ── OPTIONS: check_options_max_loss ─────────────────────────────────────────


def test_options_max_loss_triggers():
    tm = _make_options_tm(entry_price=3.0)
    cfg = _options_exit_cfg(max_loss_exit_pct=0.80)
    # Current value = 0.5 → loss = (3 - 0.5) / 3 = 83.3% >= 80%
    intent = check_options_max_loss(tm, 0.5, cfg)
    assert intent is not None
    assert intent.exit_rule == "max_loss_stop"
    assert intent.urgency == "immediate"
    assert intent.priority == 0


def test_options_max_loss_no_trigger_small_loss():
    tm = _make_options_tm(entry_price=3.0)
    cfg = _options_exit_cfg(max_loss_exit_pct=0.80)
    assert check_options_max_loss(tm, 2.0, cfg) is None


def test_options_max_loss_no_trigger_none_value():
    tm = _make_options_tm()
    cfg = _options_exit_cfg()
    assert check_options_max_loss(tm, None, cfg) is None


# ── OPTIONS: check_dte_exit ──────────────────────────────────────────────────


def test_dte_exit_triggers_at_threshold():
    expiry = datetime(2026, 4, 28, tzinfo=timezone.utc)  # 7 days from _now()
    tm = _make_options_tm(expiry_date=expiry)
    cfg = _options_exit_cfg(dte_exit_threshold=7)
    intent = check_dte_exit(tm, cfg, _now())
    assert intent is not None
    assert intent.exit_rule == "dte_threshold"
    assert intent.urgency == "immediate"
    assert intent.priority == 1


def test_dte_exit_no_trigger_above_threshold():
    expiry = datetime(2026, 5, 15, tzinfo=timezone.utc)  # 24 days from _now()
    tm = _make_options_tm(expiry_date=expiry)
    cfg = _options_exit_cfg(dte_exit_threshold=7)
    assert check_dte_exit(tm, cfg, _now()) is None


def test_dte_exit_no_expiry():
    tm = _make_options_tm(expiry_date=None)
    cfg = _options_exit_cfg(dte_exit_threshold=7)
    assert check_dte_exit(tm, cfg, _now()) is None


# ── OPTIONS: check_options_profit_target ────────────────────────────────────


def test_options_profit_target_triggers_at_50pct():
    # max_profit=2.0, entry=3.0 → need current_value >= 4.0 for 50%
    tm = _make_options_tm(entry_price=3.0, max_profit=2.0,
                          expiry_date=datetime(2026, 5, 30, tzinfo=timezone.utc))
    cfg = _options_exit_cfg(profit_target_pct=0.50, profit_target_aggressive_pct=0.75,
                            dte_warning_threshold=14)
    intent = check_options_profit_target(tm, 4.0, cfg, _now())
    assert intent is not None
    assert intent.exit_rule == "profit_target"


def test_options_profit_target_aggressive_near_expiry():
    expiry = datetime(2026, 4, 28, tzinfo=timezone.utc)  # 7 days out
    tm = _make_options_tm(entry_price=3.0, max_profit=2.0, expiry_date=expiry)
    cfg = _options_exit_cfg(profit_target_pct=0.50, profit_target_aggressive_pct=0.75,
                            dte_warning_threshold=14)
    # 75% threshold: need current_value >= 4.5
    assert check_options_profit_target(tm, 4.2, cfg, _now()) is None
    intent = check_options_profit_target(tm, 4.6, cfg, _now())
    assert intent is not None


# ── OPTIONS: check_iv_crush_exit ─────────────────────────────────────────────


def test_iv_crush_triggers():
    tm = _make_options_tm(entry_iv=0.40)
    cfg = _options_exit_cfg(iv_crush_exit_enabled=True, iv_crush_threshold=0.30)
    # current IV = 0.26 → drop = (0.40-0.26)/0.40 = 35% >= 30%
    intent = check_iv_crush_exit(tm, 0.26, cfg)
    assert intent is not None
    assert intent.exit_rule == "iv_crush"


def test_iv_crush_no_trigger_small_drop():
    tm = _make_options_tm(entry_iv=0.40)
    cfg = _options_exit_cfg(iv_crush_exit_enabled=True, iv_crush_threshold=0.30)
    assert check_iv_crush_exit(tm, 0.35, cfg) is None


def test_iv_crush_disabled():
    tm = _make_options_tm(entry_iv=0.40)
    cfg = _options_exit_cfg(iv_crush_exit_enabled=False)
    assert check_iv_crush_exit(tm, 0.10, cfg) is None


# ── OPTIONS: check_delta_drift_exit ──────────────────────────────────────────


def test_delta_drift_triggers():
    tm = _make_options_tm(entry_net_delta=0.25)
    cfg = _options_exit_cfg(delta_drift_exit_enabled=True, max_delta_drift=0.15)
    # Drift = |0.05 - 0.25| = 0.20 >= 0.15
    intent = check_delta_drift_exit(tm, 0.05, cfg)
    assert intent is not None
    assert intent.exit_rule == "delta_drift"


def test_delta_drift_no_trigger_within_limit():
    tm = _make_options_tm(entry_net_delta=0.25)
    cfg = _options_exit_cfg(delta_drift_exit_enabled=True, max_delta_drift=0.15)
    assert check_delta_drift_exit(tm, 0.20, cfg) is None


# ── OPTIONS: check_options_score_degradation ────────────────────────────────


def test_options_score_degradation_triggers():
    tm = _make_options_tm(consecutive_below_threshold=1)
    cfg = _options_exit_cfg(score_exit_enabled=True, score_exit_threshold=0.40,
                            score_exit_consecutive_cycles=2)
    intent = check_options_score_degradation(tm, 0.30, cfg)
    assert intent is not None
    assert intent.exit_rule == "score_degradation"


# ── Priority order: full exit overrides partial ──────────────────────────────


def test_hard_stop_priority_over_partial_profit():
    """If both hard stop and partial profit would fire, hard stop wins (priority 0 < 3)."""
    tm = _make_equity_tm(
        entry_price=100.0, risk_per_share=4.0, current_stop=96.0,
        current_quantity=100, partial_profit_taken=False,
    )
    # Hard stop fires at 95.5 (below stop)
    # Partial profit R = (95.5 - 100) / 4 = -1.125 — won't fire anyway
    # But this tests that partial with is_partial=True doesn't block the hard stop
    hs = check_hard_stop(tm, 95.5)
    pp = check_partial_profit(tm, 95.5, _equity_exit_cfg(partial_profit_r=2.0))
    assert hs is not None
    assert pp is None  # price is below entry, so partial profit can't fire
    assert hs.priority < (pp.priority if pp else 999)
