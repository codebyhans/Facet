"""Date ruler beside the photo grid, Google Photos style.

Marks are placed by *row*, not linearly by time: a mark for March sits where
March's first photo sits in the grid, so the ruler lines up with scrolling.
Labels are offered coarse to fine (decade ... hour) and a label is only kept
where there is room, so busy periods (many photos) reveal finer dates while
sparse stretches only keep the years.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from photo_app.config.theme import ThemeColors

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from PySide6.QtGui import QMouseEvent, QPaintEvent

    from photo_app.app.widgets.photo_grid import PhotoGridWidget

_RULER_WIDTH = 78
_MARGIN = 10  # top/bottom padding of the track, px
_LABEL_GAP = 26  # minimum vertical distance between two labels, px
_TICK_GAP = 4  # minimum distance between unlabeled year ticks, px
_MAX_BOUNDARIES = 20_000  # skip a level that would generate more than this
_HOURS_MAX_SPAN = timedelta(days=4)  # hour labels only for short views

_COLORS = ThemeColors()


@dataclass(frozen=True)
class TimelineMark:
    """One mark on the ruler; `fraction` is 0 (first row) .. 1 (past last row)."""

    fraction: float
    label: str | None  # None draws a bare tick
    major: bool = False
    muted: bool = False
    when: datetime | None = None  # the boundary (or first photo) it marks
    kind: str = ""  # "anchor", "year", "month", "day", "hour" or "undated"


@dataclass(frozen=True)
class _Level:
    floor: Callable[[datetime], datetime]
    step: Callable[[datetime], datetime]
    fmt: Callable[[datetime], str]
    approx: timedelta
    kind: str
    major: bool = False


def _add_months(value: datetime, months: int) -> datetime:
    index = value.year * 12 + value.month - 1 + months
    return datetime(index // 12, index % 12 + 1, 1)  # noqa: DTZ001


def _year_level(years: int) -> _Level:
    return _Level(
        floor=lambda d: datetime(d.year - d.year % years, 1, 1),  # noqa: DTZ001
        step=lambda d: datetime(d.year + years, 1, 1),  # noqa: DTZ001
        fmt=lambda d: f"{d.year}",
        approx=timedelta(days=365 * years),
        kind="year",
        major=True,
    )


def _month_level(months: int) -> _Level:
    return _Level(
        floor=lambda d: datetime(d.year, d.month - (d.month - 1) % months, 1),  # noqa: DTZ001
        step=lambda d: _add_months(d, months),
        fmt=lambda d: f"{d:%b}",
        approx=timedelta(days=30 * months),
        kind="month",
    )


def _day_floor(d: datetime) -> datetime:
    return datetime(d.year, d.month, d.day)  # noqa: DTZ001


def _hour_level(hours: int) -> _Level:
    return _Level(
        floor=lambda d: _day_floor(d) + timedelta(hours=d.hour - d.hour % hours),
        step=lambda d: d + timedelta(hours=hours),
        fmt=lambda d: f"{d:%H:%M}",
        approx=timedelta(hours=hours),
        kind="hour",
    )


def _day_label(d: datetime) -> str:
    return f"{d.day} {d:%b}"


# Coarse to fine; earlier levels win when two labels compete for space
_LEVELS: tuple[_Level, ...] = (
    _year_level(10),
    _year_level(5),
    _year_level(1),
    _month_level(6),
    _month_level(3),
    _month_level(1),
    _Level(  # weeks start on Monday
        floor=lambda d: _day_floor(d) - timedelta(days=d.weekday()),
        step=lambda d: d + timedelta(days=7),
        fmt=_day_label,
        approx=timedelta(days=7),
        kind="day",
    ),
    _Level(
        floor=_day_floor,
        step=lambda d: d + timedelta(days=1),
        fmt=_day_label,
        approx=timedelta(days=1),
        kind="day",
    ),
    _hour_level(6),
    _hour_level(1),
)


def _naive(value: datetime) -> datetime:
    # Mixed aware/naive values can't be compared; capture dates are wall time
    return value.replace(tzinfo=None)


def format_point_date(value: datetime | None, span: timedelta) -> str:
    """Label for one photo's date, detailed enough for the view's span."""
    if value is None:
        return "Undated"
    if span < timedelta(days=2):
        return f"{value.day} {value:%b %H:%M}"
    return f"{value.day} {value:%b %Y}"


def _anchor_label(oldest: datetime, span: timedelta) -> str:
    if span >= timedelta(days=300):
        return f"{oldest:%b %Y}"
    return format_point_date(oldest, span)


def view_span(dates: Sequence[datetime | None]) -> timedelta:
    """Time between the oldest and newest dated photo (dates sorted ascending)."""
    dated = [d for d in dates if d is not None]
    if not dated:
        return timedelta(0)
    return _naive(dated[-1]) - _naive(dated[0])


def _boundary_rows(level: _Level, dated: Sequence[datetime]) -> dict[int, datetime]:
    """Map the first row on/after each period boundary to that boundary.

    Empty periods collapse onto the same row, and the latest boundary (the
    period that actually has photos) wins.
    """
    rows: dict[int, datetime] = {}
    boundary = level.step(level.floor(dated[0]))
    while boundary <= dated[-1]:
        rows[bisect.bisect_left(dated, boundary)] = boundary
        boundary = level.step(boundary)
    return rows


def _has_room(ys: list[float], y: float, gap: float) -> bool:
    i = bisect.bisect_left(ys, y)
    return (i == len(ys) or ys[i] - y >= gap) and (i == 0 or y - ys[i - 1] >= gap)


class _MarkPlacer:
    """Accepts marks first come, first served, as long as they don't collide."""

    def __init__(self, total_rows: int, track_px: float, label_gap: float) -> None:
        self._total = total_rows
        self._track_px = track_px
        self._label_gap = label_gap
        self._label_ys: list[float] = []
        self._tick_ys: list[float] = []
        self.marks: list[TimelineMark] = []

    def place(  # noqa: PLR0913
        self,
        row: int,
        label: str | None,
        *,
        major: bool,
        muted: bool = False,
        when: datetime | None = None,
        kind: str = "",
    ) -> bool:
        fraction = row / self._total
        y = fraction * self._track_px
        if label is not None:
            if not _has_room(self._label_ys, y, self._label_gap):
                return False
            bisect.insort(self._label_ys, y)
        elif not _has_room(self._tick_ys, y, _TICK_GAP) or not _has_room(
            self._label_ys, y, 1
        ):
            return False
        bisect.insort(self._tick_ys, y)
        self.marks.append(
            TimelineMark(
                fraction, label, major=major, muted=muted, when=when, kind=kind
            )
        )
        return True


