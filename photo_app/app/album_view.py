from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from photo_app.services.album_service import AlbumPage


class AlbumView(QWidget):
    """Displays basic album summary."""

    def __init__(self) -> None:
        super().__init__()
        self._title = QLabel("Album")
        self._count = QLabel("0 images")
        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._count)

    def set_album_page(self, page: AlbumPage) -> None:
        """Update simple stats for displayed album page."""
        self._count.setText(f"{len(page.items)} images")
