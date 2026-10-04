from __future__ import annotations

import io
from typing import TYPE_CHECKING

from PIL import Image

from photo_app.app.image_loading import (
    render_face_highlight_png,
    render_fit_to_size_png,
)
from photo_app.domain.value_objects import (
    BoundingBox,
    transform_bbox_for_exif_orientation,
)

if TYPE_CHECKING:
    from pathlib import Path

# Raw (un-rotated) file dimensions and marker, matching what a face detector
# would have found before this fix — i.e. before EXIF orientation is applied.
RAW_WIDTH = 40
RAW_HEIGHT = 60
RAW_MARKER = BoundingBox(x=5, y=8, w=10, h=14)
ORIENTATION_90_CW_NEEDED = 6  # a common real-world case (e.g. phone held sideways)
OUTLINE_COLOR = (74, 158, 255)
MAX_SIZE = (100, 100)
POSITION_TOLERANCE_PX = 4  # accounts for LANCZOS resize rounding + 3px stroke width
_RED_CHANNEL_MIN = 150
_OTHER_CHANNEL_MAX = 100


def _make_rotated_marker_jpeg(path: Path) -> None:
    """Write a JPEG whose raw pixels are un-rotated, with orientation=6 in EXIF.

    Mirrors what a real phone photo looks like: the file's stored pixel grid
    doesn't match its intended display orientation.
    """
    img = Image.new("RGB", (RAW_WIDTH, RAW_HEIGHT), (0, 0, 0))
    for x in range(RAW_MARKER.x, RAW_MARKER.x + RAW_MARKER.w):
        for y in range(RAW_MARKER.y, RAW_MARKER.y + RAW_MARKER.h):
            img.putpixel((x, y), (255, 0, 0))
    exif = img.getexif()
    exif[0x0112] = ORIENTATION_90_CW_NEEDED
    img.save(path, format="JPEG", exif=exif)


def _find_reddish_bbox(img: Image.Image) -> BoundingBox:
    """Bounding box of the marker region — tolerant of JPEG/resize drift."""
    xs = []
    ys = []
    for x in range(img.width):
        for y in range(img.height):
            r, g, b = img.getpixel((x, y))
            if (
                r > _RED_CHANNEL_MIN
                and g < _OTHER_CHANNEL_MAX
                and b < _OTHER_CHANNEL_MAX
            ):
                xs.append(x)
                ys.append(y)
    assert xs, "reddish marker not found anywhere in the image"
    return BoundingBox(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def _find_color_bbox(img: Image.Image, color: tuple[int, int, int]) -> BoundingBox:
    """Bounding box of exact-color pixels — used for the freshly-drawn outline,
    which is written straight into the (lossless) PNG output, so no drift.
    """
    xs = []
    ys = []
    for x in range(img.width):
        for y in range(img.height):
            if img.getpixel((x, y)) == color:
                xs.append(x)
                ys.append(y)
    assert xs, f"color {color} not found anywhere in the image"
    return BoundingBox(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def test_render_fit_to_size_png_scale_matches_corrected_width(tmp_path: Path) -> None:
    """The returned scale must be relative to the EXIF-corrected width, not
    the raw file width — otherwise every bbox overlay computed from it
    lands in the wrong place for any rotated photo.
    """
    path = tmp_path / "rotated.jpg"
    _make_rotated_marker_jpeg(path)

    result = render_fit_to_size_png(path, MAX_SIZE)
    assert result is not None
    png_bytes, scale = result

    output = Image.open(io.BytesIO(png_bytes))
    # orientation 6 swaps dimensions: corrected width = raw height.
    expected_corrected_width = RAW_HEIGHT
    assert scale == output.width / expected_corrected_width


def test_render_face_highlight_png_box_lands_on_the_real_marker(
    tmp_path: Path,
) -> None:
    """A bbox given in EXIF-corrected space must box the marker where it
    actually ends up after real orientation correction — this is the
    end-to-end regression test for the "misaligned highlight" class of bug.
    """
    path = tmp_path / "rotated.jpg"
    _make_rotated_marker_jpeg(path)

    # This is what a post-fix detector would have stored: the bbox already
    # transformed into corrected space.
    corrected_bbox, corrected_width, _ = transform_bbox_for_exif_orientation(
        RAW_MARKER, ORIENTATION_90_CW_NEEDED, RAW_WIDTH, RAW_HEIGHT
    )

    png_bytes = render_face_highlight_png(path, corrected_bbox, max_size=MAX_SIZE)
    assert png_bytes is not None
    output = Image.open(io.BytesIO(png_bytes)).convert("RGB")

    marker_bbox = _find_reddish_bbox(output)
    outline_bbox = _find_color_bbox(output, OUTLINE_COLOR)

    scale = output.width / corrected_width
    expected_x = corrected_bbox.x * scale
    expected_y = corrected_bbox.y * scale

    # The drawn outline's top-left should sit right around the marker's
    # actual position (a few px out for the stroke width), not somewhere
    # else in the frame.
    assert abs(outline_bbox.x - expected_x) <= POSITION_TOLERANCE_PX
    assert abs(outline_bbox.y - expected_y) <= POSITION_TOLERANCE_PX
    # And the marker itself should be inside (or touching) the drawn box.
    assert outline_bbox.x <= marker_bbox.x + POSITION_TOLERANCE_PX
    assert outline_bbox.y <= marker_bbox.y + POSITION_TOLERANCE_PX