def build_timeline_marks(
    dates: Sequence[datetime | None],
    track_px: float,
    *,
    label_gap: float = _LABEL_GAP,
) -> list[TimelineMark]:
    """Choose ruler marks for `dates` (grid order: ascending, undated last)."""
    dated = [_naive(d) for d in dates if d is not None]
    if not dated or track_px <= 0:
        return []
    span = dated[-1] - dated[0]

    placer = _MarkPlacer(len(dates), track_px, label_gap)
    placer.place(
        0, _anchor_label(dated[0], span), major=True, when=dated[0], kind="anchor"
    )
    if len(dated) < len(dates):
        placer.place(len(dated), "Undated", major=True, muted=True, kind="undated")

    for level in _LEVELS:
        if span / level.approx > _MAX_BOUNDARIES:
            break
        # A bare "03:00" is only readable when the view is a day or two
        if level.approx < timedelta(days=1) and span > _HOURS_MAX_SPAN:
            break
        for row, value in _boundary_rows(level, dated).items():
            placed = placer.place(
                row, level.fmt(value), major=level.major, when=value, kind=level.kind
            )
            if not placed and level.major:
                placer.place(row, None, major=True)

    return _add_missing_context(sorted(placer.marks, key=lambda m: m.fraction))


def _add_missing_context(marks: list[TimelineMark]) -> list[TimelineMark]:
    """Spell out the year (or day) a short label relies on but can't see.

    "Jul" under "2015" reads as Jul 2015; if it's really Jul 2016 because the
    2016 label had no room, it becomes "Jul 2016". Same for "03:00" after a
    day change.
    """
    year: int | None = None
    day: date | None = None
    result: list[TimelineMark] = []
    for mark in marks:
        when, label = mark.when, mark.label
        if when is None or label is None:
            result.append(mark)
            continue
        if mark.kind in ("month", "day") and when.year != year:
            label = f"{label} {when.year}"
        elif mark.kind == "hour" and when.date() != day:
            label = f"{_day_label(when)} {label}"
        year = when.year
        if mark.kind in ("anchor", "day", "hour"):
            day = when.date()
        result.append(replace(mark, label=label))
    return result


