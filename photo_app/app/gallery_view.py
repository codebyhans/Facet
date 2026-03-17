from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem

if TYPE_CHECKING:
    from collections.abc import Sequence


class ThumbnailItemViewModel(Protocol):
    label: str
    thumbnail_path: str


class GalleryView(QListWidget):
    """Horizontal thumbnail strip with single-selection behavior."""

    selection_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._items: list[ThumbnailItemViewModel] = []
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(False)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSpacing(8)
        self.setIconSize(QSize(120, 120))
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.itemSelectionChanged.connect(self._on_selection_changed)

    def set_items(self, items: Sequence[ThumbnailItemViewModel]) -> None:
        """Replace thumbnail strip contents."""
        self.clear()
        self._items = list(items)

        for item in self._items:
            list_item = QListWidgetItem(item.label)
            pixmap = QPixmap(item.thumbnail_path)
            if not pixmap.isNull():
                list_item.setIcon(QIcon(pixmap))
            list_item.setToolTip(item.thumbnail_path)
            self.addItem(list_item)

    def set_current_index(self, index: int) -> None:
        """Select thumbnail by index."""
        if index < 0 or index >= self.count():
            self.clearSelection()
            return
        item = self.item(index)
        if item is None:
            return
        self.setCurrentItem(item)
        self.scrollToItem(item)

    def _on_selection_changed(self) -> None:
        selected = self.selectedIndexes()
        if not selected:
            return
        self.selection_changed.emit(selected[0].row())
