from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
from PySide6.QtGui import QPixmap

from photo_app.app.models.photo_grid_model import PhotoGridItem
from photo_app.app.workers.album_worker import AlbumQueryWorker
from photo_app.app.workers.tile_build_worker import TileBuildWorker
from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore

if TYPE_CHECKING:
    from photo_app.app.view_models.album_view_model import AlbumViewModel

logger = logging.getLogger(__name__)


@dataclass
class AlbumPageResult:
    """Result from album query worker."""
    items: list[object]


class GalleryViewModel(QObject):
    """Bridge between photo grid model and album/tile services."""

    page_ready = Signal(list, bool)
    tile_ready = Signal(int, object)
    status = Signal(str)
    error = Signal(str)
    loading_started = Signal()
    loading_finished = Signal()
    tile_building_started = Signal()
    tile_building_finished = Signal()

    def __init__(
        self,
        album_view_model: AlbumViewModel,
        tile_store: ThumbnailTileStore,
        page_size: int = 200,
    ) -> None:
        super().__init__()
        self._album_view_model = album_view_model
        self._tile_store = tile_store
        self._page_size = page_size
        self._current_album_id: int | None = None
        self._library_mode = False
        self._offset = 0
        self._has_more = False
        self._loading_page = False
        self._thread_pool = QThreadPool.globalInstance()
        self._tile_building = False
        self._active_operations = 0  # Track concurrent operations
        # Strong references to prevent GC while workers are running
        self._active_tile_worker: TileBuildWorker | None = None
        self._active_page_worker: AlbumQueryWorker | None = None
        
        # Operation queue management
        self._operation_queue = []  # Queue of pending operations
        self._current_operation_id = None  # ID of currently executing operation
        self._operation_counter = 0  # Simple counter for operation IDs
        
        # Debouncing with operation management
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(100)  # 100ms debounce
        self._debounce_timer.timeout.connect(self._on_debounce_timeout)
        self._pending_album_id: int | None = None
        self._pending_library_mode = False
        
        # Stale result protection
        self._epoch = 0  # Increment on each new album/library selection

    def select_album(self, album_id: int) -> None:
        """Load first page for selected album with debouncing."""
        self._cancel_current_operation()
        self._debounce_timer.stop()
        self._pending_album_id = album_id
        self._pending_library_mode = False
        self._debounce_timer.start()

    def select_library(self) -> None:
        """Load first page for all-library browsing with debouncing."""
        self._cancel_current_operation()
        self._debounce_timer.stop()
        self._pending_album_id = None
        self._pending_library_mode = True
        self._debounce_timer.start()

    def load_next_page(self) -> None:
        """Load next page if available."""
        if not self._has_more or self._loading_page:
            return
        self._load_page(append=True)

    def request_tile(self, tile_index: int) -> None:
        """Load one tile pixmap directly (fast I/O, safe on main thread)."""
        try:
            # Load tile directly from disk (fast operation, safe on main thread)
            tile_path = self._tile_store.get_tile(tile_index)
            if tile_path is None:
                return
            
            # Load pixmap in main thread (required for QPixmap safety)
            pixmap = QPixmap(str(tile_path))
            if not pixmap.isNull():
                self.tile_ready.emit(tile_index, pixmap)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Error loading tile {tile_index}: {exc}")

    def _ensure_tiles_then_load(self) -> None:
        if self._tile_building:
            return
        self._tile_building = True
        captured_epoch = self._epoch                          # capture now

        worker = TileBuildWorker(self._tile_store.build_missing_tiles)
        worker.signals.result_ready.connect(self._on_tile_build_result)
        worker.signals.error.connect(self._on_error)

        def on_tile_build_finished() -> None:                 # epoch-aware closure
            self._active_tile_worker = None                   # release reference when done
            self._on_tile_build_finished(epoch=captured_epoch)

        worker.signals.finished.connect(on_tile_build_finished)
        self._active_tile_worker = worker                     # keep alive until finished
        self._thread_pool.start(worker)
        # DO NOT load page here - wait for tiles to finish first

    def _on_tile_build_result(self, result: object) -> None:
        built_images = int(getattr(result, "images_built", 0))
        built_tiles = int(getattr(result, "tiles_built", 0))
        self.status.emit(f"Tile cache updated: {built_images} images, {built_tiles} tiles")

    def _on_tile_build_finished(self, *, epoch: int) -> None:
        print(f"[GALLERY] Tile build finished: epoch={epoch}, self._epoch={self._epoch}")
        if epoch != self._epoch:
            return
        self._tile_building = False
        self.tile_building_finished.emit()
        print(f"[GALLERY] Tile build epoch OK, calling _load_page(append=False)")
        self._load_page(append=False)

    def _load_page(self, *, append: bool) -> None:
        self._loading_page = True
        if self._library_mode:
            worker = AlbumQueryWorker(
                self._album_view_model.resolve_library_images,
                offset=self._offset,
                limit=self._page_size,
            )
        else:
            album_id = self._current_album_id
            if album_id is None:
                self._loading_page = False
                return
            worker = AlbumQueryWorker(
                self._album_view_model.resolve_album_images,
                album_id,
                offset=self._offset,
                limit=self._page_size,
            )
        
        # Create a closure that captures the append parameter and current epoch
        current_epoch = self._epoch
        def on_page_result(page: object) -> None:
            self._on_page_result(page, append=append, epoch=current_epoch)

        def on_page_finished() -> None:                       # ADD THIS
            self._active_page_worker = None                   # release reference when done
            self._on_page_finished(epoch=current_epoch)

        worker.signals.result_ready.connect(on_page_result)
        worker.signals.error.connect(self._on_error)
        worker.signals.finished.connect(on_page_finished)    # CHANGE THIS (was self._on_page_finished)
        self._active_page_worker = worker                     # keep alive until finished
        self._thread_pool.start(worker)

    def _on_page_result(self, page: object, *, append: bool, epoch: int) -> None:
        print(f"[GALLERY] Page result arrived: epoch={epoch}, self._epoch={self._epoch}, items={len(getattr(page, 'items', []))}")
        # Check for stale result - if epoch doesn't match current, discard
        if epoch != self._epoch:
            return
            
        items: list[PhotoGridItem] = []
        page_items = getattr(page, "items", [])
        for image in page_items:
            image_id = getattr(image, "id", None)
            image_hash = getattr(image, "hash", None)
            if not isinstance(image_id, int) or not isinstance(image_hash, str):
                continue
            tile_lookup = self._tile_store.get_image_tile(image_id)
            items.append(
                PhotoGridItem(
                    image_id=image_id,
                    image_hash=image_hash,
                    file_path=str(getattr(image, "file_path", "")),
                    capture_date=getattr(image, "capture_date", None),
                    tile_index=(
                        None if tile_lookup is None else int(tile_lookup.tile_index)
                    ),
                    position_in_tile=(
                        None
                        if tile_lookup is None
                        else int(tile_lookup.position_in_tile)
                    ),
                )
            )
        self._offset += len(items)
        self._has_more = len(items) >= self._page_size
        self.page_ready.emit(items, append)
        # Update status with more specific information
        action = "Appended" if append else "Loaded"
        print(f"[GALLERY] Page result processed: {len(items)} items, emitting page_ready")
        self.status.emit(f"{action} {len(items)} photos (total: {self._offset})")

    def _on_page_finished(self, *, epoch: int) -> None:
        if epoch != self._epoch:
            return
        self._loading_page = False
        self._current_operation_id = None
        self.loading_finished.emit()

    def _on_error(self, message: str) -> None:
        print(f"[GALLERY] ERROR: {message}")
        self._loading_page = False
        self._tile_building = False
        self.error.emit(message)
        # Also update status to show error state
        self.status.emit(f"Error: {message}")
        # Ensure operation is marked as complete
        if self._current_operation_id is not None:
            self._current_operation_id = None
            self.loading_finished.emit()

    def _on_debounce_timeout(self) -> None:
        """Handle debounced album selection with operation queue management."""
        # Cancel any in-progress operation before starting a new one.
        # This resets _tile_building, _loading_page, and _current_operation_id
        # so the new operation always proceeds cleanly.
        self._cancel_current_operation()
        
        print(f"[GALLERY] Debounce fired: library_mode={self._pending_library_mode}, album_id={self._pending_album_id}")
        # Create new operation
        self._operation_counter += 1
        operation_id = self._operation_counter
        
        if self._pending_album_id is not None:
            self._library_mode = False
            self._current_album_id = self._pending_album_id
            self._offset = 0
            self._has_more = True
            self.status.emit(f"Loading album {self._pending_album_id}...")
            self.loading_started.emit()
            self._execute_operation(operation_id, "album", self._pending_album_id)
            self._pending_album_id = None
        elif self._pending_library_mode:
            self._library_mode = True
            self._current_album_id = None
            self._offset = 0
            self._has_more = True
            self.status.emit("Loading library...")
            self.loading_started.emit()
            self._execute_operation(operation_id, "library", None)
            self._pending_library_mode = False

    def _execute_operation(self, operation_id: int, operation_type: str, album_id: int | None) -> None:
        """Execute an album loading operation with proper error handling."""
        self._current_operation_id = operation_id
        # Increment epoch to mark this as the current operation
        self._epoch += 1
        
        # Reset pagination state for new operation
        self._offset = 0
        self._has_more = True
        
        try:
            if operation_type == "album" and album_id is not None:
                self._ensure_tiles_then_load()
            elif operation_type == "library":
                self._ensure_tiles_then_load()
        except Exception as exc:
            logger.error(f"Operation {operation_id} failed: {exc}")
            self._on_error(f"Failed to load {operation_type}: {exc}")
            # Ensure operation is marked as complete on error
            if self._current_operation_id == operation_id:
                self._current_operation_id = None
                self.loading_finished.emit()

    def _cancel_current_operation(self) -> None:
        """Cancel any currently running operation."""
        if self._current_operation_id is not None:
            logger.info(f"Cancelling operation {self._current_operation_id}")
            # Cancel tile building if in progress
            if self._tile_building:
                self._tile_building = False
            # Cancel page loading if in progress
            if self._loading_page:
                self._loading_page = False
            self._active_tile_worker = None   # release GC reference
            self._active_page_worker = None   # release GC reference
            self._current_operation_id = None
