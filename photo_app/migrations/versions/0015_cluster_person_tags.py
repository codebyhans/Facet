"""Flip the cluster<->person relationship for name-tag support.

`identity_clusters.person_id` (nullable, not unique) replaces
`persons.identity_cluster_id` (which forced exactly one cluster per
person) — several clusters can now carry the same name tag, since tagging
a cluster only ever labels it and never merges cluster state. Existing
person->cluster bindings are carried forward onto the cluster before the
old column is dropped. Also removes the roster-tracking columns added in
0014 (that noise-reduction approach is being replaced by tagging plus
per-face ground-truth review, not kept alongside it).

Revision ID: 0015_cluster_person_tags
Revises: 0014_roster_tracking
Create Date: 2026-09-22 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_cluster_person_tags"
down_revision = "0014_roster_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cluster_columns = {
        column["name"] for column in inspector.get_columns("identity_clusters")
    }
    if "person_id" not in cluster_columns:
        with op.batch_alter_table("identity_clusters") as batch_op:
            batch_op.add_column(sa.Column("person_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_identity_clusters_person_id_persons",
                "persons",
                ["person_id"],
                ["id"],
            )
        op.create_index(
            "ix_identity_clusters_person_id", "identity_clusters", ["person_id"]
        )

    person_columns = {column["name"] for column in inspector.get_columns("persons")}
    if "identity_cluster_id" in person_columns:
        # Carry forward any existing person->cluster binding onto the
        # cluster itself before the old column is dropped.
        bind.execute(
            sa.text(
                "UPDATE identity_clusters "
                "SET person_id = ("
                "  SELECT persons.id FROM persons "
                "  WHERE persons.identity_cluster_id = identity_clusters.id"
                ") "
                "WHERE EXISTS ("
                "  SELECT 1 FROM persons "
                "  WHERE persons.identity_cluster_id = identity_clusters.id"
                ")"
            )
        )
        with op.batch_alter_table("persons") as batch_op:
            batch_op.drop_index("ix_persons_identity_cluster_id")
            batch_op.drop_column("identity_cluster_id")

    if "is_roster" in person_columns:
        with op.batch_alter_table("persons") as batch_op:
            batch_op.drop_column("is_roster")

    face_columns = {column["name"] for column in inspector.get_columns("faces")}
    if "roster_filtered" in face_columns:
        with op.batch_alter_table("faces") as batch_op:
            batch_op.drop_column("roster_filtered")


def downgrade() -> None:
    with op.batch_alter_table("faces") as batch_op:
        batch_op.add_column(
            sa.Column(
                "roster_filtered",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("persons") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_roster", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(
            sa.Column("identity_cluster_id", sa.Integer(), nullable=True)
        )
    op.create_index(
        "ix_persons_identity_cluster_id",
        "persons",
        ["identity_cluster_id"],
        unique=True,
    )
    with op.batch_alter_table("identity_clusters") as batch_op:
        batch_op.drop_index("ix_identity_clusters_person_id")
        batch_op.drop_column("person_id")
