from __future__ import annotations

from datetime import date

import numpy as np

from photo_app.ml.clustering import AgeAwareClustering, ClusteringConfig

EXPECTED_COUNT = 3


def test_age_penalty_reduces_cross_year_similarity() -> None:
    embeddings = np.array(
        [
            [1.0, 0.0],
            [0.99, 0.01],
            [0.0, 1.0],
        ],
        dtype=np.float32,
    )
    dates = [date(2020, 1, 1), date(2021, 1, 1), date(2020, 1, 1)]
    clustering = AgeAwareClustering(ClusteringConfig(min_cluster_size=2))
    labels = clustering.cluster(embeddings, dates)
    assert labels.shape[0] == EXPECTED_COUNT
