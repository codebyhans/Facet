from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date


@dataclass(frozen=True)
class BoundingBox:
    """Face location rectangle."""

    x: int
    y: int
    w: int
    h: int


def transform_bbox_for_exif_orientation(
    bbox: BoundingBox, orientation: int, width: int, height: int
) -> tuple[BoundingBox, int, int]:
    """Remap a bbox from raw (un-rotated) pixel space to EXIF-corrected space.

    `width`/`height` are the RAW image's dimensions (before any rotation) —
    the same space `bbox` is already in. Returns the transformed bbox plus
    the corrected image's (width, height), which are swapped for the four
    orientations that rotate 90°.

    Orientation values and their PIL `Image.transpose()` equivalents match
    `PIL.ImageOps.exif_transpose`'s own mapping (2->FLIP_LEFT_RIGHT,
    3->ROTATE_180, 4->FLIP_TOP_BOTTOM, 5->TRANSPOSE, 6->ROTATE_270,
    7->TRANSVERSE, 8->ROTATE_90); the coordinate formulas below were derived
    empirically (a marker rectangle transposed with real PIL calls, not
    worked out by hand) to avoid a subtly-wrong sign/axis on any one case.
    """
    x, y, w, h = bbox.x, bbox.y, bbox.w, bbox.h
    # Each entry: (new_bbox, swap_dimensions).
    transforms: dict[int, tuple[BoundingBox, bool]] = {
        1: (bbox, False),
        2: (BoundingBox(width - x - w, y, w, h), False),  # FLIP_LEFT_RIGHT
        3: (BoundingBox(width - x - w, height - y - h, w, h), False),  # ROTATE_180
        4: (BoundingBox(x, height - y - h, w, h), False),  # FLIP_TOP_BOTTOM
        5: (BoundingBox(y, x, h, w), True),  # TRANSPOSE
        6: (BoundingBox(height - y - h, x, h, w), True),  # ROTATE_270
        7: (BoundingBox(height - y - h, width - x - w, h, w), True),  # TRANSVERSE
        8: (BoundingBox(y, width - x - w, h, w), True),  # ROTATE_90
    }
    new_bbox, swap = transforms.get(orientation, (bbox, False))
    return (new_bbox, height, width) if swap else (new_bbox, width, height)


def scale_bbox_to_fit_cell(
    bbox: BoundingBox,
    image_width: int,
    image_height: int,
    cell_width: int,
    cell_height: int,
) -> tuple[int, int, int, int]:
    """Scale a bbox from an image's own (EXIF-corrected) pixel space into a
    thumbnail cell that image was fit into with preserved aspect ratio and
    centered — the same rule `PIL.Image.thumbnail` applies, which is how
    both the tile builder (`ThumbnailTileBuilder._load_thumbnail`) and the
    fallback full-decode renderer (`render_face_highlight_png`) produce
    their thumbnails. Framework-agnostic (plain ints, no PIL/Qt types) so
    both the PIL-based (`image_loading.render_face_highlight_from_tile`)
    and Qt-based (`ClusterImageModel`'s lazy per-row compositing) callers
    share one implementation instead of two copies that could drift.

    Returns (x0, y0, x1, y1) in the cell's own pixel space, clamped to be
    at least 1px wide/tall so a degenerate (e.g. 0-width) bbox still draws
    a visible mark instead of nothing.
    """
    if image_width <= 0 or image_height <= 0:
        return 0, 0, 0, 0
    scale = min(cell_width / image_width, cell_height / image_height)
    content_w = round(image_width * scale)
    content_h = round(image_height * scale)
    offset_x = (cell_width - content_w) // 2
    offset_y = (cell_height - content_h) // 2

    x0 = offset_x + bbox.x * scale
    y0 = offset_y + bbox.y * scale
    x1 = offset_x + (bbox.x + bbox.w) * scale
    y1 = offset_y + (bbox.y + bbox.h) * scale
    return int(x0), int(y0), max(int(x0) + 1, int(x1)), max(int(y0) + 1, int(y1))


@dataclass(frozen=True)
class AlbumQuery:
    """Typed virtual album query."""

    person_ids: tuple[int, ...] = ()
    # "OR" = images with any of person_ids, "AND" = images with all of them
    person_logic: str = "OR"
    cluster_ids: tuple[int, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    tag_names: tuple[str, ...] = ()
    rating_min: int | None = None
    quality_min: float | None = None
    camera_models: tuple[str, ...] = ()
    location_name: str | None = None
    gps_radius_km: float | None = None
    flags: tuple[str, ...] = ()
    # True = dated images only, False = undated only, None = both
    has_date: bool | None = None
