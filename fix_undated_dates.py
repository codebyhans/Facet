"""
Clear capture dates that were made up from file mtime.

Older indexing fell back to the file modification time when an image had no
EXIF date and no YYYY-MM-DD folder. Those images showed up in the timeline and
year filters under whatever date the file was last copied/unzipped.

For every indexed image NOT inside a YYYY-MM-DD folder, the date is re-resolved
with the indexer's current rules (EXIF -> XMP DateCreated). If that yields a
date, it is kept/updated; if not, capture_date/year/month are set to NULL so the
image appears under the Library's "Undated" tab instead.

Usage:
    pdm run python fix_undated_dates.py            # dry-run
    pdm run python fix_undated_dates.py --execute  # write changes (close the app first)
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from photo_app.services.image_index_service import ImageIndexService

_DATE_FOLDER = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _in_date_folder(path: Path) -> bool:
    return any(_DATE_FOLDER.match(part) for part in path.parts)


def _resolve(path: Path) -> datetime | None:
    # Reuse the indexer's exact date rules (no folder date applies here)
    capture, _w, _h = ImageIndexService._load_image_metadata(  # noqa: SLF001
        ImageIndexService.__new__(ImageIndexService), path, None
    )
    return capture


def _db_value(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.strftime(_DB_DATETIME_FORMAT)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--execute", action="store_true", help="Write changes")
    args = parser.parse_args()

    db_path = Path(__file__).parent / "photo_app_data" / "photo_manager.sqlite3"
    conn = sqlite3.connect(str(db_path))
    rows = [
        (image_id, Path(file_path), capture)
        for image_id, file_path, capture in conn.execute(
            "SELECT id, file_path, capture_date FROM images"
        )
        if not _in_date_folder(Path(file_path))
    ]
    print(f"{len(rows)} images outside YYYY-MM-DD folders")

    to_clear: list[int] = []
    to_set: list[tuple[str, int, int, int]] = []
    unreadable = 0
    per_folder: Counter[str] = Counter()
    for i, (image_id, path, capture) in enumerate(rows, 1):
        if i % 500 == 0:
            print(f"  ... {i}/{len(rows)}", flush=True)
        try:
            resolved = _resolve(path)
        except Exception:  # noqa: BLE001
            unreadable += 1  # missing/unreadable file: leave its row alone
            continue
        if resolved is None:
            if capture is not None:
                to_clear.append(image_id)
                per_folder[str(path.parent)] += 1
        elif capture is None or capture[:19] != _db_value(resolved)[:19]:
            to_set.append(
                (_db_value(resolved), resolved.year, resolved.month, image_id)
            )

    print(f"\nWould clear (no embedded date): {len(to_clear)}")
    for folder, n in per_folder.most_common():
        print(f"  {n:6}  {folder}")
    print(f"Would set/correct from EXIF/XMP: {len(to_set)}")
    print(f"Unreadable/missing (left alone): {unreadable}")

    if not args.execute:
        print("\nDry-run: nothing changed. Pass --execute to apply.")
        return

    now = datetime.now(UTC).strftime(_DB_DATETIME_FORMAT)
    conn.executemany(
        "UPDATE images SET capture_date = NULL, year = NULL, month = NULL,"
        " updated_at = ? WHERE id = ?",
        [(now, image_id) for image_id in to_clear],
    )
    conn.executemany(
        "UPDATE images SET capture_date = ?, year = ?, month = ?, updated_at = ?"
        " WHERE id = ?",
        [(c, y, m, now, image_id) for c, y, m, image_id in to_set],
    )
    # Cached album results embed ordering by date; drop them so they rebuild
    conn.execute("DELETE FROM album_query_cache")
    conn.commit()
    conn.close()
    print(f"\nCleared {len(to_clear)}, updated {len(to_set)}. Album cache reset.")


if __name__ == "__main__":
    main()
