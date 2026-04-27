"""Add cached fundamental snapshots.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-27 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fundamental_snapshots",
        sa.Column("symbol", sa.String(20), primary_key=True),
        sa.Column("ts", sa.DateTime, nullable=False),
        sa.Column("report_type", sa.String(30), nullable=False, server_default="ReportSnapshot"),
        sa.Column("metrics_json", sa.Text, nullable=True),
        sa.Column("raw_xml", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("reason", sa.Text, nullable=True),
    )
    op.create_index("ix_fundamental_snapshots_ts", "fundamental_snapshots", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_fundamental_snapshots_ts", table_name="fundamental_snapshots")
    op.drop_table("fundamental_snapshots")
