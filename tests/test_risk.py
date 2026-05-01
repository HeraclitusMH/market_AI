"""Tests for risk engine."""
import os
import sys
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Use in-memory DB for tests
os.environ["MARKET_AI_CONFIG"] = ""


from common.config import load_config, AppConfig
from common.models import Base, BotState, EquitySnapshot, Order, Position
from common.db import get_engine, get_session_factory, create_tables
from trader.strategy import SignalIntent


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Set up a fresh in-memory DB for each test."""
    import common.config
    import common.db

    db_path = str(tmp_path / "test.db")
    cfg = AppConfig(db={"path": db_path})
    monkeypatch.setattr(common.config, "_cached", cfg)

    # Reset engine
    common.db._engine = None
    common.db._SessionLocal = None
    create_tables()

    # Seed bot_state
    from common.db import get_db
    with get_db() as db:
        db.add(BotState(id=1, paused=False, kill_switch=False, approve_mode=True, options_enabled=True))

    yield


def _make_intent(symbol="AAPL", direction="long", max_risk=500.0):
    return SignalIntent(
        symbol=symbol, direction=direction, instrument="debit_spread",
        score=0.7, max_risk_usd=max_risk, explanation="test",
        components={"trend": 0.5}, regime="risk_on",
    )


def test_drawdown_stop():
    from common.db import get_db
    from trader.risk import record_equity_snapshot, check_can_trade

    # Record a peak, then a drop
    record_equity_snapshot(100_000, 100_000, 0, 0)
    record_equity_snapshot(45_000, 45_000, 0, 0)  # 55% drawdown

    intent = _make_intent(max_risk=1000)
    allowed, reason = check_can_trade(intent)
    assert not allowed
    assert "Drawdown" in reason


def test_max_positions_check():
    from common.db import get_db
    from trader.risk import check_can_trade

    # Add equity
    from trader.risk import record_equity_snapshot
    record_equity_snapshot(100_000, 100_000, 0, 0)

    # Add max positions for the options portfolio. Options caps count distinct
    # option/combo symbols, including unattributed option positions.
    with get_db() as db:
        for i in range(5):
            db.add(Position(
                symbol=f"SYM{i}",
                quantity=1,
                instrument="option",
                portfolio_id="options_swing",
            ))

    intent = _make_intent()
    allowed, reason = check_can_trade(intent)
    assert not allowed
    assert "Max positions" in reason


def test_kill_switch_blocks():
    from common.db import get_db
    from trader.risk import check_can_trade, record_equity_snapshot

    record_equity_snapshot(100_000, 100_000, 0, 0)

    with get_db() as db:
        state = db.query(BotState).first()
        state.kill_switch = True

    intent = _make_intent()
    allowed, reason = check_can_trade(intent)
    assert not allowed
    assert "Kill switch" in reason


def test_cash_reservation():
    from common.db import get_db
    from trader.risk import check_can_trade, record_equity_snapshot

    # equity=100k so 5% = 5k allowed per trade, but only 2k cash
    record_equity_snapshot(100_000, 2_000, 0, 0)

    # Risk fits per-trade limit but exceeds available cash
    intent = _make_intent(max_risk=3_000)
    allowed, reason = check_can_trade(intent)
    assert not allowed
    assert "Insufficient cash" in reason


def test_positive_cash_allows():
    from trader.risk import check_can_trade, record_equity_snapshot

    record_equity_snapshot(100_000, 50_000, 0, 0)

    intent = _make_intent(max_risk=2_000)
    allowed, reason = check_can_trade(intent)
    assert allowed
    assert reason == "OK"


def test_paused_blocks():
    from common.db import get_db
    from trader.risk import check_can_trade, record_equity_snapshot

    record_equity_snapshot(100_000, 100_000, 0, 0)

    with get_db() as db:
        state = db.query(BotState).first()
        state.paused = True

    intent = _make_intent()
    allowed, reason = check_can_trade(intent)
    assert not allowed
    assert "paused" in reason
