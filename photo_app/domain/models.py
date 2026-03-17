from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime

    from photo_app.domain.value_objects import AlbumQuery, BoundingBox


@dataclass(frozen=True)
class Image:
    """Image entity."""

    id: int | None
    file_path: str
    capture_date: datetime | None
    year: int | None
    month: int | None
    hash: str
    width: int
    height: int
    indexed_at: datetime
    updated_at: datetime | None = None
    rating: int | None = None
    quality_score: float | None = None
    is_favorite: bool = False
    user_notes: str | None = None
    camera_model: str | None = None
    gps_latitude: float | None = None
    gps_longitude: float | None = None
    location_name: str | None = None
    flag: str | None = None


@dataclass(frozen=True)
class Face:
    """Face entity."""

    id: int | None
    image_id: int
    bbox: BoundingBox
    embedding: bytes
    person_id: int | None
    confidence_score: float | None = None
    manual_assignment: bool = False
    excluded: bool = False


@dataclass(frozen=True)
class Person:
    """Person entity."""

    id: int | None
    name: str | None
    created_at: datetime
    birth_date: date | None
    identity_cluster_id: int | None = None


@dataclass(frozen=True)
class Album:
    """Album entity."""

    id: int | None
    name: str
    query_definition: AlbumQuery
    query_version: int
    created_at: datetime


@dataclass(frozen=True)
class IdentityCluster:
    """Temporal identity cluster for one person across age stages."""

    id: int | None
    canonical_embedding: bytes
    face_count: int
    variance: float
    flagged_for_review: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class FaceClusterMembership:
    """Link one detected face to one identity cluster."""

    face_id: int
    cluster_id: int
    confidence: float
    assigned_at: datetime


@dataclass(frozen=True)
class ClusterEmbedding:
    """Representative embedding for one cluster life-stage bucket."""

    id: int | None
    cluster_id: int
    time_period: str
    embedding: bytes
    sample_count: int
    updated_at: datetime


@dataclass(frozen=True)
class ImageTag:
    """User-defined tag for an image."""

    id: int | None
    image_id: int
    tag_name: str
    created_at: datetime
