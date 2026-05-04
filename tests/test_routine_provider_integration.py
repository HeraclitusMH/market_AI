from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from common.config import AppConfig


@pytest.fixture
def routine_db(tmp_path, monkeypatch):
    import common.config
    import common.db
    from common.db import create_tables

    monkeypatch.delenv("DATABASE_URL", raising=False)
    output_path = tmp_path / "sentiment_output.json"
    output_path.write_text(json.dumps({
        "schema_version": 1,
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "run_id": "run_integration",
        "articles_analyzed": 3,
        "articles_skipped_dedup": 1,
        "total_articles_fetched": 4,
        "market": {"score": 0.2, "summary": "Market mildly positive."},
        "sectors": {"Technology": {"score": 0.4, "summary": "Tech positive."}},
        "tickers": {"NVDA": {"score": 0.8, "summary": "NVDA positive."}},
        "sources": [{"url": "https://x/1", "title": "Nvidia rises", "feed": "feed"}],
    }), encoding="utf-8")

    cfg = AppConfig(
        db={"path": str(tmp_path / "test.db")},
        sentiment={
            "provider": "claude_routine",
            "routine": {"source_type": "local", "local_path": str(output_path)},
        },
    )
    monkeypatch.setattr(common.config, "_cached", cfg)
    common.db._engine = None
    common.db._SessionLocal = None
    create_tables()
    return cfg


def test_full_refresh_cycle(routine_db):
    from common.db import get_db
    from common.models import SentimentSnapshot
    from trader.sentiment.factory import refresh_and_store

    result = refresh_and_store()

    assert result["status"] == "success"
    assert result["snapshots_written"] == 3
    with get_db() as db:
        rows = db.query(SentimentSnapshot).all()
        snapshots = {(row.scope, row.key): row.score for row in rows}
        assert snapshots[("market", "overall")] == 0.2
        assert snapshots[("sector", "Technology")] == 0.4
        assert snapshots[("ticker", "NVDA")] == 0.8


def test_scoring_uses_routine_snapshots(routine_db):
    from common.db import get_db
    from common.models import SentimentSnapshot
    from trader.scoring import compute_sentiment_factor
    from trader.sentiment.factory import refresh_and_store
    from trader.sentiment.scoring import get_latest_ticker_score

    refresh_and_store()

    with get_db() as db:
        market = db.query(SentimentSnapshot).filter_by(scope="market", key="overall").first()
        sector = db.query(SentimentSnapshot).filter_by(scope="sector", key="Technology").first()
    ticker = get_latest_ticker_score("NVDA")

    factor = compute_sentiment_factor(market, sector, ticker, 0.2, 0.3, 0.5)

    assert factor["status"] == "ok"
    assert factor["raw_score"] > 0.5
