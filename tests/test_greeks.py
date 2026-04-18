"""Tests for the Greeks analysis layer (no IBKR connection required)."""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.greeks import (
    GreeksSnapshot, OptionChainGreeks, _sanitize_price,
    StrikeSelectionCriteria, StrikeSelector, SpreadSelection, calculate_limit_price,
    GreeksGate,
)


# ── helpers ────────────────────────────────────────────────────

def _snap(
    strike: float,
    right: str,
    delta: float,
    *,
    iv: float = 0.25,
    theta: float = -0.05,
    gamma: float = 0.01,
    vega: float = 0.10,
    bid: float = 1.00,
    ask: float = 1.10,
    open_interest: int = 500,
    underlying: float = 100.0,
    expiration: str = "20260515",
) -> GreeksSnapshot:
    return GreeksSnapshot(
        symbol="TEST",
        expiration=expiration,
        strike=strike,
        right=right,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        implied_vol=iv,
        bid=bid,
        ask=ask,
        last=bid + (ask - bid) / 2,
        mid=(bid + ask) / 2,
        open_interest=open_interest,
        underlying_price=underlying,
        data_quality="live",
    )


def _call_chain(underlying: float = 100.0, expiration: str = "20260515") -> OptionChainGreeks:
    """Chain with a realistic delta curve for calls and puts around 100."""
    chain = OptionChainGreeks(
        symbol="TEST",
        expiration=expiration,
        underlying_price=underlying,
        iv_rank=35.0,
    )
    # Calls: ITM high delta → OTM low delta as strike climbs
    calls_data = [
        (90.0, 0.85, 10.50, 10.70),
        (95.0, 0.65, 6.10, 6.30),
        (100.0, 0.50, 3.20, 3.40),
        (102.5, 0.40, 2.05, 2.20),
        (105.0, 0.30, 1.15, 1.30),
        (107.5, 0.22, 0.70, 0.85),
        (110.0, 0.15, 0.35, 0.50),
        (115.0, 0.08, 0.15, 0.25),
    ]
    for strike, delta, bid, ask in calls_data:
        chain.calls.append(_snap(strike, "C", delta, bid=bid, ask=ask,
                                 underlying=underlying, expiration=expiration))
    # Puts: high |delta| ITM (high strike) → low |delta| OTM (low strike)
    puts_data = [
        (110.0, -0.85, 10.50, 10.70),
        (105.0, -0.65, 6.10, 6.30),
        (100.0, -0.50, 3.20, 3.40),
        (97.5, -0.40, 2.05, 2.20),
        (95.0, -0.30, 1.15, 1.30),
        (92.5, -0.22, 0.70, 0.85),
        (90.0, -0.15, 0.35, 0.50),
        (85.0, -0.08, 0.15, 0.25),
    ]
    for strike, delta, bid, ask in puts_data:
        chain.puts.append(_snap(strike, "P", delta, bid=bid, ask=ask,
                                underlying=underlying, expiration=expiration))
    return chain


# ── GreeksSnapshot ─────────────────────────────────────────────

def test_greeks_snapshot_validity():
    valid = _snap(100.0, "C", 0.5)
    assert valid.is_valid is True

    missing_delta = _snap(100.0, "C", 0.5)
    missing_delta.delta = None
    assert missing_delta.is_valid is False

    missing_iv = _snap(100.0, "C", 0.5)
    missing_iv.implied_vol = None
    assert missing_iv.is_valid is False


def test_abs_delta_handles_puts():
    put = _snap(100.0, "P", -0.45)
    assert put.abs_delta == pytest.approx(0.45)


def test_moneyness_bands():
    assert _snap(100.0, "C", 0.60).moneyness == "ITM"
    assert _snap(100.0, "C", 0.50).moneyness == "ATM"
    assert _snap(100.0, "C", 0.30).moneyness == "OTM"


def test_sanitize_price_rejects_negative_one():
    assert _sanitize_price(-1.0) is None
    assert _sanitize_price(0) is None
    assert _sanitize_price(None) is None
    assert _sanitize_price(1.25) == 1.25


# ── StrikeSelector ─────────────────────────────────────────────

def test_delta_strike_selection_bull_debit():
    selector = StrikeSelector(greeks_service=None)
    chain = _call_chain()
    criteria = StrikeSelectionCriteria(
        long_delta_target=0.40,
        long_delta_min=0.35,
        long_delta_max=0.45,
        short_delta_target=0.20,
        short_delta_min=0.15,
        short_delta_max=0.30,
        preferred_spread_width=5.0,
        min_spread_width=2.5,
        max_spread_width=10.0,
        min_roc=0.10,
    )
    spread = selector.select_debit_spread_strikes(chain, "bull", criteria)
    assert spread is not None
    assert spread.long_strike == 102.5
    # short leg should be further OTM (higher strike) within width window
    assert spread.short_strike > spread.long_strike
    assert spread.estimated_debit is not None and spread.estimated_debit > 0
    assert spread.right == "C"


