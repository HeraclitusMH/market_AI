"""Signal snapshot routes."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import SignalSnapshot
from common.schema import SignalOut

router = APIRouter(tags=["signals"])


@router.get("/signals/latest", response_model=List[SignalOut])
def latest_signals(limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    rows = (
        db.query(SignalSnapshot)
        .order_by(SignalSnapshot.id.desc())
        .limit(limit)
        .all()
    )
    return [SignalOut.model_validate(r) for r in rows]
