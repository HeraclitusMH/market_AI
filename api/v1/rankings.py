"""GET /api/v1/rankings and /api/v1/trade-plans"""
from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_db
from api.v1._refresh_log import log_refresh
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
    weight_profile: Optional[str] = None


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


class RefreshRankingsResponse(BaseModel):
    status: str
    sentiment_status: str
    snapshots_written: int
    ranked: int
    latest_ts: Optional[str] = None
    reason: str = ""


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


def _missing_required_score_reasons(components: dict) -> List[str]:
    missing: List[str] = []
    composite = components.get("composite_6factor")
    factors = composite.get("factors") if isinstance(composite, dict) else None
    if isinstance(factors, dict):
        for name in ("technical", "momentum", "sentiment", "quality", "growth", "risk_penalty"):
            factor = factors.get(name)
            if not isinstance(factor, dict) or not isinstance(factor.get("score"), (int, float)):
                missing.append(f"missing_score_{name}")
        liquidity = components.get("liquidity")
        if not isinstance(liquidity, dict) or liquidity.get("value_0_1") is None:
            missing.append("missing_score_liquidity")
        return missing

    for name in ("sentiment", "momentum_trend", "risk", "fundamentals", "liquidity"):
        factor = components.get(name)
        if not isinstance(factor, dict) or factor.get("value_0_1") is None:
            missing.append(f"missing_score_{name}")
    return missing


def _factor_score(factor: object, *, invert: bool = False) -> Optional[float]:
    if not isinstance(factor, dict):
        return None
    value = factor.get("score")
    if value is None:
        value = factor.get("value_0_1")
    if not isinstance(value, (int, float)):
        return None
    score = float(value)
    if score > 1.0:
        score = score / 100.0
    score = max(0.0, min(1.0, score))
    return round(1.0 - score if invert else score, 4)


def _legacy_fundamental_score(components: dict, key: str) -> Optional[float]:
    factor = components.get(key)
    score = _factor_score(factor)
    if score is not None:
        return score

    fundamentals = components.get("fundamentals")
    if not isinstance(fundamentals, dict):
        return None
    pillars = ((fundamentals.get("metrics") or {}).get("pillars") or {})
    pillar_key = "profitability" if key == "quality" else "growth"
    pillar = pillars.get(pillar_key)
    if isinstance(pillar, dict):
        pillar_score = pillar.get("score")
        if isinstance(pillar_score, (int, float)):
            return round(max(0.0, min(1.0, float(pillar_score) / 100.0)), 4)
    return _factor_score(fundamentals)


def _ensure_composite_6factor(components: dict, score_total: float) -> None:
    """Backfill a display payload for rows persisted before composite_6factor."""
    if isinstance(components.get("composite_6factor"), dict):
        return
    if not any(key in components for key in ("technical", "momentum", "quality", "growth", "risk_penalty")):
        return

    weights = components.get("weights_used") if isinstance(components.get("weights_used"), dict) else {}
    factors = {
        "technical": _factor_score(components.get("technical")),
        "momentum": _factor_score(components.get("momentum")) or _factor_score(components.get("momentum_trend")),
        "sentiment": _factor_score(components.get("sentiment")),
        "quality": _legacy_fundamental_score(components, "quality"),
        "growth": _legacy_fundamental_score(components, "growth"),
        "risk_penalty": _factor_score(components.get("risk_penalty")) or _factor_score(components.get("risk"), invert=True),
    }
    if not any(v is not None for v in factors.values()):
        return

    factor_payload = {}
    for name, score in factors.items():
        if score is None:
            continue
        weight = float(weights.get(name, weights.get("momentum_trend" if name == "momentum" else "risk" if name == "risk_penalty" else name, 0.0)) or 0.0)
        contribution = -score * weight if name == "risk_penalty" else score * weight
        factor_payload[name] = {
            "score": score,
            "weight": weight,
            "contribution": round(contribution, 4),
            "components": {},
            "confidence": 1.0,
        }

    components["composite_6factor"] = {
        "composite_score": round(float(components.get("total_score", score_total)), 4),
        "regime": str(components.get("regime", "")),
        "weight_profile": str(components.get("weight_profile_used", "")),
        "confidence": 1.0,
        "factors": factor_payload,
    }


