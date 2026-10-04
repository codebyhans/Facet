from __future__ import annotations

import io
import os

from PIL import Image as PILImage
from PySide6.QtWidgets import QApplication

from photo_app.app.widgets.cluster_image_inspector import ClusterImageInspectorWidget


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _png_bytes(size: tuple[int, int] = (20, 20)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", size, color=(1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def test_stale_inspect_result_is_discarded() -> None:
    """A result for an image the user has since navigated away from must not
    overwrite what's currently showing — image decoding now happens on a
    background worker, so rapid clicking through a cluster can produce
    out-of-order results that would otherwise flash a stale image on screen.

    Exercises `_on_inspect_result` directly (bypassing `load_image`'s real
    thread-pool dispatch) so the test is deterministic rather than racing an
    actual background decode.
    """
    _app()
    widget = ClusterImageInspectorWidget()
    widget._current_file_path = "a.jpg"
    widget._request_epoch = 1
    widget._current_file_path = "b.jpg"
    widget._request_epoch = 2

    # A late-arriving result for the first (superseded) request must be
    # ignored, even though it names a real file and would otherwise succeed.
    widget._on_inspect_result("a.jpg", _png_bytes(), 1.0, 1)
    assert widget._face_detection_widget.pixmap().isNull()

    # The current request's result must be applied.
    widget._on_inspect_result("b.jpg", _png_bytes(), 1.0, 2)
    assert not widget._face_detection_widget.pixmap().isNull()


def test_failed_inspect_result_shows_message() -> None:
    """A decode failure (e.g. unreadable file) must surface a clear message
    rather than silently leaving the "Loading…" placeholder forever.
    """
    _app()
    widget = ClusterImageInspectorWidget()
    widget._current_file_path = "broken.heic"
    widget._request_epoch = 1

    widget._on_inspect_result("broken.heic", None, 1.0, 1)
    assert widget._face_detection_widget.text() == "Could not load image"
