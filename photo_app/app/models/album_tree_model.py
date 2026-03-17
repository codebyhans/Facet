from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QObject, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QStyle


@dataclass
class AlbumTreeNode:
    """In-memory album node used by AlbumTreeModel."""

    node_id: str
    name: str
    kind: str
    album_id: int | None = None
    parent_id: str | None = None
    query_definition: dict[str, object] | None = None
    children: list[AlbumTreeNode] = field(default_factory=list)


class AlbumTreeModel(QAbstractItemModel):
    """Tree model for nested folders and virtual albums."""

    NodeIdRole = Qt.ItemDataRole.UserRole + 1
    KindRole = Qt.ItemDataRole.UserRole + 2
    AlbumIdRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root = AlbumTreeNode(node_id="root", name="Albums", kind="root")

    def columnCount(self, _parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 1

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        parent = parent or QModelIndex()
        node = self._node_from_index(parent)
        return len(node.children)

    def index(self, row: int, column: int, parent: QModelIndex | None = None) -> QModelIndex:
        parent = parent or QModelIndex()
        if row < 0 or column != 0:
            return QModelIndex()
        parent_node = self._node_from_index(parent)
        if row >= len(parent_node.children):
            return QModelIndex()
        child = parent_node.children[row]
        return self.createIndex(row, column, child)

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        node = self._node_from_index(index)
        parent = self._find_parent(node.node_id)
        if parent is None or parent.node_id == "root":
            return QModelIndex()
        grand = self._find_parent(parent.node_id)
        if grand is None:
            return QModelIndex()
        row = grand.children.index(parent)
        return self.createIndex(row, 0, parent)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object | None:
        if not index.isValid():
            return None
        node = self._node_from_index(index)

        role_values: dict[int, object | None] = {
            Qt.ItemDataRole.DisplayRole: node.name,
            self.NodeIdRole: node.node_id,
            self.KindRole: node.kind,
            self.AlbumIdRole: node.album_id,
        }
        if role in role_values:
            return role_values[role]

        if role == Qt.ItemDataRole.DecorationRole:
            style = QApplication.style()
            if node.kind == "folder":
                return style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            # No icon for albums — fall through and return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if node.kind == "folder":
                return QColor("#5B9BD5")   # muted blue for folders
            if node.kind == "virtual":
                return QColor("#D4D4D4")   # light grey for albums

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not index.isValid():
            return base | Qt.ItemFlag.ItemIsDropEnabled
        node = self._node_from_index(index)
        if node.kind == "folder":
            return base | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
        if node.kind == "virtual":
            return base | Qt.ItemFlag.ItemIsDragEnabled
        return base

    def set_nodes(self, nodes: list[AlbumTreeNode]) -> None:
        """Replace current tree with already nested nodes."""
        self.beginResetModel()
        self._root.children = nodes
        self.endResetModel()

    def node_from_index(self, index: QModelIndex) -> AlbumTreeNode | None:
        """Return tree node from index."""
        if not index.isValid():
            return None
        node = self._node_from_index(index)
        if node.kind == "root":
            return None
        return node

    def index_from_node_id(self, node_id: str) -> QModelIndex:
        """Resolve one node id to model index."""
        if node_id == "root":
            return QModelIndex()
        return self._search_index(node_id, QModelIndex())

    def can_move(self, node_id: str, new_parent_id: str | None) -> bool:
        """Check if move target is valid and not within own subtree."""
        if new_parent_id in {None, "root"}:
            return True
        if node_id == new_parent_id:
            return False
        cursor = self._find_node(new_parent_id)
        while cursor is not None and cursor.node_id != "root":
            if cursor.node_id == node_id:
                return False
            cursor = self._find_parent(cursor.node_id)
        return True

    def move_node(self, node_id: str, new_parent_id: str | None) -> bool:
        """Move one node to another parent."""
        if not self.can_move(node_id, new_parent_id):
            return False
        node = self._find_node(node_id)
        if node is None or node.kind == "root":
            return False
        old_parent = self._find_parent(node_id)
        target_parent = self._root if new_parent_id in {None, "root"} else self._find_node(new_parent_id)
        if old_parent is None or target_parent is None:
            return False
        if target_parent.kind not in {"root", "folder"}:
            return False
        old_parent_index = self.index_from_node_id(old_parent.node_id)
        old_row = old_parent.children.index(node)
        self.beginRemoveRows(old_parent_index, old_row, old_row)
        old_parent.children.pop(old_row)
        self.endRemoveRows()

        target_index = self.index_from_node_id(target_parent.node_id)
        insert_row = len(target_parent.children)
        self.beginInsertRows(target_index, insert_row, insert_row)
        node.parent_id = None if target_parent.node_id == "root" else target_parent.node_id
        target_parent.children.append(node)
        self.endInsertRows()
        return True

    def add_node(self, node: AlbumTreeNode, parent_id: str | None) -> None:
        """Insert a new node under one parent."""
        parent = self._root if parent_id in {None, "root"} else self._find_node(parent_id)
        if parent is None:
            parent = self._root
        row = len(parent.children)
        parent_index = self.index_from_node_id(parent.node_id)
        self.beginInsertRows(parent_index, row, row)
        node.parent_id = None if parent.node_id == "root" else parent.node_id
        parent.children.append(node)
        self.endInsertRows()

    def remove_node(self, node_id: str) -> bool:
        """Remove one node from tree."""
        node = self._find_node(node_id)
        if node is None or node.kind == "root":
            return False
        parent = self._find_parent(node_id)
        if parent is None:
            return False
        row = parent.children.index(node)
        parent_index = self.index_from_node_id(parent.node_id)
        self.beginRemoveRows(parent_index, row, row)
        parent.children.pop(row)
        self.endRemoveRows()
        return True

    def rename_node(self, node_id: str, new_name: str) -> bool:
        """Update display name for one node."""
        node = self._find_node(node_id)
        if node is None or node.kind == "root":
            return False
        node.name = new_name
        index = self.index_from_node_id(node_id)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
        return True

    def all_nodes(self) -> list[AlbumTreeNode]:
        """Return root child list for serialization."""
        return self._root.children

    def _node_from_index(self, index: QModelIndex) -> AlbumTreeNode:
        if not index.isValid():
            return self._root
        node = index.internalPointer()
        if isinstance(node, AlbumTreeNode):
            return node
        return self._root

    def _find_node(self, node_id: str) -> AlbumTreeNode | None:
        if node_id == "root":
            return self._root
        stack = list(self._root.children)
        while stack:
            node = stack.pop()
            if node.node_id == node_id:
                return node
            stack.extend(node.children)
        return None

    def _find_parent(self, child_id: str) -> AlbumTreeNode | None:
        stack = [self._root]
        while stack:
            node = stack.pop()
            for child in node.children:
                if child.node_id == child_id:
                    return node
                stack.append(child)
        return None

    def _search_index(self, node_id: str, parent_index: QModelIndex) -> QModelIndex:
        for row in range(self.rowCount(parent_index)):
            idx = self.index(row, 0, parent_index)
            node = self.node_from_index(idx)
            if node is None:
                continue
            if node.node_id == node_id:
                return idx
            nested = self._search_index(node_id, idx)
            if nested.isValid():
                return nested
        return QModelIndex()
