from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from photo_app.services.face_index_service import FaceIndexResult, FaceIndexService


class FaceIndexer:
    """Thin adapter for face indexing."""

    def __init__(self, service: FaceIndexService) -> None:
        self._service = service

    def run(self) -> FaceIndexResult:
        """Execute face indexing."""
        return self._service.index_faces()
