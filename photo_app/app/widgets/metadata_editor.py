"""Right-panel metadata editor with rating, tags, quality, and EXIF info."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.widgets.star_rating import StarRatingWidget
from photo_app.app.widgets.tag_editor import TagEditorWidget

if TYPE_CHECKING:
    from photo_app.domain.models import Image


class MetadataEditorPanel(QWidget):
    """Right panel showing metadata for selected photo."""

    rating_changed = Signal(int)
    tags_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize metadata editor panel."""
        super().__init__(parent)
        self._current_image: Image | None = None
        self._init_ui()

    def _init_ui(self) -> None:
        """Initialize the UI layout."""
        layout = QVBoxLayout(self)

        # Rating widget
        rating_group = QGroupBox("Rating")
        rating_layout = QVBoxLayout()
        self._rating_widget = StarRatingWidget()
        self._rating_widget.rating_changed.connect(self._on_rating_changed)
        rating_layout.addWidget(self._rating_widget)
        rating_group.setLayout(rating_layout)
        layout.addWidget(rating_group)

        # Tags widget
        tags_group = QGroupBox("Tags")
        tags_layout = QVBoxLayout()
        self._tag_editor = TagEditorWidget()
        self._tag_editor.tags_changed.connect(self._on_tags_changed)
        tags_layout.addWidget(self._tag_editor)
        tags_group.setLayout(tags_layout)
        layout.addWidget(tags_group)

        # Quality score
        quality_group = QGroupBox("Image Quality")
        quality_layout = QVBoxLayout()
        self._quality_label = QLabel("Quality: —")
        quality_layout.addWidget(self._quality_label)
        quality_group.setLayout(quality_layout)
        layout.addWidget(quality_group)

        # EXIF info
        exif_group = QGroupBox("EXIF & File Info")
        exif_layout = QVBoxLayout()

        # Create scrollable area for EXIF data
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        exif_container = QWidget()
        exif_container_layout = QVBoxLayout(exif_container)

        self._exif_labels: dict[str, QLabel] = {}
        for field in ["File", "Camera", "Lens", "Date", "GPS", "ISO", "Shutter", "Aperture"]:
            label = QLabel(f"{field}: —")
            label.setWordWrap(True)
            exif_container_layout.addWidget(label)
            self._exif_labels[field] = label

        exif_container_layout.addStretch()
        scroll.setWidget(exif_container)
        exif_layout.addWidget(scroll)
        exif_group.setLayout(exif_layout)
        layout.addWidget(exif_group)

        layout.addStretch()

    def set_image(self, image: Image | None) -> None:
        """Update metadata display for a new image.

        Args:
            image: Image domain model or None to clear
        """
        self._current_image = image

        if image is None:
            self._clear_display()
            return

        # Update rating
        rating = getattr(image, "rating", None) or 0
        self._rating_widget.blockSignals(True)  # noqa: FBT003
        self._rating_widget.set_rating(int(rating))
        self._rating_widget.blockSignals(False)  # noqa: FBT003

        # Update tags
        tags = getattr(image, "tags", []) or []
        self._tag_editor.blockSignals(True)  # noqa: FBT003
        self._tag_editor.set_tags(list(tags))
        self._tag_editor.blockSignals(False)  # noqa: FBT003

        # Update quality score
        quality = getattr(image, "quality_score", None)
        if quality is not None:
            quality_pct = int(quality * 100)
            self._quality_label.setText(f"Quality: {quality_pct}%")
        else:
            self._quality_label.setText("Quality: Not computed")

        # Update EXIF info
        camera = getattr(image, "camera_model", None)
        self._exif_labels["Camera"].setText(f"Camera: {camera or '—'}")

        iso = getattr(image, "iso", None)
        self._exif_labels["ISO"].setText(f"ISO: {iso or '—'}")

        shutter = getattr(image, "shutter_speed", None)
        self._exif_labels["Shutter"].setText(f"Shutter: {shutter or '—'}")

        aperture = getattr(image, "aperture", None)
        self._exif_labels["Aperture"].setText(f"Aperture: {aperture or '—'}")

        datetime_str = getattr(image, "datetime_original", None)
        self._exif_labels["Date"].setText(f"Date: {datetime_str or '—'}")

        lat = getattr(image, "gps_latitude", None)
        lon = getattr(image, "gps_longitude", None)
        if lat and lon:
            self._exif_labels["GPS"].setText(f"GPS: {lat:.4f}, {lon:.4f}")
        else:
            self._exif_labels["GPS"].setText("GPS: —")

        file_path = getattr(image, "file_path", None)
        self._exif_labels["File"].setText(f"File: {file_path or '—'}")

    def _clear_display(self) -> None:
        """Clear all metadata display."""
        self._rating_widget.set_rating(0)
        self._tag_editor.set_tags([])
        self._quality_label.setText("Quality: —")
        for label in self._exif_labels.values():
            label.setText("—")

    def _on_rating_changed(self, rating: int) -> None:
        """Handle rating change."""
        if self._current_image is None:
            return
        self.rating_changed.emit(rating)

    def _on_tags_changed(self, tags: list[str]) -> None:
        """Handle tags change."""
        if self._current_image is None:
            return
        self.tags_changed.emit(tags)

    def get_current_image(self) -> Image | None:
        """Get the currently displayed image."""
        return self._current_image

    def set_rating(self, rating: int) -> None:
        """Set rating without emitting signal."""
        self._rating_widget.blockSignals(True)  # noqa: FBT003
        self._rating_widget.set_rating(rating)
        self._rating_widget.blockSignals(False)  # noqa: FBT003

    def set_tags(self, tags: list[str]) -> None:
        """Set tags without emitting signal."""
        self._tag_editor.blockSignals(True)  # noqa: FBT003
        self._tag_editor.set_tags(tags)
        self._tag_editor.blockSignals(False)  # noqa: FBT003

    def get_tags(self) -> list[str]:
        """Get current tags."""
        return self._tag_editor.get_tags()
