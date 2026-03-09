from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import and_, exists, func, nulls_last, select

from photo_app.domain.models import (
    Album,
    ClusterEmbedding,
    Face,
    FaceClusterMembership,
    IdentityCluster,
    Image,
    Person,
)
from photo_app.domain.value_objects import AlbumQuery, BoundingBox
from photo_app.infrastructure.sqlalchemy_models import (
    AlbumModel,
    AppSettingModel,
    ClusterEmbeddingModel,
    FaceModel,
    FaceClusterMembershipModel,
    IdentityClusterModel,
    ImageModel,
    ImageTagModel,
    PersonModel,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session


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


def _to_album(entity: AlbumModel) -> Album:
    date_from_raw = cast("str | None", entity.query_definition.get("date_from"))
    date_to_raw = cast("str | None", entity.query_definition.get("date_to"))
    
    # Parse new fields with defaults
    raw_tag_names = entity.query_definition.get("tag_names", [])
    tag_names: tuple[str, ...] = ()
    if isinstance(raw_tag_names, list):
        tag_names = tuple(str(t) for t in raw_tag_names if isinstance(t, str))
    
    raw_camera_models = entity.query_definition.get("camera_models", [])
    camera_models: tuple[str, ...] = ()
    if isinstance(raw_camera_models, list):
        camera_models = tuple(str(m) for m in raw_camera_models if isinstance(m, str))
    
    rating_min = entity.query_definition.get("rating_min")
    quality_min = entity.query_definition.get("quality_min")
    location_name = entity.query_definition.get("location_name")
    gps_radius_km = entity.query_definition.get("gps_radius_km")
    
    query = AlbumQuery(
        person_ids=tuple(int(i) for i in entity.query_definition.get("person_ids", [])),
        cluster_ids=tuple(
            int(i) for i in entity.query_definition.get("cluster_ids", [])
        ),
        date_from=date.fromisoformat(date_from_raw) if date_from_raw else None,
        date_to=date.fromisoformat(date_to_raw) if date_to_raw else None,
        tag_names=tag_names,
        rating_min=int(rating_min) if rating_min is not None else None,
        quality_min=float(quality_min) if quality_min is not None else None,
        camera_models=camera_models,
        location_name=str(location_name) if location_name else None,
        gps_radius_km=float(gps_radius_km) if gps_radius_km is not None else None,
    )
    return Album(
        id=entity.id,
        name=entity.name,
        query_definition=query,
        query_version=entity.query_version,
        created_at=entity.created_at,
    )


def _to_identity_cluster(entity: IdentityClusterModel) -> IdentityCluster:
    return IdentityCluster(
        id=entity.id,
        canonical_embedding=entity.canonical_embedding,
        face_count=entity.face_count,
        variance=entity.variance,
        flagged_for_review=entity.flagged_for_review,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def _to_membership(entity: FaceClusterMembershipModel) -> FaceClusterMembership:
    return FaceClusterMembership(
        face_id=entity.face_id,
        cluster_id=entity.cluster_id,
        confidence=entity.confidence,
        assigned_at=entity.assigned_at,
    )


def _to_cluster_embedding(entity: ClusterEmbeddingModel) -> ClusterEmbedding:
    return ClusterEmbedding(
        id=entity.id,
        cluster_id=entity.cluster_id,
        time_period=entity.time_period,
        embedding=entity.embedding,
        sample_count=entity.sample_count,
        updated_at=entity.updated_at,
    )


class SqlAlchemyImageRepository:
    """Image repository implementation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, images: Sequence[Image]) -> None:
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
            for image in images
        ]
        self._session.bulk_save_objects(rows)

    def exists_by_path(self, file_path: str) -> bool:
        stmt = select(exists().where(ImageModel.file_path == file_path))
        return bool(self._session.scalar(stmt))

    def get_by_path(self, file_path: str) -> Image | None:
        stmt = select(ImageModel).where(ImageModel.file_path == file_path)
        row = self._session.scalar(stmt)
        return None if row is None else _to_image(row)

    def get_by_id(self, image_id: int) -> Image | None:
        """Get a single image by ID."""
        stmt = select(ImageModel).where(ImageModel.id == image_id)
        row = self._session.scalar(stmt)
        return None if row is None else _to_image(row)

    def list_unprocessed_for_faces(self, limit: int) -> list[Image]:
        subq = select(FaceModel.image_id)
        stmt = select(ImageModel).where(~ImageModel.id.in_(subq)).limit(limit)
        return [_to_image(row) for row in self._session.scalars(stmt)]

    def list_all(self) -> list[Image]:
        stmt = select(ImageModel)
        return [_to_image(row) for row in self._session.scalars(stmt)]

    def list_by_filters(
        self,
        *,
        person_ids: Sequence[int],
        date_from: date | None,
        date_to: date | None,
        cluster_ids: Sequence[int] = (),
        tag_names: Sequence[str] = (),
        rating_min: int | None = None,
        quality_min: float | None = None,
        camera_models: Sequence[str] = (),
        offset: int,
        limit: int,
    ) -> list[Image]:
        ordered_ids = self.list_ids_by_filters(
            person_ids=person_ids,
            cluster_ids=cluster_ids,
            date_from=date_from,
            date_to=date_to,
            tag_names=tag_names,
            rating_min=rating_min,
            quality_min=quality_min,
            camera_models=camera_models,
        )
        page_ids = ordered_ids[offset : offset + limit]
        return self.list_by_ids(page_ids)

    def list_ids_by_filters(
        self,
        *,
        person_ids: Sequence[int],
        date_from: date | None,
        date_to: date | None,
        cluster_ids: Sequence[int] = (),
        tag_names: Sequence[str] = (),
        rating_min: int | None = None,
        quality_min: float | None = None,
        camera_models: Sequence[str] = (),
    ) -> list[int]:
        stmt = select(ImageModel.id)
        
        # Filter by people
        if person_ids:
            person_clause = exists(
                select(FaceModel.id).where(
                    FaceModel.image_id == ImageModel.id,
                    FaceModel.excluded.is_(False),
                    FaceModel.person_id.in_(person_ids),
                )
            )
            stmt = stmt.where(person_clause)
        
        # Filter by clusters
        if cluster_ids:
            cluster_clause = exists(
                select(FaceModel.id)
                .join(
                    FaceClusterMembershipModel,
                    FaceClusterMembershipModel.face_id == FaceModel.id,
                )
                .where(
                    FaceModel.image_id == ImageModel.id,
                    FaceModel.excluded.is_(False),
                    FaceClusterMembershipModel.cluster_id.in_(cluster_ids),
                )
            )
            stmt = stmt.where(cluster_clause)
        
        # Filter by tags
        if tag_names:
            tag_clause = exists(
                select(ImageTagModel.id).where(
                    ImageTagModel.image_id == ImageModel.id,
                    ImageTagModel.tag_name.in_(tag_names),
                )
            )
            stmt = stmt.where(tag_clause)
        
        # Filter by rating
        if rating_min is not None:
            stmt = stmt.where(ImageModel.rating >= rating_min)
        
        # Filter by quality score
        if quality_min is not None:
            stmt = stmt.where(ImageModel.quality_score >= quality_min)
        
        # Filter by camera model
        if camera_models:
            stmt = stmt.where(ImageModel.camera_model.in_(camera_models))
        
        # Filter by date range
        clauses = []
        if date_from is not None:
            clauses.append(ImageModel.capture_date >= date_from)
        if date_to is not None:
            clauses.append(ImageModel.capture_date <= date_to)
        if clauses:
            stmt = stmt.where(and_(*clauses))
        
        stmt = stmt.order_by(
            nulls_last(ImageModel.capture_date.asc()),
            ImageModel.id.asc(),
        )
        rows = self._session.scalars(stmt).unique().all()
        return [int(row) for row in rows]

    def list_by_ids(self, image_ids: Sequence[int]) -> list[Image]:
        if not image_ids:
            return []
        stmt = select(ImageModel).where(ImageModel.id.in_(image_ids))
        by_id = {row.id: _to_image(row) for row in self._session.scalars(stmt)}
        ordered: list[Image] = []
        for image_id in image_ids:
            image = by_id.get(image_id)
            if image is not None:
                ordered.append(image)
        return ordered


class SqlAlchemyFaceRepository:
    """Face repository implementation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, faces: Sequence[Face]) -> None:
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
        self._session.bulk_save_objects(rows)

    def list_all(self) -> list[Face]:
        stmt = select(FaceModel)
        return [_to_face(row) for row in self._session.scalars(stmt)]

    def list_all_active(self) -> list[Face]:
        stmt = select(FaceModel).where(FaceModel.excluded.is_(False))
        return [_to_face(row) for row in self._session.scalars(stmt)]

    def list_without_cluster_membership(self, limit: int | None = None) -> list[Face]:
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
        return [_to_face(row) for row in self._session.scalars(stmt)]

    def list_by_image(self, image_id: int) -> list[Face]:
        stmt = select(FaceModel).where(
            FaceModel.image_id == image_id,
            FaceModel.excluded.is_(False),
        )
        return [_to_face(row) for row in self._session.scalars(stmt)]

    def get(self, face_id: int) -> Face | None:
        """Get a single face by ID."""
        stmt = select(FaceModel).where(FaceModel.id == face_id)
        row = self._session.scalar(stmt)
        return None if row is None else _to_face(row)

    def assign_person_auto(self, face_ids: Sequence[int], person_id: int) -> None:
        if not face_ids:
            return
        stmt = select(FaceModel).where(FaceModel.id.in_(face_ids))
        for face in self._session.scalars(stmt):
            if face.excluded or face.manual_assignment:
                continue
            face.person_id = person_id

    def assign_person_manual(self, face_ids: Sequence[int], person_id: int) -> None:
        if not face_ids:
            return
        stmt = select(FaceModel).where(FaceModel.id.in_(face_ids))
        for face in self._session.scalars(stmt):
            if face.excluded:
                continue
            face.person_id = person_id
            face.manual_assignment = True

    def exclude(self, face_ids: Sequence[int]) -> None:
        if not face_ids:
            return
        stmt = select(FaceModel).where(FaceModel.id.in_(face_ids))
        for face in self._session.scalars(stmt):
            face.excluded = True
            face.person_id = None
            face.manual_assignment = True

    def delete_by_image(self, image_id: int) -> None:
        stmt = select(FaceModel).where(FaceModel.image_id == image_id)
        for face in self._session.scalars(stmt):
            membership = self._session.scalar(
                select(FaceClusterMembershipModel).where(
                    FaceClusterMembershipModel.face_id == face.id
                )
            )
            if membership is not None:
                self._session.delete(membership)
            self._session.delete(face)


class SqlAlchemyPersonRepository:
    """Person repository implementation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, person: Person) -> Person:
        row = PersonModel(
            name=person.name,
            created_at=person.created_at,
            birth_date=person.birth_date,
            identity_cluster_id=person.identity_cluster_id,
        )
        self._session.add(row)
        self._session.flush()
        return Person(
            id=row.id,
            name=row.name,
            created_at=row.created_at,
            birth_date=row.birth_date,
            identity_cluster_id=row.identity_cluster_id,
        )

    def get(self, person_id: int) -> Person | None:
        stmt = select(PersonModel).where(PersonModel.id == person_id)
        row = self._session.scalar(stmt)
        if row is None:
            return None
        return Person(
            id=row.id,
            name=row.name,
            created_at=row.created_at,
            birth_date=row.birth_date,
            identity_cluster_id=row.identity_cluster_id,
        )

    def get_by_name(self, name: str) -> Person | None:
        cleaned = name.strip()
        if not cleaned:
            return None
        stmt = select(PersonModel).where(
            func.lower(PersonModel.name) == cleaned.lower()
        )
        row = self._session.scalar(stmt)
        if row is None:
            return None
        return Person(
            id=row.id,
            name=row.name,
            created_at=row.created_at,
            birth_date=row.birth_date,
            identity_cluster_id=row.identity_cluster_id,
        )

    def update_name(self, person_id: int, name: str) -> None:
        stmt = select(PersonModel).where(PersonModel.id == person_id)
        row = self._session.scalar(stmt)
        if row is not None:
            row.name = name

    def find_by_cluster_id(self, cluster_id: int) -> Person | None:
        stmt = select(PersonModel).where(PersonModel.identity_cluster_id == cluster_id)
        row = self._session.scalar(stmt)
        if row is None:
            return None
        return Person(
            id=row.id,
            name=row.name,
            created_at=row.created_at,
            birth_date=row.birth_date,
            identity_cluster_id=row.identity_cluster_id,
        )

    def bind_cluster(self, person_id: int, cluster_id: int) -> None:
        stmt = select(PersonModel).where(PersonModel.id == person_id)
        row = self._session.scalar(stmt)
        if row is not None:
            row.identity_cluster_id = cluster_id


class SqlAlchemyAlbumRepository:
    """Album repository implementation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, album: Album) -> Album:
        query_payload: dict[str, object] = {
            "person_ids": list(album.query_definition.person_ids),
            "cluster_ids": list(album.query_definition.cluster_ids),
            "date_from": (
                album.query_definition.date_from.isoformat()
                if album.query_definition.date_from
                else None
            ),
            "date_to": (
                album.query_definition.date_to.isoformat()
                if album.query_definition.date_to
                else None
            ),
            "tag_names": list(album.query_definition.tag_names),
            "rating_min": album.query_definition.rating_min,
            "quality_min": album.query_definition.quality_min,
            "camera_models": list(album.query_definition.camera_models),
            "location_name": album.query_definition.location_name,
            "gps_radius_km": album.query_definition.gps_radius_km,
        }
        row = AlbumModel(
            name=album.name,
            query_definition=query_payload,
            query_version=album.query_version,
            created_at=album.created_at,
        )
        self._session.add(row)
        self._session.flush()
        return Album(
            id=row.id,
            name=row.name,
            query_definition=album.query_definition,
            query_version=row.query_version,
            created_at=row.created_at,
        )

    def get(self, album_id: int) -> Album | None:
        stmt = select(AlbumModel).where(AlbumModel.id == album_id)
        row = self._session.scalar(stmt)
        return None if row is None else _to_album(row)

    def list_all(self) -> list[Album]:
        stmt = select(AlbumModel).order_by(AlbumModel.name.asc())
        return [_to_album(row) for row in self._session.scalars(stmt)]

    def update_name(self, album_id: int, name: str) -> Album | None:
        stmt = select(AlbumModel).where(AlbumModel.id == album_id)
        row = self._session.scalar(stmt)
        if row is None:
            return None
        row.name = name
        self._session.flush()
        return _to_album(row)

    def update_query(self, album_id: int, query: AlbumQuery) -> Album | None:
        stmt = select(AlbumModel).where(AlbumModel.id == album_id)
        row = self._session.scalar(stmt)
        if row is None:
            return None
        row.query_definition = {
            "person_ids": list(query.person_ids),
            "cluster_ids": list(query.cluster_ids),
            "date_from": query.date_from.isoformat() if query.date_from else None,
            "date_to": query.date_to.isoformat() if query.date_to else None,
            "tag_names": list(query.tag_names),
            "rating_min": query.rating_min,
            "quality_min": query.quality_min,
            "camera_models": list(query.camera_models),
            "location_name": query.location_name,
            "gps_radius_km": query.gps_radius_km,
        }
        row.query_version += 1
        self._session.flush()
        return _to_album(row)

    def delete(self, album_id: int) -> bool:
        stmt = select(AlbumModel).where(AlbumModel.id == album_id)
        row = self._session.scalar(stmt)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


