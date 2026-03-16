"""Merge flag column migration branches

Revision ID: 0010_merge_flag_branches
Revises: 1c03044be575, 0009_add_image_flag
Create Date: 2026-03-11 13:30:00

"""

from __future__ import annotations

from alembic import op

revision = "0010_merge_flag_branches"
down_revision = ("1c03044be575", "0009_add_image_flag")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Merge the two migration branches."""
    # The flag column should already exist from 0009_add_image_flag
    # This migration just resolves the branching conflict


def downgrade() -> None:
    """Downgrade by dropping the flag column."""
    with op.batch_alter_table("images") as batch_op:
        batch_op.drop_index("ix_images_flag")
        batch_op.drop_column("flag")
