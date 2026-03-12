from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QListView

from photo_app.app.models.cluster_image_model import ClusterImageModel

if TYPE_CHECKING:
    from photo_app.app.widgets.people_browser import PersonStackSummary


class ClusterImageGridWidget(QListView):
    """Icon-mode photo list for cluster image gallery in person detail view."""

    def __init__(self, parent: QListView | None = None) -> None:
        super().__init__(parent)
        self.setModel(ClusterImageModel([]))
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setSpacing(8)
        self.setIconSize(QSize(80, 80))

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Handle resize events to dynamically adjust grid size for even distribution."""
        super().resizeEvent(event)
        self._update_grid_size()

    def _update_grid_size(self) -> None:
        """Calculate and set grid size to fill viewport width with even distribution."""
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return
            
        # Use 80x80 as base thumbnail size (matching current implementation)
        thumb_w, thumb_h = 80, 80
        
        # Calculate how many thumbnails fit per row (minimum 1)
        cols = max(1, viewport_width // thumb_w)
        
        # Calculate cell width to fill the viewport evenly
        cell_w = viewport_width // cols
        
        # Set grid size - add some padding for spacing
        spacing_padding = 16  # Space for spacing between items
        self.setGridSize(QSize(cell_w, thumb_h + spacing_padding))

    def set_cluster_images(self, stack: PersonStackSummary) -> None:
        """Set the cluster images for display."""
        model = self.model()
        if isinstance(model, ClusterImageModel):
            model.set_image_paths(list(stack.sample_image_paths))
