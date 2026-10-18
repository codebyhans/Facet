"""Image inspector widget for cluster detail view."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.widgets.face_detection_widget import FaceDetectionWidget
from photo_app.app.workers.image_inspect_worker import ImageInspectWorker
from photo_app.app.workers.runner import start_worker

if TYPE_CHECKING:
    from collections.abc import Callable

    from photo_app.services.face_review_service import FaceReviewItem

_MIN_INSPECT_WIDTH = 400
_MIN_INSPECT_HEIGHT = 300


class ClusterImageInspectorWidget(QWidget):
    """Right-hand image inspector for the cluster detail view.

    Shows a selected image at fit-to-panel size with optional bbox overlay,
    and hosts in-place face naming (click a face, or "Name faces").
    Receives face data from the caller — does not query the DB itself; name
    edits are shown immediately and emitted for the caller to persist.
    """

    face_delete_requested = Signal(int)  # face_id
    face_reassign_requested = Signal(int, str)  # face_id, new_person_name
    faces_changed = Signal()  # Emitted once when a naming session with edits ends

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_file_path: str | None = None
        self._faces: list[FaceReviewItem] = []
        self._thread_pool = QThreadPool.globalInstance()
        self._request_epoch = 0
        self._faces_dirty = False

        # Face detection widget
        self._face_detection_widget = FaceDetectionWidget()
        self._face_detection_widget.setStyleSheet(
            "background-color: #1e1e1e; color: white;"
        )
        self._face_detection_widget.face_name_committed.connect(
            self._on_face_name_committed
        )
        self._face_detection_widget.face_ignore_requested.connect(self._on_face_ignored)
        self._face_detection_widget.naming_mode_changed.connect(
            self._on_naming_mode_changed
        )

        # Scroll area for the face detection widget
        scroll_area = QScrollArea()
        scroll_area.setWidget(self._face_detection_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet(
            "QScrollArea { border: none; background-color: #1e1e1e; }"
        )

        # Toolbar
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)

        self._faces_checkbox = QCheckBox("Faces")
        self._faces_checkbox.setStyleSheet("color: #888888; font-size: 11px;")
        self._faces_checkbox.stateChanged.connect(self._on_faces_toggled)
        toolbar_layout.addWidget(self._faces_checkbox)

        # Spacer
        toolbar_layout.addStretch()

        self._name_faces_btn = QPushButton("Name faces")
        self._name_faces_btn.setToolTip(
            "Name each face in turn: type a name and press Enter to move on.\n"
            "Tip: click any face on the photo to name or fix just that one."
        )
        self._name_faces_btn.clicked.connect(self.start_naming)
        toolbar_layout.addWidget(self._name_faces_btn)

        # Main layout
        layout = QVBoxLayout(self)
        layout.addWidget(scroll_area, 1)
        layout.addLayout(toolbar_layout)
        layout.setContentsMargins(0, 0, 0, 0)

        self.setStyleSheet("background-color: #1e1e1e;")

        # Initially show placeholder
        self.show_placeholder()

    def show_placeholder(self) -> None:
        """Show 'Select an image to inspect' before any selection."""
        self._face_detection_widget.setText("Select an image to inspect")
        self._current_file_path = None
        self._faces = []
        self._update_face_action_visibility()

    def load_image(self, file_path: str, faces: list[FaceReviewItem]) -> None:
        """Display image and its annotated faces.

        file_path  — original full-resolution file (not the thumbnail)
        faces      — from FaceReviewService.faces_for_image_path()

        Decoding runs on a thread-pool worker rather than the UI thread:
        `QPixmap(path)` can't decode HEIC at all (it spends real time probing
        formats before giving up), and even a successful full-resolution
        JPEG decode is enough to make clicking through a cluster feel like
        the app is freezing.
        """
        self._face_detection_widget.stop_naming()
        self._current_file_path = file_path
        self._faces = faces
        self._update_face_action_visibility()

        self._face_detection_widget.setText("Loading image…")

        viewport_width = max(self.width(), _MIN_INSPECT_WIDTH)
        viewport_height = max(self.height(), _MIN_INSPECT_HEIGHT)

        self._request_epoch += 1
        worker = ImageInspectWorker(
            file_path, (viewport_width, viewport_height), self._request_epoch
        )
        worker.signals.result_ready.connect(self._on_inspect_result)
        start_worker(worker, self._thread_pool)

    def _on_inspect_result(
        self, file_path: str, png_bytes: bytes | None, scale: float, request_id: int
    ) -> None:
        """Apply a decoded inspector image, discarding stale/superseded results."""
        if request_id != self._request_epoch or file_path != self._current_file_path:
            return  # user has since selected a different image

        if png_bytes is None:
            self._face_detection_widget.setText("Could not load image")
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(png_bytes) or pixmap.isNull():
            self._face_detection_widget.setText("Could not load image")
            return

        self._face_detection_widget.set_image(pixmap, zoom_factor=scale)
        self._face_detection_widget.set_faces(self._faces)
        self._face_detection_widget.set_show_bboxes(self._faces_checkbox.isChecked())

    def set_available_persons(self, persons: list[str]) -> None:
        """Set the library's person names offered while naming faces."""
        self._face_detection_widget.set_known_names(persons)

    def set_suggestion_provider(
        self, provider: Callable[[int], list[str]] | None
    ) -> None:
        """Set the face_id -> likely names callback used while naming faces."""
        self._face_detection_widget.set_suggestion_provider(provider)

    def start_naming(self) -> None:
        """Enter naming mode on the first unnamed face of the shown image."""
        self._face_detection_widget.start_naming()

    def _update_face_action_visibility(self) -> None:
        """Only offer naming when the image has faces."""
        self._name_faces_btn.setVisible(len(self._faces) > 0)

    def _on_naming_mode_changed(self, active: bool) -> None:  # noqa: FBT001
        if active:
            self._faces_checkbox.setChecked(True)
            return
        if self._faces_dirty:
            self._faces_dirty = False
            self.faces_changed.emit()

    def _on_face_name_committed(self, face_id: int, name: str) -> None:
        self._faces = self._face_detection_widget.get_faces()
        self._faces_dirty = True
        self.face_reassign_requested.emit(face_id, name)

    def _on_face_ignored(self, face_id: int) -> None:
        self._faces = self._face_detection_widget.get_faces()
        self._faces_dirty = True
        self.face_delete_requested.emit(face_id)

    def _on_faces_toggled(self, _state: int) -> None:
        """Toggle bbox visibility immediately on the already-loaded image."""
        show = self._faces_checkbox.isChecked()
        self._face_detection_widget.set_show_bboxes(show)
        # If turning on and faces haven't been rendered yet, force a redraw
        if show:
            self._face_detection_widget.set_faces(self._faces)
