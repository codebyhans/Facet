from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import numpy as np

from photo_app.domain.models import Person
from photo_app.domain.services import now_utc
from photo_app.services.ann_index import RandomProjectionAnnIndex

if TYPE_CHECKING:
    from photo_app.domain.models import Face, IdentityCluster, Image
    from photo_app.domain.repositories import (
        FaceRepository,
        IdentityClusterRepository,
        ImageRepository,
        PersonRepository,
    )


_CENTROID_WEIGHT = 0.7
_TEMPORAL_WEIGHT = 0.3
_TIME_PERIODS = ("child", "teen", "young_adult", "adult", "senior")


@dataclass(frozen=True)
class TemporalIdentityConfig:
    """Runtime configuration for temporal identity matching."""

    match_threshold: float = 0.52
    merge_threshold: float = 0.70
    variance_review_threshold: float = 0.35
    recency_weight: float = 0.15
    ann_candidate_limit: int = 32


@dataclass(frozen=True)
class ClusterMatch:
    """Candidate matching score for one cluster."""

    cluster_id: int
    confidence: float


class TemporalIdentityClusterService:
    """Age-robust identity clustering service with temporal smoothing."""

    def __init__(
        self,
        face_repository: FaceRepository,
        image_repository: ImageRepository,
        person_repository: PersonRepository,
        cluster_repository: IdentityClusterRepository,
        config: TemporalIdentityConfig | None = None,
    ) -> None:
        self._face_repository = face_repository
        self._image_repository = image_repository
        self._person_repository = person_repository
        self._cluster_repository = cluster_repository
        self._config = config or TemporalIdentityConfig()
        self._ann = RandomProjectionAnnIndex()
        self._index_dirty = True

    def index_new_faces(
        self,
        limit: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Assign newly detected faces into identity clusters.

        Optimised for large batches:
        - ANN index built once and updated incrementally (no per-face DB rebuild)
        - Cluster state recalculated once per cluster after all assignments
        - Person assignment cached per cluster to avoid repeated DB reads
        """
        staged = self._face_repository.list_without_cluster_membership(limit=limit)
        if not staged:
            return 0

        image_by_id = self._image_map()

        # Build ANN index once from current cluster state
        self._ensure_ann_index()

        # Track which clusters receive new faces — refresh each once at the end
        affected_cluster_ids: set[int] = set()

        # Cache person lookups per cluster to avoid N repeated find_by_cluster_id calls
        cluster_person_cache: dict[int, Person | None] = {}  # cluster_id → Person | None

        total = len(staged)
        for idx, face in enumerate(staged, start=1):
            if face.id is None:
                continue

            vector = _normalize(np.frombuffer(face.embedding, dtype=np.float32))
            age_bucket = self._age_bucket_for_face(face, image_by_id=image_by_id)
            match = self._match_cluster(vector, age_bucket=age_bucket)
            timestamp = now_utc()

            if match is None:
                # New cluster — create it and add its vector to the in-memory ANN
                # index directly, without triggering a full DB reload
                cluster = self._cluster_repository.create_cluster(
                    vector.astype(np.float32).tobytes(),
                    created_at=timestamp,
                )
                cluster_id = cluster.id
                confidence = 1.0
                if cluster_id is not None:
                    # Add to in-memory index so subsequent faces can match it
                    self._ann.add_vector(cluster_id, vector)
                    # Rebuild projection signatures for the new vector
                    # (mark dirty so _ensure_ann_index rebuilds cleanly next time
                    # this is called from outside the batch)
                    self._index_dirty = True
            else:
                cluster_id = match.cluster_id
                confidence = match.confidence

            if cluster_id is None:
                continue

            self._cluster_repository.upsert_membership(
                face_id=face.id,
                cluster_id=cluster_id,
                confidence=confidence,
                assigned_at=timestamp,
            )
            affected_cluster_ids.add(cluster_id)

            # Sync person assignment with cache
            if cluster_id not in cluster_person_cache:
                cluster_person_cache[cluster_id] = (
                    self._person_repository.find_by_cluster_id(cluster_id)
                )
            bound_person = cluster_person_cache[cluster_id]

            if bound_person is None and face.person_id is not None:
                self._person_repository.bind_cluster(face.person_id, cluster_id)
                bound_person = self._person_repository.get(face.person_id)
                cluster_person_cache[cluster_id] = bound_person

            if bound_person is None:
                created = self._person_repository.create(
                    Person(
                        id=None,
                        name=None,
                        created_at=now_utc(),
                        birth_date=None,
                        identity_cluster_id=cluster_id,
                    )
                )
                bound_person = created
                cluster_person_cache[cluster_id] = bound_person

            if bound_person is not None and bound_person.id is not None:
                self._face_repository.assign_person_auto([face.id], bound_person.id)

            # Call progress callback after all assignments for this face are done
            if on_progress is not None:
                on_progress(idx, total)

        # Refresh cluster state once per affected cluster (not once per face)
        if affected_cluster_ids:
            active_face_map = {
                face.id: face
                for face in self._face_repository.list_all_active()
                if face.id is not None
            }
            for cluster_id in affected_cluster_ids:
                self._refresh_cluster_state(
                    cluster_id,
                    faces_by_id=active_face_map,
                    image_by_id=image_by_id,
                )

        return len(staged)

    def list_clusters(self, *, flagged_only: bool = False) -> list[IdentityCluster]:
        """Return all clusters, optionally only review-flagged ones."""
        clusters = self._cluster_repository.list_clusters()
        if flagged_only:
            return [cluster for cluster in clusters if cluster.flagged_for_review]
        return clusters

    def get_cluster_faces(self, cluster_id: int) -> list[Face]:
        """Return member faces for one cluster."""
        memberships = self._cluster_repository.list_memberships(cluster_id)
        face_ids = {membership.face_id for membership in memberships}
        faces = self._face_repository.list_all_active()
        return [face for face in faces if face.id is not None and face.id in face_ids]

    def merge_clusters(self, source_cluster_id: int, target_cluster_id: int) -> bool:
        """Merge source cluster into target cluster."""
        if source_cluster_id == target_cluster_id:
            return False
        source = self._cluster_repository.get_cluster(source_cluster_id)
        target = self._cluster_repository.get_cluster(target_cluster_id)
        if source is None or target is None:
            return False

        # Move all face memberships to the target cluster
        self._cluster_repository.reassign_cluster_memberships(
            source_cluster_id=source_cluster_id,
            target_cluster_id=target_cluster_id,
        )

        # ── NEW: rebind the source person to the target cluster ──────────────────
        # The source Person record still points to source_cluster_id (now being
        # deleted). Find that person and point them at the target cluster instead.
        # If both clusters had a person bound, merge the source person into the
        # target person: reassign their faces and delete the duplicate.
        source_person = self._person_repository.find_by_cluster_id(source_cluster_id)
        target_person = self._person_repository.find_by_cluster_id(target_cluster_id)

        if source_person is not None and source_person.id is not None:
            if target_person is not None and target_person.id is not None:
                # Both clusters had a person — reassign all faces from source person
                # to target person, then delete the now-orphaned source person.
                all_faces = self._face_repository.list_all_active()
                source_face_ids = [
                    f.id for f in all_faces
                    if f.person_id == source_person.id and f.id is not None
                ]
                if source_face_ids:
                    self._face_repository.assign_person_auto(source_face_ids, target_person.id)
                self._person_repository.delete(source_person.id)
            else:
                # Only source cluster had a person — point them at the target cluster
                self._person_repository.bind_cluster(source_person.id, target_cluster_id)
        # ── END NEW ──────────────────────────────────────────────────────────────

        self._cluster_repository.delete_cluster(source_cluster_id)
        self._refresh_cluster_state(
            target_cluster_id,
            faces_by_id={
                face.id: face
                for face in self._face_repository.list_all_active()
                if face.id is not None
            },
            image_by_id=self._image_map(),
        )
        self._index_dirty = True
        return True

    def recalculate_all_cluster_states(self) -> int:
        """Recompute centroid, temporal profiles, and variance for all clusters."""
        faces_by_id = {
            face.id: face
            for face in self._face_repository.list_all_active()
            if face.id is not None
        }
        image_by_id = self._image_map()
        clusters = self._cluster_repository.list_clusters()
        for cluster in clusters:
            if cluster.id is None:
                continue
            self._refresh_cluster_state(cluster.id, faces_by_id=faces_by_id, image_by_id=image_by_id)
        self._index_dirty = True
        return len(clusters)

    def detect_and_merge_duplicate_clusters(self) -> int:
        """Merge clusters whose centroids exceed merge threshold.

        Uses the ANN index to find candidates rather than checking all pairs,
        reducing complexity from O(N²) to O(N × ann_candidate_limit).
        """
        clusters = [
            c for c in self._cluster_repository.list_clusters()
            if c.id is not None
        ]
        if not clusters:
            return 0

        # Build a fresh ANN index over current cluster centroids
        vectors: dict[int, np.ndarray] = {}
        for cluster in clusters:
            if cluster.id is not None and cluster.canonical_embedding:
                vectors[cluster.id] = _normalize(
                    np.frombuffer(cluster.canonical_embedding, dtype=np.float32)
                )

        if len(vectors) < 2:
            return 0

        self._ann.build(vectors)
        self._index_dirty = False

        merged = 0
        already_merged: set[int] = set()

        for cluster_id, vector in vectors.items():
            if cluster_id in already_merged:
                continue

            # Query ANN for nearest neighbours (excludes self if not in results)
            candidates = self._ann.query(vector, limit=self._config.ann_candidate_limit + 1)

            for candidate in candidates:
                other_id = candidate.item_id
                if other_id == cluster_id:
                    continue
                if other_id in already_merged:
                    continue

                # Check current vector for other (may have been updated by a merge)
                other_vec = vectors.get(other_id)
                if other_vec is None:
                    continue

                similarity = float(np.dot(vector, other_vec))
                if similarity < self._config.merge_threshold:
                    continue

                if self.merge_clusters(source_cluster_id=other_id, target_cluster_id=cluster_id):
                    merged += 1
                    already_merged.add(other_id)
                    vectors.pop(other_id, None)

                    # Refresh the surviving cluster's centroid
                    updated = self._cluster_repository.get_cluster(cluster_id)
                    if updated is not None and updated.canonical_embedding:
                        vectors[cluster_id] = _normalize(
                            np.frombuffer(updated.canonical_embedding, dtype=np.float32)
                        )

        self._index_dirty = True  # mark dirty so next use rebuilds with merged state
        return merged

    def _match_cluster(self, vector: np.ndarray, *, age_bucket: str | None) -> ClusterMatch | None:
        self._ensure_ann_index()
        candidates = self._ann.query(vector, limit=self._config.ann_candidate_limit)
        if not candidates:
            return None

        best: ClusterMatch | None = None
        for candidate in candidates:
            cluster = self._cluster_repository.get_cluster(candidate.item_id)
            if cluster is None:
                continue
            centroid = _normalize(
                np.frombuffer(cluster.canonical_embedding, dtype=np.float32)
            )
            centroid_similarity = float(np.dot(vector, centroid))

            temporal_rows = self._cluster_repository.list_temporal_embeddings(
                candidate.item_id
            )
            temporal_similarity = -1.0
            for row in temporal_rows:
                if age_bucket is not None and row.time_period != age_bucket:
                    continue
                temporal_vector = _normalize(np.frombuffer(row.embedding, dtype=np.float32))
                temporal_similarity = max(
                    temporal_similarity,
                    float(np.dot(vector, temporal_vector)),
                )
            if temporal_similarity < -0.5:
                for row in temporal_rows:
                    temporal_vector = _normalize(np.frombuffer(row.embedding, dtype=np.float32))
                    temporal_similarity = max(
                        temporal_similarity,
                        float(np.dot(vector, temporal_vector)),
                    )
            if temporal_similarity < -0.5:
                temporal_similarity = centroid_similarity

            weighted_score = (
                _CENTROID_WEIGHT * centroid_similarity
                + _TEMPORAL_WEIGHT * temporal_similarity
            )
            smoothed = max(centroid_similarity, temporal_similarity)
            final_score = max(weighted_score, smoothed)
            if best is None or final_score > best.confidence:
                best = ClusterMatch(
                    cluster_id=candidate.item_id,
                    confidence=final_score,
                )

        if best is None or best.confidence < self._config.match_threshold:
            return None
        return best

    def _refresh_cluster_state(
        self,
        cluster_id: int,
        *,
        faces_by_id: dict[int, Face],
        image_by_id: dict[int, Image],
    ) -> None:
        memberships = self._cluster_repository.list_memberships(cluster_id)
        member_faces = [
            faces_by_id[membership.face_id]
            for membership in memberships
            if membership.face_id in faces_by_id
        ]
        if not member_faces:
            return

        vectors = np.vstack(
            [_normalize(np.frombuffer(face.embedding, dtype=np.float32)) for face in member_faces]
        )
        dates = [self._capture_date(face.image_id, image_by_id) for face in member_faces]
        weights = _recency_weights(dates, recency_weight=self._config.recency_weight)
        centroid = _normalize(np.average(vectors, axis=0, weights=weights).astype(np.float32))

        similarities = vectors @ centroid
        variance = float(np.mean(1.0 - similarities))
        flagged = variance >= self._config.variance_review_threshold

        self._cluster_repository.update_cluster_state(
            cluster_id,
            canonical_embedding=centroid.astype(np.float32).tobytes(),
            face_count=len(member_faces),
            variance=variance,
            flagged_for_review=flagged,
            updated_at=now_utc(),
        )

        cluster_person = self._person_repository.find_by_cluster_id(cluster_id)
        birth_year = None if cluster_person is None or cluster_person.birth_date is None else cluster_person.birth_date.year
        grouped: dict[str, list[np.ndarray]] = {period: [] for period in _TIME_PERIODS}
        for face in member_faces:
            period = self._age_bucket_for_face(
                face,
                image_by_id=image_by_id,
                birth_year=birth_year,
            )
            if period is None:
                continue
            grouped[period].append(_normalize(np.frombuffer(face.embedding, dtype=np.float32)))

        for period, embeddings in grouped.items():
            if not embeddings:
                continue
            profile = _normalize(np.mean(np.vstack(embeddings), axis=0).astype(np.float32))
            self._cluster_repository.upsert_temporal_embedding(
                cluster_id=cluster_id,
                time_period=period,
                embedding=profile.astype(np.float32).tobytes(),
                sample_count=len(embeddings),
                updated_at=now_utc(),
            )

    def _sync_person_assignment(self, face: Face, cluster_id: int) -> None:
        if face.id is None:
            return

        bound_person = self._person_repository.find_by_cluster_id(cluster_id)
        if bound_person is None and face.person_id is not None:
            self._person_repository.bind_cluster(face.person_id, cluster_id)
            bound_person = self._person_repository.get(face.person_id)
        if bound_person is None:
            created = self._person_repository.create(
                Person(
                    id=None,
                    name=None,
                    created_at=now_utc(),
                    birth_date=None,
                    identity_cluster_id=cluster_id,
                )
            )
            bound_person = created
        if bound_person.id is None:
            return
        self._face_repository.assign_person_auto([face.id], bound_person.id)

    def _ensure_ann_index(self) -> None:
        if not self._index_dirty:
            return
        clusters = [cluster for cluster in self._cluster_repository.list_clusters() if cluster.id is not None]
        vectors = {
            cluster.id: _normalize(np.frombuffer(cluster.canonical_embedding, dtype=np.float32))
            for cluster in clusters
            if cluster.id is not None
        }
        self._ann.build(vectors)
        self._index_dirty = False

    def _image_map(self) -> dict[int, Image]:
        return {
            image.id: image
            for image in self._image_repository.list_all()
            if image.id is not None
        }

    def _capture_date(self, image_id: int, image_by_id: dict[int, Image]) -> date:
        image = image_by_id.get(image_id)
        if image is not None and image.capture_date is not None:
            return image.capture_date
        return datetime.now(tz=UTC).date()

    def _age_bucket_for_face(
        self,
        face: Face,
        *,
        image_by_id: dict[int, Image],
        birth_year: int | None = None,
    ) -> str | None:
        if birth_year is None and face.person_id is not None:
            person = self._person_repository.get(face.person_id)
            if person is not None and person.birth_date is not None:
                birth_year = person.birth_date.year
        capture = self._capture_date(face.image_id, image_by_id)
        if birth_year is None:
            return None
        age = capture.year - birth_year
        if age < 13:
            return "child"
        if age < 18:
            return "teen"
        if age < 30:
            return "young_adult"
        if age < 60:
            return "adult"
        return "senior"


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)


def _recency_weights(dates: list[date], *, recency_weight: float) -> np.ndarray:
    if len(dates) == 1:
        return np.array([1.0], dtype=np.float32)
    ordinals = np.array([item.toordinal() for item in dates], dtype=np.float32)
    span = float(np.max(ordinals) - np.min(ordinals))
    if span <= 1e-6:
        return np.ones((len(dates),), dtype=np.float32)
    scaled = (ordinals - np.min(ordinals)) / span
    return (1.0 + recency_weight * scaled).astype(np.float32)
