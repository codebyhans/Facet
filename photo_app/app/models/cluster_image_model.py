from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, QSize, Qt
from PySide6.QtGui import QPixmap


class ClusterImageModel(QAbstractListModel):
    """Model for cluster image gallery in person detail view."""

    def __init__(self, image_paths: list[str], parent: Any | None = None) -> None:
        super().__init__(parent)
        self._image_paths = image_paths

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._image_paths)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: ANN401
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

    def _load_thumbnail(self, row: int) -> QPixmap | None:
        """Load thumbnail for the image at the given row."""
        if row < 0 or row >= len(self._image_paths):
            return None
        
        image_path = self._image_paths[row]
        try:
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    80, 80,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                return scaled
        except Exception:
            pass
        return None