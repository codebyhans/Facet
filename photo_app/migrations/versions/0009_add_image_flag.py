"""Add flag column to images table

Revision ID: 0009_add_image_flag
Revises: 0008_face_count_nullable
Create Date: 2026-03-11 12:51:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_add_image_flag"
down_revision = "0008_face_count_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add flag column to images table."""
    with op.batch_alter_table("images") as batch_op:
        batch_op.add_column(
            sa.Column("flag", sa.String(20), nullable=True)
        )

    # Create index for flag queries
    op.create_index("ix_images_flag", "images", ["flag"])


def downgrade() -> None:
    """Remove flag column from images table."""
    with op.batch_alter_table("images") as batch_op:
        batch_op.drop_index("ix_images_flag")
        batch_op.drop_column("flag")
