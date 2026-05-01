"""Integration tests for ExitManager and the exit evaluation pipeline."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from trader.exit_models import ExitIntent, ExitEvaluation


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_context(regime="risk_on", ranked=None, portfolio_id=None):
    return SimpleNamespace(
        regime=regime,
        ranked=ranked or [],
        now=datetime(2026, 4, 21, tzinfo=timezone.utc),
        portfolio_id=portfolio_id,
    )


def _equity_exit_cfg(**overrides):
    from common.config import ExitConfig
    cfg = ExitConfig()
    for k, v in overrides.items():
        setattr(cfg.equity, k, v)
    return cfg


def _options_exit_cfg(**overrides):
    from common.config import ExitConfig
    cfg = ExitConfig()
    for k, v in overrides.items():
        setattr(cfg.options, k, v)
    return cfg


# ── ExitIntent dataclass ──────────────────────────────────────────────────────


def test_exit_intent_defaults():
    intent = ExitIntent(
        symbol="AAPL",
        portfolio_id="equity_swing",
        instrument_type="equity",
        direction="long",
        quantity=100,
        exit_rule="hard_stop",
    )
    assert intent.is_partial is False
    assert intent.urgency == "normal"
    assert intent.priority == 0
    assert intent.metadata == {}


def test_exit_evaluation_defaults():
    ev = ExitEvaluation(symbol="AAPL", portfolio_id="equity_swing", management_id=1)
    assert ev.should_exit is False
    assert ev.exit_intents == []
    assert ev.stop_updated is False


# ── ExitManager with mocked DB session ───────────────────────────────────────


def _make_tm_obj(**kwargs):
    """Build a mock TradeManagement-like object."""
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
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_options_tm_obj(**kwargs):
    defaults = dict(
        id=2,
        symbol="MSFT",
        portfolio_id="options_swing",
        instrument_type="debit_spread",
        entry_price=3.0,
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
        max_profit=2.0,
        max_loss=3.0,
        long_strike=200.0,
        short_strike=205.0,
        consecutive_below_threshold=0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


_UNSET = object()


def _build_manager(tm_list, exit_cfg=None, price_override=_UNSET):
    from trader.exits import ExitManager
    from common.config import ExitConfig

    session = MagicMock()
    query_mock = MagicMock()
    query_mock.all.return_value = tm_list
    query_mock.filter.return_value = query_mock
    session.query.return_value = query_mock
    session.commit = MagicMock()

    cfg = exit_cfg or ExitConfig()
    mgr = ExitManager(cfg, session, ibkr_client=None)

    # Stub out price fetcher so tests don't need IBKR
    _price = 100.0 if price_override is _UNSET else price_override
    mgr._get_current_price = lambda symbol: _price

    mgr._get_bars = lambda symbol: None  # disables trailing stop computation
    mgr._get_current_spread_value = lambda tm: None
    mgr._get_current_score = lambda symbol, ranked: None
    mgr._get_current_iv = lambda symbol: None
    mgr._get_current_net_delta = lambda tm: None

    return mgr


# ── Test: disabled exits returns empty list ───────────────────────────────────


def test_exit_manager_disabled_returns_empty():
    from trader.exits import ExitManager
    from common.config import ExitConfig

    cfg = ExitConfig(enabled=False)
    mgr = ExitManager(cfg, MagicMock())
    result = mgr.evaluate_all_positions(_make_context())
    assert result == []


# ── Test: no positions returns empty list ─────────────────────────────────────


def test_exit_manager_no_positions():
    mgr = _build_manager(tm_list=[])
    result = mgr.evaluate_all_positions(_make_context())
    assert result == []


# ── Test: hard stop fires when price is below stop ───────────────────────────


def test_exit_manager_hard_stop_fires():
    recent = datetime(2026, 4, 19, tzinfo=timezone.utc)
    tm = _make_tm_obj(current_stop=96.0, entry_date=recent)
    mgr = _build_manager([tm], price_override=95.0)
    evaluations = mgr.evaluate_all_positions(_make_context())
    assert len(evaluations) == 1
    ev = evaluations[0]
    assert ev.should_exit is True
    assert any(i.exit_rule == "hard_stop" for i in ev.exit_intents)


# ── Test: no price data → position skipped safely ────────────────────────────


def test_exit_manager_no_price_data_skips():
    # Use a recent entry_date so max_holding_days doesn't also fire
    recent = datetime(2026, 4, 20, tzinfo=timezone.utc)
    tm = _make_tm_obj(entry_date=recent)
    mgr = _build_manager([tm], price_override=None)
    evaluations = mgr.evaluate_all_positions(_make_context())
    assert len(evaluations) == 1
    ev = evaluations[0]
    assert ev.should_exit is False
    assert len(ev.warnings) > 0


# ── Test: profit target fires at 3R ──────────────────────────────────────────


def test_exit_manager_profit_target_full():
    from common.config import ExitConfig, EquityExitConfig
    cfg = ExitConfig(equity=EquityExitConfig(
        profit_target_enabled=True, profit_target_r=3.0,
        trailing_stop_enabled=False, max_holding_days=30,
    ))
    recent = datetime(2026, 4, 19, tzinfo=timezone.utc)
    tm = _make_tm_obj(entry_price=100.0, risk_per_share=4.0, current_stop=90.0,
                      entry_date=recent)
    mgr = _build_manager([tm], exit_cfg=cfg, price_override=112.0)  # R = 3.0 exactly
    evaluations = mgr.evaluate_all_positions(_make_context())
    ev = evaluations[0]
    assert ev.should_exit is True
    assert any(i.exit_rule == "profit_target_full" for i in ev.exit_intents)


# ── Test: max holding days ────────────────────────────────────────────────────


def test_exit_manager_max_holding_days():
    from common.config import ExitConfig, EquityExitConfig
    cfg = ExitConfig(equity=EquityExitConfig(max_holding_days=10))
    # Entry 15 days ago
    entry_date = datetime(2026, 4, 6, tzinfo=timezone.utc)
    tm = _make_tm_obj(entry_date=entry_date, current_stop=0.0)
    mgr = _build_manager([tm], exit_cfg=cfg, price_override=105.0)
    evaluations = mgr.evaluate_all_positions(_make_context())
    ev = evaluations[0]
    assert ev.should_exit is True
    assert any(i.exit_rule == "max_holding_days" for i in ev.exit_intents)


# ── Test: partial profit reduces quantity ─────────────────────────────────────


def test_partial_profit_quantity_and_metadata():
    from trader.exit_rules import check_partial_profit
    from common.config import EquityExitConfig

    from types import SimpleNamespace
    tm = SimpleNamespace(
        id=1, symbol="AAPL", portfolio_id="equity_swing", instrument_type="equity",
        entry_price=100.0, risk_per_share=4.0, direction="long",
        current_quantity=100, partial_profit_taken=False,
    )
    cfg = EquityExitConfig(partial_profit_enabled=True, partial_profit_r=2.0, partial_profit_pct=0.5)
    intent = check_partial_profit(tm, 108.5, cfg)
    assert intent is not None
    assert intent.is_partial is True
    assert intent.quantity == 50
    assert intent.metadata.get("move_stop_to_breakeven") is True


# ── Test: priority ordering — full exit skips later rules ────────────────────


def test_full_exit_stops_rule_evaluation():
    from common.config import ExitConfig, EquityExitConfig
    # Hard stop fires (priority 0), should prevent further rules from generating intents
    cfg = ExitConfig(equity=EquityExitConfig(
        profit_target_enabled=True, profit_target_r=3.0, trailing_stop_enabled=False,
        max_holding_days=30,
    ))
    recent = datetime(2026, 4, 19, tzinfo=timezone.utc)
    # Price 90: hard stop fires (stop=96), profit target does NOT fire (negative R)
    tm = _make_tm_obj(current_stop=96.0, entry_price=100.0, risk_per_share=4.0,
                      entry_date=recent)
    mgr = _build_manager([tm], exit_cfg=cfg, price_override=90.0)
    evaluations = mgr.evaluate_all_positions(_make_context())
    ev = evaluations[0]
    triggered = [i.exit_rule for i in ev.exit_intents]
    assert "hard_stop" in triggered
    # Full exit means no more intents after it
    assert len([i for i in ev.exit_intents if not i.is_partial]) == 1


# ── Test: regime tightening side-effect ──────────────────────────────────────


def test_regime_tighten_updates_stop():
    from common.config import ExitConfig, EquityExitConfig
    cfg = ExitConfig(equity=EquityExitConfig(
        regime_exit_enabled=True, regime_exit_action="tighten",
        regime_tighten_atr_multiplier=1.0, trailing_stop_enabled=False,
        profit_target_enabled=False, max_holding_days=30,
    ))
    # entry_atr=2.0, price=105, tightened=105-2=103 > current_stop=96
    recent = datetime(2026, 4, 19, tzinfo=timezone.utc)
    tm = _make_tm_obj(entry_atr=2.0, current_stop=96.0, entry_regime="risk_on",
                      entry_date=recent)
    mgr = _build_manager([tm], exit_cfg=cfg, price_override=105.0)
    evaluations = mgr.evaluate_all_positions(_make_context(regime="risk_off"))
    ev = evaluations[0]
    assert ev.stop_updated is True
    assert ev.new_stop_price == pytest.approx(103.0)
    assert ev.should_exit is False  # tighten, not close


# ── Test: score degradation counter resets ────────────────────────────────────


def test_score_degradation_counter_resets_on_good_score():
    from trader.exit_rules import check_score_degradation
    from common.config import EquityExitConfig
    from types import SimpleNamespace

    tm = SimpleNamespace(consecutive_below_threshold=3, id=1, symbol="X",
                         portfolio_id="equity_swing", instrument_type="equity",
                         direction="long", current_quantity=10)
    cfg = EquityExitConfig(score_exit_enabled=True, score_exit_threshold=0.40,
                           score_exit_consecutive_cycles=2)
    result = check_score_degradation(tm, 0.80, cfg)
    assert result is None
    assert tm.consecutive_below_threshold == 0


# ── Test: options DTE exit at exact threshold ─────────────────────────────────


def test_options_dte_exact_threshold():
    from trader.exit_rules import check_dte_exit
    from common.config import OptionsExitConfig
    from types import SimpleNamespace

    expiry = datetime(2026, 4, 28, tzinfo=timezone.utc)  # 7 days from now=4/21
    tm = SimpleNamespace(id=2, symbol="MSFT", portfolio_id="options_swing",
                         instrument_type="debit_spread", direction="long",
                         current_quantity=5, expiry_date=expiry)
    cfg = OptionsExitConfig(dte_exit_threshold=7)
    now = datetime(2026, 4, 21, tzinfo=timezone.utc)
    intent = check_dte_exit(tm, cfg, now)
    assert intent is not None
    assert intent.metadata["dte"] == 7


# ── Test: options with unknown instrument_type logs warning, no crash ─────────


def test_exit_manager_unknown_instrument_type_skips():
    tm = _make_tm_obj(instrument_type="future")
    mgr = _build_manager([tm])
    evaluations = mgr.evaluate_all_positions(_make_context())
    assert evaluations == []


# ── Test: options evaluation path ────────────────────────────────────────────


def test_options_max_loss_path():
    from common.config import ExitConfig, OptionsExitConfig
    cfg = ExitConfig(options=OptionsExitConfig(max_loss_exit_pct=0.50))
    tm = _make_options_tm_obj()  # entry_price=3.0
    mgr = _build_manager([tm], exit_cfg=cfg)
    # Override spread value to trigger 83% loss
    mgr._get_current_spread_value = lambda t: 0.5
    evaluations = mgr.evaluate_all_positions(_make_context())
    ev = evaluations[0]
    assert ev.should_exit is True
    assert ev.exit_intents[0].exit_rule == "max_loss_stop"
