"""Fullscreen, click-free slideshow over a list of photos."""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QElapsedTimer, QRect, QRectF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from photo_app.app.workers.detail_image_worker import DetailImageWorker
from photo_app.app.workers.runner import start_worker

if TYPE_CHECKING:
    from collections.abc import Sequence

    from photo_app.app.image_loading import DecodedImage
    from photo_app.services.face_review_service import FaceReviewItem

LOGGER = logging.getLogger(__name__)

DEFAULT_SECONDS = 5
MIN_SECONDS = 1
MAX_SECONDS = 60

# Same colours as the grid's flag badges.
FLAG_COLORS: dict[str, QColor] = {
    "keep": QColor(67, 160, 71),
    "discard": QColor(211, 47, 47),
    "undecided": QColor(117, 117, 117),
}
FLAG_LABELS: dict[str, str] = {
    "keep": "Keep",
    "discard": "Discard",
    "undecided": "Undecided",
}

_TICK_MS = 40
_TOAST_MS = 1200
# With "next photo after flagging" on: long enough to see the badge land on
# the flagged photo, short enough not to feel like waiting.
_FLAG_ADVANCE_MS = 350
_PROGRESS_HEIGHT = 3
_HUD_HEIGHT = 36
_CACHE_ENTRIES = 5
# Ask the caller for more photos when this close to the end of the list.
_MORE_THRESHOLD = 3


@dataclass(frozen=True)
class SlideshowItem:
    """One photo in the slideshow."""

    image_id: int
    file_path: str
    flag: str | None = None


def _to_pixmap(decoded: DecodedImage) -> QPixmap:
    channels = 4 if decoded.has_alpha else 3
    image_format = (
        QImage.Format.Format_RGBA8888
        if decoded.has_alpha
        else QImage.Format.Format_RGB888
    )
    image = QImage(
        decoded.data,
        decoded.width,
        decoded.height,
        decoded.width * channels,
        image_format,
    )
    return QPixmap.fromImage(image)


