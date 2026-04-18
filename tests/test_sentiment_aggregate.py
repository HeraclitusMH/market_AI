"""Aggregation weighting + summary determinism tests."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.time import utcnow
from trader.sentiment.aggregate import aggregate, recency_weight
from trader.sentiment.schemas import LlmSentimentItem, NewsItemForLlm, SentimentEntity


def _nitem(id_, title="t", hours_old=0):
    return NewsItemForLlm(
        id=id_, title=title, snippet="", url=f"https://x/{id_}",
        published_at=utcnow() - timedelta(hours=hours_old),
        source="feed",
    )


def _ent(type_, key, sentiment, confidence):
    return SentimentEntity(type=type_, key=key, sentiment=sentiment, confidence=confidence)


def _lit(id_, entities, reasons=None):
    return LlmSentimentItem(id=id_, entities=entities, reasons=reasons or ["bullet"])


def test_market_score_is_weighted_average():
    items = [_nitem("a", hours_old=0), _nitem("b", hours_old=0)]
    llm = [
        _lit("a", [_ent("market", "US", 1.0, 0.5)]),
        _lit("b", [_ent("market", "US", -1.0, 1.0)]),
    ]
    results = aggregate(items_for_llm=items, llm_items=llm, min_confidence=0.0)
    market = next(r for r in results if r.scope == "market" and r.key == "US")
    # weights: 0.5 and 1.0 (age 0 → recency 1.0). Weighted avg = (1*0.5 + -1*1.0)/1.5 = -1/3
    assert round(market.score, 3) == round(-1 / 3, 3)


def test_low_confidence_entities_dropped():
    items = [_nitem("a")]
    llm = [_lit("a", [
        _ent("market", "US", 0.9, 0.9),
        _ent("sector", "Energy", 0.9, 0.1),   # below min_confidence 0.35
    ])]
    results = aggregate(items_for_llm=items, llm_items=llm, min_confidence=0.35)
    scopes = {r.scope for r in results}
    assert "market" in scopes
    assert "sector" not in scopes


def test_recency_halves_every_72h():
    now = utcnow()
    w_fresh = recency_weight(now, now)
    w_72 = recency_weight(now - timedelta(hours=72), now)
    w_144 = recency_weight(now - timedelta(hours=144), now)
    assert abs(w_fresh - 1.0) < 1e-6
    assert abs(w_72 - 0.5) < 1e-6
    assert abs(w_144 - 0.25) < 1e-6


def test_market_key_is_normalised_to_US():
    # Schema only accepts "US" (case-insensitive — see tests/test_sentiment_schemas).
    # Aggregator must always emit "US" regardless of case the model returned.
    items = [_nitem("a")]
    llm = [_lit("a", [_ent("market", "us", 0.2, 0.8)])]
    results = aggregate(items_for_llm=items, llm_items=llm, min_confidence=0.0)
    market = [r for r in results if r.scope == "market"]
    assert len(market) == 1
    assert market[0].key == "US"


def test_ticker_level_aggregation():
    items = [_nitem("a"), _nitem("b")]
    llm = [
        _lit("a", [
            _ent("market", "US", 0.0, 0.5),
            _ent("ticker", "NVDA", 0.8, 0.9),
        ]),
        _lit("b", [
            _ent("market", "US", 0.0, 0.5),
            _ent("ticker", "NVDA", 0.6, 0.7),
        ]),
    ]
    results = aggregate(items_for_llm=items, llm_items=llm, min_confidence=0.0)
    ticker = [r for r in results if r.scope == "ticker" and r.key == "NVDA"]
    assert len(ticker) == 1
    assert ticker[0].score > 0.6


def test_summary_is_deterministic():
    items = [_nitem("a"), _nitem("b")]
    llm = [
        _lit("a", [_ent("market", "US", 0.3, 0.8)], reasons=["alpha"]),
        _lit("b", [_ent("market", "US", -0.5, 0.9)], reasons=["beta"]),
    ]
    r1 = aggregate(items_for_llm=items, llm_items=llm, min_confidence=0.0)
    r2 = aggregate(items_for_llm=items, llm_items=llm, min_confidence=0.0)
    m1 = next(r for r in r1 if r.scope == "market").summary
    m2 = next(r for r in r2 if r.scope == "market").summary
    assert m1 == m2
