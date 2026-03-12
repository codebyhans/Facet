"""Horizontal filter bar with four filter pills for album filtering."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from photo_app.domain.models import Person
    from photo_app.domain.value_objects import AlbumQuery


class FilterPill(QToolButton):
    """Base class for filter pills with dropdown content."""

    filter_changed = Signal()

    def __init__(self, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._label = label
        self._is_active = False
        self._dropdown_widget: QWidget | None = None
        
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setText(label)
        self.setCheckable(True)
        self.setChecked(False)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        
        # Style the button to look like a pill
        self.setStyleSheet("""
            QToolButton {
                background-color: #f0f0f0;
                border: 1px solid #d0d0d0;
                border-radius: 12px;
                padding: 4px 8px;
                margin: 2px;
                font-weight: bold;
            }
            QToolButton:checked {
                background-color: #e0e0ff;
                border-color: #a0a0ff;
            }
            QToolButton:hover {
                background-color: #e8e8e8;
            }
        """)

    def set_active(self, active: bool) -> None:
        """Set the active state of the filter pill."""
        self._is_active = active
        if active:
            self.setStyleSheet("""
                QToolButton {
                    background-color: #e0e0ff;
                    border: 1px solid #a0a0ff;
                    border-radius: 12px;
                    padding: 4px 8px;
                    margin: 2px;
                    font-weight: bold;
                }
                QToolButton:hover {
                    background-color: #d0d0ff;
                }
            """)
            self.setText(f"{self._label} ✓")
        else:
            self.setStyleSheet("""
                QToolButton {
                    background-color: #f0f0f0;
                    border: 1px solid #d0d0d0;
                    border-radius: 12px;
                    padding: 4px 8px;
                    margin: 2px;
                    font-weight: bold;
                }
                QToolButton:hover {
                    background-color: #e8e8e8;
                }
            """)
            self.setText(self._label)

    def set_dropdown_widget(self, widget: QWidget) -> None:
        """Set the dropdown widget for this filter pill."""
        self._dropdown_widget = widget
        # Override the showMenu method to show our custom widget
        original_show_menu = self.showMenu
        def show_custom_menu() -> None:
            if self._dropdown_widget:
                # Position the dropdown below the button
                pos = self.mapToGlobal(self.rect().bottomLeft())
                self._dropdown_widget.move(pos)
                self._dropdown_widget.show()
                self._dropdown_widget.raise_()
                self._dropdown_widget.activateWindow()
            else:
                original_show_menu()
        self.showMenu = show_custom_menu  # type: ignore[method-assign]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Handle mouse press to toggle dropdown."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dropdown_widget and not self._dropdown_widget.isVisible():
                # Show dropdown
                pos = self.mapToGlobal(self.rect().bottomLeft())
                self._dropdown_widget.move(pos)
                self._dropdown_widget.show()
                self._dropdown_widget.raise_()
                self._dropdown_widget.activateWindow()
                self.setChecked(True)
            elif self._dropdown_widget and self._dropdown_widget.isVisible():
                # Hide dropdown
                self._dropdown_widget.hide()
                self.setChecked(False)
        super().mousePressEvent(event)


class RatingFilterPill(FilterPill):
    """Rating filter pill with star rating options."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("★ Rating", parent)
        self._rating = 0  # 0 means "Any"
        self._setup_dropdown()

    def _setup_dropdown(self) -> None:
        """Setup the rating dropdown widget."""
        dropdown = QWidget()
        layout = QVBoxLayout(dropdown)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        # Title
        title = QLabel("Minimum Rating")
        title.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(title)

        # Rating options
        self._rating_buttons = []
        ratings = [0, 1, 2, 3, 4, 5]
        rating_labels = ["Any", "1+ stars", "2+ stars", "3+ stars", "4+ stars", "5+ stars"]

        for rating, label in zip(ratings, rating_labels):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(rating == self._rating)
            btn.clicked.connect(lambda checked, r=rating: self._on_rating_selected(r))
            layout.addWidget(btn)
            self._rating_buttons.append(btn)

        layout.addStretch()
        dropdown.setLayout(layout)
        dropdown.setFixedSize(150, 200)
        dropdown.setWindowFlags(Qt.WindowType.Popup)
        dropdown.installEventFilter(self)

        self.set_dropdown_widget(dropdown)
        self._update_display()

    def _on_rating_selected(self, rating: int) -> None:
        """Handle rating selection."""
        self._rating = rating
        self._update_display()
        self.filter_changed.emit()

        # Update button states
        for i, btn in enumerate(self._rating_buttons):
            if btn:
                btn.setChecked(i == rating)

    def _update_display(self) -> None:
        """Update the pill display based on current rating."""
        if self._rating == 0:
            self.setText("★ Rating")
            self.set_active(False)
        else:
            self.setText(f"★ {self._rating}+")
            self.set_active(True)

    def get_query_params(self) -> dict[str, object]:
        """Get query parameters for this filter."""
        if self._rating == 0:
            return {}
        return {"rating_min": self._rating}

    def set_from_query(self, query: AlbumQuery | dict[str, object]) -> None:
        """Set filter state from query."""
        if isinstance(query, dict):
            rating_min = query.get("rating_min")
        else:
            rating_min = getattr(query, "rating_min", None)
        
        if rating_min is None:
            self._rating = 0
        else:
            self._rating = int(rating_min) if rating_min else 0
        
        self._update_display()
        # Update button states
        for i, btn in enumerate(self._rating_buttons):
            btn.setChecked(i == self._rating)


