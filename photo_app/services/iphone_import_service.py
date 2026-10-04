from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from photo_app.infrastructure.exif_handler import ExifMetadataHandler
from photo_app.infrastructure.quicktime_handler import read_quicktime_date
from photo_app.services.import_dedup import FolderDuplicateChecker

if TYPE_CHECKING:
    from collections.abc import Callable

LOGGER = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset(
    {
        ".heic",
        ".heif",
        ".jpg",
        ".jpeg",
        ".png",
        ".dng",
        ".raw",
        ".arw",
        ".cr2",
        ".cr3",
        ".nef",
        ".tif",
        ".tiff",
    }
)
VIDEO_EXTENSIONS = frozenset({".mov", ".mp4", ".m4v", ".3gp", ".avi"})
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# iCloud-optimized photos are stored as tiny placeholder thumbnails on-device.
# A real HEIC photo is at minimum several hundred KB; placeholders are typically <50 KB.
ICLOUD_STUB_MAX_BYTES = 10 * 1024  # 10 KB — real iCloud stubs are typically 3-8 KB


@dataclass(frozen=True)
class IPhoneDevice:
    udid: str
    name: str


@dataclass(frozen=True)
class IPhoneFileResult:
    iphone_path: str
    dest_path: Path | None
    status: Literal["copied", "failed", "skipped", "already_exists"]
    is_sidecar: bool
    error: str | None = None


