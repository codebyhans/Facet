"""Background worker for building and rendering the excluded-faces restore browser."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from photo_app.app.image_loading import (
    get_or_render_face_highlight_png,
    render_face_highlight_png,
)

if TYPE_CHECKING:
    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
    from photo_app.services.face_review_service import (
        ExcludedFaceItem,
        FaceReviewService,
    )

_MAX_RENDER_WORKERS = min(16, (os.cpu_count() or 4) * 4)


class ExcludedFacesWorkerSignals(QObject):
    """Signals for the ExcludedFacesWorker."""

    # items, thumbnail_pngs (face_id -> bytes), total_found, request_id.
    result_ready = Signal(object, object, int, int)
    error = Signal(str)
    finished = Signal()  # always emitted when run() exits


class ExcludedFacesWorker(QRunnable):
    """Build the excluded-faces list and pre-render each one's thumbnail
    off the UI thread — mirrors `PeopleListWorker`'s pattern."""

    def __init__(
        self,
        face_review_service: FaceReviewService,
        request_id: int,
        *,
        limit: int = 200,
        cache_directory: Path | None = None,
        tile_store: ThumbnailTileStore | None = None,
    ) -> None:
        super().__init__()
        self._face_review_service = face_review_service
        self._request_id = request_id
        self._limit = limit
        self._cache_directory = cache_directory
        self._tile_store = tile_store
        self.signals = ExcludedFacesWorkerSignals()

    @Slot()
    def run(self) -> None:
        """Run the worker in a background thread."""
        try:
            items, total_found = self._face_review_service.list_excluded_faces(
                limit=self._limit
            )
            thumbnails = self._render_thumbnails(items)
            self.signals.result_ready.emit(
                items, thumbnails, total_found, self._request_id
            )
        except Exception as e:  # noqa: BLE001
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()

    def _render_thumbnails(self, items: list[ExcludedFaceItem]) -> dict[int, bytes]:
        if not items:
            return {}
        cache_dir = (
            self._cache_directory / "excluded_faces_thumbnails"
            if self._cache_directory is not None
            else None
        )
        tile_lookups = {}
        if self._tile_store is not None:
            tile_lookups = self._tile_store.get_image_tiles_batch(
                [item.image_id for item in items]
            )

        def _render(item: ExcludedFaceItem) -> tuple[int, bytes | None]:
            if cache_dir is not None:
                png_bytes = get_or_render_face_highlight_png(
                    cache_dir,
                    item.image_id,
                    item.face_id,
                    Path(item.image_path),
                    item.bbox,
                    max_size=(100, 100),
                    tile_lookup=tile_lookups.get(item.image_id),
                )
            else:
                png_bytes = render_face_highlight_png(
                    Path(item.image_path), item.bbox, max_size=(100, 100)
                )
            return item.face_id, png_bytes

        with ThreadPoolExecutor(max_workers=_MAX_RENDER_WORKERS) as pool:
            return {
                face_id: png_bytes
                for face_id, png_bytes in pool.map(_render, items)
                if png_bytes is not None
            }
