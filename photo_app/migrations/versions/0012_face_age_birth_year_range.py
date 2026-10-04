"""Add face estimated_age and cluster implied birth-year range.

Revision ID: 0012_face_age_birth_year_range
Revises: 0011_capture_datetime
Create Date: 2026-09-21 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_face_age_birth_year_range"
down_revision = "0011_capture_datetime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    face_columns = {column["name"] for column in inspector.get_columns("faces")}
    if "estimated_age" not in face_columns:
        with op.batch_alter_table("faces") as batch_op:
            batch_op.add_column(sa.Column("estimated_age", sa.Float(), nullable=True))

    cluster_columns = {
        column["name"] for column in inspector.get_columns("identity_clusters")
    }
    with op.batch_alter_table("identity_clusters") as batch_op:
        if "implied_birth_year_min" not in cluster_columns:
            batch_op.add_column(
                sa.Column("implied_birth_year_min", sa.Integer(), nullable=True)
            )
        if "implied_birth_year_max" not in cluster_columns:
            batch_op.add_column(
                sa.Column("implied_birth_year_max", sa.Integer(), nullable=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("identity_clusters") as batch_op:
        batch_op.drop_column("implied_birth_year_max")
        batch_op.drop_column("implied_birth_year_min")

    with op.batch_alter_table("faces") as batch_op:
        batch_op.drop_column("estimated_age")
