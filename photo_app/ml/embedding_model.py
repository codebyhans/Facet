from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]

from photo_app.ml.protocols import EmbeddingModel

if TYPE_CHECKING:
    from photo_app.ml.face_detector import InsightFaceDetector


class OnnxEmbeddingModel(EmbeddingModel):
    """ONNXRuntime embedding adapter."""

    def __init__(self, model_path: str, input_name: str) -> None:
        self._session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )
        self._input_name = input_name

    def embed(self, face_image: np.ndarray) -> np.ndarray:
        input_tensor = np.expand_dims(face_image.astype(np.float32), axis=0)
        output = self._session.run(None, {self._input_name: input_tensor})
        return np.asarray(output[0][0], dtype=np.float32)


class InsightFaceDetectorEmbeddingModel(EmbeddingModel):
    """Embedding adapter that reuses embeddings from InsightFace detections."""

    def __init__(self, detector: InsightFaceDetector) -> None:
        self._detector = detector

    def embed(self, face_image: np.ndarray) -> np.ndarray:
        _ = face_image
        return self._detector.next_embedding()
