"""Image detail viewer panel - integrated into main window."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.widgets.face_detection_widget import FaceDetectionWidget

if TYPE_CHECKING:
    from photo_app.app.models.photo_grid_model import PhotoGridItem
    from photo_app.config.settings import AppSettings
    from photo_app.services.face_review_service import FaceReviewService
    from photo_app.services.settings_service import RuntimeSettings

LOGGER = logging.getLogger(__name__)


class ImageDetailPanel(QWidget):
    """Integrated image viewer panel for the main window.

    Shows:
    - Full image in center with zoom/pan capabilities
    - Image info (filename, dimensions, capture date)
    - Navigation buttons (prev/next image)
    - Close button to return to gallery
    """

    closed = Signal()  # Emitted when user clicks "Back to Gallery"
    image_selected = Signal(int)  # Emitted when user navigates to different image
    reindex_requested = Signal(
        str
    )  # Emitted when user requests re-indexing (file_path)

    def __init__(  # noqa: PLR0915
        self,
        parent: QWidget | None = None,
        settings: AppSettings | RuntimeSettings | None = None,
        face_review_service: FaceReviewService | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._face_review_service = face_review_service
        self._current_item: PhotoGridItem | None = None
        self._items: list[PhotoGridItem] = []
        self._current_index = 0
        self._zoom = 1.0
        self._zoom_mode = "fit"  # "fit" or "100%"

        # Image display using FaceDetectionWidget
        self._image_label = FaceDetectionWidget()
        self._image_label.setStyleSheet("background-color: #1e1e1e; color: white;")

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidget(self._image_label)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStyleSheet(
            "QScrollArea { border: none; background-color: #1e1e1e; }"
        )

        # Info label
        self._info_label = QLabel("No image selected")
        self._info_label.setStyleSheet(
            "color: #ccc; padding: 8px; background-color: #2e2e2e;"
        )
        self._info_label.setWordWrap(True)

        # Navigation and zoom buttons
        nav_layout = QHBoxLayout()

        self._prev_btn = QPushButton("← Previous")
        self._prev_btn.clicked.connect(self._show_previous)

        self._close_btn = QPushButton("Back to Gallery")
        self._close_btn.clicked.connect(self._on_close_requested)

        self._next_btn = QPushButton("Next →")
        self._next_btn.clicked.connect(self._show_next)

        # Zoom mode buttons
        self._zoom_fit_btn = QPushButton("Fit")
        self._zoom_fit_btn.clicked.connect(self._set_zoom_fit)
        self._zoom_fit_btn.setMaximumWidth(60)

        self._zoom_100_btn = QPushButton("100%")
        self._zoom_100_btn.clicked.connect(self._set_zoom_100)
        self._zoom_100_btn.setMaximumWidth(60)

        # Re-index faces button
        self._reindex_btn = QPushButton("Re-index faces")
        self._reindex_btn.setToolTip(
            "Re-run face detection on this image.\n"
            "WARNING: overwrites all manual edits for this image."
        )
        self._reindex_btn.clicked.connect(self._on_reindex_requested)
        self._reindex_btn.setMaximumWidth(120)

        # Face bounding box checkbox
        self._bbox_checkbox = QCheckBox("Show Face Bounding Boxes")
        self._bbox_checkbox.setChecked(False)
        self._bbox_checkbox.stateChanged.connect(self._on_bbox_toggle)

        # Update button style to show active zoom mode
        self._update_zoom_button_styles()

        nav_layout.addWidget(self._prev_btn)
        nav_layout.addWidget(self._zoom_fit_btn)
        nav_layout.addWidget(self._zoom_100_btn)
        nav_layout.addWidget(self._reindex_btn)
        nav_layout.addWidget(self._bbox_checkbox)
        nav_layout.addStretch()
        nav_layout.addWidget(self._close_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self._next_btn)

        # Main layout
        layout = QVBoxLayout(self)
        layout.addWidget(self._info_label)
        layout.addWidget(self._scroll_area, 1)
        layout.addLayout(nav_layout)
        layout.setContentsMargins(0, 0, 0, 0)

        self.setStyleSheet("background-color: #1e1e1e;")

    def load_image(self, items: list[PhotoGridItem], selected_index: int) -> None:
        """Load image list and show image at selected_index.

        Args:
            items: List of PhotoGridItem objects
            selected_index: Index of image to display
        """
        self._items = items
        self._current_index = max(0, min(selected_index, len(items) - 1))
        # Reset zoom based on mode
        if self._zoom_mode == "100%":
            self._zoom = 1.0
        else:  # "fit" mode - will be calculated in _render_current
            self._zoom = 1.0
        self._render_current()

    def get_items(self) -> list[PhotoGridItem]:
        """Get the current items list."""
        return self._items

    def get_current_index(self) -> int:
        """Get the current index."""
        return self._current_index

    def get_reindex_button(self) -> QPushButton:
        """Get the reindex button for enabling/disabling."""
        return self._reindex_btn

    def _calculate_zoom_for_mode(self) -> float:
        """Calculate zoom level based on current zoom mode.

        Returns:
            Zoom factor (1.0 = 100%, 0.5 = 50%, 2.0 = 200%, etc.)
        """
        zoom = 1.0
        if (
            self._items
            and self._current_index < len(self._items)
            and self._zoom_mode == "fit"
        ):
            # Calculate zoom to fit image in viewport
            viewport = self._scroll_area.viewport()
            viewport_width = viewport.width()
            viewport_height = viewport.height()

            if viewport_width > 0 and viewport_height > 0:
                item = self._items[self._current_index]
                try:
                    pixmap = QPixmap(str(Path(item.file_path)))
                    if not pixmap.isNull():
                        # Calculate zoom to fit image with aspect ratio preserved
                        width_ratio = viewport_width / pixmap.width()
                        height_ratio = viewport_height / pixmap.height()
                        zoom = min(width_ratio, height_ratio)

                        # Constrain between 0.2 and 3.0
                        zoom = max(0.2, min(3.0, zoom))
                except Exception:
                    LOGGER.exception("Failed to calculate zoom for %s", item.file_path)
        return zoom

    def _set_zoom_fit(self) -> None:
        """Set zoom mode to fit entire image in viewport."""
        self._zoom_mode = "fit"
        self._zoom = self._calculate_zoom_for_mode()
        self._update_zoom_button_styles()
        self._render_current()

    def _set_zoom_100(self) -> None:
        """Set zoom mode to 100% (1:1 pixel ratio)."""
        self._zoom_mode = "100%"
        self._zoom = 1.0
        self._update_zoom_button_styles()
        self._render_current()

    def _update_zoom_button_styles(self) -> None:
        """Update button styles to indicate active zoom mode."""
        active_style = "background-color: #4a9eff; font-weight: bold;"
        inactive_style = ""

        if self._zoom_mode == "fit":
            self._zoom_fit_btn.setStyleSheet(active_style)
            self._zoom_100_btn.setStyleSheet(inactive_style)
        else:
            self._zoom_fit_btn.setStyleSheet(inactive_style)
            self._zoom_100_btn.setStyleSheet(active_style)

    def _render_current(self) -> None:
        """Render the image at current index."""
        if not self._items:
            self._image_label.setText("No images available")
            self._info_label.setText("No images loaded")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            return

        item = self._items[self._current_index]
        self._current_item = item

        # Load image
        try:
            pixmap = QPixmap(str(Path(item.file_path)))
        except Exception:
            LOGGER.exception("Failed to load image %s", item.file_path)
            self._image_label.setText("Error loading image")
            self._info_label.setText(f"Error: Could not load {item.file_path}")
            return

        if pixmap.isNull():
            self._image_label.setText("Could not load image")
            self._info_label.setText(f"Error: Failed to load {item.file_path}")
        else:
            # For fit mode, recalculate zoom if this is first render of new image
            if self._zoom_mode == "fit":
                self._zoom = self._calculate_zoom_for_mode()

            # Scale image with zoom
            scaled = pixmap.scaled(
                int(pixmap.width() * self._zoom),
                int(pixmap.height() * self._zoom),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image_label.set_image(scaled, zoom_factor=self._zoom)

            # Load and set faces if service available
            if self._face_review_service is not None:
                try:
                    faces = self._face_review_service.faces_for_image_path(
                        item.file_path
                    )
                    self._image_label.set_faces(faces)
                except Exception:
                    LOGGER.exception("Failed to load faces for %s", item.file_path)

            # Update info
            filename = Path(item.file_path).name
            dimensions = f"{pixmap.width()}x{pixmap.height()}"
            date_str = (
                item.capture_date.strftime("%Y-%m-%d")
                if item.capture_date
                else "Unknown date"
            )
            info = f"{filename} • {dimensions} • {date_str}"
            self._info_label.setText(info)

        # Update navigation buttons
        self._prev_btn.setEnabled(self._current_index > 0)
        self._next_btn.setEnabled(self._current_index < len(self._items) - 1)

    def _show_previous(self) -> None:
        """Show previous image."""
        if self._current_index > 0:
            self._current_index -= 1
            self._zoom = self._calculate_zoom_for_mode()
            self._render_current()
            if self._current_item:
                self.image_selected.emit(self._current_item.image_id)

    def _show_next(self) -> None:
        """Show next image."""
        if self._current_index < len(self._items) - 1:
            self._current_index += 1
            self._zoom = self._calculate_zoom_for_mode()
            self._render_current()
            if self._current_item:
                self.image_selected.emit(self._current_item.image_id)

    def _on_close_requested(self) -> None:
        """Return to gallery view."""
        self.closed.emit()

    def _on_bbox_toggle(self, _state: int) -> None:
        """Handle bbox checkbox toggle — load faces on demand if not yet fetched."""
        show = self._bbox_checkbox.isChecked()
        self._image_label.set_show_bboxes(show)
        if (
            show
            and self._face_review_service is not None
            and self._current_item is not None
            and not self._image_label.get_faces()
        ):
            try:
                faces = self._face_review_service.faces_for_image_path(
                    self._current_item.file_path
                )
            except Exception:
                LOGGER.exception(
                    "Failed to load faces for %s", self._current_item.file_path
                )
                faces = []
            self._image_label.set_faces(faces)

    def _on_reindex_requested(self) -> None:
        """Handle re-index faces button click."""
        if self._current_item is None:
            return
        result = QMessageBox.warning(
            self,
            "Re-index faces",
            "This will delete all manually assigned names and edits for faces in "
            "this image and re-run face detection from scratch.\n\n"
            "Use this only to recover accidentally deleted bounding boxes.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.reindex_requested.emit(self._current_item.file_path)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle keyboard shortcuts."""
        if event.key() == Qt.Key.Key_Right:
            self._show_next()
            return
        if event.key() == Qt.Key.Key_Left:
            self._show_previous()
            return
        if event.key() == Qt.Key.Key_Plus or event.key() == Qt.Key.Key_Equal:
            self._zoom = min(3.0, self._zoom + 0.1)
            self._render_current()
            return
        if event.key() == Qt.Key.Key_Minus:
            self._zoom = max(0.2, self._zoom - 0.1)
            self._render_current()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._on_close_requested()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Handle mouse wheel for zoom."""
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(3.0, self._zoom + 0.1)
        elif delta < 0:
            self._zoom = max(0.2, self._zoom - 0.1)
        self._render_current()

    def reset_zoom(self) -> None:
        """Reset zoom to 1.0x."""
        self._zoom = 1.0
        self._render_current()
