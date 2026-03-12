from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QMenu,
    QTreeView,
)

from photo_app.app.models.album_tree_model import AlbumTreeModel, AlbumTreeNode


class MoveAlbumDialog(QDialog):
    """Simple destination picker used by Move action."""

    def __init__(
        self,
        nodes: Iterable[AlbumTreeNode],
        current_node_id: str,
        parent: QDialog | QTreeView | None = None,
    ) -> None:
        super().__init__(parent)
        self._combo = QComboBox(self)
        self._combo.addItem("Albums (root)", "root")
        for node in nodes:
            if node.kind != "folder" or node.node_id == current_node_id:
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

    albumSelected = Signal(int, object)
    albumMoved = Signal(int, object)
    createFolderRequested = Signal(object)
    createVirtualAlbumRequested = Signal(object)
    renameRequested = Signal(str, str)
    deleteRequested = Signal(str)
    editFiltersRequested = Signal(str)
    moveRequested = Signal(str, object)

    def __init__(self, model: AlbumTreeModel, parent: QTreeView | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setHeaderHidden(True)
        self.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.clicked.connect(self._on_clicked)

        self.setDragDropMode(QTreeView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        model = self.model()
        if not isinstance(model, AlbumTreeModel):
            return
        source_index = self.currentIndex()
        source_node = model.node_from_index(source_index)
        if source_node is None:
            return
        target_index = self.indexAt(event.position().toPoint())
        target_node = model.node_from_index(target_index)
        parent_id = None if target_node is None else target_node.node_id
        self.moveRequested.emit(source_node.node_id, parent_id)
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
            text, accepted = QInputDialog.getText(self, "Rename", "Name", text=node.name)
            if accepted and text.strip():
                self.renameRequested.emit(node.node_id, text.strip())
        elif picked is delete and node is not None:
            self.deleteRequested.emit(node.node_id)
        elif picked is edit and node is not None and node.kind == "virtual":
            self.editFiltersRequested.emit(node.node_id)
        elif picked is move and node is not None:
            all_nodes = self._flatten(model.all_nodes())
            dialog = MoveAlbumDialog(all_nodes, node.node_id, parent=self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.moveRequested.emit(node.node_id, dialog.target_parent_id())

    def _on_clicked(self) -> None:
        model = self.model()
        if not isinstance(model, AlbumTreeModel):
            return
        node = model.node_from_index(self.currentIndex())
        if node is None:
            return
        if node.kind == "virtual" and node.album_id is not None:
            self.albumSelected.emit(node.album_id, node.query_definition)

    def _flatten(self, nodes: list[AlbumTreeNode]) -> list[AlbumTreeNode]:
        result: list[AlbumTreeNode] = []
        stack = list(nodes)
        while stack:
            node = stack.pop()
            result.append(node)
            stack.extend(node.children)
        return result
