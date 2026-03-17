from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from photo_app.domain.models import Album, Image
from photo_app.domain.services import parse_album_query

if TYPE_CHECKING:
    from photo_app.domain.repositories import AlbumRepository, ImageRepository
    from photo_app.services.album_query_cache_service import AlbumQueryCacheService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlbumPage:
    """Paginated album images."""

    items: list[Image]
    offset: int
    limit: int


class AlbumService:
    """Use case for virtual albums."""

    def __init__(
        self,
        album_repository: AlbumRepository,
        image_repository: ImageRepository,
        query_cache_service: AlbumQueryCacheService | None = None,
    ) -> None:
        self._album_repository = album_repository
        self._image_repository = image_repository
        self._query_cache_service = query_cache_service

    def create_album(self, name: str, raw_query: dict[str, object]) -> Album:
        """Persist a virtual album."""
        query = parse_album_query(raw_query)
        album = Album(
            id=None,
            name=name,
            query_definition=query,
            query_version=1,
            created_at=datetime.now(tz=UTC),
        )
        created = self._album_repository.create(album)
        if created.id is not None and self._query_cache_service is not None:
            self._query_cache_service.invalidate_cache(created.id)
        return created

    def list_albums(self) -> list[Album]:
        """Return all virtual albums."""
        return self._album_repository.list_all()

    def rename_album(self, album_id: int, name: str) -> Album | None:
        """Rename one album."""
        cleaned = name.strip()
        if not cleaned:
            return None
        return self._album_repository.update_name(album_id, cleaned)

    def update_album_query(
        self,
        album_id: int,
        raw_query: dict[str, object],
    ) -> Album | None:
        """Update one virtual album query definition."""
        query = parse_album_query(raw_query)
        updated = self._album_repository.update_query(album_id, query)
        if updated is not None and updated.id is not None and self._query_cache_service is not None:
            self._query_cache_service.invalidate_cache(updated.id)
        return updated

    def delete_album(self, album_id: int) -> bool:
        """Delete one album."""
        deleted = self._album_repository.delete(album_id)
        if deleted and self._query_cache_service is not None:
            try:
                self._query_cache_service.invalidate_cache(album_id)
            except Exception:  # noqa: BLE001
                # Log the error but don't fail album deletion due to cache issues
                logger.warning(
                    "Failed to invalidate cache for album %s",
                    album_id,
                )
        return bool(deleted)

    def list_album_images(
        self,
        album_id: int,
        *,
        offset: int = 0,
        limit: int = 100,
        query_definition: dict[str, object] | None = None,
    ) -> AlbumPage:
        """Translate saved JSON query into repository filtering."""
        try:
            album = self._album_repository.get(album_id)
            if album is None:
                logger.warning("Album %s not found", album_id)
                return AlbumPage(items=[], offset=offset, limit=limit)

            # Combine album query with filter query if provided
            if query_definition:
                # Parse the filter query
                filter_query = parse_album_query(query_definition)

                # Combine with album query - filter takes precedence
                combined_person_ids = filter_query.person_ids or album.query_definition.person_ids
                combined_cluster_ids = filter_query.cluster_ids or album.query_definition.cluster_ids
                combined_date_from = filter_query.date_from or album.query_definition.date_from
                combined_date_to = filter_query.date_to or album.query_definition.date_to
                combined_tag_names = filter_query.tag_names or album.query_definition.tag_names
                combined_rating_min = filter_query.rating_min if filter_query.rating_min is not None else album.query_definition.rating_min
                combined_quality_min = filter_query.quality_min if filter_query.quality_min is not None else album.query_definition.quality_min
                combined_camera_models = filter_query.camera_models or album.query_definition.camera_models
            else:
                # Use album query only
                combined_person_ids = album.query_definition.person_ids
                combined_cluster_ids = album.query_definition.cluster_ids
                combined_date_from = album.query_definition.date_from
                combined_date_to = album.query_definition.date_to
                combined_tag_names = album.query_definition.tag_names
                combined_rating_min = album.query_definition.rating_min
                combined_quality_min = album.query_definition.quality_min
                combined_camera_models = album.query_definition.camera_models

            if self._query_cache_service is None:
                images = self._image_repository.list_by_filters(
                    person_ids=combined_person_ids,
                    cluster_ids=combined_cluster_ids,
                    date_from=combined_date_from,
                    date_to=combined_date_to,
                    tag_names=combined_tag_names,
                    rating_min=combined_rating_min,
                    quality_min=combined_quality_min,
                    camera_models=combined_camera_models,
                    offset=offset,
                    limit=limit,
                )
                return AlbumPage(items=images, offset=offset, limit=limit)

            # For cache service, we need to handle the combined query differently
            # For now, fall back to direct filtering when filters are applied
            if query_definition:
                images = self._image_repository.list_by_filters(
                    person_ids=combined_person_ids,
                    cluster_ids=combined_cluster_ids,
                    date_from=combined_date_from,
                    date_to=combined_date_to,
                    tag_names=combined_tag_names,
                    rating_min=combined_rating_min,
                    quality_min=combined_quality_min,
                    camera_models=combined_camera_models,
                    offset=offset,
                    limit=limit,
                )
                return AlbumPage(items=images, offset=offset, limit=limit)

            images = self._query_cache_service.get_album_images(
                album_id=album_id,
                offset=offset,
                limit=limit,
            )
            return AlbumPage(items=images, offset=offset, limit=limit)
        except Exception:
            logger.exception("Failed to list album images for album %s", album_id)
            return AlbumPage(items=[], offset=offset, limit=limit)

    def list_years(self) -> list[int]:
        """Return distinct indexed years for filter pickers."""
        years = {
            image.year
            for image in self._image_repository.list_all()
            if image.year is not None
        }
        return sorted(year for year in years if year is not None)

    def list_library_images(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        query_definition: dict[str, object] | None = None,
    ) -> AlbumPage:
        """Return paginated all-photos library view with optional filtering."""
        # If query_definition is provided, apply filters
        if query_definition:
            filter_query = parse_album_query(query_definition)
            images = self._image_repository.list_by_filters(
                person_ids=filter_query.person_ids,
                cluster_ids=filter_query.cluster_ids,
                date_from=filter_query.date_from,
                date_to=filter_query.date_to,
                tag_names=filter_query.tag_names,
                rating_min=filter_query.rating_min,
                quality_min=filter_query.quality_min,
                camera_models=filter_query.camera_models,
                offset=offset,
                limit=limit,
            )
            return AlbumPage(items=images, offset=offset, limit=limit)

        # Use paginated query to avoid loading all images into memory
        items = self._image_repository.list_paginated(offset=offset, limit=limit)
        return AlbumPage(items=items, offset=offset, limit=limit)

    def get_image_repository(self) -> ImageRepository:
        """Get the image repository for accessing image data."""
        return self._image_repository
