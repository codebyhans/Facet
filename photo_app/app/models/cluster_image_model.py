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
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

from photo_app.domain.value_objects import scale_bbox_to_fit_cell

_HIGHLIGHT_COLOR = QColor(74, 158, 255)  # matches image_loading.py's PIL box color

if TYPE_CHECKING:
    from photo_app.domain.value_objects import BoundingBox
    from photo_app.infrastructure.thumbnail_tiles import (
        ImageTileLookup,
        ThumbnailTileStore,
    )

LOGGER = logging.getLogger(__name__)


class ClusterImageModel(QAbstractListModel):
    """Model for cluster image gallery in person detail view."""

    def __init__(
        self,
        image_paths: list[str],
        image_ids: list[int] | None = None,
        tile_store: ThumbnailTileStore | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._image_paths = image_paths
        self._image_ids = image_ids or []
        self._tile_store = tile_store
        self._pixmap_cache: dict[int, QPixmap | None] = {}  # Cache for loaded pixmaps
        self._max_cache_size = 50  # Maximum number of pixmaps to cache
        self._tile_lookups: dict[int, ImageTileLookup] = {}
        self._tile_pixmap_cache: dict[str, QPixmap] = {}  # decoded tile sheets, by path
        # image_id -> this person's face bbox in that image (in the
        # image's own EXIF-corrected pixel space). Used to composite the
        # highlight box onto the tile crop lazily, per row, in
        # `_load_highlighted_tile_crop` — never a full-resolution decode.
        self._bbox_by_image_id: dict[int, BoundingBox] = {}

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
        if (
            not index.isValid()
            or index.row() < 0
            or index.row() >= len(self._image_paths)
        ):
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            return self._image_paths[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            return self._load_thumbnail(index.row())
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(80, 80)
        return None

    def image_id_at(self, row: int) -> int | None:
        """Return the image id for a row, or None if out of range."""
        if row < 0 or row >= len(self._image_ids):
            return None
        return self._image_ids[row]

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_image_paths(self, image_paths: list[str]) -> None:
        """Update the image paths and reset the model."""
        self.beginResetModel()
        self._image_paths = image_paths
        self.endResetModel()

    def remove_images(self, image_ids: list[int]) -> None:
        """Drop the given image ids from the model in place."""
        removed = set(image_ids)
        if not removed:
            return
        keep_rows = [
            row
            for row, image_id in enumerate(self._image_ids)
            if image_id not in removed
        ]
        self.beginResetModel()
        self._image_paths = [self._image_paths[row] for row in keep_rows]
        self._image_ids = [self._image_ids[row] for row in keep_rows]
        self._pixmap_cache.clear()
        self._tile_pixmap_cache.clear()
        self.endResetModel()

    def set_images(
        self,
        image_paths: list[str],
        image_ids: list[int],
        tile_lookups: dict[int, ImageTileLookup] | None = None,
        bbox_by_image_id: dict[int, BoundingBox] | None = None,
    ) -> None:
        """Update both image paths and IDs and reset the model.

        Only fetches tile *lookup metadata* up front (one cheap DB query) —
        actually decoding/cropping/highlighting tile images is deferred to
        `_load_thumbnail`, so it happens lazily, only for rows Qt actually
        asks to paint (i.e. the visible viewport), rather than eagerly for
        the entire cluster which could be thousands of images.

        Pass `tile_lookups` when the caller already fetched it (e.g. a
        background worker did so alongside the image list itself) to skip
        this otherwise-redundant query, which can itself take hundreds of ms
        for a large cluster. `bbox_by_image_id` (this person's face bbox per
        image) drives the lazy highlight compositing in
        `_load_highlighted_tile_crop` — cheap enough (a small crop + a
        drawn rectangle, no full-resolution decode) to do per visible row
        on the main thread rather than pre-rendering PNGs for every image
        up front.
        """
        self.beginResetModel()
        self._image_paths = image_paths
        self._image_ids = image_ids
        self._pixmap_cache.clear()
        self._tile_pixmap_cache.clear()
        self._bbox_by_image_id = bbox_by_image_id or {}
        if tile_lookups is not None:
            self._tile_lookups = tile_lookups
        else:
            self._tile_lookups = (
                self._tile_store.get_image_tiles_batch(image_ids)
                if self._tile_store and image_ids
                else {}
            )
        self.endResetModel()

    def _ensure_cache_size(self) -> None:
        """Ensure cache doesn't exceed maximum size by removing oldest entries."""
        while len(self._pixmap_cache) > self._max_cache_size:
            # Remove the first item (oldest) from cache
            oldest_key = next(iter(self._pixmap_cache))
            del self._pixmap_cache[oldest_key]

    def _load_highlighted_tile_crop(self, image_id: int) -> QPixmap | None:
        """Crop this row's tile cell and draw this person's face box on it.

        Entirely derived from the already-cached, already-tiny tile crop —
        never a full-resolution decode — so it's cheap enough to do lazily,
        per visible row, on the main thread, instead of pre-rendering a PNG
        per image up front for a gallery that can run to thousands of
        photos. Shares its scale/offset geometry with the PIL-based
        `image_loading.render_face_highlight_from_tile` via
        `scale_bbox_to_fit_cell`, so both agree on where the box lands.
        """
        bbox = self._bbox_by_image_id.get(image_id)
        if bbox is None:
            return None
        tile_lookup = self._tile_lookups.get(image_id)
        if (
            tile_lookup is None
            or tile_lookup.image_width <= 0
            or tile_lookup.image_height <= 0
        ):
            return None
        crop = self._load_tile_crop(image_id)
        if crop is None or crop.isNull():
            return None

        x0, y0, x1, y1 = scale_bbox_to_fit_cell(
            bbox,
            tile_lookup.image_width,
            tile_lookup.image_height,
            crop.width(),
            crop.height(),
        )
        highlighted = QPixmap(crop)
        painter = QPainter(highlighted)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(_HIGHLIGHT_COLOR)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        finally:
            painter.end()
        return highlighted

    def _load_tile_crop(self, image_id: int) -> QPixmap | None:
        """Decode this row's plain (unhighlighted) tile crop, if any."""
        try:
            tile_lookup = self._tile_lookups.get(image_id)
            if not tile_lookup or not tile_lookup.tile_path.exists():
                return None
            # Decode each tile sheet once and reuse it for every image
            # cropped from it, rather than per-row.
            tile_key = str(tile_lookup.tile_path)
            tile_pixmap = self._tile_pixmap_cache.get(tile_key)
            if tile_pixmap is None:
                tile_pixmap = QPixmap(tile_key)
                self._tile_pixmap_cache[tile_key] = tile_pixmap
            if tile_pixmap.isNull():
                return None
        except Exception:
            LOGGER.exception("Failed to load cached tile for image %s", image_id)
            return None
        else:
            return tile_pixmap.copy(
                tile_lookup.x, tile_lookup.y, tile_lookup.width, tile_lookup.height
            )

    def _load_thumbnail(self, row: int) -> QPixmap | None:
        """Load thumbnail for the image at the given row using thumbnail tiles."""
        if row < 0 or row >= len(self._image_paths):
            return None

        # Check cache first
        if row in self._pixmap_cache:
            return self._pixmap_cache[row]

        image_id = self._image_ids[row] if row < len(self._image_ids) else None
        pixmap: QPixmap | None = None

        if image_id is not None:
            # A highlighted crop (this person's face boxed) takes priority
            # over the plain tile crop when a bbox is known for this image.
            pixmap = self._load_highlighted_tile_crop(image_id) or self._load_tile_crop(
                image_id
            )

        if pixmap is None:
            # Fallback to loading full image if tile system fails
            image_path = self._image_paths[row]
            try:
                fallback = QPixmap(image_path)
                if not fallback.isNull():
                    pixmap = fallback.scaled(
                        80,
                        80,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
            except Exception:
                LOGGER.exception("Failed to load thumbnail for %s", image_path)

        if pixmap is None:
            return None

        self._pixmap_cache[row] = pixmap
        self._ensure_cache_size()
        return pixmap
