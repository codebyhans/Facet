from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from scipy.cluster.hierarchy import (  # type: ignore[import-untyped]
    leaves_list,
    linkage,
    optimal_leaf_ordering,
)
from scipy.spatial.distance import pdist  # type: ignore[import-untyped]

from photo_app.domain.models import Person
from photo_app.domain.services import now_utc

if TYPE_CHECKING:
    from datetime import date, datetime

    from photo_app.domain.models import Face, IdentityCluster, ImageSummary
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


# Optimal leaf ordering is roughly cubic; beyond this many stacks the plain
# dendrogram order is kept (still groups similar clusters, just less tidily).
_MAX_STACKS_FOR_OPTIMAL_LEAF_ORDER = 600


@dataclass(frozen=True)
class ExcludedFaceItem:
    """One excluded face, enriched for display in the restore browser.

    Excluding never deletes the underlying detection (bbox/embedding) or
    photo — only marks it `excluded` and drops its cluster/person — so
    everything needed to show and restore it is always available.
    """

    face_id: int
    image_id: int
    image_path: str
    bbox: BoundingBox


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
    """Grouped face-cluster summary for stack-based review UI.

    Grouped by `Face.person_id` (the name tag), so one stack can aggregate
    faces from several different clusters once they're all tagged with the
    same name — exactly the "find every photo of this person, regardless
    of which cluster they landed in" goal tagging exists for. `cluster_id`
    is therefore just *one representative* cluster for this person, not
    necessarily the only one; `flagged_for_review` reflects whether *any*
    of the person's clusters is flagged.
    """

    person_id: int
    cluster_id: int | None
    person_name: str | None
    face_count: int
    image_count: int
    cover_image_path: str | None
    cover_image_id: int | None
    sample_image_paths: tuple[str, ...]
    sample_image_ids: tuple[int, ...]
    cover_face_bbox: BoundingBox | None = None
    cover_capture_date: datetime | None = None
    flagged_for_review: bool = False
    # Position in the "similar faces next to each other" ordering; None when
    # the person has no cluster centroid to compare (sorted last).
    similarity_rank: int | None = None
    # Cover face bbox area as a fraction of the cover image's area; None when
    # the bbox or the image dimensions are unknown (sorted last).
    cover_face_area_ratio: float | None = None


def _similarity_ranks(vectors: dict[int, np.ndarray]) -> dict[int, int]:
    """Order people so visually similar ones sit next to each other.

    Average-linkage agglomerative clustering on the cosine distance between
    each person's centroid, read out as the dendrogram's leaf order — so
    two clusters of the same person that the assigner kept apart end up
    adjacent, and groups of look-alikes stay together. Uses only the
    already-stored centroids: one pairwise-distance pass, no ML inference.
    """
    keys = list(vectors)
    if len(keys) < 2:  # noqa: PLR2004 - linkage needs at least two points
        return {key: rank for rank, key in enumerate(keys)}
    matrix = np.stack([vectors[key] for key in keys])
    distances = np.clip(pdist(matrix, metric="cosine"), 0.0, None)
    tree = linkage(distances, method="average")
    if len(keys) <= _MAX_STACKS_FOR_OPTIMAL_LEAF_ORDER:
        tree = optimal_leaf_ordering(tree, distances)
    return {keys[int(leaf)]: rank for rank, leaf in enumerate(leaves_list(tree))}


def _unique_images(
    image_ids: list[int], images_by_id: dict[int, ImageSummary]
) -> tuple[list[int], list[str]]:
    """Return (ids, paths) of known images, deduplicated by file path."""
    unique_ids: list[int] = []
    unique_paths: list[str] = []
    seen: set[str] = set()
    for image_id in image_ids:
        image = images_by_id.get(image_id)
        if image is None or image.file_path in seen:
            continue
        seen.add(image.file_path)
        unique_ids.append(image_id)
        unique_paths.append(image.file_path)
    return unique_ids, unique_paths


