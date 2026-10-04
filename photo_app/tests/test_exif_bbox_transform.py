from __future__ import annotations

import pytest
from PIL import Image

from photo_app.domain.value_objects import (
    BoundingBox,
    transform_bbox_for_exif_orientation,
)

WIDTH = 5
HEIGHT = 9
MARKER = BoundingBox(x=1, y=1, w=2, h=3)

_METHOD_BY_ORIENTATION = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


def _marker_image() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    for x in range(MARKER.x, MARKER.x + MARKER.w):
        for y in range(MARKER.y, MARKER.y + MARKER.h):
            img.putpixel((x, y), (255, 0, 0))
    return img


def _marker_bbox(img: Image.Image) -> BoundingBox:
    xs = []
    ys = []
    for x in range(img.width):
        for y in range(img.height):
            if img.getpixel((x, y)) == (255, 0, 0):
                xs.append(x)
                ys.append(y)
    return BoundingBox(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def test_orientation_1_is_identity() -> None:
    bbox, w, h = transform_bbox_for_exif_orientation(MARKER, 1, WIDTH, HEIGHT)
    assert (bbox, w, h) == (MARKER, WIDTH, HEIGHT)


@pytest.mark.parametrize("orientation", [2, 3, 4, 5, 6, 7, 8])
def test_transform_matches_real_pil_transpose(orientation: int) -> None:
    """The transformed bbox must bound the marker after the equivalent real
    PIL `Image.transpose()` call — ground truth, not re-checking our own math.
    """
    img = _marker_image()
    transposed = img.transpose(_METHOD_BY_ORIENTATION[orientation])
    expected_bbox = _marker_bbox(transposed)
    expected_size = transposed.size

    bbox, width, height = transform_bbox_for_exif_orientation(
        MARKER, orientation, WIDTH, HEIGHT
    )

    assert bbox == expected_bbox
    assert (width, height) == expected_size
