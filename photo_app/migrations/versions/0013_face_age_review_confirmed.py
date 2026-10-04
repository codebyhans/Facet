"""Add faces.age_review_confirmed for the flagged-face review queue.

Revision ID: 0013_face_age_review_confirmed
Revises: 0012_face_age_birth_year_range
Create Date: 2026-09-22 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_face_age_review_confirmed"
down_revision = "0012_face_age_birth_year_range"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    face_columns = {column["name"] for column in inspector.get_columns("faces")}
    if "age_review_confirmed" not in face_columns:
        with op.batch_alter_table("faces") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "age_review_confirmed",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("faces") as batch_op:
        batch_op.drop_column("age_review_confirmed")
