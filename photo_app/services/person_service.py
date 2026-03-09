from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from photo_app.domain.repositories import PersonRepository


class PersonService:
    """Use case for person metadata updates."""

    def __init__(self, person_repository: PersonRepository) -> None:
        self._person_repository = person_repository

    def rename_person(self, person_id: int, name: str) -> None:
        """Rename person label."""
        self._person_repository.update_name(person_id, name)
