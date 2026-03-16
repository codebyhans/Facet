"""Make face_count column nullable

Revision ID: 0008_face_count_nullable
Revises: 0007_add_updated_at
Create Date: 2026-03-11 10:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_face_count_nullable"
down_revision = "0007_add_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Check if face_count column exists and its current definition
    image_columns = {column["name"]: column for column in inspector.get_columns("images")}
    face_count_col = image_columns.get("face_count")

    if face_count_col is None:
        # If face_count column doesn't exist, add it as nullable
        with op.batch_alter_table("images") as batch_op:
            batch_op.add_column(sa.Column("face_count", sa.Integer(), nullable=True))
    else:
        # If face_count column exists, make it nullable
        with op.batch_alter_table("images") as batch_op:
            batch_op.alter_column("face_count", existing_type=sa.Integer(), nullable=True)

    # Set existing rows to NULL so they get re-processed
    op.execute("UPDATE images SET face_count = NULL")


def downgrade() -> None:
    # Set NULL values to 0 before making column non-nullable
    op.execute("UPDATE images SET face_count = 0 WHERE face_count IS NULL")
    with op.batch_alter_table("images") as batch_op:
        batch_op.alter_column("face_count", existing_type=sa.Integer(), nullable=False)
