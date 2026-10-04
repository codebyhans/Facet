"""Add persons.is_roster and faces.roster_filtered for roster-based clustering.

Revision ID: 0014_roster_tracking
Revises: 0013_face_age_review_confirmed
Create Date: 2026-09-22 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_roster_tracking"
down_revision = "0013_face_age_review_confirmed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    person_columns = {column["name"] for column in inspector.get_columns("persons")}
    if "is_roster" not in person_columns:
        with op.batch_alter_table("persons") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_roster",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    face_columns = {column["name"] for column in inspector.get_columns("faces")}
    if "roster_filtered" not in face_columns:
        with op.batch_alter_table("faces") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "roster_filtered",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("faces") as batch_op:
        batch_op.drop_column("roster_filtered")
    with op.batch_alter_table("persons") as batch_op:
        batch_op.drop_column("is_roster")