class PeopleFilterPill(FilterPill):
    """People filter pill with multi-select and AND/OR toggle."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("👤 People", parent)
        self._person_ids: list[int] = []
        self._logic = "OR"  # "AND" or "OR"
        self._available_people: list[Person] = []
        self._setup_dropdown()

    def set_available_people(self, people: list[Person]) -> None:
        """Set the available people for selection."""
        self._available_people = people
        self._update_dropdown_people()

    def _setup_dropdown(self) -> None:
        """Setup the people dropdown widget."""
        dropdown = QWidget()
        layout = QVBoxLayout(dropdown)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Title
        title = QLabel("Select People")
        title.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(title)

        # Logic toggle
        logic_layout = QHBoxLayout()
        logic_label = QLabel("Logic:")
        self._and_btn = QPushButton("AND")
        self._and_btn.setCheckable(True)
        self._and_btn.setChecked(self._logic == "AND")
        self._and_btn.clicked.connect(self._on_logic_changed)
        
        self._or_btn = QPushButton("OR")
        self._or_btn.setCheckable(True)
        self._or_btn.setChecked(self._logic == "OR")
        self._or_btn.clicked.connect(self._on_logic_changed)

        logic_layout.addWidget(logic_label)
        logic_layout.addWidget(self._and_btn)
        logic_layout.addWidget(self._or_btn)
        logic_layout.addStretch()
        layout.addLayout(logic_layout)

        # People list
        self._people_container = QWidget()
        self._people_layout = QVBoxLayout(self._people_container)
        self._people_layout.setSpacing(2)
        layout.addWidget(self._people_container)

        # Scroll area for people list
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidget(self._people_container)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(200)
        layout.addWidget(scroll)

        layout.addStretch()
        dropdown.setLayout(layout)
        dropdown.setFixedSize(250, 300)
        dropdown.setWindowFlags(Qt.WindowType.Popup)
        dropdown.installEventFilter(self)

        self.set_dropdown_widget(dropdown)
        self._update_display()

    def _update_dropdown_people(self) -> None:
        """Update the people list in the dropdown."""
        # Clear existing widgets
        for i in reversed(range(self._people_layout.count())):
            item = self._people_layout.itemAt(i)
            if item:
                widget = item.widget()
                if widget:
                    widget.setParent(None)

        # Add people checkboxes
        for person in self._available_people:
            checkbox = QCheckBox(person.name or f"Person {person.id}")
            checkbox.setProperty("person_id", person.id)
            checkbox.setChecked(person.id in self._person_ids)
            checkbox.stateChanged.connect(self._on_person_toggled)
            self._people_layout.addWidget(checkbox)

        self._update_display()

    def _on_logic_changed(self) -> None:
        """Handle logic toggle change."""
        sender = self.sender()
        if sender == self._and_btn:
            self._logic = "AND"
            self._and_btn.setChecked(True)
            self._or_btn.setChecked(False)
        else:
            self._logic = "OR"
            self._and_btn.setChecked(False)
            self._or_btn.setChecked(True)
        self.filter_changed.emit()
        self._update_display()

    def _on_person_toggled(self) -> None:
        """Handle person checkbox toggle."""
        self._person_ids = []
        for i in range(self._people_layout.count()):
            item = self._people_layout.itemAt(i)
            if item:
                widget = item.widget()
                if isinstance(widget, QCheckBox) and widget.isChecked():
                    person_id = widget.property("person_id")
                    if person_id is not None:
                        self._person_ids.append(int(person_id))
        
        self.filter_changed.emit()
        self._update_display()

    def _update_display(self) -> None:
        """Update the pill display based on current selection."""
        if not self._person_ids:
            self.setText("👤 People")
            self.set_active(False)
        else:
            count = len(self._person_ids)
            logic = "AND" if self._logic == "AND" else "OR"
            self.setText(f"👤 {count} ({logic})")
            self.set_active(True)

    def get_query_params(self) -> dict[str, object]:
        """Get query parameters for this filter."""
        if not self._person_ids:
            return {}
        return {
            "person_ids": self._person_ids,
            "person_logic": self._logic
        }

    def set_from_query(self, query: AlbumQuery | dict[str, object]) -> None:
        """Set filter state from query."""
        if isinstance(query, dict):
            person_ids = query.get("person_ids", [])
            logic = query.get("person_logic", "OR")
        else:
            person_ids = list(getattr(query, "person_ids", []))
            logic = "OR"  # Default logic for old queries
        
        self._person_ids = list(person_ids) if person_ids else []
        self._logic = logic
        
        # Update checkbox states
        for i in range(self._people_layout.count()):
            widget = self._people_layout.itemAt(i).widget()
            if isinstance(widget, QCheckBox):
                person_id = widget.property("person_id")
                widget.setChecked(person_id in self._person_ids)
        
        # Update logic buttons
        self._and_btn.setChecked(self._logic == "AND")
        self._or_btn.setChecked(self._logic == "OR")
        
        self._update_display()


class DateFilterPill(FilterPill):
    """Date filter pill with from/to date pickers."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("📅 Date", parent)
        self._date_from: date | None = None
        self._date_to: date | None = None
        self._setup_dropdown()

    def _setup_dropdown(self) -> None:
        """Setup the date dropdown widget."""
        dropdown = QWidget()
        layout = QVBoxLayout(dropdown)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Title
        title = QLabel("Date Range")
        title.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(title)

        # From date
        from_layout = QHBoxLayout()
        from_label = QLabel("From (inclusive):")
        self._date_from_edit = QDateEdit()
        self._date_from_edit.setCalendarPopup(True)
        self._date_from_edit.setDate(date.today().replace(year=date.today().year - 1))
        self._date_from_check = QCheckBox()
        self._date_from_check.setChecked(False)
        self._date_from_check.toggled.connect(
            lambda checked: self._date_from_edit.setEnabled(checked)
        )
        self._date_from_edit.setEnabled(False)
        from_layout.addWidget(self._date_from_check)
        from_layout.addWidget(from_label)
        from_layout.addWidget(self._date_from_edit)
        layout.addLayout(from_layout)

        # To date
        to_layout = QHBoxLayout()
        to_label = QLabel("To (inclusive):")
        self._date_to_edit = QDateEdit()
        self._date_to_edit.setCalendarPopup(True)
        self._date_to_edit.setDate(date.today())
        self._date_to_check = QCheckBox()
        self._date_to_check.setChecked(False)
        self._date_to_check.toggled.connect(
            lambda checked: self._date_to_edit.setEnabled(checked)
        )
        self._date_to_edit.setEnabled(False)
        to_layout.addWidget(self._date_to_check)
        to_layout.addWidget(to_label)
        to_layout.addWidget(self._date_to_edit)
        layout.addLayout(to_layout)

        layout.addStretch()
        dropdown.setLayout(layout)
        dropdown.setFixedSize(300, 180)
        dropdown.setWindowFlags(Qt.WindowType.Popup)
        dropdown.installEventFilter(self)

        self.set_dropdown_widget(dropdown)
        
        # Add signal connections for date changes
        self._date_from_check.toggled.connect(self._on_date_changed)
        self._date_from_edit.dateChanged.connect(self._on_date_changed)
        self._date_to_check.toggled.connect(self._on_date_changed)
        self._date_to_edit.dateChanged.connect(self._on_date_changed)
        
        self._update_display()

    def _on_date_changed(self) -> None:
        """Handle date changes."""
        if self._date_from_check.isChecked():
            self._date_from = self._date_from_edit.date().toPython()
        else:
            self._date_from = None
            
        if self._date_to_check.isChecked():
            self._date_to = self._date_to_edit.date().toPython()
        else:
            self._date_to = None
            
        self.filter_changed.emit()
        self._update_display()

    def _update_display(self) -> None:
        """Update the pill display based on current dates."""
        if self._date_from is None and self._date_to is None:
            self.setText("📅 Date")
            self.set_active(False)
        else:
            from_str = self._date_from.strftime("%Y-%m-%d") if self._date_from else "Any"
            to_str = self._date_to.strftime("%Y-%m-%d") if self._date_to else "Any"
            self.setText(f"📅 {from_str} → {to_str}")
            self.set_active(True)

    def get_query_params(self) -> dict[str, object]:
        """Get query parameters for this filter."""
        params = {}
        if self._date_from:
            params["date_from"] = self._date_from
        if self._date_to:
            params["date_to"] = self._date_to
        return params

    def set_from_query(self, query: AlbumQuery | dict[str, object]) -> None:
        """Set filter state from query."""
        if isinstance(query, dict):
            date_from = query.get("date_from")
            date_to = query.get("date_to")
        else:
            date_from = getattr(query, "date_from", None)
            date_to = getattr(query, "date_to", None)
        
        if date_from:
            if isinstance(date_from, date):
                self._date_from = date_from
            else:
                self._date_from = date.fromisoformat(str(date_from))
            self._date_from_check.setChecked(True)
            self._date_from_edit.setEnabled(True)
            self._date_from_edit.setDate(self._date_from)
        else:
            self._date_from = None
            self._date_from_check.setChecked(False)
            self._date_from_edit.setEnabled(False)

        if date_to:
            if isinstance(date_to, date):
                self._date_to = date_to
            else:
                self._date_to = date.fromisoformat(str(date_to))
            self._date_to_check.setChecked(True)
            self._date_to_edit.setEnabled(True)
            self._date_to_edit.setDate(self._date_to)
        else:
            self._date_to = None
            self._date_to_check.setChecked(False)
            self._date_to_edit.setEnabled(False)
        
        self._update_display()


