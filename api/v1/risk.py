"""GET /api/v1/risk"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_db
from common.config import get_config
from common.models import BotState, EquitySnapshot, Position
from common.schema import BotStateOut, EquitySnapshotOut

router = APIRouter(tags=["v1"])


class RiskConfigOut(BaseModel):
    max_drawdown_pct: float
    max_risk_per_trade_pct: float
    max_positions: int
    require_positive_cash: bool


class RiskResponse(BaseModel):
    current: Optional[EquitySnapshotOut] = None
    history: List[EquitySnapshotOut] = []
    bot: BotStateOut
    risk_config: RiskConfigOut
    positions_used: int = 0
    positions_max: int = 5


def _default_bot() -> BotStateOut:
    return BotStateOut(paused=False, kill_switch=False, options_enabled=True, approve_mode=True)


@router.get("/risk", response_model=RiskResponse)
def get_risk(db: Session = Depends(get_db)):
    cfg = get_config()
    bot = db.query(BotState).first()
    equity = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
    history = db.query(EquitySnapshot).order_by(EquitySnapshot.id.asc()).all()
    positions_used = db.query(Position).count()
    return RiskResponse(
        current=EquitySnapshotOut.model_validate(equity) if equity else None,
        history=[EquitySnapshotOut.model_validate(e) for e in history],
        bot=BotStateOut.model_validate(bot) if bot else _default_bot(),
        risk_config=RiskConfigOut(
            max_drawdown_pct=cfg.risk.max_drawdown_pct,
            max_risk_per_trade_pct=cfg.risk.max_risk_per_trade_pct,
            max_positions=cfg.risk.max_positions,
            require_positive_cash=cfg.risk.require_positive_cash,
        ),
        positions_used=positions_used,
        positions_max=cfg.risk.max_positions,
    )
