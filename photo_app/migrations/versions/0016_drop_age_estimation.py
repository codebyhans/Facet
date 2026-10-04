"""Drop everything that depended on the face age estimator.

The estimator proved unusable (median error ~+33 years; babies routinely
read as 30-60), so clustering now matches on embedding similarity alone:
no per-face age, no implied birth-year ranges, no age-bucketed temporal
profiles, and no age-based review queue.

Revision ID: 0016_drop_age_estimation
Revises: 0015_cluster_person_tags
Create Date: 2026-09-29 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_drop_age_estimation"
down_revision = "0015_cluster_person_tags"
branch_labels = None
depends_on = None

# Matches TemporalIdentityConfig.variance_review_threshold's default.
_DEFAULT_THRESHOLD = 0.35


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "cluster_embeddings" in inspector.get_table_names():
        cluster_embedding_indexes = {
            index["name"] for index in inspector.get_indexes("cluster_embeddings")
        }
        if "ix_cluster_embeddings_cluster_id" in cluster_embedding_indexes:
            op.drop_index(
                "ix_cluster_embeddings_cluster_id", table_name="cluster_embeddings"
            )
        op.drop_table("cluster_embeddings")

    face_columns = {column["name"] for column in inspector.get_columns("faces")}
    with op.batch_alter_table("faces") as batch_op:
        for column in ("estimated_age", "age_review_confirmed"):
            if column in face_columns:
                batch_op.drop_column(column)

    cluster_columns = {
        column["name"] for column in inspector.get_columns("identity_clusters")
    }
    with op.batch_alter_table("identity_clusters") as batch_op:
        for column in ("implied_birth_year_min", "implied_birth_year_max"):
            if column in cluster_columns:
                batch_op.drop_column(column)

    # Most review flags came from the age checks. Re-derive every flag from
    # the stored variance alone — the only remaining review criterion.
    threshold = bind.execute(
        sa.text(
            "SELECT value FROM app_settings "
            "WHERE key = 'identity_variance_review_threshold'"
        )
    ).scalar()
    bind.execute(
        sa.text(
            "UPDATE identity_clusters SET flagged_for_review = (variance >= :t)"
        ),
        {"t": float(threshold) if threshold is not None else _DEFAULT_THRESHOLD},
    )


def downgrade() -> None:
    with op.batch_alter_table("identity_clusters") as batch_op:
        batch_op.add_column(
            sa.Column("implied_birth_year_min", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("implied_birth_year_max", sa.Integer(), nullable=True)
        )

    with op.batch_alter_table("faces") as batch_op:
        batch_op.add_column(sa.Column("estimated_age", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "age_review_confirmed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

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
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("cluster_id", "time_period"),
    )
    op.create_index(
        "ix_cluster_embeddings_cluster_id", "cluster_embeddings", ["cluster_id"]
    )
