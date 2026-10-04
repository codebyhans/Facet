from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import numpy as np

from photo_app.domain.models import Person
from photo_app.domain.services import now_utc
from photo_app.services.ann_index import ExactCosineIndex

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from photo_app.domain.models import Face, IdentityCluster, Image
    from photo_app.domain.repositories import (
        FaceRepository,
        IdentityClusterRepository,
        ImageRepository,
        PersonRepository,
    )


_MIN_VECTOR_NORM = 1e-12
_MIN_SPAN = 1e-6


@dataclass(frozen=True)
class TemporalIdentityConfig:
    """Runtime configuration for identity matching."""

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
    person_id: int | None = None


class TemporalIdentityClusterService:
    """Identity clustering service: faces join the most similar cluster."""

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
        self._ann = ExactCosineIndex()
        self._index_dirty = True

    def index_new_faces(  # noqa: C901, PLR0912
        self,
        limit: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Assign newly detected faces into identity clusters.

        Optimised for large batches:
        - ANN index built once and updated incrementally (no per-face DB rebuild)
        - Cluster state recalculated once per cluster after all assignments
        - A cluster's tag (`person_id`) comes straight off the already
          batch-fetched cluster row for a match, or is None for a brand new
          cluster — no repeated per-cluster person lookups.
        """
        staged = self._face_repository.list_without_cluster_membership(limit=limit)
        if not staged:
            return 0

        image_by_id = self._image_map()

        # Build ANN index once from current cluster state
        self._ensure_ann_index()

        # Track which clusters receive new faces — refresh each once at the end
        affected_cluster_ids: set[int] = set()

        total = len(staged)
        for idx, face in enumerate(staged, start=1):
            if face.id is None:
                continue

            vector = _normalize(np.frombuffer(face.embedding, dtype=np.float32))
            match = self._match_cluster(vector)
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
                cluster_person_id = None
                if cluster_id is not None:
                    # Add to the in-memory index so subsequent faces in this
                    # same batch can match it, without a full DB reload.
                    self._ann.add_vector(cluster_id, vector)
            else:
                cluster_id = match.cluster_id
                confidence = match.confidence
                cluster_person_id = match.person_id

            if cluster_id is None:
                continue

            self._cluster_repository.upsert_membership(
                face_id=face.id,
                cluster_id=cluster_id,
                confidence=confidence,
                assigned_at=timestamp,
            )
            affected_cluster_ids.add(cluster_id)

            if cluster_person_id is None and face.person_id is not None:
                # This face already carries a manual tag (e.g. reprocessed
                # after something cleared cluster membership but not the
                # label) — propagate it onto the whole (still-untagged)
                # cluster instead of losing it.
                self._cluster_repository.set_cluster_person(cluster_id, face.person_id)
                cluster_person_id = face.person_id

            if cluster_person_id is None:
                # Still no tag — spin up a placeholder "Unnamed" one so the
                # cluster still shows in the People tab, same as before.
                # Applying a real tag later (`tag_cluster`) relabels this
                # (and any other) cluster without touching cluster state.
                created = self._person_repository.create(
                    Person(id=None, name=None, created_at=now_utc(), birth_date=None)
                )
                if created.id is not None:
                    cluster_person_id = created.id
                    self._cluster_repository.set_cluster_person(
                        cluster_id, cluster_person_id
                    )

            if cluster_person_id is not None:
                self._face_repository.assign_person_auto([face.id], cluster_person_id)

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

    def tag_cluster(self, cluster_id: int, person_id: int) -> bool:
        """Apply a name tag to a cluster.

        Labels every current member face with `person_id` and records the
        tag on the cluster itself — but never touches the cluster's
        embedding or membership, so applying the same tag to several
        different clusters (e.g. a child's separate baby/toddler/kid
        clusters) never merges or smears anything.
        """
        cluster = self._cluster_repository.get_cluster(cluster_id)
        if cluster is None:
            return False

        memberships = self._cluster_repository.list_memberships(cluster_id)
        face_ids = [membership.face_id for membership in memberships]
        if face_ids:
            self._face_repository.assign_person_manual(face_ids, person_id)

        self._cluster_repository.set_cluster_person(cluster_id, person_id)
        return True

    def retag_person_clusters(self, old_person_id: int, new_person_id: int) -> int:
        """Move every cluster tagged `old_person_id` over to `new_person_id`.

        For consolidating two name tags that turned out to be the same
        person. Like `tag_cluster`, only the tag changes — embeddings and
        memberships stay separate — but member faces are left alone (their
        person ids are moved by `FaceRepository.reassign_person`, which
        preserves manual/auto provenance). Returns the number of clusters
        retagged.
        """
        clusters = self._cluster_repository.list_clusters_for_person(old_person_id)
        retagged = 0
        for cluster in clusters:
            if cluster.id is None:
                continue
            self._cluster_repository.set_cluster_person(cluster.id, new_person_id)
            retagged += 1
        return retagged

    def list_clusters(self, *, flagged_only: bool = False) -> list[IdentityCluster]:
        """Return all clusters, optionally only review-flagged ones."""
        clusters = self._cluster_repository.list_clusters()
        if flagged_only:
            return [cluster for cluster in clusters if cluster.flagged_for_review]
        return clusters

    def list_clusters_for_person(self, person_id: int) -> list[IdentityCluster]:
        """Return every cluster currently tagged with this person."""
        return self._cluster_repository.list_clusters_for_person(person_id)

    def get_cluster_faces(self, cluster_id: int) -> list[Face]:
        """Return member faces for one cluster."""
        memberships = self._cluster_repository.list_memberships(cluster_id)
        return self._face_repository.get_many(
            [membership.face_id for membership in memberships]
        )

    def exclude_persons(self, person_ids: Sequence[int]) -> int:
        """Bulk-exclude people not of interest (e.g. a stranger or random
        passerby, spotted while reviewing clusters): every face labeled
        with any of these person ids is excluded — kept, but never shown or
        re-clustered again, the same mechanism as excluding one
        false-detected face — and their now-empty cluster/person records
        are removed instead of lingering as clutter in the People tab.
        Returns the number of faces excluded.
        """
        if not person_ids:
            return 0
        total_excluded = 0
        for person_id in person_ids:
            faces = self._face_repository.list_by_person(person_id)
            face_ids = [face.id for face in faces if face.id is not None]
            if face_ids:
                self._face_repository.exclude(face_ids)
                total_excluded += len(face_ids)
            for cluster in self._cluster_repository.list_clusters_for_person(person_id):
                if cluster.id is not None:
                    self._cluster_repository.delete_cluster(cluster.id)
            self._person_repository.delete(person_id)
        self._index_dirty = True
        return total_excluded

    def delete_all_clusters(self) -> None:
        """Wipe every cluster and membership.

        Used for a full identity reset: after this, every face looks
        unclustered again, so the next `index_new_faces()` re-derives
        clusters from scratch under the current matching rules.
        Also resets the in-memory ANN index, since its cached vectors would
        otherwise reference clusters that no longer exist in the database.
        """
        self._cluster_repository.delete_all_clusters()
        self._ann = ExactCosineIndex()
        self._index_dirty = True

    def rematch_unnamed_faces(
        self, on_progress: Callable[[int, int], None] | None = None
    ) -> int:
        """Dissolve every unnamed cluster and re-match its faces.

        A face in an unnamed cluster was never confirmed by anyone, so it
        is simply run through the matcher again: it joins the most similar
        cluster (named or not) or starts a fresh unnamed one. Named
        clusters are never dissolved or merged — they can only gain faces.
        An unnamed cluster holding any manually assigned face is left
        untouched. Returns the number of faces re-matched.
        """
        persons_by_id = {
            person.id: person
            for person in self._person_repository.list_all()
            if person.id is not None
        }
        active_faces = self._face_repository.list_all_active()
        manual_person_ids = {
            face.person_id for face in active_faces if face.manual_assignment
        }

        def dissolvable(cluster: IdentityCluster) -> bool:
            if cluster.person_id is None:
                return True
            person = persons_by_id.get(cluster.person_id)
            return (
                person is None or person.name is None
            ) and cluster.person_id not in manual_person_ids

        clusters = [
            cluster
            for cluster in self._cluster_repository.list_clusters()
            if cluster.id is not None and dissolvable(cluster)
        ]
        if not clusters:
            return 0

        placeholder_ids = {
            cluster.person_id for cluster in clusters if cluster.person_id is not None
        }
        self._face_repository.unassign_faces(
            [
                face.id
                for face in active_faces
                if face.id is not None and face.person_id in placeholder_ids
            ]
        )
        self._cluster_repository.delete_clusters(
            [cluster.id for cluster in clusters if cluster.id is not None]
        )
        self._person_repository.delete_many(list(placeholder_ids))

        self._ann = ExactCosineIndex()
        self._index_dirty = True
        return self.index_new_faces(on_progress=on_progress)

    def recalculate_all_cluster_states(self) -> int:
        """Recompute centroid, variance and review flag for all clusters."""
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
            self._refresh_cluster_state(
                cluster.id, faces_by_id=faces_by_id, image_by_id=image_by_id
            )
        self._index_dirty = True
        return len(clusters)

    def _match_cluster(self, vector: np.ndarray) -> ClusterMatch | None:
        """Best-matching cluster by centroid similarity, if above threshold.

        Purely embedding-based: neither name tags nor any age estimate play
        a part, so a face always lands with the faces it looks like.
        """
        self._ensure_ann_index()
        candidates = self._ann.query(vector, limit=self._config.ann_candidate_limit)
        if not candidates:
            return None

        candidate_ids = [candidate.item_id for candidate in candidates]
        clusters_by_id = self._cluster_repository.get_clusters(candidate_ids)

        best: ClusterMatch | None = None
        for candidate in candidates:
            cluster = clusters_by_id.get(candidate.item_id)
            if cluster is None:
                continue
            similarity = self._centroid_similarity(vector, cluster)
            if best is None or similarity > best.confidence:
                best = ClusterMatch(
                    cluster_id=candidate.item_id,
                    confidence=similarity,
                    person_id=cluster.person_id,
                )

        if best is None or best.confidence < self._config.match_threshold:
            return None
        return best

    def _centroid_similarity(
        self, vector: np.ndarray, cluster: IdentityCluster
    ) -> float:
        centroid = _normalize(
            np.frombuffer(cluster.canonical_embedding, dtype=np.float32)
        )
        return float(np.dot(vector, centroid))

    def _refresh_cluster_state(
        self,
        cluster_id: int,
        *,
        faces_by_id: dict[int, Face] | None = None,
        image_by_id: dict[int, Image] | None = None,
    ) -> None:
        """Recompute one cluster's centroid, variance and review flag.

        Batch callers (e.g. `recalculate_all_cluster_states`) pass
        pre-loaded `faces_by_id`/`image_by_id` covering the whole library,
        amortizing that load across many clusters. Callers refreshing just
        one cluster should omit them — this then
        fetches only *this cluster's* member faces and the images they
        reference, instead of scanning the entire faces/images tables for
        a handful of rows.
        """
        cluster = self._cluster_repository.get_cluster(cluster_id)
        if cluster is None:
            return

        memberships = self._cluster_repository.list_memberships(cluster_id)
        if faces_by_id is None:
            member_faces = self._face_repository.get_many(
                [membership.face_id for membership in memberships]
            )
        else:
            member_faces = [
                faces_by_id[membership.face_id]
                for membership in memberships
                if membership.face_id in faces_by_id
            ]
        if not member_faces:
            return

        if image_by_id is None:
            needed_image_ids = {face.image_id for face in member_faces}
            image_by_id = {
                image.id: image
                for image in self._image_repository.list_by_ids(list(needed_image_ids))
                if image.id is not None
            }

        vectors = np.vstack(
            [
                _normalize(np.frombuffer(face.embedding, dtype=np.float32))
                for face in member_faces
            ]
        )
        dates = [
            self._capture_date(face.image_id, image_by_id) for face in member_faces
        ]
        weights = _recency_weights(dates, recency_weight=self._config.recency_weight)
        centroid = _normalize(
            np.average(vectors, axis=0, weights=weights).astype(np.float32)
        )

        similarities = vectors @ centroid
        variance = float(np.mean(1.0 - similarities))

        self._cluster_repository.update_cluster_state(
            cluster_id,
            canonical_embedding=centroid.astype(np.float32).tobytes(),
            face_count=len(member_faces),
            variance=variance,
            flagged_for_review=variance >= self._config.variance_review_threshold,
            updated_at=now_utc(),
        )

    def _ensure_ann_index(self) -> None:
        if not self._index_dirty:
            return
        clusters = [
            cluster
            for cluster in self._cluster_repository.list_clusters()
            if cluster.id is not None
        ]
        vectors = {
            cluster.id: _normalize(
                np.frombuffer(cluster.canonical_embedding, dtype=np.float32)
            )
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
            return image.capture_date.date()
        return datetime.now(tz=UTC).date()


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= _MIN_VECTOR_NORM:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)


def _recency_weights(dates: list[date], *, recency_weight: float) -> np.ndarray:
    if len(dates) == 1:
        return np.array([1.0], dtype=np.float32)
    ordinals = np.array([item.toordinal() for item in dates], dtype=np.float32)
    span = float(np.max(ordinals) - np.min(ordinals))
    if span <= _MIN_SPAN:
        return np.ones((len(dates),), dtype=np.float32)
    scaled = (ordinals - np.min(ordinals)) / span
    return (1.0 + recency_weight * scaled).astype(np.float32)
