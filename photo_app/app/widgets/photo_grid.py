from __future__ import annotations

from PySide6.QtCore import QModelIndex, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QListView

from photo_app.app.models.photo_grid_model import PhotoGridModel


class PhotoGridWidget(QListView):
    """Icon-mode photo list optimized for lazy loading + pagination."""

    photoActivated = Signal(int)

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
        
        # Timer to debounce tile requests after model changes
        self._tile_request_timer = QTimer()
        self._tile_request_timer.setSingleShot(True)
        self._tile_request_timer.setInterval(50)  # 50ms debounce
        self._tile_request_timer.timeout.connect(self._notify_visible_items)
        
        self._first_show = True
        
        # Connect model signals to trigger tile loading when data changes
        if isinstance(model, PhotoGridModel):
            # Use lambda to handle rowsInserted parameters (parent, first, last)
            model.rowsInserted.connect(lambda parent, first, last: self._on_model_structure_changed())

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