class SqlAlchemyIdentityClusterRepository:
    """Identity cluster repository implementation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_cluster(
        self,
        canonical_embedding: bytes,
        *,
        created_at: datetime,
    ) -> IdentityCluster:
        row = IdentityClusterModel(
            canonical_embedding=canonical_embedding,
            face_count=0,
            variance=0.0,
            flagged_for_review=False,
            created_at=created_at,
            updated_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return _to_identity_cluster(row)

    def list_clusters(self) -> list[IdentityCluster]:
        stmt = select(IdentityClusterModel)
        return [_to_identity_cluster(row) for row in self._session.scalars(stmt)]

    def get_cluster(self, cluster_id: int) -> IdentityCluster | None:
        stmt = select(IdentityClusterModel).where(IdentityClusterModel.id == cluster_id)
        row = self._session.scalar(stmt)
        return None if row is None else _to_identity_cluster(row)

    def update_cluster_state(
        self,
        cluster_id: int,
        *,
        canonical_embedding: bytes,
        face_count: int,
        variance: float,
        flagged_for_review: bool,
        updated_at: datetime,
    ) -> None:
        stmt = select(IdentityClusterModel).where(IdentityClusterModel.id == cluster_id)
        row = self._session.scalar(stmt)
        if row is None:
            return
        row.canonical_embedding = canonical_embedding
        row.face_count = face_count
        row.variance = variance
        row.flagged_for_review = flagged_for_review
        row.updated_at = updated_at

    def upsert_membership(
        self,
        *,
        face_id: int,
        cluster_id: int,
        confidence: float,
        assigned_at: datetime,
    ) -> None:
        stmt = select(FaceClusterMembershipModel).where(
            FaceClusterMembershipModel.face_id == face_id
        )
        row = self._session.scalar(stmt)
        if row is None:
            self._session.add(
                FaceClusterMembershipModel(
                    face_id=face_id,
                    cluster_id=cluster_id,
                    confidence=confidence,
                    assigned_at=assigned_at,
                )
            )
            return
        row.cluster_id = cluster_id
        row.confidence = confidence
        row.assigned_at = assigned_at

    def list_memberships(self, cluster_id: int) -> list[FaceClusterMembership]:
        stmt = select(FaceClusterMembershipModel).where(
            FaceClusterMembershipModel.cluster_id == cluster_id
        )
        return [_to_membership(row) for row in self._session.scalars(stmt)]

    def get_membership(self, face_id: int) -> FaceClusterMembership | None:
        stmt = select(FaceClusterMembershipModel).where(
            FaceClusterMembershipModel.face_id == face_id
        )
        row = self._session.scalar(stmt)
        return None if row is None else _to_membership(row)

    def reassign_cluster_memberships(
        self,
        source_cluster_id: int,
        target_cluster_id: int,
    ) -> None:
        stmt = select(FaceClusterMembershipModel).where(
            FaceClusterMembershipModel.cluster_id == source_cluster_id
        )
        for membership in self._session.scalars(stmt):
            membership.cluster_id = target_cluster_id

    def delete_cluster(self, cluster_id: int) -> None:
        temporal_stmt = select(ClusterEmbeddingModel).where(
            ClusterEmbeddingModel.cluster_id == cluster_id
        )
        for row in self._session.scalars(temporal_stmt):
            self._session.delete(row)

        membership_stmt = select(FaceClusterMembershipModel).where(
            FaceClusterMembershipModel.cluster_id == cluster_id
        )
        for row in self._session.scalars(membership_stmt):
            self._session.delete(row)

        cluster_stmt = select(IdentityClusterModel).where(IdentityClusterModel.id == cluster_id)
        cluster = self._session.scalar(cluster_stmt)
        if cluster is not None:
            self._session.delete(cluster)

    def list_temporal_embeddings(self, cluster_id: int) -> list[ClusterEmbedding]:
        stmt = select(ClusterEmbeddingModel).where(
            ClusterEmbeddingModel.cluster_id == cluster_id
        )
        return [_to_cluster_embedding(row) for row in self._session.scalars(stmt)]

    def upsert_temporal_embedding(
        self,
        *,
        cluster_id: int,
        time_period: str,
        embedding: bytes,
        sample_count: int,
        updated_at: datetime,
    ) -> None:
        stmt = select(ClusterEmbeddingModel).where(
            ClusterEmbeddingModel.cluster_id == cluster_id,
            ClusterEmbeddingModel.time_period == time_period,
        )
        row = self._session.scalar(stmt)
        if row is None:
            self._session.add(
                ClusterEmbeddingModel(
                    cluster_id=cluster_id,
                    time_period=time_period,
                    embedding=embedding,
                    sample_count=sample_count,
                    updated_at=updated_at,
                )
            )
            return
        row.embedding = embedding
        row.sample_count = sample_count
        row.updated_at = updated_at


class SqlAlchemySettingsRepository:
    """Settings repository implementation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_all(self) -> dict[str, str]:
        stmt = select(AppSettingModel)
        rows = list(self._session.scalars(stmt))
        return {row.key: row.value for row in rows}

    def upsert_many(self, values: dict[str, str]) -> None:
        now = datetime.now(tz=UTC)
        for key, value in values.items():
            stmt = select(AppSettingModel).where(AppSettingModel.key == key)
            row = self._session.scalar(stmt)
            if row is None:
                self._session.add(AppSettingModel(key=key, value=value, updated_at=now))
                continue
            row.value = value
            row.updated_at = now
