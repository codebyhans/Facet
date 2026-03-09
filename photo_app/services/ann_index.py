from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AnnNeighbor:
    """Approximate nearest-neighbor result."""

    item_id: int
    similarity: float


class RandomProjectionAnnIndex:
    """Simple random-projection ANN index for cosine vectors."""

    def __init__(
        self,
        *,
        projection_count: int = 8,
        bits_per_projection: int = 12,
        random_seed: int = 7,
    ) -> None:
        self._projection_count = projection_count
        self._bits_per_projection = bits_per_projection
        self._rng = np.random.default_rng(random_seed)
        self._projections: list[np.ndarray] = []
        self._buckets: list[dict[int, list[int]]] = []
        self._vectors: dict[int, np.ndarray] = {}

    def build(self, vectors: dict[int, np.ndarray]) -> None:
        """Rebuild index from normalized vectors."""
        self._vectors = {key: value.astype(np.float32) for key, value in vectors.items()}
        self._projections = []
        self._buckets = []
        if not self._vectors:
            return

        sample = next(iter(self._vectors.values()))
        dims = int(sample.shape[0])
        for _ in range(self._projection_count):
            projection = self._rng.normal(
                size=(self._bits_per_projection, dims)
            ).astype(np.float32)
            self._projections.append(projection)
            self._buckets.append({})

        for item_id, vector in self._vectors.items():
            for idx, projection in enumerate(self._projections):
                key = self._signature(vector, projection)
                self._buckets[idx].setdefault(key, []).append(item_id)

    def query(self, vector: np.ndarray, limit: int = 32) -> list[AnnNeighbor]:
        """Return top cosine candidates by approximate bucket lookup."""
        if not self._vectors:
            return []
        normalized = _normalize(vector)
        candidates: set[int] = set()
        for idx, projection in enumerate(self._projections):
            key = self._signature(normalized, projection)
            for item_id in self._buckets[idx].get(key, []):
                candidates.add(item_id)

        if not candidates:
            candidates = set(self._vectors.keys())

        scored: list[AnnNeighbor] = []
        for item_id in candidates:
            similarity = float(np.dot(normalized, self._vectors[item_id]))
            scored.append(AnnNeighbor(item_id=item_id, similarity=similarity))
        scored.sort(key=lambda item: item.similarity, reverse=True)
        return scored[:limit]

    def _signature(self, vector: np.ndarray, projection: np.ndarray) -> int:
        dots = projection @ vector
        key = 0
        for bit, value in enumerate(dots):
            if value >= 0:
                key |= 1 << bit
        return int(key)


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)
