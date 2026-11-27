"""``apply_topic_overlay`` must survive a selected hit that was never scored.

``scores_by_id`` holds local-rerank scores for the pool the overlay candidates
were ranked in.  ``selected`` is the working top-k, which other stages may have
extended with rows that never went through the reranker.  The overlay already
treats a *candidate* without a score as ``-inf`` and ``reader_base`` even
back-fills scores for missing candidates; the two lookups over ``selected``
used a bare ``scores_by_id[...]``, so one unscored row raised ``KeyError`` and
took the whole search down instead of being handled as the weakest row.
"""

from hy_memory.pipelines._retrieval.topic_candidates import (
    apply_topic_graph_candidates,
    apply_topic_overlay,
)


def test_an_unscored_selected_row_does_not_break_the_sort():
    selected = [
        {"node_id": "scored", "score": 0.7},
        {"node_id": "unscored", "score": 0.0},
    ]

    out, info = apply_topic_overlay(
        selected, [], {"scored": 0.7}, limit=2, slots=0,
    )

    assert [item["node_id"] for item in out] == ["scored", "unscored"]
    assert info["applied"] == []


def test_an_unscored_selected_row_is_displaced_first():
    selected = [
        {"node_id": "scored", "score": 0.7},
        {"node_id": "unscored", "score": 0.0},
    ]
    candidates = [{"item": {"node_id": "candidate"}, "score": 0.9}]

    out, info = apply_topic_overlay(
        selected, candidates, {"scored": 0.7, "candidate": 0.9}, limit=2, slots=1,
    )

    assert [item["node_id"] for item in out] == ["candidate", "scored"]
    assert info["applied"][0]["admitted"] == "candidate"
    assert info["applied"][0]["displaced"] == "unscored"
    assert out[0]["_local_rerank_score"] == 0.9


def test_the_graph_entry_tolerates_an_unscored_selected_row():
    selected = [{"node_id": "scored"}, {"node_id": "unscored"}]
    candidates = [
        {"item": {"node_id": "candidate"}, "relation_phrase_match": 0, "subject": "s"},
    ]

    out, info = apply_topic_graph_candidates(
        "q", ["s"], selected, candidates,
        {"scored": 0.5, "candidate": 0.9}, limit=2, slots=1,
    )

    assert info["applied"] is False
    assert [item["node_id"] for item in out] == ["scored", "unscored"]
    assert info["decisions"][0]["displaced"] == "unscored"
    assert info["decisions"][0]["displaced_bge"] == float("-inf")
