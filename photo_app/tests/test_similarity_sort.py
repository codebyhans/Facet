from __future__ import annotations

import numpy as np

from photo_app.services.face_review_service import _similarity_ranks


def _unit(values: list[float]) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float64)
    return vector / np.linalg.norm(vector)


def test_split_clusters_of_same_person_end_up_adjacent() -> None:
    # 1 and 3 are two clusters of person A; 2 and 4 two of person B.
    vectors = {
        1: _unit([1.0, 0.05, 0.0]),
        2: _unit([0.0, 1.0, 0.05]),
        3: _unit([0.95, 0.0, 0.1]),
        4: _unit([0.05, 0.9, 0.0]),
        5: _unit([0.0, 0.0, 1.0]),
    }
    ranks = _similarity_ranks(vectors)

    assert sorted(ranks.values()) == list(range(5))
    assert abs(ranks[1] - ranks[3]) == 1
    assert abs(ranks[2] - ranks[4]) == 1


def test_trivial_inputs() -> None:
    assert _similarity_ranks({}) == {}
    assert _similarity_ranks({7: _unit([1.0, 0.0])}) == {7: 0}
