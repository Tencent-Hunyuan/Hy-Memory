# -*- coding: utf-8 -*-
"""Regression: RRF must count each document at most once per channel (using
its best rank), and a None score must not abort fusion.

The old loop added a contribution for every hit, so a channel returning the
same node_id multiple times inflated its RRF score by the duplicate count and
recorded the worst rank. Per-channel score is observation-only and must mirror
reconcile_retrieval.py's tolerant `float(x or 0.0)`.
"""

from hy_memory.pipelines._retrieval.rrf import rrf_fuse

K = 60


def test_duplicate_node_in_one_channel_counts_once():
    single = rrf_fuse({"v": [{"node_id": "a", "score": 1.0}]}, k=K)
    triple = rrf_fuse(
        {
            "v": [
                {"node_id": "a", "score": 1.0},
                {"node_id": "a", "score": 0.5},
                {"node_id": "a", "score": 0.2},
            ]
        },
        k=K,
    )
    expected = 1.0 / (K + 1)
    assert abs(single[0]["rrf_score"] - expected) < 1e-12
    assert abs(triple[0]["rrf_score"] - expected) < 1e-12, (
        f"duplicate inflated score to {triple[0]['rrf_score']}, expected {expected}"
    )
    assert triple[0]["rrf_rank_by_channel"]["v"] == 1, "best rank (1) must be kept"


def test_duplicate_node_keeps_best_rank_and_first_score():
    out = rrf_fuse(
        {
            "v": [
                {"node_id": "a", "score": 0.9},
                {"node_id": "a", "score": 0.1},
            ]
        },
        k=K,
    )
    assert out[0]["rrf_rank_by_channel"]["v"] == 1
    assert out[0]["per_channel_score"]["v"] == 0.9


def test_none_score_does_not_crash():
    out = rrf_fuse({"v": [{"node_id": "a", "score": None}]}, k=K)
    assert len(out) == 1
    assert out[0]["per_channel_score"]["v"] == 0.0


def test_distinct_nodes_in_one_channel_each_contribute():
    out = rrf_fuse(
        {"v": [{"node_id": "a", "score": 1.0}, {"node_id": "b", "score": 0.8}]},
        k=K,
    )
    scores = {h["node_id"]: h["rrf_score"] for h in out}
    assert abs(scores["a"] - 1.0 / (K + 1)) < 1e-12
    assert abs(scores["b"] - 1.0 / (K + 2)) < 1e-12
