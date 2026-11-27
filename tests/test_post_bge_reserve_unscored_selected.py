"""``apply_post_bge_fusion_reserve`` must survive an unscored selected hit.

Same working set as the topic overlay (``normal_results``), reached a few steps
earlier: the reserve picks the weakest selected row before it decides whether an
omitted fusion hit may take its place.  It looked the row's score up with a bare
``scores_by_id[...]``, so a row that never went through the reranker raised
``KeyError`` and took the whole search down.
"""

from hy_memory.pipelines._retrieval.candidate_guards import (
    apply_post_bge_fusion_reserve,
)


def test_an_unscored_selected_row_does_not_break_the_reserve():
    selected = [
        {"node_id": "scored", "content": "alpha beta gamma", "score": 0.7},
        {"node_id": "unscored", "content": "delta epsilon zeta", "score": 0.0},
    ]
    pool = list(selected) + [
        {"node_id": "omitted", "content": "eta theta iota", "score": 0.6},
    ]

    out, info = apply_post_bge_fusion_reserve(
        "alpha", pool, selected, {"scored": 0.7}, limit=2,
    )

    assert [item["node_id"] for item in out] == ["scored", "unscored"]
    assert info["applied"] is False


def test_an_unscored_selected_row_is_the_first_victim():
    selected = [
        {"node_id": "scored", "content": "alpha beta gamma", "score": 0.7},
        {"node_id": "unscored", "content": "delta epsilon zeta", "score": 0.0},
    ]
    omitted = {
        "node_id": "omitted",
        "content": "alpha " + " ".join(f"token{index}" for index in range(20)),
        "score": 0.6,
    }

    out, info = apply_post_bge_fusion_reserve(
        "alpha", [*selected, omitted], selected, {"scored": 0.7, "omitted": 0.6}, limit=2,
    )

    assert info["applied"] is True
    assert info["displaced"] == "unscored"
    assert [item["node_id"] for item in out] == ["scored", "omitted"]
    assert out[1]["_local_rerank_score"] == 0.6
