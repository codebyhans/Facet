from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSize,
    Qt,
)
from PySide6.QtGui import QPixmap

if TYPE_CHECKING:
    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore

LOGGER = logging.getLogger(__name__)

class ClusterImageModel(QAbstractListModel):
    """Model for cluster image gallery in person detail view."""

    def __init__(
        self,
        image_paths: list[str],
        image_ids: list[int] | None = None,
        tile_store: ThumbnailTileStore | None = None,
        parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._image_paths = image_paths
        self._image_ids = image_ids or []
        self._tile_store = tile_store
        self._pixmap_cache: dict[int, QPixmap | None] = {}  # Cache for loaded pixmaps
        self._max_cache_size = 50  # Maximum number of pixmaps to cache

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex | None = None,
    ) -> int:
        parent = parent or QModelIndex()
        if parent.isValid():
            return 0
        return len(self._image_paths)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._image_paths):
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            return self._image_paths[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            return self._load_thumbnail(index.row())
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(80, 80)
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_image_paths(self, image_paths: list[str]) -> None:
        """Update the image paths and reset the model."""
        self.beginResetModel()
        self._image_paths = image_paths
        self.endResetModel()

    def set_images(self, image_paths: list[str], image_ids: list[int]) -> None:
        """Update both image paths and IDs and reset the model."""
        self.beginResetModel()
        self._image_paths = image_paths
        self._image_ids = image_ids
        self._pixmap_cache.clear()  # Clear cache when images change
        self._prefetch_tiles()   # populate cache BEFORE endResetModel triggers paint
        self.endResetModel()

    def _prefetch_tiles(self) -> None:
        """Batch-load all tile crops for the current image set before first paint."""
        if not self._tile_store or not self._image_ids:
            return

        # One DB query for all image IDs
        batch = self._tile_store.get_image_tiles_batch(self._image_ids)

        # Load each unique tile PNG once, crop per image
        tile_pixmaps: dict[str, QPixmap] = {}
        for row, image_id in enumerate(self._image_ids):
            lookup = batch.get(image_id)
            if lookup is None or not lookup.tile_path.exists():
                self._pixmap_cache[row] = None
                continue
            tile_key = str(lookup.tile_path)
            if tile_key not in tile_pixmaps:
                tile_pixmaps[tile_key] = QPixmap(tile_key)
            tile_pix = tile_pixmaps[tile_key]
            if tile_pix.isNull():
                self._pixmap_cache[row] = None
                continue
            cropped = tile_pix.copy(lookup.x, lookup.y, lookup.width, lookup.height)
            self._pixmap_cache[row] = cropped if not cropped.isNull() else None

    def _ensure_cache_size(self) -> None:
        """Ensure cache doesn't exceed maximum size by removing oldest entries."""
        while len(self._pixmap_cache) > self._max_cache_size:
            # Remove the first item (oldest) from cache
            oldest_key = next(iter(self._pixmap_cache))
            del self._pixmap_cache[oldest_key]

    def _load_thumbnail(self, row: int) -> QPixmap | None:
        """Load thumbnail for the image at the given row using thumbnail tiles."""
        if row < 0 or row >= len(self._image_paths):
            return None

        # Check cache first
        if row in self._pixmap_cache:
            return self._pixmap_cache[row]

        # Try to use thumbnail tile first for performance
        if self._tile_store and row < len(self._image_ids):
            image_id = self._image_ids[row]
            try:
                tile_lookup = self._tile_store.get_image_tile(image_id)
                if tile_lookup and tile_lookup.tile_path.exists():
                    # Load the tile and crop the specific thumbnail
                    tile_pixmap = QPixmap(str(tile_lookup.tile_path))
                    if not tile_pixmap.isNull():
                        # Crop the specific thumbnail from the tile
                        cropped = tile_pixmap.copy(
                            tile_lookup.x,
                            tile_lookup.y,
                            tile_lookup.width,
                            tile_lookup.height
                        )
                        # Cache the result
                        self._pixmap_cache[row] = cropped
                        self._ensure_cache_size()
                        return cropped
            except Exception:
                LOGGER.exception("Failed to load cached tile for image %s", image_id)

        # Fallback to loading full image if tile system fails
        image_path = self._image_paths[row]
        try:
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    80, 80,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                # Cache the result
                self._pixmap_cache[row] = scaled
                self._ensure_cache_size()
                return scaled
        except Exception:
            LOGGER.exception("Failed to load thumbnail for %s", image_path)
        return None
