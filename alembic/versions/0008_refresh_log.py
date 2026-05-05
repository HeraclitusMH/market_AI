"""Add refresh_log table for manual refresh audit trail.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-05 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refresh_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), server_default=""),
    )
    op.create_index("ix_refresh_log_timestamp", "refresh_log", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_refresh_log_timestamp", table_name="refresh_log")
    op.drop_table("refresh_log")
