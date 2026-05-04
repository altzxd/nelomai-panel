"""Add auto_create_interfaces to registration_links

Revision ID: 20260504_0002
Revises: 20260422_0001
Create Date: 2026-05-04 21:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260504_0002"
down_revision = "20260422_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("registration_links")}
    if "auto_create_interfaces" not in columns:
        op.add_column(
            "registration_links",
            sa.Column("auto_create_interfaces", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("registration_links")}
    if "auto_create_interfaces" in columns:
        op.drop_column("registration_links", "auto_create_interfaces")
