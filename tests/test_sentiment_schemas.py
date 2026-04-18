"""Strict validation of the LLM output schemas."""
from __future__ import annotations

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.sentiment.schemas import (
    LlmSentimentBatch,
    LlmSentimentItem,
    NewsItemForLlm,
    SentimentEntity,
)


def test_minimal_valid_batch():
    batch = LlmSentimentBatch.model_validate({
        "model": "claude-3-5-sonnet-latest",
        "as_of": "2026-04-18T12:00:00Z",
        "items": [{
            "id": "abc12345",
            "entities": [{"type": "market", "key": "US", "sentiment": 0.3, "confidence": 0.7}],
            "reasons": ["Jobs report strong."],
        }],
    })
    assert len(batch.items) == 1
    assert batch.items[0].entities[0].key == "US"


def test_item_without_market_entity_is_rejected():
    with pytest.raises(ValidationError):
        LlmSentimentItem.model_validate({
            "id": "xx",
            "entities": [{"type": "sector", "key": "Technology", "sentiment": 0.5, "confidence": 0.8}],
        })


def test_sentiment_range_enforced():
    with pytest.raises(ValidationError):
        SentimentEntity.model_validate(
            {"type": "market", "key": "US", "sentiment": 1.5, "confidence": 0.5}
        )
    with pytest.raises(ValidationError):
        SentimentEntity.model_validate(
            {"type": "market", "key": "US", "sentiment": 0.0, "confidence": 1.5}
        )


def test_reasons_capped_at_five_and_200_chars():
    item = LlmSentimentItem.model_validate({
        "id": "xyz",
        "entities": [{"type": "market", "key": "US", "sentiment": 0.1, "confidence": 0.5}],
        "reasons": [f"reason {i} " + ("x" * 500) for i in range(10)],
    })
    assert len(item.reasons) == 5
    assert all(len(r) <= 200 for r in item.reasons)


def test_empty_key_rejected():
    with pytest.raises(ValidationError):
        SentimentEntity.model_validate(
            {"type": "sector", "key": "   ", "sentiment": 0.1, "confidence": 0.5}
        )


def test_extra_fields_ignored_not_rejected():
    item = LlmSentimentItem.model_validate({
        "id": "i1",
        "entities": [{"type": "market", "key": "US", "sentiment": 0.0, "confidence": 0.5, "extra": "zzz"}],
        "reasons": [],
        "bogus_field": "ignored",
    })
    assert item.id == "i1"


def test_news_item_for_llm_accepts_optional_fields():
    it = NewsItemForLlm.model_validate({
        "id": "i1",
        "title": "Hello",
        "snippet": "World",
        "source": "reuters.com",
    })
    assert it.url is None
    assert it.published_at is None
