"""Background worker for decoding the image-detail panel's full-size image."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from photo_app.app.image_loading import render_oriented_png

if TYPE_CHECKING:
    from photo_app.services.face_review_service import (
        FaceReviewItem,
        FaceReviewService,
    )


class DetailImageWorkerSignals(QObject):
    """Signals for the DetailImageWorker."""

    # file_path, png_bytes|None, faces, request_id
    result_ready = Signal(str, object, object, int)
    finished = Signal()


class DetailImageWorker(QRunnable):
    """Decode one full-resolution detail-panel image, off the main thread.

    `QPixmap(path)` can't decode HEIC at all (and spends real time failing
    to before giving up), and even a successful full-resolution JPEG decode
    is enough to make clicking through the gallery feel like it's freezing —
    the same reasoning as `ImageInspectWorker`, but this view needs the true
    full-resolution image (not a size-capped decode) since it supports
    zooming past "fit". Also fetches this image's faces here, since that was
    previously a second synchronous DB round-trip on the same UI-thread
    click path.
    """

    def __init__(
        self,
        file_path: str,
        face_review_service: FaceReviewService | None,
        request_id: int,
    ) -> None:
        super().__init__()
        self._file_path = file_path
        self._face_review_service = face_review_service
        self._request_id = request_id
        self.signals = DetailImageWorkerSignals()

    @Slot()
    def run(self) -> None:
        """Run the worker in a background thread."""
        try:
            png_bytes = render_oriented_png(Path(self._file_path))

            faces: list[FaceReviewItem] = []
            if self._face_review_service is not None:
                try:
                    faces = self._face_review_service.faces_for_image_path(
                        self._file_path
                    )
                except Exception:  # noqa: BLE001
                    faces = []

            self.signals.result_ready.emit(
                self._file_path, png_bytes, faces, self._request_id
            )
        finally:
            self.signals.finished.emit()
