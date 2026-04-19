"""Test that options_swing and equity_swing bots do not share portfolio state."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from bots.base_bot import BotContext, Candidate, ScoreBreakdown, TradeIntent
from bots.options_swing_bot import OptionsSwingBot
from bots.equity_swing_bot import EquitySwingBot


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_universe_item(symbol: str, sector: str = "Technology", verified: bool = True):
    item = MagicMock()
    item.symbol = symbol
    item.sector = sector
    item.type = "STK"
    item.sources = ["core"]
    item.verified = verified
    item.active = True
    return item


def _make_ranked_symbol(symbol: str, bias: str = "bullish", score: float = 0.6):
    rs = MagicMock()
    rs.symbol = symbol
    rs.sector = "Technology"
    rs.score_total = score
    rs.bias = bias
    rs.eligible = True
    rs.reasons = []
    rs.sources = ["core"]
    return rs


def _make_context(universe=None, ranked=None):
    return BotContext(
        regime="risk_on",
        universe=universe or [],
        ranked=ranked or [],
        client=None,
        dry_run=True,
        approve=True,
        mode="paper",
    )


# ── Bot identity ──────────────────────────────────────────────────────────────


def test_bot_ids_are_distinct():
    assert OptionsSwingBot.bot_id != EquitySwingBot.bot_id
    assert OptionsSwingBot.bot_id == "options_swing"
    assert EquitySwingBot.bot_id == "equity_swing"


def test_instrument_types_are_distinct():
    assert OptionsSwingBot.instrument_type == "options"
    assert EquitySwingBot.instrument_type == "equity"


# ── Options bot only trades options ──────────────────────────────────────────


def test_options_bot_intents_are_options_type():
    bot = OptionsSwingBot()
    universe = [_make_universe_item("AAPL"), _make_universe_item("MSFT")]
    ranked = [_make_ranked_symbol("AAPL"), _make_ranked_symbol("MSFT")]
    ctx = _make_context(universe=universe, ranked=ranked)

    scored = [
        (
            Candidate("AAPL", "Technology", "core", True),
            ScoreBreakdown(0.7, 0.6, 0.8, 0.6, 0.68, "long", ["bullish"], {}),
        ),
        (
            Candidate("MSFT", "Technology", "core", True),
            ScoreBreakdown(0.6, 0.5, 0.7, 0.5, 0.58, "long", ["bullish"], {}),
        ),
    ]

    intents = bot.select_trades(scored, ctx)
    for intent in intents:
        assert intent.instrument_type == "options", (
            f"{intent.symbol} should be options, got {intent.instrument_type}"
        )
        assert intent.bot_id == "options_swing"


# ── Equity bot only trades equity ────────────────────────────────────────────


def test_equity_bot_intents_are_equity_type():
    from bots.equity_swing_bot import _size_equity_trade
    from unittest.mock import patch

    candidate = Candidate("AAPL", "Technology", "core", True)
    breakdown = ScoreBreakdown(
        trend=0.7, momentum=0.6, volatility=0.8, sentiment=0.6,
        final_score=0.68, direction="long", explanations=["bullish"],
        components={}, atr14=3.5, last_price=180.0,
    )

    from common.config import get_config
    cfg = get_config()

    intent = _size_equity_trade(
        candidate=candidate,
        breakdown=breakdown,
        entry_price=180.0,
        atr_val=3.5,
        nav=100_000.0,
        available_cash=50_000.0,
        sector_values={},
        equity_cfg=cfg.bots.equity_swing,
        regime="risk_on",
        bot_id="equity_swing",
    )

    assert intent is not None
    assert intent.instrument_type == "equity"
    assert intent.bot_id == "equity_swing"
    assert intent.quantity is not None and intent.quantity >= 1


# ── DB isolation: equity positions counted separately ────────────────────────


def test_equity_positions_counted_per_portfolio(tmp_path, monkeypatch):
    """Equity bot's position count query filters by portfolio_id='equity_swing'."""
    import common.config
    import common.db
    from common.config import AppConfig

    cfg = AppConfig(db={"path": str(tmp_path / "isolation_test.db")})
    monkeypatch.setattr(common.config, "_cached", cfg)
    monkeypatch.setattr(common.db, "_engine", None)
    monkeypatch.setattr(common.db, "_SessionLocal", None)

    from common.db import create_tables, get_db
    from common.models import Position
    from common.time import utcnow
    create_tables()

    with get_db() as db:
        # Add one equity_swing position and one options_swing position
        db.add(Position(
            symbol="AAPL", quantity=10, avg_cost=150.0,
            market_price=155.0, market_value=1550.0,
            instrument="stock", portfolio_id="equity_swing",
        ))
        db.add(Position(
            symbol="SPY", quantity=1, avg_cost=450.0,
            market_price=455.0, market_value=455.0,
            instrument="debit_spread", portfolio_id="options_swing",
        ))

    from bots.equity_swing_bot import _count_equity_positions
    count = _count_equity_positions()
    assert count == 1, f"Expected 1 equity position, got {count}"


# ── SPY excluded from options bot ────────────────────────────────────────────


def test_options_bot_excludes_spy():
    bot = OptionsSwingBot()
    universe = [
        _make_universe_item("SPY"),
        _make_universe_item("AAPL"),
    ]
    ctx = _make_context(universe=universe)
    candidates = bot.build_candidates(ctx)
    symbols = [c.symbol for c in candidates]
    assert "SPY" not in symbols
    assert "AAPL" in symbols


# ── Unverified tickers blocked ────────────────────────────────────────────────


def test_unverified_tickers_excluded():
    bot_opts = OptionsSwingBot()
    bot_eq = EquitySwingBot()
    universe = [
        _make_universe_item("FAKE", verified=False),
        _make_universe_item("AAPL", verified=True),
    ]
    ctx = _make_context(universe=universe)

    for bot in (bot_opts, bot_eq):
        candidates = bot.build_candidates(ctx)
        symbols = [c.symbol for c in candidates]
        assert "FAKE" not in symbols, f"{bot.bot_id} should exclude unverified FAKE"
        assert "AAPL" in symbols


# ── Disabled bot returns empty run ───────────────────────────────────────────


def test_disabled_options_bot_returns_empty():
    from common.config import get_config, load_config
    cfg = get_config()
    original = cfg.bots.options_swing.enabled
    cfg.bots.options_swing.enabled = False
    try:
        bot = OptionsSwingBot()
        result = bot.run(dry_run=True)
        assert result.executed == 0
        assert "bot_disabled" in result.skip_reasons
    finally:
        cfg.bots.options_swing.enabled = original


def test_disabled_equity_bot_returns_empty():
    from common.config import get_config
    cfg = get_config()
    original = cfg.bots.equity_swing.enabled
    cfg.bots.equity_swing.enabled = False
    try:
        bot = EquitySwingBot()
        result = bot.run(dry_run=True)
        assert result.executed == 0
        assert "bot_disabled" in result.skip_reasons
    finally:
        cfg.bots.equity_swing.enabled = original
