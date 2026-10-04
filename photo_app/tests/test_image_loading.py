from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from PIL import Image as PILImage
from sqlalchemy import create_engine

from photo_app.app.image_loading import (
    get_or_render_face_highlight_png,
    render_face_highlight_from_tile,
    render_face_highlight_png,
    render_fit_to_size_png,
)
from photo_app.domain.models import Image as ImageEntity
from photo_app.domain.value_objects import BoundingBox
from photo_app.infrastructure.repositories import SqlAlchemyImageRepository
from photo_app.infrastructure.sqlalchemy_models import Base
from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_CACHE_FILE_COUNT_TWO_BBOXES = 2
_SCALE_TOLERANCE = 1e-6
_BOX_COLOR = (74, 158, 255)
_COLOR_TOLERANCE = 40


def _has_box_color(rgb: tuple[int, int, int]) -> bool:
    return all(
        abs(c - t) < _COLOR_TOLERANCE for c, t in zip(rgb, _BOX_COLOR, strict=True)
    )


def _make_jpeg(path: Path, size: tuple[int, int] = (400, 300)) -> None:
    PILImage.new("RGB", size, color=(10, 20, 30)).save(path, format="JPEG")


def test_get_or_render_face_highlight_png_caches_to_disk(tmp_path: Path) -> None:
    image_path = tmp_path / "source.jpg"
    _make_jpeg(image_path)
    cache_dir = tmp_path / "cache"
    bbox = BoundingBox(x=10, y=10, w=40, h=40)

    first = get_or_render_face_highlight_png(cache_dir, 1, 2, image_path, bbox)
    assert first is not None

    cache_files = list(cache_dir.glob("*.png"))
    assert len(cache_files) == 1

    # Delete the source photo: a cache hit must not need to re-read it.
    image_path.unlink()
    second = get_or_render_face_highlight_png(cache_dir, 1, 2, image_path, bbox)
    assert second == first


