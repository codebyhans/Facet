from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)

from photo_app.infrastructure.repositories import RAW_SUFFIXES

if TYPE_CHECKING:
    from datetime import datetime

    from PySide6.QtGui import QPixmap

_VIDEO_SUFFIXES = frozenset({".mov", ".mp4", ".m4v", ".avi", ".mts", ".m2ts", ".3gp"})


class MediaKind(StrEnum):
    """What a grid row is, for the thumbnail badge."""

    STILL = "still"
    LIVE = "live"  # still image with a same-stem .mov sidecar (Live Photo)
    VIDEO = "video"


@dataclass(frozen=True)
class PhotoGridItem:
    """One row in the photo grid model."""

    image_id: int
    image_hash: str
    file_path: str
    capture_date: datetime | None
    tile_index: int | None
    position_in_tile: int | None
    flag: str | None = None


class PhotoGridModel(QAbstractListModel):
    """Icon-mode model backed by on-disk thumbnail tiles."""

    ImageIdRole = Qt.ItemDataRole.UserRole + 1
    CaptureDateRole = Qt.ItemDataRole.UserRole + 2
    FilePathRole = Qt.ItemDataRole.UserRole + 3
    FlagRole = Qt.ItemDataRole.UserRole + 4
    MediaKindRole = Qt.ItemDataRole.UserRole + 5
    # True when a same-stem RAW sits next to the file (the RAW itself is hidden)
    HasRawRole = Qt.ItemDataRole.UserRole + 6

    tileRequested = Signal(int)  # noqa: N815
    loadMoreRequested = Signal()  # noqa: N815

    def __init__(
        self,
        *,
        thumbnail_size: tuple[int, int] = (128, 128),
        tile_size: tuple[int, int] = (1024, 1024),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._items: list[PhotoGridItem] = []
        self._tiles: dict[int, QPixmap] = {}
        self._pending_tiles: set[int] = set()
        self._has_more = False
        self._thumbnail_size = thumbnail_size
        self._tile_size = tile_size
        # Lowercased file names per folder, to spot Live Photo MOVs and RAWs
        self._folder_names: dict[str, frozenset[str]] = {}

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex | None = None,
    ) -> int:
        parent = parent or QModelIndex()
        if parent.isValid():
            return 0
        return len(self._items)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]

        role_values: dict[int, object | None] = {
            Qt.ItemDataRole.DisplayRole: self._format_capture_date(item.capture_date),
            Qt.ItemDataRole.SizeHintRole: QSize(200, 200),
            self.ImageIdRole: item.image_id,
            self.CaptureDateRole: self._format_capture_date(item.capture_date),
            self.FilePathRole: item.file_path,
            self.FlagRole: item.flag,
        }
        if role in role_values:
            return role_values[role]
        if role == Qt.ItemDataRole.DecorationRole:
            return self._resolve_decoration(item)
        if role == self.MediaKindRole:
            return self._media_kind(item.file_path)
        if role == self.HasRawRole:
            return self._has_raw_sibling(item.file_path)
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def clear(self) -> None:
        """Reset model contents."""
        self.beginResetModel()
        self._items.clear()
        self._tiles.clear()
        self._pending_tiles.clear()
        self._folder_names.clear()
        self._has_more = False
        self.endResetModel()

    def append_page(
        self, items: list[PhotoGridItem], *, has_more: bool, append: bool = True
    ) -> None:
        """Append one paginated batch."""
        # Clear first when starting a fresh load (even if the result is empty)
        if not append:
            self.clear()

        if not items:
            self._has_more = has_more
            return

        # Add new items
        start = len(self._items)
        end = start + len(items) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._items.extend(items)
        self.endInsertRows()
        self._has_more = has_more

    def set_tile(self, tile_index: int, pixmap: QPixmap) -> None:
        """Cache one loaded tile pixmap and update all affected rows."""
        self._tiles[tile_index] = pixmap
        self._pending_tiles.discard(tile_index)

        changed_rows = [
            row
            for row, item in enumerate(self._items)
            if item.tile_index == tile_index and item.position_in_tile is not None
        ]
        for row in changed_rows:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])

    def notify_visible_rows(self, first_row: int, last_row: int) -> None:
        """Trigger pagination and tile prefetch for visible rows."""
        if self._has_more and len(self._items) - last_row <= PREFETCH_THRESHOLD:
            self.loadMoreRequested.emit()

        for row in range(max(0, first_row), min(last_row + 1, len(self._items))):
            item = self._items[row]
            if item.tile_index is None:
                continue
            if item.tile_index in self._tiles or item.tile_index in self._pending_tiles:
                continue
            self._pending_tiles.add(item.tile_index)
            self.tileRequested.emit(item.tile_index)

    def item_at(self, row: int) -> PhotoGridItem | None:
        """Return one item by row."""
        if row < 0 or row >= len(self._items):
            return None
        return self._items[row]

    @property
    def items(self) -> list[PhotoGridItem]:
        """Current model rows."""
        return list(self._items)

    @property
    def thumbnail_size(self) -> tuple[int, int]:
        """Configured native thumbnail pixel size."""
        return self._thumbnail_size

    def remove_items(self, image_ids: set[int]) -> None:
        """Remove rows for the given image IDs (e.g. after permanent deletion)."""
        rows = sorted(
            (row for row, item in enumerate(self._items) if item.image_id in image_ids),
            reverse=True,
        )
        for row in rows:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._items[row]
            self.endRemoveRows()

    def _resolve_decoration(self, item: PhotoGridItem) -> QPixmap | None:
        tile_index = item.tile_index
        position_in_tile = item.position_in_tile
        if tile_index is None or position_in_tile is None:
            return None

        tile = self._tiles.get(tile_index)
        if tile is None:
            if tile_index not in self._pending_tiles:
                self._pending_tiles.add(tile_index)
                self.tileRequested.emit(tile_index)
            return None

        grid_width = max(1, self._tile_size[0] // self._thumbnail_size[0])
        col = position_in_tile % grid_width
        line = position_in_tile // grid_width
        x = col * self._thumbnail_size[0]
        y = line * self._thumbnail_size[1]
        return tile.copy(QRect(x, y, self._thumbnail_size[0], self._thumbnail_size[1]))

    def _media_kind(self, file_path: str) -> MediaKind:
        path = Path(file_path)
        if path.suffix.lower() in _VIDEO_SUFFIXES:
            return MediaKind.VIDEO
        if f"{path.stem.lower()}.mov" in self._sibling_names(path):
            return MediaKind.LIVE
        return MediaKind.STILL

    def _has_raw_sibling(self, file_path: str) -> bool:
        path = Path(file_path)
        if path.suffix.lower() not in {".jpg", ".jpeg"}:
            return False
        names = self._sibling_names(path)
        stem = path.stem.lower()
        return any(f"{stem}{suffix}" in names for suffix in RAW_SUFFIXES)

    def _sibling_names(self, path: Path) -> frozenset[str]:
        """Lowercased names in the file's folder, listed once per folder."""
        folder = str(path.parent)
        names = self._folder_names.get(folder)
        if names is None:
            try:
                names = frozenset(p.name.lower() for p in path.parent.iterdir())
            except OSError:
                names = frozenset()
            self._folder_names[folder] = names
        return names

    def _format_capture_date(self, value: datetime | None) -> str:
        if value is None:
            return "Unknown date"
        return value.strftime("%Y-%m-%d %H:%M")

    def update_flag(self, row: int, flag: str | None) -> None:
        """Replace the flag on one item and notify the view."""
        if row < 0 or row >= len(self._items):
            return
        old = self._items[row]
        # dataclasses.replace creates a new frozen instance with updated field
        self._items[row] = replace(old, flag=flag)
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [self.FlagRole])


PREFETCH_THRESHOLD = 30
