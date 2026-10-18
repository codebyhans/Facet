from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, override

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QMouseEvent,
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QWidget

from photo_app.app.models.photo_grid_model import MediaKind, PhotoGridModel

if TYPE_CHECKING:
    from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex


class PhotoGridDelegate(QStyledItemDelegate):
    """Custom delegate for rendering photo thumbnails with flag badges and hover buttons."""

    flagChanged = Signal(object, object)  # noqa: N815  # index, flag_value

    # Flag colors
    FLAG_COLORS: ClassVar[dict[str, QColor]] = {
        "keep": QColor(67, 160, 71),  # Green
        "discard": QColor(211, 47, 47),  # Red
        "undecided": QColor(117, 117, 117),  # Grey
    }
    CLEAR_BUTTON_COLOR: ClassVar[QColor] = QColor(200, 200, 200)
    MEDIA_BADGE_BACKGROUND: ClassVar[QColor] = QColor(0, 0, 0, 150)
    MEDIA_BADGE_SIZE = 20

    # Hover button properties
    BUTTON_SIZE = 24
    BUTTON_SPACING = 8
    BUTTON_MARGIN = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hovered_index: QModelIndex | QPersistentModelIndex | None = None
        self._hover_buttons_visible = False

    def set_hovered_index(
        self, index: QModelIndex | QPersistentModelIndex | None
    ) -> None:
        """Set the hovered index for the delegate."""
        self._hovered_index = index

    def set_hover_buttons_visible(self, *, visible: bool) -> None:
        """Set whether hover buttons should be visible."""
        self._hover_buttons_visible = visible

    @override
    def initStyleOption(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        """Populate style option but blank the icon out.

        We draw the thumbnail pixmap ourselves in paint() (scaled explicitly
        with SmoothTransformation) instead of letting the base class paint
        the icon — Qt's built-in icon painting refuses to enlarge a raster
        pixmap past its native resolution, which would silently cap zoomed-in
        thumbnails at their on-disk tile size.
        """
        super().initStyleOption(option, index)
        option.icon = QIcon()
        option.decorationSize = QSize(0, 0)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        """Paint the item with thumbnail and optional flag badge."""
        # Let the base class handle the background, selection and text
        # (initStyleOption() above strips the icon so it isn't drawn here).
        super().paint(painter, option, index)

        # Get the decoration (thumbnail)
        decoration = index.data(Qt.ItemDataRole.DecorationRole)
        if decoration is None:
            return

        # Calculate thumbnail rect (centered in the item rect)
        thumbnail_rect = self._calculate_thumbnail_rect(option.rect, decoration.size())

        # Draw the thumbnail ourselves, scaled to fill thumbnail_rect — this
        # is what actually lets zoom enlarge past the tile's native size.
        scaled = decoration.scaled(
            thumbnail_rect.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(thumbnail_rect.topLeft(), scaled)

        # Draw flag badge
        self._draw_flag_badge(painter, option, index, thumbnail_rect)

        # Video / Live Photo badge (plain stills get none), RAW pill left of it
        badge_left = self._draw_media_badge(painter, index, thumbnail_rect)
        if index.data(PhotoGridModel.HasRawRole):
            self._draw_raw_badge(painter, option, badge_left, thumbnail_rect.top() + 6)

        # Draw hover buttons if visible
        if self._should_show_hover_buttons(index):
            self._draw_hover_buttons(painter, option, index, thumbnail_rect)

    @override
    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        """Return the size hint for the item.

        QIcon only ever scales a pixmap down to fit a requested size, never up
        past its native resolution — so super().sizeHint() silently caps out at
        the decoration's native pixmap size. Expanding to option.decorationSize
        (the view's current, possibly zoomed, iconSize) instead of a fixed
        constant lets items grow past that native resolution when zoomed in.
        """
        size = super().sizeHint(option, index)
        return size.expandedTo(option.decorationSize)

    @override
    def editorEvent(
        self,
        event: QEvent,
        model: QAbstractItemModel,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        """Handle mouse events for hover buttons."""
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and self._should_show_hover_buttons(index)
        ):
            button_rects = self._get_hover_button_rects(option.rect)
            for i, rect in enumerate(button_rects):
                if rect.contains(event.pos()):
                    self._handle_button_click(index, i)
                    return True
        return super().editorEvent(event, model, option, index)

    def _calculate_thumbnail_rect(
        self, item_rect: QRect, thumbnail_size: QSize
    ) -> QRect:
        """Calculate the centered thumbnail rectangle within the item rectangle."""
        # Leave some padding for the flag badge and hover buttons
        padding = 8
        available_width = item_rect.width() - (padding * 2)
        available_height = item_rect.height() - (padding * 2)

        # Scale thumbnail to fit while maintaining aspect ratio
        scale = min(
            available_width / thumbnail_size.width(),
            available_height / thumbnail_size.height(),
        )

        scaled_width = int(thumbnail_size.width() * scale)
        scaled_height = int(thumbnail_size.height() * scale)

        x = item_rect.left() + padding + (available_width - scaled_width) // 2
        y = item_rect.top() + padding + (available_height - scaled_height) // 2

        return QRect(x, y, scaled_width, scaled_height)

    def _draw_flag_badge(
        self,
        painter: QPainter,
        _option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
        thumbnail_rect: QRect,
    ) -> None:
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

    def _draw_media_badge(
        self,
        painter: QPainter,
        index: QModelIndex | QPersistentModelIndex,
        thumbnail_rect: QRect,
    ) -> int:
        """Draw a play triangle (video) or Live Photo rings in the top-right corner.

        Returns the x where the next badge to the left should end.
        """
        right_edge = thumbnail_rect.right() - 6
        kind = index.data(PhotoGridModel.MediaKindRole)
        if kind not in (MediaKind.VIDEO, MediaKind.LIVE):
            return right_edge

        size = self.MEDIA_BADGE_SIZE
        badge = QRectF(
            thumbnail_rect.right() - size - 6, thumbnail_rect.top() + 6, size, size
        )
        center = badge.center()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.MEDIA_BADGE_BACKGROUND))
        painter.drawEllipse(badge)

        white = QColor(255, 255, 255)
        if kind == MediaKind.VIDEO:
            # Play triangle, nudged right so it looks optically centred
            r = size * 0.28
            cx = center.x() + r * 0.2
            painter.setBrush(QBrush(white))
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(cx - r * 0.8, center.y() - r),
                        QPointF(cx - r * 0.8, center.y() + r),
                        QPointF(cx + r, center.y()),
                    ]
                )
            )
        else:
            # Live Photo: solid dot inside a ring
            painter.setBrush(QBrush(white))
            painter.drawEllipse(center, size * 0.12, size * 0.12)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(white, 1.5))
            painter.drawEllipse(center, size * 0.3, size * 0.3)
        painter.restore()
        return right_edge - size - 4

    def _draw_raw_badge(
        self, painter: QPainter, option: QStyleOptionViewItem, right: int, top: int
    ) -> None:
        """Draw a small "RAW" pill ending at `right`: a RAW file exists for this shot."""
        font = QFont(option.font)
        font.setPixelSize(10)
        font.setBold(True)
        text_width = QFontMetrics(font).horizontalAdvance("RAW")
        size = self.MEDIA_BADGE_SIZE
        width = text_width + 12
        pill = QRectF(right - width, top, width, size)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.MEDIA_BADGE_BACKGROUND))
        painter.drawRoundedRect(pill, size / 2, size / 2)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.setFont(font)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, "RAW")
        painter.restore()

    def _draw_hover_buttons(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        _index: QModelIndex | QPersistentModelIndex,
        thumbnail_rect: QRect,
    ) -> None:
        """Draw hover buttons in the bottom-left corner."""
        button_rects = self._get_hover_button_rects(thumbnail_rect)
        button_specs: list[tuple[str, QColor]] = [
            ("P", self.FLAG_COLORS["keep"]),
            ("X", self.FLAG_COLORS["discard"]),
            ("U", self.FLAG_COLORS["undecided"]),
            ("⌫", self.CLEAR_BUTTON_COLOR),
        ]

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for rect, (label, color) in zip(button_rects, button_specs, strict=False):
            # Draw button background
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

    def _should_show_hover_buttons(
        self, index: QModelIndex | QPersistentModelIndex
    ) -> bool:
        """Check if hover buttons should be visible for this index."""
        return self._hovered_index is not None and self._hovered_index == index

    def _handle_button_click(
        self, index: QModelIndex | QPersistentModelIndex, button_index: int
    ) -> None:
        """Handle click on hover button and emit signal to update flag."""
        button_labels: list[str | None] = ["keep", "discard", "undecided", None]
        flag_value = (
            button_labels[button_index] if button_index < len(button_labels) else None
        )

        self.flagChanged.emit(index, flag_value)
