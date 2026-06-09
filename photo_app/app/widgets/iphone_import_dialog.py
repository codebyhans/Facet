from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.widgets.import_dialog import ImportProgressWidget
from photo_app.app.workers.iphone_import_worker import IPhoneImportWorker
from photo_app.services.iphone_import_service import (
    IPhoneDevice,
    IPhoneImportService,
    IPhoneImportSummary,
)

if TYPE_CHECKING:
    from photo_app.services.face_index_service import FaceIndexService
    from photo_app.services.image_index_service import ImageIndexService

logger = logging.getLogger(__name__)


class IPhoneImportDialog(QDialog):
    """Dialog for importing photos and videos directly from an iPhone via AFC."""

    def __init__(
        self,
        image_index_service: ImageIndexService,
        face_index_service: FaceIndexService | None,
        default_dest_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._image_index_service = image_index_service
        self._face_index_service = face_index_service
        self._default_dest_path = default_dest_path
        self._thread_pool: QThreadPool | None = None
        self._is_running = False
        self._active_worker: IPhoneImportWorker | None = None
        self._devices: list[IPhoneDevice] = []
        self._import_service = IPhoneImportService()

        self.setWindowTitle("Import from iPhone")
        self.setMinimumSize(640, 560)

        self._setup_ui()
        self._wire_signals()
        self._detect_devices()

    def set_thread_pool(self, thread_pool: QThreadPool) -> None:
        self._thread_pool = thread_pool

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Prerequisites hint
        prereq_label = QLabel(
            "Prerequisites: iTunes (or Apple Mobile Device Support) must be installed, "
            "and your iPhone must be unlocked with ‘Trust This Computer’ accepted."
        )
        prereq_label.setWordWrap(True)
        prereq_label.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(prereq_label)

        # Device group
        device_group = QGroupBox("iPhone")
        device_layout = QHBoxLayout()

        self._device_combo = QComboBox()
        self._device_combo.setSizePolicy(
            self._device_combo.sizePolicy().horizontalPolicy(),  # type: ignore[arg-type]
            self._device_combo.sizePolicy().verticalPolicy(),  # type: ignore[arg-type]
        )
        self._device_combo.setMinimumWidth(300)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Re-scan for connected iPhones")

        device_layout.addWidget(self._device_combo, stretch=1)
        device_layout.addWidget(self._refresh_btn)
        device_group.setLayout(device_layout)

        # Destination group
        dest_group = QGroupBox("Import destination")
        dest_layout = QHBoxLayout()

        self._dest_line_edit = QLineEdit(str(self._default_dest_path))
        self._dest_browse_btn = QPushButton("Browse...")

        dest_layout.addWidget(self._dest_line_edit)
        dest_layout.addWidget(self._dest_browse_btn)
        dest_group.setLayout(dest_layout)

        dest_hint = QLabel("Files will be organised as YYYY/YYYY-MM-DD/filename")
        dest_hint.setStyleSheet("color: #888888; font-size: 11px;")

        # Options group
        options_group = QGroupBox("After import")
        options_layout = QVBoxLayout()

        self._index_checkbox = QCheckBox("Index imported files into library")
        self._index_checkbox.setChecked(True)

        self._face_detection_checkbox = QCheckBox("Run face detection on imported files")
        self._face_detection_checkbox.setChecked(False)

        options_layout.addWidget(self._index_checkbox)
        options_layout.addWidget(self._face_detection_checkbox)
        options_group.setLayout(options_layout)

        # Start button
        self._start_btn = QPushButton("Start import")
        self._start_btn.setEnabled(False)

        # Progress (hidden initially)
        self._progress_widget = ImportProgressWidget()
        self._progress_widget.hide()

        # Summary (hidden initially)
        self._summary_widget = IPhoneSummaryWidget()
        self._summary_widget.hide()

        # Dialog buttons
        self._button_box = QDialogButtonBox()
        self._close_btn = self._button_box.addButton(
            "Close", QDialogButtonBox.ButtonRole.RejectRole
        )
        self._close_btn.setEnabled(False)

        layout.addWidget(device_group)
        layout.addWidget(dest_group)
        layout.addWidget(dest_hint)
        layout.addWidget(options_group)
        layout.addWidget(self._start_btn)
        layout.addWidget(self._progress_widget)
        layout.addWidget(self._summary_widget)
        layout.addWidget(self._button_box)

        self._index_checkbox.toggled.connect(self._face_detection_checkbox.setEnabled)

    def _wire_signals(self) -> None:
        self._refresh_btn.clicked.connect(self._detect_devices)
        self._dest_browse_btn.clicked.connect(self._on_dest_browse)
        self._start_btn.clicked.connect(self._on_start_import)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        self._close_btn.clicked.connect(self.accept)

    # ------------------------------------------------------------------
    # Device detection
    # ------------------------------------------------------------------

    def _detect_devices(self) -> None:
        self._device_combo.clear()
        self._devices = self._import_service.list_devices()

        if not self._devices:
            self._device_combo.addItem("No iPhone detected — connect your iPhone and tap Trust")
            self._start_btn.setEnabled(False)
            self._close_btn.setEnabled(True)
            return

        for device in self._devices:
            self._device_combo.addItem(f"{device.name}  ({device.udid})")

        self._start_btn.setEnabled(True)
        self._close_btn.setEnabled(True)

    @Slot(int)
    def _on_device_changed(self, index: int) -> None:
        self._start_btn.setEnabled(index >= 0 and bool(self._devices))

    # ------------------------------------------------------------------
    # Destination
    # ------------------------------------------------------------------

    def _on_dest_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder", self._dest_line_edit.text()
        )
        if folder:
            self._dest_line_edit.setText(folder)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _on_start_import(self) -> None:
        idx = self._device_combo.currentIndex()
        if idx < 0 or idx >= len(self._devices):
            self._show_error("No iPhone selected.")
            return

        dest_path = Path(self._dest_line_edit.text())

        device = self._devices[idx]

        worker = IPhoneImportWorker(
            iphone_import_service=self._import_service,
            udid=device.udid,
            dest_path=dest_path,
            run_indexing=self._index_checkbox.isChecked(),
            run_face_detection=self._face_detection_checkbox.isChecked(),
            image_index_service=self._image_index_service,
            face_index_service=self._face_index_service,
        )

        worker.signals.progress.connect(self._progress_widget.on_progress)
        worker.signals.phase_changed.connect(self._progress_widget.on_phase_changed)
        worker.signals.finished.connect(self._on_import_finished)
        worker.signals.error.connect(self._on_import_error)
        worker.signals.done.connect(self._on_import_done)

        self._start_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._index_checkbox.setEnabled(False)
        self._face_detection_checkbox.setEnabled(False)
        self._progress_widget.show()
        self._summary_widget.hide()
        self._close_btn.setEnabled(False)
        self._is_running = True
        self._active_worker = worker

        if self._thread_pool is not None:
            self._thread_pool.start(worker)

    @Slot(object)
    def _on_import_finished(self, summary: IPhoneImportSummary) -> None:
        self._progress_widget.hide()
        self._summary_widget.show_summary(summary)

    @Slot(str)
    def _on_import_error(self, error_msg: str) -> None:
        self._progress_widget.hide()
        self._summary_widget.show_error(error_msg)

    @Slot()
    def _on_import_done(self) -> None:
        self._close_btn.setEnabled(True)
        self._is_running = False
        self._active_worker = None

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Error", message)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._is_running:
            event.ignore()
        else:
            super().closeEvent(event)


