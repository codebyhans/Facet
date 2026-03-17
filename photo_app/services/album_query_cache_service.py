from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from photo_app.infrastructure.sqlalchemy_models import AlbumQueryCacheModel

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from photo_app.domain.models import Album, Image
    from photo_app.domain.repositories import AlbumRepository, ImageRepository

logger = logging.getLogger(__name__)


class AlbumQueryCacheService:
    """Persistent cache for expensive album image resolution."""

    def __init__(
        self,
        engine: Engine,
        album_repository: AlbumRepository,
        image_repository: ImageRepository,
    ) -> None:
        self._engine = engine
        self._album_repository = album_repository
        self._image_repository = image_repository

    def resolve_album(self, album_id: int) -> list[int]:
        """Resolve ordered image IDs for one album, using persistent cache."""
        try:
            album = self._album_repository.get(album_id)
            if album is None:
                logger.warning("Album %s not found in resolve_album", album_id)
                return []

            if self._cache_is_valid(album):
                return self._cached_image_ids(album.id or 0)

            image_ids = self._image_repository.list_ids_by_filters(
                person_ids=album.query_definition.person_ids,
                cluster_ids=album.query_definition.cluster_ids,
                date_from=album.query_definition.date_from,
                date_to=album.query_definition.date_to,
                tag_names=album.query_definition.tag_names,
                rating_min=album.query_definition.rating_min,
                quality_min=album.query_definition.quality_min,
                camera_models=album.query_definition.camera_models,
            )
            self._store_results(album, image_ids)
        except Exception:
            logger.exception("Failed to resolve album %s", album_id)
            return []
        else:
            return image_ids

    def get_album_images(self, album_id: int, offset: int, limit: int) -> list[Image]:
        """Return paginated album images in stable cached order."""
        try:
            album = self._album_repository.get(album_id)
            if album is None:
                logger.warning("Album %s not found in get_album_images", album_id)
                return []

            cache_valid = self._cache_is_valid(album)
            if not cache_valid:
                image_ids = self._image_repository.list_ids_by_filters(
                    person_ids=album.query_definition.person_ids,
                    cluster_ids=album.query_definition.cluster_ids,
                    date_from=album.query_definition.date_from,
                    date_to=album.query_definition.date_to,
                    tag_names=album.query_definition.tag_names,
                    rating_min=album.query_definition.rating_min,
                    quality_min=album.query_definition.quality_min,
                    camera_models=album.query_definition.camera_models,
                )
                self._store_results(album, image_ids)

            page_ids = self._cached_image_ids_page(album_id, offset=offset, limit=limit)
            return self._image_repository.list_by_ids(page_ids)
        except Exception:
            logger.exception("Failed to get album images for album %s", album_id)
            return []

    def invalidate_cache(self, album_id: int) -> None:
        """Invalidate one album cache."""
        with Session(self._engine) as session:
            try:
                session.execute(
                    delete(AlbumQueryCacheModel).where(
                        AlbumQueryCacheModel.album_id == album_id
                    )
                )
                session.commit()
            except Exception:
                logger.exception("Failed to invalidate cache for album %s", album_id)
                session.rollback()

    def invalidate_all(self) -> None:
        """Invalidate all album caches."""
        with Session(self._engine) as session:
            try:
                session.execute(delete(AlbumQueryCacheModel))
                session.commit()
            except Exception:
                logger.exception("Failed to invalidate all caches")
                session.rollback()

    def _cache_is_valid(self, album: Album) -> bool:
        if album.id is None:
            return False
        with Session(self._engine) as session:
            try:
                stmt = select(AlbumQueryCacheModel.query_version).where(
                    AlbumQueryCacheModel.album_id == album.id
                )
                versions = {int(value) for value in session.scalars(stmt)}
                return len(versions) == 1 and album.query_version in versions
            except Exception:
                logger.exception(
                    "Failed to check cache validity for album %s", album.id
                )
                return False

    def _cached_image_ids(self, album_id: int) -> list[int]:
        with Session(self._engine) as session:
            try:
                stmt = (
                    select(AlbumQueryCacheModel.image_id)
                    .where(AlbumQueryCacheModel.album_id == album_id)
                    .order_by(AlbumQueryCacheModel.position.asc())
                )
                return [int(image_id) for image_id in session.scalars(stmt)]
            except Exception:
                logger.exception(
                    "Failed to get cached image IDs for album %s", album_id
                )
                return []

    def _cached_image_ids_page(
        self, album_id: int, *, offset: int, limit: int
    ) -> list[int]:
        with Session(self._engine) as session:
            try:
                stmt = (
                    select(AlbumQueryCacheModel.image_id)
                    .where(AlbumQueryCacheModel.album_id == album_id)
                    .order_by(AlbumQueryCacheModel.position.asc())
                    .offset(offset)
                    .limit(limit)
                )
                return [int(image_id) for image_id in session.scalars(stmt)]
            except Exception:
                logger.exception(
                    "Failed to get cached image IDs page for album %s", album_id
                )
                return []

    def _invalidate_cache_in_session(self, session: Session, album_id: int) -> None:
        """Delete cache rows for one album using an already-open session."""
        try:
            session.execute(
                delete(AlbumQueryCacheModel).where(
                    AlbumQueryCacheModel.album_id == album_id
                )
            )
        except Exception:
            logger.exception("Failed to invalidate cache for album %s", album_id)
            raise

    def _store_results(self, album: Album, image_ids: list[int]) -> None:
        if album.id is None:
            return

        with Session(self._engine) as session:
            try:
                self._invalidate_cache_in_session(session, album.id)

                generated_at = datetime.now(tz=UTC)
                rows = [
                    AlbumQueryCacheModel(
                        album_id=album.id,
                        image_id=image_id,
                        query_version=album.query_version,
                        position=position,
                        generated_at=generated_at,
                    )
                    for position, image_id in enumerate(image_ids)
                ]
                if rows:
                    session.bulk_save_objects(rows)
                session.commit()
            except Exception:
                logger.exception("Failed to store cache results for album %s", album.id)
                session.rollback()
