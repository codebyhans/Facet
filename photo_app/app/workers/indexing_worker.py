from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class IndexWorkerSignals(QObject):
    """Signals for indexing operations."""

    result_ready = Signal(object)
    error = Signal(str)
    progress = Signal(int)  # 0-100 for overall progress
    progress_detailed = Signal(int, int)  # (current, total) for detailed progress
    finished = Signal()


class IndexWorker(QRunnable):
    """Generic background worker for indexing tasks."""

    def __init__(
        self,
        fn: Any,  # noqa: ANN401
        *args: object,
        progress_callback: Callable[[int, int], None] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._progress_callback = progress_callback
        self.signals = IndexWorkerSignals()

        # Connect detailed progress signal to the callback if provided
        if progress_callback:
            self.signals.progress_detailed.connect(
                progress_callback
            )

    @Slot()
    def run(self) -> None:
        """Run the indexing task in background thread."""
        logger.debug("IndexWorker.run() starting for %s", self._fn.__name__)
        try:
            self.signals.progress.emit(5)
            logger.debug("Emitted initial progress signal")

            # Create a wrapper for on_progress that safely emits signals
            def safe_progress_callback(current: int, total: int) -> None:
                """Safely emit progress signal from worker thread."""
                try:
                    logger.debug("Progress: %s/%s", current, total)
                    # This signal is connected to a lambda in __init__ that calls
                    # the user-provided callback, so just emit the signal
                    self.signals.progress_detailed.emit(current, total)
                except RuntimeError as e:
                    # Qt object might be destroyed, ignore
                    logger.warning("Couldn't emit progress signal: %s", e)
                except Exception:
                    logger.exception("Unexpected error in progress callback")

            # Check if the function accepts on_progress parameter
            fn_code = getattr(self._fn, "__code__", None)
            if fn_code and "on_progress" in fn_code.co_varnames:
                logger.debug("Function accepts on_progress, adding callback")
                self._kwargs["on_progress"] = safe_progress_callback

            logger.debug("Calling %s with args=%s, kwargs=%s", self._fn.__name__, self._args, self._kwargs)
            result = self._fn(*self._args, **self._kwargs)
            logger.debug("Function completed successfully, result=%s", result)

            self.signals.progress.emit(100)
            self.signals.result_ready.emit(result)
        except Exception as exc:
            logger.exception("Error in IndexWorker")
            error_msg = f"{type(exc).__name__}: {exc}"
            self.signals.error.emit(error_msg)
        finally:
            logger.debug("IndexWorker.run() finished")
            self.signals.finished.emit()
