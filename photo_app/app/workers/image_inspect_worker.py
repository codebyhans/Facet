"""Background worker for decoding the cluster inspector's full-size image."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from photo_app.app.image_loading import render_fit_to_size_png


class ImageInspectWorkerSignals(QObject):
    """Signals for the ImageInspectWorker."""

    result_ready = Signal(
        str, object, float, int
    )  # file_path, png_bytes|None, scale, request_id
    finished = Signal()


class ImageInspectWorker(QRunnable):
    """Decode+scale one inspector image off the main thread.

    `QPixmap(path)` can't decode HEIC at all (and spends real time failing
    to before giving up), and even for formats it can decode, doing a
    full-resolution JPEG decode synchronously on the UI thread is enough to
    make clicking through a cluster feel like it's freezing. This runs that
    work on a thread-pool thread instead, via PIL.
    """

    def __init__(
        self, file_path: str, max_size: tuple[int, int], request_id: int
    ) -> None:
        super().__init__()
        self._file_path = file_path
        self._max_size = max_size
        self._request_id = request_id
        self.signals = ImageInspectWorkerSignals()

    @Slot()
    def run(self) -> None:
        """Run the worker in a background thread."""
        try:
            result = render_fit_to_size_png(Path(self._file_path), self._max_size)
            if result is None:
                self.signals.result_ready.emit(
                    self._file_path, None, 1.0, self._request_id
                )
            else:
                png_bytes, scale = result
                self.signals.result_ready.emit(
                    self._file_path, png_bytes, scale, self._request_id
                )
        finally:
            self.signals.finished.emit()
