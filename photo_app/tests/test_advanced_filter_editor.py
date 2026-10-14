from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtWidgets import QApplication

from photo_app.app.widgets.advanced_filter_editor import AdvancedFilterEditorDialog
from photo_app.domain.models import Collection, Person

CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _app() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _people() -> list[Person]:
    return [
        Person(id=1, name="Anna", created_at=CREATED_AT, birth_date=None),
        Person(id=2, name="Jonas", created_at=CREATED_AT, birth_date=None),
        Person(id=3, name="Mia", created_at=CREATED_AT, birth_date=None),
    ]


def test_editing_restores_every_selected_person() -> None:
    _app()
    dialog = AdvancedFilterEditorDialog(
        available_persons=_people(),
        current_query={"person_ids": [1, 3], "person_logic": "AND"},
    )

    query = dialog.get_query_definition()
    assert sorted(query["person_ids"]) == [1, 3]  # type: ignore[call-overload]
    assert query["person_logic"] == "AND"


def test_match_all_defaults_on_for_new_albums_only() -> None:
    _app()
    new_album = AdvancedFilterEditorDialog(available_persons=_people())
    assert new_album.get_query_definition()["person_logic"] == "AND"

    # An album saved before the option existed keeps matching any person
    legacy = AdvancedFilterEditorDialog(
        available_persons=_people(), current_query={"person_ids": [1, 2]}
    )
    assert legacy.get_query_definition()["person_logic"] == "OR"


def test_collection_criteria_round_trip() -> None:
    _app()
    collections = [
        Collection(id=7, name="Book 2025", created_at=CREATED_AT),
        Collection(id=8, name="Calendar", created_at=CREATED_AT),
    ]
    dialog = AdvancedFilterEditorDialog(
        available_persons=_people(),
        current_query={"collection_ids": [7], "exclude_collection_ids": [8]},
        available_collections=collections,
    )
    query = dialog.get_query_definition()
    assert query["collection_ids"] == [7]
    assert query["exclude_collection_ids"] == [8]

    # Without any collections the criteria stay empty
    plain = AdvancedFilterEditorDialog(available_persons=_people())
    assert plain.get_query_definition()["collection_ids"] == []
