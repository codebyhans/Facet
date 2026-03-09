from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Callable

from blake3 import blake3
from PIL import Image, ImageOps, UnidentifiedImageError

from photo_app.domain.models import Image as ImageEntity
from photo_app.domain.services import now_utc

if TYPE_CHECKING:
    from pathlib import Path

    from photo_app.domain.repositories import ImageRepository
    from photo_app.infrastructure.file_scanner import FileScanner
    from photo_app.infrastructure.thumbnail_store import ThumbnailStore
    from photo_app.services.album_query_cache_service import AlbumQueryCacheService


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

    def index_folder(
        self, root: Path, on_progress: Callable[[int, int], None] | None = None
    ) -> ImageIndexResult:
        """Scan and index newly discovered files only.
        
        Args:
            root: Root directory to scan
            on_progress: Optional callback(current, total) for progress updates
        """
        import logging
        logger = logging.getLogger(__name__)
        
        scanned_files: list[ScannedFile] = []
        try:
            scanned_files = self._file_scanner.scan(root)
            logger.info(f"Scanned {len(scanned_files)} image files from {root}")
        except Exception as exc:
            logger.exception(f"Scan error: {exc}")
            return ImageIndexResult(scanned=0, inserted=0, skipped=0)
        
        staged: list[ImageEntity] = []
        skipped = 0
        thumbnail_failures = 0

        for idx, scanned in enumerate(scanned_files):
            file_path = str(scanned.file_path)
            
            # Report progress every 10 files
            if idx % 10 == 0 or idx == len(scanned_files) - 1:
                if on_progress:
                    on_progress(idx + 1, len(scanned_files))
            
            try:
                if self._image_repository.exists_by_path(file_path):
                    continue

                # Skip expensive metadata extraction during indexing
                # Use folder date and placeholder dimensions to avoid PIL crashes on malformed images
                capture_date = scanned.folder_date
                width, height = 0, 0
                
                # Use path-based hash instead of reading file to avoid crashes
                from hashlib import md5
                image_hash = md5(file_path.encode()).hexdigest()
                entity = ImageEntity(
                    id=None,
                    file_path=file_path,
                    capture_date=capture_date,
                    year=capture_date.year if capture_date else None,
                    month=capture_date.month if capture_date else None,
                    hash=image_hash,
                    width=width,
                    height=height,
                    indexed_at=now_utc(),
                )
                staged.append(entity)
                
                # Skip thumbnail generation during indexing - generate on-demand while browsing
                # This prevents crashes from problematic image files
                    
            except (OSError, UnidentifiedImageError, PermissionError, IsADirectoryError):
                skipped += 1
                continue
            except Exception as exc:
                logger.exception(f"Unexpected error processing {file_path}: {exc}")
                skipped += 1
                continue

        if staged:
            try:
                self._image_repository.add_many(staged)
                logger.info(f"Inserted {len(staged)} images into database")
                if self._query_cache_service is not None:
                    self._query_cache_service.invalidate_all()
            except Exception as db_exc:
                logger.exception(f"Database error during bulk insert: {db_exc}")
                return ImageIndexResult(
                    scanned=len(scanned_files),
                    inserted=0,
                    skipped=skipped + len(staged),
                )

        if thumbnail_failures > 0:
            logger.info(f"Skipped {thumbnail_failures} thumbnails during indexing (can be regenerated)")
        
        logger.info(f"Index complete: scanned={len(scanned_files)}, inserted={len(staged)}, skipped={skipped}")
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
        except (OSError, IOError) as exc:
            import logging
            logging.warning(f"Hash computation failed for {file_path}: {exc}")
            # Fallback: use file size and name
            h.update(str(file_path.stat().st_size).encode())
            h.update(file_path.name.encode())
        return h.hexdigest()

    def _load_image_metadata(
        self,
        file_path: Path,
        fallback_date: date | None,
    ) -> tuple[date | None, int, int]:
        with Image.open(file_path) as img:
            oriented = ImageOps.exif_transpose(img)
            width, height = oriented.size
            exif = oriented.getexif()
            capture = exif.get(36867)
            capture_date = fallback_date
            if isinstance(capture, str):
                raw_value = str(capture).split(" ")[0].replace(":", "-")
                try:
                    capture_date = date.fromisoformat(raw_value)
                except ValueError:
                    capture_date = fallback_date
            return capture_date, width, height
