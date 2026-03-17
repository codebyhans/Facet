"""Background worker for loading people lists to prevent UI freezing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

if TYPE_CHECKING:
    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
    from photo_app.services.face_review_service import FaceReviewService


class PeopleListWorkerSignals(QObject):
    """Signals for the PeopleListWorker."""

    result_ready = Signal(object, object, int)
    error = Signal(str)
    finished = Signal()


class PeopleListWorker(QRunnable):
    """Background worker for loading people lists."""

    def __init__(  # noqa: PLR0913
        self,
        face_review_service: FaceReviewService,
        *,
        tile_store: ThumbnailTileStore | None = None,
        min_image_count: int = 3,
        sample_limit: int = 20,
        show_unnamed: bool = False,
        epoch: int = 0,
    ) -> None:
        super().__init__()
        self._face_review_service = face_review_service
        self._tile_store = tile_store
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
            # safe on any thread — no Qt objects created here).
            cover_lookups: dict[int, tuple[str, int, int, int, int]] = {}
            if self._tile_store is not None:
                cover_ids = [
                    s.cover_image_id for s in stacks if s.cover_image_id is not None
                ]
                batch = self._tile_store.get_image_tiles_batch(cover_ids)
                for image_id, lookup in batch.items():
                    cover_lookups[image_id] = (
                        str(lookup.tile_path),
                        lookup.x,
                        lookup.y,
                        lookup.width,
                        lookup.height,
                    )

            self.signals.result_ready.emit(stacks, cover_lookups, self._epoch)
        except Exception as e:  # noqa: BLE001
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()
