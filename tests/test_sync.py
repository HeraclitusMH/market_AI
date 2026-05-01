from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from common.config import AppConfig
from common.time import utcnow


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    import common.config
    import common.db

    cfg = AppConfig(db={"path": str(tmp_path / "sync_test.db")})
    monkeypatch.setattr(common.config, "_cached", cfg)
    common.db._engine = None
    common.db._SessionLocal = None

    from common.db import create_tables

    create_tables()
    yield


def _contract(symbol: str, sec_type: str):
    return SimpleNamespace(symbol=symbol, localSymbol="", secType=sec_type)


def _ibkr_position(symbol: str, qty: int, sec_type: str = "STK"):
    return SimpleNamespace(
        contract=_contract(symbol, sec_type),
        position=qty,
        avgCost=100.0,
        marketPrice=105.0,
        marketValue=qty * 105.0,
        unrealizedPNL=qty * 5.0,
    )


def _client(*positions):
    return SimpleNamespace(positions=lambda: list(positions))


def _add_order(db, symbol: str, portfolio_id: str, direction: str = "long", status: str = "filled"):
    from common.models import Order

    order = Order(
        intent_id=f"{symbol}_{portfolio_id}_{direction}_{status}_{utcnow().timestamp()}",
        symbol=symbol,
        direction=direction,
        instrument="stock" if portfolio_id == "equity_swing" else "debit_spread",
        portfolio_id=portfolio_id,
        status=status,
        quantity=1,
    )
    db.add(order)
    return order


def _add_trade_management(
    db,
    symbol: str,
    portfolio_id: str,
    instrument_type: str,
    direction: str = "long",
):
    from common.models import TradeManagement

    db.add(TradeManagement(
        symbol=symbol,
        portfolio_id=portfolio_id,
        instrument_type=instrument_type,
        entry_price=100.0,
        entry_date=utcnow(),
        direction=direction,
        quantity=1,
        current_quantity=1,
        initial_stop=90.0,
        current_stop=90.0,
        risk_per_share=10.0,
    ))


def _positions_by_symbol():
    from common.db import get_db
    from common.models import Position

    with get_db() as db:
        return {p.symbol: p for p in db.query(Position).all()}


def test_basic_equity_attribution():
    from common.db import get_db
    from trader.sync import sync_positions

    with get_db() as db:
        _add_order(db, "AAPL", "equity_swing")

    sync_positions(_client(_ibkr_position("AAPL", 100, "STK")))

    assert _positions_by_symbol()["AAPL"].portfolio_id == "equity_swing"


def test_basic_options_attribution_from_trade_management():
    from common.db import get_db
    from trader.sync import sync_positions

    with get_db() as db:
        _add_trade_management(db, "MSFT", "options_swing", "debit_spread")

    sync_positions(_client(_ibkr_position("MSFT", 5, "OPT")))

    assert _positions_by_symbol()["MSFT"].portfolio_id == "options_swing"


def test_unattributed_position_without_matching_order():
    from trader.sync import sync_positions

    sync_positions(_client(_ibkr_position("XYZ", 50, "STK")))

    assert _positions_by_symbol()["XYZ"].portfolio_id == "unattributed"


def test_same_symbol_different_instruments_are_attributed_to_different_bots():
    from common.db import get_db
    from trader.sync import sync_positions

    with get_db() as db:
        _add_trade_management(db, "AAPL", "equity_swing", "equity")
        _add_trade_management(db, "AAPL", "options_swing", "debit_spread")

    sync_positions(_client(
        _ibkr_position("AAPL", 100, "STK"),
        _ibkr_position("AAPL", 3, "OPT"),
    ))

    from common.db import get_db
    from common.models import Position

    with get_db() as db:
        rows = db.query(Position).order_by(Position.instrument).all()
    assert {p.instrument: p.portfolio_id for p in rows} == {
        "option": "options_swing",
        "stock": "equity_swing",
    }


def test_close_order_does_not_attribute_new_position():
    from common.db import get_db
    from trader.sync import sync_positions

    with get_db() as db:
        _add_order(db, "TSLA", "equity_swing", direction="close_long")

    sync_positions(_client(_ibkr_position("TSLA", 10, "STK")))

    assert _positions_by_symbol()["TSLA"].portfolio_id == "unattributed"


def test_equity_count_includes_unattributed_stocks():
    from common.db import get_db
    from common.models import Position
    from bots.equity_swing_bot import _count_equity_positions

    with get_db() as db:
        db.add(Position(symbol="A", quantity=1, instrument="stock", portfolio_id="equity_swing"))
        db.add(Position(symbol="B", quantity=1, instrument="stock", portfolio_id="equity_swing"))
        db.add(Position(symbol="C", quantity=1, instrument="stock", portfolio_id="unattributed"))

    assert _count_equity_positions() == 3


def test_options_count_uses_distinct_symbols_for_spread_legs():
    from common.db import get_db
    from common.models import Position
    from bots.options_swing_bot import _count_options_positions

    with get_db() as db:
        db.add(Position(symbol="AAPL", quantity=1, instrument="option", portfolio_id="options_swing"))
        db.add(Position(symbol="AAPL", quantity=-1, instrument="option", portfolio_id="options_swing"))

    assert _count_options_positions() == 1


def test_zero_quantity_positions_are_skipped():
    from trader.sync import sync_positions

    sync_positions(_client(_ibkr_position("AAPL", 0, "STK")))

    assert _positions_by_symbol() == {}


def test_trade_management_takes_priority_over_order():
    from common.db import get_db
    from trader.sync import sync_positions

    with get_db() as db:
        _add_order(db, "GOOG", "options_swing")
        _add_trade_management(db, "GOOG", "equity_swing", "equity")

    sync_positions(_client(_ibkr_position("GOOG", 25, "STK")))

    assert _positions_by_symbol()["GOOG"].portfolio_id == "equity_swing"


def test_event_logging_on_unattributed_position():
    from common.db import get_db
    from common.models import EventLog
    from trader.sync import sync_positions

    sync_positions(_client(_ibkr_position("XYZ", 50, "STK")))

    with get_db() as db:
        event = (
            db.query(EventLog)
            .filter(EventLog.type == "sync_unattributed_positions")
            .one()
        )
        payload = json.loads(event.payload_json)

    assert event.level == "WARN"
    assert payload["symbols"] == ["XYZ"]
