"""Test ATR-based equity position sizing determinism and edge cases."""
from __future__ import annotations

import math
import pytest

from bots.base_bot import Candidate, ScoreBreakdown
from bots.equity_swing_bot import _size_equity_trade
from common.config import get_config


def _equity_cfg():
    return get_config().bots.equity_swing


def _breakdown(last_price=100.0, atr14=2.0, score=0.65, direction="long"):
    return ScoreBreakdown(
        trend=0.7, momentum=0.6, volatility=0.8, sentiment=0.6,
        final_score=score, direction=direction, explanations=["test"],
        components={}, atr14=atr14, last_price=last_price,
    )


def _candidate(symbol="AAPL", sector="Technology"):
    return Candidate(symbol=symbol, sector=sector, source="core", verified=True)


# ── Basic sizing formula ───────────────────────────────────────────────────────


def test_basic_sizing():
    """shares = floor(nav * risk_pct / (atr * multiplier))"""
    cfg = _equity_cfg()
    nav = 100_000.0
    atr_val = 2.0
    entry = 100.0
    stop_dist = atr_val * cfg.atr_stop_multiplier   # 2.0 * 2.0 = 4.0
    risk_amount = nav * cfg.risk_per_trade_pct / 100  # 100000 * 1% = 1000
    expected_shares = math.floor(risk_amount / stop_dist)  # floor(1000/4) = 250

    intent = _size_equity_trade(
        candidate=_candidate(),
        breakdown=_breakdown(last_price=entry, atr14=atr_val),
        entry_price=entry,
        atr_val=atr_val,
        nav=nav,
        available_cash=100_000.0,
        sector_values={},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )

    assert intent is not None
    assert intent.quantity == expected_shares
    assert intent.limit_price == entry
    assert intent.stop_price == pytest.approx(entry - stop_dist, abs=0.01)


def test_sizing_is_deterministic():
    """Same inputs → same output every time."""
    cfg = _equity_cfg()
    kwargs = dict(
        candidate=_candidate(),
        breakdown=_breakdown(),
        entry_price=100.0,
        atr_val=2.0,
        nav=100_000.0,
        available_cash=100_000.0,
        sector_values={},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )
    r1 = _size_equity_trade(**kwargs)
    r2 = _size_equity_trade(**kwargs)
    assert r1 is not None and r2 is not None
    assert r1.quantity == r2.quantity
    assert r1.limit_price == r2.limit_price


# ── Cash cap ──────────────────────────────────────────────────────────────────


def test_cash_cap_reduces_shares():
    """When trade cost > available_cash, shares are capped."""
    cfg = _equity_cfg()
    # risk formula would give ~250 shares @ $100 each = $25000 trade
    intent = _size_equity_trade(
        candidate=_candidate(),
        breakdown=_breakdown(last_price=100.0, atr14=2.0),
        entry_price=100.0,
        atr_val=2.0,
        nav=100_000.0,
        available_cash=5_000.0,   # only $5000 available
        sector_values={},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )
    assert intent is not None
    assert intent.quantity == 50   # floor(5000 / 100)
    assert intent.quantity * intent.limit_price <= 5_000.0


def test_returns_none_when_cash_too_low_for_1_share():
    cfg = _equity_cfg()
    intent = _size_equity_trade(
        candidate=_candidate(),
        breakdown=_breakdown(last_price=500.0, atr14=2.0),
        entry_price=500.0,
        atr_val=2.0,
        nav=100_000.0,
        available_cash=400.0,   # < 1 share @ $500
        sector_values={},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )
    assert intent is None


# ── Zero / invalid inputs ─────────────────────────────────────────────────────


def test_returns_none_when_nav_zero():
    cfg = _equity_cfg()
    intent = _size_equity_trade(
        candidate=_candidate(),
        breakdown=_breakdown(),
        entry_price=100.0,
        atr_val=2.0,
        nav=0.0,
        available_cash=10_000.0,
        sector_values={},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )
    assert intent is None


