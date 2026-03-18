from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.workers.import_worker import ImportWorker
from photo_app.services.import_service import (
    ImportOptions,
    ImportService,
    ImportSummary,
)

if TYPE_CHECKING:
    from photo_app.services.face_index_service import FaceIndexService
    from photo_app.services.image_index_service import ImageIndexService

logger = logging.getLogger(__name__)


class ImportDialog(QDialog):
    """Dialog for importing files from camera to library."""

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
        self._thread_pool: QThreadPool | None = None  # Will be set by main window
        self._is_running = False

        self.setWindowTitle("Import from Camera")
        self.setMinimumSize(600, 500)

        self._setup_ui()
        self._wire_signals()

    def set_thread_pool(self, thread_pool: QThreadPool) -> None:
        """Set the thread pool for running import workers."""
        self._thread_pool = thread_pool

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Source group
        source_group = QGroupBox("Import source")
        source_layout = QHBoxLayout()

        self._source_line_edit = QLineEdit()
        self._source_line_edit.setReadOnly(True)
        self._source_line_edit.setPlaceholderText("Select source folder...")

        self._source_browse_btn = QPushButton("Browse...")

        source_layout.addWidget(self._source_line_edit)
        source_layout.addWidget(self._source_browse_btn)
        source_group.setLayout(source_layout)

        # Destination group
        dest_group = QGroupBox("Import destination")
        dest_layout = QHBoxLayout()

        self._dest_line_edit = QLineEdit(str(self._default_dest_path))
        self._dest_browse_btn = QPushButton("Browse...")
        dest_info_label = QLabel("Files will be organised as YYYY/YYYY-MM-DD/filename")
        dest_info_label.setStyleSheet("color: #888888; font-size: 11px;")

        dest_layout.addWidget(self._dest_line_edit)
        dest_layout.addWidget(self._dest_browse_btn)
        dest_group.setLayout(dest_layout)

        # Options group
        options_group = QGroupBox("After import")
        options_layout = QVBoxLayout()

        self._index_checkbox = QCheckBox("Index imported files into library")
        self._index_checkbox.setChecked(True)

        self._face_detection_checkbox = QCheckBox(
            "Run face detection on imported files"
        )
        self._face_detection_checkbox.setChecked(False)

        options_layout.addWidget(self._index_checkbox)
        options_layout.addWidget(self._face_detection_checkbox)
        options_group.setLayout(options_layout)

        # Start button
        self._start_btn = QPushButton("Start import")
        self._start_btn.setEnabled(False)

        # Progress widget (hidden initially)
        self._progress_widget = ImportProgressWidget()
        self._progress_widget.hide()

        # Summary widget (hidden initially)
        self._summary_widget = ImportSummaryWidget()
        self._summary_widget.hide()

        # Dialog buttons
        self._button_box = QDialogButtonBox()
        self._close_btn = self._button_box.addButton(
            "Close", QDialogButtonBox.ButtonRole.RejectRole
        )
        self._close_btn.setEnabled(False)

        # Add all widgets to main layout
        layout.addWidget(source_group)
        layout.addWidget(dest_group)
        layout.addWidget(dest_info_label)
        layout.addWidget(options_group)
        layout.addWidget(self._start_btn)
        layout.addWidget(self._progress_widget)
        layout.addWidget(self._summary_widget)
        layout.addWidget(self._button_box)

        # Connect checkbox signals for enabling/disabling face detection
        self._index_checkbox.toggled.connect(self._face_detection_checkbox.setEnabled)

    def _wire_signals(self) -> None:
        """Connect UI signals."""
        self._source_browse_btn.clicked.connect(self._on_source_browse)
        self._dest_browse_btn.clicked.connect(self._on_dest_browse)
        self._start_btn.clicked.connect(self._on_start_import)
        self._close_btn.clicked.connect(self.accept)

    def _on_source_browse(self) -> None:
        """Handle source folder selection."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Source Folder",
            str(Path.home()),
        )
        if folder:
            self._source_line_edit.setText(folder)
            self._start_btn.setEnabled(bool(folder))

    def _on_dest_browse(self) -> None:
        """Handle destination folder selection."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Destination Folder",
            self._dest_line_edit.text(),
        )
        if folder:
            self._dest_line_edit.setText(folder)

    def _on_start_import(self) -> None:
        """Handle start import button click."""
        source_path = Path(self._source_line_edit.text())
        dest_path = Path(self._dest_line_edit.text())

        if not source_path.exists():
            self._show_error("Source folder does not exist")
            return

        if not source_path.is_dir():
            self._show_error("Source path is not a directory")
            return

        # Create import options
        options = ImportOptions(
            source_path=source_path,
            destination_path=dest_path,
            run_indexing=self._index_checkbox.isChecked(),
            run_face_detection=self._face_detection_checkbox.isChecked(),
        )

        # Create import service and worker
        import_service = ImportService()

        worker = ImportWorker(
            import_service=import_service,
            options=options,
            image_index_service=self._image_index_service,
            face_index_service=self._face_index_service,
        )

        # Connect worker signals
        worker.signals.progress.connect(self._progress_widget.on_progress)
        worker.signals.phase_changed.connect(self._progress_widget.on_phase_changed)
        worker.signals.finished.connect(self._on_import_finished)
        worker.signals.error.connect(self._on_import_error)
        worker.signals.done.connect(self._on_import_done)

        # Show progress widget, hide start button
        self._start_btn.setEnabled(False)
        self._index_checkbox.setEnabled(False)
        self._face_detection_checkbox.setEnabled(False)
        self._progress_widget.show()
        self._summary_widget.hide()
        self._close_btn.setEnabled(False)

        # Start worker
        if self._thread_pool is not None:
            self._thread_pool.start(worker)

    def _on_import_finished(self, summary: ImportSummary) -> None:
        """Handle import completion."""
        self._progress_widget.hide()
        self._summary_widget.show_summary(summary)
        self._close_btn.setEnabled(True)

    def _on_import_error(self, error_msg: str) -> None:
        """Handle import error."""
        self._progress_widget.hide()
        self._summary_widget.show_error(error_msg)
        self._close_btn.setEnabled(True)

    def _on_import_done(self) -> None:
        """Always called when the worker exits — guarantees Close is enabled."""
        self._close_btn.setEnabled(True)
        self._is_running = False

    def _show_error(self, message: str) -> None:
        """Show error message."""
        QMessageBox.critical(self, "Error", message)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle dialog close event."""
        # If import is running, ignore close
        if self._progress_widget.isVisible():
            event.ignore()
        else:
            super().closeEvent(event)


class ImportProgressWidget(QWidget):
    """Widget showing import progress."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # Phase label
        self._phase_label = QLabel("Copying files...")
        self._phase_label.setStyleSheet("font-weight: bold; font-size: 14px;")

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)

        # Log area
        self._log_area = QPlainTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumBlockCount(1000)  # Limit log size
        self._log_area.setWordWrapMode(QTextOption.WrapMode.NoWrap)

        layout.addWidget(self._phase_label)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._log_area)

    @Slot(int, int, str)
    def on_progress(self, current: int, total: int, filename: str) -> None:
        """Handle progress update."""
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)

        # Add log entry
        log_text = f"[{current:3d}/{total:3d}] {filename}"
        self._log_area.appendPlainText(log_text)
        # Auto-scroll to bottom
        scrollbar = self._log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @Slot(str)
    def on_phase_changed(self, phase: str) -> None:
        """Handle phase change."""
        phase_text = {
            "copying": "Copying files...",
            "indexing": "Indexing imported files...",
            "face_detection": "Running face detection...",
        }.get(phase, phase)
        self._phase_label.setText(phase_text)


