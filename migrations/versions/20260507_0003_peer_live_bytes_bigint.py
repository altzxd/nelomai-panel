"""Use bigint for peer live byte counters.

Revision ID: 20260507_0003
Revises: 20260504_0002
Create Date: 2026-05-07 21:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260507_0003"
down_revision = "20260504_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("peers") as batch_op:
            batch_op.alter_column("live_rx_bytes", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=True)
            batch_op.alter_column("live_tx_bytes", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=True)
        return
    op.alter_column("peers", "live_rx_bytes", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=True)
    op.alter_column("peers", "live_tx_bytes", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=True)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("peers") as batch_op:
            batch_op.alter_column("live_rx_bytes", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=True)
            batch_op.alter_column("live_tx_bytes", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=True)
        return
    op.alter_column("peers", "live_rx_bytes", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=True)
    op.alter_column("peers", "live_tx_bytes", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=True)
