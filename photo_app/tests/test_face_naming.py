from __future__ import annotations

import os

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from photo_app.app.widgets import face_naming
from photo_app.app.widgets.face_detection_widget import FaceDetectionWidget
from photo_app.domain.value_objects import BoundingBox
from photo_app.services.face_review_service import FaceReviewItem

_SECOND_FACE = 2
_THIRD_FACE = 3


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _face(face_id: int, x: int, name: str | None = None) -> FaceReviewItem:
    return FaceReviewItem(
        face_id=face_id,
        bbox=BoundingBox(x=x, y=40, w=40, h=40),
        person_id=None,
        person_name=name,
    )


def _widget(faces: list[FaceReviewItem]) -> FaceDetectionWidget:
    _app()
    face_naming._recent_names.clear()
    widget = FaceDetectionWidget()
    widget.resize(600, 300)
    pixmap = QPixmap(400, 200)
    pixmap.fill(Qt.GlobalColor.gray)
    widget.set_image(pixmap, zoom_factor=0.5)
    widget.set_faces(faces)
    widget.show()
    return widget


def _popup_input(widget: FaceDetectionWidget) -> QLineEdit:
    assert widget._popup is not None
    return widget._popup._input


def test_naming_walks_unnamed_faces_left_to_right() -> None:
    """Enter saves and jumps to the next unnamed face in reading order,
    skipping faces that already have a name; the last one ends the mode.
    """
    # Deliberately out of order, with the middle face already named.
    widget = _widget([_face(3, 600), _face(1, 0), _face(2, 300, "Known")])
    committed: list[tuple[int, str]] = []
    widget.face_name_committed.connect(lambda fid, n: committed.append((fid, n)))

    assert widget.start_naming()
    assert widget._active_face_id == 1

    QTest.keyClicks(_popup_input(widget), "Anna")
    QTest.keyClick(_popup_input(widget), Qt.Key.Key_Return)
    assert widget._active_face_id == _THIRD_FACE

    QTest.keyClicks(_popup_input(widget), "Bob")
    QTest.keyClick(_popup_input(widget), Qt.Key.Key_Return)

    assert committed == [(1, "Anna"), (3, "Bob")]
    assert not widget.is_naming
    names = {f.face_id: f.person_name for f in widget.get_faces()}
    assert names == {1: "Anna", 2: "Known", 3: "Bob"}


def test_enter_picks_highlighted_existing_name_and_shift_enter_creates() -> None:
    widget = _widget([_face(1, 0), _face(2, 300)])
    widget.set_known_names(["Annabelle"])
    committed: list[str] = []
    widget.face_name_committed.connect(lambda _fid, n: committed.append(n))

    widget.start_naming()
    QTest.keyClicks(_popup_input(widget), "anna")
    QTest.keyClick(_popup_input(widget), Qt.Key.Key_Return)
    QTest.keyClicks(_popup_input(widget), "anna")
    QTest.keyClick(
        _popup_input(widget), Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier
    )

    assert committed == ["Annabelle", "anna"]


def test_number_keys_pick_suggestions_excluding_names_already_in_photo() -> None:
    widget = _widget([_face(1, 0, "Carl"), _face(2, 300)])
    widget.set_suggestion_provider(lambda _fid: ["Carl", "Dana"])
    committed: list[tuple[int, str]] = []
    widget.face_name_committed.connect(lambda fid, n: committed.append((fid, n)))

    widget.start_naming()
    assert widget._active_face_id == _SECOND_FACE
    # "Carl" is already on face 1, so suggestion 1 is "Dana".
    QTest.keyClick(_popup_input(widget), Qt.Key.Key_1)

    assert committed == [(2, "Dana")]


def test_tab_skips_and_delete_on_empty_ignores_face() -> None:
    widget = _widget([_face(1, 0), _face(2, 300), _face(3, 600)])
    ignored: list[int] = []
    widget.face_ignore_requested.connect(ignored.append)

    widget.start_naming()
    QTest.keyClick(_popup_input(widget), Qt.Key.Key_Tab)
    assert widget._active_face_id == _SECOND_FACE
    QTest.keyClick(_popup_input(widget), Qt.Key.Key_Delete)

    assert ignored == [2]
    assert [f.face_id for f in widget.get_faces()] == [1, 3]
    assert widget._active_face_id == _THIRD_FACE
    QTest.keyClick(_popup_input(widget), Qt.Key.Key_Escape)
    assert not widget.is_naming


def test_click_maps_through_centering_offset_and_zoom() -> None:
    """The pixmap is centred in the label and scaled by the zoom factor;
    clicking the drawn box must hit that face, not whatever sits at the raw
    label coordinates.
    """
    widget = _widget([_face(1, 0), _face(2, 300)])
    widget.set_show_bboxes(True)
    clicked: list[int] = []
    widget.face_clicked.connect(clicked.append)

    # Label 600x300, pixmap 400x200 -> offset (100, 50). Face 2's box is at
    # (300, 40) image px * 0.5 zoom = (150, 20) + offset = (250, 70).
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(260, 80))

    assert clicked == [2]
    assert widget._active_face_id == _SECOND_FACE
