"""Widget for displaying images with face bounding boxes overlaid."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel

if TYPE_CHECKING:
    from photo_app.services.face_review_service import FaceReviewItem


class FaceDetectionWidget(QLabel):
    """QLabel subclass that renders face bounding boxes on top of images."""

    face_clicked = Signal(int)  # face_id

    def __init__(self, parent: QLabel | None = None) -> None:
        super().__init__(parent)
        self._original_pixmap: QPixmap | None = None  # Unscaled pixmap
        self._display_pixmap: QPixmap | None = (
            None  # Currently displayed (possibly scaled) pixmap
        )
        self._faces: list[FaceReviewItem] = []
        self._show_bboxes = False
        self._zoom_factor = 1.0  # Scale factor for bounding box coordinates
        self._bbox_colors = [
            QColor(255, 0, 0),  # Red
            QColor(0, 255, 0),  # Green
            QColor(0, 0, 255),  # Blue
            QColor(255, 255, 0),  # Yellow
            QColor(255, 0, 255),  # Magenta
            QColor(0, 255, 255),  # Cyan
        ]
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)

    def set_image(self, pixmap: QPixmap | None, zoom_factor: float = 1.0) -> None:
        """Set the image pixmap and zoom factor.

        Args:
            pixmap: The pixmap to display (may already be scaled)
            zoom_factor: The zoom/scale factor applied to this pixmap (for bbox coordinate scaling)
        """
        self._original_pixmap = pixmap
        self._zoom_factor = zoom_factor
        self._update_display()

    def set_faces(self, faces: list[FaceReviewItem]) -> None:
        """Set the list of faces to display bounding boxes for."""
        self._faces = faces
        self._update_display()

    def get_faces(self) -> list[FaceReviewItem]:
        """Get the current faces list."""
        return self._faces

    def set_show_bboxes(self, show: bool) -> None:  # noqa: FBT001
        """Toggle bounding box visibility."""
        self._show_bboxes = show
        self._update_display()

    @property
    def show_bboxes(self) -> bool:
        """Get bounding box visibility state."""
        return self._show_bboxes

    @show_bboxes.setter
    def show_bboxes(self, value: bool) -> None:
        """Set bounding box visibility state."""
        self.set_show_bboxes(value)

    def _update_display(self) -> None:
        """Update the displayed pixmap with or without bounding boxes."""
        if self._original_pixmap is None:
            self.clear()
            return

        if not self._show_bboxes or not self._faces:
            # Just show the original image
            self.setPixmap(self._original_pixmap)
            return

        # Create a copy to draw on
        display_pixmap = self._original_pixmap.copy()
        painter = QPainter(display_pixmap)
        try:
            self._draw_bounding_boxes(painter, display_pixmap)
        finally:
            painter.end()

        self.setPixmap(display_pixmap)

    def _draw_bounding_boxes(self, painter: QPainter, _pixmap: QPixmap) -> None:
        """Draw bounding boxes on the pixmap, scaled by zoom_factor."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for idx, face in enumerate(self._faces):
            bbox = face.bbox
            color = self._bbox_colors[idx % len(self._bbox_colors)]

            # Scale bbox coordinates by zoom factor
            scaled_x = int(bbox.x * self._zoom_factor)
            scaled_y = int(bbox.y * self._zoom_factor)
            scaled_w = int(bbox.w * self._zoom_factor)
            scaled_h = int(bbox.h * self._zoom_factor)

            # Draw rectangle
            pen = QPen(color)
            pen.setWidth(3)
            painter.setPen(pen)
            painter.drawRect(scaled_x, scaled_y, scaled_w, scaled_h)

            # Draw label background and text
            label_text = face.person_name or f"Unknown face {face.face_id}"
            font = QFont()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)

            metrics = painter.fontMetrics()
            text_width = metrics.horizontalAdvance(label_text)
            text_height = metrics.height()
            padding = 4

            label_rect_x = scaled_x
            label_rect_y = max(0, scaled_y - text_height - padding * 2)
            label_rect_w = text_width + padding * 2
            label_rect_h = text_height + padding * 2

            # Draw semi-transparent background
            painter.setOpacity(0.8)
            painter.fillRect(
                label_rect_x, label_rect_y, label_rect_w, label_rect_h, color
            )
            painter.setOpacity(1.0)

            # Draw text
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(
                label_rect_x + padding,
                label_rect_y + padding + metrics.ascent(),
                label_text,
            )

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse clicks on faces (can extend later for interactivity)."""
        if not self._show_bboxes or not self._original_pixmap:
            return

        click_x = event.position().x()
        click_y = event.position().y()

        # Scale coordinates based on label size vs pixmap size
        label_rect = self.contentsRect()
        scale_x = (
            self._original_pixmap.width() / label_rect.width()
            if label_rect.width() > 0
            else 1.0
        )
        scale_y = (
            self._original_pixmap.height() / label_rect.height()
            if label_rect.height() > 0
            else 1.0
        )

        img_x = int(click_x * scale_x)
        img_y = int(click_y * scale_y)

        # Check if click is inside any face bbox (use original coordinates)
        for face in self._faces:
            bbox = face.bbox
            if (
                bbox.x <= img_x <= bbox.x + bbox.w
                and bbox.y <= img_y <= bbox.y + bbox.h
            ):
                self.face_clicked.emit(face.face_id)
                return

        super().mousePressEvent(event)
