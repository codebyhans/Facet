from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from photo_app.infrastructure.exif_handler import ExifMetadataHandler

if TYPE_CHECKING:
    from collections.abc import Callable

LOGGER = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({
    ".heic", ".heif", ".jpg", ".jpeg", ".png", ".dng",
    ".raw", ".arw", ".cr2", ".cr3", ".nef", ".tif", ".tiff",
})
VIDEO_EXTENSIONS = frozenset({".mov", ".mp4", ".m4v", ".3gp", ".avi"})
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# iCloud-optimized photos are stored as tiny placeholder thumbnails on-device.
# A real HEIC photo is at minimum several hundred KB; placeholders are typically <50 KB.
ICLOUD_STUB_MAX_BYTES = 100 * 1024  # 100 KB threshold


@dataclass(frozen=True)
class IPhoneDevice:
    udid: str
    name: str


@dataclass(frozen=True)
class IPhoneFileResult:
    iphone_path: str
    dest_path: Path | None
    status: Literal["copied", "failed", "skipped"]
    is_sidecar: bool
    error: str | None = None


@dataclass(frozen=True)
class IPhoneImportSummary:
    total_found: int
    copied: int
    failed: int
    skipped_icloud: int
    file_results: list[IPhoneFileResult] = field(default_factory=list)
    destination_path: Path = field(default_factory=Path)


