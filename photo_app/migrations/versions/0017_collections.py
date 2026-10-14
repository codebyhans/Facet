"""Add hand-picked image collections.

A collection is an explicit set of images (e.g. the picks for one year's
photo book). Virtual albums can then be scoped to one or more collections
instead of the whole library.

Revision ID: 0017_collections
Revises: 0016_drop_age_estimation
Create Date: 2026-10-13 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_collections"
down_revision = "0016_drop_age_estimation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "collections" not in tables:
        op.create_table(
            "collections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("name"),
        )

    if "collection_images" not in tables:
        op.create_table(
            "collection_images",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "collection_id",
                sa.Integer(),
                sa.ForeignKey("collections.id"),
                nullable=False,
            ),
            sa.Column(
                "image_id",
                sa.Integer(),
                sa.ForeignKey("images.id"),
                nullable=False,
            ),
            sa.Column("added_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("collection_id", "image_id"),
        )
        op.create_index(
            "ix_collection_images_collection_id",
            "collection_images",
            ["collection_id"],
        )
        op.create_index(
            "ix_collection_images_image_id", "collection_images", ["image_id"]
        )

    # Saved albums now honour their flag criteria, which the cached results
    # were built without. The cache is rebuilt lazily on next view.
    if "album_query_cache" in tables:
        bind.execute(sa.text("DELETE FROM album_query_cache"))


def downgrade() -> None:
    op.drop_index("ix_collection_images_image_id", table_name="collection_images")
    op.drop_index("ix_collection_images_collection_id", table_name="collection_images")
    op.drop_table("collection_images")
    op.drop_table("collections")
