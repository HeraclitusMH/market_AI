"""Tests for the options trade planner."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest

from trader.options_planner import plan_trade, _select_expiry, _dte, _check_cooldown, _check_max_trades_today
from trader.ranking import RankedSymbol


# ── Helpers ────────────────────────────────────────────────────────────

def _candidate(symbol="AAPL", bias="bullish", score=0.7) -> RankedSymbol:
    return RankedSymbol(
        symbol=symbol, sector="Technology", score_total=score,
        components={"market": {"raw": 0.3}, "sector": {"raw": 0.5}, "ticker": {"raw": score}},
        eligible=True, reasons=[], sources=["core"], bias=bias,
    )


def _make_chain(expirations: list[str]) -> MagicMock:
    chain = MagicMock()
    chain.expirations = expirations
    return chain


def _expiry_str(days_from_now: int) -> str:
    d = (datetime.now() + timedelta(days=days_from_now)).date()
    return d.strftime("%Y%m%d")


# ── Expiry selection ───────────────────────────────────────────────────

def test_select_expiry_target_nearest_30():
    chains = [_make_chain([_expiry_str(21), _expiry_str(28), _expiry_str(35), _expiry_str(45)])]
    result = _select_expiry(chains, dte_min=21, dte_max=45, dte_target=30, dte_fallback_min=14)
    assert result == _expiry_str(28)  # closest to 30


def test_select_expiry_none_in_range_uses_fallback():
    chains = [_make_chain([_expiry_str(14), _expiry_str(15)])]
    result = _select_expiry(chains, dte_min=21, dte_max=45, dte_target=30, dte_fallback_min=14)
    assert result is not None
    assert result in [_expiry_str(14), _expiry_str(15)]


def test_select_expiry_no_suitable_returns_none():
    chains = [_make_chain([_expiry_str(3), _expiry_str(7)])]  # all below fallback_min
    result = _select_expiry(chains, dte_min=21, dte_max=45, dte_target=30, dte_fallback_min=14)
    assert result is None


def test_select_expiry_exact_target():
    exp = _expiry_str(30)
    chains = [_make_chain([_expiry_str(25), exp, _expiry_str(35)])]
    result = _select_expiry(chains, dte_min=21, dte_max=45, dte_target=30, dte_fallback_min=14)
    assert result == exp


def test_dte_computation():
    exp = _expiry_str(21)
    assert _dte(exp) == 21


# ── Cooldown & daily limit ─────────────────────────────────────────────

def test_check_cooldown_blocks_recent_plan():
    recent = MagicMock()
    recent.ts = datetime.utcnow() - timedelta(hours=2)

    with patch("trader.options_planner.get_db") as mock_db_ctx:
        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.filter.return_value.first.return_value = recent

        result = _check_cooldown("AAPL", cooldown_hours=6)

    assert result is not None
    assert "cooldown" in result.lower()


def test_check_cooldown_allows_old_plan():
    with patch("trader.options_planner.get_db") as mock_db_ctx:
        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = _check_cooldown("AAPL", cooldown_hours=6)

    assert result is None


def test_check_max_trades_today_blocks():
    with patch("trader.options_planner.get_db") as mock_db_ctx:
        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.count.return_value = 3

        result = _check_max_trades_today(max_trades=3)

    assert result is not None
    assert "max_trades" in result.lower()


def test_check_max_trades_today_allows():
    with patch("trader.options_planner.get_db") as mock_db_ctx:
        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.count.return_value = 1

        result = _check_max_trades_today(max_trades=3)

    assert result is None


# ── plan_trade skips ───────────────────────────────────────────────────

def test_plan_trade_skips_no_ibkr():
    candidate = _candidate()
    with patch("trader.options_planner._check_cooldown", return_value=None), \
         patch("trader.options_planner._check_max_trades_today", return_value=None), \
         patch("trader.options_planner._save_plan") as mock_save:
        mock_save.return_value = MagicMock(status="skipped")
        plan = plan_trade(candidate, client=None)

    mock_save.assert_called_once()
    call_kwargs = mock_save.call_args
    assert call_kwargs.kwargs["status"] == "skipped"
    assert call_kwargs.kwargs["skip_reason"] == "no_ibkr_client"


def test_plan_trade_skips_on_cooldown():
    candidate = _candidate()
    with patch("trader.options_planner._check_cooldown", return_value="cooldown: last plan 2h ago"), \
         patch("trader.options_planner._save_plan") as mock_save:
        mock_save.return_value = MagicMock(status="skipped")
        plan = plan_trade(candidate, client=MagicMock())

    call_kwargs = mock_save.call_args
    assert call_kwargs.kwargs["status"] == "skipped"
    assert "cooldown" in call_kwargs.kwargs["skip_reason"]


def test_plan_trade_skips_no_chains():
    candidate = _candidate()
    mock_client = MagicMock()
    mock_client.option_chains.return_value = []

    with patch("trader.options_planner._check_cooldown", return_value=None), \
         patch("trader.options_planner._check_max_trades_today", return_value=None), \
         patch("trader.options_planner.get_config") as mock_cfg, \
         patch("trader.options_planner._save_plan") as mock_save:

        rc = MagicMock()
        rc.dte_min = 21; rc.dte_max = 45; rc.dte_target = 30; rc.dte_fallback_min = 14
        rc.cooldown_hours = 6; rc.max_trades_per_day = 3
        mock_cfg.return_value.ranking = rc
        mock_cfg.return_value.risk.max_risk_per_trade_pct = 1.0

        mock_save.return_value = MagicMock(status="skipped")
        plan = plan_trade(candidate, client=mock_client)

    call_kwargs = mock_save.call_args
    assert "no_option_chains" in call_kwargs.kwargs["skip_reason"]


def test_plan_trade_skips_no_suitable_expiry():
    candidate = _candidate()
    mock_client = MagicMock()

    chain = MagicMock()
    chain.expirations = [_expiry_str(2), _expiry_str(5)]  # too short
    mock_client.option_chains.return_value = [chain]

    with patch("trader.options_planner._check_cooldown", return_value=None), \
         patch("trader.options_planner._check_max_trades_today", return_value=None), \
         patch("trader.options_planner.get_config") as mock_cfg, \
         patch("trader.options_planner._save_plan") as mock_save:

        rc = MagicMock()
        rc.dte_min = 21; rc.dte_max = 45; rc.dte_target = 30; rc.dte_fallback_min = 14
        rc.cooldown_hours = 6; rc.max_trades_per_day = 3
        mock_cfg.return_value.ranking = rc
        mock_cfg.return_value.risk.max_risk_per_trade_pct = 1.0

        mock_save.return_value = MagicMock(status="skipped")
        plan = plan_trade(candidate, client=mock_client)

    call_kwargs = mock_save.call_args
    assert "no_suitable_expiry" in call_kwargs.kwargs["skip_reason"]


# ── Full happy path (mocked IBKR + Greeks) ────────────────────────────

def _make_full_mock_client(expiry_str: str):
    """Build a mock IBKRClient that returns a valid chain + Greeks."""
    mock_client = MagicMock()
    chain = MagicMock()
    chain.expirations = [expiry_str]
    mock_client.option_chains.return_value = [chain]
    return mock_client


def test_plan_trade_proposed_when_all_ok():
    expiry = _expiry_str(30)
    mock_client = _make_full_mock_client(expiry)
    candidate = _candidate()

    mock_spread = MagicMock()
    mock_spread.long_strike = 150.0
    mock_spread.short_strike = 155.0
    mock_spread.right = "C"
    mock_spread.expiration = expiry
    mock_spread.strategy_type = "bull_call_debit_spread"
    mock_spread.long_greeks = MagicMock(delta=0.40)
    mock_spread.short_greeks = MagicMock(delta=0.20)

    mock_gate = MagicMock()
    mock_gate.approved = True
    mock_gate.reason = "ok"
    mock_gate.warnings = []
    mock_gate.checks_failed = []
    mock_gate.greeks_summary = {}

    mock_chain_greeks = MagicMock()
    mock_chain_greeks.valid_legs.return_value = [MagicMock()]
    mock_chain_greeks.iv_rank = 30.0

    mock_criteria = MagicMock()
    mock_criteria.iv_environment = "moderate"

    with patch("trader.options_planner._check_cooldown", return_value=None), \
         patch("trader.options_planner._check_max_trades_today", return_value=None), \
         patch("trader.options_planner.get_config") as mock_cfg, \
         patch("trader.options_planner.GreeksService") as MockGS, \
         patch("trader.options_planner.StrikeSelector") as MockSS, \
         patch("trader.options_planner.GreeksGate") as MockGG, \
         patch("trader.options_planner.GreeksLogger"), \
         patch("trader.options_planner.calculate_limit_price", return_value=2.50), \
         patch("trader.options_planner._get_nav", return_value=50_000.0), \
         patch("trader.options_planner._save_plan") as mock_save:

        rc = MagicMock()
        rc.dte_min = 21; rc.dte_max = 45; rc.dte_target = 30; rc.dte_fallback_min = 14
        rc.cooldown_hours = 6; rc.max_trades_per_day = 3
        mock_cfg.return_value.ranking = rc
        mock_cfg.return_value.risk.max_risk_per_trade_pct = 1.0

        gs_instance = MockGS.return_value
        gs_instance.fetch_chain_greeks.return_value = mock_chain_greeks

        ss_instance = MockSS.return_value
        ss_instance.adjust_delta_for_iv.return_value = mock_criteria
        ss_instance.select_debit_spread_strikes.return_value = mock_spread

        MockGG.return_value.evaluate.return_value = mock_gate

        mock_save.return_value = MagicMock(status="proposed")
        plan = plan_trade(candidate, client=mock_client)

    call_kwargs = mock_save.call_args.kwargs
    assert call_kwargs["status"] == "proposed"
    assert call_kwargs["symbol"] == "AAPL"
    assert call_kwargs["bias"] == "bullish"
    assert call_kwargs["strategy"] == "bull_call_debit_spread"

    # Pricing should be computed correctly
    pricing = call_kwargs["pricing"]
    assert pricing["debit_per_contract"] == pytest.approx(2.50, abs=0.01)
    assert pricing["max_loss_per_contract"] == pytest.approx(250.0, abs=0.01)
    assert pricing["spread_width"] == pytest.approx(5.0, abs=0.01)


def test_plan_trade_bearish_uses_puts():
    expiry = _expiry_str(28)
    mock_client = _make_full_mock_client(expiry)
    candidate = _candidate(symbol="NVDA", bias="bearish", score=-0.7)

    mock_spread = MagicMock()
    mock_spread.long_strike = 400.0
    mock_spread.short_strike = 395.0
    mock_spread.right = "P"
    mock_spread.expiration = expiry
    mock_spread.strategy_type = "bear_put_debit_spread"
    mock_spread.long_greeks = MagicMock(delta=-0.40)
    mock_spread.short_greeks = MagicMock(delta=-0.20)

    mock_gate = MagicMock()
    mock_gate.approved = True
    mock_gate.warnings = []
    mock_gate.checks_failed = []
    mock_gate.greeks_summary = {}

    mock_chain_greeks = MagicMock()
    mock_chain_greeks.valid_legs.return_value = [MagicMock()]
    mock_chain_greeks.iv_rank = 50.0

    with patch("trader.options_planner._check_cooldown", return_value=None), \
         patch("trader.options_planner._check_max_trades_today", return_value=None), \
         patch("trader.options_planner.get_config") as mock_cfg, \
         patch("trader.options_planner.GreeksService") as MockGS, \
         patch("trader.options_planner.StrikeSelector") as MockSS, \
         patch("trader.options_planner.GreeksGate") as MockGG, \
         patch("trader.options_planner.GreeksLogger"), \
         patch("trader.options_planner.calculate_limit_price", return_value=3.00), \
         patch("trader.options_planner._get_nav", return_value=100_000.0), \
         patch("trader.options_planner._save_plan") as mock_save:

        rc = MagicMock()
        rc.dte_min = 21; rc.dte_max = 45; rc.dte_target = 30; rc.dte_fallback_min = 14
        rc.cooldown_hours = 6; rc.max_trades_per_day = 3
        mock_cfg.return_value.ranking = rc
        mock_cfg.return_value.risk.max_risk_per_trade_pct = 1.0

        MockGS.return_value.fetch_chain_greeks.return_value = mock_chain_greeks
        MockSS.return_value.adjust_delta_for_iv.return_value = MagicMock(iv_environment="elevated")
        MockSS.return_value.select_debit_spread_strikes.return_value = mock_spread
        MockGG.return_value.evaluate.return_value = mock_gate

        mock_save.return_value = MagicMock(status="proposed")
        plan = plan_trade(candidate, client=mock_client)

    call_kwargs = mock_save.call_args.kwargs
    assert call_kwargs["strategy"] == "bear_put_debit_spread"
    assert call_kwargs["bias"] == "bearish"
    # Confirm we asked for "bear" direction
    ss_instance = MockSS.return_value
    ss_instance.select_debit_spread_strikes.assert_called_once()
    args = ss_instance.select_debit_spread_strikes.call_args[0]
    assert args[1] == "bear"


def test_plan_trade_skips_when_gate_rejects():
    expiry = _expiry_str(30)
    mock_client = _make_full_mock_client(expiry)
    candidate = _candidate()

    mock_spread = MagicMock()
    mock_spread.long_strike = 150.0
    mock_spread.short_strike = 155.0
    mock_spread.right = "C"
    mock_spread.expiration = expiry
    mock_spread.strategy_type = "bull_call_debit_spread"
    mock_spread.long_greeks = MagicMock(delta=0.40)
    mock_spread.short_greeks = MagicMock(delta=0.20)

    mock_gate = MagicMock()
    mock_gate.approved = False
    mock_gate.reason = "iv_rank_too_high"
    mock_gate.warnings = []
    mock_gate.checks_failed = ["iv_rank"]
    mock_gate.greeks_summary = {}

    mock_chain_greeks = MagicMock()
    mock_chain_greeks.valid_legs.return_value = [MagicMock()]
    mock_chain_greeks.iv_rank = 95.0

    with patch("trader.options_planner._check_cooldown", return_value=None), \
         patch("trader.options_planner._check_max_trades_today", return_value=None), \
         patch("trader.options_planner.get_config") as mock_cfg, \
         patch("trader.options_planner.GreeksService") as MockGS, \
         patch("trader.options_planner.StrikeSelector") as MockSS, \
         patch("trader.options_planner.GreeksGate") as MockGG, \
         patch("trader.options_planner.GreeksLogger"), \
         patch("trader.options_planner.calculate_limit_price", return_value=2.00), \
         patch("trader.options_planner._get_nav", return_value=50_000.0), \
         patch("trader.options_planner._save_plan") as mock_save:

        rc = MagicMock()
        rc.dte_min = 21; rc.dte_max = 45; rc.dte_target = 30; rc.dte_fallback_min = 14
        rc.cooldown_hours = 6; rc.max_trades_per_day = 3
        mock_cfg.return_value.ranking = rc
        mock_cfg.return_value.risk.max_risk_per_trade_pct = 1.0

        MockGS.return_value.fetch_chain_greeks.return_value = mock_chain_greeks
        MockSS.return_value.adjust_delta_for_iv.return_value = MagicMock(iv_environment="extreme")
        MockSS.return_value.select_debit_spread_strikes.return_value = mock_spread
        MockGG.return_value.evaluate.return_value = mock_gate

        mock_save.return_value = MagicMock(status="skipped")
        plan = plan_trade(candidate, client=mock_client)

    call_kwargs = mock_save.call_args.kwargs
    assert call_kwargs["status"] == "skipped"
    assert "greeks_gate" in call_kwargs["skip_reason"]
