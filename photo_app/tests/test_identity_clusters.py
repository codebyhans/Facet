from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from photo_app.domain.models import Face, Image
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


def _embedding(values: list[float]) -> bytes:
    return np.array(values, dtype=np.float32).tobytes()


def test_temporal_service_assigns_similar_faces_to_one_cluster() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        image_repo = SqlAlchemyImageRepository(session)
        face_repo = SqlAlchemyFaceRepository(session)
        person_repo = SqlAlchemyPersonRepository(session)
        cluster_repo = SqlAlchemyIdentityClusterRepository(session)

        image_repo.add_many(
            [
                Image(
                    id=None,
                    file_path="C:/child.jpg",
                    capture_date=date(2008, 5, 1),
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
                    capture_date=date(2025, 5, 1),
                    year=2025,
                    month=5,
                    hash="i2",
                    width=100,
                    height=100,
                    indexed_at=INDEXED_AT,
                ),
            ]
        )
        session.flush()
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
        assert assigned == 2

        clusters = service.list_clusters()
        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster.id is not None
        assert cluster.face_count == 2

        members = cluster_repo.list_memberships(cluster.id)
        assert len(members) == 2


def test_image_filter_by_cluster_ids() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        image_repo = SqlAlchemyImageRepository(session)
        face_repo = SqlAlchemyFaceRepository(session)
        person_repo = SqlAlchemyPersonRepository(session)
        cluster_repo = SqlAlchemyIdentityClusterRepository(session)

        image_repo.add_many(
            [
                Image(
                    id=None,
                    file_path="C:/a.jpg",
                    capture_date=date(2024, 1, 1),
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
                    capture_date=date(2024, 1, 2),
                    year=2024,
                    month=1,
                    hash="b1",
                    width=64,
                    height=64,
                    indexed_at=INDEXED_AT,
                ),
            ]
        )
        session.flush()
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
        session.flush()

        service = TemporalIdentityClusterService(
            face_repository=face_repo,
            image_repository=image_repo,
            person_repository=person_repo,
            cluster_repository=cluster_repo,
            config=TemporalIdentityConfig(match_threshold=0.8),
        )
        _ = service.index_new_faces()
        clusters = service.list_clusters()
        assert len(clusters) == 2
        target_cluster_id = clusters[0].id
        assert target_cluster_id is not None

        filtered = image_repo.list_ids_by_filters(
            person_ids=(),
            date_from=None,
            date_to=None,
            cluster_ids=(target_cluster_id,),
        )
        assert len(filtered) == 1
