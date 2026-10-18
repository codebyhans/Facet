from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image as PilImage
from PIL import UnidentifiedImageError
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.workers.indexing_worker import IndexWorker
from photo_app.app.workers.runner import start_worker

if TYPE_CHECKING:
    from photo_app.domain.models import Image
    from photo_app.services.duplicate_detection_service import (
        DuplicateDetectionService,
        DuplicateGroup,
    )

LOGGER = logging.getLogger(__name__)

_THUMB_SIZE = 72

_BTN_STYLE = """
    QPushButton {
        background-color: #2d2d30;
        color: #ffffff;
        border: 1px solid #3e3e42;
        border-radius: 4px;
        padding: 5px 12px;
    }
    QPushButton:hover:enabled { background-color: #3e3e42; }
    QPushButton:disabled { color: #555555; }
"""

_PRIMARY_BTN_STYLE = """
    QPushButton {
        background-color: #0e639c;
        color: #ffffff;
        border: 1px solid #1177bb;
        border-radius: 4px;
        padding: 5px 16px;
        font-weight: bold;
    }
    QPushButton:hover:enabled { background-color: #1177bb; }
    QPushButton:disabled { background-color: #2a3a4a; color: #555555; border-color: #3a4a5a; }
"""


_KB = 1024
_WARN_LIST_MAX = 10  # files listed by name in the collection warning


def _load_thumbnail(file_path: str, size: int = _THUMB_SIZE) -> QPixmap | None:
    try:
        with PilImage.open(file_path) as img:
            img.thumbnail((size, size), PilImage.Resampling.LANCZOS)
            rgb = img.convert("RGB")
        data = rgb.tobytes("raw", "RGB")
        qimg = QImage(
            data,
            rgb.width,
            rgb.height,
            rgb.width * 3,
            QImage.Format.Format_RGB888,
        )
        return QPixmap.fromImage(qimg)
    except OSError, UnidentifiedImageError:
        return None


