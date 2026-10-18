"""Dedicated People browser widget for the People tab."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, QModelIndex, QObject, Qt, QThreadPool, Signal
from PySide6.QtGui import QPixmap, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.widgets.cluster_image_grid import ClusterImageGridWidget
from photo_app.app.widgets.cluster_image_inspector import ClusterImageInspectorWidget
from photo_app.app.widgets.face_naming import suggestion_provider_for
from photo_app.app.widgets.person_card_widget import (
    BASE_COVER_PX,
    CARD_EXTRA_WIDTH,
    MAX_COVER_PX,
    PersonCardWidget,
)
from photo_app.app.workers.person_gallery_worker import PersonGalleryWorker
from photo_app.app.workers.runner import start_worker

if TYPE_CHECKING:
    from pathlib import Path

    from photo_app.domain.value_objects import BoundingBox
    from photo_app.infrastructure.thumbnail_tiles import (
        ImageTileLookup,
        ThumbnailTileStore,
    )
    from photo_app.services.face_review_service import (
        FaceReviewItem,
        FaceReviewService,
        PersonStackSummary,
    )

LOGGER = logging.getLogger(__name__)

# Ctrl+scroll zoom bounds for the person cards, relative to BASE_COVER_PX
_ZOOM_SCALE_MIN = 0.6
_ZOOM_SCALE_MAX = MAX_COVER_PX / BASE_COVER_PX
_ZOOM_STEP_PER_NOTCH = 1.1


class PeopleBrowser(QWidget):
    """Dedicated People browser widget with stacks view and person detail view."""

    _CARD_SPACING = 12  # matches grid spacing

    # Sort modes for the stacks grid: (combo label, mode key)
    _SORT_CLUSTER_SIZE = "cluster_size"
    _SORT_DATE_NEWEST = "date_newest"
    _SORT_DATE_OLDEST = "date_oldest"
    _SORT_SIMILARITY = "similarity"
    _SORT_FACE_LARGEST = "face_largest"
    _SORT_FACE_SMALLEST = "face_smallest"
    _SORT_OPTIONS = (
        ("Cluster size", _SORT_CLUSTER_SIZE),
        ("Cover date (newest)", _SORT_DATE_NEWEST),
        ("Cover date (oldest)", _SORT_DATE_OLDEST),
        ("Similar faces together", _SORT_SIMILARITY),
        ("Cover face size (largest)", _SORT_FACE_LARGEST),
        ("Cover face size (smallest)", _SORT_FACE_SMALLEST),
    )

    person_selected = Signal(int, object)  # person_id, PersonStackSummary
    back_to_stacks = Signal()
    person_renamed = Signal(int, str)  # person_id, name (cosmetic tag rename)
    cluster_tag_requested = Signal(int, str)  # cluster_id, name (apply a tag)
    person_birth_date_changed = Signal(
        int, str
    )  # person_id, "YYYY-MM-DD" (empty=clear)
    people_exclude_requested = Signal(list)  # person_ids
    clusters_tag_requested = Signal(list, str)  # cluster_ids, name (bulk tag)
    show_unnamed_changed = Signal(bool)  # show_unnamed
    face_delete_requested = Signal(int)  # face_id
    face_reassign_requested = Signal(int, str)  # face_id, new_person_name
    faces_changed = Signal()  # A face-naming session in the inspector ended
    images_move_requested = Signal(
        list, int, str
    )  # image_ids, source_person_id, target_person_name

    def __init__(
        self,
        face_review_service: FaceReviewService | None = None,
        tile_store: ThumbnailTileStore | None = None,
        parent: QWidget | None = None,
        cache_directory: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._face_review_service = face_review_service
        self._tile_store = tile_store
        self._cache_directory = cache_directory
        self._current_stacks: list[PersonStackSummary] = []
        self._current_person_id: int | None = None
        self._current_inspector_path: str | None = None
        self._current_stack: PersonStackSummary | None = None
        self._person_cards: dict[int, PersonCardWidget] = {}
        self._cards_zoom_scale = 1.0
        self._selection_mode = False
        self._selected_person_ids: set[int] = set()
        self._thread_pool = QThreadPool.globalInstance()
        self._gallery_request_epoch = 0

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

        sort_label = QLabel("Sort:")
        sort_label.setStyleSheet("color: #888888; font-size: 11px;")
        toolbar.addWidget(sort_label)
        self._sort_combo = QComboBox()
        self._sort_combo.setStyleSheet("font-size: 11px;")
        for label, mode in self._SORT_OPTIONS:
            self._sort_combo.addItem(label, mode)
        self._sort_combo.setToolTip(
            "Order people by cluster size, or by the capture date of the "
            "photo used as each cluster's preview image, by how large the "
            "face is relative to that preview image, or place people "
            "whose faces look alike next to each other"
        )
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        toolbar.addWidget(self._sort_combo)

        self._show_unnamed_checkbox = QCheckBox("Show unnamed")
        self._show_unnamed_checkbox.setStyleSheet("color: #888888; font-size: 11px;")
        self._show_unnamed_checkbox.stateChanged.connect(self._on_show_unnamed_toggled)
        toolbar.addWidget(self._show_unnamed_checkbox)

        self._select_mode_btn = QPushButton("Select")
        self._select_mode_btn.setCheckable(True)
        self._select_mode_btn.setMaximumWidth(70)
        self._select_mode_btn.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 3px 8px; }"
            "QPushButton:checked { background-color: #4a9eff; color: white; }"
        )
        self._select_mode_btn.toggled.connect(self._on_select_mode_toggled)
        toolbar.addWidget(self._select_mode_btn)

        layout.addLayout(toolbar)

        # Selection action bar: appears only while selection mode is active.
        layout.addWidget(self._create_selection_bar())

        # Grid area for person cards
        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(12)
        self._grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )

        # Scroll area for the grid
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self._grid_widget)
        scroll_area.setStyleSheet(
            "QScrollArea { border: none; background-color: #1e1e1e; }"
        )
        layout.addWidget(scroll_area, 1)

        # Store reference to scroll area for responsive grid
        self._stacks_scroll_area = scroll_area
        # Ctrl+scroll anywhere over the cards zooms them (wheel events from
        # the cards propagate up to the viewport)
        scroll_area.viewport().installEventFilter(self)

        return widget

    def _create_selection_bar(self) -> QWidget:
        """Create the bulk-action bar shown while selection mode is active."""
        self._selection_bar = QWidget()
        selection_bar_layout = QHBoxLayout(self._selection_bar)
        selection_bar_layout.setContentsMargins(0, 4, 0, 4)

        self._selection_count_label = QLabel("0 selected")
        self._selection_count_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        selection_bar_layout.addWidget(self._selection_count_label)
        selection_bar_layout.addStretch()

        self._rename_selected_btn = QPushButton("Rename...")
        self._rename_selected_btn.setMaximumWidth(90)
        self._rename_selected_btn.setEnabled(False)
        self._rename_selected_btn.clicked.connect(self._on_rename_selected_clicked)
        selection_bar_layout.addWidget(self._rename_selected_btn)

        self._exclude_selected_btn = QPushButton("Not of Interest")
        self._exclude_selected_btn.setMaximumWidth(120)
        self._exclude_selected_btn.setEnabled(False)
        self._exclude_selected_btn.setToolTip(
            "Exclude every face for the selected people — they won't be "
            "clustered or shown again. Nothing is deleted; undo any time "
            "via People → Excluded Faces..."
        )
        self._exclude_selected_btn.clicked.connect(self._on_exclude_selected_clicked)
        selection_bar_layout.addWidget(self._exclude_selected_btn)

        self._clear_selection_btn = QPushButton("Clear")
        self._clear_selection_btn.setMaximumWidth(70)
        self._clear_selection_btn.clicked.connect(self._on_clear_selection_clicked)
        selection_bar_layout.addWidget(self._clear_selection_btn)

        self._selection_bar.setVisible(False)
        return self._selection_bar

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

        header_layout.addLayout(name_layout)

        layout.addLayout(header_layout)

        # Image count label (tightened)
        self._image_count_label = QLabel()
        self._image_count_label.setStyleSheet(
            "color: #999; font-size: 11px; padding: 2px 0px;"
        )
        layout.addWidget(self._image_count_label)

        # Two-column layout for image grid and inspector
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left pane: Image grid
        left_pane = QWidget()
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)

        gallery_label = QLabel("All images containing this person:")
        gallery_label.setStyleSheet(
            "font-weight: bold; font-size: 12px; padding: 8px 0px 4px 0px; color: #cccccc;"
        )
        left_layout.addWidget(gallery_label)

        # Split-cluster toolbar: select a run of wrongly-grouped photos
        # (Ctrl/Shift-click, like a file manager) and move just those to a
        # different — new or existing — person in one action, instead of
        # naming the whole (possibly still-mixed) cluster and hoping for
        # the best, or fixing one photo at a time.
        split_toolbar = QHBoxLayout()
        split_toolbar.setContentsMargins(0, 0, 0, 4)

        self._selection_count_label = QLabel("No photos selected")
        self._selection_count_label.setStyleSheet("color: #888888; font-size: 11px;")
        split_toolbar.addWidget(self._selection_count_label)
        split_toolbar.addStretch()

        self._select_all_images_btn = QPushButton("Select All")
        self._select_all_images_btn.setMaximumWidth(80)
        self._select_all_images_btn.clicked.connect(self._cluster_image_grid_select_all)
        split_toolbar.addWidget(self._select_all_images_btn)

        self._clear_image_selection_btn = QPushButton("Clear")
        self._clear_image_selection_btn.setMaximumWidth(60)
        self._clear_image_selection_btn.setEnabled(False)
        self._clear_image_selection_btn.clicked.connect(
            self._cluster_image_grid_clear_selection
        )
        split_toolbar.addWidget(self._clear_image_selection_btn)

        self._move_selected_images_btn = QPushButton("Move Selected to…")
        self._move_selected_images_btn.setEnabled(False)
        self._move_selected_images_btn.clicked.connect(
            self._on_move_selected_images_clicked
        )
        split_toolbar.addWidget(self._move_selected_images_btn)

        left_layout.addLayout(split_toolbar)

        self._cluster_image_grid = ClusterImageGridWidget()
        self._cluster_image_grid.setStyleSheet("background-color: #1e1e1e;")

        # Scroll area for image grid
        image_scroll_area = QScrollArea()
        image_scroll_area.setWidgetResizable(True)
        image_scroll_area.setWidget(self._cluster_image_grid)
        image_scroll_area.setStyleSheet(
            "QScrollArea { border: none; background-color: #1e1e1e; }"
        )
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
        self._cluster_image_grid.selection_changed.connect(
            self._on_cluster_image_selection_changed
        )
        self._inspector.face_delete_requested.connect(self.face_delete_requested)
        self._inspector.face_reassign_requested.connect(self.face_reassign_requested)
        self._inspector.faces_changed.connect(self.faces_changed)
        if self._face_review_service is not None:
            self._inspector.set_suggestion_provider(
                suggestion_provider_for(self._face_review_service)
            )

        return widget

    def load_stacks(
        self,
        stacks: list[PersonStackSummary],
        cover_lookups: dict[int, tuple[str, int, int, int, int]] | None = None,
        cover_highlight_pngs: dict[int, bytes] | None = None,
    ) -> None:
        """Load and display person stacks in grid layout."""
        stacks = self._sorted_stacks(stacks)
        self._current_stacks = stacks
        self._current_cover_lookups = cover_lookups or {}
        self._current_cover_highlight_pngs = cover_highlight_pngs or {}
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
        self._person_cards = {}
        self._selected_person_ids.clear()

        for stack in stacks:
            card = PersonCardWidget(
                stack, self._tile_store, cover_size=self._cover_size()
            )
            card.person_clicked.connect(self._on_person_card_clicked)
            card.selection_toggled.connect(self._on_card_selection_toggled)
            card.rename_requested.connect(self._on_card_rename_requested)
            card.tag_requested.connect(self.cluster_tag_requested)
            card.birth_date_requested.connect(self.person_birth_date_changed)
            card.set_selection_mode(self._selection_mode)
            self._person_cards[stack.person_id] = card
            self._populate_card_cover(card, stack, cover_lookups, tile_pixmaps)

        self._place_cards_in_grid(grid_cols)
        self._update_selection_bar()

    def _place_cards_in_grid(self, grid_cols: int) -> None:
        """(Re)position the existing person cards into `grid_cols` columns."""
        # Detach first (without deleting) so re-adding doesn't hit Qt's
        # "already in a layout" path
        while self._grid_layout.count():
            self._grid_layout.takeAt(0)
        for i, stack in enumerate(self._current_stacks):
            card = self._person_cards.get(stack.person_id)
            if card is None:
                continue
            row, col = divmod(i, grid_cols)
            self._grid_layout.addWidget(card, row, col)

        for col in range(self._grid_layout.columnCount()):
            self._grid_layout.setColumnStretch(col, 0)
        # Trailing stretch column absorbs remaining space
        self._grid_layout.setColumnStretch(grid_cols, 1)

    def _cover_size(self) -> int:
        """Return the person cards' current (zoomed) cover size in px."""
        return round(BASE_COVER_PX * self._cards_zoom_scale)

    def _card_width(self) -> int:
        """Return the person cards' current (zoomed) width in px."""
        return self._cover_size() + CARD_EXTRA_WIDTH

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Ctrl+scroll over the person cards grid zooms the cards."""
        if (
            isinstance(event, QWheelEvent)
            and hasattr(self, "_stacks_scroll_area")
            and watched is self._stacks_scroll_area.viewport()
        ):
            wheel = event
            if wheel.modifiers() & Qt.KeyboardModifier.ControlModifier:
                notches = wheel.angleDelta().y() / 120
                if notches:
                    new_scale = self._cards_zoom_scale * (_ZOOM_STEP_PER_NOTCH**notches)
                    self._set_cards_zoom(
                        min(_ZOOM_SCALE_MAX, max(_ZOOM_SCALE_MIN, new_scale))
                    )
                return True
        return super().eventFilter(watched, event)

    def _set_cards_zoom(self, scale: float) -> None:
        """Resize all person cards in place and reflow the grid columns."""
        self._cards_zoom_scale = scale
        cover_size = self._cover_size()
        for card in self._person_cards.values():
            card.set_cover_size(cover_size)
        if not self._current_stacks:
            return
        grid_cols = self._calc_grid_cols()
        if grid_cols != getattr(self, "_current_grid_cols", None):
            self._current_grid_cols = grid_cols
            self._place_cards_in_grid(grid_cols)

    def _populate_card_cover(
        self,
        card: PersonCardWidget,
        stack: PersonStackSummary,
        cover_lookups: dict[int, tuple[str, int, int, int, int]],
        tile_pixmaps: dict[str, QPixmap],
    ) -> None:
        """Set a card's cover pixmap, preferring cheap precomputed sources.

        Priority: a worker-rendered face-highlight PNG (already scaled and
        boxed off the main thread) > a pre-fetched tile crop > the card's own
        (main-thread, slower) fallback loading.
        """
        if stack.cover_face_bbox is not None:
            highlight_png = self._current_cover_highlight_pngs.get(stack.person_id)
            if highlight_png is not None:
                pixmap = QPixmap()
                if pixmap.loadFromData(highlight_png):
                    card.set_cover_pixmap(pixmap)
                    return
            # Worker-side render unavailable/failed — fall through to the
            # card's own (slower) main-thread raw-load-and-box fallback.
            card.load_cover_image()
            return

        # No highlight needed: use the pre-fetched tile crop when available,
        # deduplicating tile PNG reads per unique file.
        lookup = (
            cover_lookups.get(stack.cover_image_id) if stack.cover_image_id else None
        )
        if lookup is None:
            card.load_cover_image()
            return

        tile_path_str, x, y, w, h = lookup
        if tile_path_str not in tile_pixmaps:
            tile_pixmaps[tile_path_str] = QPixmap(tile_path_str)
        tile_pix = tile_pixmaps[tile_path_str]
        if tile_pix.isNull():
            card.load_cover_image()
            return
        card.set_cover_pixmap(tile_pix.copy(x, y, w, h))

    def get_current_stacks(self) -> list[PersonStackSummary]:
        """Get the current person stacks."""
        return self._current_stacks

    def get_current_person_id(self) -> int | None:
        """Return the person id currently shown in the detail view, if any."""
        return self._current_person_id

    def remove_images_from_current_gallery(self, image_ids: list[int]) -> None:
        """Drop the given images from the open detail view's grid immediately.

        Used after splitting photos out to a different person: cheaper and
        more immediate than re-fetching this person's stack, and keeps the
        grid from showing photos that no longer belong to the open person.
        """
        self._cluster_image_grid.remove_images(image_ids)
        model = self._cluster_image_grid.model()
        remaining = model.rowCount() if model is not None else 0
        self._image_count_label.setText(f"{remaining} images in this cluster")

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
        self._on_cluster_image_selection_changed([])

        # Update header - always set/clear the name input unconditionally
        if stack.person_name:
            self._person_name_input.setText(stack.person_name)
            self._person_name_input.setPlaceholderText("")
        else:
            self._person_name_input.setText("")
            self._person_name_input.setPlaceholderText("Name this person...")

        # Update image count
        self._image_count_label.setText(f"{stack.image_count} images in this cluster")

        # Show the cheap, already-in-hand capped sample immediately so the
        # grid isn't empty while the full list loads, then switch to the
        # detail view right away — none of this needs a DB round trip.
        self._cluster_image_grid.set_cluster_images(stack)
        self._stacked_widget.setCurrentIndex(1)

        # Load the full, uncapped gallery in the background.
        self._load_person_gallery(stack)

    def _load_person_gallery(self, stack: PersonStackSummary) -> None:
        """Load the full (uncapped) image gallery for a person, off the UI thread.

        `stack.sample_image_ids` is capped at a small `sample_limit` for the
        People tab grid's performance and would otherwise silently
        under-report large clusters (e.g. showing 20 images for a cluster
        the header says has 124). Fetching the true full list — plus its
        tile lookups — can take several hundred ms for a large cluster, so
        it runs on a worker instead of blocking the click that opens this
        view. Face-highlight compositing itself is *not* done here — see
        `ClusterImageModel._load_highlighted_tile_crop` — so this worker
        stays cheap regardless of gallery size.
        """
        if self._face_review_service is None:
            return

        self._gallery_request_epoch += 1
        worker = PersonGalleryWorker(
            self._face_review_service,
            self._tile_store,
            stack.person_id,
            self._gallery_request_epoch,
        )
        worker.signals.result_ready.connect(self._on_gallery_ready)
        worker.signals.error.connect(
            lambda msg: LOGGER.warning("Failed to load person gallery: %s", msg)
        )
        start_worker(worker, self._thread_pool)

    def _on_gallery_ready(
        self,
        image_ids: list[int],
        image_paths: list[str],
        bbox_by_image_id: dict[int, BoundingBox],
        tile_lookups: dict[int, ImageTileLookup],
        request_id: int,
    ) -> None:
        """Apply a person's full gallery, discarding stale/superseded results."""
        if request_id != self._gallery_request_epoch:
            return  # user has since opened a different person
        if not image_paths:
            return  # keep the capped sample shown rather than clearing it
        self._cluster_image_grid.set_images_list(
            image_paths,
            image_ids,
            tile_lookups=tile_lookups,
            bbox_by_image_id=bbox_by_image_id,
        )

    def _show_person_detail(self, person: object, faces: list[object]) -> None:
        """Show person detail view for the selected person."""
        # For now, just emit the signal to main window
        # The main window will handle showing the detail view
        person_id = getattr(person, "id", 0) if person is not None else 0
        self.person_selected.emit(person_id, faces)

    def _on_person_card_clicked(
        self, person_id: int, stack: PersonStackSummary
    ) -> None:
        """Handle person card click - show person detail view."""
        # For now, just emit the signal to main window
        self.person_selected.emit(person_id, stack)

    def _on_back_to_stacks(self) -> None:
        """Handle back to stacks button click."""
        self._stacked_widget.setCurrentIndex(0)
        self.back_to_stacks.emit()

    def _on_save_person_name(self) -> None:
        """Handle save person name."""
        if (
            not hasattr(self, "_current_stack")
            or getattr(self, "_current_stack", None) is None
        ):
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

    def _get_named_person_names(self, faces: list[FaceReviewItem]) -> list[str]:
        """Return all named persons in the library, for the reassign dropdown.

        Uses the cheap, direct persons-table query rather than
        `person_stacks()`, which also does a full faces/images library scan
        plus per-person cover-image selection — far more work than needed
        just to list display names, and expensive enough to notice when it
        reruns on every single thumbnail click in the cluster inspector.
        """
        if self._face_review_service is None:
            return []
        try:
            all_people = self._face_review_service.get_available_people()
            return sorted(p.name for p in all_people if p.name is not None)
        except Exception:  # noqa: BLE001
            # Fall back to names visible in this image
            return sorted(
                {
                    name
                    for name in (getattr(f, "person_name", None) for f in faces)
                    if name is not None
                }
            )

    def _cluster_image_grid_select_all(self) -> None:
        self._cluster_image_grid.select_all_images()

    def _cluster_image_grid_clear_selection(self) -> None:
        self._cluster_image_grid.clear_image_selection()

    def _on_cluster_image_selection_changed(self, image_ids: list[int]) -> None:
        """Update the split toolbar to reflect the current thumbnail selection."""
        count = len(image_ids)
        self._selection_count_label.setText(
            "No photos selected" if count == 0 else f"{count} photo(s) selected"
        )
        self._clear_image_selection_btn.setEnabled(count > 0)
        self._move_selected_images_btn.setEnabled(count > 0)

    def _on_move_selected_images_clicked(self) -> None:
        """Prompt for a target person and move the selected photos to them.

        This is the "split a mixed cluster" workflow: select the photos that
        don't belong (Ctrl/Shift-click, like a file manager), then move just
        those to a different — new or existing — person, without touching
        the rest of the current cluster.
        """
        if self._current_stack is None:
            return
        image_ids = self._cluster_image_grid.selected_image_ids()
        if not image_ids:
            return

        existing_names = self._get_named_person_names([])
        name, ok = QInputDialog.getItem(
            self,
            "Move Selected Photos",
            f"Move {len(image_ids)} photo(s) to (type a new name, or pick one):",
            existing_names,
            0,
            editable=True,
        )
        cleaned = name.strip() if ok else ""
        if not cleaned:
            return

        self.images_move_requested.emit(
            image_ids, self._current_stack.person_id, cleaned
        )

    def _on_cluster_image_activated(self, index: QModelIndex) -> None:
        """Handle image selection in the cluster grid."""
        model = self._cluster_image_grid.model()
        if not hasattr(model, "_image_paths"):
            return
        row = index.row()
        file_path: str | None = (
            getattr(model, "_image_paths", [])[row]
            if row < len(getattr(model, "_image_paths", []))
            else None
        )
        if file_path is None:
            return

        self._current_inspector_path = file_path

        # Load faces via service
        faces: list[FaceReviewItem] = []
        if self._face_review_service is not None:
            try:
                faces = self._face_review_service.faces_for_image_path(file_path)
            except Exception:  # noqa: BLE001
                faces = []

        person_names = self._get_named_person_names(faces)
        self._inspector.set_available_persons(person_names)
        self._inspector.load_image(file_path, faces)

    def reload_current_inspector_image(self) -> None:
        """Reload face data for the currently inspected image."""
        if self._current_inspector_path is None:
            return
        faces: list[FaceReviewItem] = []
        if self._face_review_service is not None:
            with contextlib.suppress(Exception):
                faces = self._face_review_service.faces_for_image_path(
                    self._current_inspector_path
                )

        person_names = self._get_named_person_names(faces)
        self._inspector.set_available_persons(person_names)
        self._inspector.load_image(self._current_inspector_path, faces)

    def _sorted_stacks(
        self, stacks: list[PersonStackSummary]
    ) -> list[PersonStackSummary]:
        """Order stacks by the sort mode selected in the toolbar.

        Date sorts use the cover (preview) image's capture date; face-size
        sorts use the cover face bbox's area relative to the cover image.
        Stacks missing that value always go last, largest cluster first.
        """
        mode = self._sort_combo.currentData()
        by_size = sorted(stacks, key=lambda s: s.face_count, reverse=True)
        if mode == self._SORT_CLUSTER_SIZE:
            return by_size
        if mode in (self._SORT_FACE_LARGEST, self._SORT_FACE_SMALLEST):
            measured = [s for s in by_size if s.cover_face_area_ratio is not None]
            unmeasured = [s for s in by_size if s.cover_face_area_ratio is None]
            measured.sort(
                key=lambda s: s.cover_face_area_ratio or 0.0,
                reverse=mode == self._SORT_FACE_LARGEST,
            )
            return measured + unmeasured
        if mode == self._SORT_SIMILARITY:
            # Stable sort: stacks without a rank keep size order at the end.
            return sorted(
                by_size,
                key=lambda s: (s.similarity_rank is None, s.similarity_rank or 0),
            )
        # Strip tzinfo so naive (SQLite) and aware dates compare safely.
        dated = [
            (s.cover_capture_date.replace(tzinfo=None), s)
            for s in by_size
            if s.cover_capture_date is not None
        ]
        undated = [s for s in by_size if s.cover_capture_date is None]
        dated.sort(key=lambda pair: pair[0], reverse=mode == self._SORT_DATE_NEWEST)
        return [s for _, s in dated] + undated

    def _on_sort_changed(self, _index: int) -> None:
        """Re-order the already-loaded stacks for the new sort mode."""
        if not self._current_stacks:
            return
        self.load_stacks(
            self._current_stacks,
            self._current_cover_lookups,
            self._current_cover_highlight_pngs,
        )

    def _on_show_unnamed_toggled(self, state: int) -> None:
        """Handle show unnamed clusters toggle."""
        self.show_unnamed_changed.emit(state == Qt.CheckState.Checked.value)

    def _on_select_mode_toggled(self, enabled: bool) -> None:  # noqa: FBT001
        """Enable/disable multi-select mode across all person cards."""
        self._selection_mode = enabled
        self._selected_person_ids.clear()
        for card in self._person_cards.values():
            card.set_selection_mode(enabled)
        self._selection_bar.setVisible(enabled)
        self._update_selection_bar()

    def _on_card_selection_toggled(self, person_id: int, selected: bool) -> None:  # noqa: FBT001
        """Track a card's selection state for bulk actions."""
        if selected:
            self._selected_person_ids.add(person_id)
        else:
            self._selected_person_ids.discard(person_id)
        self._update_selection_bar()

    def _update_selection_bar(self) -> None:
        """Refresh the selection count label and enable/disable bulk actions."""
        count = len(self._selected_person_ids)
        self._selection_count_label.setText(f"{count} selected")
        self._rename_selected_btn.setEnabled(count >= 1)
        self._exclude_selected_btn.setEnabled(count >= 1)

    def _on_clear_selection_clicked(self) -> None:
        """Clear the current selection without leaving selection mode."""
        for person_id in list(self._selected_person_ids):
            card = self._person_cards.get(person_id)
            if card is not None:
                card.set_selected(False)
        self._selected_person_ids.clear()
        self._update_selection_bar()

    def _on_card_rename_requested(self, person_id: int, name: str) -> None:
        """Forward an inline rename from a card to the main rename signal."""
        self.person_renamed.emit(person_id, name)

    def _on_rename_selected_clicked(self) -> None:
        """Name the selected person(s) via a quick prompt.

        Same tag-vs-rename split as a card's own double-click: an untagged
        (always single-cluster) selection applies a name tag to that
        cluster, a named one just renames the tag cosmetically. With
        several selected, every untagged one is tagged with the same name.
        """
        if len(self._selected_person_ids) > 1:
            self._tag_selected_clusters()
            return
        if len(self._selected_person_ids) != 1:
            return
        person_id = next(iter(self._selected_person_ids))
        stack = next(
            (s for s in self._current_stacks if s.person_id == person_id), None
        )
        current_name = stack.person_name if stack is not None else ""
        new_name, ok = QInputDialog.getText(
            self, "Name Person", "Name:", text=current_name or ""
        )
        if not ok:
            return
        cleaned = new_name.strip()
        if not cleaned or cleaned == current_name:
            return
        if stack is not None and stack.person_name:
            self.person_renamed.emit(person_id, cleaned)
        elif stack is not None and stack.cluster_id is not None:
            self.cluster_tag_requested.emit(stack.cluster_id, cleaned)

    def _tag_selected_clusters(self) -> None:
        """Tag every selected untagged cluster with one shared name."""
        cluster_ids = [
            s.cluster_id
            for s in self._current_stacks
            if s.person_id in self._selected_person_ids
            and not s.person_name
            and s.cluster_id is not None
        ]
        skipped = len(self._selected_person_ids) - len(cluster_ids)
        if not cluster_ids:
            QMessageBox.information(
                self,
                "Name Selected",
                "The selected people already have names. Select untagged "
                "clusters to tag them together.",
            )
            return
        new_name, ok = QInputDialog.getText(
            self,
            "Name Selected",
            f"Name for {len(cluster_ids)} cluster(s)"
            + (f" ({skipped} already named, skipped)" if skipped else "")
            + ":",
        )
        cleaned = new_name.strip()
        if not ok or not cleaned:
            return
        self.clusters_tag_requested.emit(cluster_ids, cleaned)

    def _on_exclude_selected_clicked(self) -> None:
        """Bulk-exclude the selected people as not of interest."""
        selected_ids = list(self._selected_person_ids)
        if not selected_ids:
            return
        self.people_exclude_requested.emit(selected_ids)

    def _calc_grid_cols(self) -> int:
        """Calculate how many cards fit in the current viewport width."""
        if not hasattr(self, "_stacks_scroll_area"):
            return 4
        viewport_w = self._stacks_scroll_area.viewport().width()
        return max(
            1,
            (viewport_w + self._CARD_SPACING)
            // (self._card_width() + self._CARD_SPACING),
        )

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
            if (
                hasattr(self, "_current_grid_cols")
                and new_cols != self._current_grid_cols
            ):
                self.load_stacks(
                    self._current_stacks,
                    self._current_cover_lookups,
                    self._current_cover_highlight_pngs,
                )

    def set_threshold(self, threshold: int) -> None:
        """Set the threshold for filtering stacks."""
        self._current_threshold = threshold
        # Reload stacks with new threshold (this will be handled by main window)
