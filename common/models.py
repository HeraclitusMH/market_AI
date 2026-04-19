"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase

from common.time import utcnow


class Base(DeclarativeBase):
    pass


class BotState(Base):
    __tablename__ = "bot_state"

    id = Column(Integer, primary_key=True, default=1)
    paused = Column(Boolean, default=False, nullable=False)
    kill_switch = Column(Boolean, default=False, nullable=False)
    options_enabled = Column(Boolean, default=True, nullable=False)
    approve_mode = Column(Boolean, default=True, nullable=False)
    last_heartbeat = Column(DateTime, nullable=True)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=utcnow, nullable=False, index=True)
    net_liquidation = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    drawdown_pct = Column(Float, default=0.0)


class Universe(Base):
    __tablename__ = "universe"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), unique=True, nullable=False, index=True)
    type = Column(String(10), default="STK")  # STK or ETF
    sector = Column(String(50), default="")
    active = Column(Boolean, default=True)
    liquidity_metrics_json = Column(Text, default="{}")


class SentimentSnapshot(Base):
    __tablename__ = "sentiment_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=utcnow, nullable=False, index=True)
    scope = Column(String(20), nullable=False)   # market / sector / ticker
    key = Column(String(50), nullable=False)       # e.g. "US", "Technology", "AAPL"
    score = Column(Float, nullable=False)
    summary = Column(Text, default="")
    sources_json = Column(Text, default="[]")

    __table_args__ = (
        Index("ix_sentiment_snapshots_scope_key_ts", "scope", "key", "timestamp"),
    )


class SignalSnapshot(Base):
    __tablename__ = "signal_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=utcnow, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    score_total = Column(Float, nullable=False)
    components_json = Column(Text, default="{}")
    regime = Column(String(20), default="")
    action = Column(String(20), default="")       # buy / bearish / hold / skip
    explanation = Column(Text, default="")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    intent_id = Column(String(64), unique=True, nullable=False)
    timestamp = Column(DateTime, default=utcnow, nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)       # long / bearish
    instrument = Column(String(30), default="stock")     # stock / debit_spread
    quantity = Column(Integer, default=1)
    order_type = Column(String(10), default="LIMIT")
    limit_price = Column(Float, nullable=True)
    status = Column(String(20), default="pending")       # pending/submitted/filled/cancelled/rejected
    ibkr_order_id = Column(Integer, nullable=True)
    max_loss = Column(Float, default=0.0)
    payload_json = Column(Text, default="{}")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Fill(Base):
    __tablename__ = "fills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime, default=utcnow, nullable=False)
    symbol = Column(String(20), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    commission = Column(Float, default=0.0)
    payload_json = Column(Text, default="{}")


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(40), nullable=False, index=True)
    quantity = Column(Integer, default=0)
    avg_cost = Column(Float, default=0.0)
    market_price = Column(Float, default=0.0)
    market_value = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    instrument = Column(String(30), default="stock")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    intent_id = Column(String(64), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    direction = Column(String(10), nullable=False)
    instrument = Column(String(30), default="stock")
    entry_time = Column(DateTime, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Integer, default=0)
    pnl = Column(Float, nullable=True)
    status = Column(String(20), default="open")   # open / closed
    max_loss = Column(Float, default=0.0)


class EventLog(Base):
    __tablename__ = "events_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=utcnow, nullable=False, index=True)
    level = Column(String(10), default="INFO")
    type = Column(String(50), default="")
    message = Column(Text, default="")
    payload_json = Column(Text, default="{}")


class ContractVerificationCache(Base):
    """IBKR contract verification cache — prevents repeated lookups and ticker hallucination."""
    __tablename__ = "contract_verification_cache"

    symbol = Column(String(20), primary_key=True)
    verified = Column(Boolean, default=False, nullable=False)
    checked_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    reason = Column(Text, nullable=True)
    contract_conid = Column(Integer, nullable=True)
    primary_exchange = Column(String(20), nullable=True)


class SymbolRanking(Base):
    """Per-cycle sentiment+eligibility ranking for all universe symbols."""
    __tablename__ = "symbol_rankings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=utcnow, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    score_total = Column(Float, nullable=False)
    components_json = Column(Text, default="{}")  # market/sector/ticker weights + ages
    eligible = Column(Boolean, default=True)
    reasons_json = Column(Text, default="[]")


class TradePlan(Base):
    """Options trade plan produced by planner; may or may not become an order."""
    __tablename__ = "trade_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=utcnow, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    bias = Column(String(10), nullable=False)         # bullish / bearish
    strategy = Column(String(30), nullable=False)     # bull_call_debit_spread / bear_put_debit_spread
    expiry = Column(String(8), nullable=True)         # YYYYMMDD
    dte = Column(Integer, nullable=True)
    legs_json = Column(Text, default="{}")
    pricing_json = Column(Text, default="{}")
    rationale_json = Column(Text, default="{}")
    status = Column(String(20), default="proposed")   # proposed|approved|submitted|skipped
    skip_reason = Column(Text, nullable=True)


class SentimentLlmItem(Base):
    """Persistent dedup record for RSS items sent to the LLM."""
    __tablename__ = "sentiment_llm_items"

    id = Column(String(32), primary_key=True)           # sha256 prefix, hex
    first_seen_at = Column(DateTime, default=utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    source = Column(String(200), default="")
    title = Column(Text, default="")
    url = Column(Text, nullable=True)
    processed_at = Column(DateTime, nullable=True, index=True)


class SentimentLlmUsage(Base):
    """Per-call usage + cost record for the Anthropic sentiment extractor."""
    __tablename__ = "sentiment_llm_usage"

    id = Column(String(36), primary_key=True)           # uuid4 hex
    ts = Column(DateTime, default=utcnow, nullable=False, index=True)
    provider = Column(String(30), default="anthropic")
    model = Column(String(100), default="")
    request_kind = Column(String(50), default="sentiment_extraction")
    input_items_count = Column(Integer, default=0)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    cost_usd_est = Column(Float, default=0.0)
    cost_eur_est = Column(Float, default=0.0)
    anthropic_request_id = Column(String(100), nullable=True)
    status = Column(String(20), default="success")      # success | failed
    error_type = Column(String(60), nullable=True)
    error_message = Column(Text, nullable=True)
