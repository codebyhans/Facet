from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from photo_app.domain.models import (
        Album,
        ClusterEmbedding,
        Face,
        FaceClusterMembership,
        IdentityCluster,
        Image,
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

    def list_paginated(self, *, offset: int, limit: int) -> list[Image]:
        """Return paginated images to avoid loading all into memory."""

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
        """Query images by filter criteria."""

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
        """Query image IDs by filter criteria with stable ordering."""

    def list_by_ids(self, image_ids: Sequence[int]) -> list[Image]:
        """Load images by explicit ID order."""

    def update_face_count(self, image_id: int, count: int) -> None:
        """Update the face_count for an image."""

    def update_flag(self, image_id: int, flag: str | None) -> None:
        """Update the flag for an image."""


class FaceRepository(Protocol):
    """Persistence port for faces."""

    def add_many(self, faces: Sequence[Face]) -> None:
        """Insert face rows."""

    def list_all(self) -> list[Face]:
        """Load all faces."""

    def list_by_image(self, image_id: int) -> list[Face]:
        """Load faces for one image."""

    def get(self, face_id: int) -> Face | None:
        """Load a single face by ID."""

    def list_all_active(self) -> list[Face]:
        """Load non-excluded faces."""

    def list_without_cluster_membership(self, limit: int | None = None) -> list[Face]:
        """Load non-excluded faces that are not linked to an identity cluster."""

    def assign_person_auto(self, face_ids: Sequence[int], person_id: int) -> None:
        """Set person_id for non-manual, non-excluded faces."""

    def assign_person_manual(self, face_ids: Sequence[int], person_id: int) -> None:
        """Set person_id and mark faces as manual assignments."""

    def exclude(self, face_ids: Sequence[int]) -> None:
        """Mark faces excluded from review and future auto-clustering."""

    def delete_by_image(self, image_id: int) -> None:
        """Delete all faces for one image, including excluded/manual rows."""


class PersonRepository(Protocol):
    """Persistence port for persons."""

    def create(self, person: Person) -> Person:
        """Create a person and return it with id."""

    def get(self, person_id: int) -> Person | None:
        """Fetch person by ID."""

    def get_by_name(self, name: str) -> Person | None:
        """Fetch person by display name (case-insensitive)."""

    def update_name(self, person_id: int, name: str) -> None:
        """Update person display name."""

    def find_by_cluster_id(self, cluster_id: int) -> Person | None:
        """Fetch person linked to one identity cluster."""

    def bind_cluster(self, person_id: int, cluster_id: int) -> None:
        """Attach person to one identity cluster."""

    def delete(self, person_id: int) -> None:
        """Delete a person record by ID."""


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

    def reassign_cluster_memberships(self, source_cluster_id: int, target_cluster_id: int) -> None:
        """Move all memberships from source cluster to target cluster."""

    def delete_cluster(self, cluster_id: int) -> None:
        """Delete cluster and attached temporal rows."""

    def list_temporal_embeddings(self, cluster_id: int) -> list[ClusterEmbedding]:
        """Load temporal profile rows for one cluster."""

    def upsert_temporal_embedding(
        self,
        *,
        cluster_id: int,
        time_period: str,
        embedding: bytes,
        sample_count: int,
        updated_at: datetime,
    ) -> None:
        """Create or update one temporal bucket embedding."""


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
