"""Main view for reviewing and tagging face clusters."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.widgets.person_stack_widget import PersonStackWidget

if TYPE_CHECKING:
    from photo_app.services.face_review_service import (
        FaceReviewService,
        PersonStackSummary,
    )

LOGGER = logging.getLogger(__name__)

class PersonDetailView(QWidget):
    """Main view for reviewing and naming face clusters."""

    person_renamed = Signal(int, str)  # person_id, new_name

    def __init__(
        self,
        face_review_service: FaceReviewService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._face_review_service = face_review_service
        self._current_stack: PersonStackSummary | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:  # noqa: PLR0915
        """Setup the user interface."""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        # Left panel: stack list
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        stacks_label = QLabel("Face Clusters")
        stacks_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 8px;")
        left_layout.addWidget(stacks_label)

        self._stacks_widget = PersonStackWidget()
        self._stacks_widget.stack_selected.connect(self._on_stack_selected)
        left_layout.addWidget(self._stacks_widget)

        left_panel.setMaximumWidth(350)
        main_layout.addWidget(left_panel)

        # Right panel: detail view
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 0, 0, 0)

        # Cover image
        self._cover_label = QLabel()
        self._cover_label.setStyleSheet("border: 1px solid #444; background-color: #2a2a2a;")
        self._cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_label.setFixedHeight(200)
        right_layout.addWidget(self._cover_label)

        # Name input
        name_layout = QHBoxLayout()
        name_label = QLabel("Person Name:")
        name_label.setStyleSheet("font-weight: bold;")
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Enter name (e.g., Alice)")
        self._name_input.returnPressed.connect(self._on_save_name)
        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save_name)
        self._save_btn.setMaximumWidth(80)

        name_layout.addWidget(name_label)
        name_layout.addWidget(self._name_input, 1)
        name_layout.addWidget(self._save_btn)
        right_layout.addLayout(name_layout)

        # Image count
        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: #999; font-size: 10px;")
        right_layout.addWidget(self._count_label)

        # Gallery of cluster images
        gallery_label = QLabel("All images in cluster:")
        gallery_label.setStyleSheet("font-weight: bold; font-size: 11px; padding: 8px 0px 4px 0px;")
        right_layout.addWidget(gallery_label)

        self._gallery_scroll = QScrollArea()
        self._gallery_scroll.setWidgetResizable(True)
        self._gallery_scroll.setStyleSheet("QScrollArea { border: none; }")

        gallery_widget = QWidget()
        self._gallery_layout = QVBoxLayout(gallery_widget)
        self._gallery_layout.setSpacing(4)
        self._gallery_scroll.setWidget(gallery_widget)
        right_layout.addWidget(self._gallery_scroll, 1)

        main_layout.addWidget(right_panel, 1)

        self.setStyleSheet("background-color: #1e1e1e; color: #ccc;")

    def load_stacks(self, stacks: list[PersonStackSummary]) -> None:
        """Load and display person stacks.

        Args:
            stacks: List of PersonStackSummary objects to display
        """
        self._stacks_widget.show_stacks(stacks)
        self._clear_detail_view()

    def _on_stack_selected(self, _person_id: int, stack: PersonStackSummary) -> None:
        """Handle stack selection."""
        self._current_stack = stack
        self._update_detail_view(stack)

    def _update_detail_view(self, stack: PersonStackSummary) -> None:
        """Update the detail view for a selected stack."""
        # Update cover image
        self._cover_label.clear()
        if stack.cover_image_path:
            try:
                pixmap = QPixmap(str(Path(stack.cover_image_path)))
                if not pixmap.isNull():
                    scaled = pixmap.scaledToHeight(
                        200,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    self._cover_label.setPixmap(scaled)
            except Exception:
                LOGGER.exception("Failed to load cover image for %s", stack.person_id)
                self._cover_label.setText("Could not load image")

        # Update name input
        self._name_input.setPlaceholderText(f"Cluster #{stack.person_id}")
        self._name_input.setText(stack.person_name or "")

        # Update count
        self._count_label.setText(f"{stack.image_count} images in this cluster")

        # Update gallery
        self._update_gallery(stack)

    def _update_gallery(self, stack: PersonStackSummary) -> None:
        """Update the gallery of images in the cluster."""
        # Clear existing
        while self._gallery_layout.count():
            item = self._gallery_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

        # Create thumbnail grid
        grid_widget = QWidget()
        grid_layout = QGridLayout(grid_widget)
        grid_layout.setSpacing(4)
        grid_layout.setContentsMargins(0, 0, 0, 0)

        cols = 4  # 4 thumbnails per row
        for idx, image_path in enumerate(stack.sample_image_paths):
            thumb_label = QLabel()
            thumb_label.setFixedSize(60, 60)
            thumb_label.setStyleSheet("border: 1px solid #444; background-color: #2a2a2a;")

            try:
                pixmap = QPixmap(str(Path(image_path)))
                if not pixmap.isNull():
                    scaled = pixmap.scaledToHeight(
                        60,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    thumb_label.setPixmap(scaled)
            except Exception:
                LOGGER.exception("Failed to load thumbnail for %s", image_path)

            grid_layout.addWidget(thumb_label, idx // cols, idx % cols)

        # Add stretch to bottom
        grid_layout.addItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding),
            (len(stack.sample_image_paths) // cols) + 1, 0
        )

        self._gallery_layout.addWidget(grid_widget)
    def _on_save_name(self) -> None:
        """Save the entered name for the current person."""
        if not self._current_stack:
            return

        name = self._name_input.text().strip()
        if not name:
            return

        # Emit signal
        self.person_renamed.emit(self._current_stack.person_id, name)

        # Update UI to reflect new name
        if self._face_review_service:
            try:
                self._face_review_service.rename_person_stack(self._current_stack.person_id, name)
                # Update local stack
                self._current_stack = self._current_stack.__class__(
                    person_id=self._current_stack.person_id,
                    cluster_id=self._current_stack.cluster_id,
                    person_name=name,
                    face_count=self._current_stack.face_count,
                    image_count=self._current_stack.image_count,
                    cover_image_path=self._current_stack.cover_image_path,
                    sample_image_paths=self._current_stack.sample_image_paths,
                )
                # Refresh the list to show updated name
                # This would require reloading stacks from service
            except Exception:
                LOGGER.exception("Failed to rename person stack %s", self._current_stack.person_id)

    def _clear_detail_view(self) -> None:
        """Clear the detail view."""
        self._cover_label.clear()
        self._name_input.clear()
        self._name_input.setPlaceholderText("Select a cluster")
        self._count_label.clear()
        while self._gallery_layout.count():
            item = self._gallery_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._current_stack = None
