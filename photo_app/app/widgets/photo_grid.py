from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QListView, QMenu

from photo_app.app.models.photo_grid_model import PhotoGridModel
from photo_app.app.widgets.photo_grid_delegate import PhotoGridDelegate

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from PySide6.QtGui import (
        QContextMenuEvent,
        QKeyEvent,
        QMouseEvent,
        QResizeEvent,
        QShowEvent,
        QWheelEvent,
    )

    from photo_app.domain.models import Collection

# Ctrl+scroll zoom bounds, relative to the model's configured thumbnail size
_ZOOM_SCALE_MIN = 0.35
_ZOOM_SCALE_MAX = 3.0
_ZOOM_STEP_PER_NOTCH = 1.1
_THUMB_PX_MIN = 32
_THUMB_PX_MAX = 640


class PhotoGridWidget(QListView):
    """Icon-mode photo list optimized for lazy loading + pagination."""

    photoActivated = Signal(int)  # noqa: N815
    flagChanged = Signal(object, object)  # noqa: N815  # index, flag_value
    deletePhotosRequested = Signal(list)  # noqa: N815  # list[int] of image_ids
    # collection_id, list[int] of image_ids
    addToCollectionRequested = Signal(int, list)  # noqa: N815
    removeFromCollectionRequested = Signal(int, list)  # noqa: N815
    newCollectionRequested = Signal(list)  # noqa: N815  # list[int] of image_ids

    def __init__(self, model: PhotoGridModel, parent: QListView | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setSpacing(10)
        self.setIconSize(QSize(180, 180))
        self._zoom_scale = 1.0
        self._list_collections: Callable[[], list[Collection]] | None = None
        self._collections_containing: (
            Callable[[Sequence[int]], list[Collection]] | None
        ) = None
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.doubleClicked.connect(self._on_double_click)

        # Set custom delegate for flag badges and hover buttons
        self.setItemDelegate(PhotoGridDelegate(self))

        # Timer to debounce tile requests after model changes
        self._tile_request_timer = QTimer()
        self._tile_request_timer.setSingleShot(True)
        self._tile_request_timer.setInterval(50)  # 50ms debounce
        self._tile_request_timer.timeout.connect(self._notify_visible_items)

        self._first_show = True

        # Connect model signals to trigger tile loading when data changes
        model.rowsInserted.connect(self._on_model_structure_changed)

        # Connect flag change signal from delegate
        delegate = self.itemDelegate()
        if hasattr(delegate, "flagChanged"):
            delegate.flagChanged.connect(self._on_flag_changed)

        # Enable keyboard events
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Enable mouse tracking for hover events
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        """Handle resize events to dynamically adjust grid size for even distribution."""
        super().resizeEvent(event)
        self._update_grid_size()

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        """Handle Ctrl+scroll to zoom the thumbnail grid in/out."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            notches = event.angleDelta().y() / 120
            if notches:
                new_scale = self._zoom_scale * (_ZOOM_STEP_PER_NOTCH**notches)
                self._zoom_scale = min(_ZOOM_SCALE_MAX, max(_ZOOM_SCALE_MIN, new_scale))
                self._update_grid_size()
            event.accept()
            return
        super().wheelEvent(event)

    def _effective_thumbnail_size(self) -> tuple[int, int]:
        """Return the current on-screen thumbnail size (model size x zoom)."""
        model = self.model()
        if model is not None and hasattr(model, "thumbnail_size"):
            base_w, base_h = model.thumbnail_size
        else:
            base_w, base_h = (128, 128)

        w = max(_THUMB_PX_MIN, min(_THUMB_PX_MAX, round(base_w * self._zoom_scale)))
        h = max(_THUMB_PX_MIN, min(_THUMB_PX_MAX, round(base_h * self._zoom_scale)))
        return w, h

    def _update_grid_size(self) -> None:
        """Calculate and set grid size to fill viewport width with even distribution."""
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return

        thumb_w, thumb_h = self._effective_thumbnail_size()

        # Calculate how many thumbnails fit per row (minimum 1)
        cols = max(1, viewport_width // thumb_w)

        # Calculate cell width to fill the viewport evenly
        cell_w = viewport_width // cols

        # Set grid size - add some padding for spacing and labels
        label_padding = 20  # Space for labels if any
        new_grid_size = QSize(cell_w, thumb_h + label_padding)
        new_icon_size = QSize(thumb_w, thumb_h)
        size_changed = (
            new_grid_size != self.gridSize() or new_icon_size != self.iconSize()
        )
        self.setGridSize(new_grid_size)
        self.setIconSize(new_icon_size)
        if size_changed:
            # setUniformItemSizes() caches the delegate's sizeHint()-derived item
            # size the first time it's computed; changing iconSize/gridSize alone
            # doesn't invalidate that cache, so force a relayout to pick it up.
            self.doItemsLayout()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        """Handle widget becoming visible - request tiles if this is first show."""
        super().showEvent(event)
        # On first show, request tiles for any existing items
        if self._first_show:
            self._first_show = False
            self._notify_visible_items()

    def _on_model_structure_changed(self) -> None:
        """Called when model rows are inserted - schedule tile loading."""
        # Debounce to allow model to finish updating
        self._tile_request_timer.start()

    def _notify_visible_items(self) -> None:
        """Notify model about visible items to trigger tile loading."""
        model = self.model()
        if not isinstance(model, PhotoGridModel):
            return

        # Get viewport dimensions
        viewport_rect = self.viewport().rect()
        if viewport_rect.isEmpty():
            # Viewport not yet sized - use full range as fallback
            first = 0
            last = max(0, model.rowCount() - 1)
        else:
            # Normal case - get visible range
            top_index = self.indexAt(viewport_rect.topLeft())
            bottom_index = self.indexAt(viewport_rect.bottomLeft())
            first = max(0, top_index.row()) if top_index.isValid() else 0
            last = (
                bottom_index.row()
                if bottom_index.isValid()
                else max(first, model.rowCount() - 1)
            )

        # Always request tiles for the range, even if empty
        model.notify_visible_rows(first, max(first, last))

    def _on_scroll(self) -> None:
        """Handle scroll events to notify about visible items."""
        self._notify_visible_items()

    def _on_double_click(self, index: QModelIndex) -> None:
        """Handle double-click to activate image."""
        model = self.model()
        if not isinstance(model, PhotoGridModel):
            return
        image_id = model.data(index, PhotoGridModel.ImageIdRole)
        if isinstance(image_id, int):
            self.photoActivated.emit(image_id)

    def _on_flag_changed(self, index: QModelIndex, flag_value: str | None) -> None:
        """Handle flag change from delegate."""
        # Emit a signal to notify the view model about the flag change
        if hasattr(self, "flagChanged"):
            self.flagChanged.emit(index, flag_value)

    def _selected_image_ids(self) -> list[int]:
        """Return image IDs for all currently selected rows."""
        model = self.model()
        if not isinstance(model, PhotoGridModel):
            return []
        ids: list[int] = []
        for index in self.selectedIndexes():
            image_id = model.data(index, PhotoGridModel.ImageIdRole)
            if isinstance(image_id, int):
                ids.append(image_id)
        return ids

    def set_collection_sources(
        self,
        list_collections: Callable[[], list[Collection]],
        collections_containing: Callable[[Sequence[int]], list[Collection]],
    ) -> None:
        """Provide the lookups that populate the collection context menus."""
        self._list_collections = list_collections
        self._collections_containing = collections_containing

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard shortcuts for flagging and deleting images."""
        key = Qt.Key(event.key())
        modifiers = event.modifiers()

        if key == Qt.Key.Key_Delete and modifiers == Qt.KeyboardModifier.ShiftModifier:
            image_ids = self._selected_image_ids()
            if image_ids:
                self.deletePhotosRequested.emit(image_ids)
            return

        if not self.currentIndex().isValid():
            super().keyPressEvent(event)
            return

        # Get the current index
        current_index = self.currentIndex()
        model = self.model()

        if not isinstance(model, PhotoGridModel):
            super().keyPressEvent(event)
            return

        # Handle flagging shortcuts
        if modifiers == Qt.KeyboardModifier.NoModifier:
            flag_by_key: dict[Qt.Key, str | None] = {
                Qt.Key.Key_P: "keep",
                Qt.Key.Key_X: "discard",
                Qt.Key.Key_U: "undecided",
                Qt.Key.Key_Backspace: None,
            }
            if key in flag_by_key:
                flag_value = flag_by_key[key]
                targets = self.selectedIndexes() or [current_index]
                for index in targets:
                    self._on_flag_changed(index, flag_value)
                return

        super().keyPressEvent(event)

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Show collection and permanent-delete actions for the selected photo(s)."""
        index = self.indexAt(event.pos())
        if index.isValid() and not self.selectionModel().isSelected(index):
            self.setCurrentIndex(index)
            self.selectionModel().select(
                index, self.selectionModel().SelectionFlag.ClearAndSelect
            )

        image_ids = self._selected_image_ids()
        if not image_ids:
            return

        label = (
            "Delete Permanently…"
            if len(image_ids) == 1
            else f"Delete {len(image_ids)} Photos Permanently…"
        )
        menu = QMenu(self)
        self._add_collection_actions(menu, image_ids)
        action = menu.addAction(label)
        action.triggered.connect(lambda: self.deletePhotosRequested.emit(image_ids))
        menu.exec(event.globalPos())

    def _add_collection_actions(self, menu: QMenu, image_ids: list[int]) -> None:
        if self._list_collections is None or self._collections_containing is None:
            return

        add_menu = menu.addMenu("Add to Collection")
        for collection in self._list_collections():
            if collection.id is None:
                continue
            add_menu.addAction(collection.name).triggered.connect(
                lambda _checked=False, cid=collection.id: (
                    self.addToCollectionRequested.emit(cid, image_ids)
                )
            )
        if not add_menu.isEmpty():
            add_menu.addSeparator()
        add_menu.addAction("New Collection…").triggered.connect(
            lambda: self.newCollectionRequested.emit(image_ids)
        )

        containing = [
            c for c in self._collections_containing(image_ids) if c.id is not None
        ]
        remove_menu = menu.addMenu("Remove from Collection")
        remove_menu.setEnabled(bool(containing))
        for collection in containing:
            remove_menu.addAction(collection.name).triggered.connect(
                lambda _checked=False, cid=collection.id: (
                    self.removeFromCollectionRequested.emit(cid, image_ids)
                )
            )
        menu.addSeparator()

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move events to track hovered index."""
        index = self.indexAt(event.pos())
        delegate = self.itemDelegate()
        if isinstance(delegate, PhotoGridDelegate):
            old = getattr(delegate, "_hovered_index", None)
            delegate.set_hovered_index(index if index.isValid() else None)
            if old != getattr(delegate, "_hovered_index", None):
                if old and old.isValid():
                    self.viewport().update(self.visualRect(old))
                if index.isValid():
                    self.viewport().update(self.visualRect(index))
        super().mouseMoveEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        """Handle mouse leave events to clear hovered index."""
        delegate = self.itemDelegate()
        if isinstance(delegate, PhotoGridDelegate):
            old = getattr(delegate, "_hovered_index", None)
            delegate.set_hovered_index(None)
            delegate.set_hover_buttons_visible(visible=False)
            if old and old.isValid():
                self.viewport().update(self.visualRect(old))
        super().leaveEvent(event)
