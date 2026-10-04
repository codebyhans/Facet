from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from sqlalchemy import create_engine

from photo_app.domain.models import Face, Image, Person
from photo_app.domain.value_objects import BoundingBox
from photo_app.infrastructure.repositories import (
    SqlAlchemyFaceRepository,
    SqlAlchemyIdentityClusterRepository,
    SqlAlchemyImageRepository,
    SqlAlchemyPersonRepository,
)
from photo_app.infrastructure.sqlalchemy_models import Base
from photo_app.services.identity_cluster_service import (
    TemporalIdentityClusterService,
    TemporalIdentityConfig,
)

INDEXED_AT = datetime(2026, 1, 1, tzinfo=UTC)
EXPECTED_ASSIGNED = 2
EXPECTED_CLUSTER_FACE_COUNT = 2
EXPECTED_MEMBER_COUNT = 2
EXPECTED_CLUSTER_COUNT = 2


def _embedding(values: list[float]) -> bytes:
    return np.array(values, dtype=np.float32).tobytes()


def test_temporal_service_assigns_similar_faces_to_one_cluster() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    image_repo = SqlAlchemyImageRepository(engine)
    face_repo = SqlAlchemyFaceRepository(engine)
    person_repo = SqlAlchemyPersonRepository(engine)
    cluster_repo = SqlAlchemyIdentityClusterRepository(engine)

    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/child.jpg",
                capture_date=datetime(2008, 5, 1, tzinfo=UTC),
                year=2008,
                month=5,
                hash="i1",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            ),
            Image(
                id=None,
                file_path="C:/adult.jpg",
                capture_date=datetime(2025, 5, 1, tzinfo=UTC),
                year=2025,
                month=5,
                hash="i2",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            ),
        ]
    )
    images = {image.file_path: image for image in image_repo.list_all()}
    child = images["C:/child.jpg"]
    adult = images["C:/adult.jpg"]
    assert child.id is not None
    assert adult.id is not None

    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=child.id,
                bbox=BoundingBox(0, 0, 10, 10),
                embedding=_embedding([1.0, 0.0, 0.0, 0.0]),
                person_id=None,
            ),
            Face(
                id=None,
                image_id=adult.id,
                bbox=BoundingBox(0, 0, 10, 10),
                embedding=_embedding([0.99, 0.01, 0.0, 0.0]),
                person_id=None,
            ),
        ]
    )

    service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
        config=TemporalIdentityConfig(match_threshold=0.3),
    )
    assigned = service.index_new_faces()
    assert assigned == EXPECTED_ASSIGNED

    clusters = service.list_clusters()
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.id is not None
    assert cluster.face_count == EXPECTED_CLUSTER_FACE_COUNT

    members = cluster_repo.list_memberships(cluster.id)
    assert len(members) == EXPECTED_MEMBER_COUNT


def test_image_filter_by_cluster_ids() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    image_repo = SqlAlchemyImageRepository(engine)
    face_repo = SqlAlchemyFaceRepository(engine)
    person_repo = SqlAlchemyPersonRepository(engine)
    cluster_repo = SqlAlchemyIdentityClusterRepository(engine)

    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/a.jpg",
                capture_date=datetime(2024, 1, 1, tzinfo=UTC),
                year=2024,
                month=1,
                hash="a1",
                width=64,
                height=64,
                indexed_at=INDEXED_AT,
            ),
            Image(
                id=None,
                file_path="C:/b.jpg",
                capture_date=datetime(2024, 1, 2, tzinfo=UTC),
                year=2024,
                month=1,
                hash="b1",
                width=64,
                height=64,
                indexed_at=INDEXED_AT,
            ),
        ]
    )
    images = {image.file_path: image for image in image_repo.list_all()}
    assert images["C:/a.jpg"].id is not None
    assert images["C:/b.jpg"].id is not None

    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=int(images["C:/a.jpg"].id),
                bbox=BoundingBox(0, 0, 5, 5),
                embedding=_embedding([1.0, 0.0]),
                person_id=None,
            ),
            Face(
                id=None,
                image_id=int(images["C:/b.jpg"].id),
                bbox=BoundingBox(0, 0, 5, 5),
                embedding=_embedding([0.0, 1.0]),
                person_id=None,
            ),
        ]
    )

    service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
        config=TemporalIdentityConfig(match_threshold=0.8),
    )
    _ = service.index_new_faces()
    clusters = service.list_clusters()
    assert len(clusters) == EXPECTED_CLUSTER_COUNT
    target_cluster_id = clusters[0].id
    assert target_cluster_id is not None

    filtered = image_repo.list_ids_by_filters(
        person_ids=(),
        date_from=None,
        date_to=None,
        cluster_ids=(target_cluster_id,),
    )
    assert len(filtered) == 1