class TimelineRuler(QWidget):
    """Vertical date ruler for a PhotoGridWidget; click or drag to jump."""

    def __init__(self, grid: PhotoGridWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = grid
        self._dates: list[datetime | None] = []
        self._span = timedelta(0)
        self._marks: list[TimelineMark] = []
        self._marks_track_px = -1.0
        self._hover_y: float | None = None
        self._dragging = False
        # Marks are fractions, so old ones stay usable while a resize settles
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(120)
        self._relayout_timer.timeout.connect(self._rebuild_marks)

        self.setFixedWidth(_RULER_WIDTH)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setVisible(False)

        self._font = QFont(self.font())
        self._font.setPointSizeF(8.5)
        self._major_font = QFont(self._font)
        self._major_font.setBold(True)

        bar = grid.verticalScrollBar()
        bar.valueChanged.connect(self.update)
        bar.rangeChanged.connect(self.update)
        grid.viewport().installEventFilter(self)
        model = grid.model()
        model.modelReset.connect(self.update)
        model.rowsInserted.connect(self.update)

    def set_dates(self, dates: list[datetime | None]) -> None:
        """Replace the view's capture dates (grid order)."""
        self._dates = dates
        self._span = view_span(dates)
        self._marks_track_px = -1.0
        self.setVisible(any(d is not None for d in dates))
        self.update()

    def clear(self) -> None:
        self.set_dates([])

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.update()  # column count may have changed
        return super().eventFilter(watched, event)

    # -- geometry -------------------------------------------------------------

    def _track_px(self) -> float:
        return max(1.0, self.height() - 2 * _MARGIN)

    def _y_for(self, fraction: float) -> float:
        return _MARGIN + fraction * self._track_px()

    def _row_at(self, y: float) -> int:
        fraction = (y - _MARGIN) / self._track_px()
        row = int(fraction * len(self._dates))
        return max(0, min(len(self._dates) - 1, row))

    def _visible_fractions(self) -> tuple[float, float] | None:
        total = len(self._dates)
        row_h = self._grid.row_height()
        if total == 0 or row_h <= 0:
            return None
        cols = self._grid.column_count()
        top = self._grid.verticalScrollBar().value()
        bottom = top + self._grid.viewport().height()
        loaded = self._grid.model().rowCount()
        first = top / row_h * cols
        last = min(bottom / row_h * cols, loaded)
        return min(1.0, first / total), min(1.0, max(first, last) / total)

    # -- painting -------------------------------------------------------------

    def _rebuild_marks(self) -> None:
        self._marks_track_px = self._track_px()
        self._marks = build_timeline_marks(self._dates, self._marks_track_px)
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(_COLORS.background))
        painter.setPen(QColor(_COLORS.border))
        painter.drawLine(0, 0, 0, self.height())
        if not self._dates:
            return

        if self._marks_track_px < 0:
            self._rebuild_marks()
        elif self._track_px() != self._marks_track_px:
            self._relayout_timer.start()

        width = self.width()
        visible = self._visible_fractions()
        if visible is not None:
            y0, y1 = self._y_for(visible[0]), self._y_for(visible[1])
            y1 = max(y1, y0 + 3)
            band = QColor(_COLORS.primary)
            band.setAlpha(45)
            painter.fillRect(QRectF(1, y0, width - 1, y1 - y0), band)
            painter.fillRect(
                QRectF(width - 3, y0, 3, y1 - y0), QColor(_COLORS.primary_light)
            )

        metrics = QFontMetrics(self._font)
        text_right = width - 12
        for mark in self._marks:
            y = self._y_for(mark.fraction)
            color = QColor(
                _COLORS.text_muted
                if mark.muted or mark.label is None
                else _COLORS.text_primary
                if mark.major
                else _COLORS.text_secondary
            )
            painter.setPen(QPen(color, 1))
            tick = 6 if mark.label is not None else 3
            painter.drawLine(QPointF(width - 4 - tick, y), QPointF(width - 4, y))
            if mark.label is None:
                continue
            painter.setFont(self._major_font if mark.major else self._font)
            text_h = metrics.height()
            text_y = min(max(y - text_h / 2, 0), self.height() - text_h)
            painter.drawText(
                QRectF(4, text_y, text_right - 4, text_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                mark.label,
            )

        if self._hover_y is not None:
            self._paint_hover(painter, self._hover_y)

    def _paint_hover(self, painter: QPainter, y: float) -> None:
        y = min(max(y, _MARGIN), self.height() - _MARGIN)
        text = format_point_date(self._dates[self._row_at(y)], self._span)
        painter.setFont(self._major_font)
        metrics = QFontMetrics(self._major_font)
        pill_w = min(self.width() - 4, metrics.horizontalAdvance(text) + 12)
        pill_h = metrics.height() + 4
        pill_y = min(max(y - pill_h / 2, 0), self.height() - pill_h)
        pill = QRectF(self.width() - pill_w - 2, pill_y, pill_w, pill_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_COLORS.primary))
        painter.drawRoundedRect(pill, 4, 4)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    # -- interaction ----------------------------------------------------------

    def _jump_to(self, y: float) -> None:
        if self._dates:
            self._grid.scroll_to_row(self._row_at(y))

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._jump_to(event.position().y())
        super().mousePressEvent(event)

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._hover_y = event.position().y()
        if self._dragging:
            self._jump_to(self._hover_y)
        self.update()
        super().mouseMoveEvent(event)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
        super().mouseReleaseEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        self._hover_y = None
        self.update()
        super().leaveEvent(event)
