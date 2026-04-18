"""Strict Pydantic schemas for Claude sentiment IO.

Input: what we send to the LLM per news item.
Output: what the LLM must return — strictly validated. Any item that does not
validate is dropped; if no items validate the run is treated as a failure.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, confloat, field_validator


# ── Input ───────────────────────────────────────────────────

class NewsItemForLlm(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    title: str
    snippet: str
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    source: str = ""
    language: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


# ── Output ──────────────────────────────────────────────────

class SentimentEntity(BaseModel):
    type: Literal["market", "sector", "ticker"]
    key: str = Field(..., min_length=1, max_length=80)
    sentiment: confloat(ge=-1.0, le=1.0)
    confidence: confloat(ge=0.0, le=1.0)

    model_config = ConfigDict(extra="ignore")

    @field_validator("key")
    @classmethod
    def _strip_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("key must be non-empty")
        return v


class LlmSentimentItem(BaseModel):
    id: str = Field(..., min_length=1)
    entities: List[SentimentEntity]
    reasons: List[str] = []
    key_phrases: List[str] = []

    model_config = ConfigDict(extra="ignore")

    @field_validator("reasons")
    @classmethod
    def _cap_reasons(cls, v: List[str]) -> List[str]:
        # cap at 5 bullets, 200 chars each
        cleaned = [str(r).strip()[:200] for r in v if str(r).strip()]
        return cleaned[:5]

    @field_validator("key_phrases")
    @classmethod
    def _cap_phrases(cls, v: List[str]) -> List[str]:
        cleaned = [str(p).strip()[:80] for p in v if str(p).strip()]
        return cleaned[:10]

    @field_validator("entities")
    @classmethod
    def _require_market(cls, v: List[SentimentEntity]) -> List[SentimentEntity]:
        # Accept any case for the market key; aggregation layer normalises to "US".
        has_market = any(e.type == "market" and e.key.strip().upper() == "US" for e in v)
        if not has_market:
            raise ValueError("item must include a market entity with key 'US'")
        return v


class LlmSentimentBatch(BaseModel):
    model: str
    as_of: datetime
    items: List[LlmSentimentItem] = []

    model_config = ConfigDict(extra="ignore")
