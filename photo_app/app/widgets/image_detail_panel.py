"""Image detail viewer panel - integrated into main window."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QKeyEvent, QPixmap, QWheelEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.widgets.face_detection_widget import FaceDetectionWidget
from photo_app.app.widgets.face_naming import suggestion_provider_for
from photo_app.app.workers.detail_image_worker import DetailImageWorker

if TYPE_CHECKING:
    from photo_app.app.models.photo_grid_model import PhotoGridItem
    from photo_app.config.settings import AppSettings
    from photo_app.services.face_review_service import (
        FaceReviewItem,
        FaceReviewService,
    )
    from photo_app.services.settings_service import RuntimeSettings

LOGGER = logging.getLogger(__name__)


class ImageDetailPanel(QWidget):
    """Integrated image viewer panel for the main window.

    Shows:
    - Full image in center with zoom/pan capabilities
    - Image info (filename, dimensions, capture date)
    - Navigation buttons (prev/next image)
    - Close button to return to gallery
    """

    closed = Signal()  # Emitted when user clicks "Back to Gallery"
    image_selected = Signal(int)  # Emitted when user navigates to different image
    reindex_requested = Signal(
        str
    )  # Emitted when user requests re-indexing (file_path)
    faces_changed = Signal()  # Face names edited; emitted once per naming session

    def __init__(  # noqa: PLR0915
        self,
        parent: QWidget | None = None,
        settings: AppSettings | RuntimeSettings | None = None,
        face_review_service: FaceReviewService | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._face_review_service = face_review_service
        self._current_item: PhotoGridItem | None = None
        self._items: list[PhotoGridItem] = []
        self._current_index = 0
        self._zoom = 1.0
        self._zoom_mode = "fit"  # "fit" or "100%"

        # Full-resolution decode of the currently-shown item, cached so
        # zoom changes just rescale it instead of re-decoding from disk.
        self._current_pixmap: QPixmap | None = None
        self._current_faces: list[FaceReviewItem] = []
        self._thread_pool = QThreadPool.globalInstance()
        self._request_epoch = 0
        self._active_detail_worker: DetailImageWorker | None = None
        self._faces_dirty = False

        # Image display using FaceDetectionWidget
        self._image_label = FaceDetectionWidget()
        self._image_label.setStyleSheet("background-color: #1e1e1e; color: white;")
        self._image_label.face_name_committed.connect(self._on_face_name_committed)
        self._image_label.face_ignore_requested.connect(self._on_face_ignored)
        self._image_label.naming_mode_changed.connect(self._on_naming_mode_changed)
        if face_review_service is not None:
            self._image_label.set_suggestion_provider(
                suggestion_provider_for(face_review_service)
            )

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidget(self._image_label)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStyleSheet(
            "QScrollArea { border: none; background-color: #1e1e1e; }"
        )

        # Video player for Live Photo sidecar MOVs
        self._video_widget = QVideoWidget()
        self._video_widget.setStyleSheet("background-color: #1e1e1e;")
        self._audio_output = QAudioOutput()
        self._media_player = QMediaPlayer()
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.setVideoOutput(self._video_widget)
        self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)

        # Stacked widget: page 0 = still image, page 1 = video
        self._media_stack = QStackedWidget()
        self._media_stack.addWidget(self._scroll_area)
        self._media_stack.addWidget(self._video_widget)

        # Info label
        self._info_label = QLabel("No image selected")
        self._info_label.setStyleSheet(
            "color: #ccc; padding: 8px; background-color: #2e2e2e;"
        )
        self._info_label.setWordWrap(True)

        # Navigation and zoom buttons
        nav_layout = QHBoxLayout()

        self._prev_btn = QPushButton("← Previous")
        self._prev_btn.clicked.connect(self._show_previous)

        self._close_btn = QPushButton("Back to Gallery")
        self._close_btn.clicked.connect(self._on_close_requested)

        self._next_btn = QPushButton("Next →")
        self._next_btn.clicked.connect(self._show_next)

        # Zoom mode buttons
        self._zoom_fit_btn = QPushButton("Fit")
        self._zoom_fit_btn.clicked.connect(self._set_zoom_fit)
        self._zoom_fit_btn.setMaximumWidth(60)

        self._zoom_100_btn = QPushButton("100%")
        self._zoom_100_btn.clicked.connect(self._set_zoom_100)
        self._zoom_100_btn.setMaximumWidth(60)

        # Live Photo playback buttons
        self._live_btn = QPushButton("▶ Live Photo")
        self._live_btn.setToolTip("Play Live Photo video")
        self._live_btn.clicked.connect(self._play_live)
        self._live_btn.setVisible(False)

        self._stop_btn = QPushButton("⏹ Stop")
        self._stop_btn.clicked.connect(self._stop_live)
        self._stop_btn.setVisible(False)

        # Re-index faces button
        self._reindex_btn = QPushButton("Re-index faces")
        self._reindex_btn.setToolTip(
            "Re-run face detection on this image.\n"
            "WARNING: overwrites all manual edits for this image."
        )
        self._reindex_btn.clicked.connect(self._on_reindex_requested)
        self._reindex_btn.setMaximumWidth(120)

        self._name_faces_btn = QPushButton("Name faces (N)")
        self._name_faces_btn.setToolTip(
            "Name each face in turn: type a name and press Enter to move on.\n"
            "Tip: click any face on the photo to name or fix just that one."
        )
        self._name_faces_btn.clicked.connect(self.start_naming)

        # Face bounding box checkbox
        self._bbox_checkbox = QCheckBox("Show Face Bounding Boxes")
        self._bbox_checkbox.setChecked(False)
        self._bbox_checkbox.stateChanged.connect(self._on_bbox_toggle)

        # Update button style to show active zoom mode
        self._update_zoom_button_styles()

        nav_layout.addWidget(self._prev_btn)
        nav_layout.addWidget(self._zoom_fit_btn)
        nav_layout.addWidget(self._zoom_100_btn)
        nav_layout.addWidget(self._live_btn)
        nav_layout.addWidget(self._stop_btn)
        nav_layout.addWidget(self._reindex_btn)
        nav_layout.addWidget(self._name_faces_btn)
        nav_layout.addWidget(self._bbox_checkbox)
        nav_layout.addStretch()
        nav_layout.addWidget(self._close_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self._next_btn)

        # Main layout
        layout = QVBoxLayout(self)
        layout.addWidget(self._info_label)
        layout.addWidget(self._media_stack, 1)
        layout.addLayout(nav_layout)
        layout.setContentsMargins(0, 0, 0, 0)

        self.setStyleSheet("background-color: #1e1e1e;")

    def load_image(self, items: list[PhotoGridItem], selected_index: int) -> None:
        """Load image list and show image at selected_index.

        Args:
            items: List of PhotoGridItem objects
            selected_index: Index of image to display
        """
        self._items = items
        self._current_index = max(0, min(selected_index, len(items) - 1))
        # Reset zoom based on mode
        if self._zoom_mode == "100%":
            self._zoom = 1.0
        else:  # "fit" mode - will be calculated in _render_current
            self._zoom = 1.0
        self._render_current()

    def get_items(self) -> list[PhotoGridItem]:
        """Get the current items list."""
        return self._items

    def get_current_index(self) -> int:
        """Get the current index."""
        return self._current_index

    def get_reindex_button(self) -> QPushButton:
        """Get the reindex button for enabling/disabling."""
        return self._reindex_btn

    def _calculate_zoom_for_mode(self, pixmap: QPixmap | None = None) -> float:
        """Calculate zoom level based on current zoom mode.

        Args:
            pixmap: Already-loaded pixmap to use for dimension calculation.
                    If None, returns 1.0 (used as a sentinel for non-fit modes).

        Returns:
            Zoom factor (1.0 = 100%, 0.5 = 50%, 2.0 = 200%, etc.)
        """
        if self._zoom_mode != "fit" or pixmap is None or pixmap.isNull():
            return 1.0

        viewport = self._scroll_area.viewport()
        viewport_width = viewport.width()
        viewport_height = viewport.height()

        if viewport_width <= 0 or viewport_height <= 0:
            return 1.0

        width_ratio = viewport_width / pixmap.width()
        height_ratio = viewport_height / pixmap.height()
        return max(0.2, min(3.0, width_ratio, height_ratio))

    def _set_zoom_fit(self) -> None:
        """Set zoom mode to fit entire image in viewport."""
        self._zoom_mode = "fit"
        self._update_zoom_button_styles()
        self._apply_zoom_to_current_pixmap()

    def _set_zoom_100(self) -> None:
        """Set zoom mode to 100% (1:1 pixel ratio)."""
        self._zoom_mode = "100%"
        self._zoom = 1.0
        self._update_zoom_button_styles()
        self._apply_zoom_to_current_pixmap()

    def _update_zoom_button_styles(self) -> None:
        """Update button styles to indicate active zoom mode."""
        active_style = "background-color: #4a9eff; font-weight: bold;"
        inactive_style = ""

        if self._zoom_mode == "fit":
            self._zoom_fit_btn.setStyleSheet(active_style)
            self._zoom_100_btn.setStyleSheet(inactive_style)
        else:
            self._zoom_fit_btn.setStyleSheet(inactive_style)
            self._zoom_100_btn.setStyleSheet(active_style)

    def _render_current(self) -> None:
        """Show the item at the current index, decoding it off the UI thread.

        `QPixmap(path)` can't decode HEIC at all (it spends real time
        probing formats before giving up), and even a full-resolution JPEG
        decode is enough to make clicking through the gallery feel like the
        app is freezing — so the decode (and this image's face lookup, a
        second DB round-trip the click path used to pay for synchronously
        too) both run on a thread-pool worker instead. Only this initial
        load re-decodes from disk; zoom changes reuse the cached pixmap via
        `_apply_zoom_to_current_pixmap()`.
        """
        if not self._items:
            self._image_label.setText("No images available")
            self._info_label.setText("No images loaded")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            return

        self._image_label.stop_naming()
        item = self._items[self._current_index]
        self._current_item = item
        self._current_pixmap = None
        self._current_faces = []

        # Stop any playing video and return to still image page
        self._stop_live(restore_live_btn=False)

        # Show/hide Live Photo button based on sidecar presence
        live_mov = self._find_live_mov(item.file_path)
        self._live_btn.setVisible(live_mov is not None)
        self._live_btn.setProperty(
            "_live_mov_path", str(live_mov) if live_mov else None
        )

        self._image_label.setText("Loading image…")

        self._request_epoch += 1
        worker = DetailImageWorker(
            item.file_path, self._face_review_service, self._request_epoch
        )
        worker.signals.result_ready.connect(self._on_detail_image_ready)
        self._active_detail_worker = worker  # keep alive until it finishes
        self._thread_pool.start(worker)

        # Update navigation buttons
        self._prev_btn.setEnabled(self._current_index > 0)
        self._next_btn.setEnabled(self._current_index < len(self._items) - 1)

    def _on_detail_image_ready(
        self,
        file_path: str,
        png_bytes: bytes | None,
        faces: list[FaceReviewItem],
        request_id: int,
    ) -> None:
        """Apply a decoded detail image, discarding stale/superseded results."""
        if (
            request_id != self._request_epoch
            or self._current_item is None
            or self._current_item.file_path != file_path
        ):
            return  # user has since navigated to a different image

        if png_bytes is None:
            self._image_label.setText("Could not load image")
            self._info_label.setText(f"Error: Failed to load {file_path}")
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(png_bytes) or pixmap.isNull():
            self._image_label.setText("Could not load image")
            self._info_label.setText(f"Error: Failed to load {file_path}")
            return

        self._current_pixmap = pixmap
        self._current_faces = faces
        self._apply_zoom_to_current_pixmap()

    def _apply_zoom_to_current_pixmap(self) -> None:
        """Rescale the already-decoded current pixmap to the active zoom.

        Cheap (no disk I/O or PIL decode) — safe to call on every zoom-mode
        toggle, +/- keypress, or scroll-wheel step.
        """
        pixmap = self._current_pixmap
        item = self._current_item
        if pixmap is None or item is None:
            return

        if self._zoom_mode == "fit":
            self._zoom = self._calculate_zoom_for_mode(pixmap)

        scaled = pixmap.scaled(
            int(pixmap.width() * self._zoom),
            int(pixmap.height() * self._zoom),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.set_image(scaled, zoom_factor=self._zoom)
        self._image_label.set_faces(self._current_faces)

        filename = Path(item.file_path).name
        dimensions = f"{pixmap.width()}x{pixmap.height()}"
        date_str = (
            item.capture_date.strftime("%Y-%m-%d %H:%M")
            if item.capture_date
            else "Unknown date"
        )
        self._info_label.setText(f"{filename} • {dimensions} • {date_str}")

    def _find_live_mov(self, image_path: str) -> Path | None:
        """Return path to matching Live Photo MOV sidecar, or None."""
        p = Path(image_path)
        for suffix in (".mov", ".MOV"):
            candidate = p.with_suffix(suffix)
            if candidate.exists():
                return candidate
        return None

    def _play_live(self) -> None:
        """Switch to video player and play the Live Photo MOV."""
        mov_path = self._live_btn.property("_live_mov_path")
        if not mov_path:
            return
        self._media_player.setSource(QUrl.fromLocalFile(mov_path))
        self._media_stack.setCurrentIndex(1)
        self._live_btn.setVisible(False)
        self._stop_btn.setVisible(True)
        self._media_player.play()

    def _stop_live(self, *, restore_live_btn: bool = True) -> None:
        """Stop video playback and return to still image."""
        self._media_player.stop()
        self._media_stack.setCurrentIndex(0)
        self._stop_btn.setVisible(False)
        if restore_live_btn and self._current_item is not None:
            self._live_btn.setVisible(
                self._live_btn.property("_live_mov_path") is not None
            )

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """Return to still image when video finishes."""
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._media_stack.setCurrentIndex(0)
            self._stop_btn.setVisible(False)
            if self._current_item is not None:
                self._live_btn.setVisible(
                    self._live_btn.property("_live_mov_path") is not None
                )

    def _show_previous(self) -> None:
        """Show previous image."""
        if self._current_index > 0:
            self._current_index -= 1
            self._render_current()
            if self._current_item:
                self.image_selected.emit(self._current_item.image_id)

    def _show_next(self) -> None:
        """Show next image."""
        if self._current_index < len(self._items) - 1:
            self._current_index += 1
            self._render_current()
            if self._current_item:
                self.image_selected.emit(self._current_item.image_id)

    def _on_close_requested(self) -> None:
        """Return to gallery view."""
        self._image_label.stop_naming()
        self._stop_live(restore_live_btn=False)
        self.closed.emit()

    def _on_bbox_toggle(self, _state: int) -> None:
        """Handle bbox checkbox toggle — load faces on demand if not yet fetched."""
        show = self._bbox_checkbox.isChecked()
        self._image_label.set_show_bboxes(show)
        if not show or self._current_item is None or self._image_label.get_faces():
            return
        if self._current_faces:
            # Already fetched by the current image's decode worker.
            self._image_label.set_faces(self._current_faces)
            return
        if self._face_review_service is None:
            return
        # Rare race: the checkbox was toggled before the async decode
        # finished. Falls back to a direct fetch rather than no-op'ing.
        try:
            faces = self._face_review_service.faces_for_image_path(
                self._current_item.file_path
            )
        except Exception:
            LOGGER.exception(
                "Failed to load faces for %s", self._current_item.file_path
            )
            faces = []
        self._current_faces = faces
        self._image_label.set_faces(faces)

    def start_naming(self) -> None:
        """Enter naming mode on the first unnamed face of the current photo."""
        if self._media_stack.currentIndex() != 0:
            return
        if not self._image_label.start_naming():
            self._info_label.setText("No faces detected in this photo")

    def _on_naming_mode_changed(self, active: bool) -> None:  # noqa: FBT001
        if active:
            self._bbox_checkbox.setChecked(True)
            self._refresh_known_names()
            return
        self.setFocus()
        if self._faces_dirty:
            self._faces_dirty = False
            self.faces_changed.emit()

    def _refresh_known_names(self) -> None:
        if self._face_review_service is None:
            return
        try:
            people = self._face_review_service.get_available_people()
        except Exception:
            LOGGER.exception("Failed to list people for face naming")
            return
        self._image_label.set_known_names(sorted(p.name for p in people if p.name))

    def _on_face_name_committed(self, face_id: int, name: str) -> None:
        self._current_faces = self._image_label.get_faces()
        if self._face_review_service is None:
            return
        try:
            self._face_review_service.assign_name(face_id, name)
            self._faces_dirty = True
        except Exception as exc:
            LOGGER.exception("Failed to name face %s", face_id)
            QMessageBox.warning(self, "Name face", f"Could not save name: {exc}")

    def _on_face_ignored(self, face_id: int) -> None:
        self._current_faces = self._image_label.get_faces()
        if self._face_review_service is None:
            return
        try:
            self._face_review_service.remove_face(face_id)
            self._faces_dirty = True
        except Exception as exc:
            LOGGER.exception("Failed to remove face %s", face_id)
            QMessageBox.warning(self, "Remove face", f"Could not remove face: {exc}")

    def _on_reindex_requested(self) -> None:
        """Handle re-index faces button click."""
        if self._current_item is None:
            return
        result = QMessageBox.warning(
            self,
            "Re-index faces",
            "This will delete all manually assigned names and edits for faces in "
            "this image and re-run face detection from scratch.\n\n"
            "Use this only to recover accidentally deleted bounding boxes.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.reindex_requested.emit(self._current_item.file_path)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle keyboard shortcuts."""
        if event.key() == Qt.Key.Key_Right:
            self._show_next()
            return
        if event.key() == Qt.Key.Key_Left:
            self._show_previous()
            return
        if event.key() == Qt.Key.Key_Plus or event.key() == Qt.Key.Key_Equal:
            self._zoom = min(3.0, self._zoom + 0.1)
            self._apply_zoom_to_current_pixmap()
            return
        if event.key() == Qt.Key.Key_Minus:
            self._zoom = max(0.2, self._zoom - 0.1)
            self._apply_zoom_to_current_pixmap()
            return
        if event.key() == Qt.Key.Key_N:
            self.start_naming()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._on_close_requested()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Handle mouse wheel for zoom."""
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(3.0, self._zoom + 0.1)
        elif delta < 0:
            self._zoom = max(0.2, self._zoom - 0.1)
        self._apply_zoom_to_current_pixmap()

    def reset_zoom(self) -> None:
        """Reset zoom to 1.0x."""
        self._zoom = 1.0
        self._apply_zoom_to_current_pixmap()
