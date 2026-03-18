from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_DATE_PATTERN = re.compile(r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})$")
_MEDIA_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".raw",
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".dng",
    ".mp4",
    ".mov",
    ".avi",
    ".mts",
    ".m2ts",
    ".3gp",
}
_MIN_PATH_PARTS = 2


@dataclass(frozen=True)
class ScannedFile:
    """File metadata from scanner."""

    file_path: Path
    folder_date: date | None


class FileScanner:
    """Folder scanner for yyyy/yyyy-mm-dd hierarchy."""

    def scan(self, root: Path) -> list[ScannedFile]:
        files: list[ScannedFile] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _MEDIA_SUFFIXES:
                continue
            folder_date = self._extract_folder_date(path)
            files.append(ScannedFile(file_path=path, folder_date=folder_date))
        return files

    def _extract_folder_date(self, path: Path) -> date | None:
        if len(path.parts) < _MIN_PATH_PARTS:
            return None
        for part in path.parts:
            match = _DATE_PATTERN.match(part)
            if match is not None:
                return date(
                    int(match.group("y")),
                    int(match.group("m")),
                    int(match.group("d")),
                )
        return None
