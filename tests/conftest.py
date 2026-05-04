"""Shared pytest fixtures.

Ensures a valid default AppConfig is always available, even when
test_risk.py sets MARKET_AI_CONFIG="" at module-import time.
"""
from __future__ import annotations

import os

import pytest

import common.config
from common.config import AppConfig


@pytest.fixture(autouse=True)
def _ensure_valid_config(monkeypatch):
    """If the config cache is None (e.g. after monkeypatch teardown from
    another test), seed it with a fresh default AppConfig so that tests
    which don't explicitly patch get_config() still work correctly.

    Tests that call ``monkeypatch.setattr(common.config, "_cached", ...)``
    override this fixture's value, which is fine — monkeypatch ordering
    applies the last setattr for the same attribute.
    """
    # Remove the poisoned env var set by test_risk at module level.
    monkeypatch.delenv("MARKET_AI_CONFIG", raising=False)
    # Keep tests on their explicit SQLite configs instead of the running
    # compose Postgres service.
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Provide a default in-memory config if nothing else has set the cache.
    if common.config._cached is None:
        monkeypatch.setattr(
            common.config,
            "_cached",
            AppConfig(db={"path": ":memory:"}),
        )


@pytest.fixture(autouse=True)
def _isolate_fundamental_caches():
    """Reset the FundamentalScorer in-memory + DB caches between tests.

    The scorer now persists to ``fundamental_snapshots`` in the live SQLite
    DB; without this, a test that hits the real DB pollutes later tests by
    serving cached rows where they expect a fresh yfinance call.
    """
    from trader.fundamental_scorer import FundamentalScorer

    FundamentalScorer._shared_cache.clear()
    try:
        from common.db import get_db
        from common.models import FundamentalSnapshot

        with get_db() as db:
            db.query(FundamentalSnapshot).delete()
    except Exception:
        pass
    yield
    FundamentalScorer._shared_cache.clear()
