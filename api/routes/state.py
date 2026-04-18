"""Bot state overview route."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_db
from common.config import get_config
from common.models import BotState, EquitySnapshot, Position
from common.schema import (
    BotStateOut, EquitySnapshotOut, PositionOut, SentimentLlmBudgetOut, StateOverview,
)
from trader.sentiment import budget as budget_mod

router = APIRouter(tags=["state"])


def _llm_budget_payload(db: Session) -> SentimentLlmBudgetOut:
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


@router.get("/state", response_model=StateOverview)
def get_state(db: Session = Depends(get_db)):
    bot = db.query(BotState).first()
    equity = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
    positions = db.query(Position).all()

    cfg = get_config()
    return StateOverview(
        bot=BotStateOut.model_validate(bot) if bot else BotStateOut(
            paused=False, kill_switch=False, options_enabled=True, approve_mode=True,
        ),
        equity=EquitySnapshotOut.model_validate(equity) if equity else None,
        positions=[PositionOut.model_validate(p) for p in positions],
        position_count=len(positions),
        sentiment_provider=cfg.sentiment.provider,
        sentiment_llm_budget=_llm_budget_payload(db),
    )
