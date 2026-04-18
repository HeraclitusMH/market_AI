"""Dedup hashing + DB filter tests."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import AppConfig
from common.models import BotState, SentimentLlmItem
from common.time import utcnow
from trader.sentiment.dedup import RawNewsItem, item_hash, mark_processed, upsert_and_filter_new


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


def test_hash_is_deterministic_and_normalises():
    a = item_hash("Fed cuts rates", "Big move in bonds.", "https://ex.com/a?x=1")
    b = item_hash("  Fed CUTS   rates!", "Big move in bonds", "https://ex.com/a")
    assert a == b
    assert len(a) == 16


def test_hash_differs_when_content_differs():
    a = item_hash("Fed cuts rates", "snippet A", "https://ex.com/a")
    b = item_hash("Fed cuts rates", "snippet B", "https://ex.com/a")
    assert a != b


def test_upsert_returns_all_new_and_then_dedups_after_mark():
    from common.db import get_db

    items = [
        RawNewsItem(title="T1", snippet="s1", url="https://x/1", published_at=None, source="feed1"),
        RawNewsItem(title="T2", snippet="s2", url="https://x/2", published_at=None, source="feed1"),
    ]

    with get_db() as db:
        first = upsert_and_filter_new(db, items, dedup_window_days=14)
        assert len(first) == 2
        mark_processed(db, [hid for hid, _ in first])

    with get_db() as db:
        second = upsert_and_filter_new(db, items, dedup_window_days=14)
        # Same content → already processed → filtered out
        assert second == []


def test_items_outside_dedup_window_are_resent():
    from common.db import get_db

    items = [RawNewsItem(title="T", snippet="s", url=None, published_at=None, source="feed1")]

    with get_db() as db:
        pairs = upsert_and_filter_new(db, items, dedup_window_days=14)
        assert len(pairs) == 1
        hid = pairs[0][0]
        mark_processed(db, [hid])

    # Age the processed_at record beyond the window.
    with get_db() as db:
        row = db.get(SentimentLlmItem, hid)
        row.processed_at = utcnow() - timedelta(days=30)

    with get_db() as db:
        pairs = upsert_and_filter_new(db, items, dedup_window_days=14)
        assert len(pairs) == 1  # re-eligible
