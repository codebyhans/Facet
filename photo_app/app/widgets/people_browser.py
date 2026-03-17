"""Dedicated People browser widget for the People tab."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.widgets.cluster_image_grid import ClusterImageGridWidget
from photo_app.app.widgets.cluster_image_inspector import ClusterImageInspectorWidget
from photo_app.app.widgets.person_card_widget import PersonCardWidget

if TYPE_CHECKING:
    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
    from photo_app.services.face_review_service import (
        FaceReviewService,
        PersonStackSummary,
    )


class PeopleBrowser(QWidget):
    """Dedicated People browser widget with stacks view and person detail view."""

    _CARD_WIDTH = 148   # matches PersonCardWidget fixed width + border
    _CARD_SPACING = 12  # matches grid spacing

    person_selected = Signal(int, object)  # person_id, PersonStackSummary
    back_to_stacks = Signal()
    person_renamed = Signal(int, str)  # person_id, name
    person_merge_requested = Signal(int)  # person_id
    person_merge_multiple_requested = Signal(list, int)  # source_person_ids, target_person_id
    show_unnamed_changed = Signal(bool)  # show_unnamed
    face_delete_requested = Signal(int)  # face_id
    face_reassign_requested = Signal(int, str)  # face_id, new_person_name

    def __init__(
        self,
        face_review_service: FaceReviewService | None = None,
        tile_store: ThumbnailTileStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._face_review_service = face_review_service
        self._tile_store = tile_store
        self._current_stacks: list[PersonStackSummary] = []
        self._current_person_id: int | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the user interface."""
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

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
        layout.setSpacing(8)

        # Toolbar: info label left, show-unnamed toggle right
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)

        self._stacks_info_label = QLabel("Loading people clusters...")
        self._stacks_info_label.setStyleSheet("color: #999; font-size: 11px;")
        toolbar.addWidget(self._stacks_info_label)
        toolbar.addStretch()

        self._show_unnamed_checkbox = QCheckBox("Show unnamed")
        self._show_unnamed_checkbox.setStyleSheet("color: #888888; font-size: 11px;")
        self._show_unnamed_checkbox.stateChanged.connect(self._on_show_unnamed_toggled)
        toolbar.addWidget(self._show_unnamed_checkbox)

        layout.addLayout(toolbar)

        # Grid area for person cards
        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(12)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Scroll area for the grid
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self._grid_widget)
        scroll_area.setStyleSheet("QScrollArea { border: none; background-color: #1e1e1e; }")
        layout.addWidget(scroll_area, 1)

        # Store reference to scroll area for responsive grid
        self._stacks_scroll_area = scroll_area

        return widget

    def _create_person_detail_view(self) -> QWidget:  # noqa: PLR0915
        """Create the person detail view widget with two-column layout."""
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

        # Person name area (tightened)
        name_layout = QHBoxLayout()
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(8)

        # Person name (editable)
        self._person_name_input = QLineEdit()
        self._person_name_input.setPlaceholderText("Enter person name...")
        self._person_name_input.setMaximumWidth(250)
        self._person_name_input.setMinimumWidth(150)
        self._person_name_input.returnPressed.connect(self._on_save_person_name)
        name_layout.addWidget(self._person_name_input)

        # Save button
        self._save_name_btn = QPushButton("Save")
        self._save_name_btn.clicked.connect(self._on_save_person_name)
        self._save_name_btn.setMaximumWidth(80)
        name_layout.addWidget(self._save_name_btn)

        # Merge button
        self._merge_btn = QPushButton("Merge")
        self._merge_btn.clicked.connect(self._on_merge_person)
        self._merge_btn.setMaximumWidth(80)
        name_layout.addWidget(self._merge_btn)

        header_layout.addLayout(name_layout)

        layout.addLayout(header_layout)

        # Image count label (tightened)
        self._image_count_label = QLabel()
        self._image_count_label.setStyleSheet("color: #999; font-size: 11px; padding: 2px 0px;")
        layout.addWidget(self._image_count_label)

        # Two-column layout for image grid and inspector
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left pane: Image grid
        left_pane = QWidget()
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)

        gallery_label = QLabel("All images containing this person:")
        gallery_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 8px 0px 4px 0px; color: #cccccc;")
        left_layout.addWidget(gallery_label)

        self._cluster_image_grid = ClusterImageGridWidget()
        self._cluster_image_grid.setStyleSheet("background-color: #1e1e1e;")

        # Scroll area for image grid
        image_scroll_area = QScrollArea()
        image_scroll_area.setWidgetResizable(True)
        image_scroll_area.setWidget(self._cluster_image_grid)
        image_scroll_area.setStyleSheet("QScrollArea { border: none; background-color: #1e1e1e; }")
        left_layout.addWidget(image_scroll_area, 1)

        splitter.addWidget(left_pane)

        # Right pane: Image inspector
        self._inspector = ClusterImageInspectorWidget()
        splitter.addWidget(self._inspector)

        # Set sizes: left min 250, initial 300; right min 350
        splitter.setSizes([300, 350])
        splitter.setMinimumWidth(600)  # Ensure minimum total width

        layout.addWidget(splitter, 1)

        # Connect signals
        self._cluster_image_grid.activated.connect(self._on_cluster_image_activated)
        self._inspector.face_delete_requested.connect(self.face_delete_requested)
        self._inspector.face_reassign_requested.connect(self.face_reassign_requested)

        return widget

    def load_stacks(self, stacks: list[PersonStackSummary], cover_lookups: dict[int, tuple[str, int, int, int, int]] | None = None) -> None:
        """Load and display person stacks in grid layout."""
        self._current_stacks = stacks
        self._current_cover_lookups = cover_lookups or {}
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
        grid_cols = self._calc_grid_cols()
        self._current_grid_cols = grid_cols
        tile_pixmaps: dict[str, QPixmap] = {}
        cover_lookups = self._current_cover_lookups

        for i, stack in enumerate(stacks):
            card = PersonCardWidget(stack, self._tile_store)
            card.person_clicked.connect(self._on_person_card_clicked)

            # Use pre-fetched lookup, deduplicating tile PNG reads per unique file
            lookup = cover_lookups.get(stack.cover_image_id) if stack.cover_image_id else None
            if lookup is not None:
                tile_path_str, x, y, w, h = lookup
                if tile_path_str not in tile_pixmaps:
                    tile_pixmaps[tile_path_str] = QPixmap(tile_path_str)
                tile_pix = tile_pixmaps[tile_path_str]
                if not tile_pix.isNull():
                    card.set_cover_pixmap(tile_pix.copy(x, y, w, h))
                else:
                    card.load_cover_image()
            else:
                card.load_cover_image()

            row, col = divmod(i, grid_cols)
            self._grid_layout.addWidget(card, row, col)

        for col in range(grid_cols):
            self._grid_layout.setColumnStretch(col, 0)
        # Trailing stretch column absorbs remaining space
        self._grid_layout.setColumnStretch(grid_cols, 1)

    def get_current_stacks(self) -> list[PersonStackSummary]:
        """Get the current person stacks."""
        return self._current_stacks

    def _clear_stacks_view(self) -> None:
        """Clear all person cards from the stacks grid."""
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def show_person_detail(self, person_id: int, stack: PersonStackSummary) -> None:
        """Show person detail view for the selected person."""
        self._current_person_id = person_id
        self._current_stack = stack
        self._current_inspector_path = None
        self._inspector.show_placeholder()

        # Update header - always set/clear the name input unconditionally
        if stack.person_name:
            self._person_name_input.setText(stack.person_name)
            self._person_name_input.setPlaceholderText("")
        else:
            self._person_name_input.setText("")
            self._person_name_input.setPlaceholderText("Name this person...")

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
        person_id = getattr(person, "id", 0) if person is not None else 0
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
        if not hasattr(self, "_current_stack") or getattr(self, "_current_stack", None) is None:
            return

        name = self._person_name_input.text().strip()
        if not name:
            return

        # Get current person ID from the stack
        current_stack = getattr(self, "_current_stack", None)
        if current_stack is not None:
            person_id = current_stack.person_id
            # Emit a signal to rename the person
            self.person_renamed.emit(person_id, name)

    def _on_merge_person(self) -> None:
        """Handle merge person button click."""
        if not hasattr(self, "_current_stack") or getattr(self, "_current_stack", None) is None:
            return

        current_stack = getattr(self, "_current_stack", None)
        if current_stack is not None:
            person_id = current_stack.person_id
            # Emit a signal to merge the person
            self.person_merge_requested.emit(person_id)

    def _on_cluster_image_activated(self, index: QModelIndex) -> None:
        """Handle image selection in the cluster grid."""
        model = self._cluster_image_grid.model()
        if not hasattr(model, "_image_paths"):
            return
        row = index.row()
        file_path: str | None = getattr(model, "_image_paths", [])[row] if row < len(getattr(model, "_image_paths", [])) else None
        if file_path is None:
            return

        self._current_inspector_path = file_path

        # Load faces via service
        faces: list[object] = []
        if self._face_review_service is not None:
            try:
                faces = self._face_review_service.faces_for_image_path(file_path)
            except Exception:  # noqa: BLE001
                faces = []

        # Populate person names for reassign dropdown
        # Populate reassign dropdown with ALL named persons in the library
        person_names: list[str] = []
        if self._face_review_service is not None:
            try:
                all_stacks = self._face_review_service.person_stacks()
                person_names = sorted(
                    s.person_name
                    for s in all_stacks
                    if s.person_name is not None
                )
            except Exception:  # noqa: BLE001
                # Fall back to names visible in this image
                person_names = sorted({
                    name for name in (getattr(f, "person_name", None) for f in faces) if name is not None
                })
        self._inspector.set_available_persons(person_names)
        self._inspector.load_image(file_path, faces)

    def reload_current_inspector_image(self) -> None:
        """Reload face data for the currently inspected image."""
        if self._current_inspector_path is None:
            return
        faces: list[object] = []
        if self._face_review_service is not None:
            with contextlib.suppress(Exception):
                faces = self._face_review_service.faces_for_image_path(
                    self._current_inspector_path
                )

        # Populate reassign dropdown with ALL named persons in the library
        person_names: list[str] = []
        if self._face_review_service is not None:
            try:
                all_stacks = self._face_review_service.person_stacks()
                person_names = sorted(
                    s.person_name
                    for s in all_stacks
                    if s.person_name is not None
                )
            except Exception:  # noqa: BLE001
                # Fall back to names visible in this image
                person_names = sorted({
                    name for name in (getattr(f, "person_name", None) for f in faces) if name is not None
                })

        self._inspector.set_available_persons(person_names)
        self._inspector.load_image(self._current_inspector_path, faces)

    def _on_show_unnamed_toggled(self, state: int) -> None:
        """Handle show unnamed clusters toggle."""
        self.show_unnamed_changed.emit(state == Qt.CheckState.Checked)

    def _calc_grid_cols(self) -> int:
        """Calculate how many cards fit in the current viewport width."""
        if not hasattr(self, "_stacks_scroll_area"):
            return 4
        viewport_w = self._stacks_scroll_area.viewport().width()
        return max(1, (viewport_w + self._CARD_SPACING) // (self._CARD_WIDTH + self._CARD_SPACING))

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        """Handle resize events to reflow the grid."""
        super().resizeEvent(event)
        # Only reflow when showing the stacks view and stacks are loaded
        if (
            hasattr(self, "_stacked_widget")
            and self._stacked_widget.currentIndex() == 0
            and hasattr(self, "_current_stacks")
            and self._current_stacks
        ):
            new_cols = self._calc_grid_cols()
            if hasattr(self, "_current_grid_cols") and new_cols != self._current_grid_cols:
                self.load_stacks(self._current_stacks, self._current_cover_lookups)

    def set_threshold(self, threshold: int) -> None:
        """Set the threshold for filtering stacks."""
        self._current_threshold = threshold
        # Reload stacks with new threshold (this will be handled by main window)
