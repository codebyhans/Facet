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
        img_h, img_w = image.shape[:2]
        faces = self._app.get(image)
        self._last_embeddings = [face.embedding.astype(np.float32) for face in faces]
        boxes = []
        for face in faces:
            x1 = max(0, int(face.bbox[0]))
            y1 = max(0, int(face.bbox[1]))
            x2 = min(img_w, int(face.bbox[2]))
            y2 = min(img_h, int(face.bbox[3]))
            w = x2 - x1
            h = y2 - y1
            if w <= 0 or h <= 0:
                # Face bbox is entirely outside image bounds — discard and pop
                # the cached embedding to keep the detect/embed lists in sync
                self._last_embeddings.pop(len(boxes))
                continue
            boxes.append(BoundingBox(x=x1, y=y1, w=w, h=h))
        return boxes

    def next_embedding(self) -> np.ndarray:
        """Return next embedding from most recent detect() call."""
        if not self._last_embeddings:
            msg = "No cached embedding available from detector"
            raise RuntimeError(msg)
        return self._last_embeddings.pop(0)
