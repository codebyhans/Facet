from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine

from photo_app.domain.models import Face, Image, Person
from photo_app.domain.value_objects import BoundingBox
from photo_app.infrastructure.db import init_db
from photo_app.infrastructure.repositories import (
    SqlAlchemyFaceRepository,
    SqlAlchemyImageRepository,
    SqlAlchemyPersonRepository,
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


def test_named_face_presence_counts_only_named_non_excluded_faces() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    init_db(engine)

    names = ("named", "unnamed_tag", "untagged", "excluded", "empty", "unindexed")
    image_repo = SqlAlchemyImageRepository(engine)
    image_repo.add_many(
        [
            Image(
                id=None,
                file_path=f"C:/{name}.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash=name,
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            )
            for name in names
        ]
    )
    ids = {image.hash: image.id for image in image_repo.list_all()}
    for name in names[:-1]:
        image_repo.update_face_count(ids[name], 0 if name == "empty" else 1)

    person_repo = SqlAlchemyPersonRepository(engine)
    anna = person_repo.create(Person(None, "Anna", INDEXED_AT, None))
    no_name = person_repo.create(Person(None, None, INDEXED_AT, None))
    person_by_image = {
        "named": anna.id,
        "unnamed_tag": no_name.id,
        "untagged": None,
        "excluded": anna.id,
    }

    face_repo = SqlAlchemyFaceRepository(engine)
    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=ids[name],
                bbox=BoundingBox(x=0.1, y=0.1, w=0.2, h=0.2),
                embedding=b"\0" * 4,
                person_id=person_id,
            )
            for name, person_id in person_by_image.items()
        ]
    )
    excluded_face = next(
        face for face in face_repo.list_all() if face.image_id == ids["excluded"]
    )
    assert excluded_face.id is not None
    face_repo.exclude([excluded_face.id])

    presence = image_repo.named_face_presence(list(ids.values()))
    assert presence == {
        ids["named"]: True,
        ids["unnamed_tag"]: False,
        ids["untagged"]: False,
        ids["excluded"]: False,
        ids["empty"]: False,
        ids["unindexed"]: None,
    }
