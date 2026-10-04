from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter
from PySide6.QtWidgets import QListView, QStyledItemDelegate

from photo_app.app.models.cluster_image_model import ClusterImageModel

if TYPE_CHECKING:
    from PySide6.QtCore import QItemSelection, QModelIndex, QPersistentModelIndex
    from PySide6.QtGui import QResizeEvent, QWheelEvent
    from PySide6.QtWidgets import QStyleOptionViewItem

    from photo_app.domain.value_objects import BoundingBox
    from photo_app.infrastructure.thumbnail_tiles import (
        ImageTileLookup,
        ThumbnailTileStore,
    )
    from photo_app.services.face_review_service import PersonStackSummary

# Ctrl+scroll zoom bounds, relative to the base 80px thumbnail
_BASE_THUMB_PX = 80
_ZOOM_SCALE_MIN = 1.0
_ZOOM_SCALE_MAX = 6.0
_ZOOM_STEP_PER_NOTCH = 1.15


class _ScalingThumbnailDelegate(QStyledItemDelegate):
    """Paint thumbnails scaled to the cell, so zoom can exceed native tile size.

    Qt's built-in icon painting never enlarges a raster pixmap past its native
    resolution, so the icon is blanked and the pixmap drawn manually instead.
    """

    @override
    def initStyleOption(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().initStyleOption(option, index)
        option.icon = QIcon()
        option.decorationSize = QSize(0, 0)

    @override
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().paint(painter, option, index)
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if pixmap is None or pixmap.isNull():
            return
        target = option.rect.adjusted(4, 4, -4, -4)
        scaled = pixmap.scaled(
            target.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = target.left() + (target.width() - scaled.width()) // 2
        y = target.top() + (target.height() - scaled.height()) // 2
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(x, y, scaled)


class ClusterImageGridWidget(QListView):
    """Icon-mode photo list for cluster image gallery in person detail view."""

    selection_changed = Signal(list)  # list[int] of selected image_ids

    def __init__(
        self,
        tile_store: ThumbnailTileStore | None = None,
        parent: QListView | None = None,
    ) -> None:
        super().__init__(parent)
        self._tile_store = tile_store
        self.setModel(ClusterImageModel([], tile_store=self._tile_store))
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setSpacing(8)
        self.setIconSize(QSize(_BASE_THUMB_PX, _BASE_THUMB_PX))
        self.setItemDelegate(_ScalingThumbnailDelegate(self))
        self._zoom_scale = 1.0
        selection_model = self.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(self._on_selection_changed)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        """Handle resize events to dynamically adjust grid size for even distribution."""
        super().resizeEvent(event)
        self._update_grid_size()

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        """Handle Ctrl+scroll to zoom the thumbnails in/out."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            notches = event.angleDelta().y() / 120
            if notches:
                new_scale = self._zoom_scale * (_ZOOM_STEP_PER_NOTCH**notches)
                self._zoom_scale = min(_ZOOM_SCALE_MAX, max(_ZOOM_SCALE_MIN, new_scale))
                self._update_grid_size()
            event.accept()
            return
        super().wheelEvent(event)

    def _update_grid_size(self) -> None:
        """Calculate and set grid size to fill viewport width with even distribution."""
        viewport_width = self.viewport().width()
        if viewport_width <= 0:
            return

        thumb_w = thumb_h = round(_BASE_THUMB_PX * self._zoom_scale)
        self.setIconSize(QSize(thumb_w, thumb_h))

        # Calculate how many thumbnails fit per row (minimum 1)
        cols = max(1, viewport_width // thumb_w)

        # Calculate cell width to fill the viewport evenly
        cell_w = viewport_width // cols

        # Set grid size - add some padding for spacing
        spacing_padding = 16  # Space for spacing between items
        self.setGridSize(QSize(cell_w, thumb_h + spacing_padding))

    def set_cluster_images(self, stack: PersonStackSummary) -> None:
        """Set the cluster images for display from a stack's capped sample.

        Prefer `set_images_list` with the full, uncapped set when available
        (e.g. via `FaceReviewService.images_for_person`) — this method only
        shows `sample_image_ids`, which is capped at a small `sample_limit`
        for the People tab grid's performance and will under-report large
        clusters.
        """
        self.set_images_list(
            list(stack.sample_image_paths), list(stack.sample_image_ids)
        )

    def set_images_list(
        self,
        image_paths: list[str],
        image_ids: list[int],
        tile_lookups: dict[int, ImageTileLookup] | None = None,
        bbox_by_image_id: dict[int, BoundingBox] | None = None,
    ) -> None:
        """Set the cluster images for display from explicit, uncapped lists.

        `bbox_by_image_id` (this person's face bbox per image) lets a
        thumbnail show *which* face in a group photo is this person — the
        model composites that box onto the already-cached tile crop
        lazily, per row, only as each one is actually painted, rather than
        this widget needing to know about pre-rendered PNGs itself.
        """
        model = self.model()
        if isinstance(model, ClusterImageModel):
            model.set_images(
                image_paths,
                image_ids,
                tile_lookups=tile_lookups,
                bbox_by_image_id=bbox_by_image_id,
            )

    def remove_images(self, image_ids: list[int]) -> None:
        """Drop the given images from the grid immediately (no re-fetch)."""
        model = self.model()
        if isinstance(model, ClusterImageModel):
            model.remove_images(image_ids)

    def selected_image_ids(self) -> list[int]:
        """Return the image ids of the currently selected thumbnails."""
        model = self.model()
        if not isinstance(model, ClusterImageModel):
            return []
        ids = (model.image_id_at(index.row()) for index in self.selectedIndexes())
        return [image_id for image_id in ids if image_id is not None]

    def select_all_images(self) -> None:
        """Select every thumbnail currently shown."""
        self.selectAll()

    def clear_image_selection(self) -> None:
        """Clear the current thumbnail selection."""
        self.clearSelection()

    def _on_selection_changed(
        self, _selected: QItemSelection, _deselected: QItemSelection
    ) -> None:
        self.selection_changed.emit(self.selected_image_ids())
