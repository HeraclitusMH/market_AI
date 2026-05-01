"""Regime history endpoints."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter

from common.config import load_config
from common.db import get_db

router = APIRouter(prefix="/api/v1/regime", tags=["regime"])


@router.get("/current")
def get_current_regime():
    with get_db() as session:
        from common.models import RegimeSnapshot
        snap = (
            session.query(RegimeSnapshot)
            .order_by(RegimeSnapshot.timestamp.desc())
            .first()
        )
    if not snap:
        return {"level": "unknown", "message": "No regime evaluation yet"}
    cfg = load_config()
    effects_by_level = {
        "risk_on": cfg.regime.effects.risk_on,
        "risk_reduced": cfg.regime.effects.risk_reduced,
        "risk_off": cfg.regime.effects.risk_off,
    }
    effects = effects_by_level.get(snap.level)
    components = None
    if snap.components_json:
        try:
            components = json.loads(snap.components_json)
        except Exception:
            pass
    return {
        "level": snap.level,
        "composite_score": snap.composite_score,
        "transition": snap.transition,
        "pillars": {
            "trend": snap.trend_score,
            "breadth": snap.breadth_score,
            "volatility": snap.volatility_score,
            "credit_stress": snap.credit_stress_score,
        },
        "hysteresis_active": snap.hysteresis_active,
        "data_quality": snap.data_quality,
        "timestamp": snap.timestamp.isoformat(),
        "effects": None if effects is None else {
            "allows_new_equity_entries": effects.allows_new_equity_entries,
            "allows_new_options_entries": effects.allows_new_options_entries,
            "sizing_factor": effects.sizing_factor,
            "stop_tightening_factor": effects.stop_tightening_factor,
            "score_threshold_adjustment": effects.score_threshold_adjustment,
        },
        "components": components,
    }


@router.get("/history")
def get_regime_history(days: int = 30):
    with get_db() as session:
        from common.models import RegimeSnapshot
        cutoff = datetime.utcnow() - timedelta(days=days)
        snaps = (
            session.query(RegimeSnapshot)
            .filter(RegimeSnapshot.timestamp >= cutoff)
            .order_by(RegimeSnapshot.timestamp.asc())
            .all()
        )
    return [
        {
            "timestamp": s.timestamp.isoformat(),
            "level": s.level,
            "composite_score": s.composite_score,
            "trend": s.trend_score,
            "breadth": s.breadth_score,
            "volatility": s.volatility_score,
            "credit_stress": s.credit_stress_score,
            "transition": s.transition,
        }
        for s in snaps
    ]
