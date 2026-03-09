from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np

    from photo_app.domain.value_objects import BoundingBox


class FaceDetector(Protocol):
    """Face detection protocol."""

    def detect(self, image: np.ndarray) -> list[BoundingBox]:
        """Detect face bounding boxes."""


class EmbeddingModel(Protocol):
    """Face embedding protocol."""

    def embed(self, face_image: np.ndarray) -> np.ndarray:
        """Compute embedding for a single face image."""
