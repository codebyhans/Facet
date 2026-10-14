from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from photo_app.domain.models import Collection


class CollectionListWidget(QWidget):
    """Sidebar list of hand-picked collections with context actions."""

    collectionSelected = Signal(int)  # noqa: N815
    createRequested = Signal()  # noqa: N815
    renameRequested = Signal(int, str)  # noqa: N815
    deleteRequested = Signal(int)  # noqa: N815
    createAlbumRequested = Signal(int)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(8, 4, 4, 4)
        title = QLabel("Collections")
        title.setStyleSheet("color: #aaaaaa; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        add_button = QToolButton()
        add_button.setText("+")
        add_button.setToolTip("New collection")
        add_button.setAutoRaise(True)
        add_button.clicked.connect(self.createRequested)
        header.addWidget(add_button)
        layout.addLayout(header)

        self._list = QListWidget()
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list, 1)

    def set_collections(self, collections: list[Collection]) -> None:
        """Replace the listed collections, keeping the current selection."""
        selected = self.selected_collection_id()
        self._list.clear()
        for collection in collections:
            if collection.id is None:
                continue
            item = QListWidgetItem(f"{collection.name}  ({collection.image_count})")
            item.setData(Qt.ItemDataRole.UserRole, collection.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, collection.name)
            self._list.addItem(item)
            if collection.id == selected:
                self._list.setCurrentItem(item)

    def selected_collection_id(self) -> int | None:
        """Return the selected collection id, if any."""
        item = self._list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, int) else None

    def select_collection(self, collection_id: int) -> None:
        """Highlight one collection without emitting collectionSelected."""
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == collection_id:
                self._list.setCurrentItem(item)
                return

    def clear_selection(self) -> None:
        """Drop the highlight, e.g. when an album is opened instead."""
        self._list.setCurrentItem(None)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        collection_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(collection_id, int):
            self.collectionSelected.emit(collection_id)

    def _show_context_menu(self, position: QPoint) -> None:
        item = self._list.itemAt(position)
        menu = QMenu(self)
        create = menu.addAction("New Collection…")
        create_album = rename = delete = None
        if item is not None:
            create_album = menu.addAction("Create Album from Collection…")
            menu.addSeparator()
            rename = menu.addAction("Rename")
            delete = menu.addAction("Delete")

        picked = menu.exec(self._list.viewport().mapToGlobal(position))
        if picked is None:
            return
        if picked is create:
            self.createRequested.emit()
            return
        if item is None:
            return
        collection_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(collection_id, int):
            return
        if picked is create_album:
            self.createAlbumRequested.emit(collection_id)
        elif picked is rename:
            current_name = str(item.data(Qt.ItemDataRole.UserRole + 1) or "")
            text, accepted = QInputDialog.getText(
                self, "Rename Collection", "Name", text=current_name
            )
            if accepted and text.strip():
                self.renameRequested.emit(collection_id, text.strip())
        elif picked is delete:
            self.deleteRequested.emit(collection_id)
