"""Add manual/excluded flags for face overrides

Revision ID: 0003_face_overrides
Revises: 0002_app_settings
Create Date: 2026-03-04 22:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_face_overrides"
down_revision = "0002_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "faces",
        sa.Column(
            "manual_assignment",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "faces",
        sa.Column(
            "excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("faces", "excluded")
    op.drop_column("faces", "manual_assignment")
