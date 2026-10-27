"""Background worker for decoding the image-detail panel's images."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from photo_app.app.image_loading import decode_oriented_image

if TYPE_CHECKING:
    from photo_app.services.face_review_service import (
        FaceReviewItem,
        FaceReviewService,
    )


class DetailImageWorkerSignals(QObject):
    """Signals for the DetailImageWorker."""

    # file_path, DecodedImage|None, faces, full_res, generation
    result_ready = Signal(str, object, object, bool, int)
    finished = Signal()


class DetailImageWorker(QRunnable):
    """Decode one detail-panel image, off the main thread.

    `QPixmap(path)` can't decode HEIC at all (and spends real time failing
    to before giving up), and even a successful full-resolution JPEG decode
    is enough to make clicking through the gallery feel like it's freezing —
    the same reasoning as `ImageInspectWorker`. Normally decodes to fit
    `max_size` (the screen), which is all "fit" view needs; `max_size=None`
    decodes full resolution, for zooming in past that. Used both for the
    shown image and for prefetching its neighbours. Also fetches this
    image's faces here, since that was previously a second synchronous DB
    round-trip on the same UI-thread click path.
    """

    def __init__(
        self,
        file_path: str,
        face_review_service: FaceReviewService | None,
        max_size: tuple[int, int] | None,
        generation: int,
    ) -> None:
        super().__init__()
        self._file_path = file_path
        self._face_review_service = face_review_service
        self._max_size = max_size
        self._generation = generation
        self.signals = DetailImageWorkerSignals()

    @Slot()
    def run(self) -> None:
        """Run the worker in a background thread."""
        try:
            decoded = decode_oriented_image(Path(self._file_path), self._max_size)

            faces: list[FaceReviewItem] = []
            if self._face_review_service is not None:
                try:
                    faces = self._face_review_service.faces_for_image_path(
                        self._file_path
                    )
                except Exception:  # noqa: BLE001
                    faces = []

            self.signals.result_ready.emit(
                self._file_path,
                decoded,
                faces,
                self._max_size is None,
                self._generation,
            )
        finally:
            self.signals.finished.emit()
