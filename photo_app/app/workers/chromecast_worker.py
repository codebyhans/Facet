from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

if TYPE_CHECKING:
    from photo_app.services.chromecast_service import ChromecastService

LOGGER = logging.getLogger(__name__)


class _ChromecastDiscoverySignals(QObject):
    found = Signal(list)
    error = Signal(str)


class ChromecastDiscoveryWorker(QRunnable):
    """Background worker that discovers Chromecasts on the LAN."""

    def __init__(self, service: ChromecastService, timeout: float = 8.0) -> None:
        super().__init__()
        self._service = service
        self._timeout = timeout
        self.signals = _ChromecastDiscoverySignals()

    @Slot()
    def run(self) -> None:
        try:
            casts = self._service.discover(timeout=self._timeout)
            self.signals.found.emit(casts)
        except Exception as exc:
            LOGGER.exception("Chromecast discovery failed")
            self.signals.error.emit(str(exc))
