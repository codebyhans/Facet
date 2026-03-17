"""Widget for displaying and selecting person face clusters/stacks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from photo_app.services.face_review_service import PersonStackSummary

LOGGER = logging.getLogger(__name__)


class PersonStackListItemWidget(QWidget):
    """Custom widget for a person stack list item."""

    def __init__(
        self, stack: PersonStackSummary, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.stack = stack
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup UI for the list item."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # Thumbnail
        thumb_label = QLabel()
        thumb_label.setFixedSize(80, 80)
        thumb_label.setStyleSheet("border: 1px solid #444; background-color: #2a2a2a;")
        if self.stack.cover_image_path:
            try:
                pixmap = QPixmap(str(Path(self.stack.cover_image_path)))
                if not pixmap.isNull():
                    scaled = pixmap.scaledToHeight(
                        80, Qt.TransformationMode.SmoothTransformation
                    )
                    thumb_label.setPixmap(scaled)
            except Exception:
                LOGGER.exception(
                    "Failed to load cover image for %s", self.stack.person_id
                )
        layout.addWidget(thumb_label)

        info_layout = QVBoxLayout()

        name_label = QLabel()
        if self.stack.person_name:
            name_label.setText(self.stack.person_name)
        else:
            name_label.setText(f"Cluster #{self.stack.person_id}")
        name_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        info_layout.addWidget(name_label)

        count_label = QLabel(f"{self.stack.image_count} images")
        count_label.setStyleSheet("font-size: 10px; color: #999;")
        info_layout.addWidget(count_label)

        info_layout.addStretch()
        layout.addLayout(info_layout, 1)


class PersonStackWidget(QListWidget):
    """List widget displaying person stacks/clusters."""

    stack_selected = Signal(int, object)  # person_id, PersonStackSummary
    stack_double_clicked = Signal(int, object)  # person_id, PersonStackSummary

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stacks: dict[int, PersonStackSummary] = {}
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setStyleSheet(
            "QListWidget { background-color: #1e1e1e; color: #ccc; border: none; }\n"
            "QListWidget::item:selected { background-color: #3a4a5a; }\n"
            "QListWidget::item:hover { background-color: #2a3a4a; }"
        )

    def show_stacks(self, stacks: list[PersonStackSummary]) -> None:
        """Load and display person stacks.

        Args:
            stacks: List of PersonStackSummary objects to display
        """
        self.clear()
        self._stacks.clear()

        for stack in stacks:
            item = QListWidgetItem()
            item.setSizeHint(
                PersonStackListItemWidget(stack).sizeHint() or item.sizeHint()
            )
            custom_widget = PersonStackListItemWidget(stack)
            self.addItem(item)
            self.setItemWidget(item, custom_widget)
            self._stacks[stack.person_id] = stack

    def _on_selection_changed(self) -> None:
        """Handle item selection."""
        selected_items = self.selectedItems()
        if not selected_items:
            return

        item = selected_items[0]
        idx = self.row(item)
        if idx >= 0:
            current_stacks = list(self._stacks.values())
            if idx < len(current_stacks):
                stack = current_stacks[idx]
                self.stack_selected.emit(stack.person_id, stack)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        """Handle item double-click."""
        idx = self.row(item)
        if idx >= 0:
            current_stacks = list(self._stacks.values())
            if idx < len(current_stacks):
                stack = current_stacks[idx]
                self.stack_double_clicked.emit(stack.person_id, stack)
