from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QModelIndex, QSize, Signal, QTimer, QEvent, Qt
from PySide6.QtGui import QShowEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QListView

from photo_app.app.models.photo_grid_model import PhotoGridModel
from photo_app.app.widgets.photo_grid_delegate import PhotoGridDelegate

if TYPE_CHECKING:
    from PySide6.QtCore import QResizeEvent


class PhotoGridWidget(QListView):
    """Icon-mode photo list optimized for lazy loading + pagination."""

    photoActivated = Signal(int)
    flagChanged = Signal(object, object)  # index, flag_value

    def __init__(self, model: PhotoGridModel, parent: QListView | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setSpacing(10)
        self.setIconSize(QSize(180, 180))
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
        self.itemDelegate().flagChanged.connect(self._on_flag_changed)
        
        # Enable keyboard events
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        # Enable mouse tracking for hover events
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Handle resize events to dynamically adjust grid size for even distribution."""
        super().resizeEvent(event)
        self._update_grid_size()

    def _update_grid_size(self) -> None:
        """Calculate and set grid size to fill viewport width with even distribution."""
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return
            
        # Use the configured thumbnail size from the model
        model = self.model()
        if model is not None and hasattr(model, 'thumbnail_size'):
            thumbnail_size = model.thumbnail_size
        else:
            # Fallback to default size
            thumbnail_size = (128, 128)
            
        thumb_w, thumb_h = thumbnail_size
        
        # Calculate how many thumbnails fit per row (minimum 1)
        cols = max(1, viewport_width // thumb_w)
        
        # Calculate cell width to fill the viewport evenly
        cell_w = viewport_width // cols
        
        # Set grid size - add some padding for spacing and labels
        label_padding = 20  # Space for labels if any
        self.setGridSize(QSize(cell_w, thumb_h + label_padding))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
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
            last = bottom_index.row() if bottom_index.isValid() else max(first, model.rowCount() - 1)
        
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
        if hasattr(self, 'flagChanged'):
            self.flagChanged.emit(index, flag_value)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard shortcuts for flagging images."""
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
        key = event.key()
        modifiers = event.modifiers()
        
        if key == Qt.Key.Key_P and modifiers == Qt.KeyboardModifier.NoModifier:
            # P = keep
            self._on_flag_changed(current_index, "keep")
        elif key == Qt.Key.Key_X and modifiers == Qt.KeyboardModifier.NoModifier:
            # X = discard
            self._on_flag_changed(current_index, "discard")
        elif key == Qt.Key.Key_U and modifiers == Qt.KeyboardModifier.NoModifier:
            # U = undecided
            self._on_flag_changed(current_index, "undecided")
        elif key == Qt.Key.Key_Backspace and modifiers == Qt.KeyboardModifier.NoModifier:
            # Backspace = clear flag
            self._on_flag_changed(current_index, None)
        else:
            super().keyPressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move events to track hovered index."""
        index = self.indexAt(event.pos())
        delegate = self.itemDelegate()
        if isinstance(delegate, PhotoGridDelegate):
            old = delegate._hovered_index
            delegate._hovered_index = index if index.isValid() else None
            if old != delegate._hovered_index:
                if old and old.isValid():
                    self.viewport().update(self.visualRect(old))
                if index.isValid():
                    self.viewport().update(self.visualRect(index))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        """Handle mouse leave events to clear hovered index."""
        delegate = self.itemDelegate()
        if isinstance(delegate, PhotoGridDelegate):
            old = delegate._hovered_index
            delegate._hovered_index = None
            delegate._hover_buttons_visible = False
            if old and old.isValid():
                self.viewport().update(self.visualRect(old))
        super().leaveEvent(event)
