from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from photo_app.domain.models import Person
from photo_app.domain.services import now_utc

if TYPE_CHECKING:
    from photo_app.domain.models import Face, IdentityCluster, Image
    from photo_app.domain.repositories import (
        FaceRepository,
        ImageRepository,
        PersonRepository,
    )
    from photo_app.domain.value_objects import BoundingBox
    from photo_app.services.album_query_cache_service import AlbumQueryCacheService
    from photo_app.services.identity_cluster_service import (
        TemporalIdentityClusterService,
    )


@dataclass(frozen=True)
class SuggestedPerson:
    """Person suggestion for a face based on similarity."""

    person_id: int
    person_name: str | None
    similarity_score: float  # 0.0-1.0
    in_same_cluster: bool  # True if in same identity cluster


@dataclass(frozen=True)
class SimilarFace:
    """Face similar to target face in same cluster."""

    face_id: int
    image_path: str
    similarity_score: float  # 0.0-1.0
    confidence_score: float  # Detection confidence


@dataclass(frozen=True)
class FaceReviewItem:
    """Face detail used by UI review tools."""

    face_id: int
    bbox: BoundingBox
    person_id: int | None
    person_name: str | None


@dataclass(frozen=True)
class PersonStackSummary:
    """Grouped face-cluster summary for stack-based review UI."""

    person_id: int
    cluster_id: int | None
    person_name: str | None
    face_count: int
    image_count: int
    cover_image_path: str | None
    cover_image_id: int | None
    sample_image_paths: tuple[str, ...]
    sample_image_ids: tuple[int, ...]


