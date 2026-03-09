"""Add updated_at field to images table

Revision ID: 0007_add_updated_at
Revises: 0006_metadata_fields
Create Date: 2026-03-06 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_add_updated_at"
down_revision = "0006_metadata_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Add updated_at column to images table if it doesn't exist
    image_columns = {column["name"] for column in inspector.get_columns("images")}
    with op.batch_alter_table("images") as batch_op:
        if "updated_at" not in image_columns:
            batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("images") as batch_op:
        batch_op.drop_column("updated_at")
