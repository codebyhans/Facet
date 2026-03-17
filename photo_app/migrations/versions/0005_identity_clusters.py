"""Add temporal identity clustering tables

Revision ID: 0005_identity_clusters
Revises: 0004_browsing_cache
Create Date: 2026-03-05 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_identity_clusters"
down_revision = "0004_browsing_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "identity_clusters" not in tables:
        op.create_table(
            "identity_clusters",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("canonical_embedding", sa.LargeBinary(), nullable=False),
            sa.Column("face_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("variance", sa.Float(), nullable=False, server_default="0"),
            sa.Column(
                "flagged_for_review",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    person_columns = {column["name"] for column in inspector.get_columns("persons")}
    if "identity_cluster_id" not in person_columns:
        with op.batch_alter_table("persons") as batch_op:
            batch_op.add_column(
                sa.Column("identity_cluster_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_persons_identity_cluster_id_identity_clusters",
                "identity_clusters",
                ["identity_cluster_id"],
                ["id"],
            )

    person_indexes = {index["name"] for index in inspector.get_indexes("persons")}
    if "ix_persons_identity_cluster_id" not in person_indexes:
        op.create_index(
            "ix_persons_identity_cluster_id",
            "persons",
            ["identity_cluster_id"],
            unique=True,
        )

    if "face_cluster_memberships" not in tables:
        op.create_table(
            "face_cluster_memberships",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "face_id", sa.Integer(), sa.ForeignKey("faces.id"), nullable=False
            ),
            sa.Column(
                "cluster_id",
                sa.Integer(),
                sa.ForeignKey("identity_clusters.id"),
                nullable=False,
            ),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("assigned_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("face_id"),
        )
    membership_indexes = (
        {index["name"] for index in inspector.get_indexes("face_cluster_memberships")}
        if "face_cluster_memberships" in set(sa.inspect(bind).get_table_names())
        else set()
    )
    if "ix_face_cluster_memberships_face_id" not in membership_indexes:
        op.create_index(
            "ix_face_cluster_memberships_face_id",
            "face_cluster_memberships",
            ["face_id"],
        )
    if "ix_face_cluster_memberships_cluster_id" not in membership_indexes:
        op.create_index(
            "ix_face_cluster_memberships_cluster_id",
            "face_cluster_memberships",
            ["cluster_id"],
        )

    if "cluster_embeddings" not in tables:
        op.create_table(
            "cluster_embeddings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "cluster_id",
                sa.Integer(),
                sa.ForeignKey("identity_clusters.id"),
                nullable=False,
            ),
            sa.Column("time_period", sa.String(length=32), nullable=False),
            sa.Column("embedding", sa.LargeBinary(), nullable=False),
            sa.Column("sample_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("cluster_id", "time_period"),
        )
    cluster_indexes = (
        {index["name"] for index in inspector.get_indexes("cluster_embeddings")}
        if "cluster_embeddings" in set(sa.inspect(bind).get_table_names())
        else set()
    )
    if "ix_cluster_embeddings_cluster_id" not in cluster_indexes:
        op.create_index(
            "ix_cluster_embeddings_cluster_id",
            "cluster_embeddings",
            ["cluster_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_cluster_embeddings_cluster_id", table_name="cluster_embeddings")
    op.drop_table("cluster_embeddings")

    op.drop_index(
        "ix_face_cluster_memberships_cluster_id",
        table_name="face_cluster_memberships",
    )
    op.drop_index(
        "ix_face_cluster_memberships_face_id",
        table_name="face_cluster_memberships",
    )
    op.drop_table("face_cluster_memberships")

    op.drop_index("ix_persons_identity_cluster_id", table_name="persons")
    op.drop_column("persons", "identity_cluster_id")
    op.drop_table("identity_clusters")
