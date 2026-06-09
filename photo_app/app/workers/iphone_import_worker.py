from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QRunnable, Slot

from photo_app.app.workers.import_worker import ImportWorkerSignals

if TYPE_CHECKING:
    from pathlib import Path

    from photo_app.services.face_index_service import FaceIndexService
    from photo_app.services.image_index_service import ImageIndexService
    from photo_app.services.iphone_import_service import IPhoneImportService

logger = logging.getLogger(__name__)


class IPhoneImportWorker(QRunnable):
    """Background worker that imports media from an iPhone.

    Follows the same three-phase pattern as ImportWorker:
    1. Copy files from iPhone via AFC
    2. Index copied images into the library database
    3. Optionally run face detection
    """

    def __init__(
        self,
        iphone_import_service: IPhoneImportService,
        udid: str,
        dest_path: Path,
        run_indexing: bool,
        run_face_detection: bool,
        image_index_service: ImageIndexService | None,
        face_index_service: FaceIndexService | None,
    ) -> None:
        super().__init__()
        self._service = iphone_import_service
        self._udid = udid
        self._dest_path = dest_path
        self._run_indexing = run_indexing
        self._run_face_detection = run_face_detection
        self._image_index_service = image_index_service
        self._face_index_service = face_index_service
        self.signals = ImportWorkerSignals()

    def _safe_progress(self, current: int, total: int, filename: str) -> None:
        with contextlib.suppress(RuntimeError):
            self.signals.progress.emit(current, total, filename)

    def _index_progress(self, current: int, total: int) -> None:
        with contextlib.suppress(RuntimeError):
            self.signals.progress.emit(current, total, "")

    def _cluster_progress(self, current: int, total: int) -> None:
        with contextlib.suppress(RuntimeError):
            self.signals.progress.emit(current, total, "")

    @Slot()
    def run(self) -> None:
        """Run the three-phase iPhone import in a background thread."""
        logger.debug("IPhoneImportWorker.run() starting for device %s", self._udid)
        try:
            # Phase 1: Copy files from iPhone
            self.signals.phase_changed.emit("copying")
            summary = self._service.run_import(
                udid=self._udid,
                dest_path=self._dest_path,
                on_progress=self._safe_progress,
            )

            # Phase 2: Index images (skip sidecar MOVs — app doesn't support video yet)
            if self._run_indexing and self._image_index_service is not None:
                self.signals.phase_changed.emit("indexing")
                try:
                    image_paths = [
                        r.dest_path
                        for r in summary.file_results
                        if r.status == "copied"
                        and r.dest_path is not None
                        and not r.is_sidecar
                    ]
                    self._image_index_service.index_paths(
                        image_paths, on_progress=self._index_progress,
                    )
                except Exception as exc:
                    logger.exception("Indexing failed during iPhone import")
                    self.signals.error.emit(f"Indexing failed: {type(exc).__name__}: {exc}")
                    return

            # Phase 3: Face detection
            if self._run_face_detection and self._face_index_service is not None:
                self.signals.phase_changed.emit("face_detection")
                try:
                    self._face_index_service.index_faces(skip_clustering=True)

                    identity_cluster_service = (
                        self._face_index_service.get_identity_cluster_service()
                    )
                    if identity_cluster_service is not None:
                        identity_cluster_service.index_new_faces(
                            on_progress=self._cluster_progress,
                        )

                    query_cache_service = self._face_index_service.get_query_cache_service()
                    if query_cache_service is not None:
                        query_cache_service.invalidate_all()

                except Exception as exc:
                    logger.exception("Face detection failed during iPhone import")
                    self.signals.error.emit(
                        f"Face detection failed: {type(exc).__name__}: {exc}"
                    )
                    return

            self.signals.finished.emit(summary)

        except Exception as exc:
            logger.exception("Error in IPhoneImportWorker")
            self.signals.error.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.signals.done.emit()
