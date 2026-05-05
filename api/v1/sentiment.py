"""GET /api/v1/sentiment + POST /api/v1/sentiment/refresh"""
from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_db
from api.v1._refresh_log import log_refresh
from common.config import get_config
from common.models import SentimentSnapshot
from common.schema import SentimentLlmBudgetOut, SentimentOut
from trader.sentiment import budget as budget_mod
from trader.sentiment.factory import refresh_and_store

router = APIRouter(tags=["v1"])


class SentimentPoint(BaseModel):
    timestamp: str
    score: float


class Headline(BaseModel):
    title: str
    score: float


class SentimentResponse(BaseModel):
    market: Optional[SentimentOut] = None
    sectors: List[SentimentOut] = []
    tickers: List[SentimentOut] = []
    headlines: List[Headline] = []
    history: List[SentimentPoint] = []
    budget: SentimentLlmBudgetOut
    provider: str


def _build_budget(db: Session) -> SentimentLlmBudgetOut:
    cfg = get_config()
    c = cfg.sentiment.claude
    status = budget_mod.get_status(
        db,
        monthly_budget_eur=c.monthly_budget_eur,
        daily_budget_fraction=c.daily_budget_fraction,
        eur_usd_rate=c.eur_usd_rate,
        hard_stop_on_budget=c.hard_stop_on_budget,
    )
    return SentimentLlmBudgetOut(
        provider=cfg.sentiment.provider,
        model=c.model,
        month_to_date_eur=status.month_to_date_eur,
        today_eur=status.today_eur,
        monthly_cap_eur=status.monthly_cap_eur,
        daily_cap_eur=status.daily_cap_eur,
        remaining_month_eur=status.remaining_month_eur,
        remaining_today_eur=status.remaining_today_eur,
        budget_stopped=status.budget_stopped,
        reason=status.reason,
    )


@router.get("/sentiment", response_model=SentimentResponse)
def get_sentiment(db: Session = Depends(get_db)):
    cfg = get_config()
    rows = (
        db.query(SentimentSnapshot)
        .order_by(SentimentSnapshot.id.desc())
        .limit(200)
        .all()
    )

    latest: dict = {}
    for r in rows:
        latest.setdefault((r.scope, r.key), r)

    market_rows = [r for (s, _), r in latest.items() if s == "market"]
    sector_rows = sorted(
        [r for (s, _), r in latest.items() if s == "sector"],
        key=lambda r: abs(r.score),
        reverse=True,
    )
    ticker_rows = sorted(
        [r for (s, _), r in latest.items() if s == "ticker"],
        key=lambda r: abs(r.score),
        reverse=True,
    )[:20]

    headlines: List[Headline] = []
    if market_rows and market_rows[0].sources_json:
        try:
            items = json.loads(market_rows[0].sources_json)
            if items and isinstance(items[0], dict):
                headlines = [
                    Headline(title=i.get("title", ""), score=float(i.get("score", 0)))
                    for i in items
                ]
        except Exception:
            pass

    history = [
        SentimentPoint(timestamp=str(r.timestamp), score=r.score)
        for r in reversed(rows)
        if r.scope == "market"
    ]

    return SentimentResponse(
        market=SentimentOut.model_validate(market_rows[0]) if market_rows else None,
        sectors=[SentimentOut.model_validate(r) for r in sector_rows],
        tickers=[SentimentOut.model_validate(r) for r in ticker_rows],
        headlines=headlines,
        history=history,
        budget=_build_budget(db),
        provider=cfg.sentiment.provider,
    )


@router.post("/sentiment/refresh")
def refresh_sentiment():
    import time

    started = time.monotonic()
    try:
        result = refresh_and_store()
    except Exception as exc:
        log_refresh("sentiment", "error", int((time.monotonic() - started) * 1000), str(exc))
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    status = str(result.get("status", "unknown"))
    snapshots_written = int(result.get("snapshots_written", 0) or 0)
    reason = str(result.get("reason", ""))
    log_status = "success" if status in ("success", "skipped") else "error"
    message = f"snapshots={snapshots_written}" + (f" reason={reason}" if reason else "")
    log_refresh("sentiment", log_status, duration_ms, message)
    return {
        "status": status,
        "snapshots_written": snapshots_written,
        "reason": reason,
    }
