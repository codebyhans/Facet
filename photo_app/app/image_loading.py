"""Shared helper for loading full-resolution images into Qt widgets.

PIL's exif_transpose is used so photos display right-side-up regardless of
their EXIF orientation tag; QPixmap alone does not apply it.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageOps
from PySide6.QtGui import QPixmap

from photo_app.domain.value_objects import scale_bbox_to_fit_cell

if TYPE_CHECKING:
    from pathlib import Path

    from photo_app.domain.value_objects import BoundingBox
    from photo_app.infrastructure.thumbnail_tiles import ImageTileLookup

LOGGER = logging.getLogger(__name__)

_EXIF_ORIENTATION_TAG = 0x0112
_SWAPPED_DIMENSION_ORIENTATIONS = frozenset({5, 6, 7, 8})


def _read_exif_orientation(image: Image.Image) -> int:
    """Return the EXIF orientation tag (1-8) of an already-opened image, or 1."""
    try:
        return int(image.getexif().get(_EXIF_ORIENTATION_TAG, 1))
    except ValueError, TypeError, OSError:
        return 1


def _corrected_width(orientation: int, raw_width: int, raw_height: int) -> int:
    """Width after EXIF correction — swapped for the four 90°-rotation orientations."""
    return raw_height if orientation in _SWAPPED_DIMENSION_ORIENTATIONS else raw_width


def load_oriented_pixmap(path: Path) -> QPixmap:
    """Load `path` as a QPixmap with EXIF orientation applied."""
    try:
        with Image.open(path) as img:
            oriented = ImageOps.exif_transpose(img)
            if oriented.mode not in ("RGB", "RGBA"):
                oriented = oriented.convert("RGB")
            buf = io.BytesIO()
            oriented.save(buf, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buf.getvalue())
        if not pixmap.isNull():
            return pixmap
    except Exception:
        LOGGER.exception("Failed to load %s via PIL, falling back to QPixmap", path)
    return QPixmap(str(path))


@dataclass(frozen=True)
class DecodedImage:
    """Raw decoded pixels plus the photo's full (EXIF-corrected) size."""

    data: bytes  # tightly packed RGB, or RGBA when has_alpha
    width: int
    height: int
    has_alpha: bool
    source_width: int
    source_height: int


def decode_oriented_image(
    path: Path, max_size: tuple[int, int] | None = None
) -> DecodedImage | None:
    """Decode `path` with EXIF orientation applied, as raw pixel bytes.

    PIL-only (no Qt objects), so safe to call from a background thread;
    the caller wraps the bytes in a QImage on the main thread, which is a
    plain copy — no PNG encode/decode round-trip, which for a large photo
    cost as much as the decode itself. With `max_size`, the image is
    scaled down to fit (never up), using draft() so JPEGs decode at a
    reduced DCT scale instead of full resolution; without it, the full
    resolution is kept (for zooming in past the screen size).
    """
    try:
        with Image.open(path) as source:
            # See render_face_highlight_png: orientation/size must be read
            # before draft() (which mutates source.size) and before
            # exif_transpose() (which forces a full decode).
            orientation = _read_exif_orientation(source)
            raw_width, raw_height = source.size
            source_width = _corrected_width(orientation, raw_width, raw_height)
            source_height = _corrected_width(orientation, raw_height, raw_width)
            if source_width == 0 or source_height == 0:
                return None
            has_alpha = (
                source.mode in ("RGBA", "LA", "PA") or "transparency" in source.info
            )
            if max_size is not None:
                source.draft("RGB", max_size)
            oriented = ImageOps.exif_transpose(source).convert(
                "RGBA" if has_alpha else "RGB"
            )
        if max_size is not None:
            oriented.thumbnail(max_size, Image.Resampling.LANCZOS)
        return DecodedImage(
            data=oriented.tobytes(),
            width=oriented.width,
            height=oriented.height,
            has_alpha=has_alpha,
            source_width=source_width,
            source_height=source_height,
        )
    except Exception:
        LOGGER.exception("Failed to decode %s", path)
        return None


