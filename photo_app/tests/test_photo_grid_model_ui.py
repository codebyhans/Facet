from __future__ import annotations

import os
from datetime import UTC, datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from photo_app.app.models.photo_grid_model import PhotoGridItem, PhotoGridModel


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def test_photo_grid_pagination_signal() -> None:
    _app()
    model = PhotoGridModel()
    model.append_page(
        [
            PhotoGridItem(1, "h1", "a.jpg", datetime(2020, 1, 1, tzinfo=UTC), 1, 0),
            PhotoGridItem(2, "h2", "b.jpg", datetime(2021, 1, 1, tzinfo=UTC), 1, 1),
        ],
        has_more=True,
    )

    triggered: list[bool] = []
    model.loadMoreRequested.connect(lambda: triggered.append(True))
    model.notify_visible_rows(0, 1)

    assert triggered


def test_photo_grid_lazy_tile_request() -> None:
    _app()
    model = PhotoGridModel()
    model.append_page(
        [PhotoGridItem(42, "hash", "photo.jpg", None, 7, 3)],
        has_more=False,
    )

    requests: list[int] = []
    model.tileRequested.connect(requests.append)

    idx = model.index(0, 0)
    _ = model.data(idx, role=Qt.ItemDataRole.DecorationRole)
    assert requests == [7]

    pixmap = QPixmap(1024, 1024)
    model.set_tile(7, pixmap)
    returned = model.data(idx, role=Qt.ItemDataRole.DecorationRole)
    assert isinstance(returned, QPixmap)
