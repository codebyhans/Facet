from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class FilterEditorWidget(QWidget):
    """Album filter editor that emits query_definition dictionaries."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._people: list[tuple[int, QCheckBox]] = []
        self._years: list[tuple[int, QCheckBox]] = []

        self._date_from = QDateEdit(self)
        self._date_from.setCalendarPopup(True)
        self._date_from.setSpecialValueText("Any")
        self._date_from.setDate(self._date_from.minimumDate())

        self._date_to = QDateEdit(self)
        self._date_to.setCalendarPopup(True)
        self._date_to.setSpecialValueText("Any")
        self._date_to.setDate(self._date_to.minimumDate())

        date_form = QFormLayout()
        date_form.addRow("From date", self._date_from)
        date_form.addRow("To date", self._date_to)
        date_box = QGroupBox("Date range", self)
        date_box.setLayout(date_form)

        self._match_any = QRadioButton("Match ANY selected", self)
        self._match_all = QRadioButton("Match ALL selected", self)
        self._match_any.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._match_any)
        mode_group.addButton(self._match_all)

        people_box = QGroupBox("People", self)
        self._people_layout = QVBoxLayout(people_box)
        self._people_layout.addWidget(self._match_any)
        self._people_layout.addWidget(self._match_all)
        self._people_layout.addWidget(QLabel("No people available"))

        years_box = QGroupBox("Years", self)
        self._years_layout = QVBoxLayout(years_box)
        self._years_layout.addWidget(QLabel("No year data available"))

        root = QHBoxLayout(self)
        root.addWidget(date_box, 1)
        root.addWidget(people_box, 1)
        root.addWidget(years_box, 1)

    def set_people(self, people: list[Person]) -> None:
        """Populate person checkbox list."""
        self._clear_layout(self._people_layout, keep=2)
        self._people = []
        for person in people:
            if person.id is None:
                continue
            box = QCheckBox(person.name or f"Person {person.id}", self)
            self._people_layout.addWidget(box)
            self._people.append((person.id, box))

    def set_years(self, years: list[int]) -> None:
        """Populate year checkbox list."""
        self._clear_layout(self._years_layout, keep=0)
        self._years = []
        for year in years:
            box = QCheckBox(str(year), self)
            self._years_layout.addWidget(box)
            self._years.append((year, box))

    def set_query_definition(self, query_definition: dict[str, object]) -> None:
        """Set editor state from existing query dictionary."""
        self._apply_date_field(query_definition.get("date_from"), self._date_from)
        self._apply_date_field(query_definition.get("date_to"), self._date_to)

        selected = self._resolve_selected_people(query_definition)

        for person_id, box in self._people:
            box.setChecked(person_id in selected)

        selected_years = self._resolve_selected_years(query_definition.get("years"))
        for year, box in self._years:
            box.setChecked(year in selected_years)

    def query_definition(self) -> dict[str, object]:
        """Return current filter configuration."""
        payload: dict[str, object] = {}

        from_date = self._date_from.date().toPython()
        min_from_date = self._date_from.minimumDate().toPython()
        if (
            isinstance(from_date, date)
            and isinstance(min_from_date, date)
            and from_date > min_from_date
        ):
            payload["date_from"] = from_date.isoformat()

        to_date = self._date_to.date().toPython()
        min_to_date = self._date_to.minimumDate().toPython()
        if (
            isinstance(to_date, date)
            and isinstance(min_to_date, date)
            and to_date > min_to_date
        ):
            payload["date_to"] = to_date.isoformat()

        selected_people = [
            person_id for person_id, box in self._people if box.isChecked()
        ]
        if selected_people:
            if self._match_all.isChecked():
                payload["person_ids_all"] = selected_people
                payload["person_ids"] = selected_people
            else:
                payload["person_ids_any"] = selected_people
                payload["person_ids"] = selected_people

        selected_years = [year for year, box in self._years if box.isChecked()]
        if selected_years:
            payload["years"] = selected_years

        return payload

    def _clear_layout(self, layout: QVBoxLayout, *, keep: int) -> None:
        while layout.count() > keep:
            item = layout.takeAt(layout.count() - 1)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _apply_date_field(self, raw: object, widget: QDateEdit) -> None:
        if isinstance(raw, str):
            parsed = QDate.fromString(raw, "yyyy-MM-dd")
            if parsed.isValid():
                widget.setDate(parsed)

    def _resolve_selected_people(self, query_definition: dict[str, object]) -> set[int]:
        ids_any = query_definition.get("person_ids_any")
        ids_all = query_definition.get("person_ids_all")
        cluster_ids = query_definition.get("cluster_ids")
        selected: set[int] = set()
        if isinstance(ids_any, list):
            selected = {int(v) for v in ids_any if isinstance(v, int)}
            self._match_any.setChecked(True)
        if isinstance(ids_all, list):
            selected = {int(v) for v in ids_all if isinstance(v, int)}
            self._match_all.setChecked(True)
        if not selected and isinstance(cluster_ids, list):
            selected = {int(v) for v in cluster_ids if isinstance(v, int)}
        return selected

    def _resolve_selected_years(self, raw_years: object) -> set[int]:
        if isinstance(raw_years, list):
            return {int(v) for v in raw_years if isinstance(v, int)}
        return set()


if TYPE_CHECKING:
    from photo_app.domain.models import Person