def render_face_highlight_png(
    path: Path, bbox: BoundingBox, max_size: tuple[int, int] = (120, 120)
) -> bytes | None:
    """Render a downscaled thumbnail of `path` with `bbox` boxed, as PNG bytes.

    Uses PIL only — no Qt objects — so it is safe to call from a background
    thread: the caller decodes the returned bytes into a QPixmap on the main
    thread, which is cheap since the heavy decode/resize already happened
    here. Applies EXIF orientation, matching `load_oriented_pixmap` — face
    detection stores bbox coordinates in EXIF-corrected space (see
    `face_index_service._extract_faces_for_image`), so the box only lands
    in the right place when the image is rotated the same way.
    """
    try:
        with Image.open(path) as source:
            # Orientation and raw size must be read before draft() below —
            # draft() mutates source.size in place to the reduced draft
            # resolution, and exif_transpose() would force a full decode if
            # called this early, defeating draft's whole purpose. Reading
            # just the orientation *tag* (header data, no pixel decode)
            # is enough to know the corrected width up front.
            orientation = _read_exif_orientation(source)
            raw_width, raw_height = source.size
            width = _corrected_width(orientation, raw_width, raw_height)
            # For JPEGs, hint libjpeg to decode at a reduced DCT scale instead
            # of decoding full-resolution pixels just to throw them away in
            # the thumbnail() resize below. No-op for formats that don't
            # support it (e.g. HEIC), which still fall through to a full decode.
            source.draft("RGB", max_size)
            oriented = ImageOps.exif_transpose(source).convert("RGB")
        if width == 0:
            return None
        oriented.thumbnail(max_size, Image.Resampling.LANCZOS)
        scale = oriented.width / width

        draw = ImageDraw.Draw(oriented)
        x0 = int(bbox.x * scale)
        y0 = int(bbox.y * scale)
        x1 = x0 + max(1, int(bbox.w * scale))
        y1 = y0 + max(1, int(bbox.h * scale))
        draw.rectangle((x0, y0, x1, y1), outline=(74, 158, 255), width=3)

        buf = io.BytesIO()
        oriented.save(buf, format="PNG")
    except Exception:
        LOGGER.exception("Failed to render face highlight for %s", path)
        return None
    return buf.getvalue()


def render_face_highlight_from_tile(
    lookup: ImageTileLookup, bbox: BoundingBox
) -> bytes | None:
    """Draw a face highlight box on an already-built thumbnail-tile crop,
    instead of opening/decoding the full-resolution source photo — the
    dominant cost of `render_face_highlight_png` at library scale (a full
    JPEG decode per photo, even with `draft()`'s reduced-DCT hint).

    Coordinate spaces line up without any conversion: tile cells are built
    via the same `ImageOps.exif_transpose(...).convert("RGB")` step face
    detection itself applies to compute bboxes (see
    `ThumbnailTileBuilder._load_thumbnail` and
    `FaceIndexService._extract_faces_for_image`), and `lookup.image_width`/
    `image_height` (from `Image.width`/`height`, which
    `image_index_service.py` stores post-orientation) describe that same
    corrected space. Only the scale differs: the tile cell fit the whole
    photo into a fixed `lookup.width` x `lookup.height` box, preserving
    aspect ratio and centering it (mirroring `PIL.Image.thumbnail`), so a
    bbox just needs that same scale-and-offset applied, not a full re-decode.

    Returns None if the tile file is missing/unreadable or the image's
    stored dimensions are degenerate — callers should fall back to
    `render_face_highlight_png` in that case (typically only for a photo
    whose tile hasn't been built yet).
    """
    if lookup.image_width <= 0 or lookup.image_height <= 0:
        return None
    try:
        with Image.open(lookup.tile_path) as atlas:
            cell = atlas.crop(
                (lookup.x, lookup.y, lookup.x + lookup.width, lookup.y + lookup.height)
            ).convert("RGB")
    except Exception:
        LOGGER.exception("Failed to read tile atlas %s", lookup.tile_path)
        return None

    x0, y0, x1, y1 = scale_bbox_to_fit_cell(
        bbox, lookup.image_width, lookup.image_height, lookup.width, lookup.height
    )
    draw = ImageDraw.Draw(cell)
    draw.rectangle((x0, y0, x1, y1), outline=(74, 158, 255), width=2)

    buf = io.BytesIO()
    cell.save(buf, format="PNG")
    return buf.getvalue()


def render_fit_to_size_png(
    path: Path, max_size: tuple[int, int]
) -> tuple[bytes, float] | None:
    """Decode `path` scaled to fit within `max_size`, as PNG bytes + scale.

    Uses PIL, unlike QPixmap: Qt has no built-in HEIC decoder, so
    `QPixmap(path)` on a HEIC file doesn't just fail, it spends real time
    (~0.5s observed) probing formats before giving up, on the main thread.
    PIL (via pillow-heif) decodes both HEIC and JPEG uniformly, and the
    draft-mode hint below avoids decoding full resolution just to shrink it.

    Applies EXIF orientation, matching `render_face_highlight_png` — the
    returned scale factor is relative to the EXIF-corrected width, which is
    the space callers overlaying bboxes on top of the returned image (e.g.
    the cluster inspector's "Faces" overlay) need, since detection stores
    bbox coordinates in that same corrected space.

    Returns (png_bytes, scale) where scale = returned_width / corrected_width,
    or None on failure.
    """
    try:
        with Image.open(path) as source:
            # See render_face_highlight_png: orientation/size must be read
            # before draft() (which mutates source.size) and before
            # exif_transpose() (which forces a full decode).
            orientation = _read_exif_orientation(source)
            raw_width, raw_height = source.size
            width = _corrected_width(orientation, raw_width, raw_height)
            if width == 0:
                return None
            source.draft("RGB", max_size)
            oriented = ImageOps.exif_transpose(source).convert("RGB")
        oriented.thumbnail(max_size, Image.Resampling.LANCZOS)
        scale = oriented.width / width
        buf = io.BytesIO()
        oriented.save(buf, format="PNG")
    except Exception:
        LOGGER.exception("Failed to load fit-to-size image for %s", path)
        return None
    return buf.getvalue(), scale


