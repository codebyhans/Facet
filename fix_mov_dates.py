"""
Relocate .MOV/.MP4 files that landed in the wrong date folder because their QuickTime
capture time was not read during import (falling back to download/mtime instead).

Reads com.apple.quicktime.creationdate (preferred) or the mvhd creation-time atom,
computes the correct {root}/{YYYY}/{YYYY}-{MM}-{DD}/ folder, and moves the file.
Also updates the images DB record if one exists.

Usage:
    pdm run python fix_mov_dates.py              # dry-run, auto-detect root & source
    pdm run python fix_mov_dates.py --execute    # actually move files + update DB
    pdm run python fix_mov_dates.py --source "F:\\Fotos\\Fotografier\\2026\\2026-06-30" --execute
    pdm run python fix_mov_dates.py --root "F:\\Fotos\\Fotografier" --execute
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from photo_app.infrastructure.quicktime_handler import read_quicktime_date

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_photo_root_from_db(db_path: Path) -> Path | None:
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'photo_root_dir'"
        ).fetchone()
        conn.close()
        return Path(row[0]) if row else None
    except Exception:
        return None


def _correct_folder(root: Path, dt: datetime) -> Path:
    return root / f"{dt.year}" / f"{dt.year}-{dt.month:02d}-{dt.day:02d}"


def _resolve_collision(dest_folder: Path, name: str) -> Path:
    dest = dest_folder / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 2
    while True:
        candidate = dest_folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _update_db(db_path: Path, old_path: Path, new_path: Path, dt: datetime) -> int:
    """Update the images row for old_path. Returns rows affected (0 if not indexed)."""
    capture_utc = dt.astimezone(UTC)
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute(
            """
            UPDATE images
               SET file_path    = ?,
                   capture_date = ?,
                   year         = ?,
                   month        = ?,
                   updated_at   = ?
             WHERE file_path    = ?
            """,
            (
                str(new_path),
                capture_utc.strftime("%Y-%m-%d %H:%M:%S"),
                dt.year,
                dt.month,
                datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                str(old_path),
            ),
        )
        conn.commit()
        rows = cur.rowcount
        conn.close()
        return rows
    except Exception as exc:
        print(f"  DB error: {exc}")
        return 0


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def run(source_dir: Path, root: Path, db_path: Path, execute: bool) -> None:
    print(f"Scanning: {source_dir}")
    print(f"Library root: {root}\n")

    moves: list[tuple[Path, Path, datetime]] = []
    already_ok: int = 0
    no_metadata: int = 0

    for p in sorted(source_dir.rglob("*")):
        if not (p.is_file() and p.suffix.lower() in {".mov", ".mp4"}):
            continue
        dt = read_quicktime_date(p)
        if dt is None:
            no_metadata += 1
            print(f"  [no metadata] {p.name}")
            continue
        correct = _correct_folder(root, dt)
        if p.parent.resolve() == correct.resolve():
            already_ok += 1
            continue
        dest = _resolve_collision(correct, p.name)
        moves.append((p, dest, dt))

    print(f"\nAlready in correct folder : {already_ok}")
    print(f"To relocate               : {len(moves)}")
    print(f"No QuickTime metadata     : {no_metadata}")

    if not moves:
        print("\nNothing to move.")
        return

    verb = "MOVE" if execute else "WOULD MOVE"
    print(f"\n{'=' * 70}")
    print(f"{verb} ({len(moves)} files):")
    print(f"{'=' * 70}")
    for src, dst, dt in moves:
        print(f"  {src.name}  ->  {dst.parent.name}/  ({dt.strftime('%Y-%m-%d')})")

    if not execute:
        print(f"\nDry-run — re-run with --execute to apply.")
        return

    errors = 0
    db_updated = 0
    for src, dst, dt in moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst = _resolve_collision(dst.parent, dst.name)
            src.rename(dst)
            db_updated += _update_db(db_path, src, dst, dt)
        except OSError as exc:
            print(f"ERROR moving {src.name}: {exc}")
            errors += 1

    moved = len(moves) - errors
    print(
        f"\nDone — {moved} file(s) moved, {db_updated} DB record(s) updated, {errors} error(s)."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        help="Photo library root (auto-detected from DB if omitted)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="Folder to scan for misplaced MOVs (default: root/2026/2026-06-30)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually move files and update DB (default is dry-run)",
    )
    args = parser.parse_args()

    db_path = Path(__file__).parent / "photo_app_data" / "photo_manager.sqlite3"

    root = args.root
    if root is None and db_path.exists():
        root = _read_photo_root_from_db(db_path)
        if root:
            print(f"Auto-detected library root: {root}")

    if root is None:
        print("Could not detect library root. Pass --root <path>.")
        sys.exit(1)
    if not root.is_dir():
        print(f"Not a directory: {root}")
        sys.exit(1)

    source = args.source or (root / "2026" / "2026-06-30")
    if not source.is_dir():
        print(f"Source folder not found: {source}")
        sys.exit(1)

    run(source, root, db_path, execute=args.execute)


if __name__ == "__main__":
    main()
