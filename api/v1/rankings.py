"""GET /api/v1/rankings and /api/v1/trade-plans"""
from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import SecurityMaster, SymbolRanking, TradePlan

router = APIRouter(tags=["v1"])


class RankingRow(BaseModel):
    id: int
    ts: str
    symbol: str
    name: str = ""
    score_total: float
    components: dict
    eligible: bool
    reasons: List[str]


class PlanRow(BaseModel):
    id: int
    ts: str
    symbol: str
    name: str = ""
    bias: str
    strategy: str
    expiry: Optional[str] = None
    dte: Optional[int] = None
    legs: dict
    pricing: dict
    rationale: dict
    status: str
    skip_reason: Optional[str] = None


def _lookup_names(db: Session, symbols: List[str]) -> dict:
    if not symbols:
        return {}
    rows = db.query(SecurityMaster.symbol, SecurityMaster.name).filter(
        SecurityMaster.symbol.in_(symbols)
    ).all()
    return {r.symbol: r.name for r in rows}


def _parse(s: Optional[str], default=None):
    if default is None:
        default = {}
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


_SCORING_FACTORS = ("sentiment", "momentum_trend", "risk", "fundamentals")
_FACTOR_ALIASES = {
    "momentum_trend": ("momentum", "trend"),
}


def _factor_value(components: dict, name: str) -> Optional[float]:
    for key in (name, *_FACTOR_ALIASES.get(name, ())):
        factor = components.get(key)
        if not isinstance(factor, dict):
            continue
        if _factor_unavailable(name, factor):
            return None
        value = factor.get("value_0_1")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _factor_unavailable(name: str, factor: dict) -> bool:
    status = factor.get("status")
    if status in {"missing", "disabled", "error"}:
        return True
    if status != "neutral":
        return False
    if name != "fundamentals":
        return False

    metrics = factor.get("metrics")
    if not isinstance(metrics, dict):
        return True
    pillars = metrics.get("pillars")
    if not isinstance(pillars, dict):
        return True
    return not any(
        isinstance(pillar, dict) and pillar.get("metrics")
        for pillar in pillars.values()
    )


def _normalize_ranking(components: dict, score_total: float, eligible: bool, reasons: List[str]):
    """Normalize legacy ranking rows where liquidity was persisted as a score factor."""
    components = dict(components)
    for name in _SCORING_FACTORS:
        factor = components.get(name)
        if isinstance(factor, dict) and _factor_unavailable(name, factor):
            factor = dict(factor)
            factor["value_0_1"] = None
            if factor.get("status") == "neutral":
                factor["status"] = "missing"
                factor.setdefault("reason", "no_usable_fundamental_metrics")
            components[name] = factor

    weights = components.get("weights_used")
    if isinstance(weights, dict):
        raw_weights = {
            name: float(weights.get(name, 0.0) or sum(
                float(weights.get(alias, 0.0) or 0.0)
                for alias in _FACTOR_ALIASES.get(name, ())
            ))
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


@router.get("/rankings", response_model=List[RankingRow])
def get_rankings(limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    max_ts = db.query(func.max(SymbolRanking.ts)).scalar()
    if max_ts is None:
        return []
    rows = (
        db.query(SymbolRanking)
        .filter(SymbolRanking.ts == max_ts)
        .all()
    )
    names = _lookup_names(db, [r.symbol for r in rows])
    result = []
    for r in rows:
        components = _parse(r.components_json)
        reasons = _parse(r.reasons_json, [])
        components, score_total, eligible, reasons = _normalize_ranking(
            components, r.score_total, r.eligible, reasons
        )
        result.append(RankingRow(
            id=r.id,
            ts=str(r.ts),
            symbol=r.symbol,
            name=names.get(r.symbol, ""),
            score_total=score_total,
            components=components,
            eligible=eligible,
            reasons=reasons,
        ))
    result.sort(key=lambda row: row.score_total, reverse=True)
    return result[:limit]


@router.get("/trade-plans", response_model=List[PlanRow])
def get_trade_plans(
    limit: int = Query(50, le=200),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(TradePlan).order_by(TradePlan.id.desc())
    if status:
        q = q.filter(TradePlan.status == status)
    rows = q.limit(limit).all()
    names = _lookup_names(db, [r.symbol for r in rows])
    return [
        PlanRow(
            id=r.id,
            ts=str(r.ts),
            symbol=r.symbol,
            name=names.get(r.symbol, ""),
            bias=r.bias,
            strategy=r.strategy,
            expiry=r.expiry,
            dte=r.dte,
            legs=_parse(r.legs_json),
            pricing=_parse(r.pricing_json),
            rationale=_parse(r.rationale_json),
            status=r.status,
            skip_reason=r.skip_reason,
        )
        for r in rows
    ]
