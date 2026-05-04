"""Budget cap behaviour — daily and monthly hard stops, plus batch sizing."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import AppConfig
from common.models import BotState, SentimentLlmUsage
from common.time import utcnow
from trader.sentiment import budget as budget_mod


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


def _seed_usage(eur_cost: float, *, when=None, eur_usd_rate=1.08):
    from common.db import get_db
    with get_db() as db:
        db.add(SentimentLlmUsage(
            id=os.urandom(8).hex(),
            ts=when or utcnow(),
            provider="anthropic",
            model="claude-3-5-sonnet-latest",
            request_kind="sentiment_extraction",
            input_items_count=1,
            prompt_tokens=100,
            completion_tokens=200,
            cost_usd_est=eur_cost * eur_usd_rate,
            cost_eur_est=eur_cost,
            status="success",
        ))


def test_status_under_cap_is_not_stopped():
    from common.db import get_db
    _seed_usage(1.0)
    with get_db() as db:
        st = budget_mod.get_status(
            db, monthly_budget_eur=10.0, daily_budget_fraction=0.12,
            eur_usd_rate=1.08, hard_stop_on_budget=True,
        )
    assert not st.budget_stopped
    assert st.month_to_date_eur == pytest.approx(1.0)


def test_daily_cap_triggers_stop():
    from common.db import get_db
    # daily_cap = 10 * 0.12 = 1.20 EUR
    _seed_usage(1.30)
    with get_db() as db:
        st = budget_mod.get_status(
            db, monthly_budget_eur=10.0, daily_budget_fraction=0.12,
            eur_usd_rate=1.08, hard_stop_on_budget=True,
        )
    assert st.budget_stopped
    assert st.reason == "daily_budget_exhausted"


def test_monthly_cap_triggers_stop_even_if_daily_ok():
    from common.db import get_db
    # Spread spend across older days so daily is 0 but month exceeds cap.
    _seed_usage(9.0, when=utcnow() - timedelta(days=2))
    _seed_usage(1.5, when=utcnow() - timedelta(days=1))   # still in month
    with get_db() as db:
        st = budget_mod.get_status(
            db, monthly_budget_eur=10.0, daily_budget_fraction=0.99,
            eur_usd_rate=1.08, hard_stop_on_budget=True,
        )
    assert st.budget_stopped
    assert st.reason == "monthly_budget_exhausted"


def test_hard_stop_false_never_stops():
    from common.db import get_db
    _seed_usage(50.0)
    with get_db() as db:
        st = budget_mod.get_status(
            db, monthly_budget_eur=10.0, daily_budget_fraction=0.12,
            eur_usd_rate=1.08, hard_stop_on_budget=False,
        )
    assert not st.budget_stopped


def test_batch_sizing_reduces_when_budget_tight():
    # remaining = 0.001 EUR. With sonnet pricing a 300/300-token item costs
    # (300/1M)*3 + (300/1M)*15 = 0.0054 USD ≈ 0.005 EUR. Can't fit one item.
    fittable = budget_mod.max_items_that_fit(
        remaining_eur=0.001,
        eur_usd_rate=1.08,
        model="claude-3-5-sonnet-latest",
        per_item_prompt_token_estimate=300,
        per_item_completion_token_estimate=300,
    )
    assert fittable == 0


def test_batch_sizing_allows_partial_fit():
    fittable = budget_mod.max_items_that_fit(
        remaining_eur=0.05,  # ~ 0.054 USD → 10 items of 0.0054 USD each
        eur_usd_rate=1.08,
        model="claude-3-5-sonnet-latest",
        per_item_prompt_token_estimate=300,
        per_item_completion_token_estimate=300,
    )
    assert fittable >= 1
    assert fittable <= 15


def test_provider_skips_call_when_budget_stopped(monkeypatch):
    """ClaudeLlmSentimentProvider.run() must not call the LLM when stopped."""
    from common.db import get_db
    from common.config import SentimentClaudeConfig, SentimentRssConfig
    from trader.sentiment.claude_provider import ClaudeLlmSentimentProvider

    _seed_usage(50.0)  # exhaust

    claude_cfg = SentimentClaudeConfig(
        monthly_budget_eur=10.0,
        daily_budget_fraction=0.12,
        hard_stop_on_budget=True,
    )
    rss_cfg = SentimentRssConfig(feeds=[])  # no feeds — the budget check runs before we need data

    # Fake client that would explode if called.
    class BoomClient:
        def complete_json(self, **kwargs):
            raise AssertionError("Claude must NOT be called when budget is stopped")

    # Feed the provider one fake entry via monkeypatch so the RSS step yields data.
    from trader.sentiment import claude_provider as cp_mod

    def fake_entries(_cfg):
        return [{
            "title": "T", "snippet": "s", "url": "https://x/1",
            "published_at": utcnow(), "source": "feed", "language": None,
        }]
    monkeypatch.setattr(cp_mod, "_extract_entries", fake_entries)

    provider = ClaudeLlmSentimentProvider(claude_cfg, rss_cfg, client=BoomClient())
    run = provider.run()
    assert run.status == "budget_stopped"
    assert run.items_sent == 0
