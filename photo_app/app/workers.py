from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    class SignalEmitter(Protocol):
        def emit(self, *args: object) -> None: ...


class WorkerSignals(QObject):
    """Signals emitted by background workers."""

    progress = Signal(int)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    """Cancelable runnable that reports progress and result."""

    def __init__(
        self,
        fn: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancelled = False
        self.signals = WorkerSignals()

    def cancel(self) -> None:
        """Request cancellation."""
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        """Run callback outside UI thread."""

        def safe_progress(value: int) -> None:
            self._safe_emit(self.signals.progress, value)

        try:
            if self._cancelled:
                return
            result = self._fn(
                *self._args,
                progress=safe_progress,
                cancelled=lambda: self._cancelled,
                **self._kwargs,
            )
            if not self._cancelled:
                self._safe_emit(self.signals.result, result)
        except Exception as exc:  # noqa: BLE001
            self._safe_emit(self.signals.error, str(exc))
        finally:
            self._safe_emit(self.signals.finished)

    def _safe_emit(self, signal: SignalEmitter, *args: object) -> None:
        """Emit Qt signal while tolerating object teardown races."""
        try:
            signal.emit(*args)
        except RuntimeError:
            return


def thumbnail_for_hash(thumbnail_root: Path, image_hash: str) -> Path:
    """Resolve thumbnail file path from hash."""
    return thumbnail_root / f"{image_hash}.webp"