class FlagFilterPill(FilterPill):
    """Flag filter pill with Keep/Undecided/Discard checkboxes and Not Discarded shortcut."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("🏷 Flag", parent)
        self._flags: list[str] = []
        self._setup_dropdown()

    def _setup_dropdown(self) -> None:
        """Setup the flag dropdown widget."""
        dropdown = QWidget()
        layout = QVBoxLayout(dropdown)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Title
        title = QLabel("Image Flags")
        title.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(title)

        # Not discarded shortcut
        self._not_discarded_check = QCheckBox("Not discarded (Keep + Undecided)")
        self._not_discarded_check.stateChanged.connect(self._on_not_discarded_toggled)
        layout.addWidget(self._not_discarded_check)

        # Individual flags
        self._keep_check = QCheckBox("Keep")
        self._keep_check.stateChanged.connect(self._on_flag_toggled)
        layout.addWidget(self._keep_check)

        self._undecided_check = QCheckBox("Undecided")
        self._undecided_check.stateChanged.connect(self._on_flag_toggled)
        layout.addWidget(self._undecided_check)

        self._discard_check = QCheckBox("Discard")
        self._discard_check.stateChanged.connect(self._on_flag_toggled)
        layout.addWidget(self._discard_check)

        layout.addStretch()
        dropdown.setLayout(layout)
        dropdown.setFixedSize(200, 180)
        dropdown.setWindowFlags(Qt.WindowType.Popup)
        dropdown.installEventFilter(self)

        self.set_dropdown_widget(dropdown)
        self._update_display()

    def _on_not_discarded_toggled(self) -> None:
        """Handle Not Discarded checkbox toggle."""
        checked = self._not_discarded_check.isChecked()
        if checked:
            self._keep_check.setChecked(True)
            self._undecided_check.setChecked(True)
            self._discard_check.setChecked(False)
        self._update_flags()
        self.filter_changed.emit()

    def _on_flag_toggled(self) -> None:
        """Handle individual flag checkbox toggle."""
        # If any individual flag is changed, uncheck "Not discarded"
        if self._keep_check.isChecked() or self._undecided_check.isChecked():
            self._not_discarded_check.setChecked(False)
        self._update_flags()
        self.filter_changed.emit()

    def _update_flags(self) -> None:
        """Update the flags list based on checkbox states."""
        self._flags = []
        if self._keep_check.isChecked():
            self._flags.append("keep")
        if self._undecided_check.isChecked():
            self._flags.append("undecided")
        if self._discard_check.isChecked():
            self._flags.append("discard")

    def _update_display(self) -> None:
        """Update the pill display based on current flags."""
        if not self._flags:
            self.setText("🏷 Flag")
            self.set_active(False)
        else:
            flags_str = " + ".join(self._flags)
            self.setText(f"🏷 {flags_str}")
            self.set_active(True)

    def get_query_params(self) -> dict[str, object]:
        """Get query parameters for this filter."""
        if not self._flags:
            return {}
        return {"flags": self._flags}

    def set_from_query(self, query: AlbumQuery | dict[str, object]) -> None:
        """Set filter state from query."""
        if isinstance(query, dict):
            flags = query.get("flags", [])
        else:
            # Old queries might not have flags, so we skip setting them
            flags = []
        
        self._flags = list(flags) if flags else []
        
        # Update checkbox states
        self._keep_check.setChecked("keep" in self._flags)
        self._undecided_check.setChecked("undecided" in self._flags)
        self._discard_check.setChecked("discard" in self._flags)
        self._not_discarded_check.setChecked(
            "keep" in self._flags and "undecided" in self._flags and "discard" not in self._flags
        )
        
        self._update_display()


class FilterBarWidget(QFrame):
    """Main filter bar widget with four filter pills."""

    filter_changed = Signal()
    save_as_album_requested = Signal(str)  # album_name

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Sunken)
        self.setLineWidth(1)
        
        # Initialize filter pills
        self._rating_pill = RatingFilterPill()
        self._people_pill = PeopleFilterPill()
        self._date_pill = DateFilterPill()
        self._flag_pill = FlagFilterPill()

        # Control buttons
        self._save_btn = QPushButton("💾 Save as Album")
        self._save_btn.clicked.connect(self._on_save_as_album)
        self._clear_btn = QPushButton("🗑️ Clear Filters")
        self._clear_btn.clicked.connect(self.clear_filters)

        # Connect filter change signals
        self._rating_pill.filter_changed.connect(self._on_filter_changed)
        self._people_pill.filter_changed.connect(self._on_filter_changed)
        self._date_pill.filter_changed.connect(self._on_filter_changed)
        self._flag_pill.filter_changed.connect(self._on_filter_changed)

        self._setup_ui()
        self._update_active_states()

    def _setup_ui(self) -> None:
        """Setup the UI layout."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        # Filter pills
        layout.addWidget(self._rating_pill)
        layout.addWidget(self._people_pill)
        layout.addWidget(self._date_pill)
        layout.addWidget(self._flag_pill)

        # Spacer to push buttons to the right
        layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        # Control buttons
        layout.addWidget(self._clear_btn)
        layout.addWidget(self._save_btn)

        self.setLayout(layout)

    def _on_filter_changed(self) -> None:
        """Handle filter changes."""
        self._update_active_states()
        self.filter_changed.emit()

    def _update_active_states(self) -> None:
        """Update the active state of all filter pills."""
        self._rating_pill.set_active(bool(self._rating_pill.get_query_params()))
        self._people_pill.set_active(bool(self._people_pill.get_query_params()))
        self._date_pill.set_active(bool(self._date_pill.get_query_params()))
        self._flag_pill.set_active(bool(self._flag_pill.get_query_params()))

    def _on_save_as_album(self) -> None:
        """Handle save as album button click."""
        from PySide6.QtWidgets import QInputDialog
        album_name, ok = QInputDialog.getText(
            self,
            "Save Filter as Album",
            "Album name:",
            text="My Filter"
        )
        if ok and album_name.strip():
            self.save_as_album_requested.emit(album_name.strip())

    def get_query_definition(self) -> dict[str, object]:
        """Get the combined query definition from all filters."""
        query: dict[str, object] = {}
        query.update(self._rating_pill.get_query_params())
        query.update(self._people_pill.get_query_params())
        query.update(self._date_pill.get_query_params())
        query.update(self._flag_pill.get_query_params())
        return query

    def set_from_query(self, query: AlbumQuery | dict[str, object]) -> None:
        """Set all filter states from a query."""
        self._rating_pill.set_from_query(query)
        self._people_pill.set_from_query(query)
        self._date_pill.set_from_query(query)
        self._flag_pill.set_from_query(query)
        self._update_active_states()

    def clear_filters(self) -> None:
        """Clear all filter selections."""
        self._rating_pill._on_rating_selected(0)
        self._people_pill._person_ids = []
        for i in range(self._people_pill._people_layout.count()):
            item = self._people_pill._people_layout.itemAt(i)
            if item:
                widget = item.widget()
                if isinstance(widget, QCheckBox):
                    widget.setChecked(False)
        self._people_pill._logic = "OR"
        self._people_pill._and_btn.setChecked(False)
        self._people_pill._or_btn.setChecked(True)
        self._date_pill._date_from_check.setChecked(False)
        self._date_pill._date_from_edit.setEnabled(False)
        self._date_pill._date_to_check.setChecked(False)
        self._date_pill._date_to_edit.setEnabled(False)
        self._flag_pill._keep_check.setChecked(False)
        self._flag_pill._undecided_check.setChecked(False)
        self._flag_pill._discard_check.setChecked(False)
        self._flag_pill._not_discarded_check.setChecked(False)
        self._flag_pill._flags = []
        self._update_active_states()
        self.filter_changed.emit()

    def set_available_people(self, people: list[Person]) -> None:
        """Set the available people for the people filter."""
        self._people_pill.set_available_people(people)