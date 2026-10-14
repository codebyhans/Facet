from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

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
    from photo_app.domain.value_objects import AlbumQuery


class ImageRepository(Protocol):
    """Persistence port for images."""

    def add_many(self, images: Sequence[Image]) -> None:
        """Insert multiple images."""

    def exists_by_path(self, file_path: str) -> bool:
        """Check if image path exists."""

    def get_by_path(self, file_path: str) -> Image | None:
        """Fetch single image by absolute file path."""

    def get_by_id(self, image_id: int) -> Image | None:
        """Fetch single image by ID."""

    def list_unprocessed_for_faces(self, limit: int) -> list[Image]:
        """Return images without face rows."""

    def list_all(self) -> list[Image]:
        """Return all images."""

    def list_all_summary(self) -> list[ImageSummary]:
        """Return a lightweight (id, file_path, width, height, capture_date) projection of every image, for grouping/display use cases that don't need the full row."""

    def list_paginated(self, *, offset: int, limit: int) -> list[Image]:
        """Return paginated images to avoid loading all into memory."""

    def list_by_filters(
        self,
        query: AlbumQuery,
        *,
        narrow_by: AlbumQuery | None = None,
        offset: int,
        limit: int,
    ) -> list[Image]:
        """Query one page of images matching `query` (see list_ids_by_filters)."""

    def list_ids_by_filters(
        self,
        query: AlbumQuery,
        *,
        narrow_by: AlbumQuery | None = None,
    ) -> list[int]:
        """Query image IDs by filter criteria with stable ordering.

        `narrow_by` is a second query that must also match, so a view filter
        can only ever shrink what `query` selects.
        """

    def count_by_filters(
        self,
        query: AlbumQuery,
        *,
        narrow_by: AlbumQuery | None = None,
    ) -> int:
        """Count images matching the same criteria as list_ids_by_filters."""

    def list_by_ids(self, image_ids: Sequence[int]) -> list[Image]:
        """Load images by explicit ID order."""

    def update_face_count(self, image_id: int, count: int) -> None:
        """Update the face_count for an image."""

    def update_flag(self, image_id: int, flag: str | None) -> None:
        """Update the flag for an image."""

    def list_all_paths(self) -> list[str]:
        """Return all indexed file paths in a single query."""

    def list_without_real_timestamp(self) -> list[Image]:
        """Return images with no capture_date, or a capture_date whose time is exactly midnight (folder-date fallback)."""

    def update_capture_date(self, image_id: int, capture_date: datetime) -> None:
        """Set capture_date (and year/month) for an image."""

    def delete_by_paths(self, paths: list[str]) -> int:
        """Delete images and all dependent rows for the given file paths."""


class FaceRepository(Protocol):
    """Persistence port for faces."""

    def add_many(self, faces: Sequence[Face]) -> None:
        """Insert face rows."""

    def list_all(self) -> list[Face]:
        """Load all faces."""

    def list_by_image(self, image_id: int) -> list[Face]:
        """Load faces for one image."""

    def list_by_person(self, person_id: int) -> list[Face]:
        """Load non-excluded faces assigned to one person."""

    def get(self, face_id: int) -> Face | None:
        """Load a single face by ID."""

    def get_many(self, face_ids: Sequence[int]) -> list[Face]:
        """Load multiple faces by ID in a single query."""

    def list_all_active(self) -> list[Face]:
        """Load non-excluded faces."""

    def list_active_stack_rows(self) -> list[FaceStackRow]:
        """Load a lightweight (id, image_id, person_id, bbox) projection of non-excluded faces, without the embedding BLOB, for stack grouping."""

    def list_without_cluster_membership(self, limit: int | None = None) -> list[Face]:
        """Load non-excluded faces that are not linked to an identity cluster."""

    def assign_person_auto(self, face_ids: Sequence[int], person_id: int) -> None:
        """Set person_id for non-manual, non-excluded faces."""

    def assign_person_manual(self, face_ids: Sequence[int], person_id: int) -> None:
        """Set person_id and mark faces as manual assignments."""

    def reassign_person(self, old_person_id: int, new_person_id: int) -> None:
        """Repoint every face from one person record to another (incl. manual tags)."""

    def exclude(self, face_ids: Sequence[int]) -> None:
        """Mark faces excluded from review and future auto-clustering."""

    def list_excluded(self, limit: int | None = None) -> list[Face]:
        """Load excluded faces (optionally capped), for a "restore" browser."""

    def count_excluded(self) -> int:
        """Count all currently-excluded faces, regardless of `limit`."""

    def restore(self, face_ids: Sequence[int]) -> None:
        """Undo `exclude()`: face re-enters normal auto-clustering on the
        next indexing run. Never deletes anything — always reversible."""

    def delete_by_image(self, image_id: int) -> None:
        """Delete all faces for one image, including excluded/manual rows."""

    def unassign_faces(self, face_ids: Sequence[int]) -> None:
        """Clear the person label (and manual flag) of the given faces."""

    def clear_all_person_assignments(self) -> None:
        """Unassign every face (including manually-tagged ones) for a full reset."""


