"""
Re-sort files that landed in the import day's folder because the folder importer
used the file mtime (reset to "now" when an iCloud extract is unzipped).

Each file is placed with the same rules as the importer now uses:
    date: EXIF DateTimeOriginal -> QuickTime creation date -> XMP DateCreated
    screenshots (iOS XMP marker)  -> {root}/Screenshots/{YYYY}/{YYYY}-{MM}-{DD}/
    other dated files             -> {root}/{YYYY}/{YYYY}-{MM}-{DD}/
    no date at all                -> {root}/undated/   (Screenshots/undated/ for screenshots)

Files that are already in the right folder are left alone. Files whose exact
content already exists in the target folder are duplicates: they are only
reported, unless --delete-duplicates is given, in which case the redundant copy
is deleted together with its images row (and dependent rows), keeping the copy
that is already in the right place.

Moves update images.file_path (thumbnails are keyed by the stored hash, which is
kept). capture_date/year/month are updated when a real date was found. The full
plan (every file and what happens to it) is written to a CSV under
photo_app_data/resort_logs/ on every run, dry-run included.

Usage:
    pdm run python fix_import_folders.py 2026-10-03 2026-10-04            # dry-run
    pdm run python fix_import_folders.py 2026-10-03 2026-10-04 --execute
    pdm run python fix_import_folders.py 2026-10-03 2026-10-04 --execute --delete-duplicates
    pdm run python fix_import_folders.py "F:\\Fotos\\Fotografier\\2026\\2026-10-04" --root "F:\\Fotos\\Fotografier"
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from photo_app.infrastructure.db import create_sqlite_engine
from photo_app.infrastructure.exif_handler import ExifMetadataHandler
from photo_app.infrastructure.quicktime_handler import read_quicktime_date
from photo_app.infrastructure.repositories import SqlAlchemyImageRepository
from photo_app.services.import_dedup import FolderDuplicateChecker
from photo_app.services.import_service import (
    QUICKTIME_EXTENSIONS,
    SCREENSHOTS_FOLDER,
    read_xmp_info,
)

_DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    source: Path
    target: Path | None  # None when no move is needed
    action: str  # "move", "ok", "duplicate", "db-conflict"
    date_source: str  # "exif", "quicktime", "xmp", "none"
    captured: datetime | None
    is_screenshot: bool
    duplicate_of: Path | None = None


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


def _resolve_date(path: Path) -> tuple[datetime | None, str, bool]:
    """Return (captured, source, is_screenshot) using the importer's precedence."""
    xmp = read_xmp_info(path)
    try:
        dto = ExifMetadataHandler.read_exif(str(path)).get("datetime_original")
        if isinstance(dto, datetime):
            return dto, "exif", xmp.is_screenshot
    except Exception:
        pass
    if path.suffix.lower() in QUICKTIME_EXTENSIONS:
        qt = read_quicktime_date(path)
        if qt is not None:
            return qt, "quicktime", False
    if xmp.created is not None:
        return xmp.created, "xmp", xmp.is_screenshot
    return None, "none", xmp.is_screenshot


def _target_folder(root: Path, captured: datetime | None, is_screenshot: bool) -> Path:
    base = root / SCREENSHOTS_FOLDER if is_screenshot else root
    if captured is None:
        return base / "undated"
    return (
        base
        / f"{captured.year}"
        / f"{captured.year}-{captured.month:02d}-{captured.day:02d}"
    )


def _resolve_collision(dest_folder: Path, name: str, taken: set[Path]) -> Path:
    dest = dest_folder / name
    if not dest.exists() and dest not in taken:
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 2
    while True:
        candidate = dest_folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists() and candidate not in taken:
            return candidate
        counter += 1


def _db_capture_value(dt: datetime) -> str:
    # Aware datetimes (QuickTime) are stored as UTC; naive ones (EXIF/XMP) as-is
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime(_DB_DATETIME_FORMAT)


# ---------------------------------------------------------------------------
# Plan + execute
# ---------------------------------------------------------------------------


def plan_folder(
    source_dir: Path,
    root: Path,
    conn: sqlite3.Connection,
    checker: FolderDuplicateChecker,
    taken: set[Path],
) -> list[Plan]:
    plans: list[Plan] = []
    files = sorted(p for p in source_dir.iterdir() if p.is_file())
    for i, src in enumerate(files, 1):
        if i % 500 == 0:
            print(f"  ... scanned {i}/{len(files)}", flush=True)
        captured, source, is_screenshot = _resolve_date(src)
        folder = _target_folder(root, captured, is_screenshot)

        if folder.resolve() == source_dir.resolve():
            plans.append(Plan(src, None, "ok", source, captured, is_screenshot))
            continue

        existing = checker.find_duplicate(src, folder)
        if existing is not None:
            plans.append(
                Plan(src, None, "duplicate", source, captured, is_screenshot, existing)
            )
            continue

        target = _resolve_collision(folder, src.name, taken)
        in_db = conn.execute(
            "SELECT 1 FROM images WHERE file_path = ?", (str(target),)
        ).fetchone()
        if in_db:
            plans.append(
                Plan(src, target, "db-conflict", source, captured, is_screenshot)
            )
            continue

        taken.add(target)
        # Let later files in this run see this one as already in the target folder
        checker._sizes_for(folder).setdefault(src.stat().st_size, []).append(src)  # noqa: SLF001
        plans.append(Plan(src, target, "move", source, captured, is_screenshot))
    return plans


def write_plan_csv(plans: list[Plan], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["action", "source", "target", "duplicate_of", "date_source", "captured"]
        )
        for p in plans:
            writer.writerow(
                [
                    p.action,
                    p.source,
                    p.target or "",
                    p.duplicate_of or "",
                    p.date_source,
                    p.captured.isoformat() if p.captured else "",
                ]
            )


