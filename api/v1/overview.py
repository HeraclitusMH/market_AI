"""GET /api/v1/overview — full dashboard snapshot."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_db
from common.config import get_config
from common.models import BotState, EquitySnapshot, EventLog, Position
from common.schema import (
    BotStateOut, EquitySnapshotOut, EventOut, PositionOut, SentimentLlmBudgetOut,
)
from trader.sentiment import budget as budget_mod

router = APIRouter(tags=["v1"])


class OverviewResponse(BaseModel):
    bot: BotStateOut
    equity: Optional[EquitySnapshotOut] = None
    equity_history: List[EquitySnapshotOut] = []
    positions: List[PositionOut] = []
    position_count: int = 0
    sentiment_provider: str = "rss_lexicon"
    sentiment_llm_budget: Optional[SentimentLlmBudgetOut] = None
    recent_events: List[EventOut] = []


def _default_bot() -> BotStateOut:
    return BotStateOut(paused=False, kill_switch=False, options_enabled=True, approve_mode=True)


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


@router.get("/overview", response_model=OverviewResponse)
def get_overview(db: Session = Depends(get_db)):
    cfg = get_config()
    bot = db.query(BotState).first()
    equity = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
    equity_history = db.query(EquitySnapshot).order_by(EquitySnapshot.id.asc()).all()
    positions = db.query(Position).all()
    recent_events = db.query(EventLog).order_by(EventLog.id.desc()).limit(20).all()
    return OverviewResponse(
        bot=BotStateOut.model_validate(bot) if bot else _default_bot(),
        equity=EquitySnapshotOut.model_validate(equity) if equity else None,
        equity_history=[EquitySnapshotOut.model_validate(e) for e in equity_history],
        positions=[PositionOut.model_validate(p) for p in positions],
        position_count=len(positions),
        sentiment_provider=cfg.sentiment.provider,
        sentiment_llm_budget=_build_budget(db),
        recent_events=[EventOut.model_validate(e) for e in recent_events],
    )
