"""
Reset mtime-derived capture times on images inside YYYY-MM-DD folders.

Older indexing fell back to the file modification time when an image had no
EXIF date. For images in a YYYY-MM-DD folder that left copy/unzip times
(e.g. 15:46:50-15:47:10 for a whole wedding shoot) that scramble the order.

Candidates are images inside a YYYY-MM-DD folder whose stored capture time
equals the file mtime (cheap stat, no decoding). Only those are re-resolved
with the indexer's current rules (EXIF -> XMP DateCreated -> folder date at
midnight). Images without an embedded date then share the folder date and
sort by filename.

Usage:
    pdm run python fix_folder_dates.py            # dry-run
    pdm run python fix_folder_dates.py --execute  # write changes (close the app first)
    pdm run python fix_folder_dates.py --folder "F:/Fotos/.../2019-04-05"  # one folder
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from photo_app.infrastructure.file_scanner import FileScanner
from photo_app.services.image_index_service import ImageIndexService

_DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _resolve(path: Path) -> datetime | None:
    # Reuse the indexer's exact date rules, including the folder-date fallback
    folder_date = FileScanner()._extract_folder_date(path)  # noqa: SLF001
    capture, _w, _h = ImageIndexService._load_image_metadata(  # noqa: SLF001
        ImageIndexService.__new__(ImageIndexService), path, folder_date
    )
    return capture


def _looks_mtime_derived(path: Path, capture: str) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return False
    return _db_value(mtime)[:19] == capture[:19]


def _db_value(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.strftime(_DB_DATETIME_FORMAT)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--execute", action="store_true", help="Write changes")
    parser.add_argument("--folder", type=Path, help="Only images in this folder")
    args = parser.parse_args()

    db_path = Path(__file__).parent / "photo_app_data" / "photo_manager.sqlite3"
    conn = sqlite3.connect(str(db_path))
    scanner = FileScanner()
    rows = [
        (image_id, Path(file_path), capture)
        for image_id, file_path, capture in conn.execute(
            "SELECT id, file_path, capture_date FROM images"
        )
        if scanner._extract_folder_date(Path(file_path)) is not None  # noqa: SLF001
        and (args.folder is None or Path(file_path).parent == args.folder)
    ]
    print(f"{len(rows)} images inside YYYY-MM-DD folders")
    rows = [
        (image_id, path, capture)
        for image_id, path, capture in rows
        if capture is not None and _looks_mtime_derived(path, capture)
    ]
    print(f"{len(rows)} with a capture time equal to the file mtime")

    to_set: list[tuple[str, int, int, int]] = []
    unreadable = 0
    per_folder: Counter[str] = Counter()
    for i, (image_id, path, capture) in enumerate(rows, 1):
        if i % 500 == 0:
            print(f"  ... {i}/{len(rows)}", flush=True)
        try:
            resolved = _resolve(path)
        except Exception:  # noqa: BLE001
            unreadable += 1  # missing/unreadable file or video: leave it alone
            continue
        if resolved is None:
            continue
        if capture[:19] != _db_value(resolved)[:19]:
            to_set.append(
                (_db_value(resolved), resolved.year, resolved.month, image_id)
            )
            per_folder[str(path.parent)] += 1

    print(f"\nWould set/correct: {len(to_set)}")
    for folder, n in per_folder.most_common():
        print(f"  {n:6}  {folder}")
    print(f"Unreadable/missing/video (left alone): {unreadable}")

    if not args.execute:
        print("\nDry-run: nothing changed. Pass --execute to apply.")
        return

    now = datetime.now(UTC).strftime(_DB_DATETIME_FORMAT)
    conn.executemany(
        "UPDATE images SET capture_date = ?, year = ?, month = ?, updated_at = ?"
        " WHERE id = ?",
        [(c, y, m, now, image_id) for c, y, m, image_id in to_set],
    )
    # Cached album results embed ordering by date; drop them so they rebuild
    conn.execute("DELETE FROM album_query_cache")
    conn.commit()
    conn.close()
    print(f"\nUpdated {len(to_set)}. Album cache reset.")


if __name__ == "__main__":
    main()
