"""Add portfolio_id to orders, positions, trades for bot isolation.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-19 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(
            sa.Column("portfolio_id", sa.String(30), nullable=False, server_default="")
        )
        batch_op.create_index("ix_orders_portfolio_id", ["portfolio_id"])

    with op.batch_alter_table("positions") as batch_op:
        batch_op.add_column(
            sa.Column("portfolio_id", sa.String(30), nullable=False, server_default="")
        )
        batch_op.create_index("ix_positions_portfolio_id", ["portfolio_id"])

    with op.batch_alter_table("trades") as batch_op:
        batch_op.add_column(
            sa.Column("portfolio_id", sa.String(30), nullable=False, server_default="")
        )
        batch_op.create_index("ix_trades_portfolio_id", ["portfolio_id"])


def downgrade() -> None:
    with op.batch_alter_table("trades") as batch_op:
        batch_op.drop_index("ix_trades_portfolio_id")
        batch_op.drop_column("portfolio_id")

    with op.batch_alter_table("positions") as batch_op:
        batch_op.drop_index("ix_positions_portfolio_id")
        batch_op.drop_column("portfolio_id")

    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_index("ix_orders_portfolio_id")
        batch_op.drop_column("portfolio_id")
