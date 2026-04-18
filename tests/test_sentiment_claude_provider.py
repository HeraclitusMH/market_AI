"""End-to-end provider test with a mocked Anthropic client (no network)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import AppConfig, SentimentClaudeConfig, SentimentRssConfig
from common.models import BotState, SentimentLlmItem, SentimentLlmUsage, SentimentSnapshot
from common.time import utcnow


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    import common.config
    import common.db
    from common.db import create_tables, get_db

    db_path = str(tmp_path / "test.db")
    cfg = AppConfig(db={"path": db_path})
    monkeypatch.setattr(common.config, "_cached", cfg)
    common.db._engine = None
    common.db._SessionLocal = None
    create_tables()
    with get_db() as db:
        db.add(BotState(id=1, paused=False, kill_switch=False, approve_mode=True, options_enabled=True))
    yield


class _FakeClient:
    """Mimics AnthropicClient.complete_json without the network."""
    def __init__(self, data, request_id="req_fake", prompt_tokens=120, completion_tokens=260, model="claude-3-5-sonnet-latest"):
        self._data = data
        self.request_id = request_id
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.model = model

    def complete_json(self, *, system, user, max_tokens, temperature=0.2):
        from trader.sentiment.llm_client import LlmResponse
        return LlmResponse(
            text="{ok}",
            data=self._data,
            model=self.model,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            request_id=self.request_id,
        )


def test_run_end_to_end_records_usage_and_marks_processed(monkeypatch):
    from common.db import get_db
    from trader.sentiment import claude_provider as cp_mod
    from trader.sentiment.claude_provider import ClaudeLlmSentimentProvider
    from trader.sentiment import factory as factory_mod

    # Fake RSS entries.
    def fake_entries(_cfg):
        return [
            {"title": "Fed cuts rates", "snippet": "dovish surprise", "url": "https://x/1",
             "published_at": utcnow(), "source": "feed1", "language": None},
            {"title": "NVDA surges on AI demand", "snippet": "datacenter beat", "url": "https://x/2",
             "published_at": utcnow(), "source": "feed2", "language": None},
        ]
    monkeypatch.setattr(cp_mod, "_extract_entries", fake_entries)

    # Sanity: we don't know the hashes in advance — so the fake reply must be
    # built AFTER dedup assigns ids. We cheat by recomputing them here.
    from trader.sentiment.dedup import item_hash
    id1 = item_hash("Fed cuts rates", "dovish surprise", "https://x/1")
    id2 = item_hash("NVDA surges on AI demand", "datacenter beat", "https://x/2")

    fake_data = {
        "model": "claude-3-5-sonnet-latest",
        "as_of": utcnow().isoformat(),
        "items": [
            {
                "id": id1,
                "entities": [
                    {"type": "market", "key": "US", "sentiment": 0.4, "confidence": 0.8},
                    {"type": "sector", "key": "Financials", "sentiment": 0.5, "confidence": 0.7},
                ],
                "reasons": ["Dovish Fed supports risk assets."],
            },
            {
                "id": id2,
                "entities": [
                    {"type": "market", "key": "US", "sentiment": 0.3, "confidence": 0.7},
                    {"type": "ticker", "key": "NVDA", "sentiment": 0.9, "confidence": 0.9},
                ],
                "reasons": ["Datacenter demand remains strong."],
            },
        ],
    }

    claude_cfg = SentimentClaudeConfig(
        monthly_budget_eur=10.0, daily_budget_fraction=0.5,
        max_items_per_run=10,
    )
    rss_cfg = SentimentRssConfig(feeds=["https://fake"])
    provider = ClaudeLlmSentimentProvider(claude_cfg, rss_cfg, client=_FakeClient(fake_data))

    summary = factory_mod.refresh_and_store(provider)
    assert summary["status"] == "success"
    assert summary["snapshots_written"] >= 3  # market + sector + ticker

    with get_db() as db:
        snapshots = db.query(SentimentSnapshot).all()
        scopes = {(s.scope, s.key) for s in snapshots}
        assert ("market", "US") in scopes
        assert ("sector", "Financials") in scopes
        assert ("ticker", "NVDA") in scopes

        processed = db.query(SentimentLlmItem).filter(SentimentLlmItem.processed_at.isnot(None)).count()
        assert processed == 2

        usage_rows = db.query(SentimentLlmUsage).all()
        assert len(usage_rows) == 1
        assert usage_rows[0].status == "success"
        assert usage_rows[0].cost_usd_est > 0


def test_llm_failure_keeps_snapshots_unchanged(monkeypatch):
    from common.db import get_db
    from trader.sentiment import claude_provider as cp_mod
    from trader.sentiment.claude_provider import ClaudeLlmSentimentProvider
    from trader.sentiment import factory as factory_mod
    from trader.sentiment.llm_client import LlmTransientError

    def fake_entries(_cfg):
        return [{"title": "T", "snippet": "s", "url": "https://x/1",
                 "published_at": utcnow(), "source": "feed", "language": None}]
    monkeypatch.setattr(cp_mod, "_extract_entries", fake_entries)

    class BoomClient:
        def complete_json(self, **_):
            raise LlmTransientError("simulated 503")

    claude_cfg = SentimentClaudeConfig(monthly_budget_eur=10.0, daily_budget_fraction=0.5)
    rss_cfg = SentimentRssConfig(feeds=["https://fake"])
    provider = ClaudeLlmSentimentProvider(claude_cfg, rss_cfg, client=BoomClient())

    summary = factory_mod.refresh_and_store(provider)
    assert summary["status"] == "failed"
    with get_db() as db:
        assert db.query(SentimentSnapshot).count() == 0
        # A failed usage row should be recorded for budget auditing.
        usage = db.query(SentimentLlmUsage).all()
        assert len(usage) == 1
        assert usage[0].status == "failed"
