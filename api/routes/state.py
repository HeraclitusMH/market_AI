"""Bot state overview route."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import BotState, EquitySnapshot, Position
from common.schema import (
    BotStateOut, EquitySnapshotOut, PositionOut, StateOverview,
)

router = APIRouter(tags=["state"])


@router.get("/state", response_model=StateOverview)
def get_state(db: Session = Depends(get_db)):
    bot = db.query(BotState).first()
    equity = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
    positions = db.query(Position).all()

    return StateOverview(
        bot=BotStateOut.model_validate(bot) if bot else BotStateOut(
            paused=False, kill_switch=False, options_enabled=True, approve_mode=True,
        ),
        equity=EquitySnapshotOut.model_validate(equity) if equity else None,
        positions=[PositionOut.model_validate(p) for p in positions],
        position_count=len(positions),
    )
