"""Start background workers without letting Python collect them mid-run.

A QRunnable's `signals` QObject lives only as long as the Python worker
object. If the last reference is dropped while the worker thread is still
running (a single `_active_worker` slot overwritten by the next request, a
dialog that closes, or no reference kept at all), the signals object is
destroyed under the thread and the next `emit()` crashes the process.

`start_worker` keeps every worker in a module-level set until its completion
signal fires, so callers never need their own keep-alive references.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QThreadPool

if TYPE_CHECKING:
    from PySide6.QtCore import QRunnable, SignalInstance

_running: set[QRunnable] = set()


def start_worker(
    worker: QRunnable,
    pool: QThreadPool | None = None,
    *,
    done: SignalInstance | None = None,
) -> None:
    """Run `worker` on `pool`, holding a reference until it has finished.

    `done` is the signal the worker always emits when `run()` exits; it
    defaults to `worker.signals.finished`.
    """
    if done is None:
        done = worker.signals.finished  # type: ignore[attr-defined]
    _running.add(worker)
    done.connect(lambda *_: _running.discard(worker))
    (pool or QThreadPool.globalInstance()).start(worker)


def running_worker_count() -> int:
    """Number of workers started here that have not finished yet."""
    return len(_running)
