from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

_RUNTIME_DATE = date
_RUNTIME_DATETIME = datetime


class Base(DeclarativeBase):
    """Base declarative model."""


class ImageModel(Base):
    """Image metadata record."""

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    capture_date: Mapped[date | None] = mapped_column(Date, index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    month: Mapped[int | None] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    indexed_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    camera_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gps_latitude: Mapped[float | None] = mapped_column(nullable=True)
    gps_longitude: Mapped[float | None] = mapped_column(nullable=True)
    location_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    faces: Mapped[list[FaceModel]] = relationship(back_populates="image")
    tags: Mapped[list[ImageTagModel]] = relationship(back_populates="image")


class PersonModel(Base):
    """Detected person cluster."""

    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    identity_cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("identity_clusters.id"),
        nullable=True,
        unique=True,
        index=True,
    )

    faces: Mapped[list[FaceModel]] = relationship(back_populates="person")
    identity_cluster: Mapped[IdentityClusterModel | None] = relationship(
        back_populates="person",
    )


class FaceModel(Base):
    """Face crop and embedding record."""

    __tablename__ = "faces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id"), index=True)
    bbox_x: Mapped[int] = mapped_column(Integer)
    bbox_y: Mapped[int] = mapped_column(Integer)
    bbox_w: Mapped[int] = mapped_column(Integer)
    bbox_h: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    confidence_score: Mapped[float | None] = mapped_column(nullable=True)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id"), nullable=True, index=True
    )
    manual_assignment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    image: Mapped[ImageModel] = relationship(back_populates="faces")
    person: Mapped[PersonModel | None] = relationship(back_populates="faces")
    cluster_membership: Mapped[FaceClusterMembershipModel | None] = relationship(
        back_populates="face",
        uselist=False,
    )


class IdentityClusterModel(Base):
    """Long-lived identity cluster for age-robust recognition."""

    __tablename__ = "identity_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    face_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    variance: Mapped[float] = mapped_column(nullable=False, default=0.0)
    flagged_for_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    person: Mapped[PersonModel | None] = relationship(back_populates="identity_cluster")
    memberships: Mapped[list[FaceClusterMembershipModel]] = relationship(
        back_populates="cluster",
    )
    temporal_embeddings: Mapped[list[ClusterEmbeddingModel]] = relationship(
        back_populates="cluster",
    )


class FaceClusterMembershipModel(Base):
    """Face-to-cluster assignment with matching confidence."""

    __tablename__ = "face_cluster_memberships"
    __table_args__ = (UniqueConstraint("face_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    face_id: Mapped[int] = mapped_column(
        ForeignKey("faces.id"),
        nullable=False,
        index=True,
    )
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("identity_clusters.id"),
        nullable=False,
        index=True,
    )
    confidence: Mapped[float] = mapped_column(nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    face: Mapped[FaceModel] = relationship(back_populates="cluster_membership")
    cluster: Mapped[IdentityClusterModel] = relationship(back_populates="memberships")


class ClusterEmbeddingModel(Base):
    """Per-life-stage cluster embedding profile."""

    __tablename__ = "cluster_embeddings"
    __table_args__ = (UniqueConstraint("cluster_id", "time_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("identity_clusters.id"),
        nullable=False,
        index=True,
    )
    time_period: Mapped[str] = mapped_column(String(32), nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    cluster: Mapped[IdentityClusterModel] = relationship(
        back_populates="temporal_embeddings",
    )


class ImageTagModel(Base):
    """User-defined tags for images."""

    __tablename__ = "image_tags"
    __table_args__ = (UniqueConstraint("image_id", "tag_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id"),
        nullable=False,
        index=True,
    )
    tag_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    image: Mapped[ImageModel] = relationship(back_populates="tags")


class AlbumModel(Base):
    """Virtual album query holder."""

    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    query_definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    query_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AppSettingModel(Base):
    """Key-value persisted application setting."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(String(2048))
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class ThumbnailTileModel(Base):
    """Tile mapping record for fast thumbnail lookup."""

    __tablename__ = "thumbnail_tiles"
    __table_args__ = (
        UniqueConstraint("image_id"),
        UniqueConstraint("tile_index", "position_in_tile"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tile_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    tile_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id"),
        nullable=False,
        index=True,
    )
    position_in_tile: Mapped[int] = mapped_column(Integer, nullable=False)


class AlbumQueryCacheModel(Base):
    """Persisted album query result cache."""

    __tablename__ = "album_query_cache"
    __table_args__ = (
        UniqueConstraint("album_id", "position"),
        UniqueConstraint("album_id", "image_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    album_id: Mapped[int] = mapped_column(
        ForeignKey("albums.id"),
        nullable=False,
        index=True,
    )
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id"), nullable=False)
    query_version: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
