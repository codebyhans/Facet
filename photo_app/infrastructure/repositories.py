from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import and_, exists, func, nulls_last, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from photo_app.domain.models import (
    Album,
    Collection,
    Face,
    FaceClusterMembership,
    FaceStackRow,
    IdentityCluster,
    Image,
    ImageSummary,
    Person,
)
from photo_app.domain.services import album_query_to_dict, parse_album_query
from photo_app.domain.value_objects import AlbumQuery, BoundingBox
from photo_app.infrastructure.sqlalchemy_models import (
    AlbumModel,
    AlbumQueryCacheModel,
    AppSettingModel,
    CollectionImageModel,
    CollectionModel,
    FaceClusterMembershipModel,
    FaceModel,
    IdentityClusterModel,
    ImageModel,
    ImageTagModel,
    PersonModel,
    ThumbnailTileModel,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy import ColumnElement, Select
    from sqlalchemy.engine import Engine


def _to_image(entity: ImageModel) -> Image:
    return Image(
        id=entity.id,
        file_path=entity.file_path,
        capture_date=entity.capture_date,
        year=entity.year,
        month=entity.month,
        hash=entity.hash,
        width=entity.width,
        height=entity.height,
        indexed_at=entity.indexed_at,
        updated_at=entity.updated_at,
        rating=entity.rating,
        quality_score=entity.quality_score,
        is_favorite=entity.is_favorite,
        user_notes=entity.user_notes,
        camera_model=entity.camera_model,
        gps_latitude=entity.gps_latitude,
        gps_longitude=entity.gps_longitude,
        location_name=entity.location_name,
        flag=entity.flag,
    )


def _to_face(entity: FaceModel) -> Face:
    bbox = BoundingBox(
        x=entity.bbox_x,
        y=entity.bbox_y,
        w=entity.bbox_w,
        h=entity.bbox_h,
    )
    return Face(
        id=entity.id,
        image_id=entity.image_id,
        bbox=bbox,
        embedding=entity.embedding,
        person_id=entity.person_id,
        confidence_score=entity.confidence_score,
        manual_assignment=entity.manual_assignment,
        excluded=entity.excluded,
    )


def _to_person(entity: PersonModel) -> Person:
    return Person(
        id=entity.id,
        name=entity.name,
        created_at=entity.created_at,
        birth_date=entity.birth_date,
    )


def _to_album(entity: AlbumModel) -> Album:
    return Album(
        id=entity.id,
        name=entity.name,
        query_definition=parse_album_query(entity.query_definition),
        query_version=entity.query_version,
        created_at=entity.created_at,
    )


def _to_identity_cluster(entity: IdentityClusterModel) -> IdentityCluster:
    return IdentityCluster(
        id=entity.id,
        canonical_embedding=entity.canonical_embedding,
        face_count=entity.face_count if entity.face_count is not None else 0,
        variance=entity.variance,
        flagged_for_review=entity.flagged_for_review,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        person_id=entity.person_id,
    )


def _to_membership(entity: FaceClusterMembershipModel) -> FaceClusterMembership:
    return FaceClusterMembership(
        face_id=entity.face_id,
        cluster_id=entity.cluster_id,
        confidence=entity.confidence,
        assigned_at=entity.assigned_at,
    )


# SQLite's default build caps bound parameters per statement at 999; chunking
# `IN (...)` lookups keeps id-list queries working regardless of how large
# the input collection is (e.g. resetting identities across a whole library).
_ID_CHUNK_SIZE = 500
_T = TypeVar("_T")


def _chunked(items: Sequence[_T], size: int = _ID_CHUNK_SIZE) -> Iterator[Sequence[_T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class SqlAlchemyImageRepository:
    """Image repository implementation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_many(self, images: Sequence[Image]) -> None:
        """Insert images in chunks to stay within SQLite variable limits."""
        chunk_size = 500  # 500 rows x 9 columns = 4500 variables, well under SQLite's 999-per-stmt limit
        with Session(self._engine) as session:
            for i in range(0, len(images), chunk_size):
                chunk = images[i : i + chunk_size]
                rows = [
                    ImageModel(
                        file_path=image.file_path,
                        capture_date=image.capture_date,
                        year=image.year,
                        month=image.month,
                        hash=image.hash,
                        width=image.width,
                        height=image.height,
                        indexed_at=image.indexed_at,
                    )
                    for image in chunk
                ]
                session.bulk_save_objects(rows)
            session.commit()  # single commit after all chunks

    def exists_by_path(self, file_path: str) -> bool:
        with Session(self._engine) as session:
            stmt = select(exists().where(ImageModel.file_path == file_path))
            return bool(session.scalar(stmt))

    def get_by_path(self, file_path: str) -> Image | None:
        with Session(self._engine) as session:
            stmt = select(ImageModel).where(ImageModel.file_path == file_path)
            row = session.scalar(stmt)
            return None if row is None else _to_image(row)

    def get_by_id(self, image_id: int) -> Image | None:
        """Get a single image by ID."""
        with Session(self._engine) as session:
            stmt = select(ImageModel).where(ImageModel.id == image_id)
            row = session.scalar(stmt)
            return None if row is None else _to_image(row)

    def list_unprocessed_for_faces(self, limit: int) -> list[Image]:
        with Session(self._engine) as session:
            stmt = (
                select(ImageModel)
                .where(ImageModel.face_count.is_(None))
                .order_by(ImageModel.id.asc())
                .limit(limit)
            )
            return [_to_image(row) for row in session.scalars(stmt)]

    def list_all(self) -> list[Image]:
        with Session(self._engine) as session:
            stmt = select(ImageModel)
            return [_to_image(row) for row in session.scalars(stmt)]

    def list_all_summary(self) -> list[ImageSummary]:
        """Lightweight projection for grouping/display — skips large optional
        text columns (user_notes, location_name, etc.) that callers like
        `person_stacks()` never touch, cutting the row size (and Python
        object allocation) considerably at library scale."""
        with Session(self._engine) as session:
            stmt = select(
                ImageModel.id,
                ImageModel.file_path,
                ImageModel.width,
                ImageModel.height,
                ImageModel.capture_date,
            )
            return [
                ImageSummary(
                    id=row.id,
                    file_path=row.file_path,
                    width=row.width,
                    height=row.height,
                    capture_date=row.capture_date,
                )
                for row in session.execute(stmt)
            ]

    def list_paginated(self, *, offset: int, limit: int) -> list[Image]:
        """Paginated query to avoid loading all images into memory."""
        with Session(self._engine) as session:
            stmt = (
                select(ImageModel)
                .order_by(
                    nulls_last(ImageModel.capture_date.asc()), ImageModel.id.asc()
                )
                .offset(offset)
                .limit(limit)
            )
            return [_to_image(row) for row in session.scalars(stmt)]

    def list_by_filters(
        self,
        query: AlbumQuery,
        *,
        narrow_by: AlbumQuery | None = None,
        offset: int,
        limit: int,
    ) -> list[Image]:
        ordered_ids = self.list_ids_by_filters(query, narrow_by=narrow_by)
        page_ids = ordered_ids[offset : offset + limit]
        return self.list_by_ids(page_ids)

    def list_ids_by_filters(
        self,
        query: AlbumQuery,
        *,
        narrow_by: AlbumQuery | None = None,
    ) -> list[int]:
        stmt = self._filtered_ids_stmt(query, narrow_by).order_by(
            nulls_last(ImageModel.capture_date.asc()),
            ImageModel.id.asc(),
        )
        with Session(self._engine) as session:
            rows = session.scalars(stmt).unique().all()
            return [int(row) for row in rows]

    def count_by_filters(
        self,
        query: AlbumQuery,
        *,
        narrow_by: AlbumQuery | None = None,
    ) -> int:
        ids = self._filtered_ids_stmt(query, narrow_by).subquery()
        with Session(self._engine) as session:
            return int(session.scalar(select(func.count()).select_from(ids)) or 0)

    def _filtered_ids_stmt(
        self, query: AlbumQuery, narrow_by: AlbumQuery | None
    ) -> Select[tuple[int]]:
        """Build the unordered image-id query shared by listing and counting."""
        stmt = self._apply_query(select(ImageModel.id), query)
        if narrow_by is not None:
            stmt = self._apply_query(stmt, narrow_by)
        return stmt

    def _apply_query(  # noqa: C901, PLR0912
        self, stmt: Select[tuple[int]], query: AlbumQuery
    ) -> Select[tuple[int]]:
        """AND every criterion of one query onto the statement."""
        # Dated / undated split (undated images never match a date range)
        if query.has_date is True:
            stmt = stmt.where(ImageModel.capture_date.is_not(None))
        elif query.has_date is False:
            stmt = stmt.where(ImageModel.capture_date.is_(None))

        # People/cluster/tag/collection filters use uncorrelated IN
        # subqueries: SQLite evaluates them once. A correlated EXISTS made it
        # rescan every face of the person for each image (~minutes for a
        # large library).

        # Filter by people: "AND" intersects one subquery per person so the
        # image must contain every one of them, "OR" matches any.
        if query.person_ids and query.person_logic == "AND":
            for person_id in dict.fromkeys(query.person_ids):
                stmt = stmt.where(
                    ImageModel.id.in_(
                        select(FaceModel.image_id).where(
                            FaceModel.excluded.is_(False),
                            FaceModel.person_id == person_id,
                        )
                    )
                )
        elif query.person_ids:
            stmt = stmt.where(
                ImageModel.id.in_(
                    select(FaceModel.image_id).where(
                        FaceModel.excluded.is_(False),
                        FaceModel.person_id.in_(query.person_ids),
                    )
                )
            )

        # Filter by clusters
        if query.cluster_ids:
            stmt = stmt.where(
                ImageModel.id.in_(
                    select(FaceModel.image_id)
                    .join(
                        FaceClusterMembershipModel,
                        FaceClusterMembershipModel.face_id == FaceModel.id,
                    )
                    .where(
                        FaceModel.excluded.is_(False),
                        FaceClusterMembershipModel.cluster_id.in_(query.cluster_ids),
                    )
                )
            )

        # Filter by tags
        if query.tag_names:
            stmt = stmt.where(
                ImageModel.id.in_(
                    select(ImageTagModel.image_id).where(
                        ImageTagModel.tag_name.in_(query.tag_names)
                    )
                )
            )

        # Filter by collections: in any of the included, in none of the excluded
        if query.collection_ids:
            stmt = stmt.where(
                ImageModel.id.in_(
                    select(CollectionImageModel.image_id).where(
                        CollectionImageModel.collection_id.in_(query.collection_ids)
                    )
                )
            )
        if query.exclude_collection_ids:
            stmt = stmt.where(
                ImageModel.id.not_in(
                    select(CollectionImageModel.image_id).where(
                        CollectionImageModel.collection_id.in_(
                            query.exclude_collection_ids
                        )
                    )
                )
            )

        # Filter by rating
        if query.rating_min is not None:
            stmt = stmt.where(ImageModel.rating >= query.rating_min)

        # Filter by quality score
        if query.quality_min is not None:
            stmt = stmt.where(ImageModel.quality_score >= query.quality_min)

        # Filter by camera model
        if query.camera_models:
            stmt = stmt.where(ImageModel.camera_model.in_(query.camera_models))

        # Filter by date range
        clauses = []
        if query.date_from is not None:
            clauses.append(func.date(ImageModel.capture_date) >= query.date_from)
        if query.date_to is not None:
            clauses.append(func.date(ImageModel.capture_date) <= query.date_to)
        if clauses:
            stmt = stmt.where(and_(*clauses))

        # Filter by flags
        if query.flags:
            stmt = stmt.where(ImageModel.flag.in_(query.flags))

        return stmt

    def list_by_ids(self, image_ids: Sequence[int]) -> list[Image]:
        with Session(self._engine) as session:
            if not image_ids:
                return []
            by_id: dict[int, Image] = {}
            for chunk in _chunked(image_ids):
                stmt = select(ImageModel).where(ImageModel.id.in_(chunk))
                by_id.update((row.id, _to_image(row)) for row in session.scalars(stmt))
            ordered: list[Image] = []
            for image_id in image_ids:
                image = by_id.get(image_id)
                if image is not None:
                    ordered.append(image)
            return ordered

    def update_flag(self, image_id: int, flag: str | None) -> None:
        with Session(self._engine) as session:
            stmt = select(ImageModel).where(ImageModel.id == image_id)
            row = session.scalar(stmt)
            if row is not None:
                row.flag = flag
                session.commit()

    def update_face_count(self, image_id: int, count: int) -> None:
        with Session(self._engine) as session:
            stmt = select(ImageModel).where(ImageModel.id == image_id)
            row = session.scalar(stmt)
            if row is not None:
                row.face_count = count
                session.commit()

    def list_all_paths(self) -> list[str]:
        """Return all indexed file paths in a single query."""
        with Session(self._engine) as session:
            stmt = select(ImageModel.file_path)
            return [str(row) for row in session.scalars(stmt)]

    def list_without_capture_date(self) -> list[Image]:
        """Return all images that have no capture_date stored."""
        with Session(self._engine) as session:
            stmt = select(ImageModel).where(ImageModel.capture_date.is_(None))
            return [_to_image(row) for row in session.scalars(stmt)]

    def list_without_real_timestamp(self) -> list[Image]:
        """Return images with no capture_date, or capture_date at exactly midnight (folder-date fallback)."""
        with Session(self._engine) as session:
            stmt = select(ImageModel).where(
                ImageModel.capture_date.is_(None)
                | (func.strftime("%H:%M:%S", ImageModel.capture_date) == "00:00:00")
            )
            return [_to_image(row) for row in session.scalars(stmt)]

    def update_capture_date(self, image_id: int, capture_date: datetime) -> None:
        """Set capture_date (and year/month) for an image."""
        with Session(self._engine) as session:
            stmt = select(ImageModel).where(ImageModel.id == image_id)
            row = session.scalar(stmt)
            if row is not None:
                row.capture_date = capture_date
                row.year = capture_date.year
                row.month = capture_date.month
                session.commit()

    def delete_by_paths(self, paths: list[str]) -> int:
        """Delete images (and all dependent rows) for the given file paths.

        Deletion order follows FK constraints:
        face_cluster_memberships -> faces -> image_tags -> collection_images
        -> thumbnail_tiles -> album_query_cache -> images
        """
        if not paths:
            return 0
        with Session(self._engine) as session:
            ids: list[int] = []
            for path_chunk in _chunked(paths):
                ids.extend(
                    session.scalars(
                        select(ImageModel.id).where(
                            ImageModel.file_path.in_(path_chunk)
                        )
                    )
                )
            if not ids:
                return 0
            face_ids: list[int] = []
            for chunk in _chunked(ids):
                face_ids.extend(
                    session.scalars(
                        select(FaceModel.id).where(FaceModel.image_id.in_(chunk))
                    )
                )
            for chunk in _chunked(face_ids):
                session.execute(
                    sa_delete(FaceClusterMembershipModel).where(
                        FaceClusterMembershipModel.face_id.in_(chunk)
                    )
                )
            for chunk in _chunked(ids):
                session.execute(
                    sa_delete(FaceModel).where(FaceModel.image_id.in_(chunk))
                )
                session.execute(
                    sa_delete(ImageTagModel).where(ImageTagModel.image_id.in_(chunk))
                )
                session.execute(
                    sa_delete(CollectionImageModel).where(
                        CollectionImageModel.image_id.in_(chunk)
                    )
                )
                session.execute(
                    sa_delete(ThumbnailTileModel).where(
                        ThumbnailTileModel.image_id.in_(chunk)
                    )
                )
                session.execute(
                    sa_delete(AlbumQueryCacheModel).where(
                        AlbumQueryCacheModel.image_id.in_(chunk)
                    )
                )
                session.execute(sa_delete(ImageModel).where(ImageModel.id.in_(chunk)))
            session.commit()
            return len(ids)


class SqlAlchemyFaceRepository:
    """Face repository implementation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_many(self, faces: Sequence[Face]) -> None:
        with Session(self._engine) as session:
            rows = [
                FaceModel(
                    image_id=face.image_id,
                    bbox_x=face.bbox.x,
                    bbox_y=face.bbox.y,
                    bbox_w=face.bbox.w,
                    bbox_h=face.bbox.h,
                    embedding=face.embedding,
                    person_id=face.person_id,
                    manual_assignment=face.manual_assignment,
                    excluded=face.excluded,
                )
                for face in faces
            ]
            session.bulk_save_objects(rows)
            session.commit()

    def list_all(self) -> list[Face]:
        with Session(self._engine) as session:
            stmt = select(FaceModel)
            return [_to_face(row) for row in session.scalars(stmt)]

    def list_all_active(self) -> list[Face]:
        with Session(self._engine) as session:
            stmt = select(FaceModel).where(FaceModel.excluded.is_(False))
            return [_to_face(row) for row in session.scalars(stmt)]

    def list_active_stack_rows(self) -> list[FaceStackRow]:
        """Lightweight projection of non-excluded faces for stack grouping —
        skips the embedding BLOB (a few KB per face), which is otherwise
        fetched and deserialized for every face on every People-tab refresh
        despite never being used for grouping or cover selection."""
        with Session(self._engine) as session:
            stmt = select(
                FaceModel.id,
                FaceModel.image_id,
                FaceModel.person_id,
                FaceModel.bbox_x,
                FaceModel.bbox_y,
                FaceModel.bbox_w,
                FaceModel.bbox_h,
            ).where(FaceModel.excluded.is_(False))
            return [
                FaceStackRow(
                    id=row.id,
                    image_id=row.image_id,
                    person_id=row.person_id,
                    bbox=BoundingBox(
                        x=row.bbox_x, y=row.bbox_y, w=row.bbox_w, h=row.bbox_h
                    ),
                )
                for row in session.execute(stmt)
            ]

    def list_without_cluster_membership(self, limit: int | None = None) -> list[Face]:
        with Session(self._engine) as session:
            stmt = (
                select(FaceModel)
                .outerjoin(
                    FaceClusterMembershipModel,
                    FaceClusterMembershipModel.face_id == FaceModel.id,
                )
                .where(
                    FaceModel.excluded.is_(False),
                    FaceClusterMembershipModel.id.is_(None),
                )
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            return [_to_face(row) for row in session.scalars(stmt)]

    def list_by_image(self, image_id: int) -> list[Face]:
        with Session(self._engine) as session:
            stmt = select(FaceModel).where(
                FaceModel.image_id == image_id,
                FaceModel.excluded.is_(False),
            )
            return [_to_face(row) for row in session.scalars(stmt)]

    def list_by_person(self, person_id: int) -> list[Face]:
        with Session(self._engine) as session:
            stmt = select(FaceModel).where(
                FaceModel.person_id == person_id,
                FaceModel.excluded.is_(False),
            )
            return [_to_face(row) for row in session.scalars(stmt)]

    def get(self, face_id: int) -> Face | None:
        """Get a single face by ID."""
        with Session(self._engine) as session:
            stmt = select(FaceModel).where(FaceModel.id == face_id)
            row = session.scalar(stmt)
            return None if row is None else _to_face(row)

    def get_many(self, face_ids: Sequence[int]) -> list[Face]:
        """Load multiple faces by ID in a single query."""
        if not face_ids:
            return []
        faces: list[Face] = []
        with Session(self._engine) as session:
            for chunk in _chunked(face_ids):
                stmt = select(FaceModel).where(FaceModel.id.in_(chunk))
                faces.extend(_to_face(row) for row in session.scalars(stmt))
        return faces

    def assign_person_auto(self, face_ids: Sequence[int], person_id: int) -> None:
        if not face_ids:
            return
        with Session(self._engine) as session:
            for chunk in _chunked(face_ids):
                stmt = select(FaceModel).where(FaceModel.id.in_(chunk))
                for face in session.scalars(stmt):
                    if face.excluded or face.manual_assignment:
                        continue
                    face.person_id = person_id
            session.commit()

    def unassign_faces(self, face_ids: Sequence[int]) -> None:
        with Session(self._engine) as session:
            for chunk in _chunked(face_ids):
                session.execute(
                    sa_update(FaceModel)
                    .where(FaceModel.id.in_(chunk))
                    .values(person_id=None, manual_assignment=False)
                )
            session.commit()

    def assign_person_manual(self, face_ids: Sequence[int], person_id: int) -> None:
        if not face_ids:
            return
        with Session(self._engine) as session:
            for chunk in _chunked(face_ids):
                stmt = select(FaceModel).where(FaceModel.id.in_(chunk))
                for face in session.scalars(stmt):
                    if face.excluded:
                        continue
                    face.person_id = person_id
                    face.manual_assignment = True
            session.commit()

    def reassign_person(self, old_person_id: int, new_person_id: int) -> None:
        """Repoint every face from one person record to another.

        Unlike `assign_person_auto`, this does not skip manually-tagged
        faces: it's for consolidating two Person rows that turned out to be
        the same identity (e.g. two clusters merging), not for automatic
        re-clustering overriding a manual choice. `manual_assignment` is left
        as-is so the face's provenance (manual vs. auto) is preserved.
        """
        with Session(self._engine) as session:
            stmt = select(FaceModel).where(FaceModel.person_id == old_person_id)
            for face in session.scalars(stmt):
                face.person_id = new_person_id
            session.commit()

    def exclude(self, face_ids: Sequence[int]) -> None:
        if not face_ids:
            return
        with Session(self._engine) as session:
            for chunk in _chunked(face_ids):
                stmt = select(FaceModel).where(FaceModel.id.in_(chunk))
                for face in session.scalars(stmt):
                    face.excluded = True
                    face.person_id = None
                    face.manual_assignment = True
            session.commit()

    def list_excluded(self, limit: int | None = None) -> list[Face]:
        with Session(self._engine) as session:
            stmt = select(FaceModel).where(FaceModel.excluded.is_(True))
            if limit is not None:
                stmt = stmt.limit(limit)
            return [_to_face(row) for row in session.scalars(stmt)]

    def count_excluded(self) -> int:
        with Session(self._engine) as session:
            stmt = (
                select(func.count())
                .select_from(FaceModel)
                .where(FaceModel.excluded.is_(True))
            )
            return session.scalar(stmt) or 0

    def restore(self, face_ids: Sequence[int]) -> None:
        """Undo `exclude()`: the face re-enters normal auto-clustering on
        the next `index_new_faces()` run — as a fresh (likely new) cluster,
        since the old one was removed when it was excluded, but the
        underlying photo/bbox/embedding was never touched either way."""
        if not face_ids:
            return
        with Session(self._engine) as session:
            for chunk in _chunked(face_ids):
                stmt = select(FaceModel).where(FaceModel.id.in_(chunk))
                for face in session.scalars(stmt):
                    face.excluded = False
                    face.manual_assignment = False
            session.commit()

    def delete_by_image(self, image_id: int) -> None:
        with Session(self._engine) as session:
            stmt = select(FaceModel).where(FaceModel.image_id == image_id)
            for face in session.scalars(stmt):
                membership = session.scalar(
                    select(FaceClusterMembershipModel).where(
                        FaceClusterMembershipModel.face_id == face.id
                    )
                )
                if membership is not None:
                    session.delete(membership)
                session.delete(face)
            session.commit()

    def clear_all_person_assignments(self) -> None:
        """Unassign every face (including manually-tagged ones) for a full reset."""
        with Session(self._engine) as session:
            session.execute(
                sa_update(FaceModel).values(person_id=None, manual_assignment=False)
            )
            session.commit()


class SqlAlchemyPersonRepository:
    """Person repository implementation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, person: Person) -> Person:
        with Session(self._engine) as session:
            row = PersonModel(
                name=person.name,
                created_at=person.created_at,
                birth_date=person.birth_date,
            )
            session.add(row)
            session.flush()
            session.commit()
            return _to_person(row)

    def get(self, person_id: int) -> Person | None:
        with Session(self._engine) as session:
            stmt = select(PersonModel).where(PersonModel.id == person_id)
            row = session.scalar(stmt)
            return None if row is None else _to_person(row)

    def get_by_name(self, name: str) -> Person | None:
        with Session(self._engine) as session:
            cleaned = name.strip()
            if not cleaned:
                return None
            stmt = select(PersonModel).where(
                func.lower(PersonModel.name) == cleaned.lower()
            )
            row = session.scalar(stmt)
            return None if row is None else _to_person(row)

    def update_name(self, person_id: int, name: str) -> None:
        with Session(self._engine) as session:
            stmt = select(PersonModel).where(PersonModel.id == person_id)
            row = session.scalar(stmt)
            if row is not None:
                row.name = name
            session.commit()

    def update_birth_date(self, person_id: int, birth_date: date | None) -> None:
        with Session(self._engine) as session:
            stmt = select(PersonModel).where(PersonModel.id == person_id)
            row = session.scalar(stmt)
            if row is not None:
                row.birth_date = birth_date
            session.commit()

    def list_all(self) -> list[Person]:
        """Fetch all persons in a single query to avoid N+1 DB round-trips."""
        with Session(self._engine) as session:
            stmt = select(PersonModel)
            return [_to_person(row) for row in session.scalars(stmt)]

    def delete(self, person_id: int) -> None:
        """Delete a person record by ID."""
        with Session(self._engine) as session:
            stmt = select(PersonModel).where(PersonModel.id == person_id)
            row = session.scalar(stmt)
            if row is not None:
                session.delete(row)
                session.commit()

    def delete_many(self, person_ids: Sequence[int]) -> None:
        with Session(self._engine) as session:
            for chunk in _chunked(person_ids):
                session.execute(sa_delete(PersonModel).where(PersonModel.id.in_(chunk)))
            session.commit()

    def delete_all(self) -> None:
        """Delete every person record, for a full identity reset."""
        with Session(self._engine) as session:
            session.execute(sa_delete(PersonModel))
            session.commit()


class SqlAlchemyAlbumRepository:
    """Album repository implementation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, album: Album) -> Album:
        with Session(self._engine) as session:
            row = AlbumModel(
                name=album.name,
                query_definition=album_query_to_dict(album.query_definition),
                query_version=album.query_version,
                created_at=album.created_at,
            )
            session.add(row)
            session.flush()
            session.commit()
            return Album(
                id=row.id,
                name=row.name,
                query_definition=album.query_definition,
                query_version=row.query_version,
                created_at=row.created_at,
            )

    def get(self, album_id: int) -> Album | None:
        with Session(self._engine) as session:
            stmt = select(AlbumModel).where(AlbumModel.id == album_id)
            row = session.scalar(stmt)
            return None if row is None else _to_album(row)

    def list_all(self) -> list[Album]:
        with Session(self._engine) as session:
            stmt = select(AlbumModel).order_by(AlbumModel.name.asc())
            return [_to_album(row) for row in session.scalars(stmt)]

    def update_name(self, album_id: int, name: str) -> Album | None:
        with Session(self._engine) as session:
            stmt = select(AlbumModel).where(AlbumModel.id == album_id)
            row = session.scalar(stmt)
            if row is None:
                return None
            row.name = name
            session.flush()
            session.commit()
            return _to_album(row)

    def update_query(self, album_id: int, query: AlbumQuery) -> Album | None:
        with Session(self._engine) as session:
            stmt = select(AlbumModel).where(AlbumModel.id == album_id)
            row = session.scalar(stmt)
            if row is None:
                return None
            row.query_definition = album_query_to_dict(query)
            row.query_version += 1
            session.flush()
            session.commit()
            return _to_album(row)

    def delete(self, album_id: int) -> bool:
        with Session(self._engine) as session:
            stmt = select(AlbumModel).where(AlbumModel.id == album_id)
            row = session.scalar(stmt)
            if row is None:
                return False
            session.delete(row)
            session.flush()
            session.commit()
            return True


class SqlAlchemyCollectionRepository:
    """Collection repository implementation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, name: str, *, created_at: datetime) -> Collection:
        with Session(self._engine) as session:
            row = CollectionModel(name=name, created_at=created_at)
            session.add(row)
            session.commit()
            return Collection(id=row.id, name=row.name, created_at=row.created_at)

    def get(self, collection_id: int) -> Collection | None:
        with Session(self._engine) as session:
            rows = self._list(session, CollectionModel.id == collection_id)
            return rows[0] if rows else None

    def get_by_name(self, name: str) -> Collection | None:
        with Session(self._engine) as session:
            rows = self._list(
                session, func.lower(CollectionModel.name) == name.strip().lower()
            )
            return rows[0] if rows else None

    def list_all(self) -> list[Collection]:
        with Session(self._engine) as session:
            return self._list(session)

    def update_name(self, collection_id: int, name: str) -> Collection | None:
        with Session(self._engine) as session:
            row = session.get(CollectionModel, collection_id)
            if row is None:
                return None
            row.name = name
            session.commit()
        return self.get(collection_id)

    def delete(self, collection_id: int) -> bool:
        with Session(self._engine) as session:
            row = session.get(CollectionModel, collection_id)
            if row is None:
                return False
            session.execute(
                sa_delete(CollectionImageModel).where(
                    CollectionImageModel.collection_id == collection_id
                )
            )
            session.delete(row)
            session.commit()
            return True

    def add_images(
        self, collection_id: int, image_ids: Sequence[int], *, added_at: datetime
    ) -> int:
        wanted = list(dict.fromkeys(image_ids))
        with Session(self._engine) as session:
            existing: set[int] = set()
            for chunk in _chunked(wanted):
                existing.update(
                    session.scalars(
                        select(CollectionImageModel.image_id).where(
                            CollectionImageModel.collection_id == collection_id,
                            CollectionImageModel.image_id.in_(chunk),
                        )
                    )
                )
            rows = [
                CollectionImageModel(
                    collection_id=collection_id, image_id=image_id, added_at=added_at
                )
                for image_id in wanted
                if image_id not in existing
            ]
            session.add_all(rows)
            session.commit()
            return len(rows)

    def remove_images(self, collection_id: int, image_ids: Sequence[int]) -> int:
        with Session(self._engine) as session:
            before = self._member_count(session, collection_id)
            for chunk in _chunked(list(image_ids)):
                session.execute(
                    sa_delete(CollectionImageModel).where(
                        CollectionImageModel.collection_id == collection_id,
                        CollectionImageModel.image_id.in_(chunk),
                    )
                )
            removed = before - self._member_count(session, collection_id)
            session.commit()
            return removed

    def list_containing(self, image_ids: Sequence[int]) -> list[Collection]:
        with Session(self._engine) as session:
            collection_ids: set[int] = set()
            for chunk in _chunked(list(image_ids)):
                collection_ids.update(
                    session.scalars(
                        select(CollectionImageModel.collection_id)
                        .where(CollectionImageModel.image_id.in_(chunk))
                        .distinct()
                    )
                )
            if not collection_ids:
                return []
            return self._list(session, CollectionModel.id.in_(collection_ids))

    def _member_count(self, session: Session, collection_id: int) -> int:
        stmt = select(func.count(CollectionImageModel.id)).where(
            CollectionImageModel.collection_id == collection_id
        )
        return int(session.scalar(stmt) or 0)

    def _list(self, session: Session, *where: ColumnElement[bool]) -> list[Collection]:
        stmt = (
            select(CollectionModel, func.count(CollectionImageModel.id))
            .outerjoin(
                CollectionImageModel,
                CollectionImageModel.collection_id == CollectionModel.id,
            )
            .where(*where)
            .group_by(CollectionModel.id)
            .order_by(func.lower(CollectionModel.name))
        )
        return [
            Collection(
                id=row.id,
                name=row.name,
                created_at=row.created_at,
                image_count=int(count),
            )
            for row, count in session.execute(stmt)
        ]


class SqlAlchemyIdentityClusterRepository:
    """Identity cluster repository implementation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_cluster(
        self,
        canonical_embedding: bytes,
        *,
        created_at: datetime,
    ) -> IdentityCluster:
        with Session(self._engine) as session:
            row = IdentityClusterModel(
                canonical_embedding=canonical_embedding,
                face_count=0,
                variance=0.0,
                flagged_for_review=False,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(row)
            session.flush()
            session.commit()
            return _to_identity_cluster(row)

    def list_clusters(self) -> list[IdentityCluster]:
        with Session(self._engine) as session:
            stmt = select(IdentityClusterModel)
            return [_to_identity_cluster(row) for row in session.scalars(stmt)]

    def get_cluster(self, cluster_id: int) -> IdentityCluster | None:
        with Session(self._engine) as session:
            stmt = select(IdentityClusterModel).where(
                IdentityClusterModel.id == cluster_id
            )
            row = session.scalar(stmt)
            return None if row is None else _to_identity_cluster(row)

    def get_clusters(self, cluster_ids: Sequence[int]) -> dict[int, IdentityCluster]:
        """Load multiple clusters by ID in a single query."""
        if not cluster_ids:
            return {}
        with Session(self._engine) as session:
            stmt = select(IdentityClusterModel).where(
                IdentityClusterModel.id.in_(cluster_ids)
            )
            return {row.id: _to_identity_cluster(row) for row in session.scalars(stmt)}

    def set_cluster_person(self, cluster_id: int, person_id: int | None) -> None:
        with Session(self._engine) as session:
            stmt = select(IdentityClusterModel).where(
                IdentityClusterModel.id == cluster_id
            )
            row = session.scalar(stmt)
            if row is not None:
                row.person_id = person_id
            session.commit()

    def list_clusters_for_person(self, person_id: int) -> list[IdentityCluster]:
        with Session(self._engine) as session:
            stmt = select(IdentityClusterModel).where(
                IdentityClusterModel.person_id == person_id
            )
            return [_to_identity_cluster(row) for row in session.scalars(stmt)]

    def update_cluster_state(  # noqa: PLR0913
        self,
        cluster_id: int,
        *,
        canonical_embedding: bytes,
        face_count: int,
        variance: float,
        flagged_for_review: bool,
        updated_at: datetime,
    ) -> None:
        with Session(self._engine) as session:
            stmt = select(IdentityClusterModel).where(
                IdentityClusterModel.id == cluster_id
            )
            row = session.scalar(stmt)
            if row is None:
                return
            row.canonical_embedding = canonical_embedding
            row.face_count = face_count
            row.variance = variance
            row.flagged_for_review = flagged_for_review
            row.updated_at = updated_at
            session.commit()

    def upsert_membership(
        self,
        *,
        face_id: int,
        cluster_id: int,
        confidence: float,
        assigned_at: datetime,
    ) -> None:
        with Session(self._engine) as session:
            stmt = select(FaceClusterMembershipModel).where(
                FaceClusterMembershipModel.face_id == face_id
            )
            row = session.scalar(stmt)
            if row is None:
                session.add(
                    FaceClusterMembershipModel(
                        face_id=face_id,
                        cluster_id=cluster_id,
                        confidence=confidence,
                        assigned_at=assigned_at,
                    )
                )
                session.commit()
                return
            row.cluster_id = cluster_id
            row.confidence = confidence
            row.assigned_at = assigned_at
            session.commit()

    def list_memberships(self, cluster_id: int) -> list[FaceClusterMembership]:
        with Session(self._engine) as session:
            stmt = select(FaceClusterMembershipModel).where(
                FaceClusterMembershipModel.cluster_id == cluster_id
            )
            return [_to_membership(row) for row in session.scalars(stmt)]

    def get_membership(self, face_id: int) -> FaceClusterMembership | None:
        with Session(self._engine) as session:
            stmt = select(FaceClusterMembershipModel).where(
                FaceClusterMembershipModel.face_id == face_id
            )
            row = session.scalar(stmt)
            return None if row is None else _to_membership(row)

    def reassign_cluster_memberships(
        self,
        source_cluster_id: int,
        target_cluster_id: int,
    ) -> None:
        with Session(self._engine) as session:
            stmt = select(FaceClusterMembershipModel).where(
                FaceClusterMembershipModel.cluster_id == source_cluster_id
            )
            for membership in session.scalars(stmt):
                membership.cluster_id = target_cluster_id
            session.commit()

    def delete_cluster(self, cluster_id: int) -> None:
        with Session(self._engine) as session:
            membership_stmt = select(FaceClusterMembershipModel).where(
                FaceClusterMembershipModel.cluster_id == cluster_id
            )
            for membership in session.scalars(membership_stmt):
                session.delete(membership)

            cluster_stmt = select(IdentityClusterModel).where(
                IdentityClusterModel.id == cluster_id
            )
            cluster = session.scalar(cluster_stmt)
            if cluster is not None:
                session.delete(cluster)
            session.commit()

    def delete_clusters(self, cluster_ids: Sequence[int]) -> None:
        with Session(self._engine) as session:
            for chunk in _chunked(cluster_ids):
                session.execute(
                    sa_delete(FaceClusterMembershipModel).where(
                        FaceClusterMembershipModel.cluster_id.in_(chunk)
                    )
                )
                session.execute(
                    sa_delete(IdentityClusterModel).where(
                        IdentityClusterModel.id.in_(chunk)
                    )
                )
            session.commit()

    def delete_all_clusters(self) -> None:
        """Wipe every cluster and membership at once."""
        with Session(self._engine) as session:
            session.execute(sa_delete(FaceClusterMembershipModel))
            session.execute(sa_delete(IdentityClusterModel))
            session.commit()


class SqlAlchemySettingsRepository:
    """Settings repository implementation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_all(self) -> dict[str, str]:
        with Session(self._engine) as session:
            stmt = select(AppSettingModel)
            rows = list(session.scalars(stmt))
            return {row.key: row.value for row in rows}

    def upsert_many(self, values: dict[str, str]) -> None:
        with Session(self._engine) as session:
            now = datetime.now(tz=UTC)
            for key, value in values.items():
                stmt = select(AppSettingModel).where(AppSettingModel.key == key)
                row = session.scalar(stmt)
                if row is None:
                    session.add(AppSettingModel(key=key, value=value, updated_at=now))
                    continue
                row.value = value
                row.updated_at = now
            session.commit()
