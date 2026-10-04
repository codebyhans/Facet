from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_NORM = 1e-12


@dataclass(frozen=True)
class AnnNeighbor:
    """Nearest-neighbor result."""

    item_id: int
    similarity: float


class ExactCosineIndex:
    """Brute-force cosine nearest-neighbor index.

    Scores every stored vector with one matrix-vector product. Even with
    tens of thousands of clusters that's a millisecond or two per query, and
    unlike the random-projection hashing this replaced it never misses the
    true best match (that index only surfaced it ~40% of the time, so most
    faces started a new cluster instead of joining their own).
    """

    def __init__(self) -> None:
        self._ids: list[int] = []
        self._rows: list[np.ndarray] = []
        self._matrix: np.ndarray | None = None

    def build(self, vectors: dict[int, np.ndarray]) -> None:
        """Rebuild index from normalized vectors."""
        self._ids = list(vectors.keys())
        self._rows = [vectors[key].astype(np.float32) for key in self._ids]
        self._matrix = None

    def add_vector(self, item_id: int, vector: np.ndarray) -> None:
        """Add a single normalized vector; the matrix is re-stacked lazily."""
        self._ids.append(item_id)
        self._rows.append(vector.astype(np.float32))
        self._matrix = None

    def query(self, vector: np.ndarray, limit: int = 32) -> list[AnnNeighbor]:
        """Return the `limit` most cosine-similar vectors, best first."""
        if not self._ids:
            return []
        if self._matrix is None:
            self._matrix = np.vstack(self._rows)
        similarities = self._matrix @ _normalize(vector)
        count = min(limit, len(self._ids))
        top = np.argpartition(-similarities, count - 1)[:count]
        top = top[np.argsort(-similarities[top])]
        return [
            AnnNeighbor(item_id=self._ids[idx], similarity=float(similarities[idx]))
            for idx in top
        ]


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= MIN_NORM:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)
