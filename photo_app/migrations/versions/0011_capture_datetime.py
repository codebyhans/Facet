"""Store capture timestamps as DateTime.

Revision ID: 0011_capture_datetime
Revises: 0010_merge_flag_branches
Create Date: 2026-03-17 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_capture_datetime"
down_revision = "0010_merge_flag_branches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("images") as batch_op:
        batch_op.alter_column(
            "capture_date",
            existing_type=sa.Date(),
            type_=sa.DateTime(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("images") as batch_op:
        batch_op.alter_column(
            "capture_date",
            existing_type=sa.DateTime(),
            type_=sa.Date(),
            existing_nullable=True,
        )
