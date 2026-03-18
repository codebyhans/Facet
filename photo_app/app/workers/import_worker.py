from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

if TYPE_CHECKING:
    from photo_app.services.face_index_service import FaceIndexService
    from photo_app.services.image_index_service import ImageIndexService
    from photo_app.services.import_service import ImportOptions, ImportService

logger = logging.getLogger(__name__)


class ImportWorkerSignals(QObject):
    """Signals for import operations."""

    progress = Signal(int, int, str)  # current, total, filename
    file_done = Signal(object)  # ImportFileResult
    phase_changed = Signal(str)  # "copying" | "indexing" | "face_detection"
    finished = Signal(object)  # ImportSummary
    error = Signal(str)
    done = Signal()  # add this — always emitted when run() exits


class ImportWorker(QRunnable):
    """Background worker for import tasks."""

    def __init__(
        self,
        import_service: ImportService,
        options: ImportOptions,
        image_index_service: ImageIndexService | None,
        face_index_service: FaceIndexService | None,
    ) -> None:
        super().__init__()
        self._import_service = import_service
        self._options = options
        self._image_index_service = image_index_service
        self._face_index_service = face_index_service
        self.signals = ImportWorkerSignals()

    @Slot()
    def run(self) -> None:
        """Run the import task in background thread."""
        logger.debug("ImportWorker.run() starting")
        try:
            # Phase 1: Copy files
            self.signals.phase_changed.emit("copying")

            def safe_progress_callback(current: int, total: int, filename: str) -> None:
                """Safely emit progress signal from worker thread."""
                try:
                    logger.debug("Progress: %s/%s - %s", current, total, filename)
                    self.signals.progress.emit(current, total, filename)
                except RuntimeError as e:
                    # Qt object might be destroyed, ignore
                    logger.warning("Couldn't emit progress signal: %s", e)
                except Exception:
                    logger.exception("Unexpected error in progress callback")

            summary = self._import_service.run_import(
                self._options,
                on_progress=safe_progress_callback,
            )

            # Phase 2: Index imported files
            if self._options.run_indexing and self._image_index_service is not None:
                self.signals.phase_changed.emit("indexing")
                try:
                    self._image_index_service.index_folder(
                        self._options.destination_path
                    )
                except Exception as exc:
                    logger.exception("Indexing failed during import")
                    error_msg = f"Indexing failed: {type(exc).__name__}: {exc}"
                    self.signals.error.emit(error_msg)
                    return

            # Phase 3: Run face detection
            if (
                self._options.run_face_detection
                and self._face_index_service is not None
            ):
                self.signals.phase_changed.emit("face_detection")
                try:
                    self._face_index_service.index_faces()
                except Exception as exc:
                    logger.exception("Face detection failed during import")
                    error_msg = f"Face detection failed: {type(exc).__name__}: {exc}"
                    self.signals.error.emit(error_msg)
                    return

            self.signals.finished.emit(summary)

        except Exception as exc:
            logger.exception("Error in ImportWorker")
            error_msg = f"{type(exc).__name__}: {exc}"
            self.signals.error.emit(error_msg)
        finally:
            self.signals.done.emit()
