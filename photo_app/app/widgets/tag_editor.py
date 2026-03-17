"""Tag editor widget for image metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCompleter,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QKeyEvent


class TagEditorWidget(QWidget):
    """Widget for editing image tags with autocomplete."""

    tags_changed = Signal(list)  # Emits list of tag names

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize tag editor widget."""
        super().__init__(parent)
        self._tags: list[str] = []
        self._all_tags: list[str] = []
        self._init_ui()

    def _init_ui(self) -> None:
        """Initialize the UI layout."""
        layout = QVBoxLayout(self)

        # Label
        label = QLabel("Tags:")
        layout.addWidget(label)

        # Input field with autocomplete
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type tag and press Enter...")
        self._input.keyPressEvent = self._on_input_key_press

        self._completer = QCompleter(self._all_tags)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._input.setCompleter(self._completer)

        layout.addWidget(self._input)

        # Tag list
        self._tag_list = QListWidget()
        self._tag_list.setMaximumHeight(120)
        layout.addWidget(self._tag_list)

        # Clear button
        self._clear_button = QPushButton("Clear All")
        self._clear_button.clicked.connect(self._on_clear_all)
        layout.addWidget(self._clear_button)

        layout.addStretch()

    def set_tags(self, tags: list[str]) -> None:
        """Set the current tags without emitting signal."""
        self._tags = [t.lower().strip() for t in tags if t.strip()]
        self._refresh_tag_list()

    def get_tags(self) -> list[str]:
        """Get current tags."""
        return self._tags.copy()

    def set_available_tags(self, tags: list[str]) -> None:
        """Set available tags for autocomplete."""
        self._all_tags = [t.lower().strip() for t in tags if t.strip()]
        self._all_tags = sorted(set(self._all_tags))
        self._completer.setModel(
            __import__("PySide6.QtCore").QStringListModel(self._all_tags)
        )

    def _on_input_key_press(self, event: QKeyEvent) -> None:
        """Handle key press in tag input."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._add_tag()
        else:
            QLineEdit.keyPressEvent(self._input, event)

    def _add_tag(self) -> None:
        """Add tag from input field."""
        text = self._input.text().lower().strip()
        if not text or text in self._tags:
            self._input.clear()
            return

        self._tags.append(text)
        self._refresh_tag_list()
        self._input.clear()
        self.tags_changed.emit(self._tags)

    def _on_clear_all(self) -> None:
        """Clear all tags."""
        self._tags.clear()
        self._refresh_tag_list()
        self.tags_changed.emit(self._tags)

    def _refresh_tag_list(self) -> None:
        """Refresh the tag list display."""
        self._tag_list.clear()
        for tag in sorted(self._tags):
            item = QListWidgetItem(tag)
            # Add close button behavior via custom widget
            self._tag_list.addItem(item)
            # Set item to be removable on double-click
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled)

        # Connect double-click to remove
        self._tag_list.itemDoubleClicked.connect(self._on_tag_double_clicked)

    def _on_tag_double_clicked(self, item: QListWidgetItem) -> None:
        """Remove tag on double-click."""
        tag = item.text()
        if tag in self._tags:
            self._tags.remove(tag)
            self._refresh_tag_list()
            self.tags_changed.emit(self._tags)
