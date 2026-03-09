"""Add thumbnail tile cache and album query cache tables

Revision ID: 0004_browsing_cache
Revises: 0003_face_overrides
Create Date: 2026-03-05 08:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_browsing_cache"
down_revision = "0003_face_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "albums",
        sa.Column(
            "query_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    op.create_table(
        "thumbnail_tiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tile_path", sa.String(length=1024), nullable=False),
        sa.Column("tile_index", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("images.id"), nullable=False),
        sa.Column("position_in_tile", sa.Integer(), nullable=False),
        sa.UniqueConstraint("image_id"),
        sa.UniqueConstraint("tile_index", "position_in_tile"),
    )
    op.create_index("ix_thumbnail_tiles_image_id", "thumbnail_tiles", ["image_id"])
    op.create_index("ix_thumbnail_tiles_tile_index", "thumbnail_tiles", ["tile_index"])

    op.create_table(
        "album_query_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("album_id", sa.Integer(), sa.ForeignKey("albums.id"), nullable=False),
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("images.id"), nullable=False),
        sa.Column("query_version", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("album_id", "position"),
        sa.UniqueConstraint("album_id", "image_id"),
    )
    op.create_index("ix_album_query_cache_album_id", "album_query_cache", ["album_id"])
    op.create_index("ix_album_query_cache_position", "album_query_cache", ["position"])


def downgrade() -> None:
    op.drop_index("ix_album_query_cache_position", table_name="album_query_cache")
    op.drop_index("ix_album_query_cache_album_id", table_name="album_query_cache")
    op.drop_table("album_query_cache")

    op.drop_index("ix_thumbnail_tiles_tile_index", table_name="thumbnail_tiles")
    op.drop_index("ix_thumbnail_tiles_image_id", table_name="thumbnail_tiles")
    op.drop_table("thumbnail_tiles")

    op.drop_column("albums", "query_version")