class IPhoneSummaryWidget(QWidget):
    """Summary panel shown after an iPhone import completes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        self._header_label = QLabel("Import complete")
        self._header_label.setStyleSheet("font-weight: bold; font-size: 16px;")

        self._stats_label = QLabel()

        self._dest_label = QLabel()
        self._dest_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        # Failed files section
        self._failed_group = QGroupBox("Failed files")
        failed_layout = QVBoxLayout()
        self._failed_text = QPlainTextEdit()
        self._failed_text.setReadOnly(True)
        self._failed_text.setMaximumBlockCount(2000)
        self._copy_failed_btn = QPushButton("Copy list to clipboard")
        failed_layout.addWidget(self._failed_text)
        failed_layout.addWidget(self._copy_failed_btn)
        self._failed_group.setLayout(failed_layout)
        self._failed_group.hide()

        # iCloud skipped section
        self._icloud_group = QGroupBox("Skipped — iCloud placeholders")
        icloud_layout = QVBoxLayout()
        icloud_hint = QLabel(
            "These files are stored in iCloud and not fully downloaded to your iPhone.\n"
            "To import them: open the Photos app → tap the photo → tap the download icon, "
            "then re-run this import."
        )
        icloud_hint.setWordWrap(True)
        icloud_hint.setStyleSheet("color: #ffcc00; font-size: 11px;")
        self._icloud_text = QPlainTextEdit()
        self._icloud_text.setReadOnly(True)
        self._icloud_text.setMaximumBlockCount(2000)
        self._copy_icloud_btn = QPushButton("Copy list to clipboard")
        icloud_layout.addWidget(icloud_hint)
        icloud_layout.addWidget(self._icloud_text)
        icloud_layout.addWidget(self._copy_icloud_btn)
        self._icloud_group.setLayout(icloud_layout)
        self._icloud_group.hide()

        layout.addWidget(self._header_label)
        layout.addWidget(self._stats_label)
        layout.addWidget(self._dest_label)
        layout.addWidget(self._failed_group)
        layout.addWidget(self._icloud_group)
        layout.addStretch()

    def show_summary(self, summary: IPhoneImportSummary) -> None:
        super().show()

        if summary.failed == 0:
            color = "#4caf50"
        elif summary.copied > 0:
            color = "#ff9800"
        else:
            color = "#f44336"

        self._header_label.setStyleSheet(
            f"font-weight: bold; font-size: 16px; color: {color};"
        )

        sidecars = sum(1 for r in summary.file_results if r.is_sidecar and r.status == "copied")
        images_copied = summary.copied - sidecars

        stats_parts = [
            f"{images_copied} photos/videos copied",
            f"{sidecars} Live Photo sidecars",
            f"{summary.failed} failed",
            f"{summary.skipped_icloud} iCloud placeholders skipped",
        ]
        self._stats_label.setText("  •  ".join(stats_parts))
        self._dest_label.setText(f"Files saved to: {summary.destination_path}")

        # Failed section
        failed_results = [r for r in summary.file_results if r.status == "failed"]
        if failed_results:
            self._failed_group.show()
            lines = [
                f"{r.iphone_path}\n  → {r.error or 'unknown error'}"
                for r in failed_results
            ]
            self._failed_text.setPlainText("\n\n".join(lines))
            self._copy_failed_btn.clicked.disconnect()
            self._copy_failed_btn.clicked.connect(
                lambda: self._copy_to_clipboard(
                    "\n".join(r.iphone_path for r in failed_results)
                )
            )
        else:
            self._failed_group.hide()

        # iCloud section
        skipped_results = [r for r in summary.file_results if r.status == "skipped"]
        if skipped_results:
            self._icloud_group.show()
            self._icloud_text.setPlainText(
                "\n".join(r.iphone_path for r in skipped_results)
            )
            self._copy_icloud_btn.clicked.disconnect()
            self._copy_icloud_btn.clicked.connect(
                lambda: self._copy_to_clipboard(
                    "\n".join(r.iphone_path for r in skipped_results)
                )
            )
        else:
            self._icloud_group.hide()

    def show_error(self, error_msg: str) -> None:
        super().show()
        self._header_label.setText("Import failed")
        self._header_label.setStyleSheet(
            "font-weight: bold; font-size: 16px; color: #f44336;"
        )
        self._stats_label.setText(f"Error: {error_msg}")
        self._dest_label.setText("")
        self._failed_group.hide()
        self._icloud_group.hide()

    def _copy_to_clipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)
