from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import create_engine

from photo_app.domain.models import Album, Face, Image
from photo_app.domain.value_objects import AlbumQuery, BoundingBox
from photo_app.infrastructure.repositories import (
    SqlAlchemyAlbumRepository,
    SqlAlchemyFaceRepository,
    SqlAlchemyImageRepository,
)
from photo_app.infrastructure.sqlalchemy_models import Base
from photo_app.services.album_service import AlbumService

INDEXED_AT = datetime(2026, 1, 1, tzinfo=UTC)
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def test_album_service_translates_query_to_repo_filters() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    image_repo = SqlAlchemyImageRepository(engine)
    face_repo = SqlAlchemyFaceRepository(engine)
    album_repo = SqlAlchemyAlbumRepository(engine)
    service = AlbumService(album_repo, image_repo)

    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/a.jpg",
                capture_date=datetime(2022, 1, 1, tzinfo=UTC),
                year=2022,
                month=1,
                hash="ha",
                width=10,
                height=10,
                indexed_at=INDEXED_AT,
            ),
            Image(
                id=None,
                file_path="C:/b.jpg",
                capture_date=datetime(2024, 1, 1, tzinfo=UTC),
                year=2024,
                month=1,
                hash="hb",
                width=10,
                height=10,
                indexed_at=INDEXED_AT,
            ),
        ]
    )

    all_images = image_repo.list_all()
    image_a = next(image for image in all_images if image.file_path.endswith("a.jpg"))
    assert image_a.id is not None

    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_a.id,
                bbox=BoundingBox(0, 0, 1, 1),
                embedding=b"x",
                person_id=1,
            )
        ]
    )

    album = album_repo.create(
        Album(
            id=None,
            name="Test",
            query_definition=AlbumQuery(
                person_ids=(1,),
                date_from=date(2020, 1, 1),
                date_to=date(2023, 12, 31),
            ),
            query_version=1,
            created_at=CREATED_AT,
        )
    )

    assert album.id is not None

    page = service.list_album_images(album.id)
    assert len(page.items) == 1
    assert page.items[0].file_path.endswith("a.jpg")


def _image(path: str, captured: datetime | None, image_hash: str) -> Image:
    return Image(
        id=None,
        file_path=path,
        capture_date=captured,
        year=captured.year if captured else None,
        month=captured.month if captured else None,
        hash=image_hash,
        width=10,
        height=10,
        indexed_at=INDEXED_AT,
    )


def test_undated_images_split_and_excluded_from_date_filters() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo = SqlAlchemyImageRepository(engine)
    face_repo = SqlAlchemyFaceRepository(engine)
    album_repo = SqlAlchemyAlbumRepository(engine)
    service = AlbumService(album_repo, image_repo)

    image_repo.add_many(
        [
            _image("C:/dated.jpg", datetime(2022, 1, 1, tzinfo=UTC), "h1"),
            _image("C:/undated_face.png", None, "h2"),
            _image("C:/undated.png", None, "h3"),
        ]
    )
    ids = {Path(i.file_path).stem: i.id for i in image_repo.list_all()}
    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_id,
                bbox=BoundingBox(0, 0, 1, 1),
                embedding=b"x",
                person_id=1,
            )
            for image_id in (ids["dated"], ids["undated_face"])
            if image_id is not None
        ]
    )

    def names(page_items: list[Image]) -> list[str]:
        return [Path(i.file_path).stem for i in page_items]

    # Library tabs
    dated = service.list_library_images(query_definition={"has_date": True})
    undated = service.list_library_images(query_definition={"has_date": False})
    assert names(dated.items) == ["dated"]
    assert sorted(names(undated.items)) == ["undated", "undated_face"]

    def album(query: AlbumQuery) -> int:
        created = album_repo.create(
            Album(
                id=None,
                name="a",
                query_definition=query,
                query_version=1,
                created_at=CREATED_AT,
            )
        )
        assert created.id is not None
        return created.id

    # A date filter never matches undated images
    date_album = album(AlbumQuery(person_ids=(1,), date_from=date(2000, 1, 1)))
    assert names(service.list_album_images(date_album).items) == ["dated"]

    # A face-only album still includes undated images, sorted after dated ones
    face_album = album(AlbumQuery(person_ids=(1,)))
    assert names(service.list_album_images(face_album).items) == [
        "dated",
        "undated_face",
    ]

    library_album = album(AlbumQuery())
    assert service.is_unfiltered_album(library_album)
    assert not service.is_unfiltered_album(face_album)
