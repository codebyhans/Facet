from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING

from PIL import Image as PILImage
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from photo_app.app.models.cluster_image_model import ClusterImageModel
from photo_app.domain.value_objects import BoundingBox, scale_bbox_to_fit_cell
from photo_app.infrastructure.thumbnail_tiles import ImageTileLookup

if TYPE_CHECKING:
    from pathlib import Path

_THUMB_SIZE = 40
_IMAGES_PER_TILE = 4
_HIGHLIGHT_COLOR = (74, 158, 255)
_COLOR_TOLERANCE = 40


def _is_highlight_color(color: QColor) -> bool:
    rgb = (color.red(), color.green(), color.blue())
    return all(
        abs(c - t) < _COLOR_TOLERANCE
        for c, t in zip(rgb, _HIGHLIGHT_COLOR, strict=True)
    )


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


class _FakeTileStore:
    """Minimal stand-in exposing only what ClusterImageModel calls."""

    def __init__(self, tile_path: Path, image_ids: list[int]) -> None:
        self._lookups = {
            image_id: ImageTileLookup(
                image_id=image_id,
                tile_index=0,
                tile_path=tile_path,
                position_in_tile=i % _IMAGES_PER_TILE,
                x=(i % _IMAGES_PER_TILE) * _THUMB_SIZE,
                y=0,
                width=_THUMB_SIZE,
                height=_THUMB_SIZE,
                image_width=_THUMB_SIZE,
                image_height=_THUMB_SIZE,
            )
            for i, image_id in enumerate(image_ids)
        }

    def get_image_tiles_batch(self, image_ids: list[int]) -> dict[int, ImageTileLookup]:
        return {i: self._lookups[i] for i in image_ids if i in self._lookups}


def test_set_images_does_not_eagerly_decode_any_tiles(tmp_path: Path) -> None:
    """Regression test: set_images() must only fetch cheap lookup metadata,
    not decode/crop every thumbnail up front. For a person with thousands of
    images, eager decoding of every tile before the first paint made simply
    opening the detail view noticeably slow.
    """
    _app()
    tile_path = tmp_path / "tile_000001.png"
    PILImage.new("RGB", (_THUMB_SIZE * _IMAGES_PER_TILE, _THUMB_SIZE)).save(tile_path)

    image_ids = list(range(1, 101))  # a large-ish cluster
    image_paths = [f"C:/img_{i}.jpg" for i in image_ids]
    tile_store = _FakeTileStore(tile_path, image_ids)

    model = ClusterImageModel([], tile_store=tile_store)  # type: ignore[arg-type]
    model.set_images(image_paths, image_ids)

    assert model._tile_lookups, "lookup metadata should be fetched"
    assert model._tile_pixmap_cache == {}, "no tile sheet should be decoded yet"
    assert model._pixmap_cache == {}, "no thumbnail should be cropped yet"


def test_data_lazily_decodes_only_requested_row(tmp_path: Path) -> None:
    """Requesting one row's thumbnail must not force-decode the whole cluster."""
    _app()
    tile_path = tmp_path / "tile_000001.png"
    PILImage.new("RGB", (_THUMB_SIZE * _IMAGES_PER_TILE, _THUMB_SIZE)).save(tile_path)

    image_ids = list(range(1, 101))
    image_paths = [f"C:/img_{i}.jpg" for i in image_ids]
    tile_store = _FakeTileStore(tile_path, image_ids)

    model = ClusterImageModel([], tile_store=tile_store)  # type: ignore[arg-type]
    model.set_images(image_paths, image_ids)

    pixmap = model.data(model.index(0), Qt.ItemDataRole.DecorationRole)
    assert isinstance(pixmap, QPixmap)
    assert not pixmap.isNull()

    # Only the one tile sheet that row 0 lives in should have been decoded,
    # and only that one row's crop should be cached — not all 100.
    assert len(model._tile_pixmap_cache) == 1
    assert len(model._pixmap_cache) == 1


def test_load_thumbnail_draws_highlight_box_at_correct_scaled_position(
    tmp_path: Path,
) -> None:
    """The lazily-composited highlight (drawn with QPainter, directly on
    the cached tile crop) must land at the same scaled position the
    PIL-based `render_face_highlight_from_tile` path computes — both share
    `scale_bbox_to_fit_cell`, but this exercises the actual Qt drawing
    code, not just the geometry function underneath it.
    """
    _app()
    tile_path = tmp_path / "tile_000001.png"
    PILImage.new(
        "RGB", (_THUMB_SIZE * _IMAGES_PER_TILE, _THUMB_SIZE), color=(10, 20, 30)
    ).save(tile_path)

    image_id = 1
    tile_store = _FakeTileStore(tile_path, [image_id])
    # Swap in a non-square source (2:1 landscape) so scale/offset actually
    # differ per axis, not just a trivial square-to-square case.
    tile_store._lookups[image_id] = dataclasses.replace(
        tile_store._lookups[image_id],
        image_width=80,
        image_height=40,
    )

    model = ClusterImageModel([], tile_store=tile_store)  # type: ignore[arg-type]
    bbox = BoundingBox(x=60, y=20, w=10, h=10)
    model.set_images(["C:/img_1.jpg"], [image_id], bbox_by_image_id={image_id: bbox})

    pixmap = model.data(model.index(0), Qt.ItemDataRole.DecorationRole)
    assert isinstance(pixmap, QPixmap)
    image = pixmap.toImage()

    x0, y0, x1, _y1 = scale_bbox_to_fit_cell(
        bbox, 80, 40, image.width(), image.height()
    )
    center_x = (x0 + x1) // 2
    assert _is_highlight_color(QColor(image.pixel(center_x, y0))), (
        "highlight box missing at the expected scaled position"
    )

    # A corner far from the box, in the tile's own padding area, must not
    # be highlight-colored — catches a wrong scale/offset that still draws
    # *a* box, just not where the bbox says.
    assert not _is_highlight_color(QColor(image.pixel(2, 2))), (
        "highlight color found far from the expected position"
    )
