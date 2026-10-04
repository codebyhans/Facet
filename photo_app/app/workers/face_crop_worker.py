"""Background worker for rendering a high-resolution face crop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from photo_app.app.image_loading import render_face_crop_png

if TYPE_CHECKING:
    from pathlib import Path

    from photo_app.domain.value_objects import BoundingBox


class FaceCropWorkerSignals(QObject):
    """Signals for the FaceCropWorker."""

    result_ready = Signal(object)  # png_bytes | None


class FaceCropWorker(QRunnable):
    """Decode the original photo and crop around one face, off the main thread.

    Used by the People tab's hover preview: the card covers are built from
    small thumbnail tiles, so a sharp look at a small face needs the source
    photo — a decode that's too slow (and, for HEIC, impossible via QPixmap)
    to do on the UI thread.
    """

    def __init__(self, image_path: Path, bbox: BoundingBox) -> None:
        super().__init__()
        self._image_path = image_path
        self._bbox = bbox
        self.signals = FaceCropWorkerSignals()

    @Slot()
    def run(self) -> None:
        """Run the worker in a background thread."""
        self.signals.result_ready.emit(
            render_face_crop_png(self._image_path, self._bbox)
        )
