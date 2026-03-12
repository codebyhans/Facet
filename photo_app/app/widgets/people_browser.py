"""Dedicated People browser widget for the People tab."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QLayout,
)

from photo_app.app.widgets.cluster_image_grid import ClusterImageGridWidget
from photo_app.app.widgets.person_card_widget import PersonCardWidget

if TYPE_CHECKING:
    from photo_app.services.face_review_service import FaceReviewService, PersonStackSummary


class PeopleBrowser(QWidget):
    """Dedicated People browser widget with stacks view and person detail view."""

    person_selected = Signal(int, object)  # person_id, PersonStackSummary
    back_to_stacks = Signal()
    person_renamed = Signal(int, str)  # person_id, name
    show_unnamed_changed = Signal(bool)  # show_unnamed

    def __init__(
        self,
        face_review_service: "FaceReviewService | None" = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._face_review_service = face_review_service
        self._current_stacks: list[PersonStackSummary] = []
        self._current_person_id: int | None = None
        
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the user interface."""
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Show unnamed clusters toggle (moved to top, no title needed since navigation is persistent)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(12)

        # Spacer
        header_layout.addStretch()

        # Show unnamed clusters toggle
        self._show_unnamed_checkbox = QCheckBox("Show unnamed clusters")
        self._show_unnamed_checkbox.setStyleSheet("color: #cccccc; font-size: 11px;")
        self._show_unnamed_checkbox.stateChanged.connect(self._on_show_unnamed_toggled)
        header_layout.addWidget(self._show_unnamed_checkbox)

        main_layout.addLayout(header_layout)

        # Stacked widget for stacks view and person detail view
        self._stacked_widget = QStackedWidget()
        self._stacked_widget.setStyleSheet("background-color: #1e1e1e;")
        main_layout.addWidget(self._stacked_widget, 1)

        # Create stacks view
        self._stacks_view = self._create_stacks_view()
        self._stacked_widget.addWidget(self._stacks_view)  # Index 0

        # Create person detail view
        self._person_detail_view = self._create_person_detail_view()
        self._stacked_widget.addWidget(self._person_detail_view)  # Index 1

        # Set default view to stacks
        self._stacked_widget.setCurrentIndex(0)

        self.setStyleSheet("background-color: #1e1e1e;")

    def _create_stacks_view(self) -> QWidget:
        """Create the stacks view widget."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(12)

        # Info label
        self._stacks_info_label = QLabel("Loading people clusters...")
        self._stacks_info_label.setStyleSheet("color: #999; font-size: 11px; padding: 8px;")
        layout.addWidget(self._stacks_info_label)

        # Grid area for person cards
        self._grid_widget = QWidget()
        self._grid_layout = QVBoxLayout(self._grid_widget)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(12)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Scroll area for the grid
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self._grid_widget)
        scroll_area.setStyleSheet("QScrollArea { border: none; background-color: #1e1e1e; }")
        layout.addWidget(scroll_area, 1)

        return widget

    def _create_person_detail_view(self) -> QWidget:
        """Create the person detail view widget."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Header with back button and name
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        # Back button
        self._back_btn = QPushButton("← Back to People")
        self._back_btn.clicked.connect(self._on_back_to_stacks)
        self._back_btn.setMaximumWidth(150)
        header_layout.addWidget(self._back_btn)

        # Spacer
        header_layout.addStretch()

        # Person name (editable)
        self._person_name_input = QLineEdit()
        self._person_name_input.setPlaceholderText("Enter person name...")
        self._person_name_input.setMaximumWidth(300)
        self._person_name_input.returnPressed.connect(self._on_save_person_name)
        header_layout.addWidget(self._person_name_input)

        # Save button
        self._save_name_btn = QPushButton("Save Name")
        self._save_name_btn.clicked.connect(self._on_save_person_name)
        self._save_name_btn.setMaximumWidth(100)
        header_layout.addWidget(self._save_name_btn)

        layout.addLayout(header_layout)

        # Action buttons
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)

        self._merge_btn = QPushButton("Merge with...")
        self._merge_btn.setMaximumWidth(120)
        action_layout.addWidget(self._merge_btn)

        self._rename_btn = QPushButton("Rename")
        self._rename_btn.setMaximumWidth(100)
        action_layout.addWidget(self._rename_btn)

        self._delete_btn = QPushButton("Delete person")
        self._delete_btn.setMaximumWidth(120)
        action_layout.addWidget(self._delete_btn)

        action_layout.addStretch()
        layout.addLayout(action_layout)

        # Image count label
        self._image_count_label = QLabel()
        self._image_count_label.setStyleSheet("color: #999; font-size: 11px; padding: 4px;")
        layout.addWidget(self._image_count_label)

        # Gallery of person images
        gallery_label = QLabel("All images containing this person:")
        gallery_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 8px 0px 4px 0px; color: #cccccc;")
        layout.addWidget(gallery_label)

        # Image grid area - use QListView instead of manual layout
        self._cluster_image_grid = ClusterImageGridWidget()
        self._cluster_image_grid.setStyleSheet("background-color: #1e1e1e;")
        
        # Scroll area for image grid
        image_scroll_area = QScrollArea()
        image_scroll_area.setWidgetResizable(True)
        image_scroll_area.setWidget(self._cluster_image_grid)
        image_scroll_area.setStyleSheet("QScrollArea { border: none; background-color: #1e1e1e; }")
        layout.addWidget(image_scroll_area, 1)

        return widget

    def load_stacks(self, stacks: list[PersonStackSummary]) -> None:
        """Load and display person stacks in grid layout."""
        self._current_stacks = stacks
        self._clear_stacks_view()

        if not stacks:
            self._stacks_info_label.setText("No person clusters found.")
            return

        # Update info label
        total_images = sum(stack.image_count for stack in stacks)
        self._stacks_info_label.setText(
            f"Showing {len(stacks)} person clusters ({total_images} total images)"
        )

        # Create person cards in a grid layout
        grid_cols = 4  # Number of columns in the grid
        current_col = 0
        current_row_layout: QHBoxLayout | None = None

        for stack in stacks:
            card = PersonCardWidget(stack)
            card.person_clicked.connect(self._on_person_card_clicked)
            card.load_cover_image()

            # Create new row layout if needed
            if current_col == 0:
                current_row_layout = QHBoxLayout()
                current_row_layout.setContentsMargins(0, 0, 0, 0)
                current_row_layout.setSpacing(12)

            if current_row_layout is not None:
                current_row_layout.addWidget(card, 1)
            current_col += 1

            # Add row to grid and start new row if needed
            if current_col >= grid_cols:
                if current_row_layout is not None:
                    self._grid_layout.addLayout(current_row_layout)
                current_col = 0
                current_row_layout = None

        # Add remaining cards in incomplete row
        if current_row_layout is not None:
            current_row_layout.addStretch()
            self._grid_layout.addLayout(current_row_layout)

        # Add stretch at the bottom
        self._grid_layout.addStretch()

    def _clear_stacks_view(self) -> None:
        """Clear all person cards and row layouts from the stacks grid."""
        self._clear_layout(self._grid_layout)

    def _clear_layout(self, layout: QLayout) -> None:
        """Recursively remove all items from a layout."""
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child_layout = item.layout()
            if child_layout is not None:
                self._clear_layout(child_layout)
                child_layout.deleteLater()

    def show_person_detail(self, person_id: int, stack: PersonStackSummary) -> None:
        """Show person detail view for the selected person."""
        self._current_person_id = person_id
        self._current_stack = stack

        # Update header
        if stack.person_name:
            self._person_name_input.setText(stack.person_name)
        else:
            self._person_name_input.setPlaceholderText(f"Cluster #{person_id}")

        # Update image count
        self._image_count_label.setText(f"{stack.image_count} images in this cluster")

        # Load gallery images
        self._load_person_gallery(stack)

        # Switch to detail view
        self._stacked_widget.setCurrentIndex(1)

    def _load_person_gallery(self, stack: PersonStackSummary) -> None:
        """Load the gallery of images for the selected person."""
        # Use the new QListView-based grid
        self._cluster_image_grid.set_cluster_images(stack)

    def _show_person_detail(self, person: object, faces: list[object]) -> None:
        """Show person detail view for the selected person."""
        # For now, just emit the signal to main window
        # The main window will handle showing the detail view
        person_id = getattr(person, 'id', 0) if person is not None else 0
        self.person_selected.emit(person_id, faces)

    def _on_person_card_clicked(self, person_id: int, stack: PersonStackSummary) -> None:
        """Handle person card click - show person detail view."""
        # For now, just emit the signal to main window
        self.person_selected.emit(person_id, stack)

    def _on_back_to_stacks(self) -> None:
        """Handle back to stacks button click."""
        self._stacked_widget.setCurrentIndex(0)
        self.back_to_stacks.emit()

    def _on_save_person_name(self) -> None:
        """Handle save person name."""
        if not hasattr(self, '_current_stack') or getattr(self, '_current_stack', None) is None:
            return

        name = self._person_name_input.text().strip()
        if not name:
            return

        # Get current person ID from the stack
        current_stack = getattr(self, '_current_stack', None)
        if current_stack is not None:
            person_id = current_stack.person_id
            # Emit a signal to save the person name
            self.person_selected.emit(person_id, name)

    def _on_show_unnamed_toggled(self, state: int) -> None:
        """Handle show unnamed clusters toggle."""
        # This will be handled by the main window
        pass

    def set_threshold(self, threshold: int) -> None:
        """Set the threshold for filtering stacks."""
        self._current_threshold = threshold
        # Reload stacks with new threshold (this will be handled by main window)