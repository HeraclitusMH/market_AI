"""GET /api/v1/orders and /api/v1/fills"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import Fill, Order, SecurityMaster
from common.schema import FillOut, OrderOut

router = APIRouter(tags=["v1"])


def _names(db: Session, symbols: List[str]) -> dict:
    if not symbols:
        return {}
    return {r.symbol: r.name for r in db.query(SecurityMaster.symbol, SecurityMaster.name).filter(SecurityMaster.symbol.in_(symbols)).all()}


@router.get("/orders", response_model=List[OrderOut])
def list_orders(limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    rows = db.query(Order).order_by(Order.id.desc()).limit(limit).all()
    nm = _names(db, [r.symbol for r in rows])
    return [OrderOut.model_validate(r).model_copy(update={"name": nm.get(r.symbol)}) for r in rows]


@router.get("/fills", response_model=List[FillOut])
def list_fills(limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    rows = db.query(Fill).order_by(Fill.id.desc()).limit(limit).all()
    nm = _names(db, [r.symbol for r in rows])
    return [FillOut.model_validate(r).model_copy(update={"name": nm.get(r.symbol)}) for r in rows]
