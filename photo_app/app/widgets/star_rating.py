"""Star rating widget for photo metadata editing."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent


class StarRatingWidget(QWidget):
    """Interactive 1-5 star rating widget."""

    rating_changed = Signal(int)  # Emits 1-5 or 0 for unrated

    STAR_SIZE = 24
    STAR_SPACING = 4
    STAR_PADDING = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize star rating widget."""
        super().__init__(parent)
        self._rating = 0
        self._hover_rating = 0
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

        # Set minimum size
        self.setMinimumHeight(self.STAR_SIZE + 4)
        self.setMinimumWidth(self.STAR_SIZE * 5 + self.STAR_SPACING * 4 + 4)

    def set_rating(self, rating: int) -> None:
        """Set the rating without emitting signal."""
        self._rating = max(0, min(5, rating))
        self._hover_rating = 0
        self.update()

    def get_rating(self) -> int:
        """Get current rating (0-5)."""
        return self._rating

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Update hover effect on mouse movement."""
        star = self._get_star_at_position(event.position().x())
        if star != self._hover_rating:
            self._hover_rating = star
            self.update()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle star rating on click."""
        rating = self._get_star_at_position(event.position().x())
        if rating >= 0:
            self._rating = rating
            self._hover_rating = 0
            self.update()
            self.rating_changed.emit(rating)

    @override
    def leaveEvent(self, _event: object) -> None:
        """Clear hover effect when leaving widget."""
        self._hover_rating = 0
        self.update()

    @override
    def paintEvent(self, _event: object) -> None:
        """Paint the star rating."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Determine which stars to highlight
        display_rating = self._hover_rating if self._hover_rating > 0 else self._rating

        x = self.STAR_PADDING
        for star_num in range(1, 6):
            is_filled = star_num <= display_rating
            self._draw_star(
                painter,
                x,
                2,
                is_filled,
            )
            x += self.STAR_SIZE + self.STAR_SPACING

        painter.end()

    def _get_star_at_position(self, x: float) -> int:
        """Calculate which star is at the given x position (1-5, or 0 for none)."""
        if x < self.STAR_PADDING:
            return 0
        x -= self.STAR_PADDING
        for star_num in range(1, 6):
            star_x = (star_num - 1) * (self.STAR_SIZE + self.STAR_SPACING)
            star_right = star_x + self.STAR_SIZE
            if x >= star_x and x < star_right:
                return star_num
        return 0

    def _draw_star(self, painter: QPainter, x: int, y: int, filled: bool) -> None:  # noqa: FBT001
        """Draw a single star."""
        size = self.STAR_SIZE
        center_x = x + size / 2
        center_y = y + size / 2
        radius = size / 2

        # Star points
        points = []
        for i in range(10):
            angle = (i * 36 - 90) * 3.14159 / 180  # Convert to radians
            r = radius if i % 2 == 0 else radius * 0.4

            px = center_x + r * math.cos(angle)
            py = center_y + r * math.sin(angle)
            points.append((px, py))

        # Draw star
        if filled:
            painter.fillPath(
                self._create_polygon_path(points),
                QColor(255, 200, 0),  # Gold for filled
            )
        else:
            painter.fillPath(
                self._create_polygon_path(points),
                QColor(200, 200, 200),  # Light gray for empty
            )

        # Draw border
        painter.setPen(QPen(QColor(100, 100, 100), 1))
        painter.drawPath(self._create_polygon_path(points))

    def _create_polygon_path(self, points: list[tuple[float, float]]) -> QPainterPath:
        """Create a QPainterPath from points."""
        path = QPainterPath()
        if points:
            path.moveTo(points[0][0], points[0][1])
            for px, py in points[1:]:
                path.lineTo(px, py)
            path.closeSubpath()
        return path
