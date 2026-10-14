from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from photo_app.domain.models import Image
from photo_app.infrastructure.repositories import (
    SqlAlchemyAlbumRepository,
    SqlAlchemyCollectionRepository,
    SqlAlchemyImageRepository,
)
from photo_app.infrastructure.sqlalchemy_models import Base
from photo_app.services.album_query_cache_service import AlbumQueryCacheService
from photo_app.services.album_service import AlbumService
from photo_app.services.collection_service import (
    CollectionNameError,
    CollectionService,
)

INDEXED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _image(stem: str, year: int, flag: str | None = None) -> Image:
    return Image(
        id=None,
        file_path=f"C:/{stem}.jpg",
        capture_date=datetime(year, 6, 1, tzinfo=UTC),
        year=year,
        month=6,
        hash=f"h-{stem}",
        width=10,
        height=10,
        indexed_at=INDEXED_AT,
        flag=flag,
    )


def _setup(
    *, cached: bool = False
) -> tuple[AlbumService, CollectionService, dict[str, int], SqlAlchemyImageRepository]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo = SqlAlchemyImageRepository(engine)
    album_repo = SqlAlchemyAlbumRepository(engine)
    cache = AlbumQueryCacheService(engine, album_repo, image_repo) if cached else None
    albums = AlbumService(album_repo, image_repo, query_cache_service=cache)
    collections = CollectionService(SqlAlchemyCollectionRepository(engine), cache)

    image_repo.add_many([_image("a24", 2024), _image("b25", 2025), _image("c25", 2025)])
    ids = {
        Path(i.file_path).stem: i.id for i in image_repo.list_all() if i.id is not None
    }
    return albums, collections, ids, image_repo


def _names(albums: AlbumService, album_id: int, **kwargs: object) -> list[str]:
    page = albums.list_album_images(album_id, **kwargs)  # type: ignore[arg-type]
    return [Path(i.file_path).stem for i in page.items]


def test_membership_add_remove_and_counts() -> None:
    _albums, collections, ids, _ = _setup()
    book = collections.create_collection("  Book 2025 ")
    assert book.id is not None
    assert book.name == "Book 2025"

    assert collections.add_images(book.id, [ids["b25"], ids["c25"]]) == 2  # noqa: PLR2004
    # Re-adding is a no-op for images already in the collection
    assert collections.add_images(book.id, [ids["c25"], ids["a24"]]) == 1
    assert [c.image_count for c in collections.list_collections()] == [3]

    assert collections.remove_images(book.id, [ids["a24"], 9999]) == 1
    containing = collections.collections_containing([ids["b25"]])
    assert [c.name for c in containing] == ["Book 2025"]
    assert collections.collections_containing([ids["a24"]]) == []


def test_names_must_be_unique_and_non_empty() -> None:
    _albums, collections, _ids, _ = _setup()
    book = collections.create_collection("Book 2025")
    assert book.id is not None
    with pytest.raises(CollectionNameError):
        collections.create_collection("book 2025")
    with pytest.raises(CollectionNameError):
        collections.create_collection("   ")
    # Renaming to its own name (e.g. a case fix) is allowed
    renamed = collections.rename_collection(book.id, "BOOK 2025")
    assert renamed is not None
    assert renamed.name == "BOOK 2025"


def test_album_scoped_to_and_excluding_collections() -> None:
    albums, collections, ids, _ = _setup()
    book = collections.create_collection("Book 2025")
    other = collections.create_collection("Calendar")
    assert book.id is not None
    assert other.id is not None
    collections.add_images(book.id, [ids["b25"]])
    collections.add_images(other.id, [ids["a24"]])

    in_book = albums.create_album("picked", {"collection_ids": [book.id]})
    either = albums.create_album("either", {"collection_ids": [book.id, other.id]})
    left_over = albums.create_album(
        "2025 not picked",
        {
            "date_from": "2025-01-01",
            "date_to": "2025-12-31",
            "exclude_collection_ids": [book.id],
        },
    )
    assert in_book.id is not None
    assert either.id is not None
    assert left_over.id is not None

    assert _names(albums, in_book.id) == ["b25"]
    assert _names(albums, either.id) == ["a24", "b25"]
    assert _names(albums, left_over.id) == ["c25"]
    assert albums.count_album_images(left_over.id) == 1

    # The criteria survive a round trip through the database
    reloaded = next(a for a in albums.list_albums() if a.id == left_over.id)
    assert reloaded.query_definition.exclude_collection_ids == (book.id,)


def test_view_filter_narrows_album_instead_of_replacing_it() -> None:
    albums, collections, ids, _ = _setup()
    book = collections.create_collection("Book 2025")
    other = collections.create_collection("Calendar")
    assert book.id is not None
    assert other.id is not None
    collections.add_images(book.id, [ids["b25"], ids["c25"]])
    collections.add_images(other.id, [ids["a24"], ids["c25"]])

    album = albums.create_album("picked", {"collection_ids": [book.id]})
    assert album.id is not None

    # Filtering by another collection intersects; it must not leak "a24"
    assert _names(
        albums, album.id, query_definition={"collection_ids": [other.id]}
    ) == ["c25"]
    # Same for a date filter that lies outside the album
    assert (
        _names(
            albums,
            album.id,
            query_definition={"date_from": "2024-01-01", "date_to": "2024-12-31"},
        )
        == []
    )


def test_saved_flag_criteria_are_applied() -> None:
    albums, _collections, ids, image_repo = _setup(cached=True)
    image_repo.update_flag(ids["b25"], "keep")
    album = albums.create_album("keepers", {"flags": ["keep"]})
    assert album.id is not None
    assert _names(albums, album.id) == ["b25"]
    assert albums.count_album_images(album.id) == 1


def test_membership_change_refreshes_cached_album() -> None:
    albums, collections, ids, _ = _setup(cached=True)
    book = collections.create_collection("Book 2025")
    assert book.id is not None
    collections.add_images(book.id, [ids["b25"]])
    album = albums.create_album("picked", {"collection_ids": [book.id]})
    assert album.id is not None

    assert _names(albums, album.id) == ["b25"]  # fills the cache
    collections.add_images(book.id, [ids["c25"]])
    assert _names(albums, album.id) == ["b25", "c25"]
    collections.remove_images(book.id, [ids["b25"]])
    assert _names(albums, album.id) == ["c25"]


def test_deleting_images_and_collections_cleans_up_membership() -> None:
    _albums, collections, ids, image_repo = _setup()
    book = collections.create_collection("Book 2025")
    assert book.id is not None
    collections.add_images(book.id, [ids["b25"], ids["c25"]])

    assert image_repo.delete_by_paths(["C:/b25.jpg"]) == 1
    remaining = collections.get_collection(book.id)
    assert remaining is not None
    assert remaining.image_count == 1

    assert collections.delete_collection(book.id)
    assert collections.list_collections() == []
    # The images themselves are untouched
    assert image_repo.get_by_id(ids["c25"]) is not None
