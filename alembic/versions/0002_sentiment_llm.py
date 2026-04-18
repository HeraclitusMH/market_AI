"""Sentiment LLM dedup + usage tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-18 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sentiment_llm_items",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(200), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sentiment_llm_items_last_seen_at",
        "sentiment_llm_items",
        ["last_seen_at"],
    )
    op.create_index(
        "ix_sentiment_llm_items_processed_at",
        "sentiment_llm_items",
        ["processed_at"],
    )

    op.create_table(
        "sentiment_llm_usage",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("request_kind", sa.String(50), nullable=True),
        sa.Column("input_items_count", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd_est", sa.Float(), nullable=True),
        sa.Column("cost_eur_est", sa.Float(), nullable=True),
        sa.Column("anthropic_request_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("error_type", sa.String(60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sentiment_llm_usage_ts", "sentiment_llm_usage", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_sentiment_llm_usage_ts", table_name="sentiment_llm_usage")
    op.drop_table("sentiment_llm_usage")
    op.drop_index(
        "ix_sentiment_llm_items_processed_at",
        table_name="sentiment_llm_items",
    )
    op.drop_index(
        "ix_sentiment_llm_items_last_seen_at",
        table_name="sentiment_llm_items",
    )
    op.drop_table("sentiment_llm_items")
