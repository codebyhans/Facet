from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from pathlib import Path


class ThumbnailStore:
    """WebP thumbnail persistence."""

    def __init__(self, root: Path, max_size: int = 300) -> None:
        self._root = root
        self._max_size = max_size
        self._root.mkdir(parents=True, exist_ok=True)

    def set_max_size(self, max_size: int) -> None:
        """Update thumbnail max edge size for subsequent generations."""
        self._max_size = max_size

    def store(self, source_path: Path, image_hash: str) -> Path:
        """Generate and persist a resized thumbnail."""
        target = self._root / f"{image_hash}.webp"
        if target.exists():
            return target

        with Image.open(source_path) as img:
            img.thumbnail((self._max_size, self._max_size))
            img.save(target, format="WEBP", quality=85)

        return target
