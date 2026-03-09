"""Advanced filter editor dialog for creating virtual albums."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from photo_app.domain.models import Person
    from photo_app.domain.value_objects import AlbumQuery


class AdvancedFilterEditorDialog(QDialog):
    """Dialog for building complex album filter queries."""

    def __init__(
        self,
        parent: QWidget | None = None,
        available_persons: list[Person] | None = None,
        available_tags: list[str] | None = None,
        available_cameras: list[str] | None = None,
        current_query: AlbumQuery | None = None,
    ) -> None:
        """Initialize advanced filter editor."""
        super().__init__(parent)
        self._available_persons = available_persons or []
        self._available_tags = available_tags or []
        self._available_cameras = available_cameras or []
        self._current_query = current_query

        self.setWindowTitle("Advanced Filter Editor")
        self.setGeometry(100, 100, 700, 800)
        self._init_ui()
        self._load_query()

    def _init_ui(self) -> None:
        """Initialize the UI layout."""
        layout = QVBoxLayout(self)

        # Person filter
        people_group = QGroupBox("People & Faces")
        people_layout = QVBoxLayout()

        people_layout.addWidget(QLabel("Select people to include:"))
        self._person_list = QListWidget()
        self._person_list.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection
        )
        for person in sorted(self._available_persons, key=lambda p: p.name or ""):
            item = QListWidgetItem(person.name or f"Person #{person.person_id}")
            item.setData(Qt.ItemDataRole.UserRole, person.person_id)
            self._person_list.addItem(item)
        people_layout.addWidget(self._person_list)
        people_group.setLayout(people_layout)
        layout.addWidget(people_group)

        # Metadata filters
        metadata_group = QGroupBox("Photo Metadata")
        metadata_layout = QFormLayout()

        # Rating
        rating_layout = QHBoxLayout()
        self._rating_slider = QSlider(Qt.Orientation.Horizontal)
        self._rating_slider.setMinimum(0)
        self._rating_slider.setMaximum(5)
        self._rating_slider.setValue(0)
        self._rating_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._rating_label = QLabel("Any")
        self._rating_slider.valueChanged.connect(self._on_rating_changed)
        rating_layout.addWidget(QLabel("Min Rating:"))
        rating_layout.addWidget(self._rating_slider)
        rating_layout.addWidget(self._rating_label)
        metadata_layout.addRow(rating_layout)

        # Quality score
        quality_layout = QHBoxLayout()
        self._quality_spinbox = QDoubleSpinBox()
        self._quality_spinbox.setMinimum(0.0)
        self._quality_spinbox.setMaximum(1.0)
        self._quality_spinbox.setValue(0.0)
        self._quality_spinbox.setSingleStep(0.1)
        quality_layout.addWidget(QLabel("Min Quality Score:"))
        quality_layout.addWidget(self._quality_spinbox)
        quality_layout.addStretch()
        metadata_layout.addRow(quality_layout)

        # Camera model
        self._camera_combo = QComboBox()
        self._camera_combo.addItem("(Any)", None)
        for camera in sorted(set(self._available_cameras)):
            self._camera_combo.addItem(camera, camera)
        metadata_layout.addRow("Camera Model:", self._camera_combo)

        metadata_group.setLayout(metadata_layout)
        layout.addWidget(metadata_group)

        # Date range
        date_group = QGroupBox("Date Range")
        date_layout = QFormLayout()

        self._date_from = QDateEdit()
        self._date_from.setDate(date.today() - timedelta(days=365))
        self._date_from.setCalendarPopup(True)
        self._date_from_check = QCheckBox("From:")
        self._date_from_check.toggled.connect(
            lambda checked: self._date_from.setEnabled(checked)
        )
        self._date_from.setEnabled(False)
        date_layout.addRow(self._date_from_check, self._date_from)

        self._date_to = QDateEdit()
        self._date_to.setDate(date.today())
        self._date_to.setCalendarPopup(True)
        self._date_to_check = QCheckBox("To:")
        self._date_to_check.toggled.connect(
            lambda checked: self._date_to.setEnabled(checked)
        )
        self._date_to.setEnabled(False)
        date_layout.addRow(self._date_to_check, self._date_to)

        date_group.setLayout(date_layout)
        layout.addWidget(date_group)

        # Tags
        tags_group = QGroupBox("Tags")
        tags_layout = QVBoxLayout()

        tags_layout.addWidget(QLabel("Select tags to include:"))
        self._tag_list = QListWidget()
        self._tag_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for tag in sorted(self._available_tags):
            item = QListWidgetItem(tag)
            self._tag_list.addItem(item)
        tags_layout.addWidget(self._tag_list)
        tags_group.setLayout(tags_layout)
        layout.addWidget(tags_group)

        # Buttons
        button_layout = QHBoxLayout()

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.accept)
        button_layout.addWidget(apply_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _load_query(self) -> None:
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
        if person_ids:
            for i in range(self._person_list.count()):
                item = self._person_list.item(i)
                item_id = item.data(Qt.ItemDataRole.UserRole)
                if item_id in person_ids:
                    self._person_list.setCurrentItem(item)

        # Load rating
        rating_min = get_value("rating_min")
        if rating_min is not None:
            try:
                if isinstance(rating_min, (int, float)):
                    self._rating_slider.setValue(int(rating_min))
                elif isinstance(rating_min, str):
                    self._rating_slider.setValue(int(rating_min))
            except (ValueError, TypeError):
                pass

        # Load quality
        quality_min = get_value("quality_min")
        if quality_min is not None:
            try:
                if isinstance(quality_min, (int, float)):
                    self._quality_spinbox.setValue(float(quality_min))
                elif isinstance(quality_min, str):
                    self._quality_spinbox.setValue(float(quality_min))
            except (ValueError, TypeError):
                pass

        # Load camera
        camera_models = get_value("camera_models", [])
        if camera_models:
            camera = camera_models[0] if isinstance(camera_models, list) and camera_models else camera_models
            for i in range(self._camera_combo.count()):
                if self._camera_combo.itemData(i) == camera:
                    self._camera_combo.setCurrentIndex(i)
                    break

        # Load date range
        date_from = get_value("date_from")
        if date_from is not None:
            self._date_from_check.setChecked(True)
            if isinstance(date_from, str):
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(date_from)
                    self._date_from.setDate(dt.date())
                except ValueError:
                    pass
            elif hasattr(date_from, 'date'):  # QDate object
                self._date_from.setDate(date_from)
            else:
                try:
                    self._date_from.setDate(date_from)
                except (ValueError, TypeError):
                    pass

        date_to = get_value("date_to")
        if date_to is not None:
            self._date_to_check.setChecked(True)
            if isinstance(date_to, str):
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(date_to)
                    self._date_to.setDate(dt.date())
                except ValueError:
                    pass
            elif hasattr(date_to, 'date'):  # QDate object
                self._date_to.setDate(date_to)
            else:
                try:
                    self._date_to.setDate(date_to)
                except (ValueError, TypeError):
                    pass

        # Load tags
        tag_names = get_value("tag_names", [])
        if tag_names:
            for i in range(self._tag_list.count()):
                item = self._tag_list.item(i)
                item_text = item.text()
                if isinstance(tag_names, list) and item_text in tag_names:
                    self._tag_list.setCurrentItem(item)

    def _on_rating_changed(self, value: int) -> None:
        """Update rating label."""
        if value == 0:
            self._rating_label.setText("Any")
        else:
            self._rating_label.setText(f"{value}★")

    def get_query_definition(self) -> dict[str, object]:
        """Get the filter query definition as a dict."""
        # Get selected people
        person_ids = []
        for item in self._person_list.selectedItems():
            person_ids.append(item.data(Qt.ItemDataRole.UserRole))

        # Get selected tags
        tag_names = []
        for item in self._tag_list.selectedItems():
            tag_names.append(item.text())

        return {
            "person_ids": person_ids,
            "cluster_ids": [],
            "tag_names": tag_names,
            "rating_min": self._rating_slider.value() if self._rating_slider.value() > 0 else None,
            "quality_min": self._quality_spinbox.value() if self._quality_spinbox.value() > 0 else None,
            "camera_models": [self._camera_combo.currentData()] if self._camera_combo.currentData() else [],
            "date_from": self._date_from.date().isoformat() if self._date_from_check.isChecked() else None,
            "date_to": self._date_to.date().isoformat() if self._date_to_check.isChecked() else None,
            "location_name": None,
            "gps_radius_km": None,
        }
