from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from photo_app.services.image_index_service import (
        ImageIndexResult,
        ImageIndexService,
    )


class ImageIndexer:
    """Thin adapter over image indexing use case."""

    def __init__(self, service: ImageIndexService) -> None:
        self._service = service

    def run(self, root: Path) -> ImageIndexResult:
        """Execute image indexing."""
        return self._service.index_folder(root)
