from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from photo_app.domain.models import Collection
    from photo_app.domain.repositories import CollectionRepository
    from photo_app.services.album_query_cache_service import AlbumQueryCacheService

logger = logging.getLogger(__name__)


class CollectionNameError(ValueError):
    """Raised when a collection name is empty or already taken."""


class CollectionService:
    """Use case for hand-picked image collections."""

    def __init__(
        self,
        collection_repository: CollectionRepository,
        query_cache_service: AlbumQueryCacheService | None = None,
    ) -> None:
        self._collection_repository = collection_repository
        self._query_cache_service = query_cache_service

    def list_collections(self) -> list[Collection]:
        """Return all collections with image counts, ordered by name."""
        return self._collection_repository.list_all()

    def get_collection(self, collection_id: int) -> Collection | None:
        """Return one collection with its image count."""
        return self._collection_repository.get(collection_id)

    def create_collection(self, name: str) -> Collection:
        """Create an empty collection."""
        cleaned = self._validated_name(name)
        return self._collection_repository.create(
            cleaned, created_at=datetime.now(tz=UTC)
        )

    def rename_collection(self, collection_id: int, name: str) -> Collection | None:
        """Rename one collection."""
        cleaned = self._validated_name(name, own_id=collection_id)
        return self._collection_repository.update_name(collection_id, cleaned)

    def delete_collection(self, collection_id: int) -> bool:
        """Delete one collection; its images stay in the library."""
        deleted = self._collection_repository.delete(collection_id)
        if deleted:
            self._invalidate_album_caches()
        return deleted

    def add_images(self, collection_id: int, image_ids: Sequence[int]) -> int:
        """Add images to a collection; returns how many were newly added."""
        added = self._collection_repository.add_images(
            collection_id, image_ids, added_at=datetime.now(tz=UTC)
        )
        if added:
            self._invalidate_album_caches()
        return added

    def remove_images(self, collection_id: int, image_ids: Sequence[int]) -> int:
        """Remove images from a collection; returns how many were removed."""
        removed = self._collection_repository.remove_images(collection_id, image_ids)
        if removed:
            self._invalidate_album_caches()
        return removed

    def collections_containing(self, image_ids: Sequence[int]) -> list[Collection]:
        """Return the collections holding at least one of the given images."""
        return self._collection_repository.list_containing(image_ids)

    def _validated_name(self, name: str, *, own_id: int | None = None) -> str:
        cleaned = name.strip()
        if not cleaned:
            msg = "Collection name cannot be empty"
            raise CollectionNameError(msg)
        existing = self._collection_repository.get_by_name(cleaned)
        if existing is not None and existing.id != own_id:
            msg = f"A collection named '{existing.name}' already exists"
            raise CollectionNameError(msg)
        return cleaned

    def _invalidate_album_caches(self) -> None:
        # Any album may be scoped to (or exclude) this collection, and cached
        # results are only versioned by the album's query, not by membership.
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()