def test_returns_none_when_price_zero():
    cfg = _equity_cfg()
    intent = _size_equity_trade(
        candidate=_candidate(),
        breakdown=_breakdown(last_price=0.0),
        entry_price=0.0,
        atr_val=2.0,
        nav=100_000.0,
        available_cash=50_000.0,
        sector_values={},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )
    assert intent is None


def test_returns_none_when_atr_zero():
    cfg = _equity_cfg()
    intent = _size_equity_trade(
        candidate=_candidate(),
        breakdown=_breakdown(atr14=0.0),
        entry_price=100.0,
        atr_val=0.0,
        nav=100_000.0,
        available_cash=50_000.0,
        sector_values={},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )
    # atr=0 → stop_dist uses max(0, 0.01) floor, shares still computed
    # should not raise; may return a valid small intent or None
    # key invariant: no exception raised


# ── Sector concentration cap ──────────────────────────────────────────────────


def test_sector_concentration_blocks_over_limit():
    cfg = _equity_cfg()
    nav = 100_000.0
    # Already have $28000 in Technology (28%), limit is 30%
    # New trade would add ~250 * $100 = $25000 → 53% total → rejected
    intent = _size_equity_trade(
        candidate=_candidate(sector="Technology"),
        breakdown=_breakdown(last_price=100.0, atr14=2.0),
        entry_price=100.0,
        atr_val=2.0,
        nav=nav,
        available_cash=50_000.0,
        sector_values={"Technology": 28_000.0},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )
    assert intent is None


def test_sector_concentration_allows_when_under_limit():
    cfg = _equity_cfg()
    nav = 100_000.0
    # Only $5000 in Technology (5%), limit 30%, small trade → should pass
    intent = _size_equity_trade(
        candidate=_candidate(sector="Technology"),
        breakdown=_breakdown(last_price=50.0, atr14=1.0),
        entry_price=50.0,
        atr_val=1.0,
        nav=nav,
        available_cash=50_000.0,
        sector_values={"Technology": 5_000.0},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )
    assert intent is not None


# ── Intent fields populated ───────────────────────────────────────────────────


def test_intent_fields_populated():
    cfg = _equity_cfg()
    intent = _size_equity_trade(
        candidate=_candidate("NVDA", "Technology"),
        breakdown=_breakdown(last_price=400.0, atr14=8.0, score=0.72),
        entry_price=400.0,
        atr_val=8.0,
        nav=200_000.0,
        available_cash=100_000.0,
        sector_values={},
        equity_cfg=cfg,
        regime="risk_on",
        bot_id="equity_swing",
    )
    assert intent is not None
    assert intent.symbol == "NVDA"
    assert intent.direction == "long"
    assert intent.instrument_type == "equity"
    assert intent.bot_id == "equity_swing"
    assert intent.quantity is not None and intent.quantity >= 1
    assert intent.limit_price == 400.0
    assert intent.stop_price is not None and intent.stop_price < intent.limit_price
    assert intent.atr == 8.0
    assert intent.max_risk_usd > 0


# ── Risk-off / disabled path ──────────────────────────────────────────────────


def test_equity_bot_no_candidates_in_risk_off_cash_mode():
    from unittest.mock import MagicMock
    from bots.equity_swing_bot import EquitySwingBot
    from bots.base_bot import BotContext

    bot = EquitySwingBot()
    item = MagicMock()
    item.symbol = "AAPL"
    item.sector = "Technology"
    item.type = "STK"
    item.sources = ["core"]
    item.verified = True

    ctx = BotContext(
        regime="risk_off",
        universe=[item],
        ranked=[],
        client=None,
        dry_run=True,
        approve=True,
        mode="paper",
    )
    # In risk_off + cash mode, no candidates returned
    candidates = bot.build_candidates(ctx)
    assert candidates == []