class IPhoneImportService:
    """Service for importing media from an iPhone via the AFC (Apple File Connection) protocol.

    Requires iTunes or Apple Mobile Device Support drivers installed on Windows,
    and the iPhone to have tapped "Trust This Computer" at least once.
    """

    def list_devices(self) -> list[IPhoneDevice]:
        """Return connected iPhones. Returns empty list if none found or drivers missing."""
        try:
            from pymobiledevice3 import usbmux
            from pymobiledevice3.lockdown import create_using_usbmux
        except ImportError:
            LOGGER.error("pymobiledevice3 is not installed — cannot detect iPhone devices")
            return []

        raw_devices = usbmux.list_devices()
        if not raw_devices:
            return []

        devices: list[IPhoneDevice] = []
        for dev in raw_devices:
            udid = dev.serial
            try:
                lockdown = create_using_usbmux(serial=udid)
                name: str = lockdown.display_name or udid
            except Exception as exc:
                LOGGER.warning("Could not read device info for %s: %s", udid, exc)
                name = udid
            devices.append(IPhoneDevice(udid=udid, name=name))

        return devices

    def run_import(
        self,
        udid: str,
        dest_path: Path,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> IPhoneImportSummary:
        """Import all media from the iPhone's DCIM folder to dest_path.

        Files are organised as YYYY/YYYY-MM-DD/filename, matching the camera import structure.
        Live Photo sidecar MOVs are placed in the same folder as their parent image.
        Standalone videos get their own dated folder.
        iCloud placeholder files (too small to be real photos) are skipped with a warning.
        """
        try:
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.afc import AfcService
        except ImportError as exc:
            msg = "pymobiledevice3 is not installed. Run: pip install pymobiledevice3"
            raise RuntimeError(msg) from exc

        LOGGER.info("Connecting to iPhone %s", udid)
        lockdown = create_using_usbmux(serial=udid)

        file_results: list[IPhoneFileResult] = []

        with AfcService(lockdown) as afc:
            subfolder_files = self._collect_dcim_files(afc)

            total = sum(len(files) for files in subfolder_files.values())
            LOGGER.info("Found %d media files across %d DCIM subfolders", total, len(subfolder_files))

            current = 0
            for _subfolder, files in subfolder_files.items():
                # Track image destination folders so sidecar MOVs can be paired
                image_dest_by_stem: dict[str, Path] = {}

                # Pass 1: images
                for filename, iphone_path in files:
                    if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
                        continue
                    current += 1
                    if on_progress is not None:
                        on_progress(current, total, filename)

                    result = self._process_one_file(
                        afc, iphone_path, is_sidecar=False,
                        forced_dest_folder=None, dest_root=dest_path,
                    )
                    file_results.append(result)

                    if result.status == "copied" and result.dest_path is not None:
                        stem = Path(filename).stem.lower()
                        image_dest_by_stem[stem] = result.dest_path.parent

                # Pass 2: videos (sidecars first, then standalone)
                for filename, iphone_path in files:
                    if Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
                        continue
                    current += 1
                    if on_progress is not None:
                        on_progress(current, total, filename)

                    stem = Path(filename).stem.lower()
                    forced_folder = image_dest_by_stem.get(stem)
                    is_sidecar = forced_folder is not None

                    result = self._process_one_file(
                        afc, iphone_path, is_sidecar=is_sidecar,
                        forced_dest_folder=forced_folder, dest_root=dest_path,
                    )
                    file_results.append(result)

        copied = sum(1 for r in file_results if r.status == "copied")
        failed = sum(1 for r in file_results if r.status == "failed")
        skipped = sum(1 for r in file_results if r.status == "skipped")

        LOGGER.info(
            "iPhone import complete: %d copied, %d failed, %d skipped (iCloud placeholders)",
            copied, failed, skipped,
        )

        return IPhoneImportSummary(
            total_found=len(file_results),
            copied=copied,
            failed=failed,
            skipped_icloud=skipped,
            file_results=file_results,
            destination_path=dest_path,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_dcim_files(self, afc: object) -> dict[str, list[tuple[str, str]]]:
        """Walk /DCIM/ and return {subfolder: [(filename, full_iphone_path)]} for media files."""
        try:
            dcim_entries: list[str] = afc.listdir("/DCIM/")  # type: ignore[attr-defined]
        except Exception as exc:
            msg = f"Cannot read /DCIM/ from iPhone: {exc}"
            raise RuntimeError(msg) from exc

        result: dict[str, list[tuple[str, str]]] = {}

        for entry in dcim_entries:
            if entry in (".", ".."):
                continue
            subfolder = f"/DCIM/{entry}"
            try:
                entries: list[str] = afc.listdir(subfolder)  # type: ignore[attr-defined]
                files: list[tuple[str, str]] = []
                for filename in entries:
                    if filename in (".", ".."):
                        continue
                    ext = Path(filename).suffix.lower()
                    if ext in MEDIA_EXTENSIONS:
                        files.append((filename, f"{subfolder}/{filename}"))
                if files:
                    result[subfolder] = files
            except Exception as exc:
                LOGGER.warning("Could not list DCIM subfolder %s: %s", subfolder, exc)

        return result

    def _process_one_file(
        self,
        afc: object,
        iphone_path: str,
        is_sidecar: bool,
        forced_dest_folder: Path | None,
        dest_root: Path,
    ) -> IPhoneFileResult:
        """Copy a single file from the iPhone to the local destination."""
        filename = iphone_path.rsplit("/", 1)[-1]
        ext = Path(filename).suffix.lower()
        tmp_path: Path | None = None

        try:
            # Check file size to detect iCloud placeholder stubs
            stat: dict[str, object] = afc.stat(iphone_path)  # type: ignore[attr-defined]
            file_size = int(stat.get("st_size", 0))  # type: ignore[arg-type]
            afc_mtime_ns = int(stat.get("st_mtime", 0))  # type: ignore[arg-type]

            if ext in IMAGE_EXTENSIONS and file_size < ICLOUD_STUB_MAX_BYTES:
                LOGGER.debug(
                    "Skipping %s — size %d bytes suggests iCloud placeholder",
                    iphone_path, file_size,
                )
                return IPhoneFileResult(
                    iphone_path=iphone_path,
                    dest_path=None,
                    status="skipped",
                    is_sidecar=is_sidecar,
                    error=(
                        f"File is only {file_size:,} bytes — likely an iCloud placeholder. "
                        "Open the Photos app on your iPhone and download the photo to device first."
                    ),
                )

            # Stream the file to a local temp file so we can read EXIF before deciding the dest folder
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_fh:
                tmp_path = Path(tmp_fh.name)
                # NOTE: get_file_contents loads the entire file into memory.
                # For very large video files (>1 GB) this may cause high memory usage.
                contents: bytes = afc.get_file_contents(iphone_path)  # type: ignore[attr-defined]
                tmp_fh.write(contents)

            # Determine destination folder
            if forced_dest_folder is not None:
                dest_folder = forced_dest_folder
            else:
                capture_date = self._resolve_capture_date(tmp_path, afc_mtime_ns)
                if capture_date is not None:
                    dest_folder = (
                        dest_root
                        / str(capture_date.year)
                        / f"{capture_date.year}-{capture_date.month:02d}-{capture_date.day:02d}"
                    )
                else:
                    dest_folder = dest_root / "undated"

            dest_folder.mkdir(parents=True, exist_ok=True)
            dest_file_path = self._resolve_dest_path(dest_folder, filename)

            # Move temp → final destination
            tmp_path.rename(dest_file_path)
            tmp_path = None  # Successfully moved — don't delete in finally

            # Restore iPhone mtime on the copied file
            if afc_mtime_ns:
                mtime_s = afc_mtime_ns / 1e9
                os.utime(dest_file_path, (mtime_s, mtime_s))

            LOGGER.debug("Copied %s → %s", iphone_path, dest_file_path)
            return IPhoneFileResult(
                iphone_path=iphone_path,
                dest_path=dest_file_path,
                status="copied",
                is_sidecar=is_sidecar,
            )

        except Exception as exc:
            LOGGER.warning("Failed to copy %s: %s", iphone_path, exc)
            return IPhoneFileResult(
                iphone_path=iphone_path,
                dest_path=None,
                status="failed",
                is_sidecar=is_sidecar,
                error=str(exc),
            )
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    def _resolve_capture_date(self, local_path: Path, afc_mtime_ns: int) -> date | None:
        """Try EXIF first, then AFC mtime, then local file mtime."""
        try:
            exif_data = ExifMetadataHandler.read_exif(str(local_path))
            dt_original = exif_data.get("datetime_original")
            if dt_original is not None:
                return dt_original.date()  # type: ignore[union-attr]
        except Exception as exc:
            LOGGER.debug("EXIF read failed for %s: %s", local_path.name, exc)

        if afc_mtime_ns:
            try:
                mtime_s = afc_mtime_ns / 1e9
                return datetime.fromtimestamp(mtime_s, tz=UTC).date()
            except Exception:
                pass

        try:
            return datetime.fromtimestamp(local_path.stat().st_mtime, tz=UTC).date()
        except Exception:
            pass

        return None

    def _resolve_dest_path(self, dest_folder: Path, filename: str) -> Path:
        """Return a collision-free destination path, appending _2, _3 etc. if needed."""
        dest_path = dest_folder / filename
        if not dest_path.exists():
            return dest_path

        stem = dest_path.stem
        suffix = dest_path.suffix
        counter = 2
        while True:
            candidate = dest_folder / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1
