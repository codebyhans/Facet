from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.workers.chromecast_worker import ChromecastDiscoveryWorker
from photo_app.app.workers.runner import start_worker

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent

    from photo_app.services.chromecast_service import ChromecastService

LOGGER = logging.getLogger(__name__)


class ChromecastSlideshowDialog(QDialog):
    """Two-phase dialog: Chromecast picker → slideshow remote control."""

    def __init__(  # noqa: PLR0915
        self,
        service: ChromecastService,
        image_paths: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cast Slideshow to TV")
        self.setMinimumWidth(420)
        self._service = service
        self._image_paths = image_paths
        self._chromecasts: list[object] = []
        self._paused = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)

        layout = QVBoxLayout(self)

        # Phase 1 — discovery
        self._discovery_label = QLabel(
            "Searching for Chromecast devices on your network..."
        )
        layout.addWidget(self._discovery_label)

        self._cast_list = QListWidget()
        self._cast_list.setMaximumHeight(160)
        layout.addWidget(self._cast_list)

        self._connect_btn = QPushButton("Connect and Start Slideshow")
        self._connect_btn.setEnabled(False)
        self._connect_btn.clicked.connect(self._on_connect)
        layout.addWidget(self._connect_btn)

        # Phase 2 — controls (hidden until connected)
        self._controls_widget = QWidget()
        controls_layout = QVBoxLayout(self._controls_widget)
        controls_layout.setContentsMargins(0, 8, 0, 0)

        self._progress_label = QLabel("")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        controls_layout.addWidget(self._progress_label)

        self._filename_label = QLabel("")
        self._filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._filename_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        controls_layout.addWidget(self._filename_label)

        buttons_row = QHBoxLayout()
        self._prev_btn = QPushButton("⏮")
        self._prev_btn.setToolTip("Previous")
        self._prev_btn.clicked.connect(self._on_prev)
        self._play_pause_btn = QPushButton("⏸")
        self._play_pause_btn.setToolTip("Pause")
        self._play_pause_btn.clicked.connect(self._on_play_pause)
        self._next_btn = QPushButton("⏭")
        self._next_btn.setToolTip("Next")
        self._next_btn.clicked.connect(self._on_next)
        self._stop_btn = QPushButton("⏹ Stop & Close")
        self._stop_btn.clicked.connect(self._on_stop)
        for btn in (self._prev_btn, self._play_pause_btn, self._next_btn):
            btn.setFixedWidth(48)
        buttons_row.addWidget(self._prev_btn)
        buttons_row.addWidget(self._play_pause_btn)
        buttons_row.addWidget(self._next_btn)
        buttons_row.addStretch()
        buttons_row.addWidget(self._stop_btn)
        controls_layout.addLayout(buttons_row)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Auto-advance every"))
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(2, 120)
        self._interval_spin.setValue(5)
        self._interval_spin.setSuffix(" s")
        self._interval_spin.valueChanged.connect(self._on_interval_changed)
        interval_row.addWidget(self._interval_spin)
        interval_row.addStretch()
        controls_layout.addLayout(interval_row)

        self._controls_widget.setVisible(False)
        layout.addWidget(self._controls_widget)

        self._start_discovery()

    def _start_discovery(self) -> None:
        worker = ChromecastDiscoveryWorker(self._service)
        worker.signals.found.connect(self._on_chromecasts_found)
        worker.signals.error.connect(self._on_discovery_error)
        start_worker(worker)

    def _on_chromecasts_found(self, casts: list[object]) -> None:
        self._chromecasts = casts
        self._cast_list.clear()
        if not casts:
            self._discovery_label.setText(
                "No Chromecast devices found. Is the Chromecast on the same Wi-Fi?"
            )
            return
        self._discovery_label.setText(f"Found {len(casts)} device(s) — select one:")
        for cast in casts:
            name = getattr(cast, "name", str(cast))
            self._cast_list.addItem(QListWidgetItem(name))
        self._cast_list.setCurrentRow(0)
        self._connect_btn.setEnabled(True)

    def _on_discovery_error(self, msg: str) -> None:
        self._discovery_label.setText(f"Discovery failed: {msg}")

    def _on_connect(self) -> None:
        row = self._cast_list.currentRow()
        if row < 0 or row >= len(self._chromecasts):
            return
        cast = self._chromecasts[row]
        self._connect_btn.setEnabled(False)
        self._discovery_label.setText(f"Connecting to {getattr(cast, 'name', cast)}...")
        try:
            self._service.connect(cast)
        except Exception as exc:
            LOGGER.exception("Failed to connect to Chromecast")
            self._discovery_label.setText(f"Connection failed: {exc}")
            self._connect_btn.setEnabled(True)
            return
        self._service.start_slideshow(self._image_paths)
        if self._service.total == 0:
            self._discovery_label.setText("No images to cast in this album.")
            return
        self._cast_list.setVisible(False)
        self._connect_btn.setVisible(False)
        self._controls_widget.setVisible(True)
        self._update_labels()
        self._timer.start(self._interval_spin.value() * 1000)

    def _update_labels(self) -> None:
        idx = self._service.current_index
        total = self._service.total
        self._progress_label.setText(f"{idx + 1} / {total}")
        path = self._service.current_path
        if path:
            self._filename_label.setText(Path(path).name)

    def _on_timer(self) -> None:
        self._service.next()
        self._update_labels()

    def _on_prev(self) -> None:
        self._service.prev()
        self._update_labels()
        if self._timer.isActive():
            self._timer.start(self._interval_spin.value() * 1000)

    def _on_next(self) -> None:
        self._service.next()
        self._update_labels()
        if self._timer.isActive():
            self._timer.start(self._interval_spin.value() * 1000)

    def _on_play_pause(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._play_pause_btn.setText("▶")
            self._play_pause_btn.setToolTip("Play")
        else:
            self._timer.start(self._interval_spin.value() * 1000)
            self._play_pause_btn.setText("⏸")
            self._play_pause_btn.setToolTip("Pause")

    def _on_interval_changed(self, value: int) -> None:
        if self._timer.isActive():
            self._timer.start(value * 1000)

    def _on_stop(self) -> None:
        self._timer.stop()
        self._service.stop()
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._timer.stop()
        if self._service.is_connected:
            self._service.stop()
        super().closeEvent(event)
