"""Pydantic response schemas for the API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class BotStateOut(BaseModel):
    paused: bool
    kill_switch: bool
    options_enabled: bool
    approve_mode: bool
    last_heartbeat: Optional[datetime] = None

    class Config:
        from_attributes = True


class EquitySnapshotOut(BaseModel):
    timestamp: datetime
    net_liquidation: float
    cash: float
    unrealized_pnl: float
    realized_pnl: float
    drawdown_pct: float

    class Config:
        from_attributes = True


class PositionOut(BaseModel):
    symbol: str
    name: Optional[str] = None
    quantity: int
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float
    instrument: str
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    id: int
    intent_id: str
    timestamp: datetime
    symbol: str
    name: Optional[str] = None
    direction: str
    instrument: str
    quantity: int
    order_type: str
    limit_price: Optional[float] = None
    status: str
    ibkr_order_id: Optional[int] = None
    max_loss: float

    class Config:
        from_attributes = True


class FillOut(BaseModel):
    id: int
    order_id: int
    timestamp: datetime
    symbol: str
    name: Optional[str] = None
    quantity: int
    price: float
    commission: float

    class Config:
        from_attributes = True


class SignalOut(BaseModel):
    id: int
    timestamp: datetime
    symbol: str
    name: Optional[str] = None
    score_total: float
    components_json: str
    regime: str
    action: str
    explanation: str

    class Config:
        from_attributes = True


class SentimentOut(BaseModel):
    id: int
    timestamp: datetime
    scope: str
    key: str
    score: float
    summary: str
    sources_json: str

    class Config:
        from_attributes = True


class SentimentLlmBudgetOut(BaseModel):
    provider: str
    model: Optional[str] = None
    month_to_date_eur: float = 0.0
    today_eur: float = 0.0
    monthly_cap_eur: float = 0.0
    daily_cap_eur: float = 0.0
    remaining_month_eur: float = 0.0
    remaining_today_eur: float = 0.0
    budget_stopped: bool = False
    reason: Optional[str] = None


class StateOverview(BaseModel):
    bot: BotStateOut
    equity: Optional[EquitySnapshotOut] = None
    positions: List[PositionOut] = []
    position_count: int = 0
    sentiment_provider: str = "rss_lexicon"
    sentiment_llm_budget: Optional[SentimentLlmBudgetOut] = None


class EventOut(BaseModel):
    id: int
    timestamp: datetime
    level: str
    type: str
    message: str

    class Config:
        from_attributes = True
