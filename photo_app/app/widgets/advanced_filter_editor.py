"""Advanced filter editor dialog for creating virtual albums."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from photo_app.domain.models import Collection, Person
    from photo_app.domain.value_objects import AlbumQuery


class AdvancedFilterEditorDialog(QDialog):
    """Dialog for building complex album filter queries."""

    def __init__(
        self,
        parent: QWidget | None = None,
        available_persons: list[Person] | None = None,
        current_query: AlbumQuery | dict[str, object] | None = None,
        available_collections: list[Collection] | None = None,
    ) -> None:
        """Initialize advanced filter editor."""
        super().__init__(parent)
        self._available_persons = available_persons or []
        self._available_collections = available_collections or []
        self._current_query = current_query

        self.setWindowTitle("Advanced Filter Editor")
        self.setGeometry(100, 100, 700, 800)
        self._init_ui()
        self._load_query()

    def _init_ui(self) -> None:  # noqa: PLR0915
        """Initialize the UI layout."""
        layout = QVBoxLayout(self)

        # Person filter
        people_group = QGroupBox("People & Faces")
        people_layout = QVBoxLayout()

        people_layout.addWidget(QLabel("Select people to include:"))
        self._person_list = QListWidget()
        self._person_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for person in sorted(self._available_persons, key=lambda p: p.name or ""):
            display_id = person.id if person.id is not None else 0
            item = QListWidgetItem(person.name or f"Person #{display_id}")
            item.setData(Qt.ItemDataRole.UserRole, display_id)
            self._person_list.addItem(item)
        people_layout.addWidget(self._person_list)
        self._match_all_check = QCheckBox("Only photos containing all selected people")
        self._match_all_check.setChecked(True)
        people_layout.addWidget(self._match_all_check)
        people_group.setLayout(people_layout)
        layout.addWidget(people_group)

        # Collection filter
        collection_group = QGroupBox("Collections")
        collection_layout = QHBoxLayout()
        self._collection_list = self._build_collection_list()
        self._exclude_collection_list = self._build_collection_list()
        for label, widget in (
            ("Only photos in any of:", self._collection_list),
            ("Leave out photos in:", self._exclude_collection_list),
        ):
            column = QVBoxLayout()
            column.addWidget(QLabel(label))
            column.addWidget(widget)
            collection_layout.addLayout(column)
        collection_group.setLayout(collection_layout)
        collection_group.setVisible(bool(self._available_collections))
        layout.addWidget(collection_group)

        # Date range
        date_group = QGroupBox("Date Range")
        date_layout = QFormLayout()

        self._date_from = QDateEdit()
        today = datetime.now(tz=UTC).date()
        start_date = today - timedelta(days=365)
        self._date_from.setDate(
            QDate(start_date.year, start_date.month, start_date.day)
        )
        self._date_from.setCalendarPopup(True)
        self._date_from_check = QCheckBox("From:")
        self._date_from_check.toggled.connect(self._date_from.setEnabled)
        self._date_from.setEnabled(False)
        date_layout.addRow(self._date_from_check, self._date_from)

        self._date_to = QDateEdit()
        self._date_to.setDate(QDate(today.year, today.month, today.day))
        self._date_to.setCalendarPopup(True)
        self._date_to_check = QCheckBox("To:")
        self._date_to_check.toggled.connect(self._date_to.setEnabled)
        self._date_to.setEnabled(False)
        date_layout.addRow(self._date_to_check, self._date_to)

        date_group.setLayout(date_layout)
        layout.addWidget(date_group)

        # Flag filter
        flag_group = QGroupBox("Image Flags")
        flag_layout = QVBoxLayout()

        flag_layout.addWidget(
            QLabel("Show only images with these flags (leave all unchecked for any):")
        )

        self._flag_keep_check = QCheckBox("Keep")
        self._flag_undecided_check = QCheckBox("Undecided")
        self._flag_discard_check = QCheckBox("Discard")
        self._not_discarded_check = QCheckBox("Not discarded (Keep + Undecided)")
        self._not_discarded_check.toggled.connect(self._on_not_discarded_toggled)

        flag_layout.addWidget(self._not_discarded_check)
        flag_layout.addWidget(self._flag_keep_check)
        flag_layout.addWidget(self._flag_undecided_check)
        flag_layout.addWidget(self._flag_discard_check)

        flag_group.setLayout(flag_layout)
        layout.addWidget(flag_group)

        # Buttons
        button_layout = QHBoxLayout()

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.accept)
        button_layout.addWidget(apply_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _build_collection_list(self) -> QListWidget:
        widget = QListWidget()
        widget.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        widget.setMaximumHeight(110)
        for collection in self._available_collections:
            if collection.id is None:
                continue
            item = QListWidgetItem(collection.name)
            item.setData(Qt.ItemDataRole.UserRole, collection.id)
            widget.addItem(item)
        return widget

    @staticmethod
    def _select_ids(widget: QListWidget, ids: object) -> None:
        wanted = (
            {v for v in ids if isinstance(v, int)}
            if isinstance(ids, (list, tuple))
            else set()
        )
        for i in range(widget.count()):
            item = widget.item(i)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in wanted)

    @staticmethod
    def _selected_ids(widget: QListWidget) -> list[int]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in widget.selectedItems()
            if isinstance(item.data(Qt.ItemDataRole.UserRole), int)
        ]

    def _load_query(self) -> None:  # noqa: C901
        """Load current query into UI if provided."""
        if self._current_query is None:
            return

        # Handle both dict and AlbumQuery object
        def get_value(key: str, default: object = None) -> object:
            if isinstance(self._current_query, dict):
                return self._current_query.get(key, default)
            return getattr(self._current_query, key, default)

        # Load people
        person_ids = get_value("person_ids", [])
        if isinstance(person_ids, (list, tuple)) and person_ids:
            person_id_set = {int(v) for v in person_ids if isinstance(v, int)}
            for i in range(self._person_list.count()):
                item = self._person_list.item(i)
                item_id = item.data(Qt.ItemDataRole.UserRole)
                # setCurrentItem would clear the previous selection each time
                item.setSelected(isinstance(item_id, int) and item_id in person_id_set)
            self._match_all_check.setChecked(get_value("person_logic") == "AND")

        # Load collections
        self._select_ids(self._collection_list, get_value("collection_ids", []))
        self._select_ids(
            self._exclude_collection_list, get_value("exclude_collection_ids", [])
        )

        # Load date range
        date_from = get_value("date_from")
        if date_from is not None:
            self._date_from_check.setChecked(True)
            if isinstance(date_from, str):
                try:
                    dt = datetime.fromisoformat(date_from)
                    self._date_from.setDate(QDate(dt.year, dt.month, dt.day))
                except ValueError:
                    pass
            elif isinstance(date_from, date):
                self._date_from.setDate(
                    QDate(date_from.year, date_from.month, date_from.day)
                )

        date_to = get_value("date_to")
        if date_to is not None:
            self._date_to_check.setChecked(True)
            if isinstance(date_to, str):
                try:
                    dt = datetime.fromisoformat(date_to)
                    self._date_to.setDate(QDate(dt.year, dt.month, dt.day))
                except ValueError:
                    pass
            elif isinstance(date_to, date):
                self._date_to.setDate(QDate(date_to.year, date_to.month, date_to.day))

        # Load flags
        flags = get_value("flags", [])
        if flags and isinstance(flags, (list, tuple)):
            self._flag_keep_check.setChecked("keep" in flags)
            self._flag_undecided_check.setChecked("undecided" in flags)
            self._flag_discard_check.setChecked("discard" in flags)
            # Restore the "Not discarded" shortcut state without triggering its toggle handler
            is_not_discarded = (
                "keep" in flags and "undecided" in flags and "discard" not in flags
            )
            self._not_discarded_check.blockSignals(True)  # noqa: FBT003
            self._not_discarded_check.setChecked(is_not_discarded)
            self._not_discarded_check.blockSignals(False)  # noqa: FBT003

    def _on_not_discarded_toggled(self, checked: bool) -> None:  # noqa: FBT001
        """Handle Not Discarded shortcut toggle."""
        if checked:
            self._flag_keep_check.setChecked(True)
            self._flag_undecided_check.setChecked(True)
            self._flag_discard_check.setChecked(False)

    def get_query_definition(self) -> dict[str, object]:
        """Get the filter query definition as a dict."""
        # Get selected people
        person_ids = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._person_list.selectedItems()
            if isinstance(item.data(Qt.ItemDataRole.UserRole), int)
        ]

        # Get selected flags
        selected_flags = []
        if self._flag_keep_check.isChecked():
            selected_flags.append("keep")
        if self._flag_undecided_check.isChecked():
            selected_flags.append("undecided")
        if self._flag_discard_check.isChecked():
            selected_flags.append("discard")

        return {
            "person_ids": person_ids,
            "person_logic": "AND" if self._match_all_check.isChecked() else "OR",
            "cluster_ids": [],
            "tag_names": [],
            "rating_min": None,
            "quality_min": None,
            "camera_models": [],
            "date_from": (
                self._date_from.date().toString("yyyy-MM-dd")
                if self._date_from_check.isChecked()
                else None
            ),
            "date_to": (
                self._date_to.date().toString("yyyy-MM-dd")
                if self._date_to_check.isChecked()
                else None
            ),
            "location_name": None,
            "gps_radius_km": None,
            "flags": selected_flags,
            "collection_ids": self._selected_ids(self._collection_list),
            "exclude_collection_ids": self._selected_ids(self._exclude_collection_list),
        }
