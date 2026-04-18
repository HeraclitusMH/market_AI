"""Aggregation: per-item LLM outputs → SentimentResult per scope/key.

Weighting:
    weight  = confidence * recency_weight
    recency_weight = 0.5 ** (age_hours / RECENCY_HALF_LIFE_HOURS)

`RECENCY_HALF_LIFE_HOURS = 72` (3 days) matches the spec's "half-life 3 days".

The summary text is built deterministically from the top three contributing
items; we do NOT call the LLM again to write the summary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from common.time import utcnow
from trader.sentiment.base import SentimentResult
from trader.sentiment.schemas import LlmSentimentItem, NewsItemForLlm


RECENCY_HALF_LIFE_HOURS: float = 72.0


# ── helpers ─────────────────────────────────────────────────────────

def recency_weight(published_at: Optional[datetime], now: datetime) -> float:
    if published_at is None:
        return 0.5
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - published_at).total_seconds() / 3600.0)
    return 0.5 ** (age_hours / RECENCY_HALF_LIFE_HOURS)


@dataclass
class _Contribution:
    item_id: str
    title: str
    url: Optional[str]
    sentiment: float
    confidence: float
    recency: float
    reasons: List[str]

    @property
    def weight(self) -> float:
        return self.confidence * self.recency

    @property
    def rank_score(self) -> float:
        return self.confidence * abs(self.sentiment) * self.recency


# ── main ────────────────────────────────────────────────────────────

def aggregate(
    *,
    items_for_llm: List[NewsItemForLlm],
    llm_items: List[LlmSentimentItem],
    min_confidence: float = 0.35,
    now: Optional[datetime] = None,
) -> List[SentimentResult]:
    """Turn validated LLM per-item entities into one SentimentResult per (scope, key).

    Always emits at most one market snapshot (scope='market', key='US'), plus
    any sector / ticker snapshots with non-zero total weight.
    """
    now = now or utcnow()
    by_id: Dict[str, NewsItemForLlm] = {i.id: i for i in items_for_llm}

    # (scope, key) -> list of contributions
    bucket: Dict[Tuple[str, str], List[_Contribution]] = {}

    for item in llm_items:
        src = by_id.get(item.id)
        recency = recency_weight(src.published_at if src else None, now)

        for ent in item.entities:
            if ent.confidence < min_confidence:
                continue
            scope = ent.type  # "market" | "sector" | "ticker"
            key = ent.key.strip()
            if scope == "market":
                key = "US"  # spec: market-wide key is "US"
            else:
                if not key:
                    continue
            bucket.setdefault((scope, key), []).append(
                _Contribution(
                    item_id=item.id,
                    title=(src.title if src else "") or "",
                    url=(src.url if src else None),
                    sentiment=float(ent.sentiment),
                    confidence=float(ent.confidence),
                    recency=recency,
                    reasons=list(item.reasons),
                )
            )

    results: List[SentimentResult] = []
    for (scope, key), contribs in bucket.items():
        total_w = sum(c.weight for c in contribs)
        if total_w <= 0:
            continue
        score = sum(c.sentiment * c.weight for c in contribs) / total_w
        score = max(-1.0, min(1.0, score))

        top = sorted(contribs, key=lambda c: c.rank_score, reverse=True)[:3]
        summary = _build_summary(top, count=len(contribs))
        breakdown = _build_breakdown(scope, key, contribs, score=score, total_weight=total_w)

        results.append(SentimentResult(
            scope=scope,
            key=key,
            score=round(score, 4),
            summary=summary,
            sources=[json.dumps(breakdown, ensure_ascii=False)],
        ))

    return results


# ── formatting ──────────────────────────────────────────────────────

def _build_summary(top: List[_Contribution], *, count: int) -> str:
    """Human-readable summary string, truncated to a reasonable length."""
    lines = [f"{count} contributing item(s). Top:"]
    for c in top:
        bullet = (c.reasons[0] if c.reasons else c.title)[:160]
        lines.append(f"- [{c.sentiment:+.2f} @ {c.confidence:.2f}] {bullet}")
    return "\n".join(lines)[:1000]


def _build_breakdown(
    scope: str,
    key: str,
    contribs: List[_Contribution],
    *,
    score: float,
    total_weight: float,
) -> Dict:
    return {
        "scope": scope,
        "key": key,
        "score": round(score, 4),
        "total_weight": round(total_weight, 4),
        "items": [
            {
                "id": c.item_id,
                "title": c.title[:200],
                "url": c.url,
                "sentiment": round(c.sentiment, 4),
                "confidence": round(c.confidence, 4),
                "recency_weight": round(c.recency, 4),
                "weight": round(c.weight, 4),
                "reasons": c.reasons[:5],
            }
            for c in contribs
        ],
    }
