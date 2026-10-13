from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QMenu,
    QTreeView,
)

from photo_app.app.models.album_tree_model import AlbumTreeModel, AlbumTreeNode
from photo_app.app.widgets.album_tree_delegate import AlbumTreeDelegate
from photo_app.app.widgets.album_tree_style import AlbumTreeStyle

if TYPE_CHECKING:
    from collections.abc import Iterable

    from PySide6.QtGui import QDropEvent


class MoveAlbumDialog(QDialog):
    """Simple destination picker used by Move action."""

    def __init__(
        self,
        nodes: Iterable[AlbumTreeNode],
        moving_node_ids: set[str],
        parent: QDialog | QTreeView | None = None,
    ) -> None:
        super().__init__(parent)
        self._combo = QComboBox(self)
        self._combo.addItem("Albums (root)", "root")
        for node in nodes:
            if node.kind != "folder" or node.node_id in moving_node_ids:
                continue
            self._combo.addItem(node.name, node.node_id)

        form = QFormLayout(self)
        form.addRow("New parent", self._combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def target_parent_id(self) -> str | None:
        """Return selected parent node id."""
        value = self._combo.currentData()
        if isinstance(value, str):
            return None if value == "root" else value
        return None


class AlbumTreeWidget(QTreeView):
    """Album tree view with context actions and drag/drop move requests."""

    albumSelected = Signal(int, object)  # noqa: N815
    createFolderRequested = Signal(object)  # noqa: N815
    createVirtualAlbumRequested = Signal(object)  # noqa: N815
    renameRequested = Signal(str, str)  # noqa: N815
    deleteRequested = Signal(str)  # noqa: N815
    editFiltersRequested = Signal(str)  # noqa: N815
    moveRequested = Signal(list, object, object)  # noqa: N815

    def __init__(self, model: AlbumTreeModel, parent: QTreeView | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setHeaderHidden(True)
        self.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.clicked.connect(self._on_clicked)

        self.setDragDropMode(QTreeView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

        # Apply custom style for dark theme arrows
        self.setStyle(AlbumTreeStyle())
        self.setItemDelegate(AlbumTreeDelegate(self))

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        model = self.model()
        if not isinstance(model, AlbumTreeModel):
            return
        source_ids = self.selected_node_ids()
        if not source_ids:
            return

        target_index = self.indexAt(event.position().toPoint())
        target_node = model.node_from_index(target_index)
        drop_position = self.dropIndicatorPosition()

        if (
            target_node is None
            or drop_position == QAbstractItemView.DropIndicatorPosition.OnViewport
        ):
            # Dropped on empty space: move to top level, append at the end.
            self.moveRequested.emit(source_ids, None, None)
            event.accept()
            return

        if target_node.node_id in source_ids:
            # Dropped onto one of the dragged items: nothing to do.
            event.ignore()
            return

        if (
            drop_position == QAbstractItemView.DropIndicatorPosition.OnItem
            and target_node.kind == "folder"
        ):
            # Dropped directly on a folder: move into it, append at the end.
            self.moveRequested.emit(source_ids, target_node.node_id, None)
            event.accept()
            return

        # Dropped above/below a sibling (or on a non-folder item): reorder in place.
        position_info = model.node_position(target_node.node_id)
        if position_info is None:
            event.ignore()
            return
        parent_id, row = position_info
        insert_row = (
            row
            if drop_position == QAbstractItemView.DropIndicatorPosition.AboveItem
            else row + 1
        )
        self.moveRequested.emit(source_ids, parent_id, insert_row)
        event.accept()

    def _show_context_menu(self, position: QPoint) -> None:
        model = self.model()
        if not isinstance(model, AlbumTreeModel):
            return
        index = self.indexAt(position)
        node = model.node_from_index(index)

        menu = QMenu(self)
        create_folder = menu.addAction("Create Folder")
        create_virtual = menu.addAction("Create Virtual Album")
        rename = menu.addAction("Rename")
        delete = menu.addAction("Delete")
        move = menu.addAction("Move")
        edit = menu.addAction("Edit Filters")

        picked = menu.exec(self.viewport().mapToGlobal(position))
        parent_id = None if node is None else node.node_id

        if picked is create_folder:
            self.createFolderRequested.emit(parent_id)
        elif picked is create_virtual:
            self.createVirtualAlbumRequested.emit(parent_id)
        elif picked is rename and node is not None:
            text, accepted = QInputDialog.getText(
                self, "Rename", "Name", text=node.name
            )
            if accepted and text.strip():
                self.renameRequested.emit(node.node_id, text.strip())
        elif picked is delete and node is not None:
            self.deleteRequested.emit(node.node_id)
        elif picked is edit and node is not None and node.kind == "virtual":
            self.editFiltersRequested.emit(node.node_id)
        elif picked is move and node is not None:
            # Move the whole selection when right-clicking inside it.
            selected = self.selected_node_ids()
            move_ids = selected if node.node_id in selected else [node.node_id]
            all_nodes = self._flatten(model.all_nodes())
            dialog = MoveAlbumDialog(all_nodes, set(move_ids), parent=self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.moveRequested.emit(move_ids, dialog.target_parent_id(), None)

    def _on_clicked(self) -> None:
        model = self.model()
        if not isinstance(model, AlbumTreeModel):
            return
        # Ctrl/Shift-clicking to build a selection shouldn't open each album.
        if len(self.selectionModel().selectedRows()) > 1:
            return
        node = model.node_from_index(self.currentIndex())
        if node is None:
            return
        if node.kind == "virtual" and node.album_id is not None:
            self.albumSelected.emit(node.album_id, node.query_definition)

    def selected_node_ids(self) -> list[str]:
        """Return node ids of all selected rows."""
        model = self.model()
        if not isinstance(model, AlbumTreeModel):
            return []
        nodes = (model.node_from_index(i) for i in self.selectionModel().selectedRows())
        return [node.node_id for node in nodes if node is not None]

    def _flatten(self, nodes: list[AlbumTreeNode]) -> list[AlbumTreeNode]:
        result: list[AlbumTreeNode] = []
        stack = list(nodes)
        while stack:
            node = stack.pop()
            result.append(node)
            stack.extend(node.children)
        return result