def execute_moves(plans: list[Plan], conn: sqlite3.Connection) -> tuple[int, int]:
    moved = db_rows = 0
    now = datetime.now(UTC).strftime(_DB_DATETIME_FORMAT)
    for plan in plans:
        if plan.action != "move" or plan.target is None:
            continue
        plan.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(plan.source), str(plan.target))
        moved += 1

        if plan.captured is not None:
            cur = conn.execute(
                """
                UPDATE images
                   SET file_path = ?, capture_date = ?, year = ?, month = ?,
                       updated_at = ?
                 WHERE file_path = ?
                """,
                (
                    str(plan.target),
                    _db_capture_value(plan.captured),
                    plan.captured.year,
                    plan.captured.month,
                    now,
                    str(plan.source),
                ),
            )
        else:
            cur = conn.execute(
                "UPDATE images SET file_path = ?, updated_at = ? WHERE file_path = ?",
                (str(plan.target), now, str(plan.source)),
            )
        # Commit per file so the DB never points at a file that was not moved
        conn.commit()
        db_rows += cur.rowcount
    return moved, db_rows


def delete_duplicates(plans: list[Plan], db_path: Path) -> tuple[int, int]:
    """Delete redundant copies whose content already sits in the right folder."""
    deleted: list[str] = []
    for plan in plans:
        if plan.action != "duplicate" or plan.duplicate_of is None:
            continue
        # Re-check right before deleting: the kept copy must still exist
        if not plan.duplicate_of.is_file():
            print(f"  SKIP (kept copy missing): {plan.source}")
            continue
        plan.source.unlink()
        deleted.append(str(plan.source))
    repo = SqlAlchemyImageRepository(create_sqlite_engine(str(db_path)))
    return len(deleted), repo.delete_by_paths(deleted)


def _summarize(plans: list[Plan], root: Path) -> None:
    counts: Counter[str] = Counter()
    for p in plans:
        kind = "screenshot" if p.is_screenshot else "file"
        counts[f"{p.action:<11} {kind:<10} date from {p.date_source}"] += 1
    for key, n in sorted(counts.items()):
        print(f"  {n:6}  {key}")

    dest_counts: Counter[str] = Counter()
    for p in plans:
        if p.action == "move" and p.target is not None:
            rel = p.target.parent.relative_to(root)
            top = rel.parts[0]
            dest_counts[
                f"{top}/{rel.parts[1]}"
                if top == SCREENSHOTS_FOLDER and len(rel.parts) > 1
                else top
            ] += 1
    if dest_counts:
        print("  Destinations:")
        for key, n in sorted(dest_counts.items()):
            print(f"  {n:6}  {key}")

    dup_counts: Counter[str] = Counter()
    for p in plans:
        if p.action == "duplicate" and p.duplicate_of is not None:
            dup_counts[p.duplicate_of.parent.relative_to(root).parts[0]] += 1
        elif p.action == "db-conflict":
            print(f"  DB-CONFLICT  {p.source.name} -> {p.target} already in DB")
    if dup_counts:
        print("  Duplicates already present in:")
        for key, n in sorted(dup_counts.items()):
            print(f"  {n:6}  {key}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "folders",
        nargs="+",
        help="Folders to re-sort: full paths, or YYYY-MM-DD names under {root}/{YYYY}/",
    )
    parser.add_argument("--root", type=Path, help="Library root (default: from DB)")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Move files and update DB (default: dry-run)",
    )
    parser.add_argument(
        "--delete-duplicates",
        action="store_true",
        help="With --execute: delete copies whose content is already in the target folder",
    )
    args = parser.parse_args()

    db_path = Path(__file__).parent / "photo_app_data" / "photo_manager.sqlite3"
    root: Path | None = args.root
    if root is None and db_path.exists():
        root = _read_photo_root_from_db(db_path)
    if root is None:
        sys.exit("Could not determine library root; pass --root")

    sources: list[Path] = []
    for name in args.folders:
        p = Path(name)
        if not p.is_absolute():
            p = root / name[:4] / name
        if not p.is_dir():
            sys.exit(f"Not a folder: {p}")
        sources.append(p)

    print(f"Library root: {root}")
    print(f"Mode: {'EXECUTE' if args.execute else 'dry-run (pass --execute to apply)'}")

    conn = sqlite3.connect(str(db_path))
    checker = FolderDuplicateChecker()
    taken: set[Path] = set()
    all_plans: list[Plan] = []
    for src in sources:
        print(f"\n{src}")
        plans = plan_folder(src, root, conn, checker, taken)
        _summarize(plans, root)
        all_plans.extend(plans)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # noqa: DTZ005
    kind = "resort" if args.execute else "plan"
    log_path = db_path.parent / "resort_logs" / f"{kind}_{stamp}.csv"
    write_plan_csv(all_plans, log_path)

    n_moves = sum(p.action == "move" for p in all_plans)
    n_dups = sum(p.action == "duplicate" for p in all_plans)
    if args.execute:
        moved, db_rows = execute_moves(all_plans, conn)
        print(f"\nMoved {moved} files, updated {db_rows} DB rows.")
        if args.delete_duplicates:
            deleted, rows = delete_duplicates(all_plans, db_path)
            print(f"Deleted {deleted} duplicate files and {rows} DB rows.")
        else:
            print(
                f"Left {n_dups} duplicates in place (--delete-duplicates removes them)."
            )
    else:
        print(
            f"\nDry-run: {n_moves} files would be moved, {n_dups} duplicates found."
            " Nothing changed."
        )
    print(f"Plan: {log_path}")
    conn.close()


if __name__ == "__main__":
    main()