class ImportSummaryWidget(QWidget):
    """Widget showing import summary."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # Header label
        self._header_label = QLabel("Import complete")
        self._header_label.setStyleSheet("font-weight: bold; font-size: 16px;")

        # Stats line
        self._stats_label = QLabel()

        # Destination line
        self._dest_label = QLabel()
        self._dest_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        # Unhandled section
        self._unhandled_group = QGroupBox("Unhandled files")
        self._unhandled_layout = QVBoxLayout()

        self._unhandled_label = QLabel()
        self._unhandled_label.setStyleSheet("font-weight: bold; color: #ffcc00;")

        self._unhandled_text = QPlainTextEdit()
        self._unhandled_text.setReadOnly(True)
        self._unhandled_text.setMaximumBlockCount(1000)

        self._copy_paths_btn = QPushButton("Copy path list to clipboard")

        self._unhandled_layout.addWidget(self._unhandled_label)
        self._unhandled_layout.addWidget(self._unhandled_text)
        self._unhandled_layout.addWidget(self._copy_paths_btn)
        self._unhandled_group.setLayout(self._unhandled_layout)
        self._unhandled_group.hide()

        layout.addWidget(self._header_label)
        layout.addWidget(self._stats_label)
        layout.addWidget(self._dest_label)
        layout.addWidget(self._unhandled_group)
        layout.addStretch()

    def show_summary(self, summary: ImportSummary) -> None:
        """Show summary with results."""
        super().show()

        # Set header color based on success
        if summary.failed == 0:
            self._header_label.setStyleSheet(
                "font-weight: bold; font-size: 16px; color: #4caf50;"
            )
        else:
            self._header_label.setStyleSheet(
                "font-weight: bold; font-size: 16px; color: #ff9800;"
            )

        # Set stats
        stats_text = (
            f"{summary.copied} files copied  •  "
            f"{summary.failed} failed  •  "
            f"{summary.no_capture_date_count} without capture date"
        )
        self._stats_label.setText(stats_text)

        # Set destination
        self._dest_label.setText(f"Files saved to: {summary.destination_path}")

        # Set unhandled paths
        if summary.failed > 0 or summary.no_capture_date_count > 0:
            self._unhandled_group.show()
            unhandled_count = len(summary.unhandled_paths)
            self._unhandled_label.setText(
                f"{unhandled_count} files could not be fully handled:"
            )

            paths_text = "\n".join(str(path) for path in summary.unhandled_paths)
            self._unhandled_text.setPlainText(paths_text)

            # Connect copy button
            self._copy_paths_btn.clicked.disconnect()
            self._copy_paths_btn.clicked.connect(
                lambda: self._copy_paths_to_clipboard(summary.unhandled_paths)
            )
        else:
            self._unhandled_group.hide()

    def show_error(self, error_msg: str) -> None:
        """Show error instead of summary."""
        super().show()

        self._header_label.setText("Import failed")
        self._header_label.setStyleSheet(
            "font-weight: bold; font-size: 16px; color: #f44336;"
        )
        self._stats_label.setText(f"Error: {error_msg}")
        self._dest_label.setText("")
        self._unhandled_group.hide()

    def _copy_paths_to_clipboard(self, paths: list[Path]) -> None:
        """Copy paths to clipboard."""
        clipboard = QApplication.clipboard()
        paths_text = "\n".join(str(path) for path in paths)
        clipboard.setText(paths_text)