def _make_repos(engine: object) -> tuple:
    return (
        SqlAlchemyImageRepository(engine),
        SqlAlchemyFaceRepository(engine),
        SqlAlchemyPersonRepository(engine),
        SqlAlchemyIdentityClusterRepository(engine),
    )


def _add_image(image_repo: SqlAlchemyImageRepository, path: str, year: int) -> int:
    image_repo.add_many(
        [
            Image(
                id=None,
                file_path=path,
                capture_date=datetime(year, 6, 1, tzinfo=UTC),
                year=year,
                month=6,
                hash=path,
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            )
        ]
    )
    image = image_repo.get_by_path(path)
    assert image is not None
    assert image.id is not None
    return image.id


def _make_cluster_with_faces(
    image_repo: SqlAlchemyImageRepository,
    face_repo: SqlAlchemyFaceRepository,
    cluster_repo: SqlAlchemyIdentityClusterRepository,
    capture_years: list[int],
    *,
    path_prefix: str,
) -> tuple[int, list[int]]:
    """Create one cluster with one member face per capture year, all with
    the same embedding. Returns (cluster_id, face_ids).
    """
    cluster = cluster_repo.create_cluster(
        _embedding([1.0, 0.0, 0.0, 0.0]), created_at=INDEXED_AT
    )
    assert cluster.id is not None
    face_ids: list[int] = []
    for i, year in enumerate(capture_years):
        image_id = _add_image(image_repo, f"C:/{path_prefix}_{i}.jpg", year)
        face_repo.add_many(
            [
                Face(
                    id=None,
                    image_id=image_id,
                    bbox=BoundingBox(0, 0, 10, 10),
                    embedding=_embedding([1.0, 0.0, 0.0, 0.0]),
                    person_id=None,
                )
            ]
        )
        face = next(f for f in face_repo.list_all_active() if f.image_id == image_id)
        assert face.id is not None
        cluster_repo.upsert_membership(
            face_id=face.id,
            cluster_id=cluster.id,
            confidence=1.0,
            assigned_at=INDEXED_AT,
        )
        face_ids.append(face.id)
    return cluster.id, face_ids


