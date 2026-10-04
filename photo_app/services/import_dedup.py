from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from blake3 import blake3

if TYPE_CHECKING:
    from pathlib import Path

LOGGER = logging.getLogger(__name__)


def hash_file(path: Path) -> str:
    """Return the BLAKE3 content hash of a file."""
    h = blake3()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class FolderDuplicateChecker:
    """Detects files whose content already exists in a destination folder.

    Only files of identical byte size are hashed, so most lookups cost a
    single stat call. Folder listings and hashes are cached for the lifetime
    of the checker, which should span one import run.
    """

    def __init__(self) -> None:
        self._folders: dict[Path, dict[int, list[Path]]] = {}
        self._hashes: dict[Path, str] = {}

    def find_duplicate(self, source: Path, dest_folder: Path) -> Path | None:
        """Return an existing file in dest_folder with the same content as source."""
        candidates = self._sizes_for(dest_folder).get(source.stat().st_size)
        if not candidates:
            return None
        source_hash = hash_file(source)
        for candidate in candidates:
            try:
                if self._hash_of(candidate) == source_hash:
                    return candidate
            except OSError:
                LOGGER.warning("Could not hash %s", candidate)
        return None

    def register(self, dest_path: Path) -> None:
        """Record a newly written file so later files in the batch see it."""
        sizes = self._sizes_for(dest_path.parent)
        try:
            sizes.setdefault(dest_path.stat().st_size, []).append(dest_path)
        except OSError:
            LOGGER.warning("Could not stat %s", dest_path)

    def _sizes_for(self, folder: Path) -> dict[int, list[Path]]:
        sizes = self._folders.get(folder)
        if sizes is not None:
            return sizes
        sizes = {}
        try:
            for entry in folder.iterdir():
                if entry.name.startswith("._") or not entry.is_file():
                    continue
                sizes.setdefault(entry.stat().st_size, []).append(entry)
        except FileNotFoundError:
            pass
        except OSError:
            LOGGER.warning("Could not list %s", folder)
        self._folders[folder] = sizes
        return sizes

    def _hash_of(self, path: Path) -> str:
        cached = self._hashes.get(path)
        if cached is None:
            cached = hash_file(path)
            self._hashes[path] = cached
        return cached