def _with_similarity_ranks(
    stacks: list[PersonStackSummary], person_vectors: dict[int, np.ndarray]
) -> list[PersonStackSummary]:
    """Return `stacks` with `similarity_rank` set, ranked among themselves."""
    ranks = _similarity_ranks(
        {
            stack.person_id: person_vectors[stack.person_id]
            for stack in stacks
            if stack.person_id in person_vectors
        }
    )
    return [
        replace(stack, similarity_rank=ranks.get(stack.person_id)) for stack in stacks
    ]


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

        # Batch-load the (typically few) referenced persons in one query
        # instead of one `.get()` round-trip per face.
        wanted_person_ids = {
            face.person_id for face in faces if face.person_id is not None
        }
        persons_by_id: dict[int, Person] = {}
        if wanted_person_ids:
            persons_by_id = {
                person.id: person
                for person in self._person_repository.list_all()
                if person.id in wanted_person_ids
            }

        items: list[FaceReviewItem] = []
        for face in faces:
            if face.id is None:
                continue
            person_name: str | None = None
            if face.person_id is not None:
                person = persons_by_id.get(face.person_id)
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
        """Assign one face to a person by name, preserving manual override.

        Only relabels this one face — never touches cluster membership, so
        it can't merge or smear anything. For labeling a whole cluster at
        once (the common case, e.g. confirming a cluster is a given kid),
        use `tag_cluster` instead.
        """
        resolved_person_id = self._resolve_or_create_person(name)
        if resolved_person_id is None:
            return

        self._face_repository.assign_person_manual([face_id], resolved_person_id)
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()

    def move_images_to_person(
        self, image_ids: list[int], source_person_id: int, target_name: str
    ) -> int:
        """Split off the given images' faces from one person to another.

        For splitting a mixed cluster: pulls this person's face out of each
        given image and reassigns it to `target_name` (an existing or brand
        new person), without touching the rest of the source person's
        photos. Only affects `source_person_id`'s own face in each image —
        other people who happen to appear in the same photo are untouched.
        """
        resolved_person_id = self._resolve_or_create_person(target_name)
        if resolved_person_id is None or resolved_person_id == source_person_id:
            return 0

        wanted_image_ids = set(image_ids)
        source_faces = self._face_repository.list_by_person(source_person_id)
        face_ids = [
            face.id
            for face in source_faces
            if face.id is not None and face.image_id in wanted_image_ids
        ]
        if not face_ids:
            return 0

        self.batch_assign_faces_to_person(face_ids, resolved_person_id)
        return len(face_ids)

    def _resolve_or_create_person(self, name: str) -> int | None:
        """Find a person by (case-insensitive) name, creating one if needed."""
        cleaned = name.strip()
        if not cleaned:
            return None
        existing = self._person_repository.get_by_name(cleaned)
        if existing is not None:
            return existing.id
        person = self._person_repository.create(
            Person(id=None, name=cleaned, created_at=now_utc(), birth_date=None)
        )
        return person.id

    def remove_face(self, face_id: int) -> None:
        """Exclude one detected face from UI and future auto-clustering."""
        self._face_repository.exclude([face_id])
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()

    def list_excluded_faces(
        self, limit: int = 200
    ) -> tuple[list[ExcludedFaceItem], int]:
        """Browse every currently-excluded face (e.g. from "Not of
        Interest"), so any of them can be found and restored later —
        nothing excluded is ever a one-way door. `limit` only caps how
        many are *returned*, for pagination; the second element is always
        the true, complete count (same "no cheating" honesty as the
        flagged-face review queue).
        """
        total = self._face_repository.count_excluded()
        if total == 0:
            return [], 0
        faces = self._face_repository.list_excluded(limit=limit)
        image_ids = {face.image_id for face in faces}
        images_by_id = {
            image.id: image
            for image in self._image_repository.list_by_ids(list(image_ids))
            if image.id is not None
        }
        items = [
            ExcludedFaceItem(
                face_id=face.id,
                image_id=face.image_id,
                image_path=images_by_id[face.image_id].file_path,
                bbox=face.bbox,
            )
            for face in faces
            if face.id is not None and face.image_id in images_by_id
        ]
        return items, total

    def restore_faces(self, face_ids: list[int]) -> None:
        """Undo excluding these faces — they re-enter normal auto-clustering
        on the next indexing run (as a fresh cluster; the old grouping
        isn't preserved, but the photo/bbox/embedding always were)."""
        self._face_repository.restore(face_ids)
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()

    def _clusters_by_person(
        self,
    ) -> tuple[dict[int, list[int]], dict[int, bool], dict[int, np.ndarray]]:
        """One pass over every cluster (not one query per person) to know,
        per tag: which cluster(s) it's applied to, whether any of them is
        flagged — since a tag can now span several clusters, a person's
        flagged state is "any of theirs," not "the one cluster" — and the
        person's overall centroid (face-count-weighted mean of their
        clusters' centroids, re-normalised) for the similarity sort.
        """
        person_cluster_ids: dict[int, list[int]] = defaultdict(list)
        person_any_flagged: dict[int, bool] = {}
        vector_sums: dict[int, np.ndarray] = {}
        if self._identity_cluster_service is not None:
            for cluster in self._identity_cluster_service.list_clusters():
                if cluster.person_id is None or cluster.id is None:
                    continue
                person_cluster_ids[cluster.person_id].append(cluster.id)
                if cluster.flagged_for_review:
                    person_any_flagged[cluster.person_id] = True
                centroid = np.frombuffer(cluster.canonical_embedding, dtype=np.float32)
                weighted = centroid.astype(np.float64) * max(cluster.face_count, 1)
                if cluster.person_id in vector_sums:
                    vector_sums[cluster.person_id] += weighted
                else:
                    vector_sums[cluster.person_id] = weighted
        person_vectors = {
            person_id: total / norm
            for person_id, total in vector_sums.items()
            if (norm := float(np.linalg.norm(total))) > 0.0
        }
        return dict(person_cluster_ids), person_any_flagged, person_vectors

    def person_stacks(
        self, sample_limit: int = 20, *, min_image_count: int = 0
    ) -> list[PersonStackSummary]:
        """Return stack summaries grouped by person ID.

        Stacks with fewer than `min_image_count` unique images are dropped
        *before* cover picking and the similarity sort — a large library
        can have many thousands of tiny unnamed clusters, and ranking all
        of them (a pairwise-distance pass) dominated every refresh even
        though only the few hundred shown stacks need an order.
        """
        grouped: dict[int, list[int]] = {}
        image_faces: dict[int, list[int | None]] = {}
        face_bbox_lookup: dict[tuple[int, int], BoundingBox] = {}
        # Lightweight projection (no embedding BLOB) — this scan runs on
        # every People-tab refresh, so avoiding a few-KB-per-face payload
        # here matters a lot on a library with tens of thousands of faces.
        all_faces = self._face_repository.list_active_stack_rows()
        for face in all_faces:
            image_faces.setdefault(face.image_id, []).append(face.person_id)
            if face.person_id is None:
                continue
            grouped.setdefault(face.person_id, []).append(face.image_id)
            face_bbox_lookup.setdefault((face.image_id, face.person_id), face.bbox)

        # Likewise a lightweight projection — skips large optional text
        # columns (user_notes, location_name, etc.) not needed to group and
        # pick covers.
        images_by_id = {
            image.id: image for image in self._image_repository.list_all_summary()
        }

        # Batch load all persons to avoid N+1 DB queries
        all_persons = self._person_repository.list_all()
        persons_by_id: dict[int, Person] = {}
        for person in all_persons:
            if person.id is not None:
                persons_by_id[person.id] = person

        person_cluster_ids, person_any_flagged, person_vectors = (
            self._clusters_by_person()
        )
        stacks: list[PersonStackSummary] = []
        for person_id, image_ids in grouped.items():
            unique_image_ids, unique_paths = _unique_images(image_ids, images_by_id)
            if len(unique_paths) < min_image_count:
                continue

            cover_image_id, cover_image_path = self._pick_stack_cover(
                person_id=person_id,
                image_ids=unique_image_ids,
                images_by_id=images_by_id,
                image_faces=image_faces,
                face_bbox_lookup=face_bbox_lookup,
            )

            # Always resolve this person's face on the cover image (even when
            # it's the only face) so the UI can consistently draw the same
            # highlight regardless of whether the cover is a solo or group shot.
            cover_face_bbox: BoundingBox | None = (
                face_bbox_lookup.get((cover_image_id, person_id))
                if cover_image_id is not None
                else None
            )
            cover_image = (
                images_by_id.get(cover_image_id) if cover_image_id is not None else None
            )
            cover_capture_date: datetime | None = (
                cover_image.capture_date if cover_image is not None else None
            )
            # Area ratio is rotation-invariant, so EXIF orientation (which
            # may swap width/height) doesn't matter here.
            cover_face_area_ratio: float | None = None
            if (
                cover_face_bbox is not None
                and cover_image is not None
                and cover_image.width
                and cover_image.height
            ):
                cover_face_area_ratio = (cover_face_bbox.w * cover_face_bbox.h) / (
                    cover_image.width * cover_image.height
                )

            # Use batch-loaded person data instead of individual DB queries
            person_record = persons_by_id.get(person_id)
            this_persons_clusters = person_cluster_ids.get(person_id, [])
            stack_cluster_id = (
                this_persons_clusters[0] if this_persons_clusters else None
            )
            stacks.append(
                PersonStackSummary(
                    person_id=person_id,
                    cluster_id=stack_cluster_id,
                    person_name=None if person_record is None else person_record.name,
                    face_count=len(image_ids),
                    image_count=len(unique_paths),
                    cover_image_path=cover_image_path,
                    cover_image_id=cover_image_id,
                    sample_image_paths=tuple(unique_paths[:sample_limit]),
                    sample_image_ids=tuple(unique_image_ids[:sample_limit]),
                    cover_face_bbox=cover_face_bbox,
                    cover_capture_date=cover_capture_date,
                    flagged_for_review=person_any_flagged.get(person_id, False),
                    cover_face_area_ratio=cover_face_area_ratio,
                )
            )

        stacks = _with_similarity_ranks(stacks, person_vectors)
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
        return self.person_stacks(
            sample_limit=sample_limit, min_image_count=min_image_count
        )

    def images_for_person(
        self, person_id: int
    ) -> tuple[list[int], list[str], dict[int, BoundingBox]]:
        """Return every unique image (id, path) for one person, uncapped,
        plus this person's own face bbox in each image.

        The bbox lets the caller highlight *which* face in a group photo is
        this person, without an extra query — it's read off the same
        `list_by_person` call already needed for the image list.

        `PersonStackSummary.sample_image_ids/paths` are capped at a small
        `sample_limit` for performance in the People tab's grid — this
        method is for the person detail view's "all images" gallery, which
        must actually show all of them rather than that capped preview.
        Queries only this person's faces/images directly (both indexed by
        the DB) instead of loading the whole library, since this runs on
        every click into a person's detail view, not once per tab load.
        """
        person_faces = self._face_repository.list_by_person(person_id)
        bbox_by_image_id: dict[int, BoundingBox] = {}
        for face in person_faces:
            bbox_by_image_id.setdefault(face.image_id, face.bbox)
        image_ids = sorted(bbox_by_image_id)
        if not image_ids:
            return [], [], {}

        images = self._image_repository.list_by_ids(image_ids)

        unique_ids: list[int] = []
        unique_paths: list[str] = []
        seen_paths: set[str] = set()
        for image in images:
            if image.id is None or image.file_path in seen_paths:
                continue
            seen_paths.add(image.file_path)
            unique_ids.append(image.id)
            unique_paths.append(image.file_path)

        return unique_ids, unique_paths, bbox_by_image_id

    def rename_person_stack(self, person_id: int, name: str) -> None:
        """Cosmetically rename an existing name tag (fix a typo, etc).

        Affects every cluster already carrying this tag — it's a pure
        display-name change, not a re-tagging. Refuses if another, different
        person already has that name; use `tag_cluster` to apply an
        *existing* name to a cluster (which is how you consolidate several
        age-split clusters of one child under one name), or
        `merge_person_into` to fold a mistakenly separate tag into it.
        """
        cleaned = name.strip()
        if not cleaned:
            return

        existing_person = self._person_repository.get_by_name(cleaned)
        if existing_person is not None and existing_person.id != person_id:
            msg = f"Person name '{cleaned}' already exists"
            raise ValueError(msg)

        self._person_repository.update_name(person_id, cleaned)
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()

    def merge_person_into(self, source_person_id: int, target_person_id: int) -> None:
        """Fold one name tag into another that is the same person.

        For when a cluster was accidentally tagged with a second name (e.g.
        "A1" instead of "A"): every face and cluster carrying the source tag
        moves to the target tag, the target inherits the source's birth date
        if it has none, and the source person record is deleted. Cluster
        state is never merged — the clusters just share one tag afterwards,
        exactly as if `tag_cluster` had been used with the right name.
        """
        if source_person_id == target_person_id:
            return
        source = self._person_repository.get(source_person_id)
        target = self._person_repository.get(target_person_id)
        if source is None or target is None:
            msg = "Cannot merge: person not found"
            raise ValueError(msg)

        # Copy the birth date first so the cluster refresh below re-flags
        # outliers against it.
        if target.birth_date is None and source.birth_date is not None:
            self._person_repository.update_birth_date(
                target_person_id, source.birth_date
            )

        self._face_repository.reassign_person(source_person_id, target_person_id)
        if self._identity_cluster_service is not None:
            self._identity_cluster_service.retag_person_clusters(
                source_person_id, target_person_id
            )
        self._person_repository.delete(source_person_id)
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()

    def tag_cluster(
        self, cluster_id: int, name: str, birth_date: date | None = None
    ) -> int | None:
        """Apply a name tag to one cluster.

        Resolves `name` to an existing tag (case-insensitive) or creates a
        new one, then labels every current member face of `cluster_id` with
        it — without touching the cluster's embedding, membership, or
        temporal profile (see `TemporalIdentityClusterService.tag_cluster`).
        The same tag can be applied to as many different clusters as
        needed (e.g. a child's separate baby/toddler/kid clusters) without
        ever merging them. `birth_date` is only used the first time a tag
        is created, or to backfill one that doesn't have it yet — it never
        overwrites an existing birth date silently.

        Returns the resolved person id, or None if `name` was blank or the
        cluster doesn't exist.
        """
        cleaned = name.strip()
        if not cleaned:
            return None

        person = self._person_repository.get_by_name(cleaned)
        if person is None:
            person = self._person_repository.create(
                Person(
                    id=None, name=cleaned, created_at=now_utc(), birth_date=birth_date
                )
            )
        elif birth_date is not None and person.birth_date is None and person.id:
            self._person_repository.update_birth_date(person.id, birth_date)

        if person.id is None:
            return None

        applied = False
        if self._identity_cluster_service is not None:
            applied = self._identity_cluster_service.tag_cluster(cluster_id, person.id)
        if not applied:
            return None

        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()
        return person.id

    def set_person_birth_date(self, person_id: int, birth_date: date | None) -> None:
        """Set (or clear) a person's birth date.

        Purely person metadata — clustering never looks at it.
        """
        self._person_repository.update_birth_date(person_id, birth_date)
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

    def exclude_people(self, person_ids: list[int]) -> int:
        """Bulk-exclude people not of interest — see
        `TemporalIdentityClusterService.exclude_persons`."""
        if self._identity_cluster_service is None:
            return 0
        count = self._identity_cluster_service.exclude_persons(person_ids)
        if count and self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()
        return count

    def _pick_stack_cover(
        self,
        *,
        person_id: int,
        image_ids: list[int],
        images_by_id: dict[int, ImageSummary],
        image_faces: dict[int, list[int | None]],
        face_bbox_lookup: dict[tuple[int, int], BoundingBox],
    ) -> tuple[int | None, str | None]:
        """Pick a representative (image_id, file_path) pair for a person stack.

        Ranks purely by how large this person's face is relative to the frame
        (a tiny face is hard to make out in a small preview thumbnail, even
        with a highlight box); whether other people are in the photo doesn't
        matter since the highlight box marks which face is theirs. Skips
        candidates whose file no longer exists on disk (e.g. moved to a trash
        folder and later purged, but never cleaned up in the DB) — picking
        one of those would make the preview silently fall back to a plain,
        unboxed thumbnail.

        Checks at most `max_cover_candidates` candidates with a filesystem
        `exists()` stat. This runs once per person on every People-tab
        refresh, so for a person whose photos have many stale/moved paths,
        scanning every candidate would mean a stat-call storm. In the common
        case the best-ranked candidate exists and this returns after one stat.
        """
        max_cover_candidates = 50

        def _relative_face_area(image_id: int) -> float:
            image = images_by_id.get(image_id)
            bbox = face_bbox_lookup.get((image_id, person_id))
            if image is None or bbox is None or image.width <= 0 or image.height <= 0:
                return 0.0
            return (bbox.w * bbox.h) / (image.width * image.height)

        candidates = [i for i in image_ids if image_faces.get(i)]
        if not candidates:
            return None, None
        ranked = sorted(candidates, key=_relative_face_area, reverse=True)
        cover_image_id = next(
            (
                i
                for i in ranked[:max_cover_candidates]
                if i in images_by_id and Path(images_by_id[i].file_path).exists()
            ),
            ranked[0],
        )
        image = images_by_id.get(cover_image_id)
        return cover_image_id, None if image is None else image.file_path

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

        Only relabels these faces — never touches cluster membership, so
        it can't merge or smear anything (same reasoning as `assign_name`).

        Args:
            face_ids: List of face IDs to assign
            person_id: Target person ID
        """
        self._face_repository.assign_person_manual(face_ids, person_id)
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()
