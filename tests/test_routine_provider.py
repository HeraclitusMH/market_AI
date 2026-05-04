from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from common.config import AppConfig
from trader.sentiment.routine_provider import RoutineProvider, StaleDataError


def _iso(hours_ago: float = 1.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def _payload(**overrides):
    data = {
        "schema_version": 1,
        "timestamp": _iso(1),
        "run_id": "run_test",
        "articles_analyzed": 2,
        "articles_skipped_dedup": 0,
        "total_articles_fetched": 2,
        "market": {"score": 0.25, "summary": "Constructive market tone."},
        "sectors": {"Technology": {"score": 0.5, "summary": "Tech strong."}},
        "tickers": {"NVDA": {"score": 0.75, "summary": "AI demand."}},
        "sources": [{"url": "https://x/1", "title": "T", "feed": "feed"}],
    }
    data.update(overrides)
    return data


def _write(tmp_path, data) -> str:
    path = tmp_path / "sentiment_output.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_load_local_valid(tmp_path):
    provider = RoutineProvider(local_path=_write(tmp_path, _payload()))

    market = provider.fetch_market_sentiment()
    sectors = provider.fetch_sector_sentiment()
    tickers = provider.fetch_ticker_sentiment()

    assert market.scope == "market"
    assert market.key == "overall"
    assert market.score == 0.25
    assert sectors[0].key == "Technology"
    assert sectors[0].score == 0.5
    assert tickers[0].key == "NVDA"
    assert tickers[0].score == 0.75


def test_load_local_missing_file(tmp_path):
    provider = RoutineProvider(local_path=str(tmp_path / "missing.json"))

    assert provider.fetch_market_sentiment() is None
    assert provider.fetch_sector_sentiment() == []
    assert provider.fetch_ticker_sentiment() == []


def test_load_local_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")
    provider = RoutineProvider(local_path=str(path))

    assert provider.fetch_market_sentiment() is None
    assert provider.fetch_sector_sentiment() == []


def test_staleness_ok(tmp_path):
    provider = RoutineProvider(local_path=_write(tmp_path, _payload(timestamp=_iso(2))))

    assert provider.fetch_market_sentiment().score == 0.25


def test_staleness_warning(tmp_path, caplog):
    provider = RoutineProvider(local_path=_write(tmp_path, _payload(timestamp=_iso(6.5))))

    assert provider.fetch_market_sentiment().score == 0.25
    assert "approaching staleness" in caplog.text


def test_staleness_error(tmp_path):
    provider = RoutineProvider(local_path=_write(tmp_path, _payload(timestamp=_iso(9))))

    with pytest.raises(StaleDataError):
        provider.fetch_market_sentiment()


def test_score_clamping(tmp_path, caplog):
    provider = RoutineProvider(local_path=_write(tmp_path, _payload(market={"score": 1.5, "summary": "Too high"})))

    assert provider.fetch_market_sentiment().score == 1.0
    assert "clamped" in caplog.text


def test_missing_sectors_key(tmp_path):
    data = _payload()
    data.pop("sectors")
    provider = RoutineProvider(local_path=_write(tmp_path, data))

    assert provider.fetch_sector_sentiment() == []


def test_missing_tickers_key(tmp_path):
    data = _payload()
    data.pop("tickers")
    provider = RoutineProvider(local_path=_write(tmp_path, data))

    assert provider.fetch_ticker_sentiment() == []


def test_caching_within_cycle(tmp_path, monkeypatch):
    provider = RoutineProvider(local_path=_write(tmp_path, _payload()))
    calls = 0
    original = provider._read_local

    def counted():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(provider, "_read_local", counted)
    provider.fetch_market_sentiment()
    provider.fetch_sector_sentiment()
    provider.fetch_ticker_sentiment()

    assert calls == 1


def test_github_source(monkeypatch):
    seen = {}

    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return _payload()

    def fake_get(url, headers, timeout):
        seen.update({"url": url, "headers": headers, "timeout": timeout})
        return Response()

    monkeypatch.setattr("trader.sentiment.routine_provider.requests.get", fake_get)
    provider = RoutineProvider(
        source_type="github",
        github_raw_url="https://raw.githubusercontent.com/u/r/main/data/sentiment_output.json",
        github_token="tok",
    )

    assert provider.fetch_market_sentiment().score == 0.25
    assert seen["url"].endswith("sentiment_output.json")
    assert seen["headers"]["Authorization"] == "Bearer tok"


def test_github_404(monkeypatch):
    class Response:
        status_code = 404
        headers = {}

    monkeypatch.setattr("trader.sentiment.routine_provider.requests.get", lambda *_, **__: Response())
    provider = RoutineProvider(source_type="github", github_raw_url="https://x/missing.json")

    assert provider.fetch_market_sentiment() is None


def test_zero_articles_analyzed(tmp_path):
    provider = RoutineProvider(local_path=_write(tmp_path, _payload(articles_analyzed=0)))

    assert provider.fetch_market_sentiment().score == 0.25


def test_factory_builds_routine_provider(monkeypatch):
    import common.config
    from trader.sentiment.factory import build_provider

    monkeypatch.setattr(common.config, "_cached", AppConfig(sentiment={"provider": "claude_routine"}))

    assert isinstance(build_provider(), RoutineProvider)


def test_factory_refresh_handles_stale(tmp_path, monkeypatch):
    import common.config
    import common.db
    from common.db import create_tables, get_db
    from common.models import SentimentSnapshot
    from trader.sentiment.factory import refresh_and_store

    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = AppConfig(
        db={"path": str(tmp_path / "test.db")},
        sentiment={
            "provider": "claude_routine",
            "routine": {
                "source_type": "local",
                "local_path": _write(tmp_path, _payload(timestamp=_iso(9))),
                "max_staleness_hours": 8.0,
            },
        },
    )
    monkeypatch.setattr(common.config, "_cached", cfg)
    common.db._engine = None
    common.db._SessionLocal = None
    create_tables()

    result = refresh_and_store()

    assert result["status"] == "stale"
    assert result["snapshots_written"] == 0
    with get_db() as db:
        assert db.query(SentimentSnapshot).count() == 0
