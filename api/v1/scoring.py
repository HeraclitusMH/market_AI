"""Scoring metadata endpoints — weights, parameter docs, refresh history."""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_db
from common.config import get_config
from common.models import RefreshLog, RegimeSnapshot

router = APIRouter(tags=["v1"])


# ─────────────────────────────────────────────────────────────────────────────
# Static parameter documentation. Keep wording aligned with composite_scorer/factors/*.py
# ─────────────────────────────────────────────────────────────────────────────

FACTOR_ORDER: List[str] = [
    "technical", "momentum", "sentiment", "quality", "growth", "risk_penalty",
]

PARAMETER_DOCS: Dict[str, dict] = {
    "technical": {
        "label": "Technical",
        "what": (
            "Captures price-action setup quality from daily bars: trend alignment "
            "(EMA stack), momentum oscillators (RSI, MACD), and pullback location. "
            "High scores point to symbols already trending and mid-pullback."
        ),
        "how": (
            "trader/composite_scorer/factors/technical.py — combines EMA20/50/200 stack, "
            "RSI(14), MACD histogram, and distance from EMA20 into a 0-100 score."
        ),
        "source": "IBKR daily bars (1Y window)",
        "subscores": [
            "EMA stack alignment (above 20 > 50 > 200)",
            "RSI(14) zone",
            "MACD histogram sign + slope",
            "Distance from EMA20 (pullback proximity)",
        ],
        "score_min": 0,
        "score_max": 100,
        "inverted": False,
        "bands": [
            ["80-100", "Excellent setup"],
            ["60-79", "Good"],
            ["40-59", "Average"],
            ["20-39", "Below average"],
            ["0-19", "Weak / broken"],
        ],
        "refresh_via": "rankings",
        "refresh_note": (
            "Recomputed every Rankings refresh (no separate cache). "
            "Triggers from IBKR bar pulls."
        ),
    },
    "momentum": {
        "label": "Momentum",
        "what": (
            "Persistence of price gains over multiple horizons. Rewards symbols "
            "outperforming over 1m/3m/6m without excessive volatility."
        ),
        "how": (
            "trader/composite_scorer/factors/momentum.py — multi-horizon returns "
            "(21/63/126d) blended and risk-adjusted."
        ),
        "source": "IBKR daily bars",
        "subscores": [
            "21-day return",
            "63-day return",
            "126-day return",
            "Volatility-adjustment overlay",
        ],
        "score_min": 0,
        "score_max": 100,
        "inverted": False,
        "bands": [
            ["80-100", "Strong sustained move"],
            ["60-79", "Healthy momentum"],
            ["40-59", "Mixed"],
            ["20-39", "Decelerating"],
            ["0-19", "Negative momentum"],
        ],
        "refresh_via": "rankings",
        "refresh_note": "Recomputed every Rankings refresh from IBKR bars.",
    },
    "sentiment": {
        "label": "Sentiment",
        "what": (
            "News-flow signal from market, sector, and ticker scopes. Captures "
            "how the broader narrative is leaning right now."
        ),
        "how": (
            "trader/composite_scorer/factors/sentiment.py — weighted blend of "
            "market/sector/ticker SentimentSnapshot rows (latest per scope)."
        ),
        "source": "SentimentSnapshot DB (RSS lexicon, Claude routine, or LLM provider)",
        "subscores": [
            "Market scope (US)",
            "Sector scope (GICS sector)",
            "Ticker scope (symbol-level mentions)",
        ],
        "score_min": 0,
        "score_max": 100,
        "inverted": False,
        "bands": [
            ["80-100", "Strongly bullish narrative"],
            ["60-79", "Constructive"],
            ["40-59", "Neutral"],
            ["20-39", "Cautious"],
            ["0-19", "Bearish narrative"],
        ],
        "refresh_via": "sentiment",
        "refresh_note": (
            "Refresh independently via the Sentiment refresh button. "
            "Pipeline: trader/sentiment/factory.refresh_and_store()."
        ),
    },
    "quality": {
        "label": "Quality",
        "what": (
            "Balance-sheet and operational health. Higher quality means stronger "
            "margins, lower leverage, and steadier earnings — less likely to break "
            "during a multi-day swing hold."
        ),
        "how": (
            "trader/composite_scorer/factors/fundamental.py:QualityFactor — derived "
            "from yfinance fundamentals (margins, ROE, leverage ratios)."
        ),
        "source": "FundamentalSnapshot DB (yfinance, 7-day cache)",
        "subscores": [
            "Operating / net margin",
            "Return on equity",
            "Debt / equity",
            "Earnings stability",
        ],
        "score_min": 0,
        "score_max": 100,
        "inverted": False,
        "bands": [
            ["80-100", "High quality"],
            ["60-79", "Solid"],
            ["40-59", "Mixed"],
            ["20-39", "Weak"],
            ["0-19", "Stressed"],
        ],
        "refresh_via": "fundamentals",
        "refresh_note": (
            "Refresh via the Fundamentals refresh button. Same fetch updates Growth too."
        ),
    },
    "growth": {
        "label": "Growth",
        "what": (
            "Top- and bottom-line growth trajectory. Rewards companies expanding "
            "revenue and earnings against their own history and peers."
        ),
        "how": (
            "trader/composite_scorer/factors/fundamental.py:GrowthFactor — "
            "yfinance revenue / EPS growth metrics, sector-relative."
        ),
        "source": "FundamentalSnapshot DB (yfinance, 7-day cache)",
        "subscores": [
            "Revenue growth (TTM)",
            "EPS growth (TTM)",
            "Forward growth estimates",
        ],
        "score_min": 0,
        "score_max": 100,
        "inverted": False,
        "bands": [
            ["80-100", "Strong growth"],
            ["60-79", "Above average"],
            ["40-59", "Average"],
            ["20-39", "Slowing"],
            ["0-19", "Contracting"],
        ],
        "refresh_via": "fundamentals",
        "refresh_note": (
            "Refresh via the Fundamentals refresh button. Same fetch updates Quality too."
        ),
    },
    "risk_penalty": {
        "label": "Risk Penalty",
        "what": (
            "Subtractive penalty for elevated volatility and drawdown risk. "
            "Higher value = MORE risk = larger deduction from composite."
        ),
        "how": (
            "trader/composite_scorer/factors/risk.py — realized volatility, ATR, "
            "drawdown depth. Optionally folds in implied vol if available."
        ),
        "source": "IBKR daily bars (+ optional implied vol)",
        "subscores": [
            "Realized volatility (20d / 60d)",
            "ATR(14) % of price",
            "Recent drawdown depth",
        ],
        "score_min": 0,
        "score_max": 100,
        "inverted": True,
        "bands": [
            ["0-19", "Low risk (small penalty)"],
            ["20-39", "Modest risk"],
            ["40-59", "Average"],
            ["60-79", "Elevated risk"],
            ["80-100", "High risk (large penalty)"],
        ],
        "refresh_via": "rankings",
        "refresh_note": "Recomputed every Rankings refresh from IBKR bars.",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class WeightProfileOut(BaseModel):
    technical: float
    momentum: float
    sentiment: float
    quality: float
    growth: float
    risk_penalty: float


class ScoringWeightsResponse(BaseModel):
    profiles: Dict[str, WeightProfileOut]
    active_profile: str
    active_weights: WeightProfileOut
    regime_level: str


class ParameterBand(BaseModel):
    range: str
    label: str


class ParameterDoc(BaseModel):
    key: str
    label: str
    weight: float
    what: str
    how: str
    source: str
    subscores: List[str]
    score_min: int
    score_max: int
    inverted: bool
    bands: List[ParameterBand]
    refresh_via: str
    refresh_note: str


class ScoringDocsResponse(BaseModel):
    parameters: List[ParameterDoc]
    active_profile: str
    regime_level: str


class RefreshHistoryEvent(BaseModel):
    id: int
    timestamp: str
    action: str
    status: str
    duration_ms: int
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _current_regime_level(db: Session) -> str:
    snap = (
        db.query(RegimeSnapshot)
        .order_by(RegimeSnapshot.timestamp.desc())
        .first()
    )
    if snap is None or not snap.level:
        return "risk_reduced"
    return str(snap.level)


def _active_profile_name(regime_level: str) -> str:
    return "aggressive_swing" if regime_level == "risk_on" else "defensive_swing"


def _profile_to_dict(profile) -> Dict[str, float]:
    if hasattr(profile, "model_dump"):
        return {k: float(v) for k, v in profile.model_dump().items()}
    return {k: float(v) for k, v in dict(profile).items()}


@router.get("/scoring/weights", response_model=ScoringWeightsResponse)
def get_scoring_weights(db: Session = Depends(get_db)) -> ScoringWeightsResponse:
    cfg = get_config()
    regime_level = _current_regime_level(db)
    active_name = _active_profile_name(regime_level)

    profiles_out: Dict[str, WeightProfileOut] = {
        name: WeightProfileOut(**_profile_to_dict(profile))
        for name, profile in cfg.ranking.weight_profiles.items()
    }
    active = profiles_out.get(active_name) or next(iter(profiles_out.values()))
    return ScoringWeightsResponse(
        profiles=profiles_out,
        active_profile=active_name,
        active_weights=active,
        regime_level=regime_level,
    )


@router.get("/scoring/docs", response_model=ScoringDocsResponse)
def get_scoring_docs(db: Session = Depends(get_db)) -> ScoringDocsResponse:
    cfg = get_config()
    regime_level = _current_regime_level(db)
    active_name = _active_profile_name(regime_level)
    active = cfg.ranking.weight_profiles.get(active_name)
    weights = _profile_to_dict(active) if active is not None else {}

    params = [
        ParameterDoc(
            key=key,
            label=PARAMETER_DOCS[key]["label"],
            weight=float(weights.get(key, 0.0)),
            what=PARAMETER_DOCS[key]["what"],
            how=PARAMETER_DOCS[key]["how"],
            source=PARAMETER_DOCS[key]["source"],
            subscores=PARAMETER_DOCS[key]["subscores"],
            score_min=PARAMETER_DOCS[key]["score_min"],
            score_max=PARAMETER_DOCS[key]["score_max"],
            inverted=PARAMETER_DOCS[key]["inverted"],
            bands=[
                ParameterBand(range=r, label=lbl)
                for r, lbl in PARAMETER_DOCS[key]["bands"]
            ],
            refresh_via=PARAMETER_DOCS[key]["refresh_via"],
            refresh_note=PARAMETER_DOCS[key]["refresh_note"],
        )
        for key in FACTOR_ORDER
    ]
    return ScoringDocsResponse(
        parameters=params,
        active_profile=active_name,
        regime_level=regime_level,
    )


@router.get("/scoring/refresh-history", response_model=List[RefreshHistoryEvent])
def get_refresh_history(
    limit: int = 20,
    action: Optional[str] = None,
    db: Session = Depends(get_db),
) -> List[RefreshHistoryEvent]:
    q = db.query(RefreshLog).order_by(RefreshLog.id.desc())
    if action:
        q = q.filter(RefreshLog.action == action)
    rows = q.limit(max(1, min(limit, 200))).all()
    return [
        RefreshHistoryEvent(
            id=r.id,
            timestamp=r.timestamp.isoformat() if r.timestamp else "",
            action=r.action,
            status=r.status,
            duration_ms=int(r.duration_ms or 0),
            message=r.message or "",
        )
        for r in rows
    ]