def render_face_crop_png(
    path: Path,
    bbox: BoundingBox,
    max_size: tuple[int, int] = (320, 320),
    context: float = 2.0,
) -> bytes | None:
    """Render a high-resolution square crop around `bbox`, as PNG bytes.

    The crop side is `context` x the face's larger dimension, so some hair
    and surroundings are included; it's shifted to stay inside the photo.
    PIL-only, so safe to call from a background thread. Applies EXIF
    orientation, matching the space face detection stores bboxes in (see
    `render_face_highlight_png`).
    """
    try:
        with Image.open(path) as source:
            # See render_face_highlight_png: orientation/size must be read
            # before draft() (which mutates source.size) and before
            # exif_transpose() (which forces a full decode).
            orientation = _read_exif_orientation(source)
            raw_width, raw_height = source.size
            width = _corrected_width(orientation, raw_width, raw_height)
            if width == 0:
                return None
            side = max(bbox.w, bbox.h) * context
            # Only decode as much resolution as the crop needs: ask draft()
            # for the whole image at the scale that makes the crop max_size.
            if side > 0:
                shrink = max(1.0, side / max_size[0])
                source.draft("RGB", (int(raw_width / shrink), int(raw_height / shrink)))
            oriented = ImageOps.exif_transpose(source).convert("RGB")
        scale = oriented.width / width

        crop_side = max(1, min(int(side * scale), oriented.width, oriented.height))
        center_x = (bbox.x + bbox.w / 2) * scale
        center_y = (bbox.y + bbox.h / 2) * scale
        left = int(min(max(0, center_x - crop_side / 2), oriented.width - crop_side))
        top = int(min(max(0, center_y - crop_side / 2), oriented.height - crop_side))
        crop = oriented.crop((left, top, left + crop_side, top + crop_side))
        crop.thumbnail(max_size, Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        crop.save(buf, format="PNG")
    except Exception:
        LOGGER.exception("Failed to render face crop for %s", path)
        return None
    return buf.getvalue()


def _cover_cache_path(
    cache_dir: Path,
    image_id: int,
    person_id: int,
    bbox: BoundingBox,
    max_size: tuple[int, int],
) -> Path:
    name = (
        f"{image_id}_{person_id}_{bbox.x}_{bbox.y}_{bbox.w}_{bbox.h}"
        f"_{max_size[0]}x{max_size[1]}.png"
    )
    return cache_dir / name


def get_or_render_face_highlight_png(  # noqa: PLR0913
    cache_dir: Path,
    image_id: int,
    person_id: int,
    image_path: Path,
    bbox: BoundingBox,
    max_size: tuple[int, int] = (120, 120),
    tile_lookup: ImageTileLookup | None = None,
) -> bytes | None:
    """Return a cached highlight-cover PNG, rendering and caching it on miss.

    The cache key is purely structural (image/person/bbox/size), so it
    self-invalidates whenever any of those change (a different cover image is
    picked, a face gets reassigned, etc.) without needing explicit
    invalidation — stale files are simply never looked up again.

    Rendering a full-resolution photo is the expensive part of loading the
    People tab (can be 100s of ms per photo); once cached, subsequent loads
    only need a small file read, making repeat People tab refreshes fast.

    When `tile_lookup` is given (the image's thumbnail tile has already
    been built), draws the highlight on that existing small crop instead —
    no full-resolution decode at all, not even on a cache miss. Falls back
    to the full decode only when there's no tile yet (e.g. a just-imported
    photo tiles haven't caught up to) or the tile read fails.
    """
    cache_path = _cover_cache_path(cache_dir, image_id, person_id, bbox, max_size)
    try:
        if cache_path.exists():
            return cache_path.read_bytes()
    except OSError:
        LOGGER.exception("Failed to read cached cover %s", cache_path)

    png_bytes = None
    if tile_lookup is not None:
        png_bytes = render_face_highlight_from_tile(tile_lookup, bbox)
    if png_bytes is None:
        png_bytes = render_face_highlight_png(image_path, bbox, max_size)
    if png_bytes is None:
        return None

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(png_bytes)
    except OSError:
        LOGGER.exception("Failed to write cached cover %s", cache_path)

    return png_bytes
