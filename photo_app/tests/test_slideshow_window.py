from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QElapsedTimer, QEvent, Qt, QThreadPool
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from photo_app.app.widgets.slideshow_window import SlideshowItem, SlideshowWindow

if TYPE_CHECKING:
    from collections.abc import Iterator

THIRD_ID = 3
SECOND_ID = 2
APPENDED_ID = 9


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


@pytest.fixture(autouse=True)
def _drain_decodes() -> Iterator[None]:
    """Let decode workers started by a test finish before the next one."""
    yield
    QThreadPool.globalInstance().waitForDone()
    _app().processEvents()


def _press(window: SlideshowWindow, key: Qt.Key) -> None:
    window.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    )


def _items(count: int) -> list[SlideshowItem]:
    return [SlideshowItem(i, f"missing_{i}.jpg") for i in range(1, count + 1)]


def test_arrow_keys_skip_and_stay_in_range() -> None:
    _app()
    window = SlideshowWindow(_items(3), start_index=1)
    _press(window, Qt.Key.Key_Right)
    _press(window, Qt.Key.Key_Right)
    assert window.current_item().image_id == THIRD_ID
    _press(window, Qt.Key.Key_Left)
    assert window.current_item().image_id == SECOND_ID
    window.close()


def test_flag_keys_emit_and_update_current_item() -> None:
    _app()
    window = SlideshowWindow(_items(2))
    flagged: list[tuple[int, object]] = []
    window.flag_requested.connect(
        lambda image_id, flag: flagged.append((image_id, flag))
    )

    _press(window, Qt.Key.Key_P)
    _press(window, Qt.Key.Key_X)
    _press(window, Qt.Key.Key_U)
    _press(window, Qt.Key.Key_Backspace)

    assert flagged == [(1, "keep"), (1, "discard"), (1, "undecided"), (1, None)]
    assert window.current_item().flag is None
    window.close()


def test_space_pauses_countdown() -> None:
    _app()
    window = SlideshowWindow(_items(2), seconds=3)
    window._restart_clock()  # as if the photo were on screen
    _press(window, Qt.Key.Key_Space)
    assert window._paused
    remaining = window._remaining_ms()
    assert window._remaining_ms() == remaining  # clock frozen while paused
    _press(window, Qt.Key.Key_Space)
    assert not window._paused
    window.close()


def test_up_down_change_seconds_within_bounds() -> None:
    _app()
    window = SlideshowWindow(_items(1), seconds=2)
    changes: list[int] = []
    window.seconds_changed.connect(changes.append)
    _press(window, Qt.Key.Key_Up)
    _press(window, Qt.Key.Key_Down)
    _press(window, Qt.Key.Key_Down)
    _press(window, Qt.Key.Key_Down)
    assert changes == [3, 2, 1, 1]
    window.close()


def test_append_resumes_from_end() -> None:
    _app()
    window = SlideshowWindow(_items(1))
    window._at_end = True
    window.append_items([SlideshowItem(APPENDED_ID, "missing_9.jpg")])
    assert window.current_item().image_id == APPENDED_ID


def test_close_reports_current_image() -> None:
    _app()
    window = SlideshowWindow(_items(3), start_index=2)
    ended: list[int] = []
    window.finished.connect(ended.append)
    window.close()
    assert ended == [3]


def _wait(ms: int) -> None:
    app = _app()
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        app.processEvents()


def test_flag_moves_on_when_advance_on_flag_enabled() -> None:
    _app()
    window = SlideshowWindow(_items(3), advance_on_flag=True)
    _press(window, Qt.Key.Key_X)
    _wait(600)
    assert window.current_item().image_id == SECOND_ID
    _press(window, Qt.Key.Key_Backspace)  # clearing a flag stays put
    _wait(600)
    assert window.current_item().image_id == SECOND_ID
    window.close()


def test_flag_stays_when_advance_on_flag_disabled() -> None:
    _app()
    window = SlideshowWindow(_items(3))
    _press(window, Qt.Key.Key_P)
    _wait(600)
    assert window.current_item().image_id == 1
    window.close()


def test_arrow_key_cancels_pending_flag_advance() -> None:
    _app()
    window = SlideshowWindow(_items(3), advance_on_flag=True)
    _press(window, Qt.Key.Key_P)
    _press(window, Qt.Key.Key_Right)
    _wait(600)
    assert window.current_item().image_id == SECOND_ID
    window.close()


def test_a_key_toggles_advance_on_flag() -> None:
    _app()
    window = SlideshowWindow(_items(1))
    changes: list[bool] = []
    window.advance_on_flag_changed.connect(changes.append)
    _press(window, Qt.Key.Key_A)
    _press(window, Qt.Key.Key_A)
    assert changes == [True, False]
    window.close()


def test_flag_badge_hidden_when_photo_changes() -> None:
    _app()
    window = SlideshowWindow(_items(3), seconds=5)
    _press(window, Qt.Key.Key_P)
    assert window._toast_text
    _press(window, Qt.Key.Key_Right)
    assert not window._toast_text
    _press(window, Qt.Key.Key_Up)  # interval feedback survives a jump
    _press(window, Qt.Key.Key_Right)
    assert window._toast_text
    window.close()


def test_newest_first_reverses_dated_and_keeps_undated_last() -> None:
    from datetime import UTC, datetime  # noqa: PLC0415

    from photo_app.app.main_window import MainWindow  # noqa: PLC0415
    from photo_app.app.models.photo_grid_model import PhotoGridItem  # noqa: PLC0415

    def item(image_id: int, year: int | None) -> PhotoGridItem:
        date = datetime(year, 1, 1, tzinfo=UTC) if year else None
        return PhotoGridItem(
            image_id, f"h{image_id}", f"{image_id}.jpg", date, None, None
        )

    # Grid order: oldest first, undated last.
    grid = [item(1, 2019), item(2, 2020), item(3, 2021), item(4, None), item(5, None)]
    ordered = MainWindow._newest_first(grid)
    assert [i.image_id for i in ordered] == [3, 2, 1, 4, 5]