@dataclass(frozen=True)
class IPhoneImportSummary:
    total_found: int
    copied: int
    failed: int
    skipped_icloud: int
    skipped_existing: int = 0
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
            from pymobiledevice3 import (  # type: ignore[import-untyped]  # noqa: F401, PLC0415
                usbmux,
            )
            from pymobiledevice3.lockdown import (  # type: ignore[import-untyped]  # noqa: F401, PLC0415
                create_using_usbmux,
            )
        except ImportError:
            LOGGER.error(  # noqa: TRY400
                "pymobiledevice3 is not installed — cannot detect iPhone devices"
            )
            return []

        try:
            return asyncio.run(self._list_devices_async())
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to list iPhone devices: %s", exc)
            return []

    async def _list_devices_async(self) -> list[IPhoneDevice]:
        from pymobiledevice3 import usbmux  # noqa: PLC0415
        from pymobiledevice3.lockdown import create_using_usbmux  # noqa: PLC0415

        raw_devices = await usbmux.list_devices()
        if not raw_devices:
            return []

        devices: list[IPhoneDevice] = []
        for dev in raw_devices:
            udid = dev.serial
            try:
                lockdown = await create_using_usbmux(serial=udid)
                name: str = lockdown.display_name or udid
            except Exception as exc:  # noqa: BLE001
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
            from pymobiledevice3.lockdown import (  # noqa: F401, PLC0415
                create_using_usbmux,
            )
            from pymobiledevice3.services.afc import (  # type: ignore[import-untyped]  # noqa: F401, PLC0415
                AfcService,
            )
        except ImportError as exc:
            msg = "pymobiledevice3 is not installed. Run: pip install pymobiledevice3"
            raise RuntimeError(msg) from exc

        return asyncio.run(self._run_import_async(udid, dest_path, on_progress))

    async def _run_import_async(
        self,
        udid: str,
        dest_path: Path,
        on_progress: Callable[[int, int, str], None] | None,
    ) -> IPhoneImportSummary:
        from pymobiledevice3.lockdown import create_using_usbmux  # noqa: PLC0415
        from pymobiledevice3.services.afc import AfcService  # noqa: PLC0415

        LOGGER.info("Connecting to iPhone %s", udid)
        lockdown = await create_using_usbmux(serial=udid)

        file_results: list[IPhoneFileResult] = []
        checker = FolderDuplicateChecker()

        async with AfcService(lockdown) as afc:
            subfolder_files = await self._collect_dcim_files(afc)

            total = sum(len(files) for files in subfolder_files.values())
            LOGGER.info(
                "Found %d media files across %d DCIM subfolders",
                total,
                len(subfolder_files),
            )

            current = 0
            for files in subfolder_files.values():
                # Track image destination paths so sidecar MOVs can be paired
                image_dest_by_stem: dict[str, Path] = {}

                # Pass 1: images
                for filename, iphone_path in files:
                    if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
                        continue
                    current += 1
                    if on_progress is not None:
                        on_progress(current, total, filename)

                    result = await self._process_one_file(
                        afc,
                        iphone_path,
                        is_sidecar=False,
                        forced_dest_folder=None,
                        forced_dest_name=None,
                        dest_root=dest_path,
                        checker=checker,
                    )
                    file_results.append(result)

                    if (
                        result.status in ("copied", "already_exists")
                        and result.dest_path is not None
                    ):
                        stem = Path(filename).stem.lower()
                        image_dest_by_stem[stem] = result.dest_path

                # Pass 2: videos (sidecars first, then standalone)
                for filename, iphone_path in files:
                    if Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
                        continue
                    current += 1
                    if on_progress is not None:
                        on_progress(current, total, filename)

                    stem = Path(filename).stem.lower()
                    image_dest = image_dest_by_stem.get(stem)
                    is_sidecar = image_dest is not None

                    # Name the sidecar after the image's actual stem so a
                    # renamed image (IMG_1234_2.HEIC) keeps its pairing.
                    result = await self._process_one_file(
                        afc,
                        iphone_path,
                        is_sidecar=is_sidecar,
                        forced_dest_folder=image_dest.parent if image_dest else None,
                        forced_dest_name=(
                            image_dest.stem + Path(filename).suffix
                            if image_dest
                            else None
                        ),
                        dest_root=dest_path,
                        checker=checker,
                    )
                    file_results.append(result)

        copied = sum(1 for r in file_results if r.status == "copied")
        failed = sum(1 for r in file_results if r.status == "failed")
        skipped = sum(1 for r in file_results if r.status == "skipped")
        already_exists = sum(1 for r in file_results if r.status == "already_exists")

        LOGGER.info(
            "iPhone import complete: %d copied, %d failed, %d skipped (iCloud), %d already existed",
            copied,
            failed,
            skipped,
            already_exists,
        )

        return IPhoneImportSummary(
            total_found=len(file_results),
            copied=copied,
            failed=failed,
            skipped_icloud=skipped,
            skipped_existing=already_exists,
            file_results=file_results,
            destination_path=dest_path,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _collect_dcim_files(
        self, afc: object
    ) -> dict[str, list[tuple[str, str]]]:
        """Walk /DCIM/ and return {subfolder: [(filename, full_iphone_path)]} for media files."""
        try:
            dcim_entries: list[str] = await afc.listdir("/DCIM/")  # type: ignore[attr-defined]
        except Exception as exc:
            msg = f"Cannot read /DCIM/ from iPhone: {exc}"
            raise RuntimeError(msg) from exc

        result: dict[str, list[tuple[str, str]]] = {}

        for entry in dcim_entries:
            subfolder = f"/DCIM/{entry}"
            try:
                entries: list[str] = await afc.listdir(subfolder)  # type: ignore[attr-defined]
                files: list[tuple[str, str]] = [
                    (filename, f"{subfolder}/{filename}")
                    for filename in entries
                    if Path(filename).suffix.lower() in MEDIA_EXTENSIONS
                ]
                if files:
                    result[subfolder] = files
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Could not list DCIM subfolder %s: %s", subfolder, exc)

        return result

    async def _process_one_file(  # noqa: PLR0913
        self,
        afc: object,
        iphone_path: str,
        *,
        is_sidecar: bool,
        forced_dest_folder: Path | None,
        forced_dest_name: str | None,
        dest_root: Path,
        checker: FolderDuplicateChecker,
    ) -> IPhoneFileResult:
        """Copy a single file from the iPhone to the local destination."""
        filename = iphone_path.rsplit("/", 1)[-1]
        ext = Path(filename).suffix.lower()
        tmp_path: Path | None = None

        try:
            # Check file size to detect iCloud placeholder stubs.
            # st_size is int; st_mtime is a datetime object in this pymobiledevice3 version.
            stat: dict[str, object] = await afc.stat(iphone_path)  # type: ignore[attr-defined]
            file_size = int(stat.get("st_size", 0))  # type: ignore[call-overload]
            afc_mtime: datetime | None = stat.get("st_mtime")  # type: ignore[assignment]

            if ext in IMAGE_EXTENSIONS and file_size < ICLOUD_STUB_MAX_BYTES:
                LOGGER.debug(
                    "Skipping %s — size %d bytes suggests iCloud placeholder",
                    iphone_path,
                    file_size,
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
                contents: bytes = await afc.get_file_contents(iphone_path)  # type: ignore[attr-defined]
                tmp_fh.write(contents)

            # Determine destination folder
            if forced_dest_folder is not None:
                dest_folder = forced_dest_folder
            else:
                capture_date = self._resolve_capture_date(tmp_path, afc_mtime)
                if capture_date is not None:
                    dest_folder = (
                        dest_root
                        / str(capture_date.year)
                        / f"{capture_date.year}-{capture_date.month:02d}-{capture_date.day:02d}"
                    )
                else:
                    dest_folder = dest_root / "undated"

            dest_folder.mkdir(parents=True, exist_ok=True)

            # Compare by content, not name: two phones can both produce IMG_1234.HEIC
            existing = checker.find_duplicate(tmp_path, dest_folder)
            if existing is not None:
                LOGGER.debug("Skipping %s — duplicate of %s", iphone_path, existing)
                return IPhoneFileResult(
                    iphone_path=iphone_path,
                    dest_path=existing,
                    status="already_exists",
                    is_sidecar=is_sidecar,
                )

            dest_file_path = self._resolve_dest_path(
                dest_folder, forced_dest_name or filename
            )

            # Copy then delete — Path.rename and shutil.move both fail cross-drive on Windows
            shutil.copy2(str(tmp_path), str(dest_file_path))
            tmp_path.unlink()
            tmp_path = None  # Successfully moved — don't delete in finally
            checker.register(dest_file_path)

            # Restore iPhone mtime on the copied file
            if afc_mtime is not None:
                mtime_s = afc_mtime.timestamp()
                os.utime(dest_file_path, (mtime_s, mtime_s))

            LOGGER.debug("Copied %s → %s", iphone_path, dest_file_path)
            return IPhoneFileResult(
                iphone_path=iphone_path,
                dest_path=dest_file_path,
                status="copied",
                is_sidecar=is_sidecar,
            )

        except Exception as exc:  # noqa: BLE001
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

    def _resolve_capture_date(
        self, local_path: Path, afc_mtime: datetime | None
    ) -> date | None:
        """Try EXIF first, then QuickTime (for .mov/.mp4), then AFC mtime, then local file mtime."""
        try:
            exif_data = ExifMetadataHandler.read_exif(str(local_path))
            dt_original = exif_data.get("datetime_original")
            if isinstance(dt_original, datetime):
                return dt_original.date()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("EXIF read failed for %s: %s", local_path.name, exc)

        if local_path.suffix.lower() in {".mov", ".mp4"}:
            try:
                qt_dt = read_quicktime_date(local_path)
                if qt_dt is not None:
                    return qt_dt.date()
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("QuickTime read failed for %s: %s", local_path.name, exc)

        if afc_mtime is not None:
            try:
                return afc_mtime.date()
            except Exception:  # noqa: BLE001, S110
                pass

        try:
            return datetime.fromtimestamp(local_path.stat().st_mtime, tz=UTC).date()
        except Exception:  # noqa: BLE001, S110
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
