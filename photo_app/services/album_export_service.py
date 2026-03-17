"""Service for exporting albums to folders with EXIF preservation."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from photo_app.domain.models import Image
    from photo_app.services.album_service import AlbumService

logger = logging.getLogger(__name__)


class AlbumExportService:
    """Export albums to filesystem with optional EXIF preservation."""

    def __init__(self, album_service: AlbumService) -> None:
        """Initialize export service.

        Args:
            album_service: Service for loading album images
        """
        self._album_service = album_service

    def export_to_folder(
        self,
        album_id: int,
        destination: Path,
        naming_pattern: str = "{date}_{name}",
        copy_mode: Literal["copy", "symlink"] = "copy",
        *,
        preserve_structure: bool = False,
    ) -> dict[str, object]:
        """Export album images to folder.

        Args:
            album_id: Album ID to export
            destination: Target folder path
            naming_pattern: Naming pattern for files
                - {date}: Image capture date (YYYY-MM-DD)
                - {name}: Original filename
                - {rating}: Star rating (0-5)
                - {tags}: First tag (if any)
            copy_mode: 'copy' to copy files, 'symlink' for symlinks
            preserve_structure: Create subdirectories for months/people

        Returns:
            Dictionary with export results:
                - total_images: Number of images exported
                - copied_files: List of destination paths
                - skipped: Count of errors
                - destination: Export folder path
        """
        destination = Path(destination)
        if not destination.exists():
            destination.mkdir(parents=True, exist_ok=True)
            logger.info("Created export destination: %s", destination)

        # Get album images (page through results)
        album_images = self._load_album_images(album_id)
        if not album_images:
            logger.warning("Album %s has no images", album_id)
            return {
                "total_images": 0,
                "copied_files": [],
                "skipped": 0,
                "destination": str(destination),
            }

        copied_files = []
        skipped = 0

        for image in album_images:
            try:
                src = Path(image.file_path)
                if not src.exists():
                    logger.warning("Source file not found: %s", src)
                    skipped += 1
                    continue

                # Generate destination filename
                dest_filename = self._generate_filename(image, naming_pattern)

                # Create subdirectories if requested
                if preserve_structure:
                    subdir = self._get_subdirectory(image)
                    dest_dir = destination / subdir
                    dest_dir.mkdir(parents=True, exist_ok=True)
                else:
                    dest_dir = destination

                dest_path = dest_dir / dest_filename

                # Handle duplicate filenames
                dest_path = self._get_unique_path(dest_path)

                # Copy or symlink
                if copy_mode == "symlink":
                    if dest_path.exists() or dest_path.is_symlink():
                        dest_path.unlink()
                    dest_path.symlink_to(src)
                    logger.debug("Symlinked: %s -> %s", src, dest_path)
                else:
                    shutil.copy2(src, dest_path)
                    logger.debug("Copied: %s -> %s", src, dest_path)

                copied_files.append(str(dest_path))

            except Exception:
                logger.exception("Failed to export %s", image.file_path)
                skipped += 1

        result = {
            "total_images": len(album_images),
            "copied_files": copied_files,
            "skipped": skipped,
            "destination": str(destination),
        }

        logger.info(
            "Album export complete: %s copied, %s skipped", len(copied_files), skipped
        )
        return result

    def _generate_filename(self, image: Image, pattern: str) -> str:
        """Generate destination filename from pattern.

        Args:
            image: Image domain model
            pattern: Naming pattern with {placeholders}

        Returns:
            Filename with extension preserved
        """
        src_path = Path(image.file_path)
        ext = src_path.suffix

        # Extract values for pattern
        replacements: dict[str, str] = {
            "{name}": src_path.stem,
            "{date}": "2020-01-01",  # Default if no datetime
            "{rating}": "0",
            "{tags}": "untagged",
        }

        # Add actual values if available
        if image.capture_date:
            replacements["{date}"] = image.capture_date.date().isoformat()

        if image.rating is not None:
            replacements["{rating}"] = str(image.rating)

        # Apply replacements
        filename = pattern
        for placeholder, value in replacements.items():
            filename = filename.replace(placeholder, str(value))

        # Clean filename
        filename = "".join(c for c in filename if c.isalnum() or c in "._-")
        if not filename:
            filename = "photo"

        return filename + ext

    def _get_subdirectory(self, image: Image) -> str:
        """Get subdirectory path for image based on date/person.

        Args:
            image: Image domain model

        Returns:
            Relative directory path
        """
        # Default: by year/month
        if image.capture_date:
            return (
                f"{image.capture_date.date().year}/"
                f"{image.capture_date.date().month:02d}"
            )

        return "uncategorized"

    def _load_album_images(self, album_id: int) -> list[Image]:
        """Load all images for an album by paging through results."""
        offset = 0
        limit = 500
        images: list[Image] = []
        while True:
            page = self._album_service.list_album_images(
                album_id,
                offset=offset,
                limit=limit,
            )
            if not page.items:
                break
            images.extend(page.items)
            if len(page.items) < limit:
                break
            offset += limit
        return images

    def _get_unique_path(self, path: Path) -> Path:
        """Get unique file path by appending number if needed.

        Args:
            path: Desired file path

        Returns:
            Unique file path that doesn't exist
        """
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        counter = 1

        while True:
            new_name = f"{stem}_{counter}{suffix}"
            new_path = path.parent / new_name
            if not new_path.exists():
                return new_path
            counter += 1
