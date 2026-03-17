from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from PIL import Image as PilImage
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from photo_app.domain.models import Album, Face, Image
from photo_app.domain.value_objects import AlbumQuery, BoundingBox
from photo_app.infrastructure.repositories import (
    SqlAlchemyAlbumRepository,
    SqlAlchemyFaceRepository,
    SqlAlchemyImageRepository,
)
from photo_app.infrastructure.sqlalchemy_models import AlbumQueryCacheModel, Base
from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
from photo_app.services.album_query_cache_service import AlbumQueryCacheService

if TYPE_CHECKING:
    from pathlib import Path

INDEXED_AT = datetime(2026, 1, 1, tzinfo=UTC)
CREATED_AT = datetime(2026, 1, 2, tzinfo=UTC)
EXPECTED_IMAGES_BUILT = 3
EXPECTED_CACHED_ROWS = 2
QUERY_VERSION_1 = 1
QUERY_VERSION_2 = 2


def _make_image(path: Path, color: tuple[int, int, int]) -> None:
    image = PilImage.new("RGB", (640, 480), color=color)
    image.save(path, format="JPEG")


def test_thumbnail_tile_generation_and_lookup(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    image_paths = [tmp_path / "a.jpg", tmp_path / "b.jpg", tmp_path / "c.jpg"]
    _make_image(image_paths[0], (255, 0, 0))
    _make_image(image_paths[1], (0, 255, 0))
    _make_image(image_paths[2], (0, 0, 255))

    with Session(engine) as session:
        image_repo = SqlAlchemyImageRepository(session)
        image_repo.add_many(
            [
                Image(
                    id=None,
                    file_path=str(path),
                    capture_date=date(2024, 1, idx + 1),
                    year=2024,
                    month=1,
                    hash=f"h{idx}",
                    width=640,
                    height=480,
                    indexed_at=INDEXED_AT,
                )
                for idx, path in enumerate(image_paths)
            ]
        )
        session.flush()

        store = ThumbnailTileStore(
            session,
            cache_directory=tmp_path / "cache",
            tile_size=(256, 256),
            thumbnail_size=(64, 64),
            images_per_tile=16,
        )
        result = store.build_missing_tiles()
        session.commit()

        assert result.images_built == EXPECTED_IMAGES_BUILT
        assert result.tiles_built == 1

        images = image_repo.list_all()
        first = images[0]
        assert first.id is not None
        lookup = store.get_image_tile(first.id)
        assert lookup is not None
        assert lookup.position_in_tile == 0
        assert lookup.x == 0
        assert lookup.y == 0

        tile_path = store.get_tile(lookup.tile_index)
        assert tile_path is not None
        assert tile_path.exists()


def test_album_query_cache_hit_miss_and_invalidation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        image_repo = SqlAlchemyImageRepository(session)
        face_repo = SqlAlchemyFaceRepository(session)
        album_repo = SqlAlchemyAlbumRepository(session)
        cache_service = AlbumQueryCacheService(session, album_repo, image_repo)

        image_repo.add_many(
            [
                Image(
                    id=None,
                    file_path="C:/1.jpg",
                    capture_date=date(2022, 1, 1),
                    year=2022,
                    month=1,
                    hash="h1",
                    width=10,
                    height=10,
                    indexed_at=INDEXED_AT,
                ),
                Image(
                    id=None,
                    file_path="C:/2.jpg",
                    capture_date=date(2023, 1, 1),
                    year=2023,
                    month=1,
                    hash="h2",
                    width=10,
                    height=10,
                    indexed_at=INDEXED_AT,
                ),
                Image(
                    id=None,
                    file_path="C:/3.jpg",
                    capture_date=date(2024, 1, 1),
                    year=2024,
                    month=1,
                    hash="h3",
                    width=10,
                    height=10,
                    indexed_at=INDEXED_AT,
                ),
            ]
        )
        session.flush()

        by_path = {image.file_path: image for image in image_repo.list_all()}
        assert by_path["C:/1.jpg"].id is not None
        assert by_path["C:/2.jpg"].id is not None
        assert by_path["C:/3.jpg"].id is not None

        face_repo.add_many(
            [
                Face(
                    id=None,
                    image_id=int(by_path["C:/1.jpg"].id),
                    bbox=BoundingBox(0, 0, 1, 1),
                    embedding=b"x",
                    person_id=7,
                ),
                Face(
                    id=None,
                    image_id=int(by_path["C:/2.jpg"].id),
                    bbox=BoundingBox(0, 0, 1, 1),
                    embedding=b"y",
                    person_id=7,
                ),
            ]
        )

        album = album_repo.create(
            Album(
                id=None,
                name="People",
                query_definition=AlbumQuery(person_ids=(7,), date_from=None, date_to=None),
                query_version=QUERY_VERSION_1,
                created_at=CREATED_AT,
            )
        )
        session.flush()
        assert album.id is not None

        # Cache miss: compute and store.
        first_ids = cache_service.resolve_album(album.id)
        assert first_ids == [int(by_path["C:/1.jpg"].id), int(by_path["C:/2.jpg"].id)]

        cached_rows = session.scalars(
            select(AlbumQueryCacheModel).where(AlbumQueryCacheModel.album_id == album.id)
        ).all()
        assert len(cached_rows) == EXPECTED_CACHED_ROWS
        assert {row.query_version for row in cached_rows} == {QUERY_VERSION_1}

        # Cache hit.
        second_ids = cache_service.resolve_album(album.id)
        assert second_ids == first_ids

        # Invalidate via query version bump.
        updated = album_repo.update_query(
            album.id,
            AlbumQuery(person_ids=(7,), date_from=date(2023, 1, 1), date_to=None),
        )
        assert updated is not None
        assert updated.query_version == QUERY_VERSION_2
        session.flush()

        page = cache_service.get_album_images(album.id, offset=0, limit=10)
        assert [image.file_path for image in page] == ["C:/2.jpg"]

        refreshed_rows = session.scalars(
            select(AlbumQueryCacheModel).where(AlbumQueryCacheModel.album_id == album.id)
        ).all()
        assert len(refreshed_rows) == 1
        assert {row.query_version for row in refreshed_rows} == {QUERY_VERSION_2}