class SlideshowWindow(QWidget):
    """Shows photos one at a time, advancing on a timer.

    Keys: Space pauses/resumes the current photo's countdown, Left/Right
    step back/forward (restarting the countdown), Up/Down change the time
    per photo, P/X/U/Backspace flag the shown photo, A toggles moving on
    right after P/X/U, Esc closes.

    The countdown only runs while a photo is actually on screen, so a slow
    decode never eats into its viewing time. A thin bar along the bottom
    edge shrinks as the time runs out.
    """

    flag_requested = Signal(int, object)  # image_id, flag (str | None)
    more_requested = Signal()  # near the end of the list; caller may append
    finished = Signal(int)  # image_id shown when closed, or -1
    seconds_changed = Signal(int)
    advance_on_flag_changed = Signal(bool)

    def __init__(
        self,
        items: Sequence[SlideshowItem],
        start_index: int = 0,
        *,
        seconds: int = DEFAULT_SECONDS,
        advance_on_flag: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Slideshow")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.BlankCursor)

        self._items = list(items)
        self._index = max(0, min(start_index, len(self._items) - 1))
        self._duration_ms = max(MIN_SECONDS, min(seconds, MAX_SECONDS)) * 1000
        self._closed = False

        # Countdown: time banked before the last pause plus the running clock.
        self._clock = QElapsedTimer()
        self._banked_ms = 0
        self._paused = False
        self._at_end = False

        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._failed: set[str] = set()
        self._pending: set[str] = set()
        self._scaled: tuple[int, QPixmap] | None = None  # (cacheKey, scaled)
        self._thread_pool = QThreadPool.globalInstance()

        self._toast_text = ""
        self._toast_color = QColor(0, 0, 0)
        self._toast_clock = QElapsedTimer()
        self._toast_is_flag = False

        self._tick = QTimer(self)
        self._tick.setInterval(_TICK_MS)
        self._tick.timeout.connect(self._on_tick)

        self._advance_on_flag = advance_on_flag
        self._flag_advance = QTimer(self)
        self._flag_advance.setSingleShot(True)
        self._flag_advance.setInterval(_FLAG_ADVANCE_MS)
        self._flag_advance.timeout.connect(lambda: self._step(1))

    # -- public API -----------------------------------------------------------

    def start(self) -> None:
        """Go fullscreen and show the first photo."""
        self.showFullScreen()
        self.activateWindow()
        self.setFocus()
        self._tick.start()
        self._show_current()

    def append_items(self, items: Sequence[SlideshowItem]) -> None:
        """Add photos to the end, e.g. the next page of a gallery."""
        if not items:
            return
        self._items.extend(items)
        if self._at_end:
            self._at_end = False
            self._step(1)
        else:
            self._prefetch()

    def current_item(self) -> SlideshowItem | None:
        if not self._items:
            return None
        return self._items[self._index]

    @property
    def seconds(self) -> int:
        return self._duration_ms // 1000

    # -- countdown ------------------------------------------------------------

    def _clock_running(self) -> bool:
        return self._clock.isValid() and not self._paused and not self._at_end

    def _elapsed_ms(self) -> int:
        running = self._clock.elapsed() if self._clock_running() else 0
        return self._banked_ms + running

    def _remaining_ms(self) -> int:
        return max(0, self._duration_ms - self._elapsed_ms())

    def _restart_clock(self) -> None:
        self._banked_ms = 0
        self._clock.start()

    def _stop_clock(self) -> None:
        """Clock waits until the current photo is on screen."""
        self._banked_ms = 0
        self._clock.invalidate()

    def _toggle_pause(self) -> None:
        if self._at_end:
            return
        if self._paused:
            self._paused = False
            if self._clock.isValid():
                self._clock.restart()
        else:
            if self._clock.isValid():
                self._banked_ms += self._clock.elapsed()
            self._paused = True
        self.update()

    def _change_seconds(self, delta: int) -> None:
        seconds = max(MIN_SECONDS, min(self.seconds + delta, MAX_SECONDS))
        self._duration_ms = seconds * 1000
        self.seconds_changed.emit(seconds)
        self._show_toast(f"{seconds} s per photo", QColor(60, 60, 60))

    def _on_tick(self) -> None:
        if self._clock_running() and self._remaining_ms() <= 0:
            if self._index < len(self._items) - 1:
                self._step(1)
            else:
                self._at_end = True
                self.more_requested.emit()
                self.update()
            return
        if self._toast_text:
            self.update()
        else:
            self.update(self._hud_rect())

    # -- navigation and loading -----------------------------------------------

    def _step(self, delta: int) -> None:
        self._flag_advance.stop()  # any move supersedes a pending flag jump
        target = self._index + delta
        if not 0 <= target < len(self._items):
            return
        self._index = target
        self._at_end = False
        # A flag badge belongs to the photo it was set on; never let it
        # linger over the next one, or it reads as that photo's flag.
        if self._toast_is_flag:
            self._toast_text = ""
        self._show_current()

    def _show_current(self) -> None:
        item = self.current_item()
        if item is None:
            self._stop_clock()
            self.update()
            return
        if item.file_path in self._cache:
            self._cache.move_to_end(item.file_path)
            self._restart_clock()
        elif item.file_path in self._failed:
            self._restart_clock()  # show the error briefly, then move on
        else:
            self._stop_clock()
            self._request_decode(item.file_path, current=True)
        self._prefetch()
        if self._index >= len(self._items) - _MORE_THRESHOLD:
            self.more_requested.emit()
        self.update()

    def _prefetch(self) -> None:
        for offset in (1, 2, -1):
            index = self._index + offset
            if not 0 <= index < len(self._items):
                continue
            file_path = self._items[index].file_path
            if file_path in self._cache:
                self._cache.move_to_end(file_path)
            elif file_path not in self._failed:
                self._request_decode(file_path, current=False)

    def _request_decode(self, file_path: str, *, current: bool) -> None:
        if file_path in self._pending:
            return
        self._pending.add(file_path)
        screen = self.screen().size()
        worker = DetailImageWorker(
            file_path, None, (screen.width(), screen.height()), 0
        )
        worker.signals.result_ready.connect(self._on_decoded)
        start_worker(worker, self._thread_pool, priority=1 if current else 0)

    def _on_decoded(
        self,
        file_path: str,
        decoded: DecodedImage | None,
        _faces: list[FaceReviewItem],
        _full_res: bool,  # noqa: FBT001
        _generation: int,
    ) -> None:
        if self._closed:
            return
        self._pending.discard(file_path)
        if decoded is None:
            self._failed.add(file_path)
        else:
            self._cache[file_path] = _to_pixmap(decoded)
            current = self.current_item()
            keep = {current.file_path} if current else set()
            while len(self._cache) > _CACHE_ENTRIES:
                oldest = next(p for p in self._cache if p not in keep)
                del self._cache[oldest]

        current = self.current_item()
        if current is not None and current.file_path == file_path:
            self._restart_clock()
            self.update()

    # -- flagging -------------------------------------------------------------

    def _set_flag(self, flag: str | None) -> None:
        item = self.current_item()
        if item is None:
            return
        self._items[self._index] = replace(item, flag=flag)
        self.flag_requested.emit(item.image_id, flag)
        if flag is None:
            self._show_toast("Flag cleared", QColor(60, 60, 60), is_flag=True)
        else:
            self._show_toast(FLAG_LABELS[flag], FLAG_COLORS[flag], is_flag=True)
            if self._advance_on_flag:
                self._flag_advance.start()

    def _toggle_advance_on_flag(self) -> None:
        self._advance_on_flag = not self._advance_on_flag
        self.advance_on_flag_changed.emit(self._advance_on_flag)
        state = "on" if self._advance_on_flag else "off"
        self._show_toast(f"Next photo after flagging: {state}", QColor(60, 60, 60))

    def _show_toast(self, text: str, color: QColor, *, is_flag: bool = False) -> None:
        self._toast_text = text
        self._toast_color = color
        self._toast_is_flag = is_flag
        self._toast_clock.start()
        self.update()

    # -- events ---------------------------------------------------------------

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = Qt.Key(event.key())
        flag_by_key: dict[Qt.Key, str | None] = {
            Qt.Key.Key_P: "keep",
            Qt.Key.Key_X: "discard",
            Qt.Key.Key_U: "undecided",
            Qt.Key.Key_Backspace: None,
        }
        if key in flag_by_key:
            self._set_flag(flag_by_key[key])
        elif key == Qt.Key.Key_Space:
            self._toggle_pause()
        elif key == Qt.Key.Key_Right:
            self._step(1)
        elif key == Qt.Key.Key_Left:
            self._step(-1)
        elif key == Qt.Key.Key_Up:
            self._change_seconds(1)
        elif key == Qt.Key.Key_Down:
            self._change_seconds(-1)
        elif key == Qt.Key.Key_A:
            self._toggle_advance_on_flag()
        elif key == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closed:
            self._closed = True
            self._tick.stop()
            self._flag_advance.stop()
            self._cache.clear()
            item = self.current_item()
            self.finished.emit(item.image_id if item else -1)
        super().closeEvent(event)

    # -- painting -------------------------------------------------------------

    def _hud_rect(self) -> QRect:
        return QRect(0, self.height() - _HUD_HEIGHT, self.width(), _HUD_HEIGHT)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(event.rect(), QColor(0, 0, 0))

        item = self.current_item()
        if item is None:
            self._draw_message(painter, "No photos to show")
        elif item.file_path in self._cache:
            self._draw_photo(painter, self._cache[item.file_path])
        elif item.file_path in self._failed:
            self._draw_message(painter, f"Could not load {Path(item.file_path).name}")
        else:
            self._draw_message(painter, "Loading…")

        if self._at_end:
            self._draw_message(painter, "End of slideshow — Esc to exit", bottom=True)
        self._draw_hud(painter, item)
        self._draw_toast(painter)
        painter.end()

    def _draw_photo(self, painter: QPainter, pixmap: QPixmap) -> None:
        # Rescale once per photo/size, not on every countdown repaint.
        target = pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        key = pixmap.cacheKey()
        if (
            self._scaled is None
            or self._scaled[0] != key
            or (self._scaled[1].size() != target)
        ):
            scaled = (
                pixmap
                if pixmap.size() == target
                else pixmap.scaled(
                    target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self._scaled = (key, scaled)
        scaled = self._scaled[1]
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)

    def _draw_message(
        self, painter: QPainter, text: str, *, bottom: bool = False
    ) -> None:
        painter.setPen(QColor(200, 200, 200))
        font = QFont(self.font())
        font.setPointSize(14)
        painter.setFont(font)
        rect = self.rect()
        if bottom:
            rect = QRect(0, self.height() - 120, self.width(), 60)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_hud(self, painter: QPainter, item: SlideshowItem | None) -> None:
        width, height = self.width(), self.height()

        # Remaining time: a thin bar along the bottom edge.
        if self._clock.isValid() and not self._at_end:
            fraction = self._remaining_ms() / self._duration_ms
            color = (
                QColor(255, 193, 7, 220) if self._paused else QColor(255, 255, 255, 140)
            )
            painter.fillRect(
                QRect(
                    0,
                    height - _PROGRESS_HEIGHT,
                    round(width * fraction),
                    _PROGRESS_HEIGHT,
                ),
                color,
            )

        font = QFont(self.font())
        font.setPointSize(9)
        painter.setFont(font)
        text_rect = QRect(12, height - _HUD_HEIGHT, width - 24, _HUD_HEIGHT - 8)

        parts = []
        if self._paused:
            parts.append("❚❚ Paused")
        elif self._clock.isValid() and not self._at_end:
            parts.append(f"{(self._remaining_ms() + 999) // 1000} s")
        if self._items:
            parts.append(f"{self._index + 1} / {len(self._items)}")
        painter.setPen(QColor(255, 255, 255, 150))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "   ".join(parts),
        )

        # The shown photo's flag, as a small pill bottom-left.
        if item is not None and item.flag in FLAG_COLORS:
            label = FLAG_LABELS[item.flag]
            pill_width = painter.fontMetrics().horizontalAdvance(label) + 20
            pill = QRectF(12, height - _HUD_HEIGHT + 6, pill_width, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(FLAG_COLORS[item.flag])
            painter.drawRoundedRect(pill, 10, 10)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_toast(self, painter: QPainter) -> None:
        """Briefly confirm a flag or interval change, fading out."""
        if not self._toast_text:
            return
        age = self._toast_clock.elapsed()
        if age >= _TOAST_MS:
            self._toast_text = ""
            return
        opacity = min(1.0, 2.0 * (1.0 - age / _TOAST_MS))

        font = QFont(self.font())
        font.setPointSize(28)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        box_width = metrics.horizontalAdvance(self._toast_text) + 64
        box_height = metrics.height() + 28
        box = QRectF(
            (self.width() - box_width) / 2,
            self.height() * 0.75 - box_height / 2,
            box_width,
            box_height,
        )
        painter.setOpacity(opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        background = QColor(self._toast_color)
        background.setAlpha(225)
        painter.setBrush(background)
        painter.drawRoundedRect(box, 14, 14)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self._toast_text)
        painter.setOpacity(1.0)
