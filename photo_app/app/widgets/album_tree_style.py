"""Custom QProxyStyle for album tree expand/collapse arrows."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleOptionViewItem


class AlbumTreeStyle(QProxyStyle):
    """Custom style for album tree with dark theme expand/collapse arrows."""

    def drawPrimitive(self, element: QStyle.PrimitiveElement, option: QStyleOptionViewItem, painter: QPainter, widget=None) -> None:
        """Draw custom expand/collapse arrows for album tree."""
        if element == QStyle.PrimitiveElement.PE_IndicatorBranch:
            has_children = bool(option.state & QStyle.StateFlag.State_Children)
            if has_children:
                self._draw_custom_arrow(option, painter)
            return

        # For other elements, use default style
        super().drawPrimitive(element, option, painter, widget)

    def _draw_custom_arrow(self, option: QStyleOptionViewItem, painter: QPainter) -> None:
        """Draw custom dark theme arrow for expand/collapse."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Calculate arrow position and size
        rect = option.rect
        arrow_size = 10
        center_x = rect.center().x()
        center_y = rect.center().y()

        # Determine if expanded or collapsed
        is_expanded = option.state & QStyle.StateFlag.State_Open

        # Set colors based on theme
        arrow_color = QColor("#cccccc")  # Light gray for dark theme
        background_color = QColor("#252526")  # Dark background

        # Draw background circle (optional, for better visibility)
        painter.setBrush(QBrush(background_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPoint(center_x, center_y), 8, 8)

        # Draw arrow
        painter.setBrush(QBrush(arrow_color))
        painter.setPen(QPen(arrow_color, 2, Qt.PenStyle.SolidLine))

        # Create arrow path
        path = QPainterPath()
        if is_expanded:
            # Down arrow (expanded)
            path.moveTo(center_x - arrow_size/2, center_y - arrow_size/2)
            path.lineTo(center_x + arrow_size/2, center_y - arrow_size/2)
            path.lineTo(center_x, center_y + arrow_size/2)
            path.closeSubpath()
        else:
            # Right arrow (collapsed)
            path.moveTo(center_x - arrow_size/2, center_y - arrow_size/2)
            path.lineTo(center_x - arrow_size/2, center_y + arrow_size/2)
            path.lineTo(center_x + arrow_size/2, center_y)
            path.closeSubpath()

        painter.drawPath(path)
        painter.restore()
