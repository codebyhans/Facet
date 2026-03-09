"""Add app settings table

Revision ID: 0002_app_settings
Revises: 0001_initial
Create Date: 2026-03-04 00:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_app_settings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("value", sa.String(length=2048), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
