"""Image inspector widget for cluster detail view."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.widgets.face_detection_widget import FaceDetectionWidget

if TYPE_CHECKING:
    from photo_app.services.face_review_service import FaceReviewItem

LOGGER = logging.getLogger(__name__)


class ClusterImageInspectorWidget(QWidget):
    """Right-hand image inspector for the cluster detail view.

    Shows a selected image at fit-to-panel size with optional bbox overlay.
    Receives face data from the caller — does not query the DB itself.
    """

    face_delete_requested = Signal(int)  # face_id
    face_reassign_requested = Signal(int, str)  # face_id, new_person_name

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_file_path: str | None = None
        self._faces: list[FaceReviewItem] = []
        self._available_persons: list[str] = []

        # Face detection widget
        self._face_detection_widget = FaceDetectionWidget()
        self._face_detection_widget.setStyleSheet(
            "background-color: #1e1e1e; color: white;"
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

        # Face action area (initially hidden)
        self._face_action_layout = QHBoxLayout()
        self._face_action_layout.setContentsMargins(0, 0, 0, 0)

        self._face_dropdown = QComboBox()
        self._face_dropdown.setMaximumWidth(200)
        self._face_dropdown.setMinimumWidth(120)
        self._face_action_layout.addWidget(self._face_dropdown)

        self._delete_face_btn = QPushButton("Delete face")
        self._delete_face_btn.setMaximumWidth(100)
        self._delete_face_btn.clicked.connect(self._on_delete_face)
        self._face_action_layout.addWidget(self._delete_face_btn)

        self._reassign_face_btn = QPushButton("Reassign face…")
        self._reassign_face_btn.setMaximumWidth(120)
        self._reassign_face_btn.clicked.connect(self._on_reassign_face)
        self._face_action_layout.addWidget(self._reassign_face_btn)

        # Add face action area to toolbar
        toolbar_layout.addLayout(self._face_action_layout)

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
        """
        self._current_file_path = file_path
        self._faces = faces

        # Load image with fit-to-panel zoom
        try:
            pixmap = QPixmap(file_path)
            if pixmap.isNull():
                self._face_detection_widget.setText("Could not load image")
                self._update_face_action_visibility()
                return

            # Calculate fit-to-panel zoom
            viewport_width = self.width()
            viewport_height = self.height()

            # If viewport not yet laid out, use widget size
            if viewport_width <= 0 or viewport_height <= 0:
                viewport_width = self.width()
                viewport_height = self.height()

            # Calculate zoom to fit image with aspect ratio preserved
            width_ratio = viewport_width / pixmap.width() if pixmap.width() > 0 else 1.0
            height_ratio = (
                viewport_height / pixmap.height() if pixmap.height() > 0 else 1.0
            )
            zoom = min(width_ratio, height_ratio)

            # Constrain between 0.2 and 3.0
            zoom = max(0.2, min(3.0, zoom))

            # Scale image
            scaled = pixmap.scaled(
                int(pixmap.width() * zoom),
                int(pixmap.height() * zoom),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

            self._face_detection_widget.set_image(scaled, zoom_factor=zoom)
            self._face_detection_widget.set_faces(faces)
            self._face_detection_widget.set_show_bboxes(
                self._faces_checkbox.isChecked()
            )

        except Exception:
            LOGGER.exception("Failed to load image %s", file_path)
            self._face_detection_widget.setText("Error loading image")

        self._update_face_action_visibility()

    def set_available_persons(self, persons: list[str]) -> None:
        """Set the list of available persons for reassignment dropdown."""
        self._available_persons = persons
        self._update_face_dropdown()

    def _update_face_action_visibility(self) -> None:
        """Show/hide face action area based on whether faces exist."""
        has_faces = len(self._faces) > 0
        for i in range(self._face_action_layout.count()):
            item = self._face_action_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if widget:
                    widget.setVisible(has_faces)
        self._update_face_dropdown()

    def _update_face_dropdown(self) -> None:
        """Update the face dropdown with current faces."""
        self._face_dropdown.clear()
        for face in self._faces:
            label = face.person_name or f"Unknown face {face.face_id}"
            self._face_dropdown.addItem(label, face.face_id)

    def _on_delete_face(self) -> None:
        """Handle delete face button click."""
        if not self._faces:
            return
        current_index = self._face_dropdown.currentIndex()
        if current_index >= 0:
            face_id = self._face_dropdown.itemData(current_index)
            self.face_delete_requested.emit(face_id)

    def _on_reassign_face(self) -> None:
        """Handle reassign face button click."""
        if not self._faces:
            return
        current_index = self._face_dropdown.currentIndex()
        if current_index >= 0:
            face_id = self._face_dropdown.itemData(current_index)

            name, ok = QInputDialog.getItem(
                self,
                "Reassign Face",
                "Select person name:",
                self._available_persons,
                0,
                editable=False,
            )
            if ok and name:
                self.face_reassign_requested.emit(face_id, name)

    def _on_faces_toggled(self, _state: int) -> None:
        """Toggle bbox visibility immediately on the already-loaded image."""
        show = self._faces_checkbox.isChecked()
        self._face_detection_widget.set_show_bboxes(show)
        # If turning on and faces haven't been rendered yet, force a redraw
        if show:
            self._face_detection_widget.set_faces(self._faces)