def test_delta_strike_selection_bear_debit_uses_puts():
    selector = StrikeSelector(greeks_service=None)
    chain = _call_chain()
    criteria = StrikeSelectionCriteria(
        long_delta_target=0.40,
        long_delta_min=0.35,
        long_delta_max=0.45,
        preferred_spread_width=5.0,
        min_spread_width=2.5,
        max_spread_width=10.0,
        min_roc=0.10,
    )
    spread = selector.select_debit_spread_strikes(chain, "bear", criteria)
    assert spread is not None
    assert spread.right == "P"
    # For bear put: long is higher strike put, short is lower
    assert spread.long_strike == 97.5
    assert spread.short_strike < spread.long_strike


def test_no_matching_strikes_returns_none():
    selector = StrikeSelector(greeks_service=None)
    chain = _call_chain()
    impossible = StrikeSelectionCriteria(
        long_delta_target=0.99,
        long_delta_min=0.95,
        long_delta_max=1.0,
    )
    # Even with fallback widening, nothing will match deltas ≥ 0.95
    assert selector.select_debit_spread_strikes(chain, "bull", impossible) is None


def test_credit_spread_selection_bull_put():
    selector = StrikeSelector(greeks_service=None)
    chain = _call_chain()
    criteria = StrikeSelectionCriteria(
        short_delta_target=0.20,
        short_delta_min=0.15,
        short_delta_max=0.30,
        preferred_spread_width=5.0,
        min_spread_width=2.5,
        max_spread_width=10.0,
        min_roc=0.05,
        max_bid_ask_spread_pct=0.50,
    )
    spread = selector.select_credit_spread_strikes(chain, "bull", criteria)
    assert spread is not None
    assert spread.right == "P"
    # bull put: short = higher strike put, long = lower strike put
    assert spread.short_strike > spread.long_strike
    assert spread.estimated_credit is not None and spread.estimated_credit > 0


def test_liquidity_filter_rejects_wide_spreads():
    selector = StrikeSelector(greeks_service=None)
    chain = _call_chain()
    # Make every put wildly wide
    for p in chain.puts:
        p.bid = 0.10
        p.ask = 5.00
    criteria = StrikeSelectionCriteria(max_bid_ask_spread_pct=0.30)
    spread = selector.select_debit_spread_strikes(chain, "bear", criteria)
    assert spread is None


# ── IV rank adjustment ─────────────────────────────────────────

def test_iv_adjustment_low_regime():
    selector = StrikeSelector(greeks_service=None)
    base = StrikeSelectionCriteria()
    adjusted = selector.adjust_delta_for_iv(base, iv_rank=10.0)
    assert adjusted.iv_environment == "low_iv_environment"
    # low IV → long delta moves closer to ATM
    assert adjusted.long_delta_target > base.long_delta_target


def test_iv_adjustment_moderate_regime():
    selector = StrikeSelector(greeks_service=None)
    base = StrikeSelectionCriteria()
    adjusted = selector.adjust_delta_for_iv(base, iv_rank=30.0)
    assert adjusted.iv_environment == "moderate_iv_environment"


def test_iv_adjustment_elevated_regime():
    selector = StrikeSelector(greeks_service=None)
    base = StrikeSelectionCriteria()
    adjusted = selector.adjust_delta_for_iv(base, iv_rank=50.0)
    assert adjusted.iv_environment == "elevated_iv_environment"
    # elevated IV → further-OTM short leg
    assert adjusted.short_delta_target < base.short_delta_target


def test_iv_adjustment_extreme_regime():
    selector = StrikeSelector(greeks_service=None)
    base = StrikeSelectionCriteria()
    adjusted = selector.adjust_delta_for_iv(base, iv_rank=80.0)
    assert adjusted.iv_environment == "extreme_iv_environment"


def test_iv_adjustment_unknown_when_none():
    selector = StrikeSelector(greeks_service=None)
    base = StrikeSelectionCriteria()
    adjusted = selector.adjust_delta_for_iv(base, iv_rank=None)
    assert adjusted.iv_environment == "unknown"


def test_iv_adjustment_does_not_mutate_input():
    selector = StrikeSelector(greeks_service=None)
    base = StrikeSelectionCriteria()
    original_target = base.long_delta_target
    selector.adjust_delta_for_iv(base, iv_rank=10.0)
    assert base.long_delta_target == original_target
    assert base.iv_environment == "unknown"


# ── Limit price calculation ────────────────────────────────────

def test_limit_price_for_debit_spread():
    s = SpreadSelection(
        long_strike=100, short_strike=105, expiration="20260515", right="C",
        long_delta=0.4, short_delta=0.2, long_iv=0.25, short_iv=0.24,
        net_delta=0.2, net_theta=-0.03, net_vega=0.05, net_gamma=0.01,
        long_mid=3.30, short_mid=1.25,
        long_bid=3.20, long_ask=3.40, short_bid=1.15, short_ask=1.35,
        spread_width=5.0, estimated_debit=2.05, max_profit=2.95, max_loss=2.05,
        return_on_capital=1.44, breakeven=102.05, strategy_type="debit", direction="bull",
    )
    assert calculate_limit_price(s) == 2.05


