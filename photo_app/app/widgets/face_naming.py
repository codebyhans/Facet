"""Inline popup for naming the faces of one photo in quick succession.

Anchored under the active face on the photo itself, so naming a 15-person
group shot is "type, Enter, type, Enter" rather than a dialog round-trip per
face. Deliberately does not use `QCompleter`: its popup forwards Enter to the
line edit *before* applying the highlighted completion, which makes "commit
what's highlighted" unreliable. A small inline list gives full control.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from photo_app.services.face_review_service import FaceReviewService

MAX_SUGGESTIONS = 5
_MAX_MATCHES = 6
_MAX_RECENT = 10
_CROP_SIZE = 56
_NAME_ROLE = Qt.ItemDataRole.UserRole

# Names committed this session, most recent first. Shared by every naming
# surface: consecutive photos from one event tend to contain the same people.
_recent_names: list[str] = []


def remember_name(name: str) -> None:
    """Move `name` to the front of the session's recently-used names."""
    folded = name.casefold()
    _recent_names[:] = [n for n in _recent_names if n.casefold() != folded]
    _recent_names.insert(0, name)
    del _recent_names[_MAX_RECENT:]


def recent_names() -> list[str]:
    """Return recently-used names, most recent first."""
    return list(_recent_names)


def suggestion_provider_for(
    service: FaceReviewService,
) -> Callable[[int], list[str]]:
    """Build a face_id -> likely names callback from the review service."""

    def provide(face_id: int) -> list[str]:
        return [
            s.person_name
            for s in service.get_suggested_names_for_face(face_id)
            if s.person_name
        ]

    return provide


class FaceNamePopup(QFrame):
    """Name entry anchored on one face; keyboard-driven, never modal."""

    committed = Signal(str)  # name
    skip_requested = Signal(int)  # +1 next unnamed face, -1 previous face
    ignore_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("faceNamePopup")
        self.setFixedWidth(270)
        self.setStyleSheet(
            "#faceNamePopup { background-color: #2b2b2b; border: 1px solid #4a9eff;"
            " border-radius: 6px; }"
            "QLabel { color: #ddd; background: transparent; }"
            "QLineEdit { background-color: #1e1e1e; color: white;"
            " border: 1px solid #555; border-radius: 3px; padding: 4px; }"
            "QListWidget { background-color: #1e1e1e; color: #ddd;"
            " border: 1px solid #444; }"
            "QListWidget::item:selected { background-color: #4a9eff; color: white; }"
        )
        self._known: dict[str, str] = {}  # casefolded -> display name
        self._suggestions: list[str] = []

        self._crop = QLabel()
        self._crop.setFixedSize(_CROP_SIZE, _CROP_SIZE)
        self._crop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title = QLabel()
        self._title.setStyleSheet("font-weight: bold;")
        self._subtitle = QLabel()
        self._subtitle.setStyleSheet("color: #999; font-size: 11px;")

        header_text = QVBoxLayout()
        header_text.addWidget(self._title)
        header_text.addWidget(self._subtitle)
        header_text.addStretch()
        header = QHBoxLayout()
        header.addWidget(self._crop)
        header.addLayout(header_text, 1)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a name…")
        self._input.installEventFilter(self)
        self._input.textEdited.connect(self._refresh_rows)

        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item_clicked)

        hint = QLabel(
            "Enter save & next · Tab skip · Shift+Tab back\n"
            "Shift+Enter new name as typed · Del (empty) not a face · Esc done"
        )
        hint.setStyleSheet("color: #777; font-size: 10px;")
        hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self._input)
        layout.addWidget(self._list)
        layout.addWidget(hint)

    def set_known_names(self, names: list[str]) -> None:
        """Set the library's person names offered while typing."""
        self._known = {name.casefold(): name for name in names}

    def show_face(
        self,
        crop: QPixmap | None,
        title: str,
        current_name: str | None,
        suggestions: list[str],
    ) -> None:
        """Retarget the popup at one face and focus the name field."""
        if crop is not None and not crop.isNull():
            self._crop.setPixmap(
                crop.scaled(
                    _CROP_SIZE,
                    _CROP_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self._crop.clear()
        self._title.setText(title)
        self._subtitle.setText(
            f"Currently: {current_name}" if current_name else "Unnamed"
        )
        self._suggestions = suggestions[:MAX_SUGGESTIONS]
        self._input.setText(current_name or "")
        self._input.selectAll()
        self._refresh_rows()
        self.adjustSize()
        self.show()
        self.raise_()
        self._input.setFocus()

    def _refresh_rows(self) -> None:
        """Rebuild the list: numbered suggestions when empty, else matches."""
        self._list.clear()
        text = self._input.text().strip()
        if not text:
            for number, name in enumerate(self._suggestions, start=1):
                self._add_row(f"{number}   {name}", name)
            self._list.setCurrentRow(-1)
        else:
            folded = text.casefold()
            matches = [n for n in self._known.values() if folded in n.casefold()]
            # Exact match first, then prefix matches, then substring matches.
            matches.sort(
                key=lambda n: (
                    n.casefold() != folded,
                    not n.casefold().startswith(folded),
                    n.casefold(),
                )
            )
            for name in matches[:_MAX_MATCHES]:
                self._add_row(name, name)
            if folded not in self._known:
                self._add_row(f'+ New person "{text}"', text)
            self._list.setCurrentRow(0)
        rows = self._list.count()
        self._list.setVisible(rows > 0)
        if rows:
            self._list.setFixedHeight(self._list.sizeHintForRow(0) * rows + 4)
        self.adjustSize()

    def _add_row(self, label: str, name: str) -> None:
        item = QListWidgetItem(label)
        item.setData(_NAME_ROLE, name)
        self._list.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.committed.emit(str(item.data(_NAME_ROLE)))

    def _move_highlight(self, step: int) -> None:
        rows = self._list.count()
        if rows:
            self._list.setCurrentRow((self._list.currentRow() + step) % rows)

    def _commit(self, *, as_typed: bool) -> None:
        text = self._input.text().strip()
        item = self._list.currentItem()
        if as_typed and text:
            self.committed.emit(text)
        elif item is not None:
            self.committed.emit(str(item.data(_NAME_ROLE)))
        elif text:
            self.committed.emit(self._known.get(text.casefold(), text))
        else:
            self.skip_requested.emit(1)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self._input or event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        assert isinstance(event, QKeyEvent)  # noqa: S101 - narrowed by type()
        return self._handle_key(event)

    def _handle_key(self, event: QKeyEvent) -> bool:  # noqa: PLR0911
        key = event.key()
        empty = not self._input.text().strip()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._commit(as_typed=shift)
            return True
        if key == Qt.Key.Key_Tab:
            self.skip_requested.emit(1)
            return True
        if key == Qt.Key.Key_Backtab:
            self.skip_requested.emit(-1)
            return True
        if key == Qt.Key.Key_Escape:
            self.close_requested.emit()
            return True
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self._move_highlight(1 if key == Qt.Key.Key_Down else -1)
            return True
        if key == Qt.Key.Key_Delete and empty:
            self.ignore_requested.emit()
            return True
        if empty and Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            index = key - Qt.Key.Key_1
            if index < len(self._suggestions):
                self.committed.emit(self._suggestions[index])
                return True
        return False
