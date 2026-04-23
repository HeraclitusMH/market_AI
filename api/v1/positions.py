"""GET /api/v1/positions"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import Position
from common.schema import PositionOut

router = APIRouter(tags=["v1"])


@router.get("/positions", response_model=List[PositionOut])
def list_positions(db: Session = Depends(get_db)):
    rows = db.query(Position).all()
    return [PositionOut.model_validate(r) for r in rows]
