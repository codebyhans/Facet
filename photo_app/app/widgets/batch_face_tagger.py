"""Batch face tagging dialog for assigning names to multiple faces at once."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from photo_app.domain.models import Face, Person
    from photo_app.services.face_review_service import FaceReviewService


@dataclass
class FaceAssignment:
    """Face to person assignment."""

    face_id: int
    person_id: int | None
    exclude: bool = False


class BatchFaceTaggerDialog(QDialog):
    """Dialog for batch-tagging multiple faces in selected images."""

    def __init__(
        self,
        parent: QWidget | None = None,
        face_review_service: FaceReviewService | None = None,
    ) -> None:
        """Initialize batch face tagger dialog."""
        super().__init__(parent)
        self._face_review_service = face_review_service
        self._faces: list[Face] = []
        self._persons: list[Person] = []
        self._assignments: dict[int, FaceAssignment] = {}
        self.setWindowTitle("Batch Face Tagger")
        self.setGeometry(100, 100, 900, 600)
        self._init_ui()

    def _init_ui(self) -> None:
        """Initialize the UI layout."""
        layout = QVBoxLayout(self)

        # Instructions
        instructions = QLabel(
            "Select a person for each face. Double-click to mark as excluded."
        )
        layout.addWidget(instructions)

        # Confidence threshold slider
        confidence_layout = QHBoxLayout()
        confidence_layout.addWidget(QLabel("Min Detection Confidence:"))
        self._confidence_slider = QSlider(Qt.Orientation.Horizontal)
        self._confidence_slider.setMinimum(50)
        self._confidence_slider.setMaximum(100)
        self._confidence_slider.setValue(80)
        self._confidence_slider.valueChanged.connect(self._on_confidence_changed)
        confidence_layout.addWidget(self._confidence_slider)
        self._confidence_label = QLabel("80%")
        confidence_layout.addWidget(self._confidence_label)
        layout.addLayout(confidence_layout)

        # Face list
        face_list_label = QLabel("Detected Faces:")
        layout.addWidget(face_list_label)

        self._face_list = QListWidget()
        layout.addWidget(self._face_list)

        # Buttons
        button_layout = QHBoxLayout()

        self._apply_button = QPushButton("Apply Assignments")
        self._apply_button.clicked.connect(self.accept)
        button_layout.addWidget(self._apply_button)

        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self._cancel_button)

        layout.addLayout(button_layout)

    def set_faces(self, faces: list[Face]) -> None:
        """Set faces to display for tagging."""
        self._faces = faces
        self._assignments.clear()
        self._render_faces()

    def set_persons(self, persons: list[Person]) -> None:
        """Set available persons for assignment."""
        self._persons = persons

    def get_assignments(self) -> dict[int, FaceAssignment]:
        """Get face assignments after dialog acceptance."""
        return self._assignments.copy()

    def _render_faces(self) -> None:
        """Render the face list."""
        self._face_list.clear()
        for face in self._faces:
            if face.id is None:
                continue

            item = QListWidgetItem()
            widget = self._create_face_item_widget(face)
            item.setSizeHint(widget.sizeHint())
            self._face_list.addItem(item)
            self._face_list.setItemWidget(item, widget)

            # Initialize assignment
            if face.id not in self._assignments:
                self._assignments[face.id] = FaceAssignment(
                    face_id=face.id,
                    person_id=face.person_id,
                )

    def _create_face_item_widget(self, face: Face) -> QWidget:
        """Create a widget for a single face item."""
        widget = QWidget()
        layout = QHBoxLayout(widget)

        # Face ID and confidence
        confidence_text = f"Face #{face.id}"
        if face.confidence_score is not None:
            confidence_text += f" (Conf: {face.confidence_score*100:.0f}%)"

        label = QLabel(confidence_text)
        layout.addWidget(label)

        # Person combo
        combo = QComboBox()
        combo.addItem("(Unassigned)", None)
        for person in self._persons:
            combo.addItem(person.name or f"Person #{person.id}", person.id)

        # Set current selection
        if face.person_id is not None:
            for i in range(combo.count()):
                if combo.itemData(i) == face.person_id:
                    combo.setCurrentIndex(i)
                    break

        # Connect signal
        face_id = face.id
        combo.currentIndexChanged.connect(
            lambda idx, fid=face_id: self._on_person_selected(fid, combo.itemData(idx))
        )
        layout.addWidget(combo)

        # Exclude checkbox (via button)
        exclude_btn = QPushButton("Exclude")
        exclude_btn.setCheckable(True)
        exclude_btn.setMaximumWidth(100)
        exclude_btn.toggled.connect(
            lambda checked, fid=face_id: self._on_exclude_toggled(fid, checked)
        )
        layout.addWidget(exclude_btn)

        layout.addStretch()
        return widget

    def _on_person_selected(self, face_id: int, person_id: int | None) -> None:
        """Handle person selection for a face."""
        if face_id in self._assignments:
            self._assignments[face_id].person_id = person_id

    def _on_exclude_toggled(self, face_id: int, checked: bool) -> None:
        """Handle face exclusion toggle."""
        if face_id in self._assignments:
            self._assignments[face_id].exclude = checked

    def _on_confidence_changed(self, value: int) -> None:
        """Handle confidence slider change."""
        self._confidence_label.setText(f"{value}%")
        # Could filter faces here based on confidence
