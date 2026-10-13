"""Album tree item delegate that draws an image-count pill after the name."""

from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from photo_app.app.models.album_tree_model import AlbumTreeModel

_PILL_H_PADDING = 6
_PILL_V_MARGIN = 3
_PILL_RIGHT_MARGIN = 6
_PILL_GAP = 8
_PILL_BACKGROUND = QColor("#3C3C3C")
_PILL_TEXT = QColor("#A0A0A0")


class AlbumTreeDelegate(QStyledItemDelegate):
    """Paints the album name as usual plus a right-aligned count pill."""

    @override
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        text = self._count_text(index)
        if text is None:
            super().paint(painter, option, index)
            return

        font = self._pill_font(option)
        metrics = QFontMetrics(font)
        pill_width = metrics.horizontalAdvance(text) + 2 * _PILL_H_PADDING
        pill_height = min(
            metrics.height() + 2, option.rect.height() - 2 * _PILL_V_MARGIN
        )
        # Pin to the visible edge so pills line up even if the row is wider.
        right = option.rect.right()
        if isinstance(option.widget, QAbstractItemView):
            right = min(right, option.widget.viewport().rect().right())
        pill = QRect(
            right - _PILL_RIGHT_MARGIN - pill_width,
            option.rect.center().y() - pill_height // 2,
            pill_width,
            pill_height,
        )

        # Shrink the name's rect so long names elide before the pill.
        name_option = QStyleOptionViewItem(option)
        name_option.rect = QRect(option.rect)
        name_option.rect.setRight(pill.left() - _PILL_GAP)
        super().paint(painter, name_option, index)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)  # noqa: FBT003
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_PILL_BACKGROUND)
        radius = pill.height() / 2
        painter.drawRoundedRect(pill, radius, radius)
        painter.setFont(font)
        painter.setPen(_PILL_TEXT)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    @staticmethod
    def _pill_font(option: QStyleOptionViewItem) -> QFont:
        font = QFont(option.font)
        font.setPointSizeF(max(font.pointSizeF() * 0.85, 7.0))
        return font

    @staticmethod
    def _count_text(index: QModelIndex | QPersistentModelIndex) -> str | None:
        count = index.data(AlbumTreeModel.ImageCountRole)
        if not isinstance(count, int):
            return None
        return f"{count:,}"