def _fmt_bytes(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < _KB:
            return f"{size:.0f} {unit}"
        size //= _KB
    return f"{size:.0f} TB"


class _GroupWidget(QFrame):
    """Panel for one group of duplicate images — one radio button per file."""

    def __init__(
        self,
        group_index: int,
        group: DuplicateGroup,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._group = group
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            _GroupWidget, QFrame#groupFrame {
                background-color: #252526;
                border: 1px solid #3e3e42;
                border-radius: 6px;
            }
        """)
        self.setObjectName("groupFrame")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(6)

        # Header
        size_str = _fmt_bytes(group.file_size) if group.file_size else ""
        header_text = f"Group {group_index + 1}  ·  {len(group.images)} identical files"
        if size_str:
            header_text += f"  ·  {size_str} each"
        header = QLabel(header_text)
        header.setStyleSheet(
            "color: #cccccc; font-weight: bold; font-size: 12px; "
            "background: transparent; border: none;"
        )
        outer.addWidget(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #3e3e42; border: none; max-height: 1px;")
        outer.addWidget(sep)

        for image in group.images:
            outer.addWidget(self._build_row(image, group.suggested_keeper_id))

    def _build_row(self, image: Image, suggested_keeper_id: int) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent; border: none;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(10)

        # Thumbnail
        thumb = QLabel()
        thumb.setFixedSize(_THUMB_SIZE, _THUMB_SIZE)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet(
            "background-color: #1a1a1a; border-radius: 4px; border: none;"
        )
        pixmap = _load_thumbnail(image.file_path)
        if pixmap:
            thumb.setPixmap(pixmap)
        else:
            thumb.setText("?")
            thumb.setStyleSheet(
                "background-color: #1a1a1a; border-radius: 4px; "
                "border: none; color: #555555; font-size: 18px;"
            )
        hl.addWidget(thumb)

        # Info column
        info_vl = QVBoxLayout()
        info_vl.setSpacing(3)
        info_vl.setContentsMargins(0, 0, 0, 0)

        path_obj = Path(image.file_path)
        parts = path_obj.parts
        display = (
            str(Path(*parts[-2:]))
            if len(parts) > 2  # noqa: PLR2004
            else image.file_path
        )
        path_lbl = QLabel(display)
        path_lbl.setToolTip(image.file_path)
        path_lbl.setStyleSheet(
            "color: #d4d4d4; font-size: 11px; background: transparent; border: none;"
        )
        path_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        path_lbl.setWordWrap(False)
        info_vl.addWidget(path_lbl)

        meta: list[str] = []
        if image.capture_date:
            meta.append(image.capture_date.strftime("%Y-%m-%d  %H:%M"))
        with contextlib.suppress(OSError):
            meta.append(_fmt_bytes(Path(image.file_path).stat().st_size))
        if image.rating:
            meta.append("★" * image.rating)
        if image.is_favorite:
            meta.append("♥")
        if image.camera_model:
            meta.append(image.camera_model)

        meta_lbl = QLabel("  ·  ".join(meta))
        meta_lbl.setStyleSheet(
            "color: #777777; font-size: 10px; background: transparent; border: none;"
        )
        info_vl.addWidget(meta_lbl)

        self._add_collection_label(info_vl, image)
        hl.addLayout(info_vl)

        # Keep radio
        radio = QRadioButton("Keep")
        radio.setStyleSheet(
            "color: #cccccc; background: transparent; border: none; font-size: 11px;"
        )
        radio.setChecked(image.id == suggested_keeper_id)
        self._btn_group.addButton(radio, image.id or -1)
        hl.addWidget(radio)

        return row

    def _add_collection_label(self, layout: QVBoxLayout, image: Image) -> None:
        names = self._group.collections.get(image.id or -1)
        if not names:
            return
        label = QLabel("In collection: " + ", ".join(names))
        label.setStyleSheet(
            "color: #e0a030; font-size: 10px; background: transparent; border: none;"
        )
        layout.addWidget(label)

    def connect_changed(self, slot: object) -> None:
        for btn in self._btn_group.buttons():
            btn.toggled.connect(slot)

    def reset_to_suggested(self) -> None:
        btn = self._btn_group.button(self._group.suggested_keeper_id)
        if btn:
            btn.setChecked(True)

    def get_trashed_collection_members(self) -> list[tuple[Image, list[str]]]:
        """Images marked for trash that belong to a collection, with its names."""
        trash_ids = set(self.get_trash_ids())
        return [
            (img, self._group.collections[img.id])
            for img in self._group.images
            if img.id in trash_ids and img.id in self._group.collections
        ]

    def get_trash_ids(self) -> list[int]:
        keeper = self._btn_group.checkedId()
        return [
            img.id
            for img in self._group.images
            if img.id is not None and img.id != keeper
        ]


class DuplicateReviewDialog(QDialog):
    """Scan the library for duplicate files and review them before trashing."""

    def __init__(
        self,
        service: DuplicateDetectionService,
        default_trash_dir: Path,
        thread_pool: QThreadPool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._thread_pool = thread_pool
        self._group_widgets: list[_GroupWidget] = []

        self.setWindowTitle("Find Duplicates")
        self.setMinimumSize(720, 560)
        self.resize(860, 680)
        self.setStyleSheet("background-color: #1e1e1e; color: #cccccc;")

        self._build_ui(default_trash_dir)

    # ─── UI construction ────────────────────────────────────────────────────

    def _build_ui(self, default_trash_dir: Path) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        root.addWidget(self._build_scan_panel())

        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(
            "color: #cccccc; font-size: 12px; font-weight: bold; "
            "background: transparent;"
        )
        self._summary_lbl.setVisible(False)
        root.addWidget(self._summary_lbl)

        self._scroll = self._build_scroll()
        self._scroll.setVisible(False)
        root.addWidget(self._scroll, 1)

        self._trash_row = self._build_trash_row(default_trash_dir)
        self._trash_row.setVisible(False)
        root.addWidget(self._trash_row)

        self._action_row = self._build_action_row()
        self._action_row.setVisible(False)
        root.addWidget(self._action_row)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        close_box.setStyleSheet("color: #cccccc; background: transparent;")
        root.addWidget(close_box)

    def _build_scan_panel(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("""
            QFrame {
                background-color: #2d2d30;
                border: 1px solid #3e3e42;
                border-radius: 6px;
            }
        """)
        vl = QVBoxLayout(panel)
        vl.setContentsMargins(14, 10, 14, 12)
        vl.setSpacing(8)

        desc = QLabel(
            "Scans your library for files with identical content (byte-for-byte).\n"
            "For each group choose which copy to keep, then move the rest to trash."
        )
        desc.setStyleSheet(
            "color: #999999; font-size: 11px; background: transparent; border: none;"
        )
        desc.setWordWrap(True)
        vl.addWidget(desc)

        btn_row = QHBoxLayout()
        self._scan_btn = QPushButton("Scan for Duplicates")
        self._scan_btn.setStyleSheet(_PRIMARY_BTN_STYLE)
        self._scan_btn.setFixedHeight(32)
        self._scan_btn.clicked.connect(self._start_scan)
        btn_row.addWidget(self._scan_btn)
        btn_row.addStretch()
        vl.addLayout(btn_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1a1a1a;
                border: 1px solid #3e3e42;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #0e639c;
                border-radius: 2px;
            }
        """)
        vl.addWidget(self._progress_bar)

        self._scan_status = QLabel("")
        self._scan_status.setStyleSheet(
            "color: #888888; font-size: 10px; background: transparent; border: none;"
        )
        self._scan_status.setVisible(False)
        vl.addWidget(self._scan_status)

        return panel

    def _build_scroll(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                background-color: #1a1a1a;
                border: 1px solid #3e3e42;
                border-radius: 4px;
            }
            QScrollBar:vertical {
                background: #1a1a1a;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #3e3e42;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        self._groups_container = QWidget()
        self._groups_container.setStyleSheet("background-color: #1a1a1a;")
        self._groups_layout = QVBoxLayout(self._groups_container)
        self._groups_layout.setContentsMargins(8, 8, 8, 8)
        self._groups_layout.setSpacing(8)
        self._groups_layout.addStretch()

        scroll.setWidget(self._groups_container)
        return scroll

    def _build_trash_row(self, default_trash_dir: Path) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)

        lbl = QLabel("Trash folder:")
        lbl.setStyleSheet("color: #888888; font-size: 11px; background: transparent;")
        hl.addWidget(lbl)

        self._trash_dir_edit = QLineEdit(str(default_trash_dir))
        self._trash_dir_edit.setStyleSheet("""
            QLineEdit {
                background-color: #252526;
                color: #d4d4d4;
                border: 1px solid #3e3e42;
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }
        """)
        hl.addWidget(self._trash_dir_edit, 1)

        browse = QPushButton("Browse…")
        browse.setStyleSheet(_BTN_STYLE)
        browse.setFixedHeight(28)
        browse.clicked.connect(self._browse_trash)
        hl.addWidget(browse)

        return row

    def _build_action_row(self) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)

        reset_btn = QPushButton("Reset to Suggested")
        reset_btn.setStyleSheet(_BTN_STYLE)
        reset_btn.setFixedHeight(32)
        reset_btn.setToolTip(
            "Reset all groups to the auto-suggested keeper\n"
            "(prefers collection members, then favorites,\n"
            "then highest rating, then earliest)"
        )
        reset_btn.clicked.connect(self._reset_to_suggested)
        hl.addWidget(reset_btn)

        hl.addStretch()

        self._move_btn = QPushButton("Move to Trash (0 files)")
        self._move_btn.setStyleSheet(_PRIMARY_BTN_STYLE)
        self._move_btn.setFixedHeight(32)
        self._move_btn.setEnabled(False)
        self._move_btn.clicked.connect(self._move_to_trash)
        hl.addWidget(self._move_btn)

        return row

    # ─── Scan ───────────────────────────────────────────────────────────────

    def _start_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(True)
        self._scan_status.setText("Hashing files…")
        self._scan_status.setVisible(True)
        self._summary_lbl.setVisible(False)
        self._scroll.setVisible(False)
        self._trash_row.setVisible(False)
        self._action_row.setVisible(False)
        self._clear_groups()

        def run(on_progress: object = None) -> list[DuplicateGroup]:
            return self._service.find_exact_duplicates(on_progress=on_progress)  # type: ignore[arg-type]

        worker = IndexWorker(run)

        def on_progress(current: int, total: int) -> None:
            if total > 0:
                self._progress_bar.setRange(0, total)
                self._progress_bar.setValue(current)
                self._scan_status.setText(f"Hashing {current} / {total} files…")

        worker.signals.progress_detailed.connect(on_progress)
        worker.signals.result_ready.connect(self._on_scan_done)
        worker.signals.error.connect(self._on_scan_error)
        start_worker(worker, self._thread_pool)

    def _on_scan_done(self, result: object) -> None:
        self._scan_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._scan_status.setVisible(False)

        groups: list[DuplicateGroup] = result if isinstance(result, list) else []

        if not groups:
            self._summary_lbl.setText("No duplicates found — your library is clean!")
            self._summary_lbl.setVisible(True)
            return

        total_redundant = sum(len(g.images) - 1 for g in groups)
        wasted = sum(g.file_size * (len(g.images) - 1) for g in groups)
        self._summary_lbl.setText(
            f"Found {len(groups)} group(s)  ·  "
            f"{total_redundant} redundant file(s)  ·  "
            f"{_fmt_bytes(wasted)} recoverable"
        )
        self._summary_lbl.setVisible(True)

        self._populate_groups(groups)
        self._scroll.setVisible(True)
        self._trash_row.setVisible(True)
        self._action_row.setVisible(True)
        self._refresh_move_btn()

    def _on_scan_error(self, msg: str) -> None:
        self._scan_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._scan_status.setVisible(False)
        QMessageBox.critical(self, "Scan Error", f"Duplicate scan failed:\n{msg}")

    # ─── Groups ─────────────────────────────────────────────────────────────

    def _populate_groups(self, groups: list[DuplicateGroup]) -> None:
        self._clear_groups()
        layout = self._groups_layout
        # Remove trailing stretch
        stretch = layout.takeAt(layout.count() - 1)
        del stretch

        for idx, group in enumerate(groups):
            widget = _GroupWidget(idx, group)
            widget.connect_changed(self._refresh_move_btn)
            self._group_widgets.append(widget)
            layout.addWidget(widget)

        layout.addStretch()

    def _clear_groups(self) -> None:
        layout = self._groups_layout
        while layout.count():
            item = layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._group_widgets = []
        layout.addStretch()

    def _refresh_move_btn(self) -> None:
        total = sum(len(w.get_trash_ids()) for w in self._group_widgets)
        noun = "file" if total == 1 else "files"
        self._move_btn.setText(f"Move to Trash ({total} {noun})")
        self._move_btn.setEnabled(total > 0)

    def _reset_to_suggested(self) -> None:
        for w in self._group_widgets:
            w.reset_to_suggested()
        self._refresh_move_btn()

    # ─── Trash ──────────────────────────────────────────────────────────────

    def _browse_trash(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Trash Folder",
            self._trash_dir_edit.text(),
        )
        if folder:
            self._trash_dir_edit.setText(folder)

    def _confirm_trash(self, trash_ids: list[int], trash_dir: Path) -> bool:
        message = (
            f"Move {len(trash_ids)} file(s) to:\n\n{trash_dir}\n\n"
            "Files will not be permanently deleted — "
            "you can restore them from that folder."
        )
        members = [
            member
            for w in self._group_widgets
            for member in w.get_trashed_collection_members()
        ]
        if not members:
            reply = QMessageBox.question(
                self,
                "Move to Trash",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes

        lines = [
            f"  • {Path(img.file_path).name} — {', '.join(names)}"
            for img, names in members[:_WARN_LIST_MAX]
        ]
        if len(members) > _WARN_LIST_MAX:
            lines.append(f"  … and {len(members) - _WARN_LIST_MAX} more")
        reply = QMessageBox.warning(
            self,
            "Collection Photos Will Be Removed",
            f"{len(members)} of the files to trash belong to a collection and "
            "will be removed from it:\n\n"
            + "\n".join(lines)
            + "\n\nThe copy you keep is not added to those collections.\n\n"
            + message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _move_to_trash(self) -> None:
        trash_ids: list[int] = []
        for w in self._group_widgets:
            trash_ids.extend(w.get_trash_ids())

        if not trash_ids:
            return

        trash_dir = Path(self._trash_dir_edit.text().strip())
        if not self._confirm_trash(trash_ids, trash_dir):
            return

        self._move_btn.setEnabled(False)
        self._move_btn.setText("Moving…")

        def run() -> int:
            return self._service.move_to_trash(trash_ids, trash_dir)

        worker = IndexWorker(run)

        def on_done(count: object) -> None:
            moved = int(count) if isinstance(count, int) else len(trash_ids)
            QMessageBox.information(
                self,
                "Done",
                f"Moved {moved} file(s) to trash at:\n{trash_dir}\n\n"
                "Rescanning to show remaining duplicates…",
            )
            self._start_scan()

        def on_err(msg: str) -> None:
            QMessageBox.critical(self, "Error", f"Failed to move files:\n{msg}")
            self._refresh_move_btn()

        worker.signals.result_ready.connect(on_done)
        worker.signals.error.connect(on_err)
        start_worker(worker, self._thread_pool)