def test_similar_face_years_later_joins_existing_cluster() -> None:
    """Matching is purely by embedding similarity: a near-identical face
    photographed nine years after the cluster's photos still joins it, and
    picks up the cluster's name tag.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo, face_repo, person_repo, cluster_repo = _make_repos(engine)

    cluster_id, _ = _make_cluster_with_faces(
        image_repo, face_repo, cluster_repo, [2016, 2016], path_prefix="then"
    )
    emma = person_repo.create(
        Person(id=None, name="Emma", created_at=INDEXED_AT, birth_date=None)
    )
    assert emma.id is not None
    cluster_repo.set_cluster_person(cluster_id, emma.id)

    image_id = _add_image(image_repo, "C:/now.jpg", 2025)
    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_id,
                bbox=BoundingBox(0, 0, 10, 10),
                embedding=_embedding([0.98, 0.2, 0.0, 0.0]),
                person_id=None,
            )
        ]
    )

    service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
    )
    assert service.index_new_faces() == 1

    new_face = next(f for f in face_repo.list_all_active() if f.image_id == image_id)
    assert new_face.id is not None
    membership = cluster_repo.get_membership(new_face.id)
    assert membership is not None
    assert membership.cluster_id == cluster_id
    assert new_face.person_id == emma.id


def test_tag_cluster_labels_faces_without_touching_cluster_state() -> None:
    """Tagging a cluster sets `Face.person_id` on its members and records
    the tag on the cluster, but never merges/smears cluster state: the
    cluster keeps exactly the same members before and after.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo, face_repo, person_repo, cluster_repo = _make_repos(engine)

    cluster_id, face_ids = _make_cluster_with_faces(
        image_repo, face_repo, cluster_repo, [2016, 2017], path_prefix="tag_kid"
    )

    service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
    )

    emma = person_repo.create(
        Person(id=None, name="Emma", created_at=INDEXED_AT, birth_date=None)
    )
    assert emma.id is not None

    applied = service.tag_cluster(cluster_id, emma.id)
    assert applied is True

    cluster = cluster_repo.get_cluster(cluster_id)
    assert cluster is not None
    assert cluster.person_id == emma.id

    members = {m.face_id for m in cluster_repo.list_memberships(cluster_id)}
    assert members == set(face_ids)

    for face_id in face_ids:
        face = face_repo.get(face_id)
        assert face is not None
        assert face.person_id == emma.id
        assert face.manual_assignment is True


def test_tag_cluster_same_name_applies_to_multiple_clusters_without_merging() -> None:
    """Tagging two different clusters with the same name links them to one
    person without merging their ML state — both clusters keep existing
    independently, each with only its own original members.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo, face_repo, person_repo, cluster_repo = _make_repos(engine)

    baby_cluster_id, baby_face_ids = _make_cluster_with_faces(
        image_repo, face_repo, cluster_repo, [2016], path_prefix="multi_baby"
    )
    kid_cluster_id, kid_face_ids = _make_cluster_with_faces(
        image_repo, face_repo, cluster_repo, [2023], path_prefix="multi_kid"
    )

    service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
    )

    emma = person_repo.create(
        Person(id=None, name="Emma", created_at=INDEXED_AT, birth_date=None)
    )
    assert emma.id is not None

    assert service.tag_cluster(baby_cluster_id, emma.id) is True
    assert service.tag_cluster(kid_cluster_id, emma.id) is True

    # Both clusters still exist, independently, each with only its own faces.
    baby_cluster = cluster_repo.get_cluster(baby_cluster_id)
    kid_cluster = cluster_repo.get_cluster(kid_cluster_id)
    assert baby_cluster is not None
    assert kid_cluster is not None
    assert baby_cluster.person_id == emma.id
    assert kid_cluster.person_id == emma.id

    baby_members = {m.face_id for m in cluster_repo.list_memberships(baby_cluster_id)}
    kid_members = {m.face_id for m in cluster_repo.list_memberships(kid_cluster_id)}
    assert baby_members == set(baby_face_ids)
    assert kid_members == set(kid_face_ids)


def test_exclude_persons_removes_faces_and_cleans_up_clusters() -> None:
    """Excluding a person (e.g. a stranger spotted while reviewing) marks
    all their faces excluded — never re-clustered or shown again — and
    removes the now-empty cluster/person records.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo, face_repo, person_repo, cluster_repo = _make_repos(engine)

    cluster_id, face_ids = _make_cluster_with_faces(
        image_repo, face_repo, cluster_repo, [2016, 2017], path_prefix="stranger"
    )

    stranger = person_repo.create(
        Person(id=None, name=None, created_at=INDEXED_AT, birth_date=None)
    )
    assert stranger.id is not None
    cluster_repo.set_cluster_person(cluster_id, stranger.id)
    face_repo.assign_person_manual(face_ids, stranger.id)

    service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
    )
    excluded_count = service.exclude_persons([stranger.id])
    assert excluded_count == len(face_ids)

    assert cluster_repo.get_cluster(cluster_id) is None
    assert person_repo.get(stranger.id) is None
    for face_id in face_ids:
        face = face_repo.get(face_id)
        assert face is not None
        assert face.excluded is True
        assert face.person_id is None