def test_get_or_render_face_highlight_png_cache_key_varies_with_bbox(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "source.jpg"
    _make_jpeg(image_path)
    cache_dir = tmp_path / "cache"

    bbox_a = BoundingBox(x=10, y=10, w=40, h=40)
    bbox_b = BoundingBox(x=100, y=100, w=40, h=40)

    get_or_render_face_highlight_png(cache_dir, 1, 2, image_path, bbox_a)
    get_or_render_face_highlight_png(cache_dir, 1, 2, image_path, bbox_b)

    # Different bbox (e.g. after a face reassignment) must not reuse a stale
    # cached render for the same image/person pair.
    assert len(list(cache_dir.glob("*.png"))) == EXPECTED_CACHE_FILE_COUNT_TWO_BBOXES


def test_render_face_highlight_places_box_within_bounds_for_large_jpeg(
    tmp_path: Path,
) -> None:
    """Regression test for a scale-factor bug from JPEG draft-mode decoding.

    PIL's Image.draft() mutates the image's reported size in place to the
    reduced draft resolution. Reading the "original" width AFTER calling
    draft() (instead of before) uses that much-smaller size as the scale
    baseline instead of the true original, inflating the scale factor and
    pushing the highlight box completely outside the rendered thumbnail —
    so the box silently never appears, without any error being raised.
    """
    image_path = tmp_path / "large.jpg"
    # Large enough that JPEG draft-mode decoding actually kicks in.
    PILImage.new("RGB", (3000, 2000), color=(10, 20, 30)).save(
        image_path, format="JPEG"
    )

    # A bbox roughly in the middle of the original (full-resolution) image.
    bbox = BoundingBox(x=1400, y=900, w=200, h=200)
    png_bytes = render_face_highlight_png(image_path, bbox)
    assert png_bytes is not None

    result = PILImage.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = result.size
    pixels = result.load()

    has_box = any(_has_box_color(pixels[x, y]) for x in range(w) for y in range(h))
    assert has_box, "highlight box must be visible within the thumbnail bounds"


def test_render_fit_to_size_shrinks_large_image_and_reports_correct_scale(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "large.jpg"
    PILImage.new("RGB", (3000, 2000), color=(10, 20, 30)).save(
        image_path, format="JPEG"
    )

    result = render_fit_to_size_png(image_path, (400, 300))
    assert result is not None
    png_bytes, scale = result

    decoded = PILImage.open(io.BytesIO(png_bytes))
    # 3000x2000 fit within 400x300 -> width is the binding dimension (400/3000).
    assert decoded.size == (400, 267)
    assert abs(scale - decoded.width / 3000) < _SCALE_TOLERANCE


def test_render_fit_to_size_does_not_upscale_small_image(tmp_path: Path) -> None:
    image_path = tmp_path / "small.jpg"
    PILImage.new("RGB", (100, 80), color=(10, 20, 30)).save(image_path, format="JPEG")

    result = render_fit_to_size_png(image_path, (400, 300))
    assert result is not None
    png_bytes, scale = result

    decoded = PILImage.open(io.BytesIO(png_bytes))
    assert decoded.size == (100, 80)
    assert scale == 1.0


def test_render_face_highlight_from_tile_places_box_at_correct_scaled_position(
    tmp_path: Path,
) -> None:
    """The tile-based highlight renderer must place the box at the position
    the tile's own fit-within-and-center scaling implies — verified against
    a real `ThumbnailTileBuilder`-built tile (not a hand-reimplemented
    scale), the same empirical-not-assumed standard used for the EXIF bbox
    transform elsewhere in this codebase. Also confirms the box does *not*
    show up somewhere else in the cell, which a wrong scale/offset would
    still happily draw.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    width, height = 300, 150  # 2:1 landscape — width is the binding dimension
    image_path = tmp_path / "photo.jpg"
    PILImage.new("RGB", (width, height), color=(10, 20, 30)).save(
        image_path, format="JPEG"
    )

    image_repo = SqlAlchemyImageRepository(engine)
    image_repo.add_many(
        [
            ImageEntity(
                id=None,
                file_path=str(image_path),
                capture_date=None,
                year=None,
                month=None,
                hash="h1",
                width=width,
                height=height,
                indexed_at=datetime.now(UTC),
            )
        ]
    )
    stored = image_repo.get_by_path(str(image_path))
    assert stored is not None
    assert stored.id is not None

    tile_store = ThumbnailTileStore(
        engine,
        cache_directory=tmp_path / "cache",
        tile_size=(1024, 1024),
        thumbnail_size=(128, 128),
        images_per_tile=64,
    )
    build_result = tile_store.build_missing_tiles()
    assert build_result.images_built == 1

    lookup = tile_store.get_image_tile(stored.id)
    assert lookup is not None
    assert lookup.image_width == width
    assert lookup.image_height == height

    bbox = BoundingBox(x=200, y=100, w=20, h=20)
    png_bytes = render_face_highlight_from_tile(lookup, bbox)
    assert png_bytes is not None

    result = PILImage.open(io.BytesIO(png_bytes)).convert("RGB")
    assert result.size == (lookup.width, lookup.height)
    pixels = result.load()

    # Independently recompute where the box should land — fit-within,
    # preserve-aspect, center is the same rule PIL's own .thumbnail() used
    # to build this tile cell in the first place.
    scale = min(lookup.width / width, lookup.height / height)
    content_w = round(width * scale)
    content_h = round(height * scale)
    offset_x = (lookup.width - content_w) // 2
    offset_y = (lookup.height - content_h) // 2
    expected_x0 = offset_x + bbox.x * scale
    expected_y0 = offset_y + bbox.y * scale
    expected_x1 = offset_x + (bbox.x + bbox.w) * scale
    center_x = int((expected_x0 + expected_x1) / 2)
    top_y = int(expected_y0)

    assert _has_box_color(pixels[center_x, top_y]), (
        "box outline missing at the expected scaled position"
    )

    # A point near the tile's own top-left padding corner must not be
    # box-colored — catches a wrong scale/offset that still draws *a* box
    # somewhere, just not where the bbox actually says.
    far_x, far_y = offset_x + 2, offset_y + 2
    assert not _has_box_color(pixels[far_x, far_y]), (
        "box color found far from the expected position — scale/offset is wrong"
    )
