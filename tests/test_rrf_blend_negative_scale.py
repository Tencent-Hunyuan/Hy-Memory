"""RRF blend must stay a positive scale when the additive scores are not positive.

``blend_rrf_into_scores`` mixes ``(1 - b) * add + b * max_add * r_norm``.
``max_add`` came from ``max(...) or 1.0``: an all-zero pool fell back to 1.0,
but an all-negative pool kept a *negative* max_add, which flips the sign of the
RRF term. At ``blend=1`` the item with the best RRF rank is then pushed to the
bottom instead of the top.
"""

from hy_memory.pipelines._retrieval.diversity import blend_rrf_into_scores


def _hits(first_score, second_score):
    # "B" has the best semantic / BM25 (and therefore RRF) rank, while "A" has
    # the best additive score. With a correct positive scale, blend=1 must
    # place the RRF winner "B" first regardless of the additive sign.
    return [
        {"node_id": "A", "score": first_score, "_semantic": 0.0},
        {
            "node_id": "B", "score": second_score,
            "_semantic": 1.0, "_bm25": 2.0,
        },
    ]


def test_all_negative_scores_do_not_invert_rrf_order():
    """A negative max_add must not turn the RRF bonus into a penalty."""
    result = blend_rrf_into_scores(_hits(-0.1, -0.9), blend=1.0)
    assert [hit["node_id"] for hit in result] == ["B", "A"]


def test_all_zero_scores_keep_the_unit_scale():
    """The pre-existing all-zero fallback (max_add -> 1.0) still holds."""
    result = blend_rrf_into_scores(_hits(0.0, 0.0), blend=1.0)
    assert [hit["node_id"] for hit in result] == ["B", "A"]
    assert result[0]["score"] > 0.0


def test_positive_scores_keep_the_original_scale():
    """No behaviour change for the normal (positive-score) pool."""
    result = blend_rrf_into_scores(_hits(0.9, 0.1), blend=1.0)
    assert [hit["node_id"] for hit in result] == ["B", "A"]
    # max_add = 0.9, so the RRF winner is scaled to exactly 0.9.
    assert abs(result[0]["score"] - 0.9) < 1e-9


def test_blend_zero_is_a_pure_passthrough():
    """blend=0 returns the pool untouched, independent of the scale fix."""
    hits = _hits(-0.1, -0.9)
    result = blend_rrf_into_scores(hits, blend=0.0)
    assert result == hits