class FaceReviewService:
    """Use case layer for face inspection and name assignment."""

    def __init__(
        self,
        image_repository: ImageRepository,
        face_repository: FaceRepository,
        person_repository: PersonRepository,
        query_cache_service: AlbumQueryCacheService | None = None,
        identity_cluster_service: TemporalIdentityClusterService | None = None,
    ) -> None:
        self._image_repository = image_repository
        self._face_repository = face_repository
        self._person_repository = person_repository
        self._query_cache_service = query_cache_service
        self._identity_cluster_service = identity_cluster_service

    def faces_for_image_path(self, file_path: str) -> list[FaceReviewItem]:
        """Return all indexed face details for a given image file path."""
        image = self._image_repository.get_by_path(file_path)
        if image is None or image.id is None:
            return []

        faces = self._face_repository.list_by_image(image.id)
        items: list[FaceReviewItem] = []
        for face in faces:
            if face.id is None:
                continue
            person_name: str | None = None
            if face.person_id is not None:
                person = self._person_repository.get(face.person_id)
                if person is not None:
                    person_name = person.name
            items.append(
                FaceReviewItem(
                    face_id=face.id,
                    bbox=face.bbox,
                    person_id=face.person_id,
                    person_name=person_name,
                )
            )
        return items

    def assign_name(self, face_id: int, name: str) -> None:
        """Assign one face to a person by name, preserving manual override."""
        cleaned = name.strip()
        if not cleaned:
            return

        resolved_person_id: int | None = None
        existing = self._person_repository.get_by_name(cleaned)
        if existing is not None:
            resolved_person_id = existing.id
        else:
            person = self._person_repository.create(
                Person(
                    id=None,
                    name=cleaned,
                    created_at=now_utc(),
                    birth_date=None,
                )
            )
            resolved_person_id = person.id

        if resolved_person_id is None:
            return

        self._face_repository.assign_person_manual([face_id], resolved_person_id)
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()

    def remove_face(self, face_id: int) -> None:
        """Exclude one detected face from UI and future auto-clustering."""
        self._face_repository.exclude([face_id])
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()

    def person_stacks(self, sample_limit: int = 20) -> list[PersonStackSummary]:
        """Return stack summaries grouped by person ID."""
        grouped: dict[int, list[int]] = {}
        image_faces: dict[int, list[int | None]] = {}
        all_faces = self._face_repository.list_all_active()
        for face in all_faces:
            image_faces.setdefault(face.image_id, []).append(face.person_id)
            if face.person_id is None:
                continue
            grouped.setdefault(face.person_id, []).append(face.image_id)

        images_by_id = {
            image.id: image
            for image in self._image_repository.list_all()
            if image.id is not None
        }

        # Batch load all persons to avoid N+1 DB queries
        all_persons = self._person_repository.list_all()
        persons_by_id: dict[int, Person] = {}
        for person in all_persons:
            if person.id is not None:
                persons_by_id[person.id] = person

        stacks: list[PersonStackSummary] = []
        for person_id, image_ids in grouped.items():
            unique_paths: list[str] = []
            unique_image_ids: list[int] = []
            seen: set[str] = set()
            for image_id in image_ids:
                image = images_by_id.get(image_id)
                if image is None:
                    continue
                if image.file_path in seen:
                    continue
                seen.add(image.file_path)
                unique_paths.append(image.file_path)
                unique_image_ids.append(image_id)

            cover_image_path = self._pick_stack_cover_path(
                person_id=person_id,
                image_ids=unique_image_ids,
                images_by_id=images_by_id,
                image_faces=image_faces,
            )

            # Use batch-loaded person data instead of individual DB queries
            person_record = persons_by_id.get(person_id)
            stacks.append(
                PersonStackSummary(
                    person_id=person_id,
                    cluster_id=(
                        None
                        if person_record is None
                        else person_record.identity_cluster_id
                    ),
                    person_name=None if person_record is None else person_record.name,
                    face_count=len(image_ids),
                    image_count=len(unique_paths),
                    cover_image_path=cover_image_path,
                    cover_image_id=unique_image_ids[0] if unique_image_ids else None,
                    sample_image_paths=tuple(unique_paths[:sample_limit]),
                    sample_image_ids=tuple(unique_image_ids[:sample_limit]),
                )
            )

        stacks.sort(key=lambda item: item.face_count, reverse=True)
        return stacks

    def person_stacks_filtered(
        self, min_image_count: int = 3, sample_limit: int = 20
    ) -> list[PersonStackSummary]:
        """Return stack summaries filtered by minimum image count per person.

        Args:
            min_image_count: Minimum number of images required for a stack to be included
            sample_limit: Maximum number of sample image paths to include per stack

        Returns:
            List of PersonStackSummary objects with at least min_image_count images, sorted by count
        """
        all_stacks = self.person_stacks(sample_limit=sample_limit)
        return [stack for stack in all_stacks if stack.image_count >= min_image_count]

    def rename_person_stack(self, person_id: int, name: str) -> None:
        """Assign one name to an entire matched person stack."""
        cleaned = name.strip()
        if not cleaned:
            return

        # Check for name uniqueness
        existing_person = self._person_repository.get_by_name(cleaned)
        if existing_person is not None and existing_person.id != person_id:
            msg = f"Person name '{cleaned}' already exists"
            raise ValueError(msg)

        self._person_repository.update_name(person_id, cleaned)
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()

    def list_identity_clusters(
        self, *, flagged_only: bool = False
    ) -> list[IdentityCluster]:
        """Return temporal identity clusters for inspection APIs."""
        if self._identity_cluster_service is None:
            return []
        return self._identity_cluster_service.list_clusters(flagged_only=flagged_only)

    def get_person_repository(self) -> PersonRepository:
        """Get the person repository for accessing person data."""
        return self._person_repository

    def get_available_people(self) -> list[Person]:
        """Get list of available people for filtering."""
        return self._person_repository.list_all()

    def cluster_faces(self, cluster_id: int) -> list[Face]:
        """Return all faces assigned to one cluster."""
        if self._identity_cluster_service is None:
            return []
        return self._identity_cluster_service.get_cluster_faces(cluster_id)

    def merge_identity_clusters(
        self, source_cluster_id: int, target_cluster_id: int
    ) -> bool:
        """Apply manual feedback by merging two identity clusters."""
        if self._identity_cluster_service is None:
            return False
        merged = self._identity_cluster_service.merge_clusters(
            source_cluster_id=source_cluster_id,
            target_cluster_id=target_cluster_id,
        )
        if merged and self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()
        return merged

    def _pick_stack_cover_path(
        self,
        *,
        person_id: int,
        image_ids: list[int],
        images_by_id: dict[int, Image],
        image_faces: dict[int, list[int | None]],
    ) -> str | None:
        """Prefer a representative image with only one detected person."""
        single_face_match: list[int] = []
        one_person_match: list[int] = []
        fallback: list[int] = []

        for image_id in image_ids:
            faces = image_faces.get(image_id, [])
            if not faces:
                continue
            if all(pid == person_id for pid in faces):
                if len(faces) == 1:
                    single_face_match.append(image_id)
                else:
                    one_person_match.append(image_id)
            fallback.append(image_id)

        preferred_ids = single_face_match or one_person_match or fallback
        if not preferred_ids:
            return None

        image = images_by_id.get(preferred_ids[0])
        if image is None:
            return None
        return image.file_path

    def get_similar_unassigned_faces(
        self,
        face_id: int,
        similarity_threshold: float = 0.85,
    ) -> list[SimilarFace]:
        """
        Get unassigned faces in the same identity cluster as the given face.

        Args:
            face_id: ID of reference face
            similarity_threshold: Minimum similarity score (0.0-1.0)

        Returns:
            List of similar unassigned faces, sorted by similarity
        """
        # Get the reference face and its cluster
        ref_face = self._face_repository.get(face_id)
        if ref_face is None or ref_face.id is None:
            return []

        # Get cluster membership
        if self._identity_cluster_service is None:
            return []

        cluster_faces = self._identity_cluster_service.get_cluster_faces(ref_face.id)
        if not cluster_faces:
            return []

        # Find unassigned faces in the same cluster
        similar_faces: list[SimilarFace] = []
        ref_embedding = np.frombuffer(ref_face.embedding, dtype=np.float32)

        for face in cluster_faces:
            if face.id is None or face.person_id is not None:
                # Skip assigned faces
                continue
            if face.id == face_id:
                # Skip self
                continue

            # Compute cosine similarity
            face_embedding = np.frombuffer(face.embedding, dtype=np.float32)
            similarity = float(
                np.dot(ref_embedding, face_embedding)
                / (
                    np.linalg.norm(ref_embedding) * np.linalg.norm(face_embedding)
                    + 1e-8
                )
            )

            if similarity >= similarity_threshold:
                image = self._image_repository.get_by_id(face.image_id)
                if image is not None:
                    similar_faces.append(
                        SimilarFace(
                            face_id=face.id,
                            image_path=image.file_path,
                            similarity_score=similarity,
                            confidence_score=face.confidence_score or 0.0,
                        )
                    )

        # Sort by similarity, descending
        similar_faces.sort(key=lambda x: (-x.similarity_score, -x.confidence_score))
        return similar_faces

    def get_suggested_names_for_face(
        self,
        face_id: int,
        top_k: int = 5,
        similarity_threshold: float = 0.80,
    ) -> list[SuggestedPerson]:
        """
        Get suggested person names for a face based on similarity.

        Priority:
        1. Persons already in the same identity cluster (highest confidence)
        2. Other high-similarity persons from ANN search

        Args:
            face_id: ID of face to get suggestions for
            top_k: Maximum number of suggestions to return
            similarity_threshold: Minimum similarity score

        Returns:
            List of suggested persons, sorted by confidence
        """
        suggestions: list[SuggestedPerson] = []
        face = self._face_repository.get(face_id)

        if face is None or face.id is None:
            return []

        face_embedding = np.frombuffer(face.embedding, dtype=np.float32)

        # Priority 1: Check same identity cluster
        if self._identity_cluster_service is not None:
            cluster_faces = self._identity_cluster_service.get_cluster_faces(face.id)
            seen_person_ids: set[int] = set()

            for cluster_face in cluster_faces:
                if cluster_face.person_id is None:
                    continue
                if cluster_face.person_id in seen_person_ids:
                    continue

                person = self._person_repository.get(cluster_face.person_id)
                if person is None:
                    continue

                # Compute similarity to this cluster face
                cluster_emb = np.frombuffer(cluster_face.embedding, dtype=np.float32)
                similarity = float(
                    np.dot(face_embedding, cluster_emb)
                    / (
                        np.linalg.norm(face_embedding) * np.linalg.norm(cluster_emb)
                        + 1e-8
                    )
                )

                if similarity >= similarity_threshold:
                    seen_person_ids.add(cluster_face.person_id)
                    suggestions.append(
                        SuggestedPerson(
                            person_id=cluster_face.person_id,
                            person_name=person.name,
                            similarity_score=similarity,
                            in_same_cluster=True,
                        )
                    )

        # Priority 2: ANN search for high-similarity named faces (not yet implemented)

        # Sort by (in_same_cluster desc, similarity_score desc)
        suggestions.sort(key=lambda x: (-int(x.in_same_cluster), -x.similarity_score))

        return suggestions[:top_k]

    def batch_assign_faces_to_person(self, face_ids: list[int], person_id: int) -> None:
        """
        Assign multiple faces to the same person.

        Args:
            face_ids: List of face IDs to assign
            person_id: Target person ID
        """
        self._face_repository.assign_person_manual(face_ids, person_id)
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()

    def resolve_cluster_id_for_person(
        self, person_id: int, stack_cluster_id: int | None
    ) -> int | None:
        """Return cluster_id from the stack, or look it up via face memberships."""
        if stack_cluster_id is not None:
            return stack_cluster_id
        return self._face_repository.find_cluster_id_for_person(person_id)
