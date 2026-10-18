from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from sqlalchemy import create_engine

from photo_app.domain.models import Image
from photo_app.infrastructure.repositories import SqlAlchemyImageRepository
from photo_app.infrastructure.sqlalchemy_models import Base
from photo_app.services.image_index_service import (
    ImageIndexService,
    find_companion_files,
)

if TYPE_CHECKING:
    from pathlib import Path


def _setup(
    tmp_path: Path, names: list[str], indexed: list[str]
) -> tuple[ImageIndexService, SqlAlchemyImageRepository]:
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    repo = SqlAlchemyImageRepository(engine)
    repo.add_many(
        [
            Image(
                id=None,
                file_path=str(tmp_path / name),
                capture_date=None,
                year=None,
                month=None,
                hash=name,
                width=1,
                height=1,
                indexed_at=datetime.now(UTC),
            )
            for name in indexed
        ]
    )
    return ImageIndexService(repo, MagicMock(), MagicMock()), repo


def _id(repo: SqlAlchemyImageRepository, path: Path) -> int:
    image = repo.get_by_path(str(path))
    assert image is not None
    assert image.id is not None
    return image.id


def test_companions_are_same_stem_raw_and_mov_only(tmp_path: Path) -> None:
    for name in ["IMG_1.JPG", "IMG_1.CR2", "img_1.mov", "IMG_1.xmp", "IMG_10.CR2"]:
        (tmp_path / name).write_bytes(b"x")

    companions = find_companion_files(str(tmp_path / "IMG_1.JPG"))

    assert sorted(p.name for p in companions) == ["IMG_1.CR2", "img_1.mov"]


def test_raw_and_video_have_no_companions(tmp_path: Path) -> None:
    for name in ["IMG_1.JPG", "IMG_1.CR2", "IMG_1.MOV"]:
        (tmp_path / name).write_bytes(b"x")

    assert find_companion_files(str(tmp_path / "IMG_1.CR2")) == []
    assert find_companion_files(str(tmp_path / "IMG_1.MOV")) == []


def test_delete_removes_raw_and_live_video_with_photo(tmp_path: Path) -> None:
    service, repo = _setup(
        tmp_path,
        ["IMG_1.JPG", "IMG_1.CR2", "IMG_1.MOV", "IMG_2.JPG"],
        indexed=["IMG_1.JPG", "IMG_1.CR2", "IMG_2.JPG"],
    )
    target = _id(repo, tmp_path / "IMG_1.JPG")

    assert service.count_companion_files([target]) == (1, 1)
    result = service.delete_images([target])

    assert (result.deleted, result.failed, result.companions_deleted) == (1, 0, 2)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["IMG_2.JPG"]
    assert [img.file_path for img in repo.list_all()] == [str(tmp_path / "IMG_2.JPG")]


def test_deleting_lone_raw_keeps_jpeg(tmp_path: Path) -> None:
    service, repo = _setup(
        tmp_path, ["IMG_1.JPG", "IMG_1.CR2"], indexed=["IMG_1.JPG", "IMG_1.CR2"]
    )

    service.delete_images([_id(repo, tmp_path / "IMG_1.CR2")])

    assert sorted(p.name for p in tmp_path.iterdir()) == ["IMG_1.JPG"]
