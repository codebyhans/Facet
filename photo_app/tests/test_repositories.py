from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine

from photo_app.domain.models import Face, Image
from photo_app.domain.value_objects import BoundingBox
from photo_app.infrastructure.db import init_db
from photo_app.infrastructure.repositories import (
    SqlAlchemyFaceRepository,
    SqlAlchemyImageRepository,
)
from photo_app.infrastructure.sqlalchemy_models import Base

INDEXED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def test_image_repository_roundtrip() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    init_db(engine)

    repo = SqlAlchemyImageRepository(engine)
    repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/photo.jpg",
                capture_date=datetime(2024, 2, 3, tzinfo=UTC),
                year=2024,
                month=2,
                hash="h1",
                width=1200,
                height=900,
                indexed_at=INDEXED_AT,
            )
        ]
    )

    assert repo.exists_by_path("C:/photo.jpg")
    all_images = repo.list_all()
    assert len(all_images) == 1
    assert all_images[0].hash == "h1"


def test_face_repository_bulk_id_lookups_exceed_sqlite_variable_limit() -> None:
    """Bulk id lookups/updates must chunk `id IN (...)` statements.

    Regression test for a full-library reset failing with sqlite3's "too
    many SQL variables" once a library has more faces than SQLite's default
    999 bound-parameter cap per statement.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    init_db(engine)

    image_repo = SqlAlchemyImageRepository(engine)
    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/photo.jpg",
                capture_date=datetime(2024, 2, 3, tzinfo=UTC),
                year=2024,
                month=2,
                hash="h1",
                width=1200,
                height=900,
                indexed_at=INDEXED_AT,
            )
        ]
    )
    image_id = image_repo.list_all()[0].id
    assert image_id is not None

    face_count = 1500  # comfortably above SQLite's default 999-variable cap
    face_repo = SqlAlchemyFaceRepository(engine)
    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_id,
                bbox=BoundingBox(x=0.1, y=0.1, w=0.2, h=0.2),
                embedding=b"\x00" * 4,
                person_id=None,
            )
            for _ in range(face_count)
        ]
    )
    all_faces = face_repo.list_all()
    assert len(all_faces) == face_count
    face_ids = [face.id for face in all_faces if face.id is not None]

    face_repo.exclude(face_ids)
    excluded = face_repo.get_many(face_ids)
    assert len(excluded) == face_count
    assert all(face.excluded for face in excluded)
