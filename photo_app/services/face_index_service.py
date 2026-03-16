from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, UnidentifiedImageError

import logging

from photo_app.domain.models import Face, Person
from photo_app.domain.models import Image as ImageEntity

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from photo_app.domain.repositories import (
        FaceRepository,
        ImageRepository,
        PersonRepository,
    )
    from photo_app.ml.clustering import AgeAwareClustering
    from photo_app.ml.protocols import EmbeddingModel, FaceDetector
    from photo_app.services.album_query_cache_service import AlbumQueryCacheService
    from photo_app.services.identity_cluster_service import TemporalIdentityClusterService
    from photo_app.services.identity_maintenance_jobs import IdentityMaintenanceJobs


@dataclass(frozen=True)
class FaceIndexResult:
    """Result summary for face indexing."""

    processed_images: int
    detected_faces: int


@dataclass(frozen=True)
class FaceIndexDependencies:
    """Dependency bundle for face indexing use case."""

    image_repository: ImageRepository
    face_repository: FaceRepository
    person_repository: PersonRepository
    detector: FaceDetector
    embedding_model: EmbeddingModel
    clustering: AgeAwareClustering
    query_cache_service: AlbumQueryCacheService | None = None
    identity_cluster_service: TemporalIdentityClusterService | None = None
    identity_maintenance_jobs: IdentityMaintenanceJobs | None = None


class FaceIndexService:
    """Use case for face extraction and clustering."""

    def __init__(self, dependencies: FaceIndexDependencies) -> None:
        self._image_repository = dependencies.image_repository
        self._face_repository = dependencies.face_repository
        self._person_repository = dependencies.person_repository
        self._detector = dependencies.detector
        self._embedding_model = dependencies.embedding_model
        self._clustering = dependencies.clustering
        self._query_cache_service = dependencies.query_cache_service
        self._identity_cluster_service = dependencies.identity_cluster_service
        self._identity_maintenance_jobs = dependencies.identity_maintenance_jobs

    def index_faces(
        self,
        limit: int = 128,
        on_progress: Callable[[int, int], None] | None = None,
        skip_clustering: bool = False,
    ) -> FaceIndexResult:
        """Detect faces, persist embeddings, and optionally assign cluster person IDs."""
        images = self._image_repository.list_unprocessed_for_faces(limit)
        total = len(images)
        staged_faces: list[Face] = []

        for current, image in enumerate(images, start=1):
            if image.id is None:
                continue
            new_faces = self._extract_faces_for_image(image)
            staged_faces.extend(new_faces)
            self._image_repository.update_face_count(image.id, len(new_faces))
            if on_progress is not None:
                on_progress(current, total)

        if staged_faces:
            self._face_repository.add_many(staged_faces)

        if not skip_clustering:
            if self._identity_cluster_service is None:
                self._cluster_all_faces()
            else:
                self._identity_cluster_service.index_new_faces()
            if self._identity_maintenance_jobs is not None:
                self._identity_maintenance_jobs.run_all()
            if self._query_cache_service is not None:
                self._query_cache_service.invalidate_all()

        return FaceIndexResult(
            processed_images=len(images),
            detected_faces=len(staged_faces),
        )

    def reindex_image(self, image_path: str) -> FaceIndexResult:
        """Re-detect one image and overwrite all existing face rows for it."""
        image = self._image_repository.get_by_path(image_path)
        if image is None or image.id is None:
            return FaceIndexResult(processed_images=0, detected_faces=0)

        self._face_repository.delete_by_image(image.id)
        staged_faces = self._extract_faces_for_image(image)
        if staged_faces:
            self._face_repository.add_many(staged_faces)
            self._image_repository.update_face_count(image.id, len(staged_faces))
        if self._identity_cluster_service is None:
            self._cluster_all_faces()
        else:
            self._identity_cluster_service.index_new_faces()
        if self._identity_maintenance_jobs is not None:
            self._identity_maintenance_jobs.run_all()
        if self._query_cache_service is not None:
            self._query_cache_service.invalidate_all()
        return FaceIndexResult(processed_images=1, detected_faces=len(staged_faces))

    def _extract_faces_for_image(self, image: ImageEntity) -> list[Face]:
        try:
            with Image.open(image.file_path) as image_file:
                np_image = np.array(image_file.convert("RGB"))
        except (UnidentifiedImageError, OSError, PermissionError) as exc:
            logger.warning("Skipping %s: %s", image.file_path, exc)
            return []
        except Exception as exc:
            logger.exception("Unexpected error opening %s: %s", image.file_path, exc)
            return []

        boxes = self._detector.detect(np_image)
        if image.id is None:
            return []

        staged: list[Face] = []
        for box in boxes:
            crop = np_image[box.y : box.y + box.h, box.x : box.x + box.w]
            embedding = self._embedding_model.embed(crop).astype(np.float32)
            staged.append(
                Face(
                    id=None,
                    image_id=image.id,
                    bbox=box,
                    embedding=embedding.tobytes(),
                    person_id=None,
                )
            )
        return staged

    def _cluster_all_faces(self) -> None:
        faces = self._face_repository.list_all_active()
        if not faces:
            return

        embeddings = np.vstack(
            [np.frombuffer(face.embedding, dtype=np.float32) for face in faces]
        )
        image_map = {
            image.id: image
            for image in self._image_repository.list_all()
            if image.id is not None
        }
        dates = [
            image_map[face.image_id].capture_date
            if face.image_id in image_map
            else None
            for face in faces
        ]
        labels = self._clustering.cluster(embeddings, dates)

        clusters: dict[int, list[Face]] = {}
        for face, label in zip(faces, labels, strict=True):
            if face.id is None:
                continue
            clusters.setdefault(int(label), []).append(face)

        for label, cluster_faces in clusters.items():
            if label < 0:
                continue
            assigned_person_ids = [
                face.person_id for face in cluster_faces if face.person_id is not None
            ]
            person_id: int | None = None
            if assigned_person_ids:
                counts = Counter(assigned_person_ids)
                person_id = min(
                    counts,
                    key=lambda candidate: (-counts[candidate], int(candidate)),
                )
            if person_id is None:
                person = self._person_repository.create(
                    Person(
                        id=None,
                        name=None,
                        created_at=datetime.now(tz=UTC),
                        birth_date=None,
                    )
                )
                person_id = person.id

            if person_id is not None:
                self._face_repository.assign_person_auto(
                    [face.id for face in cluster_faces if face.id is not None],
                    person_id,
                )
