from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QMouseEvent
from PySide6.QtWidgets import QStyledItemDelegate, QWidget, QStyleOptionViewItem

if TYPE_CHECKING:
    from PySide6.QtCore import QAbstractItemModel, QModelIndex
    from PySide6.QtGui import QMouseEvent


class PhotoGridDelegate(QStyledItemDelegate):
    """Custom delegate for rendering photo thumbnails with flag badges and hover buttons."""

    flagChanged = Signal(object, object)  # index, flag_value

    # Flag colors
    FLAG_COLORS = {
        "keep": QColor(67, 160, 71),      # Green
        "discard": QColor(211, 47, 47),   # Red
        "undecided": QColor(117, 117, 117),  # Grey
    }
    
    # Hover button properties
    BUTTON_SIZE = 24
    BUTTON_SPACING = 8
    BUTTON_MARGIN = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hovered_index: QModelIndex | None = None
        self._hover_buttons_visible = False

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """Paint the item with thumbnail and optional flag badge."""
        # Let the base class handle the background and thumbnail
        super().paint(painter, option, index)
        
        # Get the decoration (thumbnail)
        decoration = index.data(Qt.ItemDataRole.DecorationRole)
        if decoration is None:
            return

        # Calculate thumbnail rect (centered in the item rect)
        thumbnail_rect = self._calculate_thumbnail_rect(option.rect, decoration.size())
        
        # Draw flag badge
        self._draw_flag_badge(painter, option, index, thumbnail_rect)

        # Draw hover buttons if visible
        if self._should_show_hover_buttons(index):
            self._draw_hover_buttons(painter, option, index, thumbnail_rect)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Return the size hint for the item."""
        size = super().sizeHint(option, index)
        # Ensure minimum size for flag badge and hover buttons
        return size.expandedTo(QSize(180, 180))

    def editorEvent(
        self,
        event: QEvent,
        model: QAbstractItemModel,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        """Handle mouse events for hover buttons."""
        if event.type() == QEvent.Type.MouseButtonPress:
            if isinstance(event, QMouseEvent):
                if self._should_show_hover_buttons(index):
                    button_rects = self._get_hover_button_rects(option.rect)
                    for i, rect in enumerate(button_rects):
                        if rect.contains(event.pos()):
                            self._handle_button_click(index, i)
                            return True
        return super().editorEvent(event, model, option, index)

    def _calculate_thumbnail_rect(self, item_rect: QRect, thumbnail_size: QSize) -> QRect:
        """Calculate the centered thumbnail rectangle within the item rectangle."""
        # Leave some padding for the flag badge and hover buttons
        padding = 8
        available_width = item_rect.width() - (padding * 2)
        available_height = item_rect.height() - (padding * 2)
        
        # Scale thumbnail to fit while maintaining aspect ratio
        scale = min(available_width / thumbnail_size.width(), 
                   available_height / thumbnail_size.height())
        
        scaled_width = int(thumbnail_size.width() * scale)
        scaled_height = int(thumbnail_size.height() * scale)
        
        x = item_rect.left() + padding + (available_width - scaled_width) // 2
        y = item_rect.top() + padding + (available_height - scaled_height) // 2
        
        return QRect(x, y, scaled_width, scaled_height)

    def _draw_flag_badge(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex, thumbnail_rect: QRect) -> None:
        """Draw the flag badge in the bottom-left corner of the thumbnail."""
        flag = index.data(Qt.ItemDataRole.UserRole + 4)  # FlagRole
        if flag is None or flag not in self.FLAG_COLORS:
            return

        color = self.FLAG_COLORS[flag]
        
        # Badge size and position
        badge_size = 12
        badge_x = thumbnail_rect.left() + 6
        badge_y = thumbnail_rect.bottom() - badge_size - 6
        
        # Draw badge circle
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(badge_x, badge_y, badge_size, badge_size)
        
        # Draw white border for better visibility
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(badge_x, badge_y, badge_size, badge_size)

    def _draw_hover_buttons(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex, thumbnail_rect: QRect) -> None:
        """Draw hover buttons in the bottom-left corner."""
        button_rects = self._get_hover_button_rects(thumbnail_rect)
        button_labels = ["P", "X", "U", "⌫"]  # Keyboard shortcuts: P=Keep, X=Discard, U=Undecided, Backspace=Clear
        
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        for i, (rect, label) in enumerate(zip(button_rects, button_labels)):
            # Draw button background
            if i == 0:  # P (Keep)
                color = self.FLAG_COLORS["keep"]
            elif i == 1:  # X (Discard)
                color = self.FLAG_COLORS["discard"]
            elif i == 2:  # U (Undecided)
                color = self.FLAG_COLORS["undecided"]
            else:  # ⌫ (Clear)
                color = QColor(200, 200, 200)
            
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect)
            
            # Draw white border
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
            
            # Draw label text
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.setFont(option.font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _get_hover_button_rects(self, thumbnail_rect: QRect) -> list[QRect]:
        """Calculate positions for hover buttons."""
        button_count = 4
        start_x = thumbnail_rect.left() + self.BUTTON_MARGIN
        start_y = thumbnail_rect.bottom() - self.BUTTON_SIZE - self.BUTTON_MARGIN
        
        rects: list[QRect] = []
        for i in range(button_count):
            x = start_x + i * (self.BUTTON_SIZE + self.BUTTON_SPACING)
            rects.append(QRect(x, start_y, self.BUTTON_SIZE, self.BUTTON_SIZE))
        
        return rects

    def _should_show_hover_buttons(self, index: QModelIndex) -> bool:
        """Check if hover buttons should be visible for this index."""
        return self._hovered_index is not None and self._hovered_index == index

    def _handle_button_click(self, index: QModelIndex, button_index: int) -> None:
        """Handle click on hover button and emit signal to update flag."""
        button_labels: list[str | None] = ["keep", "discard", "undecided", None]
        flag_value = button_labels[button_index] if button_index < len(button_labels) else None
        
        self.flagChanged.emit(index, flag_value)
