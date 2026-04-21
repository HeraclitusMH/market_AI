"""Add security_master, security_alias, rss_entity_matches tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-21 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "security_master",
        sa.Column("symbol", sa.String(20), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("security_type", sa.String(10), nullable=False, server_default="STK"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("market_cap", sa.Float, nullable=True),
        sa.Column("avg_dollar_volume_20d", sa.Float, nullable=True),
        sa.Column("options_eligible", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("ibkr_conid", sa.Integer, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_security_master_exchange_active", "security_master", ["exchange", "active"])
    op.create_index("ix_security_master_options_eligible", "security_master", ["options_eligible"])

    op.create_table(
        "security_alias",
        sa.Column("alias", sa.String(200), primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("alias_type", sa.String(30), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_security_alias_symbol", "security_alias", ["symbol"])

    op.create_table(
        "rss_entity_matches",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("article_id", sa.String(64), nullable=False),
        sa.Column("company_input", sa.Text, nullable=False),
        sa.Column("normalized_input", sa.Text, nullable=True),
        sa.Column("symbol", sa.String(20), nullable=True),
        sa.Column("match_type", sa.String(30), nullable=True),
        sa.Column("match_score", sa.Float, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_rss_entity_matches_article_id", "rss_entity_matches", ["article_id"])


def downgrade() -> None:
    op.drop_index("ix_rss_entity_matches_article_id", "rss_entity_matches")
    op.drop_table("rss_entity_matches")
    op.drop_index("ix_security_alias_symbol", "security_alias")
    op.drop_table("security_alias")
    op.drop_index("ix_security_master_options_eligible", "security_master")
    op.drop_index("ix_security_master_exchange_active", "security_master")
    op.drop_table("security_master")
