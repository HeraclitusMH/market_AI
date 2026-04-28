from __future__ import annotations

import pytest

from common.config import AppConfig
from common.models import Base
from trader.fundamental_scorer import FundamentalScorer
from trader.fundamentals_refresh import refresh_fundamentals


SAMPLE_INFO = {
    "trailingPE": 20,
    "priceToBook": 2.5,
    "returnOnEquity": 0.18,
    "currentRatio": 2,
    "debtToEquity": 50,
}


@pytest.fixture(autouse=True)
def _clear_memory_cache():
    FundamentalScorer._shared_cache.clear()
    yield
    FundamentalScorer._shared_cache.clear()


@pytest.fixture
def _db_session(monkeypatch):
    import common.db as db_mod

    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    engine = db_mod.get_engine()
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)


@pytest.fixture
def _fundamentals_cfg(monkeypatch):
    cfg = AppConfig(db={"path": ":memory:"}, fundamentals={"enabled": True})
    import common.config

    monkeypatch.setattr(common.config, "_cached", cfg)
    return cfg


def test_refresh_fundamentals_writes_to_db_for_each_symbol(_db_session, _fundamentals_cfg, monkeypatch):
    monkeypatch.setattr(
        FundamentalScorer,
        "_fetch_yfinance_info",
        lambda self, symbol: SAMPLE_INFO,
    )

    result = refresh_fundamentals(symbols=["AAPL", "MSFT"], force=True)

    assert result["refreshed"] == 2
    assert result["missing"] == 0
    assert result["errors"] == []

    from common.db import get_db
    from common.models import FundamentalSnapshot

    with get_db() as db:
        rows = {r.symbol: r for r in db.query(FundamentalSnapshot).all()}
    assert set(rows) == {"AAPL", "MSFT"}
    assert all(r.status == "ok" for r in rows.values())


def test_refresh_fundamentals_continues_after_error(_db_session, _fundamentals_cfg, monkeypatch):
    def _flaky(self, symbol):
        if symbol == "BAD":
            raise RuntimeError("yfinance blew up")
        return SAMPLE_INFO

    monkeypatch.setattr(FundamentalScorer, "_fetch_yfinance_info", _flaky)

    result = refresh_fundamentals(symbols=["AAPL", "BAD", "MSFT"], force=True)

    # The scorer's outer try/except catches yfinance exceptions and produces a
    # neutral result (source="none"); refresh_fundamentals counts those as
    # missing rather than as hard errors.
    assert result["refreshed"] == 2
    assert result["missing"] == 1
    assert result["errors"] == []
