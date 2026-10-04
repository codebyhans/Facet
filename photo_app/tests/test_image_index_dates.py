from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlalchemy import create_engine

from photo_app.infrastructure.file_scanner import FileScanner
from photo_app.infrastructure.repositories import SqlAlchemyImageRepository
from photo_app.infrastructure.sqlalchemy_models import Base
from photo_app.services.image_index_service import ImageIndexService

if TYPE_CHECKING:
    from pathlib import Path

_MTIME = datetime(2024, 5, 17, 12, tzinfo=UTC).timestamp()


def _png(path: Path, xmp: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    info = PngInfo()
    if xmp is not None:
        info.add_itxt("XML:com.adobe.xmp", xmp)
    Image.new("RGB", (4, 4)).save(path, format="PNG", pnginfo=info)
    os.utime(path, (_MTIME, _MTIME))


def _index(root: Path) -> dict[str, datetime | None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    repo = SqlAlchemyImageRepository(engine)
    service = ImageIndexService(repo, FileScanner(), MagicMock())
    service.index_folder(root)
    return {
        img.file_path.rsplit(os.sep, 1)[-1]: img.capture_date for img in repo.list_all()
    }


def test_image_without_any_date_stays_undated(tmp_path: Path) -> None:
    _png(tmp_path / "undated" / "saved.png")

    dates = _index(tmp_path)

    assert dates == {"saved.png": None}


def test_folder_and_xmp_dates_are_used(tmp_path: Path) -> None:
    _png(tmp_path / "2023" / "2023-04-05" / "in_day_folder.png")
    _png(
        tmp_path / "Exports" / "screenshot.png",
        xmp="<x:xmpmeta><photoshop:DateCreated>2021-02-03T04:05:06"
        "</photoshop:DateCreated></x:xmpmeta>",
    )

    dates = _index(tmp_path)

    day = dates["in_day_folder.png"]
    shot = dates["screenshot.png"]
    assert day is not None
    assert (day.year, day.month, day.day) == (2023, 4, 5)
    assert shot is not None
    assert (shot.year, shot.month, shot.day, shot.hour) == (2021, 2, 3, 4)
