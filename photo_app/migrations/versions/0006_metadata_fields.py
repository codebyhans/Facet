"""Add metadata fields for rating, quality, tags, and GPS

Revision ID: 0006_metadata_fields
Revises: 0005_identity_clusters
Create Date: 2026-03-06 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_metadata_fields"
down_revision = "0005_identity_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:  # noqa: C901
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Add columns to images table
    image_columns = {column["name"] for column in inspector.get_columns("images")}
    with op.batch_alter_table("images") as batch_op:
        if "rating" not in image_columns:
            batch_op.add_column(sa.Column("rating", sa.Integer(), nullable=True))
        if "quality_score" not in image_columns:
            batch_op.add_column(sa.Column("quality_score", sa.Float(), nullable=True))
        if "is_favorite" not in image_columns:
            batch_op.add_column(
                sa.Column(
                    "is_favorite",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "user_notes" not in image_columns:
            batch_op.add_column(sa.Column("user_notes", sa.String(2048), nullable=True))
        if "camera_model" not in image_columns:
            batch_op.add_column(
                sa.Column("camera_model", sa.String(255), nullable=True)
            )
        if "gps_latitude" not in image_columns:
            batch_op.add_column(sa.Column("gps_latitude", sa.Float(), nullable=True))
        if "gps_longitude" not in image_columns:
            batch_op.add_column(sa.Column("gps_longitude", sa.Float(), nullable=True))
        if "location_name" not in image_columns:
            batch_op.add_column(
                sa.Column("location_name", sa.String(255), nullable=True)
            )

    # Add column to faces table
    face_columns = {column["name"] for column in inspector.get_columns("faces")}
    with op.batch_alter_table("faces") as batch_op:
        if "confidence_score" not in face_columns:
            batch_op.add_column(
                sa.Column("confidence_score", sa.Float(), nullable=True)
            )

    # Create image_tags table
    tables = set(inspector.get_table_names())
    if "image_tags" not in tables:
        op.create_table(
            "image_tags",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("image_id", sa.Integer(), nullable=False),
            sa.Column("tag_name", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["image_id"], ["images.id"], name="fk_image_tags_image_id"
            ),
            sa.UniqueConstraint("image_id", "tag_name", name="uq_image_id_tag_name"),
        )
        op.create_index("ix_image_tags_image_id", "image_tags", ["image_id"])
        op.create_index("ix_image_tags_tag_name", "image_tags", ["tag_name"])


def downgrade() -> None:  # noqa: C901
    # Drop image_tags table
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "image_tags" in tables:
        op.drop_table("image_tags")

    # Remove columns from images table
    image_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("images")
    }
    with op.batch_alter_table("images") as batch_op:
        if "rating" in image_columns:
            batch_op.drop_column("rating")
        if "quality_score" in image_columns:
            batch_op.drop_column("quality_score")
        if "is_favorite" in image_columns:
            batch_op.drop_column("is_favorite")
        if "user_notes" in image_columns:
            batch_op.drop_column("user_notes")
        if "camera_model" in image_columns:
            batch_op.drop_column("camera_model")
        if "gps_latitude" in image_columns:
            batch_op.drop_column("gps_latitude")
        if "gps_longitude" in image_columns:
            batch_op.drop_column("gps_longitude")
        if "location_name" in image_columns:
            batch_op.drop_column("location_name")

    # Remove column from faces table
    face_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("faces")
    }
    with op.batch_alter_table("faces") as batch_op:
        if "confidence_score" in face_columns:
            batch_op.drop_column("confidence_score")
