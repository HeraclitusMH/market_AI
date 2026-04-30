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


def _normalize_ranking(components: dict, score_total: float, eligible: bool, reasons: List[str]):
    """Expose persisted 7-factor ranking rows without recomputing old scores."""
    components = dict(components)
    composite = components.get("composite_7factor")
    if isinstance(composite, dict):
        composite_score = composite.get("composite_score")
        if isinstance(composite_score, (int, float)):
            score_total = round(float(composite_score) / 100.0, 4)
            components["total_score"] = score_total
        factors = composite.get("factors")
        if isinstance(factors, dict):
            components["weights_used"] = {
                name: float(info.get("weight", 0.0))
                for name, info in factors.items()
                if isinstance(info, dict)
            }

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
