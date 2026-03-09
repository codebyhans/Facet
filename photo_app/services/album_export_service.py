"""Service for exporting albums to folders with EXIF preservation."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from photo_app.domain.repositories import ImageRepository

logger = logging.getLogger(__name__)


class AlbumExportService:
    """Export albums to filesystem with optional EXIF preservation."""

    def __init__(self, image_repository: ImageRepository) -> None:
        """Initialize export service.

        Args:
            image_repository: Repository for loading image metadata
        """
        self.image_repository = image_repository

    def export_to_folder(
        self,
        album_id: int,
        destination: Path,
        naming_pattern: str = "{date}_{name}",
        copy_mode: Literal["copy", "symlink"] = "copy",
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
            logger.info(f"Created export destination: {destination}")

        # Get album images
        album_images = self.image_repository.list_album_images(album_id)
        if not album_images:
            logger.warning(f"Album {album_id} has no images")
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
                    logger.warning(f"Source file not found: {src}")
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
                    logger.debug(f"Symlinked: {src} -> {dest_path}")
                else:
                    shutil.copy2(src, dest_path)
                    logger.debug(f"Copied: {src} -> {dest_path}")

                copied_files.append(str(dest_path))

            except Exception as exc:  # noqa: BLE001
                logger.exception(f"Failed to export {image.file_path}: {exc}")
                skipped += 1

        result = {
            "total_images": len(album_images),
            "copied_files": copied_files,
            "skipped": skipped,
            "destination": str(destination),
        }

        logger.info(
            f"Album export complete: {len(copied_files)} copied, {skipped} skipped"
        )
        return result

    def _generate_filename(self, image: object, pattern: str) -> str:
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
        replacements = {
            "{name}": src_path.stem,
            "{date}": "2020-01-01",  # Default if no datetime
            "{rating}": "0",
            "{tags}": "untagged",
        }

        # Add actual values if available
        if hasattr(image, "datetime_original") and image.datetime_original:
            try:
                dt = image.datetime_original
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                replacements["{date}"] = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        if hasattr(image, "rating") and image.rating:
            replacements["{rating}"] = str(image.rating)

        if hasattr(image, "tags") and image.tags:
            tag_list = image.tags if isinstance(image.tags, list) else [image.tags]
            if tag_list:
                replacements["{tags}"] = tag_list[0]

        # Apply replacements
        filename = pattern
        for placeholder, value in replacements.items():
            filename = filename.replace(placeholder, str(value))

        # Clean filename
        filename = "".join(c for c in filename if c.isalnum() or c in "._-")
        if not filename:
            filename = "photo"

        return filename + ext

    def _get_subdirectory(self, image: object) -> str:
        """Get subdirectory path for image based on date/person.

        Args:
            image: Image domain model

        Returns:
            Relative directory path
        """
        # Default: by year/month
        try:
            if hasattr(image, "datetime_original") and image.datetime_original:
                dt = image.datetime_original
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                return f"{dt.year}/{dt.month:02d}"
        except Exception:
            pass

        return "uncategorized"

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
