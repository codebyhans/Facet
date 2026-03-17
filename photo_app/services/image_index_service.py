from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from blake3 import blake3
from PIL import Image, ImageOps, UnidentifiedImageError

from photo_app.domain.models import Image as ImageEntity
from photo_app.domain.services import now_utc
from photo_app.infrastructure.exif_handler import ExifMetadataHandler

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from photo_app.domain.repositories import ImageRepository
    from photo_app.infrastructure.file_scanner import FileScanner, ScannedFile
    from photo_app.infrastructure.thumbnail_store import ThumbnailStore
    from photo_app.services.album_query_cache_service import AlbumQueryCacheService

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageIndexResult:
    """Result summary for image indexing."""

    scanned: int
    inserted: int
    skipped: int


class ImageIndexService:
    """Use case for filesystem image indexing."""

    def __init__(
        self,
        image_repository: ImageRepository,
        file_scanner: FileScanner,
        thumbnail_store: ThumbnailStore,
        query_cache_service: AlbumQueryCacheService | None = None,
    ) -> None:
        self._image_repository = image_repository
        self._file_scanner = file_scanner
        self._thumbnail_store = thumbnail_store
        self._query_cache_service = query_cache_service

    def index_folder(  # noqa: C901
        self, root: Path, on_progress: Callable[[int, int], None] | None = None
    ) -> ImageIndexResult:
        """Scan and index newly discovered files only.

        Args:
            root: Root directory to scan
            on_progress: Optional callback(current, total) for progress updates
        """
        scanned_files: list[ScannedFile] = []
        try:
            scanned_files = self._file_scanner.scan(root)
            LOGGER.info("Scanned %s image files from %s", len(scanned_files), root)
        except Exception:
            LOGGER.exception("Scan error while scanning %s", root)
            return ImageIndexResult(scanned=0, inserted=0, skipped=0)

        # Single DB query to fetch all already-indexed paths — avoids N+1 sessions
        existing_paths: set[str] = set(self._image_repository.list_all_paths())

        staged: list[ImageEntity] = []
        skipped = 0
        thumbnail_failures = 0

        for idx, scanned in enumerate(scanned_files):
            file_path = str(scanned.file_path)

            # Report progress every 10 files
            if on_progress and (idx % 10 == 0 or idx == len(scanned_files) - 1):
                on_progress(idx + 1, len(scanned_files))

            try:
                if file_path in existing_paths:
                    continue

                # Extract EXIF-based capture time (fallback to folder date)
                capture_date, width, height = self._load_image_metadata(
                    scanned.file_path, scanned.folder_date
                )

                # Use path-based hash instead of reading file to avoid crashes
                image_hash = sha256(file_path.encode()).hexdigest()
                entity = ImageEntity(
                    id=None,
                    file_path=file_path,
                    capture_date=capture_date,
                    year=capture_date.date().year if capture_date else None,
                    month=capture_date.date().month if capture_date else None,
                    hash=image_hash,
                    width=width,
                    height=height,
                    indexed_at=now_utc(),
                )
                staged.append(entity)

                # Skip thumbnail generation during indexing - generate on-demand while browsing
                # This prevents crashes from problematic image files

            except OSError, UnidentifiedImageError, PermissionError, IsADirectoryError:
                skipped += 1
                continue
            except Exception:
                LOGGER.exception("Unexpected error processing %s", file_path)
                skipped += 1
                continue

        if staged:
            try:
                self._image_repository.add_many(staged)
                LOGGER.info("Inserted %s images into database", len(staged))
                if self._query_cache_service is not None:
                    self._query_cache_service.invalidate_all()
            except Exception:
                LOGGER.exception("Database error during bulk insert")
                return ImageIndexResult(
                    scanned=len(scanned_files),
                    inserted=0,
                    skipped=skipped + len(staged),
                )

        if thumbnail_failures > 0:
            LOGGER.info(
                "Skipped %s thumbnails during indexing (can be regenerated)",
                thumbnail_failures,
            )

        LOGGER.info(
            "Index complete: scanned=%s, inserted=%s, skipped=%s",
            len(scanned_files),
            len(staged),
            skipped,
        )
        return ImageIndexResult(
            scanned=len(scanned_files),
            inserted=len(staged),
            skipped=skipped,
        )

    def set_thumbnail_max_size(self, max_size: int) -> None:
        """Apply runtime thumbnail size for future generations."""
        self._thumbnail_store.set_max_size(max_size)

    def _compute_hash(self, file_path: Path) -> str:
        """Compute BLAKE3 hash of file for deduplication."""
        h = blake3()
        try:
            with file_path.open("rb") as stream:
                while True:
                    block = stream.read(1024 * 1024)  # Read in 1MB chunks
                    if not block:
                        break
                    h.update(block)
        except OSError as exc:
            LOGGER.warning("Hash computation failed for %s: %s", file_path, exc)
            # Fallback: use file size and name
            h.update(str(file_path.stat().st_size).encode())
            h.update(file_path.name.encode())
        return h.hexdigest()

    def _load_image_metadata(
        self,
        file_path: Path,
        fallback_date: date | None,
    ) -> tuple[datetime | None, int, int]:
        with Image.open(file_path) as img:
            oriented = ImageOps.exif_transpose(img)
            width, height = oriented.size
            exif = oriented.getexif()
            capture = exif.get(36867)
            capture_date: datetime | None = None
            if isinstance(capture, str):
                raw_value = str(capture)
                try:
                    capture_date = datetime.strptime(
                        raw_value, "%Y:%m:%d %H:%M:%S"
                    ).replace(tzinfo=UTC)
                except ValueError:
                    capture_date = None
            if capture_date is None:
                exif_data = ExifMetadataHandler.read_exif(str(file_path))
                capture_date = exif_data.get("datetime_original")
            if capture_date is None:
                capture_date = self._fallback_datetime(fallback_date)
            return capture_date, width, height

    @staticmethod
    def _fallback_datetime(value: date | None) -> datetime | None:
        if value is None:
            return None
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
