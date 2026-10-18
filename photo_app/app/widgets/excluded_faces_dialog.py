"""Dialog for browsing and restoring excluded ("Not of Interest") faces."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QThreadPool
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.workers.excluded_faces_worker import ExcludedFacesWorker
from photo_app.app.workers.runner import start_worker

if TYPE_CHECKING:
    from pathlib import Path

    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
    from photo_app.services.face_review_service import (
        ExcludedFaceItem,
        FaceReviewService,
    )

LOGGER = logging.getLogger(__name__)

_ICON_SIZE = 100
_INITIAL_LIMIT = 200
_LOAD_MORE_INCREMENT = 200


class ExcludedFacesDialog(QDialog):
    """Browse every currently-excluded face and restore any of them.

    Excluding a face (via "Not of Interest" or the single-face "Remove")
    never deletes its detection or photo — only marks it excluded and, for
    a bulk exclude, removes its now-empty cluster/person. This dialog is
    the undo: nothing excluded is a one-way door. A restored face simply
    re-enters normal auto-clustering on the next indexing run as a fresh
    cluster (the old grouping/name isn't preserved, only the underlying
    photo/bbox/embedding always was).
    """

    def __init__(
        self,
        face_review_service: FaceReviewService,
        cache_directory: Path | None,
        parent: QWidget | None = None,
        tile_store: ThumbnailTileStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Excluded Faces")
        self.resize(760, 600)
        self._face_review_service = face_review_service
        self._cache_directory = cache_directory
        self._tile_store = tile_store
        self._thread_pool = QThreadPool.globalInstance()
        self._request_epoch = 0
        self._items_by_face_id: dict[int, ExcludedFaceItem] = {}
        self._limit = _INITIAL_LIMIT
        self._total_found = 0

        self._setup_ui()
        self._reload()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._summary_label = QLabel("Loading excluded faces…")
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet("color: #cccccc;")
        layout.addWidget(self._summary_label)

        self._list_widget = QListWidget()
        self._list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self._list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list_widget.setMovement(QListWidget.Movement.Static)
        self._list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._list_widget.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._list_widget.setSpacing(8)
        self._list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list_widget, 1)

        action_bar = QHBoxLayout()
        self._selection_label = QLabel("No items selected")
        self._selection_label.setStyleSheet("color: #888888; font-size: 11px;")
        action_bar.addWidget(self._selection_label)
        action_bar.addStretch()

        self._restore_btn = QPushButton("Restore Selected")
        self._restore_btn.setEnabled(False)
        self._restore_btn.setToolTip(
            "Undo excluding — these faces re-enter normal clustering again"
        )
        self._restore_btn.clicked.connect(self._on_restore_selected)
        action_bar.addWidget(self._restore_btn)

        self._load_more_btn = QPushButton("Load More")
        self._load_more_btn.setEnabled(False)
        self._load_more_btn.setVisible(False)
        self._load_more_btn.clicked.connect(self._on_load_more)
        action_bar.addWidget(self._load_more_btn)
        layout.addLayout(action_bar)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

    def _reload(self) -> None:
        """(Re)fetch the list from scratch, discarding any stale request."""
        self._limit = _INITIAL_LIMIT
        self._start_fetch()

    def _on_load_more(self) -> None:
        """Widen the pagination cap and re-fetch — never a silent ceiling."""
        self._limit += _LOAD_MORE_INCREMENT
        self._start_fetch()

    def _start_fetch(self) -> None:
        self._request_epoch += 1
        self._summary_label.setText("Loading excluded faces…")
        self._load_more_btn.setEnabled(False)
        worker = ExcludedFacesWorker(
            self._face_review_service,
            self._request_epoch,
            limit=self._limit,
            cache_directory=self._cache_directory,
            tile_store=self._tile_store,
        )
        worker.signals.result_ready.connect(self._on_list_ready)
        worker.signals.error.connect(self._on_list_error)
        start_worker(worker, self._thread_pool)

    def _on_list_ready(
        self,
        items: list[ExcludedFaceItem],
        thumbnails: dict[int, bytes],
        total_found: int,
        request_id: int,
    ) -> None:
        if request_id != self._request_epoch:
            return  # a newer reload superseded this one
        self._items_by_face_id = {item.face_id: item for item in items}
        self._total_found = total_found
        self._list_widget.clear()
        for item in items:
            self._add_item(item, thumbnails.get(item.face_id))
        self._update_summary(len(items), total_found)
        has_more = len(items) < total_found
        self._load_more_btn.setVisible(has_more)
        self._load_more_btn.setEnabled(has_more)

    def _on_list_error(self, msg: str) -> None:
        self._summary_label.setText(f"Failed to load excluded faces: {msg}")
        LOGGER.warning("Failed to load excluded faces: %s", msg)

    def _update_summary(self, shown_count: int, total_found: int) -> None:
        if total_found == 0:
            self._summary_label.setText("No excluded faces right now.")
            return
        if shown_count < total_found:
            self._summary_label.setText(
                f"Showing {shown_count} of {total_found} excluded face(s)."
            )
        else:
            self._summary_label.setText(f"{total_found} excluded face(s).")

    def _add_item(self, item: ExcludedFaceItem, thumbnail_bytes: bytes | None) -> None:
        pixmap = QPixmap()
        if thumbnail_bytes is not None:
            pixmap.loadFromData(thumbnail_bytes)
        icon = QIcon(pixmap) if not pixmap.isNull() else QIcon()
        list_item = QListWidgetItem(icon, "")
        list_item.setData(Qt.ItemDataRole.UserRole, item.face_id)
        list_item.setSizeHint(QSize(_ICON_SIZE + 24, _ICON_SIZE + 24))
        list_item.setToolTip(item.image_path)
        self._list_widget.addItem(list_item)

    def _selected_face_ids(self) -> list[int]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._list_widget.selectedItems()
        ]

    def _on_selection_changed(self) -> None:
        count = len(self._list_widget.selectedItems())
        self._selection_label.setText(
            "No items selected" if count == 0 else f"{count} item(s) selected"
        )
        self._restore_btn.setEnabled(count > 0)

    def _on_restore_selected(self) -> None:
        face_ids = self._selected_face_ids()
        if not face_ids:
            return
        self._face_review_service.restore_faces(face_ids)
        self._remove_items(face_ids)

    def _remove_items(self, face_ids: list[int]) -> None:
        """Drop restored items from the list immediately (no re-fetch)."""
        remove_set = set(face_ids)
        for row in reversed(range(self._list_widget.count())):
            list_item = self._list_widget.item(row)
            if (
                list_item is not None
                and list_item.data(Qt.ItemDataRole.UserRole) in remove_set
            ):
                self._list_widget.takeItem(row)
        for face_id in face_ids:
            self._items_by_face_id.pop(face_id, None)
        self._total_found = max(0, self._total_found - len(remove_set))
        self._update_summary(len(self._items_by_face_id), self._total_found)
