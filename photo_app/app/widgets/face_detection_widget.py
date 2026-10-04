"""Widget for displaying images with face bounding boxes overlaid."""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLabel, QScrollArea, QStyle, QWidget

from photo_app.app.widgets.face_naming import (
    MAX_SUGGESTIONS,
    FaceNamePopup,
    recent_names,
    remember_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from photo_app.services.face_review_service import FaceReviewItem

LOGGER = logging.getLogger(__name__)

_NAMED_COLOR = QColor(76, 175, 80)  # Green
_UNNAMED_COLOR = QColor(255, 167, 38)  # Amber
_ACTIVE_COLOR = QColor(74, 158, 255)  # Accent blue
_SPOTLIGHT_DIM = QColor(0, 0, 0, 120)
_POPUP_GAP = 6
_CROP_MARGIN = 0.25  # Extra context around the face in the popup crop


class FaceDetectionWidget(QLabel):
    """QLabel subclass that renders face bounding boxes on top of images.

    Also hosts "naming mode": clicking a face (or `start_naming()`) opens an
    inline name popup under it, and committing advances to the next unnamed
    face left-to-right. Name changes are applied to the local face list
    immediately; the host only has to persist them via the emitted signals.
    """

    face_clicked = Signal(int)  # face_id
    face_name_committed = Signal(int, str)  # face_id, name
    face_ignore_requested = Signal(int)  # face_id (not a face / not of interest)
    naming_mode_changed = Signal(bool)  # active

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._original_pixmap: QPixmap | None = None  # Displayed (scaled) pixmap
        self._faces: list[FaceReviewItem] = []
        self._show_bboxes = False
        self._zoom_factor = 1.0  # Scale factor for bounding box coordinates
        self._known_names: list[str] = []
        self._suggestion_provider: Callable[[int], list[str]] | None = None
        self._popup: FaceNamePopup | None = None
        self._active_face_id: int | None = None
        self._skipped_face_ids: set[int] = set()
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
        self._faces = list(faces)
        if (
            self._active_face_id is not None
            and self._face(self._active_face_id) is None
        ):
            self.stop_naming()
        self._update_display()

    def get_faces(self) -> list[FaceReviewItem]:
        """Get the current faces list (including names edited in place)."""
        return list(self._faces)

    def set_show_bboxes(self, show: bool) -> None:  # noqa: FBT001
        """Toggle bounding box visibility."""
        self._show_bboxes = show
        if not show:
            self.stop_naming()
        self._update_display()

    @property
    def show_bboxes(self) -> bool:
        """Get bounding box visibility state."""
        return self._show_bboxes

    @show_bboxes.setter
    def show_bboxes(self, value: bool) -> None:
        """Set bounding box visibility state."""
        self.set_show_bboxes(value)

    # ------------------------------------------------------------------
    # Naming mode
    # ------------------------------------------------------------------

    def set_known_names(self, names: list[str]) -> None:
        """Set the library's person names offered by the naming popup."""
        self._known_names = list(names)
        if self._popup is not None:
            self._popup.set_known_names(self._known_names)

    def set_suggestion_provider(
        self, provider: Callable[[int], list[str]] | None
    ) -> None:
        """Set the face_id -> likely names callback used by the naming popup."""
        self._suggestion_provider = provider

    @property
    def is_naming(self) -> bool:
        """Whether the naming popup is currently active."""
        return self._active_face_id is not None

    def start_naming(self, face_id: int | None = None) -> bool:
        """Open the naming popup on `face_id`, or on the first unnamed face.

        Returns False when there is nothing to name.
        """
        if self._original_pixmap is None or not self._faces:
            return False
        if face_id is None:
            face_id = self._next_unnamed_after(None)
            if face_id is None:
                face_id = self._ordered_faces()[0].face_id
        was_naming = self.is_naming
        if not was_naming:
            self._skipped_face_ids.clear()
            self._show_bboxes = True
            # Emitted before the popup opens so hosts can refresh known names.
            self.naming_mode_changed.emit(True)  # noqa: FBT003
        self._focus_face(face_id)
        return True

    def stop_naming(self) -> None:
        """Close the naming popup and leave naming mode."""
        if self._active_face_id is None:
            return
        self._active_face_id = None
        if self._popup is not None:
            self._popup.hide()
        self._update_display()
        self.naming_mode_changed.emit(False)  # noqa: FBT003

    def _ensure_popup(self) -> FaceNamePopup:
        if self._popup is None:
            popup = FaceNamePopup(self)
            popup.set_known_names(self._known_names)
            popup.committed.connect(self._on_popup_committed)
            popup.skip_requested.connect(self._on_popup_skip)
            popup.ignore_requested.connect(self._on_popup_ignore)
            popup.close_requested.connect(self.stop_naming)
            popup.hide()
            self._popup = popup
        return self._popup

    def _focus_face(self, face_id: int) -> None:
        face = self._face(face_id)
        if face is None:
            self.stop_naming()
            return
        self._active_face_id = face_id
        self._update_display()

        ordered = self._ordered_faces()
        position = next(i for i, f in enumerate(ordered) if f.face_id == face_id)
        popup = self._ensure_popup()
        popup.show_face(
            self._face_crop(face),
            f"Face {position + 1} of {len(ordered)}",
            face.person_name,
            self._suggestions_for(face),
        )
        self._position_popup()

    def _suggestions_for(self, face: FaceReviewItem) -> list[str]:
        """Likely names first, then recently used ones.

        Names already on another face in this photo are left out: one person
        can't appear twice in the same picture.
        """
        candidates: list[str] = []
        if self._suggestion_provider is not None:
            try:
                candidates.extend(self._suggestion_provider(face.face_id))
            except Exception:
                LOGGER.exception("Face name suggestions failed for %s", face.face_id)
        candidates.extend(recent_names())

        taken = {
            f.person_name.casefold()
            for f in self._faces
            if f.person_name and f.face_id != face.face_id
        }
        result: list[str] = []
        seen: set[str] = set()
        for name in candidates:
            folded = name.casefold()
            if folded in taken or folded in seen:
                continue
            seen.add(folded)
            result.append(name)
        return result[:MAX_SUGGESTIONS]

    def _on_popup_committed(self, name: str) -> None:
        face_id = self._active_face_id
        if face_id is None:
            return
        self._faces = [
            dataclasses.replace(f, person_name=name) if f.face_id == face_id else f
            for f in self._faces
        ]
        remember_name(name)
        if name.casefold() not in {n.casefold() for n in self._known_names}:
            self.set_known_names([*self._known_names, name])
        self.face_name_committed.emit(face_id, name)
        self._advance_from(face_id)

    def _on_popup_skip(self, step: int) -> None:
        face_id = self._active_face_id
        if face_id is None:
            return
        if step < 0:
            ordered = self._ordered_faces()
            position = next(i for i, f in enumerate(ordered) if f.face_id == face_id)
            self._focus_face(ordered[(position - 1) % len(ordered)].face_id)
            return
        self._skipped_face_ids.add(face_id)
        self._advance_from(face_id)

    def _on_popup_ignore(self) -> None:
        face_id = self._active_face_id
        if face_id is None:
            return
        next_id = self._next_unnamed_after(face_id)
        self._faces = [f for f in self._faces if f.face_id != face_id]
        self.face_ignore_requested.emit(face_id)
        if next_id is None:
            self.stop_naming()
        else:
            self._focus_face(next_id)

    def _advance_from(self, face_id: int) -> None:
        next_id = self._next_unnamed_after(face_id)
        if next_id is None:
            self.stop_naming()
        else:
            self._focus_face(next_id)

    def _next_unnamed_after(self, face_id: int | None) -> int | None:
        """Next unnamed, not-skipped face after `face_id`, wrapping around."""
        ordered = self._ordered_faces()
        start = 0
        if face_id is not None:
            start = next(
                (i + 1 for i, f in enumerate(ordered) if f.face_id == face_id), 0
            )
        for offset in range(len(ordered)):
            face = ordered[(start + offset) % len(ordered)]
            if (
                face.face_id != face_id
                and not face.person_name
                and face.face_id not in self._skipped_face_ids
            ):
                return face.face_id
        return None

    def _ordered_faces(self) -> list[FaceReviewItem]:
        """Faces in reading order: left to right by bbox centre."""
        return sorted(
            self._faces,
            key=lambda f: (f.bbox.x + f.bbox.w / 2, f.bbox.y + f.bbox.h / 2),
        )

    def _face(self, face_id: int) -> FaceReviewItem | None:
        return next((f for f in self._faces if f.face_id == face_id), None)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _pixmap_rect(self) -> QRect:
        """Where the displayed pixmap sits inside this label (it is centred)."""
        if self._original_pixmap is None:
            return QRect()
        size = self._original_pixmap.deviceIndependentSize().toSize()
        return QStyle.alignedRect(
            self.layoutDirection(), self.alignment(), size, self.contentsRect()
        )

    def _face_rect_in_pixmap(self, face: FaceReviewItem) -> QRect:
        bbox = face.bbox
        zoom = self._zoom_factor
        return QRect(
            int(bbox.x * zoom),
            int(bbox.y * zoom),
            int(bbox.w * zoom),
            int(bbox.h * zoom),
        )

    def _face_rect_in_widget(self, face: FaceReviewItem) -> QRect:
        return self._face_rect_in_pixmap(face).translated(self._pixmap_rect().topLeft())

    def _face_at(self, point: QPoint) -> FaceReviewItem | None:
        # Last drawn is on top, so search in reverse for overlapping boxes.
        for face in reversed(self._faces):
            if self._face_rect_in_widget(face).contains(point):
                return face
        return None

    def _face_crop(self, face: FaceReviewItem) -> QPixmap | None:
        if self._original_pixmap is None:
            return None
        rect = self._face_rect_in_pixmap(face)
        margin_x = int(rect.width() * _CROP_MARGIN)
        margin_y = int(rect.height() * _CROP_MARGIN)
        rect = rect.adjusted(-margin_x, -margin_y, margin_x, margin_y)
        return self._original_pixmap.copy(
            rect.intersected(self._original_pixmap.rect())
        )

    def _position_popup(self) -> None:
        """Place the popup under the active face (above if it won't fit)."""
        popup = self._popup
        face = (
            self._face(self._active_face_id)
            if self._active_face_id is not None
            else None
        )
        if popup is None or face is None or not popup.isVisible():
            return
        popup.adjustSize()
        face_rect = self._face_rect_in_widget(face)
        x = face_rect.center().x() - popup.width() // 2
        y = face_rect.bottom() + _POPUP_GAP
        if y + popup.height() > self.height() and face_rect.top() > popup.height():
            y = face_rect.top() - _POPUP_GAP - popup.height()
        x = max(0, min(x, self.width() - popup.width()))
        y = max(0, min(y, self.height() - popup.height()))
        popup.move(x, y)

        scroll_area = self._scroll_area()
        if scroll_area is not None:
            scroll_area.ensureWidgetVisible(popup, 20, 20)

    def _scroll_area(self) -> QScrollArea | None:
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, QScrollArea):
                return widget
            widget = widget.parentWidget()
        return None

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

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
        self._position_popup()

    def _draw_bounding_boxes(self, painter: QPainter, pixmap: QPixmap) -> None:
        """Draw boxes: green named, amber unnamed, spotlight on the active face."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        active = (
            self._face(self._active_face_id)
            if self._active_face_id is not None
            else None
        )
        if active is not None:
            dim = QPainterPath()
            dim.addRect(pixmap.rect().toRectF())
            hole = QPainterPath()
            hole.addRect(self._face_rect_in_pixmap(active).toRectF())
            painter.fillPath(dim.subtracted(hole), _SPOTLIGHT_DIM)

        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        padding = 4

        for number, face in enumerate(self._ordered_faces(), start=1):
            is_active = active is not None and face.face_id == active.face_id
            if is_active:
                color = _ACTIVE_COLOR
            elif face.person_name:
                color = _NAMED_COLOR
            else:
                color = _UNNAMED_COLOR
            rect = self._face_rect_in_pixmap(face)

            pen = QPen(color)
            pen.setWidth(4 if is_active else 3)
            painter.setPen(pen)
            painter.drawRect(rect)

            label_text = face.person_name or f"#{number}"
            text_width = metrics.horizontalAdvance(label_text)
            text_height = metrics.height()
            label_rect = QRect(
                rect.x(),
                max(0, rect.y() - text_height - padding * 2),
                text_width + padding * 2,
                text_height + padding * 2,
            )

            painter.setOpacity(0.8)
            painter.fillRect(label_rect, color)
            painter.setOpacity(1.0)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(
                label_rect.x() + padding,
                label_rect.y() + padding + metrics.ascent(),
                label_text,
            )

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Clicking a face names it; clicking elsewhere ends naming mode."""
        if not self._show_bboxes or not self._original_pixmap:
            super().mousePressEvent(event)
            return

        face = self._face_at(event.position().toPoint())
        if face is None:
            self.stop_naming()
            super().mousePressEvent(event)
            return

        self.face_clicked.emit(face.face_id)
        self.start_naming(face.face_id)