def _add_face_in_new_cluster(  # noqa: PLR0913
    image_repo: SqlAlchemyImageRepository,
    face_repo: SqlAlchemyFaceRepository,
    cluster_repo: SqlAlchemyIdentityClusterRepository,
    person_id: int,
    values: list[float],
    path: str,
) -> tuple[int, int]:
    """One face in its own cluster tagged `person_id`. Returns (cluster, face)."""
    image_id = _add_image(image_repo, path, 2024)
    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_id,
                bbox=BoundingBox(0, 0, 10, 10),
                embedding=_embedding(values),
                person_id=person_id,
            )
        ]
    )
    face = next(f for f in face_repo.list_all_active() if f.image_id == image_id)
    assert face.id is not None
    cluster = cluster_repo.create_cluster(_embedding(values), created_at=INDEXED_AT)
    assert cluster.id is not None
    cluster_repo.upsert_membership(
        face_id=face.id, cluster_id=cluster.id, confidence=1.0, assigned_at=INDEXED_AT
    )
    cluster_repo.set_cluster_person(cluster.id, person_id)
    return cluster.id, face.id


def test_rematch_unnamed_faces_moves_strays_into_matching_named_cluster() -> None:
    """An unnamed single-face cluster that looks like a named one is folded
    into it; a dissimilar one stays unnamed; a hand-assigned unnamed
    cluster and the named cluster's own members are left alone.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo, face_repo, person_repo, cluster_repo = _make_repos(engine)

    named_cluster_id, named_face_ids = _make_cluster_with_faces(
        image_repo, face_repo, cluster_repo, [2016, 2017], path_prefix="emma"
    )
    emma = person_repo.create(
        Person(id=None, name="Emma", created_at=INDEXED_AT, birth_date=None)
    )
    assert emma.id is not None
    cluster_repo.set_cluster_person(named_cluster_id, emma.id)
    face_repo.assign_person_manual(named_face_ids, emma.id)

    def placeholder() -> int:
        person = person_repo.create(
            Person(id=None, name=None, created_at=INDEXED_AT, birth_date=None)
        )
        assert person.id is not None
        return person.id

    _, stray_face_id = _add_face_in_new_cluster(
        image_repo,
        face_repo,
        cluster_repo,
        placeholder(),
        [0.98, 0.2, 0.0, 0.0],
        "C:/stray.jpg",
    )
    _, other_face_id = _add_face_in_new_cluster(
        image_repo,
        face_repo,
        cluster_repo,
        placeholder(),
        [0.0, 0.0, 1.0, 0.0],
        "C:/other.jpg",
    )
    kept_person_id = placeholder()
    kept_cluster_id, kept_face_id = _add_face_in_new_cluster(
        image_repo,
        face_repo,
        cluster_repo,
        kept_person_id,
        [0.0, 1.0, 0.0, 0.0],
        "C:/kept.jpg",
    )
    face_repo.assign_person_manual([kept_face_id], kept_person_id)

    service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
    )
    assert service.rematch_unnamed_faces() == EXPECTED_ASSIGNED

    stray = face_repo.get(stray_face_id)
    assert stray is not None
    assert stray.person_id == emma.id
    stray_membership = cluster_repo.get_membership(stray_face_id)
    assert stray_membership is not None
    assert stray_membership.cluster_id == named_cluster_id

    other = face_repo.get(other_face_id)
    assert other is not None
    assert other.person_id is not None
    other_person = person_repo.get(other.person_id)
    assert other_person is not None
    assert other_person.name is None

    kept_membership = cluster_repo.get_membership(kept_face_id)
    assert kept_membership is not None
    assert kept_membership.cluster_id == kept_cluster_id

    named_members = {m.face_id for m in cluster_repo.list_memberships(named_cluster_id)}
    assert named_members == {*named_face_ids, stray_face_id}
