from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from photo_app.services.import_dedup import hash_file

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from photo_app.domain.models import Image
    from photo_app.domain.repositories import ImageRepository

LOGGER = logging.getLogger(__name__)


@dataclass
class DuplicateGroup:
    content_hash: str
    images: list[Image]
    suggested_keeper_id: int
    file_size: int = field(default=0)
    # image_id -> names of the collections it belongs to (members only)
    collections: dict[int, list[str]] = field(default_factory=dict)


class DuplicateDetectionService:
    """Finds and removes exact duplicate image files by content hash."""

    def __init__(
        self,
        image_repository: ImageRepository,
        collection_names: Callable[[Sequence[int]], dict[int, list[str]]] | None = None,
    ) -> None:
        self._repo = image_repository
        self._collection_names = collection_names

    def find_exact_duplicates(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[DuplicateGroup]:
        """Hash every indexed file and return groups with 2+ identical files."""
        all_images = self._repo.list_all()
        total = len(all_images)
        hash_groups: dict[str, list[Image]] = {}

        for idx, image in enumerate(all_images):
            if on_progress and idx % 25 == 0:
                on_progress(idx, total)
            path = Path(image.file_path)
            if not path.exists():
                continue
            try:
                content_hash = self._hash_file(path)
                hash_groups.setdefault(content_hash, []).append(image)
            except OSError:
                LOGGER.warning("Could not hash %s", image.file_path)

        if on_progress:
            on_progress(total, total)

        duplicate_sets = {
            h: imgs
            for h, imgs in hash_groups.items()
            if len(imgs) >= 2  # noqa: PLR2004
        }
        collections = self._lookup_collections(
            [
                img.id
                for imgs in duplicate_sets.values()
                for img in imgs
                if img.id is not None
            ]
        )

        groups: list[DuplicateGroup] = []
        for h, imgs in duplicate_sets.items():
            keeper_id = self._suggest_keeper(imgs, collections)
            try:
                file_size = Path(imgs[0].file_path).stat().st_size
            except OSError:
                file_size = 0
            groups.append(
                DuplicateGroup(
                    content_hash=h,
                    images=imgs,
                    suggested_keeper_id=keeper_id,
                    file_size=file_size,
                    collections={
                        img.id: collections[img.id]
                        for img in imgs
                        if img.id in collections
                    },
                )
            )

        groups.sort(key=lambda g: len(g.images), reverse=True)
        return groups

    def move_to_trash(self, image_ids: list[int], trash_dir: Path) -> int:
        """Move files to trash_dir and remove their DB records. Returns count moved."""
        trash_dir.mkdir(parents=True, exist_ok=True)
        moved_paths: list[str] = []

        for image_id in image_ids:
            image = self._repo.get_by_id(image_id)
            if image is None:
                continue
            src = Path(image.file_path)
            if not src.exists():
                # File already missing — clean up DB record anyway
                moved_paths.append(image.file_path)
                continue
            dest = trash_dir / src.name
            counter = 0
            while dest.exists():
                counter += 1
                dest = trash_dir / f"{src.stem}_{counter}{src.suffix}"
            try:
                shutil.move(str(src), dest)
                moved_paths.append(image.file_path)
                LOGGER.info("Moved %s → %s", src, dest)
            except OSError:
                LOGGER.exception("Could not move %s to trash", src)

        return self._repo.delete_by_paths(moved_paths) if moved_paths else 0

    @staticmethod
    def _hash_file(path: Path) -> str:
        return hash_file(path)

    def _lookup_collections(self, image_ids: list[int]) -> dict[int, list[str]]:
        if self._collection_names is None or not image_ids:
            return {}
        return self._collection_names(image_ids)

    @staticmethod
    def _suggest_keeper(
        images: list[Image], collections: dict[int, list[str]] | None = None
    ) -> int:
        in_collection = collections or {}

        def score(img: Image) -> tuple[int, int, int, float]:
            return (
                # Trashing a collection member would drop it from the collection
                1 if img.id in in_collection else 0,
                1 if img.is_favorite else 0,
                img.rating or 0,
                -(img.indexed_at.timestamp() if img.indexed_at else 0),
            )

        best = max(images, key=score)
        if best.id is None:
            msg = "Image in duplicate group has no database ID"
            raise ValueError(msg)
        return best.id
