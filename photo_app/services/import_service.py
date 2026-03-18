from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from photo_app.infrastructure.exif_handler import ExifMetadataHandler

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date
    from pathlib import Path

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportOptions:
    """Options for camera import operation."""

    source_path: Path
    destination_path: Path
    run_indexing: bool = True
    run_face_detection: bool = False


@dataclass(frozen=True)
class ImportFileResult:
    """Result for individual file import."""

    source_path: Path
    dest_path: Path | None
    status: Literal["copied", "failed"]
    no_capture_date: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ImportSummary:
    """Summary of import operation."""

    total_files: int
    copied: int
    failed: int
    no_capture_date_count: int
    unhandled_paths: list[Path]
    destination_path: Path
    file_results: list[ImportFileResult]


class ImportService:
    """Service for importing files from camera to library."""

    def run_import(
        self,
        options: ImportOptions,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> ImportSummary:
        """Run import operation with progress callback.

        Args:
            options: Import configuration
            on_progress: Callback receiving (current, total, current_filename)
        """
        LOGGER.info(
            "Starting import from %s to %s",
            options.source_path,
            options.destination_path,
        )

        # Collect all files to import
        files_to_import = self._collect_files_to_import(options.source_path)
        total_files = len(files_to_import)
        LOGGER.info("Found %d files to import", total_files)

        if total_files == 0:
            return ImportSummary(
                total_files=0,
                copied=0,
                failed=0,
                no_capture_date_count=0,
                unhandled_paths=[],
                destination_path=options.destination_path,
                file_results=[],
            )

        file_results: list[ImportFileResult] = []
        copied_count = 0
        failed_count = 0
        no_capture_date_count = 0

        # Process each file
        for current, source_file in enumerate(files_to_import, 1):
            result = self._process_file(source_file, options)
            file_results.append(result)

            # Update counts
            if result.status == "copied":
                copied_count += 1
            else:
                failed_count += 1
            if result.no_capture_date:
                no_capture_date_count += 1

            # Emit progress
            if on_progress is not None:
                filename = source_file.name
                on_progress(current, total_files, filename)

        # Build unhandled paths list
        unhandled_paths = []
        for result in file_results:
            if result.status == "failed":
                unhandled_paths.append(result.source_path)
            elif result.no_capture_date and result.dest_path is not None:
                unhandled_paths.append(result.dest_path)

        summary = ImportSummary(
            total_files=total_files,
            copied=copied_count,
            failed=failed_count,
            no_capture_date_count=no_capture_date_count,
            unhandled_paths=unhandled_paths,
            destination_path=options.destination_path,
            file_results=file_results,
        )

        LOGGER.info(
            "Import completed: %d copied, %d failed, %d without capture date",
            copied_count,
            failed_count,
            no_capture_date_count,
        )

        return summary

    def _resolve_capture_date(self, file_path: Path) -> date | None:
        """Read capture date from EXIF. Returns None for non-image files or missing EXIF."""
        try:
            exif_data = ExifMetadataHandler.read_exif(str(file_path))
            datetime_original = exif_data.get("datetime_original")
            if isinstance(datetime_original, datetime):
                return datetime_original.date()
        except (ValueError, AttributeError, OSError) as exc:
            # piexif fails on video files and other non-image formats
            LOGGER.debug("Failed to read EXIF from %s: %s", file_path, exc)

        return None

    def _resolve_dest_path(self, dest_folder: Path, filename: str) -> Path:
        """Return a collision-free destination path, appending _2, _3 etc. if needed."""
        dest_path = dest_folder / filename

        if not dest_path.exists():
            return dest_path

        # Extract stem and suffix
        stem = dest_path.stem
        suffix = dest_path.suffix

        counter = 2
        while True:
            new_filename = f"{stem}_{counter}{suffix}"
            new_path = dest_folder / new_filename
            if not new_path.exists():
                return new_path
            counter += 1

    def _collect_files_to_import(self, source_path: Path) -> list[Path]:
        """Collect all files to import from the source path."""
        files_to_import = []
        try:
            for file_path in source_path.rglob("*"):
                if file_path.is_file():
                    # Skip macOS resource forks
                    if file_path.name.startswith("._"):
                        continue
                    files_to_import.append(file_path)
        except Exception as exc:
            LOGGER.exception("Failed to scan source directory")
            msg = f"Failed to scan source directory: {exc}"
            raise RuntimeError(msg) from exc
        return files_to_import

    def _process_file(
        self, source_file: Path, options: ImportOptions
    ) -> ImportFileResult:
        """Process a single file for import."""
        try:
            # Extract capture date from EXIF
            capture_date = self._resolve_capture_date(source_file)

            # Determine destination folder
            if capture_date is not None:
                dest_folder = (
                    options.destination_path
                    / f"{capture_date.year}"
                    / f"{capture_date.year}-{capture_date.month:02d}-{capture_date.day:02d}"
                )
            else:
                dest_folder = options.destination_path / "undated"

            # Create destination folder
            dest_folder.mkdir(parents=True, exist_ok=True)

            # Resolve filename collision
            dest_path = self._resolve_dest_path(dest_folder, source_file.name)

            # Copy file
            shutil.copy2(source_file, dest_path)

        except (OSError, PermissionError) as exc:
            # File operation failed
            error_result = ImportFileResult(
                source_path=source_file,
                dest_path=None,
                status="failed",
                error=str(exc),
            )
            LOGGER.warning("Failed to copy %s: %s", source_file, exc)
            return error_result
        except Exception as exc:
            # Unexpected error
            error_result = ImportFileResult(
                source_path=source_file,
                dest_path=None,
                status="failed",
                error=str(exc),
            )
            LOGGER.exception("Unexpected error processing %s", source_file)
            return error_result
        else:
            # Create success result
            result: ImportFileResult = ImportFileResult(
                source_path=source_file,
                dest_path=dest_path,
                status="copied",
                no_capture_date=capture_date is None,
            )
            return result
