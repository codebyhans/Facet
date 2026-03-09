from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PhotoViewerWidget(QWidget):
    """Simple full image viewer with keyboard navigation and zoom."""

    def __init__(self, items: list[tuple[int, str]], start_row: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items = items
        self._row = start_row
        self._zoom = 1.0
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        root = QVBoxLayout(self)
        root.addWidget(self._label)
        self.setWindowTitle("Photo Viewer")
        self.resize(1200, 900)
        self._render()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Right:
            self._row = min(len(self._items) - 1, self._row + 1)
            self._render()
            return
        if event.key() == Qt.Key.Key_Left:
            self._row = max(0, self._row - 1)
            self._render()
            return
        if event.key() == Qt.Key.Key_Plus:
            self._zoom = min(5.0, self._zoom + 0.1)
            self._render()
            return
        if event.key() == Qt.Key.Key_Minus:
            self._zoom = max(0.2, self._zoom - 0.1)
            self._render()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(5.0, self._zoom + 0.1)
        elif delta < 0:
            self._zoom = max(0.2, self._zoom - 0.1)
        self._render()

    def _render(self) -> None:
        if not self._items:
            self._label.setText("No image")
            return
        _image_id, file_path = self._items[self._row]
        pixmap = QPixmap(str(Path(file_path)))
        if pixmap.isNull():
            self._label.setText("Could not load image")
            return
        scaled = pixmap.scaled(
            int(pixmap.width() * self._zoom),
            int(pixmap.height() * self._zoom),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)