def _normalize_ranking(components: dict, score_total: float, eligible: bool, reasons: List[str]):
    """Expose persisted 6-factor ranking rows without recomputing scores."""
    components = dict(components)
    _ensure_composite_6factor(components, score_total)
    composite = components.get("composite_6factor")
    if isinstance(composite, dict):
        composite_score = composite.get("composite_score")
        if isinstance(composite_score, (int, float)):
            score_total = round(float(composite_score), 4)
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

    missing_score_reasons = _missing_required_score_reasons(components)
    if missing_score_reasons:
        eligible = False
        for reason in missing_score_reasons:
            if reason not in reasons:
                reasons.append(reason)

    return components, score_total, eligible, reasons


@router.get("/rankings", response_model=List[RankingRow])
def get_rankings(limit: int = Query(50, le=2000), db: Session = Depends(get_db)):
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
            weight_profile=components.get("weight_profile_used"),
        ))
    result.sort(key=lambda row: row.score_total, reverse=True)
    return result[:limit]


@router.post("/rankings/refresh", response_model=RefreshRankingsResponse)
def refresh_rankings(db: Session = Depends(get_db)):
    """Refresh routine sentiment, then recompute and persist a rankings batch."""
    import asyncio
    import time

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    from trader.ibkr_client import get_ibkr_client
    from trader.ranking import rank_symbols
    from trader.sentiment.factory import refresh_and_store
    from trader.universe import get_verified_universe, seed_universe

    started = time.monotonic()
    sentiment = refresh_and_store()
    sentiment_status = str(sentiment.get("status", "unknown"))
    if sentiment_status not in ("success", "skipped"):
        log_refresh(
            "rankings",
            "error",
            int((time.monotonic() - started) * 1000),
            f"sentiment={sentiment_status} reason={sentiment.get('reason', '')}",
        )
        raise HTTPException(
            status_code=400,
            detail={
                "status": "failed",
                "sentiment_status": sentiment_status,
                "snapshots_written": sentiment.get("snapshots_written", 0),
                "ranked": 0,
                "reason": sentiment.get("reason", "sentiment_refresh_failed"),
            },
        )

    client = get_ibkr_client()
    try:
        client.connect()
    except Exception as exc:
        log_refresh(
            "rankings",
            "error",
            int((time.monotonic() - started) * 1000),
            f"ibkr_unavailable: {exc}",
        )
        raise HTTPException(
            status_code=503,
            detail={
                "status": "failed",
                "sentiment_status": sentiment_status,
                "snapshots_written": sentiment.get("snapshots_written", 0),
                "ranked": 0,
                "reason": f"ibkr_unavailable: {exc}",
            },
        ) from exc

    try:
        seed_universe()
        universe = get_verified_universe(client)
        ranked = rank_symbols(universe, client=client)
    except Exception as exc:
        log_refresh(
            "rankings",
            "error",
            int((time.monotonic() - started) * 1000),
            f"ranking_failed: {exc}",
        )
        raise

    latest_ts = db.query(func.max(SymbolRanking.ts)).scalar()
    duration_ms = int((time.monotonic() - started) * 1000)
    log_refresh(
        "rankings",
        "success",
        duration_ms,
        f"ranked={len(ranked)} sentiment={sentiment_status}",
    )
    return RefreshRankingsResponse(
        status="success",
        sentiment_status=sentiment_status,
        snapshots_written=int(sentiment.get("snapshots_written", 0) or 0),
        ranked=len(ranked),
        latest_ts=str(latest_ts) if latest_ts else None,
    )


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
