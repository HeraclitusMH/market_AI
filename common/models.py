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
    portfolio_id = Column(String(30), default="", nullable=False, index=True)
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
    portfolio_id = Column(String(30), default="", nullable=False, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    intent_id = Column(String(64), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    direction = Column(String(10), nullable=False)
    instrument = Column(String(30), default="stock")
    portfolio_id = Column(String(30), default="", nullable=False, index=True)
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


class FundamentalSnapshot(Base):
    """Cached IBKR fundamental data parsed into scoring metrics."""
    __tablename__ = "fundamental_snapshots"

    symbol = Column(String(20), primary_key=True)
    ts = Column(DateTime, default=utcnow, nullable=False, index=True)
    report_type = Column(String(30), nullable=False, default="ReportSnapshot")
    metrics_json = Column(Text, default="{}")
    raw_xml = Column(Text, default="")
    status = Column(String(20), nullable=False, default="ok")
    reason = Column(Text, nullable=True)


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


class SecurityMaster(Base):
    """Canonical registry of US-listed securities eligible for trading."""
    __tablename__ = "security_master"

    symbol = Column(String(20), primary_key=True)
    name = Column(Text, nullable=False)
    exchange = Column(String(20), nullable=False)
    security_type = Column(String(10), nullable=False, default="STK")
    currency = Column(String(10), nullable=False, default="USD")
    active = Column(Boolean, nullable=False, default=True)
    market_cap = Column(Float, nullable=True)
    avg_dollar_volume_20d = Column(Float, nullable=True)
    options_eligible = Column(Boolean, nullable=False, default=False)
    ibkr_conid = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_security_master_exchange_active", "exchange", "active"),
        Index("ix_security_master_options_eligible", "options_eligible"),
    )


class SecurityAlias(Base):
    """Normalized name aliases that map company mentions to symbols."""
    __tablename__ = "security_alias"

    alias = Column(String(200), primary_key=True)       # normalized key (lowercase)
    symbol = Column(String(20), nullable=False, index=True)
    alias_type = Column(String(30), nullable=False)     # canonical|normalized_name|short_name|symbol|manual
    priority = Column(Integer, nullable=False, default=100)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class RssEntityMatch(Base):
    """Audit log: every company mention from RSS that was run through the matcher."""
    __tablename__ = "rss_entity_matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(String(64), nullable=False, index=True)
    company_input = Column(Text, nullable=False)
    normalized_input = Column(Text, nullable=True)
    symbol = Column(String(20), nullable=True)
    match_type = Column(String(30), nullable=True)      # exact_alias|fuzzy|unmatched|ambiguous
    match_score = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class TradeManagement(Base):
    """Lifecycle state for each open position used by the exit manager.

    One row per open position. Created on order placement, deleted on full close.
    """
    __tablename__ = "trade_management"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    portfolio_id = Column(String(30), nullable=False)     # "equity_swing" | "options_swing"
    instrument_type = Column(String(20), nullable=False)  # "equity" | "debit_spread"

    # Entry metadata (set once at open)
    entry_price = Column(Float, nullable=False)           # avg fill / debit paid
    entry_date = Column(DateTime, nullable=False)
    entry_atr = Column(Float, nullable=True)              # ATR14 at entry (equity)
    entry_score = Column(Float, nullable=True)            # composite score at entry
    entry_regime = Column(String(20), nullable=True)      # "risk_on" | "risk_off"
    direction = Column(String(10), nullable=False)        # "long" | "short"
    quantity = Column(Integer, nullable=False)            # original quantity
    current_quantity = Column(Integer, nullable=False)    # after partial exits

    # Risk parameters
    initial_stop = Column(Float, nullable=False)
    current_stop = Column(Float, nullable=False)
    risk_per_share = Column(Float, nullable=False)        # entry_price - initial_stop (1R)

    # Tracking (updated every cycle)
    highest_price_since_entry = Column(Float, nullable=True)
    lowest_price_since_entry = Column(Float, nullable=True)
    current_r_multiple = Column(Float, nullable=True)
    trailing_activated = Column(Boolean, default=False)
    partial_profit_taken = Column(Boolean, default=False)
    days_held = Column(Integer, default=0)
    consecutive_below_threshold = Column(Integer, default=0)

    # Options-specific fields
    entry_iv = Column(Float, nullable=True)
    entry_net_delta = Column(Float, nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    long_strike = Column(Float, nullable=True)
    short_strike = Column(Float, nullable=True)
    spread_width = Column(Float, nullable=True)
    max_profit = Column(Float, nullable=True)
    max_loss = Column(Float, nullable=True)

    # Linkage
    intent_id = Column(String(64), nullable=True, index=True)
    order_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class RegimeSnapshot(Base):
    """Persisted regime evaluation result for history and restart recovery."""
    __tablename__ = "regime_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=utcnow, index=True)
    level = Column(String(20), nullable=False)
    composite_score = Column(Float, nullable=False)
    previous_level = Column(String(20), nullable=True)
    transition = Column(String(20), nullable=True)
    trend_score = Column(Float, nullable=True)
    breadth_score = Column(Float, nullable=True)
    volatility_score = Column(Float, nullable=True)
    credit_stress_score = Column(Float, nullable=True)
    raw_suggested_level = Column(String(20), nullable=True)
    consecutive_confirmations = Column(Integer, default=0)
    cycles_in_current_state = Column(Integer, default=0)
    hysteresis_active = Column(Boolean, default=False)
    components_json = Column(Text, nullable=True)
    data_quality = Column(String(20), default="full")
    warnings_json = Column(Text, nullable=True)
