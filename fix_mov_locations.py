"""
Move misplaced Live Photo .MOV files to sit alongside their matching .HEIC.

Matching is done via the Content Identifier UUID embedded in both files by iOS —
NOT by filename. This correctly handles duplicate IMG numbers that occur when the
iPhone counter rolls over (e.g. IMG_4280 in 2023 vs IMG_4280 in 2026).

The previous filename-based approach caused MOVs to land in the folder of the
wrong HEIC whenever duplicate IMG numbers existed across years.

Usage:
    python fix_mov_locations.py              # dry-run (safe, no changes)
    python fix_mov_locations.py --execute    # actually move files
    python fix_mov_locations.py --root F:/Fotos/Fotografier --execute
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# UUID extraction
# ---------------------------------------------------------------------------


def _heic_content_id(path: Path) -> str | None:
    """Extract the Content Identifier UUID from an Apple HEIC MakerNote."""
    try:
        with path.open("rb") as f:
            data = f.read(512 * 1024)
        idx = data.find(b"Apple iOS\x00")
        if idx < 0:
            return None
        region = data[idx : idx + 4096]
        m = re.search(
            rb"([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
            rb"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})",
            region,
        )
        return m.group(1).decode().upper() if m else None
    except OSError:
        return None


def _mov_content_id(path: Path) -> str | None:
    """Extract the Content Identifier UUID from a QuickTime Live Photo MOV."""
    try:
        with path.open("rb") as f:
            data = f.read()
        if b"com.apple.quicktime.content.identifier" not in data:
            return None
        idx = data.find(b"com.apple.quicktime.content.identifier")
        region = data[idx:]
        m = re.search(
            rb"([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
            rb"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})",
            region,
        )
        return m.group(1).decode().upper() if m else None
    except OSError:
        return None


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


def _resolve_collision(dest_folder: Path, preferred_name: str) -> Path:
    dest = dest_folder / preferred_name
    if not dest.exists():
        return dest
    stem, suffix = Path(preferred_name).stem, Path(preferred_name).suffix
    counter = 2
    while True:
        candidate = dest_folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _preferred_name(mov: Path, target_heic: Path) -> str:
    """Use the HEIC's stem as the target MOV filename (drops any _2/_3 suffix)."""
    return target_heic.stem + mov.suffix


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def run(root: Path, execute: bool) -> None:
    print("Phase 1: scanning HEIC files for Content Identifier UUIDs ...")
    heic_by_uuid: dict[str, Path] = {}
    heic_total = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".heic", ".heif"}:
            heic_total += 1
            cid = _heic_content_id(p)
            if cid and cid not in heic_by_uuid:
                heic_by_uuid[cid] = p
    print(
        f"  {heic_total} HEIC files scanned, {len(heic_by_uuid)} have a Content Identifier.\n"
    )

    print("Phase 2: scanning MOV files and matching by UUID ...")
    moves: list[tuple[Path, Path]] = []  # (source, destination)
    already_ok: int = 0
    no_uuid: int = 0
    no_heic_match: int = 0

    for p in root.rglob("*"):
        if not (p.is_file() and p.suffix.lower() == ".mov"):
            continue
        cid = _mov_content_id(p)
        if cid is None:
            no_uuid += 1
            continue
        heic = heic_by_uuid.get(cid)
        if heic is None:
            no_heic_match += 1
            continue
        if p.parent.resolve() == heic.parent.resolve():
            already_ok += 1
            continue
        preferred = _preferred_name(p, heic)
        dest = _resolve_collision(heic.parent, preferred)
        moves.append((p, dest))

    print(f"  Correctly placed (UUID matches co-located HEIC) : {already_ok}")
    print(f"  MISPLACED — need to move                        : {len(moves)}")
    print(f"  Regular videos / no UUID                        : {no_uuid}")
    print(f"  Live Photo MOV but matching HEIC not found      : {no_heic_match}")

    if not moves:
        print("\nNothing to do.")
        return

    verb = "MOVE" if execute else "WOULD MOVE"
    print(f"\n{'=' * 70}")
    print(f"{verb} ({len(moves)} files):")
    print(f"{'=' * 70}")
    for src, dst in moves:
        print(f"  {src}")
        print(f"  -> {dst}")
        print()

    if not execute:
        print(
            f"Dry-run complete — {len(moves)} MOV file(s) would be moved.\n"
            "Re-run with --execute to apply."
        )
        return

    errors = 0
    for src, dst in moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst = _resolve_collision(dst.parent, dst.name)
            src.rename(dst)
        except OSError as exc:
            print(f"ERROR moving {src}: {exc}")
            errors += 1

    moved = len(moves) - errors
    print(f"\nDone — {moved} MOV file(s) moved, {errors} error(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        help="Photo library root (auto-detected from DB if omitted)",
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
        print("Could not detect library root. Pass --root <path>.")
        sys.exit(1)
    if not root.is_dir():
        print(f"Not a directory: {root}")
        sys.exit(1)

    run(root, execute=args.execute)


if __name__ == "__main__":
    main()
