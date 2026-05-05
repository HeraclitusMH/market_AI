"""Tests for /api/v1/scoring/* endpoints."""
from __future__ import annotations

import time

import pytest

from common.config import AppConfig
from common.time import utcnow


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    import common.config
    import common.db

    cfg = AppConfig(db={"path": str(tmp_path / "scoring_test.db")})
    monkeypatch.setattr(common.config, "_cached", cfg)
    common.db._engine = None
    common.db._SessionLocal = None

    from common.db import create_tables
    create_tables()
    yield


def _next_session():
    """Get a fresh session — caller is responsible for closing it."""
    from common.db import _SessionLocal, get_engine
    if _SessionLocal is None:
        from common.db import create_tables
        create_tables()
    from common.db import _SessionLocal as Sess
    assert Sess is not None
    return Sess()


def test_scoring_weights_returns_both_profiles_and_active():
    from api.v1.scoring import get_scoring_weights

    db = _next_session()
    try:
        result = get_scoring_weights(db=db)
    finally:
        db.close()

    assert "aggressive_swing" in result.profiles
    assert "defensive_swing" in result.profiles

    aggressive = result.profiles["aggressive_swing"]
    assert aggressive.technical == pytest.approx(0.30)
    assert aggressive.risk_penalty == pytest.approx(0.15)

    # No regime snapshot yet → defaults to risk_reduced → defensive_swing
    assert result.active_profile == "defensive_swing"
    assert result.regime_level == "risk_reduced"
    assert result.active_weights.technical == pytest.approx(0.25)


def test_scoring_weights_uses_active_regime_for_profile_selection():
    from api.v1.scoring import get_scoring_weights
    from common.db import get_db
    from common.models import RegimeSnapshot

    with get_db() as db:
        db.add(RegimeSnapshot(level="risk_on", composite_score=72.0))

    db = _next_session()
    try:
        result = get_scoring_weights(db=db)
    finally:
        db.close()

    assert result.regime_level == "risk_on"
    assert result.active_profile == "aggressive_swing"


def test_scoring_docs_returns_six_factors_with_live_weights():
    from api.v1.scoring import FACTOR_ORDER, get_scoring_docs

    db = _next_session()
    try:
        result = get_scoring_docs(db=db)
    finally:
        db.close()

    keys = [p.key for p in result.parameters]
    assert keys == FACTOR_ORDER
    assert len(result.parameters) == 6

    by_key = {p.key: p for p in result.parameters}
    # Defensive profile is active by default → technical weight 0.25
    assert by_key["technical"].weight == pytest.approx(0.25)
    assert by_key["risk_penalty"].weight == pytest.approx(0.20)
    assert by_key["risk_penalty"].inverted is True
    assert by_key["technical"].inverted is False

    # All cards must contain a non-empty description and at least one band.
    for p in result.parameters:
        assert p.what
        assert p.how
        assert p.bands
        assert p.refresh_via in {"rankings", "sentiment", "fundamentals"}


def test_refresh_history_returns_recent_log_entries_descending():
    from api.v1._refresh_log import log_refresh
    from api.v1.scoring import get_refresh_history

    log_refresh("rankings", "success", 4500, "ranked=120")
    time.sleep(0.001)
    log_refresh("sentiment", "success", 1200, "snapshots=5")
    time.sleep(0.001)
    log_refresh("fundamentals", "error", 800, "yfinance timeout")

    db = _next_session()
    try:
        events = get_refresh_history(limit=20, action=None, db=db)
    finally:
        db.close()

    assert len(events) == 3
    assert events[0].action == "fundamentals"
    assert events[0].status == "error"
    assert events[1].action == "sentiment"
    assert events[2].action == "rankings"
    assert events[2].duration_ms == 4500
    assert events[2].message == "ranked=120"


def test_refresh_history_filters_by_action():
    from api.v1._refresh_log import log_refresh
    from api.v1.scoring import get_refresh_history

    log_refresh("rankings", "success", 100, "")
    log_refresh("sentiment", "success", 50, "")
    log_refresh("rankings", "error", 200, "boom")

    db = _next_session()
    try:
        rankings_events = get_refresh_history(limit=20, action="rankings", db=db)
        sentiment_events = get_refresh_history(limit=20, action="sentiment", db=db)
    finally:
        db.close()

    assert len(rankings_events) == 2
    assert all(e.action == "rankings" for e in rankings_events)
    assert len(sentiment_events) == 1


def test_log_refresh_truncates_long_messages():
    from api.v1._refresh_log import log_refresh
    from common.db import get_db
    from common.models import RefreshLog

    long_msg = "x" * 5000
    log_refresh("rankings", "error", 0, long_msg)

    with get_db() as db:
        row = db.query(RefreshLog).order_by(RefreshLog.id.desc()).first()
        assert row is not None
        assert len(row.message) <= 1000


# Silence unused import warning if utcnow is stripped by a future linter pass
_ = utcnow
