"""GET /api/v1/positions"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import Position, SecurityMaster
from common.schema import PositionOut

router = APIRouter(tags=["v1"])


@router.get("/positions", response_model=List[PositionOut])
def list_positions(db: Session = Depends(get_db)):
    rows = db.query(Position).all()
    symbols = [r.symbol for r in rows]
    names = (
        {r.symbol: r.name for r in db.query(SecurityMaster.symbol, SecurityMaster.name).filter(SecurityMaster.symbol.in_(symbols)).all()}
        if symbols else {}
    )
    return [PositionOut.model_validate(r).model_copy(update={"name": names.get(r.symbol)}) for r in rows]
