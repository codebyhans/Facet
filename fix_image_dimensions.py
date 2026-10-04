"""
Backfill images.width/height for rows stored as 0 (or NULL).

Cover selection ranks a person's photos by relative face size
(bbox area / image area) and the inspector draws face boxes by scaling the bbox
against the image dimensions. With 0x0 dimensions both silently break: every
candidate ties at area 0 (so an arbitrary, often tiny, face is picked as the
preview) and no bbox is drawn on the inspector thumbnails.

Dimensions are stored EXIF-corrected, matching ImageIndexService.

Usage:
    pdm run python fix_image_dimensions.py              # dry-run
    pdm run python fix_image_dimensions.py --execute    # write to the DB
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from PIL import Image, ImageOps

DEFAULT_DB = Path("photo_app_data") / "photo_manager.sqlite3"

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


def _read_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as img:
            # Header-only when there is no EXIF rotation; otherwise swap axes
            # exactly as exif_transpose would, without decoding pixels.
            orientation = img.getexif().get(274, 1)
            width, height = img.size
            if orientation in (5, 6, 7, 8):
                width, height = height, width
            return (width, height) if width > 0 and height > 0 else None
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    rows = conn.execute(
        "SELECT id, file_path FROM images WHERE width IS NULL OR width <= 0 "
        "OR height IS NULL OR height <= 0"
    ).fetchall()
    print(f"{len(rows)} images with missing dimensions")

    updates: list[tuple[int, int, int]] = []
    missing = 0
    for image_id, file_path in rows:
        size = _read_size(Path(file_path))
        if size is None:
            missing += 1
            continue
        updates.append((size[0], size[1], image_id))

    print(f"resolved {len(updates)}, unreadable/missing {missing}")
    if args.execute:
        conn.executemany("UPDATE images SET width=?, height=? WHERE id=?", updates)
        conn.commit()
        print("DB updated.")
    else:
        print("Dry run — pass --execute to write.")
    conn.close()
    # Note: covers are cached per (image, person, bbox) so no cache purge is
    # needed; just refresh the People tab.


if __name__ == "__main__":
    main()
