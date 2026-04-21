"""Sentiment snapshot routes."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from common.config import get_config
from common.models import SentimentSnapshot
from common.schema import SentimentLlmBudgetOut, SentimentOut
from trader.sentiment import budget as budget_mod
from trader.sentiment.factory import refresh_and_store

router = APIRouter(tags=["sentiment"])


@router.get("/sentiment/latest", response_model=List[SentimentOut])
def latest_sentiment(limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    rows = (
        db.query(SentimentSnapshot)
        .order_by(SentimentSnapshot.id.desc())
        .limit(limit)
        .all()
    )
    return [SentimentOut.model_validate(r) for r in rows]


@router.get("/sentiment/llm-budget", response_model=SentimentLlmBudgetOut)
def llm_budget(db: Session = Depends(get_db)):
    cfg = get_config()
    claude_cfg = cfg.sentiment.claude
    status = budget_mod.get_status(
        db,
        monthly_budget_eur=claude_cfg.monthly_budget_eur,
        daily_budget_fraction=claude_cfg.daily_budget_fraction,
        eur_usd_rate=claude_cfg.eur_usd_rate,
        hard_stop_on_budget=claude_cfg.hard_stop_on_budget,
    )
    return SentimentLlmBudgetOut(
        provider=cfg.sentiment.provider,
        model=claude_cfg.model,
        month_to_date_eur=status.month_to_date_eur,
        today_eur=status.today_eur,
        monthly_cap_eur=status.monthly_cap_eur,
        daily_cap_eur=status.daily_cap_eur,
        remaining_month_eur=status.remaining_month_eur,
        remaining_today_eur=status.remaining_today_eur,
        budget_stopped=status.budget_stopped,
        reason=status.reason,
    )


@router.post("/sentiment/refresh")
def trigger_refresh():
    result = refresh_and_store()
    return {
        "status": result.get("status", "unknown"),
        "snapshots_written": result.get("snapshots_written", 0),
        "reason": result.get("reason", ""),
    }
