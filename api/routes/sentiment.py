"""Sentiment snapshot routes."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import SentimentSnapshot
from common.schema import SentimentOut

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
