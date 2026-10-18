"""Background worker for loading one person's full image gallery."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

if TYPE_CHECKING:
    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
    from photo_app.services.face_review_service import FaceReviewService


class PersonGalleryWorkerSignals(QObject):
    """Signals for the PersonGalleryWorker."""

    # image_ids, image_paths, bbox_by_image_id, tile_lookups, request_id. Declared as `object` (not `list`/`dict`)
    # because the queued cross-thread connection can't convert a dict of
    # custom dataclass values (ImageTileLookup) to C++ — `object` passes the
    # Python object through untouched, matching the pattern used by the
    # other workers in this codebase (e.g. PeopleListWorkerSignals).
    result_ready = Signal(object, object, object, object, int)
    error = Signal(str)
    finished = Signal()  # always emitted when run() exits


class PersonGalleryWorker(QRunnable):
    """Fetch one person's full (uncapped) image list + tile lookups off the UI thread.

    `images_for_person()` plus a tile-lookup batch query together can take
    several hundred ms for a large cluster (thousands of images) — done
    synchronously, that means the People tab visibly freezes on every click
    into a person's detail view, right before the view even switches.

    Deliberately does *not* render any highlight thumbnails here (unlike
    `PeopleListWorker`, which pre-renders a bounded set
    up front): `ClusterImageModel` composites the highlight box lazily,
    per row, only for images Qt actually paints (i.e. the visible
    viewport), directly from the already-cached tile crop — cheap enough
    now that it never needs a full-resolution decode (see
    `render_face_highlight_from_tile`/`scale_bbox_to_fit_cell`) that doing
    it eagerly for a gallery of thousands of images would be pure waste.
    This worker just hands over the (bbox, tile lookup) data the model
    needs to do that.
    """

    def __init__(
        self,
        face_review_service: FaceReviewService,
        tile_store: ThumbnailTileStore | None,
        person_id: int,
        request_id: int,
    ) -> None:
        super().__init__()
        self._face_review_service = face_review_service
        self._tile_store = tile_store
        self._person_id = person_id
        self._request_id = request_id
        self.signals = PersonGalleryWorkerSignals()

    @Slot()
    def run(self) -> None:
        """Run the worker in a background thread."""
        try:
            image_ids, image_paths, bbox_by_image_id = (
                self._face_review_service.images_for_person(self._person_id)
            )
            tile_lookups = {}
            if self._tile_store is not None and image_ids:
                tile_lookups = self._tile_store.get_image_tiles_batch(image_ids)

            self.signals.result_ready.emit(
                image_ids,
                image_paths,
                bbox_by_image_id,
                tile_lookups,
                self._request_id,
            )
        except Exception as e:  # noqa: BLE001
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()
