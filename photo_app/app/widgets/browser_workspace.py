from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QImageReader, QKeyEvent, QPixmap
from PySide6.QtWidgets import QLabel, QListView, QVBoxLayout, QWidget

from photo_app.app.models.photo_grid_model import PhotoGridModel


class FilmstripView(QListView):
    """Single-row horizontal thumbnail strip with lazy loading hooks."""

    photoActivated = Signal(int)
    currentImageChanged = Signal(QModelIndex)

    def __init__(self, model: PhotoGridModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(False)
        self.setWordWrap(False)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setSpacing(8)
        self.setIconSize(QSize(150, 150))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.doubleClicked.connect(self._on_double_click)
        self.clicked.connect(self._on_clicked)
        self.horizontalScrollBar().valueChanged.connect(self._on_scrolled)
        model.modelReset.connect(self._on_model_reset)
        model.rowsInserted.connect(self._on_rows_inserted)
        self.installEventFilter(self)

    def currentIndex(self) -> QModelIndex:  # noqa: N802
        current = self.selectionModel().currentIndex()
        if current.isValid():
            return current
        selected = self.selectedIndexes()
        if selected:
            return selected[0]
        return QModelIndex()

    def focus_first_item(self) -> None:
        model = self.model()
        if not isinstance(model, PhotoGridModel) or model.rowCount() == 0:
            return
        if self.currentIndex().isValid():
            return
        index = model.index(0, 0)
        self.setCurrentIndex(index)
        self.selectionModel().select(
            index,
            self.selectionModel().SelectionFlag.ClearAndSelect,
        )
        self.scrollTo(index)
        self.currentImageChanged.emit(index)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if watched is self and event.type() == QEvent.Type.Resize:
            self._request_visible_rows()
        return super().eventFilter(watched, event)

    def _on_model_reset(self) -> None:
        self.focus_first_item()
        self._request_visible_rows()

    def _on_rows_inserted(self, _parent: QModelIndex, _first: int, _last: int) -> None:
        self.focus_first_item()
        self._request_visible_rows()

    def _on_double_click(self, index: QModelIndex) -> None:
        image_id = self.model().data(index, PhotoGridModel.ImageIdRole)
        if isinstance(image_id, int):
            self.photoActivated.emit(image_id)

    def _on_clicked(self, index: QModelIndex) -> None:
        self.currentImageChanged.emit(index)
        self._request_visible_rows()

    def _on_scrolled(self) -> None:
        self._request_visible_rows()

    def _request_visible_rows(self) -> None:
        model = self.model()
        if not isinstance(model, PhotoGridModel):
            return
        if model.rowCount() == 0:
            return
        y = max(0, self.viewport().height() // 2)
        first_index = self.indexAt(QPoint(0, y))
        last_index = self.indexAt(QPoint(max(0, self.viewport().width() - 1), y))
        first = first_index.row() if first_index.isValid() else 0
        last = last_index.row() if last_index.isValid() else model.rowCount() - 1
        model.notify_visible_rows(max(0, first), max(first, last))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        current = self.currentIndex()
        model = self.model()
        if not isinstance(model, PhotoGridModel):
            return super().keyPressEvent(event)
        row = current.row() if current.isValid() else -1
        if key == Qt.Key.Key_Right and row < model.rowCount() - 1:
            new_index = model.index(row + 1, 0)
            self.setCurrentIndex(new_index)
            self.selectionModel().select(
                new_index,
                self.selectionModel().SelectionFlag.ClearAndSelect,
            )
            self.scrollTo(new_index)
            self.currentImageChanged.emit(new_index)
            return None
        if key == Qt.Key.Key_Left and row > 0:
            new_index = model.index(row - 1, 0)
            self.setCurrentIndex(new_index)
            self.selectionModel().select(
                new_index,
                self.selectionModel().SelectionFlag.ClearAndSelect,
            )
            self.scrollTo(new_index)
            self.currentImageChanged.emit(new_index)
            return None
        super().keyPressEvent(event)


class BrowserWorkspaceWidget(QWidget):
    """Large preview above a Lightroom-like horizontal filmstrip."""

    photoActivated = Signal(int)

    def __init__(self, model: PhotoGridModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._source_preview: QPixmap | None = None

        self.setObjectName("browserWorkspace")
        self._preview = QLabel(self)
        self._preview.setObjectName("mainPreview")
        self._preview.setMinimumHeight(360)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setText("No image selected")

        self._meta = QLabel(self)
        self._meta.setObjectName("previewMeta")
        self._meta.setText("Select a photo from the filmstrip")

        self._filmstrip = FilmstripView(model, self)
        self._filmstrip.setFixedHeight(210)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addWidget(self._preview, 1)
        root.addWidget(self._meta, 0)
        root.addWidget(self._filmstrip, 0)

        self._filmstrip.currentImageChanged.connect(self._on_current_image_changed)
        self._filmstrip.photoActivated.connect(self.photoActivated.emit)

    def currentIndex(self) -> QModelIndex:  # noqa: N802
        return self._filmstrip.currentIndex()

    def resizeEvent(self, event: QEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._render_preview()

    def _on_current_image_changed(self, index: QModelIndex) -> None:
        if not index.isValid():
            self._source_preview = None
            self._preview.setText("No image selected")
            self._meta.setText("Select a photo from the filmstrip")
            return
        file_path = self._model.data(index, PhotoGridModel.FilePathRole)
        capture_date = self._model.data(index, PhotoGridModel.CaptureDateRole)
        if not isinstance(file_path, str) or not file_path:
            self._source_preview = None
            self._preview.setText("Could not resolve image path")
            self._meta.setText("")
            return
        self._source_preview = self._load_oriented_pixmap(Path(file_path))
        if self._source_preview is None:
            self._preview.setText("Could not load image")
            self._meta.setText(file_path)
            return
        self._meta.setText(f"{capture_date}  |  {file_path}")
        self._render_preview()

    def _render_preview(self) -> None:
        if self._source_preview is None:
            return
        scaled = self._source_preview.scaled(
            self._preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview.setPixmap(scaled)

    def _load_oriented_pixmap(self, file_path: Path) -> QPixmap | None:
        reader = QImageReader(str(file_path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            fallback = QPixmap(str(file_path))
            return None if fallback.isNull() else fallback
        pixmap = QPixmap.fromImage(image)
        return None if pixmap.isNull() else pixmap
