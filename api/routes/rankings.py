"""Rankings API routes."""
from __future__ import annotations

import json
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from common.db import get_db
from common.models import SymbolRanking, TradePlan

router = APIRouter(prefix="/api/rankings", tags=["rankings"])


class RankingRow(BaseModel):
    id: int
    ts: str
    symbol: str
    score_total: float
    components: dict
    eligible: bool
    reasons: List[str]


class PlanRow(BaseModel):
    id: int
    ts: str
    symbol: str
    bias: str
    strategy: str
    expiry: str | None
    dte: int | None
    legs: dict
    pricing: dict
    rationale: dict
    status: str
    skip_reason: str | None


@router.get("/latest", response_model=List[RankingRow])
def get_latest_rankings(limit: int = 50):
    """Return the most recent ranking rows, one per symbol."""
    with get_db() as db:
        # Latest batch: find max ts, then get all rows at that ts
        from sqlalchemy import func
        max_ts = db.query(func.max(SymbolRanking.ts)).scalar()
        if max_ts is None:
            return []
        rows = (
            db.query(SymbolRanking)
            .filter(SymbolRanking.ts == max_ts)
            .all()
        )
    result = []
    for r in rows:
        components = _parse_json(r.components_json)
        reasons = _parse_json(r.reasons_json, [])
        components, score_total, eligible, reasons = _normalize_ranking(
            components, r.score_total, r.eligible, reasons
        )
        result.append(RankingRow(
            id=r.id,
            ts=str(r.ts),
            symbol=r.symbol,
            score_total=score_total,
            components=components,
            eligible=eligible,
            reasons=reasons,
        ))
    result.sort(key=lambda row: row.score_total, reverse=True)
    return result[:limit]


@router.get("/plans", response_model=List[PlanRow])
def get_trade_plans(limit: int = 50, status: str | None = None):
    """Return recent trade plans, optionally filtered by status."""
    with get_db() as db:
        q = db.query(TradePlan).order_by(TradePlan.id.desc())
        if status:
            q = q.filter(TradePlan.status == status)
        rows = q.limit(limit).all()
    result = []
    for r in rows:
        result.append(PlanRow(
            id=r.id,
            ts=str(r.ts),
            symbol=r.symbol,
            bias=r.bias,
            strategy=r.strategy,
            expiry=r.expiry,
            dte=r.dte,
            legs=_parse_json(r.legs_json),
            pricing=_parse_json(r.pricing_json),
            rationale=_parse_json(r.rationale_json),
            status=r.status,
            skip_reason=r.skip_reason,
        ))
    return result


def _parse_json(s, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


_SCORING_FACTORS = ("sentiment", "momentum_trend", "risk", "fundamentals")


def _factor_value(components: dict, name: str) -> float | None:
    factor = components.get(name)
    if not isinstance(factor, dict):
        return None
    value = factor.get("value_0_1")
    return float(value) if isinstance(value, (int, float)) else None


def _normalize_ranking(components: dict, score_total: float, eligible: bool, reasons: List[str]):
    """Normalize legacy ranking rows where liquidity was persisted as a score factor."""
    components = dict(components)
    weights = components.get("weights_used")
    if isinstance(weights, dict):
        raw_weights = {
            name: float(weights.get(name, 0.0))
            for name in _SCORING_FACTORS
            if _factor_value(components, name) is not None
        }
        total_weight = sum(raw_weights.values())
        if total_weight > 0:
            normalized_weights = {
                name: round(raw_weights.get(name, 0.0) / total_weight, 4)
                for name in _SCORING_FACTORS
            }
            score_total = round(sum(
                normalized_weights[name] * (_factor_value(components, name) or 0.0)
                for name in _SCORING_FACTORS
            ), 4)
            components["weights_used"] = normalized_weights
            components["total_score"] = score_total

    liquidity = components.get("liquidity")
    if isinstance(liquidity, dict) and liquidity.get("eligible") is False:
        eligible = False
        for reason in liquidity.get("reasons", []):
            if reason not in reasons:
                reasons.append(reason)

    return components, score_total, eligible, reasons