class PersonRepository(Protocol):
    """Persistence port for persons."""

    def create(self, person: Person) -> Person:
        """Create a person and return it with id."""

    def get(self, person_id: int) -> Person | None:
        """Fetch person by ID."""

    def get_by_name(self, name: str) -> Person | None:
        """Fetch person by display name (case-insensitive)."""

    def list_all(self) -> list[Person]:
        """Fetch all persons."""

    def update_name(self, person_id: int, name: str) -> None:
        """Update person display name."""

    def update_birth_date(self, person_id: int, birth_date: date | None) -> None:
        """Update person birth date (None clears it)."""

    def delete(self, person_id: int) -> None:
        """Delete a person record by ID."""

    def delete_many(self, person_ids: Sequence[int]) -> None:
        """Delete several person records by ID."""

    def delete_all(self) -> None:
        """Delete every person record, for a full identity reset."""


class IdentityClusterRepository(Protocol):
    """Persistence port for temporal identity clusters."""

    def create_cluster(
        self,
        canonical_embedding: bytes,
        *,
        created_at: datetime,
    ) -> IdentityCluster:
        """Create new cluster with initial centroid."""

    def list_clusters(self) -> list[IdentityCluster]:
        """Load all clusters."""

    def get_cluster(self, cluster_id: int) -> IdentityCluster | None:
        """Load one cluster."""

    def get_clusters(self, cluster_ids: Sequence[int]) -> dict[int, IdentityCluster]:
        """Load multiple clusters by ID in a single query."""

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
        """Persist aggregate cluster statistics."""

    def upsert_membership(
        self,
        *,
        face_id: int,
        cluster_id: int,
        confidence: float,
        assigned_at: datetime,
    ) -> None:
        """Create or update face-cluster membership."""

    def list_memberships(self, cluster_id: int) -> list[FaceClusterMembership]:
        """List cluster members."""

    def get_membership(self, face_id: int) -> FaceClusterMembership | None:
        """Fetch membership for one face."""

    def reassign_cluster_memberships(
        self, source_cluster_id: int, target_cluster_id: int
    ) -> None:
        """Move all memberships from source cluster to target cluster."""

    def delete_cluster(self, cluster_id: int) -> None:
        """Delete cluster and its memberships."""

    def delete_clusters(self, cluster_ids: Sequence[int]) -> None:
        """Delete several clusters and their memberships in bulk."""

    def delete_all_clusters(self) -> None:
        """Wipe every cluster and membership at once."""

    def set_cluster_person(self, cluster_id: int, person_id: int | None) -> None:
        """Set (or clear, with None) the name tag applied to one cluster.

        Never touches the cluster's embedding or membership — tagging only ever labels a cluster, it doesn't merge it
        with anything. Many different clusters may carry the same
        `person_id`.
        """

    def list_clusters_for_person(self, person_id: int) -> list[IdentityCluster]:
        """Return every cluster currently tagged with this person."""


class AlbumRepository(Protocol):
    """Persistence port for albums."""

    def create(self, album: Album) -> Album:
        """Insert album."""

    def get(self, album_id: int) -> Album | None:
        """Fetch album by id."""

    def list_all(self) -> list[Album]:
        """Fetch all albums."""

    def update_name(self, album_id: int, name: str) -> Album | None:
        """Rename one album and return updated model."""

    def update_query(self, album_id: int, query: AlbumQuery) -> Album | None:
        """Update one album query and return updated model."""

    def delete(self, album_id: int) -> bool:
        """Delete one album by id."""


class CollectionRepository(Protocol):
    """Persistence port for hand-picked image collections."""

    def create(self, name: str, *, created_at: datetime) -> Collection:
        """Insert a collection."""

    def get(self, collection_id: int) -> Collection | None:
        """Fetch one collection, with its image count."""

    def get_by_name(self, name: str) -> Collection | None:
        """Fetch one collection by name (case-insensitive)."""

    def list_all(self) -> list[Collection]:
        """Fetch all collections with their image counts, ordered by name."""

    def update_name(self, collection_id: int, name: str) -> Collection | None:
        """Rename one collection and return the updated model."""

    def delete(self, collection_id: int) -> bool:
        """Delete one collection and its memberships (images are untouched)."""

    def add_images(
        self, collection_id: int, image_ids: Sequence[int], *, added_at: datetime
    ) -> int:
        """Add images to a collection; returns how many were newly added."""

    def remove_images(self, collection_id: int, image_ids: Sequence[int]) -> int:
        """Remove images from a collection; returns how many were removed."""

    def list_containing(self, image_ids: Sequence[int]) -> list[Collection]:
        """Return the collections that hold at least one of the given images."""


class AlbumQueryParser(Protocol):
    """Port for JSON-to-query parsing."""

    def parse(self, raw_definition: dict[str, object]) -> AlbumQuery:
        """Return typed album query."""


class SettingsRepository(Protocol):
    """Persistence port for app-level runtime settings."""

    def get_all(self) -> dict[str, str]:
        """Return all persisted key-value settings."""

    def upsert_many(self, values: dict[str, str]) -> None:
        """Insert or update settings in one operation."""
