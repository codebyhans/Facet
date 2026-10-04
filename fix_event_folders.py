"""
One-time cleanup: dissolve event/collection folders into the date layout.

Every file outside {root}/{YYYY}/{YYYY-MM-DD}/, {root}/Screenshots/ and
{root}/undated/ is placed with the importer's rules:
    date taken (EXIF -> QuickTime -> XMP) -> {root}/{YYYY}/{YYYY-MM-DD}/
    iOS screenshots                       -> {root}/Screenshots/{YYYY}/{YYYY-MM-DD}/
    no date                               -> {root}/undated/

One-time exception (NOT used by imports): a file without a date taken uses its
file mtime when that day lies within the date-taken span (+/- 1 day) of the
dated files in the same event folder. Old .avi clips in trip folders keep their
day this way; mtimes that look like a later copy date are ignored.

Also:
  - Exports/ holds re-exports of library photos: each file is deleted once an
    indexed original with the same name and date taken is confirmed.
  - Thumbs.db / .DS_Store / ._* and the Hugin project (*.pto, *.pto.mk) are
    deleted; camera .THM thumbnails move next to their video.
  - Files whose content already exists in the target folder are deleted.
  - Empty folders are removed afterwards. Anything unrecognised is left alone.

The DB is kept in sync (paths, capture dates, deleted rows). A CSV with every
planned action is written to photo_app_data/resort_logs/ on each run.

Usage:
    pdm run python fix_event_folders.py            # dry-run
    pdm run python fix_event_folders.py --execute  # close the app first
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fix_import_folders import (
    _DB_DATETIME_FORMAT,
    _db_capture_value,
    _read_photo_root_from_db,
    _resolve_collision,
    _resolve_date,
    _target_folder,
)
from photo_app.infrastructure.db import create_sqlite_engine
from photo_app.infrastructure.repositories import SqlAlchemyImageRepository
from photo_app.services.import_dedup import FolderDuplicateChecker

_YEAR = re.compile(r"^\d{4}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_JUNK_NAMES = {"thumbs.db", ".ds_store"}
_DELETE_SUFFIXES = (".pto", ".pto.mk")
_MEDIA = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif",
    ".mov", ".mp4", ".m4v", ".3gp", ".avi", ".rw2",
}  # fmt: skip
_EXPORTS = "Exports"
_MTIME_SLACK = timedelta(days=1)


@dataclass
class Action:
    kind: str  # move | duplicate | delete-junk | delete-export | skip
    source: Path
    target: Path | None = None
    captured: datetime | None = None
    date_source: str = ""
    note: str = ""


def _group_of(rel: tuple[str, ...]) -> str | None:
    """Event/collection key for a file outside the date layout, else None."""
    if rel[0] in ("undated", "Screenshots") or len(rel) == 1:
        return None
    if _YEAR.match(rel[0]):
        if len(rel) == 3 and _DAY.match(rel[1]):
            return None  # already in place
        if len(rel) == 2:
            return f"{rel[0]}/(loose)"
        return f"{rel[0]}/{rel[1]}"
    return rel[0]


def _local_day(dt: datetime) -> date:
    return dt.astimezone().date() if dt.tzinfo else dt.date()


def collect(root: Path) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in root.rglob("*"):
        if p.is_file() and (g := _group_of(p.relative_to(root).parts)) is not None:
            groups[g].append(p)
    return groups


def plan(
    root: Path, groups: dict[str, list[Path]], conn: sqlite3.Connection
) -> list[Action]:
    actions: list[Action] = []
    checker = FolderDuplicateChecker()
    taken: set[Path] = set()

    for group, files in sorted(groups.items()):
        print(f"  planning {group} ({len(files)} files)", flush=True)
        media, other = [], []
        for p in files:
            (media if p.suffix.lower() in _MEDIA else other).append(p)

        if group == _EXPORTS:
            for p in media:
                actions.append(_plan_export(p, root, conn))
            actions.extend(Action("skip", p, note="unknown file") for p in other)
            continue

        resolved = {p: _resolve_date(p) for p in media}
        days = [_local_day(c) for c, _s, _sh in resolved.values() if c is not None]
        span = (min(days) - _MTIME_SLACK, max(days) + _MTIME_SLACK) if days else None

        video_targets: dict[str, Path] = {}
        for p in sorted(media):
            captured, source, is_screenshot = resolved[p]
            if captured is None and span is not None:
                mtime = datetime.fromtimestamp(p.stat().st_mtime)  # noqa: DTZ006
                if span[0] <= mtime.date() <= span[1]:
                    captured, source = mtime, "mtime-in-event-range"
            folder = _target_folder(root, captured, is_screenshot)
            existing = checker.find_duplicate(p, folder)
            if existing is not None:
                actions.append(
                    Action("duplicate", p, existing, captured, source or "none")
                )
                continue
            target = _resolve_collision(folder, p.name, taken)
            taken.add(target)
            checker._sizes_for(folder).setdefault(p.stat().st_size, []).append(p)  # noqa: SLF001
            actions.append(Action("move", p, target, captured, source or "none"))
            video_targets[p.stem.lower()] = target

        for p in other:
            name = p.name.lower()
            if (
                name in _JUNK_NAMES
                or name.startswith("._")
                or name.endswith(_DELETE_SUFFIXES)
            ):
                actions.append(Action("delete-junk", p))
            elif p.suffix.lower() == ".thm" and p.stem.lower() in video_targets:
                video = video_targets[p.stem.lower()]
                target = _resolve_collision(video.parent, video.stem + p.suffix, taken)
                taken.add(target)
                actions.append(Action("move", p, target, note="thumbnail of video"))
            else:
                actions.append(Action("skip", p, note="unknown file"))
    return actions


def _plan_export(p: Path, root: Path, conn: sqlite3.Connection) -> Action:
    """Delete an export only when its original is indexed elsewhere."""
    captured, _source, _shot = _resolve_date(p)
    if captured is None:
        return Action("skip", p, note="export without date taken")
    key = _db_capture_value(captured)[:19]
    for (path,) in conn.execute(
        "SELECT file_path FROM images WHERE capture_date LIKE ?", (key + "%",)
    ):
        original = Path(path)
        if (
            original.name.lower() == p.name.lower()
            and _EXPORTS not in original.relative_to(root).parts
            and original.is_file()
        ):
            return Action("delete-export", p, original, captured)
    return Action("skip", p, note="no indexed original found")


def write_csv(actions: list[Action], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["action", "source", "target", "date_source", "captured", "note"])
        for a in actions:
            w.writerow(
                [
                    a.kind,
                    a.source,
                    a.target or "",
                    a.date_source,
                    a.captured.isoformat() if a.captured else "",
                    a.note,
                ]
            )


def summarize(actions: list[Action], root: Path) -> None:
    by_kind: Counter[str] = Counter()
    for a in actions:
        detail = a.date_source if a.kind in ("move", "duplicate") else ""
        if a.kind == "move" and a.target is not None:
            top = a.target.relative_to(root).parts[0]
            detail = f"{detail} -> {'undated' if top == 'undated' else 'date folder'}"
        by_kind[f"{a.kind:<14} {detail}"] += 1
    for key, n in sorted(by_kind.items()):
        print(f"  {n:6}  {key}")
    for a in actions:
        if a.kind == "skip":
            print(f"  SKIP {a.source}  ({a.note})")


def execute(actions: list[Action], db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(UTC).strftime(_DB_DATETIME_FORMAT)
    moved = rows = 0
    for a in actions:
        if a.kind != "move" or a.target is None:
            continue
        a.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(a.source), str(a.target))
        moved += 1
        if a.captured is not None:
            cur = conn.execute(
                "UPDATE images SET file_path = ?, capture_date = ?, year = ?,"
                " month = ?, updated_at = ? WHERE file_path = ?",
                (
                    str(a.target),
                    _db_capture_value(a.captured),
                    a.captured.year,
                    a.captured.month,
                    now,
                    str(a.source),
                ),
            )
        else:
            cur = conn.execute(
                "UPDATE images SET file_path = ?, capture_date = NULL, year = NULL,"
                " month = NULL, updated_at = ? WHERE file_path = ?",
                (str(a.target), now, str(a.source)),
            )
        conn.commit()  # per file: DB never points at a file that was not moved
        rows += cur.rowcount
    print(f"Moved {moved} files, updated {rows} DB rows.")

    repo = SqlAlchemyImageRepository(create_sqlite_engine(str(db_path)))
    deleted: list[str] = []
    kept_rows = 0
    for a in actions:
        if a.kind in ("duplicate", "delete-export") and not (
            a.target and a.target.is_file()
        ):
            print(f"  SKIP delete, kept copy missing: {a.source}")
            continue
        if a.kind in ("duplicate", "delete-export") and a.target is not None:
            kept_rows += _keep_richer_row(conn, repo, a, now)
        if a.kind in ("duplicate", "delete-export", "delete-junk"):
            a.source.unlink()
            deleted.append(str(a.source))
    conn.execute("DELETE FROM album_query_cache")  # ordering changed; rebuilds
    conn.commit()
    conn.close()
    print(
        f"Deleted {len(deleted)} files, {repo.delete_by_paths(deleted)} DB rows;"
        f" {kept_rows} duplicate rows with faces/tags re-pointed to the kept file."
    )


def _richness(conn: sqlite3.Connection, path: Path) -> tuple[int, ...] | None:
    row = conn.execute(
        "SELECT id, rating IS NOT NULL OR is_favorite OR user_notes IS NOT NULL"
        " OR flag IS NOT NULL FROM images WHERE file_path = ?",
        (str(path),),
    ).fetchone()
    if row is None:
        return None
    image_id, edits = row

    def count(sql: str) -> int:
        return conn.execute(sql, (image_id,)).fetchone()[0]

    return (
        count("SELECT count(*) FROM faces WHERE image_id = ? AND person_id IS NOT NULL"),
        count("SELECT count(*) FROM image_tags WHERE image_id = ?"),
        int(bool(edits)),
        count("SELECT count(*) FROM faces WHERE image_id = ?"),
    )


def _keep_richer_row(
    conn: sqlite3.Connection, repo: SqlAlchemyImageRepository, a: Action, now: str
) -> int:
    """If the copy being deleted has more faces/tags/edits, keep its DB row.

    The kept file's (poorer) row is dropped and the richer row is re-pointed at
    the kept file, so named faces survive the file deletion.
    """
    assert a.target is not None
    src, kept = _richness(conn, a.source), _richness(conn, a.target)
    if src is None or (kept is not None and kept >= src):
        return 0
    if kept is not None:
        conn.commit()  # release our write lock before the repository's session
        repo.delete_by_paths([str(a.target)])
    captured = _db_capture_value(a.captured) if a.captured else None
    conn.execute(
        "UPDATE images SET file_path = ?, capture_date = ?, year = ?, month = ?,"
        " updated_at = ? WHERE file_path = ?",
        (
            str(a.target),
            captured,
            a.captured.year if a.captured else None,
            a.captured.month if a.captured else None,
            now,
            str(a.source),
        ),
    )
    conn.commit()
    return 1


def remove_empty_dirs(root: Path, groups: dict[str, list[Path]]) -> None:
    dirs = {
        root / Path(g.replace("/(loose)", "")) for g in groups if "(loose)" not in g
    }
    removed = 0
    for top in dirs:
        for d in sorted((p for p in top.rglob("*") if p.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
                removed += 1
        if top.is_dir() and not any(top.iterdir()):
            top.rmdir()
            removed += 1
    print(f"Removed {removed} empty folders.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()

    db_path = Path(__file__).parent / "photo_app_data" / "photo_manager.sqlite3"
    root = args.root or _read_photo_root_from_db(db_path)
    if root is None:
        sys.exit("Could not determine library root; pass --root")
    print(f"Library root: {root}")

    groups = collect(root)
    conn = sqlite3.connect(str(db_path))
    actions = plan(root, groups, conn)
    conn.close()

    print()
    summarize(actions, root)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # noqa: DTZ005
    log = (
        db_path.parent
        / "resort_logs"
        / f"events_{'run' if args.execute else 'plan'}_{stamp}.csv"
    )
    write_csv(actions, log)
    print(f"Plan: {log}")

    if not args.execute:
        print("\nDry-run: nothing changed. Pass --execute to apply.")
        return
    execute(actions, db_path)
    remove_empty_dirs(root, groups)


if __name__ == "__main__":
    main()
