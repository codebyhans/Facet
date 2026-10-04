"""Widget for displaying person cards in a responsive grid layout."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QEnterEvent,
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.image_loading import load_oriented_pixmap
from photo_app.app.workers.face_crop_worker import FaceCropWorker

if TYPE_CHECKING:
    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
    from photo_app.services.face_review_service import PersonStackSummary

LOGGER = logging.getLogger(__name__)

# Default cover size; the People tab's Ctrl+scroll zoom scales this.
BASE_COVER_PX = 120
MAX_COVER_PX = 360
# Card chrome around the cover: layout margins + border (width), plus the
# name/count labels below it (height).
CARD_EXTRA_WIDTH = 28
CARD_EXTRA_HEIGHT = 52
# Size of the hover-to-magnify badge in the cover's bottom-right corner
_MAGNIFIER_PX = 22


class PersonCardWidget(QWidget):
    """Card widget for displaying a person cluster in the stacks view."""

    person_clicked = Signal(int, object)  # person_id, PersonStackSummary
    selection_toggled = Signal(int, bool)  # person_id, selected
    rename_requested = Signal(int, str)  # person_id, new_name (cosmetic tag rename)
    tag_requested = Signal(int, str)  # cluster_id, name (apply a tag to a cluster)
    birth_date_requested = Signal(int, str)  # person_id, "YYYY-MM-DD" (empty = clear)

    def __init__(
        self,
        stack: PersonStackSummary,
        tile_store: ThumbnailTileStore | None = None,
        parent: QWidget | None = None,
        cover_size: int = BASE_COVER_PX,
    ) -> None:
        super().__init__(parent)
        self.stack = stack
        self._tile_store = tile_store
        self._cover_size = cover_size
        # Unscaled cover source, kept so zoom changes can rescale without
        # reloading from disk.
        self._cover_source: QPixmap | None = None
        self._selection_mode = False
        self._selected = False
        self._pressed = False
        self._pending_double_click = False
        # Hover face preview state: the rendered crop (once loaded), whether
        # a render is in flight, and whether the badge is currently hovered.
        self._face_crop: QPixmap | None = None
        self._face_crop_loading = False
        self._face_crop_failed = False
        self._magnifier_hovered = False
        self._face_popup: QLabel | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup UI for the person card."""
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Cover image area
        cover_layout = QHBoxLayout()
        cover_layout.setContentsMargins(0, 0, 0, 0)

        self._cover_label = QLabel()
        self._cover_label.setFixedSize(self._cover_size, self._cover_size)
        self._cover_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._cover_label.setStyleSheet(
            "border: 1px solid #444; background-color: #2a2a2a;"
        )
        self._cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_label.setToolTip(self._cover_tooltip_text())
        cover_layout.addWidget(self._cover_label, 0, Qt.AlignmentFlag.AlignHCenter)
        main_layout.addLayout(cover_layout)

        # Selection checkbox, overlaid on the cover image (hidden until selection
        # mode is enabled via the People tab's "Select" toggle).
        self._select_checkbox = QCheckBox(self._cover_label)
        self._select_checkbox.setGeometry(4, 4, 22, 22)
        self._select_checkbox.setStyleSheet(
            "QCheckBox::indicator { width: 18px; height: 18px; "
            "background-color: rgba(30, 30, 30, 200); border: 1px solid #888; "
            "border-radius: 3px; }"
            "QCheckBox::indicator:checked { background-color: #4a9eff; "
            "border: 1px solid #4a9eff; }"
        )
        self._select_checkbox.hide()
        self._select_checkbox.toggled.connect(self._on_checkbox_toggled)

        # Small warning badge shown when one of this person's clusters has a
        # high embedding variance — a likely clustering mistake (e.g. two
        # different people merged because they look alike).
        self._review_badge = QLabel("!", self._cover_label)
        self._review_badge.setGeometry(self._cover_size - 24, 4, 20, 20)
        self._review_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._review_badge.setStyleSheet(
            "background-color: #d9822b; color: white; font-weight: 700; "
            "border-radius: 10px;"
        )
        self._review_badge.setToolTip(
            "Flagged for review: this person's photos may include two "
            "different people merged by mistake."
        )
        self._review_badge.setVisible(self.stack.flagged_for_review)

        # Magnifier badge: hover it for a high-resolution crop of the face,
        # since covers are built from small thumbnails and faces in group
        # shots can be tiny. Only shown when the cover's face is known.
        self._magnifier = QLabel("🔍", self._cover_label)
        self._magnifier.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._magnifier.setStyleSheet(
            "background-color: rgba(30, 30, 30, 200); border: 1px solid #666; "
            "border-radius: 4px; font-size: 11px;"
        )
        self._magnifier.setVisible(
            self.stack.cover_face_bbox is not None and bool(self.stack.cover_image_path)
        )
        self._magnifier.installEventFilter(self)
        self._position_magnifier()

        # Info area
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(4)

        # Person name
        self._name_label = QLabel()
        self._name_label.setWordWrap(False)  # single line, elide if too long
        self._name_label.setStyleSheet(
            "font-size: 10px; font-weight: 600; color: #cccccc;"
        )
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_name_label()
        info_layout.addWidget(self._name_label)

        # Image count
        self._count_label = QLabel()
        self._count_label.setStyleSheet("font-size: 9px; color: #666666;")
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._count_label.setText(f"{self.stack.image_count} images")
        info_layout.addWidget(self._count_label)

        main_layout.addLayout(info_layout)

        # Styling
        self.setStyleSheet(
            "background-color: #252526; border: 1px solid #3e3e42; border-radius: 4px;"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.setFixedSize(
            self._cover_size + CARD_EXTRA_WIDTH, self._cover_size + CARD_EXTRA_HEIGHT
        )

    def set_cover_size(self, size: int) -> None:
        """Resize the cover (and card) for the People tab's zoom level."""
        if size == self._cover_size:
            return
        self._cover_size = size
        self._cover_label.setFixedSize(size, size)
        self._review_badge.setGeometry(size - 24, 4, 20, 20)
        self._position_magnifier()
        self.setFixedSize(size + CARD_EXTRA_WIDTH, size + CARD_EXTRA_HEIGHT)
        self._show_cover_source()

    def _position_magnifier(self) -> None:
        """Pin the magnifier badge to the cover's bottom-right corner."""
        offset = self._cover_size - _MAGNIFIER_PX - 4
        self._magnifier.setGeometry(offset, offset, _MAGNIFIER_PX, _MAGNIFIER_PX)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Show/hide the face preview while the magnifier badge is hovered."""
        if watched is self._magnifier:
            if event.type() == QEvent.Type.Enter:
                self._magnifier_hovered = True
                self._show_face_preview()
            elif event.type() == QEvent.Type.Leave:
                self._magnifier_hovered = False
                self._hide_face_preview()
        return super().eventFilter(watched, event)

    def _show_face_preview(self) -> None:
        """Show the face crop popup, starting a background render if needed."""
        if self._face_crop is None and not self._face_crop_failed:
            self._request_face_crop()
        popup = self._ensure_face_popup()
        if self._face_crop is not None:
            popup.setPixmap(self._face_crop)
        elif self._face_crop_loading:
            popup.setText("Loading…")
        else:
            popup.setText("Preview unavailable")
        popup.adjustSize()
        popup.move(self._face_popup_position(popup))
        popup.show()

    def _hide_face_preview(self) -> None:
        if self._face_popup is not None:
            self._face_popup.hide()

    def _ensure_face_popup(self) -> QLabel:
        """Create (once) the frameless tooltip-style window for the preview."""
        if self._face_popup is None:
            popup = QLabel(
                self,
                Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint,
            )
            popup.setAlignment(Qt.AlignmentFlag.AlignCenter)
            popup.setMinimumSize(80, 40)
            popup.setStyleSheet(
                "background-color: #1e1e1e; color: #cccccc; "
                "border: 1px solid #4a9eff; padding: 3px;"
            )
            self._face_popup = popup
        return self._face_popup

    def _face_popup_position(self, popup: QLabel) -> QPoint:
        """Place the popup beside the card, flipping left near the screen edge."""
        top_right = self.mapToGlobal(QPoint(self.width() + 6, 0))
        screen = self.screen().availableGeometry()
        x = top_right.x()
        if x + popup.width() > screen.right():
            x = self.mapToGlobal(QPoint(0, 0)).x() - popup.width() - 6
        y = min(top_right.y(), screen.bottom() - popup.height())
        return QPoint(max(screen.left(), x), max(screen.top(), y))

    def _request_face_crop(self) -> None:
        """Render the face crop from the original photo, off the main thread."""
        bbox = self.stack.cover_face_bbox
        if self._face_crop_loading or bbox is None or not self.stack.cover_image_path:
            return
        self._face_crop_loading = True
        worker = FaceCropWorker(Path(self.stack.cover_image_path), bbox)
        worker.signals.result_ready.connect(self._on_face_crop_ready)
        QThreadPool.globalInstance().start(worker)

    def _on_face_crop_ready(self, png_bytes: bytes | None) -> None:
        self._face_crop_loading = False
        if png_bytes is not None:
            pixmap = QPixmap()
            if pixmap.loadFromData(png_bytes):
                self._face_crop = pixmap
        self._face_crop_failed = self._face_crop is None
        # Refresh the popup in place if the badge is still hovered.
        if self._magnifier_hovered:
            self._show_face_preview()

    def _set_cover_source(self, pixmap: QPixmap) -> None:
        """Remember the unscaled cover and display it at the current size."""
        self._cover_source = pixmap
        self._show_cover_source()

    def _show_cover_source(self) -> None:
        """Scale the stored cover source to the current cover size."""
        if self._cover_source is None or self._cover_source.isNull():
            return
        self._cover_label.setPixmap(
            self._cover_source.scaled(
                self._cover_size,
                self._cover_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _update_name_label(self) -> None:
        """Update the name label with proper styling."""
        if self.stack.person_name:
            self._name_label.setText(self.stack.person_name)
            self._name_label.setStyleSheet(
                "font-size: 10px; font-weight: 600; color: #cccccc;"
            )
        else:
            self._name_label.setText("Unnamed")
            self._name_label.setStyleSheet(
                "font-size: 10px; color: #555555; font-style: italic;"
            )
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _cover_tooltip_text(self) -> str:
        """Build the hover tooltip for the cover photo.

        Surfaces the capture date, since photos of similar-looking people
        (e.g. babies who look alike at a similar age) are often easiest to
        tell apart by *when* the photo was taken rather than how it looks.
        """
        capture_date = self.stack.cover_capture_date
        if capture_date is None:
            return "Capture date unknown"
        return capture_date.strftime("%b %d, %Y")

    def load_cover_image(self) -> None:
        """Load the cover image for this person using thumbnail tiles."""
        self._cover_label.clear()

        # Always highlight this person's face on the cover, for a consistent
        # look whether the cover is a solo or group shot. This needs the raw
        # (non-EXIF-rotated) file instead of the pre-baked tile, since that's
        # the pixel space face detection stored bboxes in; the tile system
        # can't be used here since its thumbnails are EXIF-corrected.
        if self.stack.cover_face_bbox is not None and self._load_cover_with_highlight():
            return

        # Try to use thumbnail tile first for performance
        if self._tile_store and self.stack.cover_image_id is not None:
            try:
                tile_lookup = self._tile_store.get_image_tile(self.stack.cover_image_id)
                if tile_lookup and tile_lookup.tile_path.exists():
                    # Load the tile and crop the specific thumbnail
                    tile_pixmap = QPixmap(str(tile_lookup.tile_path))
                    if not tile_pixmap.isNull():
                        # Crop the specific thumbnail from the tile
                        cropped = tile_pixmap.copy(
                            tile_lookup.x,
                            tile_lookup.y,
                            tile_lookup.width,
                            tile_lookup.height,
                        )
                        self._set_cover_source(cropped)
                        return
            except Exception:
                LOGGER.exception(
                    "Failed to load cover tile for %s", self.stack.person_id
                )

        # Fallback to loading full image if tile system fails
        if self.stack.cover_image_path:
            try:
                pixmap = QPixmap(str(Path(self.stack.cover_image_path)))
                if not pixmap.isNull():
                    # Don't keep a full-resolution photo around per card;
                    # this is still enough headroom for max zoom.
                    self._set_cover_source(
                        pixmap.scaled(
                            MAX_COVER_PX,
                            MAX_COVER_PX,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
            except Exception:
                LOGGER.exception(
                    "Failed to load cover image for %s", self.stack.person_id
                )

    def _load_cover_with_highlight(self) -> bool:
        """Load the cover photo (EXIF-oriented) and box this person's face on it.

        Rare fallback path — the common case is a pre-rendered, cached PNG
        from `PeopleListWorker` (already boxed). Only reached when that's
        missing. Uses `load_oriented_pixmap` (not a plain `QPixmap(path)`,
        which ignores EXIF orientation and can't decode HEIC at all) so the
        box aligns correctly: face detection stores bbox coordinates in
        EXIF-corrected space (see `face_index_service.py`), matching every
        other place this app displays a photo.

        Returns True on success so the caller can fall back to the normal
        (unboxed) tile path if the raw file can't be read.
        """
        bbox = self.stack.cover_face_bbox
        if bbox is None or not self.stack.cover_image_path:
            return False
        try:
            raw_pixmap = load_oriented_pixmap(Path(self.stack.cover_image_path))
            if raw_pixmap.isNull() or raw_pixmap.width() == 0:
                return False

            scaled = raw_pixmap.scaled(
                120,
                120,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scale = scaled.width() / raw_pixmap.width()

            painter = QPainter(scaled)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                pen = QPen(QColor(74, 158, 255))  # matches app accent blue
                pen.setWidth(3)
                painter.setPen(pen)
                painter.drawRect(
                    int(bbox.x * scale),
                    int(bbox.y * scale),
                    max(1, int(bbox.w * scale)),
                    max(1, int(bbox.h * scale)),
                )
            finally:
                painter.end()
        except Exception:
            LOGGER.exception(
                "Failed to draw face highlight for %s", self.stack.person_id
            )
            return False
        else:
            self._set_cover_source(scaled)
            return True

    def _apply_style(self, *, hovered: bool = False, pressed: bool = False) -> None:
        """Recompute the card's background/border style from its current state."""
        if self._selected:
            self.setStyleSheet(
                "background-color: #3a4a5a; border: 2px solid #4a9eff; border-radius: 4px;"
            )
        elif pressed:
            self.setStyleSheet(
                "background-color: #3a4a5a; border: 1px solid #4a9eff; border-radius: 4px;"
            )
        elif hovered:
            self.setStyleSheet(
                "background-color: #2d2d30; border: 1px solid #5a5a5a; border-radius: 4px;"
            )
        else:
            self.setStyleSheet(
                "background-color: #252526; border: 1px solid #3e3e42; border-radius: 4px;"
            )

    def set_selection_mode(self, enabled: bool) -> None:  # noqa: FBT001
        """Show or hide the selection checkbox for bulk actions."""
        self._selection_mode = enabled
        self._select_checkbox.setVisible(enabled)
        if not enabled:
            self.set_selected(False)

    def set_selected(self, selected: bool) -> None:  # noqa: FBT001
        """Set the selection state, updating the checkbox and highlight."""
        self._selected = selected
        if self._select_checkbox.isChecked() != selected:
            self._select_checkbox.blockSignals(True)  # noqa: FBT003
            self._select_checkbox.setChecked(selected)
            self._select_checkbox.blockSignals(False)  # noqa: FBT003
        self._apply_style()

    def is_selected(self) -> bool:
        """Return whether this card is currently selected."""
        return self._selected

    def _on_checkbox_toggled(self, checked: bool) -> None:  # noqa: FBT001
        """Handle the overlay checkbox being toggled directly."""
        self._selected = checked
        self._apply_style()
        self.selection_toggled.emit(self.stack.person_id, checked)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press: toggle selection in select mode, else navigate.

        The "navigate to detail view" side of a plain click is deferred by
        one double-click interval rather than fired immediately: firing it
        on press would switch the stacked view away on the *first* click of
        a double-click, before the second click ever lands back on this
        (now hidden) widget — silently swallowing every
        double-click-to-rename attempt, since `mouseDoubleClickEvent` would
        then never fire. Deferring it lets `mouseDoubleClickEvent` cancel
        the pending navigation via `_pending_double_click` when a genuine
        double-click follows.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            if self._selection_mode:
                self._select_checkbox.setChecked(not self._select_checkbox.isChecked())
            else:
                self._pressed = True
                self._apply_style(pressed=True)
                QTimer.singleShot(
                    QApplication.doubleClickInterval(), self._emit_click_if_not_double
                )
        super().mousePressEvent(event)

    def _emit_click_if_not_double(self) -> None:
        """Fire the deferred navigation click, unless a double-click beat it."""
        if self._pending_double_click:
            self._pending_double_click = False
            return
        self.person_clicked.emit(self.stack.person_id, self.stack)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Reset visual style on mouse release."""
        self._pressed = False
        self._apply_style(hovered=True)
        super().mouseReleaseEvent(event)

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Double-click to quickly (re)name this person without opening detail view."""
        if event.button() == Qt.MouseButton.LeftButton and not self._selection_mode:
            self._pending_double_click = True
            self._prompt_rename()
        super().mouseDoubleClickEvent(event)

    def _prompt_rename(self) -> None:
        """Prompt for a name.

        For an untagged cluster (no name yet — always exactly one cluster
        by construction), this applies a name tag to that cluster:
        resolves an existing tag by name or creates a new one, never
        merging anything — so tagging several different clusters with the
        same name (e.g. a child's separate baby/toddler/kid clusters) is
        just repeating this action on each one. For an already-tagged
        person, this is a cosmetic rename of the whole tag instead, since
        "which cluster" is ambiguous once a person may have several.
        """
        current_name = self.stack.person_name or ""
        new_name, ok = QInputDialog.getText(
            self, "Name Person", "Name:", text=current_name
        )
        if not ok:
            return
        cleaned = new_name.strip()
        if not cleaned or cleaned == current_name:
            return
        if self.stack.person_name:
            self.rename_requested.emit(self.stack.person_id, cleaned)
        elif self.stack.cluster_id is not None:
            self.tag_requested.emit(self.stack.cluster_id, cleaned)

    def _prompt_birth_date(self) -> None:
        """Prompt for a birth date (YYYY-MM-DD) and emit birth_date_requested.

        Person metadata only; clustering never uses it. Leave blank to
        clear it.
        """
        text, ok = QInputDialog.getText(
            self,
            "Set Birth Date",
            "Birth date (YYYY-MM-DD), blank to clear:",
        )
        if not ok:
            return
        cleaned = text.strip()
        if cleaned:
            try:
                date.fromisoformat(cleaned)
            except ValueError:
                LOGGER.warning("Ignoring invalid birth date input: %r", cleaned)
                return
        self.birth_date_requested.emit(self.stack.person_id, cleaned)

    @override
    def enterEvent(self, event: QEnterEvent) -> None:
        """Handle mouse enter for hover effect."""
        self._apply_style(hovered=True)
        super().enterEvent(event)

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        """Don't leave the face preview floating when the card is hidden."""
        self._hide_face_preview()
        super().hideEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        """Reset style on mouse leave."""
        self._apply_style()
        super().leaveEvent(event)

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Handle right-click context menu."""
        menu = QMenu(self)

        name_label = "Rename..." if self.stack.person_name else "Tag Cluster..."
        rename_action = menu.addAction(name_label)
        rename_action.triggered.connect(self._prompt_rename)

        birth_date_action = menu.addAction("Set Birth Date...")
        birth_date_action.triggered.connect(self._prompt_birth_date)

        menu.exec(event.globalPos())

    def set_cover_pixmap(self, pixmap: QPixmap) -> None:
        """Set the cover image from an already-loaded and cropped QPixmap."""
        if not pixmap.isNull():
            self._set_cover_source(pixmap)