def test_limit_price_for_credit_spread():
    s = SpreadSelection(
        long_strike=90, short_strike=95, expiration="20260515", right="P",
        long_delta=-0.15, short_delta=-0.20, long_iv=0.25, short_iv=0.24,
        net_delta=0.05, net_theta=0.03, net_vega=-0.05, net_gamma=-0.01,
        long_mid=0.40, short_mid=1.20,
        long_bid=0.35, long_ask=0.45, short_bid=1.10, short_ask=1.30,
        spread_width=5.0, estimated_credit=0.80, max_profit=0.80, max_loss=4.20,
        return_on_capital=0.19, breakeven=94.20, strategy_type="credit", direction="bull",
    )
    assert calculate_limit_price(s) == 0.80


# ── GreeksGate ─────────────────────────────────────────────────

def _future_expiration(days: int = 30) -> str:
    return (datetime.utcnow().date() + timedelta(days=days)).strftime("%Y%m%d")


def _good_debit_spread(expiration: str = None) -> SpreadSelection:
    return SpreadSelection(
        long_strike=100, short_strike=105,
        expiration=expiration or _future_expiration(30),
        right="C",
        long_delta=0.40, short_delta=0.20, long_iv=0.25, short_iv=0.24,
        net_delta=0.20, net_theta=-0.03, net_vega=0.10, net_gamma=0.02,
        long_mid=3.30, short_mid=1.25,
        long_bid=3.20, long_ask=3.40, short_bid=1.15, short_ask=1.35,
        spread_width=5.0, estimated_debit=2.05, max_profit=2.95, max_loss=2.05,
        return_on_capital=1.44, breakeven=102.05,
        strategy_type="debit", direction="bull",
        iv_rank=35.0, underlying_price=100.0, buffer_pct=0.05,
    )


def test_gate_all_pass_debit():
    chain = _call_chain()
    gate = GreeksGate()
    result = gate.evaluate(_good_debit_spread(), chain, "debit")
    assert result.approved, f"expected approval, got: {result.checks_failed}"
    assert result.checks_failed == []


def test_gate_iv_rank_too_high_for_debit():
    chain = _call_chain()
    spread = _good_debit_spread()
    spread.iv_rank = 80.0  # exceeds max for debit
    result = GreeksGate().evaluate(spread, chain, "debit")
    assert not result.approved
    assert any("IV_RANK_CHECK" in c for c in result.checks_failed)


def test_gate_iv_rank_too_low_for_credit():
    chain = _call_chain()
    spread = _good_debit_spread()
    spread.strategy_type = "credit"
    spread.estimated_credit = 0.50
    spread.net_theta = 0.05
    spread.iv_rank = 10.0
    result = GreeksGate().evaluate(spread, chain, "credit")
    assert not result.approved
    assert any("IV_RANK_CHECK" in c for c in result.checks_failed)


def test_gate_rejects_short_leg_delta_out_of_range():
    chain = _call_chain()
    spread = _good_debit_spread()
    spread.short_delta = 0.60  # too deep
    result = GreeksGate().evaluate(spread, chain, "debit")
    assert not result.approved
    assert any("DELTA_RANGE_CHECK" in c for c in result.checks_failed)


def test_gate_gamma_near_expiry_blocks():
    chain = _call_chain()
    spread = _good_debit_spread(expiration=_future_expiration(3))
    spread.net_gamma = 0.50  # huge gamma near expiry
    result = GreeksGate().evaluate(spread, chain, "debit")
    assert not result.approved
    assert any("GAMMA_NEAR_EXPIRY_CHECK" in c for c in result.checks_failed)


def test_gate_liquidity_rejection():
    chain = _call_chain()
    spread = _good_debit_spread()
    spread.long_bid = 0.10
    spread.long_ask = 5.00
    result = GreeksGate().evaluate(spread, chain, "debit")
    assert not result.approved
    assert any("LIQUIDITY_CHECK" in c for c in result.checks_failed)


def test_gate_pricing_roc_rejection():
    chain = _call_chain()
    spread = _good_debit_spread()
    spread.return_on_capital = 0.05
    result = GreeksGate().evaluate(spread, chain, "debit")
    assert not result.approved
    assert any("PRICING_CHECK" in c for c in result.checks_failed)


def test_gate_buffer_rejection():
    chain = _call_chain()
    spread = _good_debit_spread()
    spread.buffer_pct = 0.01
    result = GreeksGate().evaluate(spread, chain, "debit")
    assert not result.approved
    assert any("BUFFER_CHECK" in c for c in result.checks_failed)


def test_gate_missing_iv_rank_warns_but_passes():
    chain = _call_chain()
    spread = _good_debit_spread()
    spread.iv_rank = None
    result = GreeksGate().evaluate(spread, chain, "debit")
    # should still pass — IV rank missing is a warning, not a failure
    assert result.approved
    assert any("IV_RANK_CHECK" in w for w in result.warnings)
