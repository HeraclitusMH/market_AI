"""Orders, fills, positions routes."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import Order, Fill, Position
from common.schema import OrderOut, FillOut, PositionOut

router = APIRouter(tags=["trades"])


@router.get("/orders", response_model=List[OrderOut])
def list_orders(limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    rows = db.query(Order).order_by(Order.id.desc()).limit(limit).all()
    return [OrderOut.model_validate(r) for r in rows]


@router.get("/fills", response_model=List[FillOut])
def list_fills(limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    rows = db.query(Fill).order_by(Fill.id.desc()).limit(limit).all()
    return [FillOut.model_validate(r) for r in rows]


@router.get("/positions", response_model=List[PositionOut])
def list_positions(db: Session = Depends(get_db)):
    return [PositionOut.model_validate(r) for r in db.query(Position).all()]
