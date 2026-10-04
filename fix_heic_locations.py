"""
Move misplaced HEIC files to the correct date-based folder.

Folder structure: {root}/{year}/{year}-{MM}-{dd}/filename.heic

Usage:
    python fix_heic_locations.py                  # dry-run, auto-detect library root
    python fix_heic_locations.py --root D:/Photos # dry-run with explicit root
    python fix_heic_locations.py --execute        # actually move files
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pillow_heif
from PIL import Image

pillow_heif.register_heif_opener()


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


def _date_taken(file_path: Path) -> datetime | None:
    try:
        with Image.open(file_path) as img:
            exif = img.getexif()
            # DateTimeOriginal (36867) is in the Exif sub-IFD (34665), not IFD0
            raw = exif.get_ifd(34665).get(36867) or exif.get(36867)
            if isinstance(raw, (str, bytes)):
                s = raw.decode() if isinstance(raw, bytes) else raw
                return datetime.strptime(s, "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except Exception:
        pass
    return None


def _resolve_collision(dest_folder: Path, filename: str) -> Path:
    dest = dest_folder / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 2
    while True:
        candidate = dest_folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _correct_folder(root: Path, dt: datetime) -> Path:
    return root / f"{dt.year}" / f"{dt.year}-{dt.month:02d}-{dt.day:02d}"


def _companion_mov(heic: Path) -> Path | None:
    """Return the paired .MOV file if it exists alongside a HEIC (Live Photo)."""
    for ext in (".mov", ".MOV"):
        candidate = heic.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def run(root: Path, execute: bool) -> None:
    heic_files = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in {".heic", ".heif"}
    ]

    if not heic_files:
        print("No HEIC files found.")
        return

    print(f"Found {len(heic_files)} HEIC file(s) under {root}\n")

    # Each entry: (src, dst) — may include a companion .MOV alongside the .HEIC
    moves: list[tuple[Path, Path]] = []
    no_date: list[Path] = []

    for f in heic_files:
        dt = _date_taken(f)
        if dt is None:
            no_date.append(f)
            continue

        correct = _correct_folder(root, dt)
        if f.parent.resolve() == correct.resolve():
            continue  # already in the right place

        moves.append((f, _resolve_collision(correct, f.name)))

        mov = _companion_mov(f)
        if mov is not None:
            moves.append((mov, _resolve_collision(correct, mov.name)))

    heic_moves = sum(1 for src, _ in moves if src.suffix.lower() in {".heic", ".heif"})
    mov_moves = len(moves) - heic_moves
    already_correct = len(heic_files) - heic_moves - len(no_date)
    print(f"  Already in correct folder : {already_correct} HEIC")
    print(
        f"  Need to move              : {heic_moves} HEIC + {mov_moves} companion MOV"
    )
    print(f"  No date-taken metadata    : {len(no_date)}")

    if no_date:
        print("\nFiles without Date Taken (skipped):")
        for p in no_date:
            print(f"  {p}")

    if not moves:
        print("\nNothing to do.")
        return

    print(f"\n{'ACTION':7}  SOURCE  ->  DESTINATION")
    print("-" * 80)
    for src, dst in moves:
        label = "MOVE" if execute else "WOULD"
        print(f"  {label}  {src}")
        print(f"         -> {dst}")

    if not execute:
        print(
            f"\nDry-run complete -- {len(moves)} file(s) would be moved."
            "\nRe-run with --execute to apply."
        )
        return

    # Execute moves
    errors = 0
    for src, dst in moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        except OSError as exc:
            print(f"ERROR moving {src}: {exc}")
            errors += 1

    moved = len(moves) - errors
    print(f"\nDone -- {moved} file(s) moved, {errors} error(s).")
    if moved:
        print(
            "\nNext steps in the app:\n"
            "  1. File -> Index Images   (picks up new paths)\n"
            "  2. File -> Fix Missing Timestamps   (backfills any remaining nulls)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        help="Photo library root folder (auto-detected from DB if omitted)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually move files (default is dry-run)",
    )
    args = parser.parse_args()

    root: Path | None = args.root

    if root is None:
        db_path = Path(__file__).parent / "photo_app_data" / "photo_manager.sqlite3"
        if db_path.exists():
            root = _read_photo_root_from_db(db_path)
            if root:
                print(f"Auto-detected library root from DB: {root}\n")

    if root is None:
        print(
            "Could not detect library root. Pass it explicitly:\n"
            "  python fix_heic_locations.py --root D:/Photos"
        )
        sys.exit(1)

    if not root.is_dir():
        print(f"Root folder does not exist or is not a directory: {root}")
        sys.exit(1)

    run(root, execute=args.execute)


if __name__ == "__main__":
    main()
