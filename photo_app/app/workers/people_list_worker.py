"""Background worker for loading people lists to prevent UI freezing."""

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
    from photo_app.infrastructure.thumbnail_tiles import (
        ImageTileLookup,
        ThumbnailTileStore,
    )
    from photo_app.services.face_review_service import (
        FaceReviewService,
        PersonStackSummary,
    )

# Rendering is I/O + native-decode bound (PIL/libjpeg/libheif release the GIL
# during the actual decode, and file reads can be off a slow/external disk),
# so a wider thread pool than cpu_count() gives a large speedup over decoding
# hundreds of covers one at a time.
_MAX_RENDER_WORKERS = min(16, (os.cpu_count() or 4) * 4)


class PeopleListWorkerSignals(QObject):
    """Signals for the PeopleListWorker."""

    result_ready = Signal(object, object, object, int)
    error = Signal(str)
    finished = Signal()


class PeopleListWorker(QRunnable):
    """Background worker for loading people lists."""

    def __init__(  # noqa: PLR0913
        self,
        face_review_service: FaceReviewService,
        *,
        tile_store: ThumbnailTileStore | None = None,
        cover_cache_dir: Path | None = None,
        min_image_count: int = 3,
        sample_limit: int = 20,
        show_unnamed: bool = False,
        epoch: int = 0,
    ) -> None:
        super().__init__()
        self._face_review_service = face_review_service
        self._tile_store = tile_store
        self._cover_cache_dir = cover_cache_dir
        self._min_image_count = min_image_count
        self._sample_limit = sample_limit
        self._show_unnamed = show_unnamed
        self._epoch = epoch
        self.signals = PeopleListWorkerSignals()

    @Slot()
    def run(self) -> None:
        """Run the worker in a background thread."""
        try:
            if self._show_unnamed:
                stacks = self._face_review_service.person_stacks(
                    sample_limit=self._sample_limit
                )
            else:
                stacks = self._face_review_service.person_stacks_filtered(
                    min_image_count=self._min_image_count,
                    sample_limit=self._sample_limit,
                )

            # Batch-fetch all cover tile lookups in one DB query (pure SQLAlchemy,
            # safe on any thread — no Qt objects created here). Reused below for
            # the highlight-render jobs too, so drawing a face box never needs a
            # full-resolution decode when a tile already exists for that photo.
            cover_lookups: dict[int, tuple[str, int, int, int, int]] = {}
            tile_batch: dict[int, ImageTileLookup] = {}
            if self._tile_store is not None:
                cover_ids = [
                    s.cover_image_id for s in stacks if s.cover_image_id is not None
                ]
                tile_batch = self._tile_store.get_image_tiles_batch(cover_ids)
                for image_id, lookup in tile_batch.items():
                    cover_lookups[image_id] = (
                        str(lookup.tile_path),
                        lookup.x,
                        lookup.y,
                        lookup.width,
                        lookup.height,
                    )

            # Render face-highlight covers here too (PIL only, no Qt objects)
            # so the main thread only ever has to decode an already-scaled
            # PNG into a QPixmap instead of loading/scaling full-res photos.
            # Cached to disk (keyed by image/person/bbox) so this expensive
            # decode only ever happens once per cover, and parallelized across
            # threads since decoding releases the GIL — both matter a lot on
            # large libraries where this loop is otherwise the dominant cost.
            cover_highlight_pngs: dict[int, bytes] = {}
            render_jobs = [
                stack
                for stack in stacks
                if stack.cover_face_bbox is not None and stack.cover_image_path
            ]
            if render_jobs:
                cache_dir = (
                    self._cover_cache_dir / "people_covers"
                    if self._cover_cache_dir is not None
                    else None
                )

                def _render(stack: PersonStackSummary) -> tuple[int, bytes | None]:
                    assert stack.cover_face_bbox is not None  # noqa: S101
                    assert stack.cover_image_path is not None  # noqa: S101
                    assert stack.cover_image_id is not None  # noqa: S101
                    if cache_dir is not None:
                        png_bytes = get_or_render_face_highlight_png(
                            cache_dir,
                            stack.cover_image_id,
                            stack.person_id,
                            Path(stack.cover_image_path),
                            stack.cover_face_bbox,
                            tile_lookup=tile_batch.get(stack.cover_image_id),
                        )
                    else:
                        png_bytes = render_face_highlight_png(
                            Path(stack.cover_image_path), stack.cover_face_bbox
                        )
                    return stack.person_id, png_bytes

                with ThreadPoolExecutor(max_workers=_MAX_RENDER_WORKERS) as pool:
                    cover_highlight_pngs = {
                        person_id: png_bytes
                        for person_id, png_bytes in pool.map(_render, render_jobs)
                        if png_bytes is not None
                    }

            self.signals.result_ready.emit(
                stacks, cover_lookups, cover_highlight_pngs, self._epoch
            )
        except Exception as e:  # noqa: BLE001
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()
