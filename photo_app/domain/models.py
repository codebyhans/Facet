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
class ImageSummary:
    """Lightweight image projection for grouping/display.

    Omits large optional text columns (user_notes, location_name, etc.) that
    stack-grouping and cover-selection never touch, so loading every image
    for that purpose doesn't drag megabytes of unused text through SQLite.
    """

    id: int
    file_path: str
    width: int
    height: int
    capture_date: datetime | None


@dataclass(frozen=True)
class FaceStackRow:
    """Lightweight active-face projection for stack grouping.

    Omits the embedding BLOB (a few KB per face) that stack grouping and
    cover selection never touch — the dominant cost of loading every face
    in a large library just to group them by person.
    """

    id: int
    image_id: int
    person_id: int | None
    bbox: BoundingBox


@dataclass(frozen=True)
class Person:
    """A name tag: a name plus (usually) a fixed, known birth date.

    Deliberately not tied to any one cluster — `IdentityCluster.person_id`
    is the other direction of this relationship, and many clusters can
    carry the same tag (e.g. a child's separate baby/toddler/kid clusters),
    since tagging never merges cluster state. See `tag_cluster` in
    `TemporalIdentityClusterService`.
    """

    id: int | None
    name: str | None
    created_at: datetime
    birth_date: date | None


@dataclass(frozen=True)
class Album:
    """Album entity."""

    id: int | None
    name: str
    query_definition: AlbumQuery
    query_version: int
    created_at: datetime


@dataclass(frozen=True)
class Collection:
    """A hand-picked set of images (e.g. the picks for one year's photo book).

    Unlike an album, which is a saved query, membership is assigned
    explicitly. An image may belong to any number of collections.
    """

    id: int | None
    name: str
    created_at: datetime
    image_count: int = 0


@dataclass(frozen=True)
class IdentityCluster:
    """A machine-formed group of visually similar faces.

    `person_id` is the name tag applied to this cluster, if any (nullable,
    not unique — several clusters may carry the same tag). Tagging only
    ever sets this field plus `Face.person_id` on the members; it never
    touches `canonical_embedding` or membership, so
    a cluster's own ML state is unaffected by which tag (if any) it wears.
    """

    id: int | None
    canonical_embedding: bytes
    face_count: int
    variance: float
    flagged_for_review: bool
    created_at: datetime
    updated_at: datetime
    person_id: int | None = None


@dataclass(frozen=True)
class FaceClusterMembership:
    """Link one detected face to one identity cluster."""

    face_id: int
    cluster_id: int
    confidence: float
    assigned_at: datetime


@dataclass(frozen=True)
class ImageTag:
    """User-defined tag for an image."""

    id: int | None
    image_id: int
    tag_name: str
    created_at: datetime
