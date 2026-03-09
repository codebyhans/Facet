from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class AlbumWorkerSignals(QObject):
    """Signals for album query worker."""

    result_ready = Signal(object)
    error = Signal(str)
    progress = Signal(int)
    finished = Signal()


class AlbumQueryWorker(QRunnable):
    """Executes album query resolution outside the UI thread."""

    def __init__(self, fn: Any, *args: object, **kwargs: object) -> None:  # noqa: ANN401
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = AlbumWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit(10)
            result = self._fn(*self._args, **self._kwargs)
            self.signals.progress.emit(100)
            self.signals.result_ready.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()
