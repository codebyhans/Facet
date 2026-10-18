from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QRunnable, Signal
from PySide6.QtWidgets import QApplication

from photo_app.app.workers.album_worker import AlbumQueryWorker
from photo_app.app.workers.runner import running_worker_count, start_worker

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _wait_until(condition: object, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:  # type: ignore[operator]
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)


def test_dropped_workers_do_not_crash_and_still_deliver() -> None:
    """Workers whose caller dropped every reference mid-run must neither
    crash the process nor lose their result — the album-switch crash.

    Runs in a subprocess: the failure mode is a segfault, which would take
    the whole pytest run down instead of failing this test.
    """
    script = textwrap.dedent(
        """
        import gc, sys, time
        from PySide6.QtCore import QCoreApplication, QTimer
        from photo_app.app.workers.album_worker import AlbumQueryWorker
        from photo_app.app.workers.runner import start_worker

        app = QCoreApplication(sys.argv)
        got = []

        def slow():
            time.sleep(0.2)
            return 1

        def start_and_drop():
            for _ in range(20):
                worker = AlbumQueryWorker(slow)
                worker.signals.result_ready.connect(got.append)
                start_worker(worker)
                del worker
            gc.collect()

        QTimer.singleShot(0, start_and_drop)
        QTimer.singleShot(0, gc.collect)
        QTimer.singleShot(2500, app.quit)
        app.exec()
        sys.exit(0 if len(got) == 20 else 1)
        """
    )
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    for _ in range(3):
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_reference_released_after_finish() -> None:
    _app()
    before = running_worker_count()
    results: list[object] = []
    worker = AlbumQueryWorker(lambda: 42)
    worker.signals.result_ready.connect(results.append)
    start_worker(worker)
    del worker

    _wait_until(lambda: results and running_worker_count() == before)
    assert results == [42]
    assert running_worker_count() == before


class _DoneSignals(QObject):
    done = Signal()


class _DoneWorker(QRunnable):
    """Worker whose always-emitted exit signal is not called `finished`."""

    def __init__(self) -> None:
        super().__init__()
        self.signals = _DoneSignals()

    def run(self) -> None:
        self.signals.done.emit()


def test_custom_done_signal_releases_reference() -> None:
    _app()
    before = running_worker_count()
    worker = _DoneWorker()
    start_worker(worker, done=worker.signals.done)
    assert running_worker_count() == before + 1
    del worker

    _wait_until(lambda: running_worker_count() == before)
    assert running_worker_count() == before
