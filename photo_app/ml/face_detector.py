from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import numpy as np

from photo_app.domain.value_objects import BoundingBox
from photo_app.ml.protocols import FaceDetector

if TYPE_CHECKING:
    from collections.abc import Sequence


class InsightFaceLike(Protocol):
    def get(self, image: np.ndarray) -> Sequence[InsightFaceResult]: ...


class InsightFaceResult(Protocol):
    @property
    def bbox(self) -> np.ndarray: ...

    @property
    def embedding(self) -> np.ndarray: ...


class InsightFaceDetector(FaceDetector):
    """insightface-backed detector adapter."""

    def __init__(self, app: InsightFaceLike) -> None:
        self._app = app
        self._last_embeddings: list[np.ndarray] = []

    def detect(self, image: np.ndarray) -> list[BoundingBox]:
        faces = self._app.get(image)
        self._last_embeddings = [face.embedding.astype(np.float32) for face in faces]
        return [
            BoundingBox(
                x=int(face.bbox[0]),
                y=int(face.bbox[1]),
                w=int(face.bbox[2] - face.bbox[0]),
                h=int(face.bbox[3] - face.bbox[1]),
            )
            for face in faces
        ]

    def next_embedding(self) -> np.ndarray:
        """Return next embedding from most recent detect() call."""
        if not self._last_embeddings:
            msg = "No cached embedding available from detector"
            raise RuntimeError(msg)
        return self._last_embeddings.pop(0)
