from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

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
    ) -> None:  # noqa: ANN401
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._progress_callback = progress_callback
        self.signals = IndexWorkerSignals()
        
        # Connect detailed progress signal to the callback if provided
        if progress_callback:
            self.signals.progress_detailed.connect(
                lambda current, total: progress_callback(current, total)
            )

    @Slot()
    def run(self) -> None:
        """Run the indexing task in background thread."""
        logger.debug(f"IndexWorker.run() starting for {self._fn.__name__}")
        try:
            self.signals.progress.emit(5)
            logger.debug("Emitted initial progress signal")
            
            # Create a wrapper for on_progress that safely emits signals
            def safe_progress_callback(current: int, total: int) -> None:
                """Safely emit progress signal from worker thread."""
                try:
                    logger.debug(f"Progress: {current}/{total}")
                    # This signal is connected to a lambda in __init__ that calls
                    # the user-provided callback, so just emit the signal
                    self.signals.progress_detailed.emit(current, total)
                except RuntimeError as e:
                    # Qt object might be destroyed, ignore
                    logger.warning(f"Couldn't emit progress signal: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error in progress callback: {e}")
            
            # Check if the function accepts on_progress parameter
            fn_code = getattr(self._fn, '__code__', None)
            if fn_code and 'on_progress' in fn_code.co_varnames:
                logger.debug("Function accepts on_progress, adding callback")
                self._kwargs['on_progress'] = safe_progress_callback
            
            logger.debug(f"Calling {self._fn.__name__} with args={self._args}, kwargs={self._kwargs}")
            result = self._fn(*self._args, **self._kwargs)
            logger.debug(f"Function completed successfully, result={result}")
            
            self.signals.progress.emit(100)
            self.signals.result_ready.emit(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Error in IndexWorker: {exc}")
            error_msg = f"{type(exc).__name__}: {exc}"
            self.signals.error.emit(error_msg)
        finally:
            logger.debug("IndexWorker.run() finished")
            self.signals.finished.emit()

