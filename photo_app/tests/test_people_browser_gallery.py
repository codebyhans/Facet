from __future__ import annotations

import os

from PySide6.QtWidgets import QApplication

from photo_app.app.widgets.people_browser import PeopleBrowser
from photo_app.services.face_review_service import PersonStackSummary

_SAMPLE_COUNT = 20


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _make_stack(sample_count: int = 20, image_count: int = 124) -> PersonStackSummary:
    return PersonStackSummary(
        person_id=1,
        cluster_id=None,
        person_name="Alice",
        face_count=image_count,
        image_count=image_count,
        cover_image_path=None,
        cover_image_id=None,
        sample_image_paths=tuple(f"C:/img_{i}.jpg" for i in range(sample_count)),
        sample_image_ids=tuple(range(sample_count)),
    )


def test_show_person_detail_shows_capped_sample_immediately() -> None:
    """Opening a person's detail view must not block on a DB round trip: the
    capped sample already carried by the stack is shown right away, and the
    view switches immediately, before the full (uncapped) list is fetched.
    """
    _app()
    browser = PeopleBrowser()
    stack = _make_stack(sample_count=20, image_count=124)

    browser.show_person_detail(1, stack)

    assert browser._stacked_widget.currentIndex() == 1
    model = browser._cluster_image_grid.model()
    assert model is not None
    assert model.rowCount() == _SAMPLE_COUNT


def test_gallery_ready_discards_stale_result() -> None:
    """A slow, superseded background fetch (e.g. from a previously opened
    person) must not overwrite the currently displayed gallery.
    """
    _app()
    browser = PeopleBrowser()
    browser._gallery_request_epoch = 5

    browser._on_gallery_ready([100], ["C:/full.jpg"], {}, {}, 4)  # stale
    model = browser._cluster_image_grid.model()
    assert model is not None
    assert model.rowCount() == 0

    browser._on_gallery_ready([100], ["C:/full.jpg"], {}, {}, 5)  # current
    assert model.rowCount() == 1
