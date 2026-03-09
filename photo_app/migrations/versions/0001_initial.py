"""Initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-03-04 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("capture_date", sa.Date(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("month", sa.Integer(), nullable=True),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("file_path"),
        sa.UniqueConstraint("hash"),
    )
    op.create_index("ix_images_capture_date", "images", ["capture_date"])
    op.create_index("ix_images_file_path", "images", ["file_path"])
    op.create_index("ix_images_year", "images", ["year"])

    op.create_table(
        "persons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
    )

    op.create_table(
        "albums",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("query_definition", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "faces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("images.id"), nullable=False),
        sa.Column("bbox_x", sa.Integer(), nullable=False),
        sa.Column("bbox_y", sa.Integer(), nullable=False),
        sa.Column("bbox_w", sa.Integer(), nullable=False),
        sa.Column("bbox_h", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_faces_image_id", "faces", ["image_id"])
    op.create_index("ix_faces_person_id", "faces", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_faces_person_id", table_name="faces")
    op.drop_index("ix_faces_image_id", table_name="faces")
    op.drop_table("faces")
    op.drop_table("albums")
    op.drop_table("persons")
    op.drop_index("ix_images_year", table_name="images")
    op.drop_index("ix_images_file_path", table_name="images")
    op.drop_index("ix_images_capture_date", table_name="images")
    op.drop_table("images")
