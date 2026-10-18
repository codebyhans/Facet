from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import create_engine

from photo_app.domain.models import Image
from photo_app.infrastructure.repositories import (
    SqlAlchemyCollectionRepository,
    SqlAlchemyImageRepository,
)
from photo_app.infrastructure.sqlalchemy_models import Base
from photo_app.services.collection_service import CollectionService
from photo_app.services.duplicate_detection_service import DuplicateDetectionService

if TYPE_CHECKING:
    from pathlib import Path


def _image(path: Path, *, rating: int | None = None) -> Image:
    return Image(
        id=None,
        file_path=str(path),
        capture_date=None,
        year=None,
        month=None,
        hash=f"h-{path}",
        width=10,
        height=10,
        indexed_at=datetime(2026, 1, 1, tzinfo=UTC),
        rating=rating,
    )


def _setup(
    tmp_path: Path,
) -> tuple[DuplicateDetectionService, CollectionService, dict[str, int]]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo = SqlAlchemyImageRepository(engine)
    collections = CollectionService(SqlAlchemyCollectionRepository(engine))

    # Same name and content in two folders, plus an unrelated file
    files = {
        "first": tmp_path / "a" / "IMG_1.jpg",
        "second": tmp_path / "b" / "IMG_1.jpg",
        "other": tmp_path / "IMG_2.jpg",
    }
    for key, path in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"other" if key == "other" else b"same bytes")
    # The non-collection copy has the higher rating, so only the collection
    # preference can make the second copy the keeper
    image_repo.add_many(
        [
            _image(files["first"], rating=5),
            _image(files["second"]),
            _image(files["other"]),
        ]
    )
    by_path = {i.file_path: i.id for i in image_repo.list_all() if i.id is not None}
    ids = {key: by_path[str(path)] for key, path in files.items()}
    service = DuplicateDetectionService(
        image_repo, collection_names=collections.collection_names_by_image
    )
    return service, collections, ids


def test_collection_names_by_image(tmp_path: Path) -> None:
    _service, collections, ids = _setup(tmp_path)
    book = collections.create_collection("Book")
    trip = collections.create_collection("all trips")
    assert book.id is not None
    assert trip.id is not None
    collections.add_images(book.id, [ids["second"]])
    collections.add_images(trip.id, [ids["second"], ids["other"]])

    names = collections.collection_names_by_image(list(ids.values()))

    assert names == {ids["second"]: ["all trips", "Book"], ids["other"]: ["all trips"]}


def test_keeper_prefers_collection_member(tmp_path: Path) -> None:
    service, collections, ids = _setup(tmp_path)
    book = collections.create_collection("Book")
    assert book.id is not None
    collections.add_images(book.id, [ids["second"]])

    [group] = service.find_exact_duplicates()

    assert group.suggested_keeper_id == ids["second"]
    assert group.collections == {ids["second"]: ["Book"]}


def test_keeper_without_collections_keeps_old_rules(tmp_path: Path) -> None:
    service, _collections, ids = _setup(tmp_path)

    [group] = service.find_exact_duplicates()

    assert group.suggested_keeper_id == ids["first"]  # higher rating
    assert group.collections == {}
