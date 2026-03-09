from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import hdbscan  # type: ignore[import-untyped]
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date


@dataclass(frozen=True)
class ClusteringConfig:
    """Config for age-aware clustering."""

    age_penalty_weight: float = 0.15
    penalty_year_scale: float = 10.0
    min_cluster_size: int = 2


class AgeAwareClustering:
    """Age-penalized face embedding clustering."""

    def __init__(self, config: ClusteringConfig) -> None:
        self._config = config

    def cluster(
        self,
        embeddings: np.ndarray,
        dates: Sequence[date | None],
    ) -> np.ndarray:
        """Return cluster labels for embeddings."""
        if len(embeddings) == 0:
            return np.array([], dtype=np.int64)
        if len(embeddings) == 1:
            return np.array([0], dtype=np.int64)

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        safe_norms = np.clip(norms, 1e-12, None)
        normalized = embeddings / safe_norms
        cosine = normalized @ normalized.T

        adjusted = cosine.copy()
        for i in range(len(dates)):
            for j in range(i + 1, len(dates)):
                penalty = self._penalty(dates[i], dates[j])
                value = cosine[i, j] - (self._config.age_penalty_weight * penalty)
                adjusted[i, j] = value
                adjusted[j, i] = value

        distance = (1.0 - adjusted).astype(np.float64, copy=False)
        clusterer = hdbscan.HDBSCAN(
            metric="precomputed",
            min_cluster_size=self._config.min_cluster_size,
        )
        labels = clusterer.fit_predict(distance)
        return cast("np.ndarray", labels)

    def _penalty(self, a: date | None, b: date | None) -> float:
        if a is None or b is None:
            return 0.0
        years = abs(a.year - b.year)
        return min(years / self._config.penalty_year_scale, 1.0)
