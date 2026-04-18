"""Initial schema — all 10 tables.

Revision ID: 0001
Revises:
Create Date: 2026-04-18 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("kill_switch", sa.Boolean(), nullable=False),
        sa.Column("options_enabled", sa.Boolean(), nullable=False),
        sa.Column("approve_mode", sa.Boolean(), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "equity_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("net_liquidation", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("drawdown_pct", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equity_snapshots_timestamp", "equity_snapshots", ["timestamp"])

    op.create_table(
        "universe",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("type", sa.String(10), nullable=True),
        sa.Column("sector", sa.String(50), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("liquidity_metrics_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol"),
    )
    op.create_index("ix_universe_symbol", "universe", ["symbol"])

    op.create_table(
        "sentiment_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("sources_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sentiment_snapshots_timestamp", "sentiment_snapshots", ["timestamp"])

    op.create_table(
        "signal_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("score_total", sa.Float(), nullable=False),
        sa.Column("components_json", sa.Text(), nullable=True),
        sa.Column("regime", sa.String(20), nullable=True),
        sa.Column("action", sa.String(20), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signal_snapshots_timestamp", "signal_snapshots", ["timestamp"])
    op.create_index("ix_signal_snapshots_symbol", "signal_snapshots", ["symbol"])

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("intent_id", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("instrument", sa.String(30), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("order_type", sa.String(10), nullable=True),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("ibkr_order_id", sa.Integer(), nullable=True),
        sa.Column("max_loss", sa.Float(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("intent_id"),
    )
    op.create_index("ix_orders_timestamp", "orders", ["timestamp"])

    op.create_table(
        "fills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("commission", sa.Float(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fills_order_id", "fills", ["order_id"])

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("avg_cost", sa.Float(), nullable=True),
        sa.Column("market_price", sa.Float(), nullable=True),
        sa.Column("market_value", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("instrument", sa.String(30), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_positions_symbol", "positions", ["symbol"])

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("intent_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("instrument", sa.String(30), nullable=True),
        sa.Column("entry_time", sa.DateTime(), nullable=True),
        sa.Column("exit_time", sa.DateTime(), nullable=True),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("max_loss", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trades_symbol", "trades", ["symbol"])

    op.create_table(
        "events_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("level", sa.String(10), nullable=True),
        sa.Column("type", sa.String(50), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_log_timestamp", "events_log", ["timestamp"])


def downgrade() -> None:
    op.drop_table("events_log")
    op.drop_table("trades")
    op.drop_table("positions")
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_table("signal_snapshots")
    op.drop_table("sentiment_snapshots")
    op.drop_table("universe")
    op.drop_table("equity_snapshots")
    op.drop_table("bot_state")
