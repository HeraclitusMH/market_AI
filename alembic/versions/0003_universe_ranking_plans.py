"""Contract verification cache, symbol rankings, and trade plans tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-18 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contract_verification_cache",
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("contract_conid", sa.Integer(), nullable=True),
        sa.Column("primary_exchange", sa.String(20), nullable=True),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_index(
        "ix_contract_verification_cache_checked_at",
        "contract_verification_cache",
        ["checked_at"],
    )

    op.create_table(
        "symbol_rankings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("score_total", sa.Float(), nullable=False),
        sa.Column("components_json", sa.Text(), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=True),
        sa.Column("reasons_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_symbol_rankings_ts", "symbol_rankings", ["ts"])
    op.create_index("ix_symbol_rankings_symbol", "symbol_rankings", ["symbol"])

    op.create_table(
        "trade_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("bias", sa.String(10), nullable=False),
        sa.Column("strategy", sa.String(30), nullable=False),
        sa.Column("expiry", sa.String(8), nullable=True),
        sa.Column("dte", sa.Integer(), nullable=True),
        sa.Column("legs_json", sa.Text(), nullable=True),
        sa.Column("pricing_json", sa.Text(), nullable=True),
        sa.Column("rationale_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_plans_ts", "trade_plans", ["ts"])
    op.create_index("ix_trade_plans_symbol", "trade_plans", ["symbol"])

    # Composite index on sentiment_snapshots for efficient ticker queries
    op.create_index(
        "ix_sentiment_snapshots_scope_key_ts",
        "sentiment_snapshots",
        ["scope", "key", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_sentiment_snapshots_scope_key_ts", table_name="sentiment_snapshots")

    op.drop_index("ix_trade_plans_symbol", table_name="trade_plans")
    op.drop_index("ix_trade_plans_ts", table_name="trade_plans")
    op.drop_table("trade_plans")

    op.drop_index("ix_symbol_rankings_symbol", table_name="symbol_rankings")
    op.drop_index("ix_symbol_rankings_ts", table_name="symbol_rankings")
    op.drop_table("symbol_rankings")

    op.drop_index(
        "ix_contract_verification_cache_checked_at",
        table_name="contract_verification_cache",
    )
    op.drop_table("contract_verification_cache")
