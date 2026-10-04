from __future__ import annotations

import numpy as np

from photo_app.services.ann_index import ExactCosineIndex

VECTOR_COUNT = 5000
DIMS = 512


def _unit(vector: np.ndarray) -> np.ndarray:
    return (vector / np.linalg.norm(vector)).astype(np.float32)


def test_query_always_returns_the_true_best_match_first() -> None:
    """Moderately similar matches (cosine ~0.6) must never be missed — the
    old random-projection index surfaced them well under half the time."""
    rng = np.random.default_rng(0)
    vectors = {i: _unit(rng.normal(size=DIMS)) for i in range(VECTOR_COUNT)}
    index = ExactCosineIndex()
    index.build(vectors)

    for target in range(0, VECTOR_COUNT, 250):
        noise = _unit(rng.normal(size=DIMS))
        query = _unit(0.6 * vectors[target] + 0.8 * noise)
        neighbors = index.query(query, limit=3)
        assert neighbors[0].item_id == target
        assert neighbors[0].similarity >= neighbors[1].similarity


def test_added_vectors_are_searchable_without_rebuild() -> None:
    index = ExactCosineIndex()
    index.build({1: _unit(np.array([1.0, 0.0, 0.0]))})
    index.add_vector(2, _unit(np.array([0.0, 1.0, 0.0])))

    neighbors = index.query(np.array([0.1, 0.9, 0.0]), limit=5)
    assert [n.item_id for n in neighbors] == [2, 1]


def test_empty_index_returns_nothing() -> None:
    assert ExactCosineIndex().query(np.array([1.0, 0.0])) == []
